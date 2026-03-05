"""
Fast Reversal Trader (v2)
=========================
Detects high-conviction directional windows using LIVE CLOB price as primary signal,
then places a reversal order in the NEXT window as fast as possible.

WHY CLOB PRICE IS BETTER THAN SPOT USD:
  - Single API call vs DB query + math
  - Market makers have already priced in ALL information (spot, momentum, orderflow)
  - UP token at 90¢ with 30s left ≡ "$500 BTC move, UP wins" — no threshold tuning needed
  - Works identically for ETH/SOL/XRP without asset-specific USD thresholds

TIMING OPTIMIZATION (no wasted seconds):
  - Poll for next window's market from T-20s (potentially before current closes)
  - If next market appears BEFORE current closes → place immediately, don't wait
  - If appears at T+0 to T+90s → still place within seconds of window open
  - This beats the "wait for resolution" approach by 15-30 seconds

SIGNAL LOGIC:
  Primary:  CLOB mid-price of current UP token
            > CLOB_HIGH_THRESHOLD (default 0.82) → strong UP confirmed → reverse to DOWN
            < CLOB_LOW_THRESHOLD  (default 0.18) → strong DOWN confirmed → reverse to UP
  Secondary: Spot USD move (fallback when CLOB call fails)
            abs(usd_move) >= per-asset threshold

TRAJECTORY GUARD (early trigger):
  In last 10s: lower spot threshold by 25% (e.g. $200→$150)
  CLOB threshold stays fixed (market already priced it)
"""

import bisect
import json
import logging
import threading
import time
from typing import Dict, Optional, Tuple

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from py_clob_client.order_builder.constants import BUY

from config import config
from db import get_connection
from ingestion.polymarket import (
    fetch_active_crypto_markets,
    _fetch_gamma_market_by_slug,
    _parse_token_ids,
    _build_slug_variants,
)

logger = logging.getLogger(__name__)

ASSETS = ("BTC", "ETH", "SOL", "XRP")
INTERVALS = (5, 15, 60)

# ── Signal thresholds ────────────────────────────────────────────────────────

# CLOB: if current UP token mid-price is above/below these, we have high conviction
CLOB_HIGH_THRESHOLD = config.REVERSAL_CLOB_HIGH
CLOB_LOW_THRESHOLD  = config.REVERSAL_CLOB_LOW


def _parse_reversal_thresholds() -> Dict[str, float]:
    result: Dict[str, float] = {"BTC": 200.0, "ETH": 20.0, "SOL": 2.0, "XRP": 0.10}
    raw = (config.REVERSAL_USD_THRESHOLDS or "").strip()
    for part in raw.split(","):
        if ":" in part:
            asset, val = part.strip().split(":", 1)
            try:
                result[asset.strip().upper()] = float(val.strip())
            except ValueError:
                pass
    return result


def _window_boundaries(interval_minutes: int, at_ts: Optional[int] = None) -> Tuple[int, int]:
    ts = at_ts or int(time.time())
    w = interval_minutes * 60
    start = (ts // w) * w
    return start, start + w


def _spot_move_in_window(asset: str, window_start_ts: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    USD change from window open to now.
    Returns (usd_change, open_price, current_price).
    Accepts ticks within ±60s of window start as anchor.
    """
    anchor_tolerance = 60
    now = int(time.time())

    conn = get_connection()
    try:
        ticks = conn.execute(
            """SELECT captured_at, price_usd FROM spot_prices
               WHERE asset=? AND captured_at >= ? AND captured_at <= ?
               ORDER BY captured_at ASC""",
            (asset.upper(), window_start_ts - anchor_tolerance, now + 5),
        ).fetchall()
    finally:
        conn.close()

    if len(ticks) < 2:
        return None, None, None

    tick_ts = [r[0] for r in ticks]
    tick_px = [float(r[1]) for r in ticks]

    i = bisect.bisect_left(tick_ts, window_start_ts)
    candidates = []
    if i < len(tick_ts):
        candidates.append((abs(tick_ts[i] - window_start_ts), tick_px[i]))
    if i > 0:
        candidates.append((abs(tick_ts[i - 1] - window_start_ts), tick_px[i - 1]))
    if not candidates:
        return None, None, None
    candidates.sort()
    open_price = candidates[0][1]
    current_price = tick_px[-1]
    return round(current_price - open_price, 4), open_price, current_price


def _clob_mid_price(client: ClobClient, token_id: str) -> Optional[float]:
    """Get best-bid/best-ask midpoint for a token. Returns None on failure."""
    try:
        book = client.get_order_book(token_id)
        bids = sorted(book.bids or [], key=lambda x: -float(x.price))
        asks = sorted(book.asks or [], key=lambda x:  float(x.price))
        if bids and asks:
            return (float(bids[0].price) + float(asks[0].price)) / 2.0
        if bids:
            return float(bids[0].price)
        if asks:
            return float(asks[0].price)
    except Exception as e:
        logger.debug("CLOB mid-price fetch failed token=%s: %s", token_id, e)
    return None


def _build_client() -> Optional[ClobClient]:
    if not config.PRIVATE_KEY:
        return None
    if not (config.CLOB_API_KEY and config.CLOB_SECRET and config.CLOB_PASS_PHRASE):
        return None
    creds = ApiCreds(
        api_key=config.CLOB_API_KEY,
        api_secret=config.CLOB_SECRET,
        api_passphrase=config.CLOB_PASS_PHRASE,
    )
    return ClobClient(
        host=config.CLOB_HOST,
        chain_id=137,
        key=config.PRIVATE_KEY,
        creds=creds,
        signature_type=2,
        funder=config.WALLET_ADDRESS or None,
    )


def _record_reversal_order(
    slug: str, asset: str, interval: int, token_id: str,
    predicted_side: str, price: float, size: float,
    trigger_usd_move: Optional[float], trigger_clob_price: Optional[float],
    signal_source: str, status: str,
    response_json: Optional[Dict] = None, error: Optional[str] = None,
) -> int:
    pattern_label = (
        f"CLOB:{trigger_clob_price:.2f}" if trigger_clob_price is not None
        else f"SPOT:{'+' if (trigger_usd_move or 0) > 0 else ''}{(trigger_usd_move or 0):.0f}USD"
    )
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO auto_trade_orders
            (slug, asset, interval_minutes, token_id, pattern_str, predicted_side,
             order_price, order_size, status, response_json, error,
             trigger_type, trigger_usd_move, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REVERSAL', ?, ?)
            """,
            (
                slug, asset, interval, token_id,
                f"REVERSAL[{signal_source}:{pattern_label}]",
                predicted_side, price, size, status,
                json.dumps(response_json) if response_json else None,
                error, trigger_usd_move, int(time.time()),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _already_fired(asset: str, interval: int, next_start_ts: int) -> bool:
    slug_pattern = f"%-updown-{interval}m-{next_start_ts}"
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT id FROM auto_trade_orders
               WHERE asset=? AND interval_minutes=? AND trigger_type='REVERSAL'
                 AND slug LIKE ? LIMIT 1""",
            (asset.upper(), interval, slug_pattern),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


class FastReversalTrader:
    """
    Clock-driven reversal trader using CLOB mid-price as primary signal.
    Places order as soon as next window's market appears on Gamma.
    """

    CHECK_INTERVAL_S = 2

    def __init__(self):
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[ClobClient] = None
        self._thresholds = _parse_reversal_thresholds()
        self._monitor_s  = config.REVERSAL_MONITOR_WINDOW_S  # default 45s before close
        self._order_price = config.REVERSAL_ORDER_PRICE
        self._order_size  = config.REVERSAL_ORDER_SIZE
        # pending["{asset}-{interval}-{window_end_ts}"] = order spec
        self._pending: Dict[str, Dict] = {}
        # cache of last active-markets fetch: (timestamp, list)
        self._markets_cache: Tuple[int, list] = (0, [])

    def _get_active_markets(self) -> list:
        """Cache active markets for 3s to avoid hammering the API."""
        now = int(time.time())
        if now - self._markets_cache[0] < 3:
            return self._markets_cache[1]
        try:
            markets = fetch_active_crypto_markets()
            self._markets_cache = (now, markets)
            return markets
        except Exception as e:
            logger.debug("Active markets fetch error: %s", e)
            return self._markets_cache[1]

    def _next_market(self, asset: str, interval: int, next_start_ts: int, active_markets: list) -> Optional[Dict]:
        """
        Fetch the next window's market.
        Polymarket pre-creates 10+ future windows, so we fetch by slug directly from Gamma.
        Falls back to the in-memory active_markets list (populated by current-window fetch).
        """
        # Try each slug variant (e.g. "5m", "5-minute", "5min") for this asset/interval
        slug_variants = _build_slug_variants(asset, interval, next_start_ts)

        for slug in slug_variants:
            # 1. Check in-memory first (no API call needed if already loaded)
            for m in active_markets:
                if m.get("slug") == slug:
                    return m

            # 2. Direct Gamma fetch by slug — pre-created markets are always available
            try:
                raw = _fetch_gamma_market_by_slug(slug)
                if raw and (raw.get("active") or not raw.get("closed", True)):
                    token_ids = _parse_token_ids(raw)
                    if token_ids:
                        return {
                            "slug": raw.get("slug", slug),
                            "asset": asset,
                            "interval_minutes": interval,
                            "up_token_id": token_ids[0],
                            "down_token_id": token_ids[1] if len(token_ids) > 1 else None,
                            "end_date": raw.get("endDate"),
                            "start_ts": next_start_ts,
                        }
            except Exception as e:
                logger.debug("[FastReversal] Gamma fetch failed for slug=%s: %s", slug, e)

        return None

    def _place_reversal(
        self,
        asset: str, interval: int, next_start_ts: int,
        reverse_side: str, usd_move: Optional[float], clob_mid: Optional[float],
        signal_source: str, active_markets: list,
        time_remaining: int,
    ) -> bool:
        """
        Resolve next market token and place the order immediately.
        Returns True if order was placed (success or fail), False if market not found yet.
        """
        market = self._next_market(asset, interval, next_start_ts, active_markets)
        next_slug = f"{asset.lower()}-updown-{interval}m-{next_start_ts}"

        if not market:
            logger.warning(
                "[FastReversal] ⚠️  Next market not found in active list: %s "
                "(Gamma lag?) — retrying next tick",
                next_slug,
            )
            return False

        token_id = market["up_token_id"] if reverse_side == "UP" else market.get("down_token_id")
        if not token_id:
            logger.warning("[FastReversal] No %s token for %s", reverse_side, next_slug)
            return True  # treat as handled — no point retrying

        signal_label = f"CLOB={clob_mid:.3f}" if clob_mid is not None else f"spot={usd_move:+.1f}USD"
        now = int(time.time())
        delta_s = next_start_ts - now  # negative = current window already closed

        logger.info(
            "[FastReversal] 🚀 REVERSAL [%s]: %s %sm → %s @ %.2f "
            "(size=%.4f) | %s | %+ds from window open | slug=%s",
            signal_source, asset, interval, reverse_side,
            self._order_price, self._order_size,
            signal_label, -delta_s, next_slug,
        )

        try:
            args = OrderArgs(
                token_id=str(token_id),
                price=float(self._order_price),
                size=float(self._order_size),
                side=BUY,
            )
            response = self._client.create_and_post_order(args)
            _record_reversal_order(
                slug=next_slug, asset=asset, interval=interval,
                token_id=str(token_id), predicted_side=reverse_side,
                price=self._order_price, size=self._order_size,
                trigger_usd_move=usd_move, trigger_clob_price=clob_mid,
                signal_source=signal_source, status="submitted",
                response_json=response if isinstance(response, dict) else {"resp": str(response)},
            )
            logger.info(
                "[FastReversal] ✅ Order SUCCESS: %s %sm %s | %s | slug=%s",
                asset, interval, reverse_side, signal_label, next_slug,
            )
        except Exception as exc:
            _record_reversal_order(
                slug=next_slug, asset=asset, interval=interval,
                token_id=str(token_id), predicted_side=reverse_side,
                price=self._order_price, size=self._order_size,
                trigger_usd_move=usd_move, trigger_clob_price=clob_mid,
                signal_source=signal_source, status="failed", error=str(exc),
            )
            logger.error("[FastReversal] ❌ Order failed %s: %s", next_slug, exc)

        return True

    def _detect_and_fire(self, active_markets: list):
        """
        Single pass: detect signal + place order in one shot.
        Since next markets are pre-created, no queueing/waiting needed.
        """
        now = int(time.time())

        # Index current markets by (asset, interval) for fast CLOB lookups
        by_ai: Dict[tuple, Dict] = {}
        for m in active_markets:
            a = m.get("asset", "").upper()
            iv = int(m.get("interval_minutes", 0))
            if a and iv:
                by_ai[(a, iv)] = m

        for asset in ASSETS:
            for interval in INTERVALS:
                start_ts, end_ts = _window_boundaries(interval, now)
                time_remaining = end_ts - now
                key = f"{asset}-{interval}-{end_ts}"

                if time_remaining > self._monitor_s:
                    continue  # not in monitor window yet
                if key in self._pending:
                    continue  # already handled this window

                # ── Primary signal: CLOB mid-price of current UP token ──────
                current_market = by_ai.get((asset, interval))
                clob_mid: Optional[float] = None
                clob_signal: Optional[str] = None

                if current_market and current_market.get("up_token_id"):
                    clob_mid = _clob_mid_price(self._client, str(current_market["up_token_id"]))
                    if clob_mid is not None:
                        if clob_mid > CLOB_HIGH_THRESHOLD:
                            clob_signal = "DOWN"
                        elif clob_mid < CLOB_LOW_THRESHOLD:
                            clob_signal = "UP"

                # ── Fallback: spot USD ────────────────────────────────────
                usd_signal: Optional[str] = None
                usd_move: Optional[float] = None
                if clob_signal is None:
                    usd_threshold = self._thresholds.get(asset, 9999)
                    if time_remaining <= 10:
                        usd_threshold *= 0.75  # lower bar in final 10s
                    usd_move, _, _ = _spot_move_in_window(asset, start_ts)
                    if usd_move is not None and abs(usd_move) >= usd_threshold:
                        usd_signal = "DOWN" if usd_move > 0 else "UP"

                signal_direction = clob_signal or usd_signal
                signal_source    = "CLOB" if clob_signal else "SPOT"

                if not signal_direction:
                    continue

                # Dedup: already placed for this exact next window?
                if _already_fired(asset, interval, end_ts):
                    self._pending[key] = True  # suppress future checks
                    continue

                logger.info(
                    "[FastReversal] 🎯 Signal [%s]: %s %sm | "
                    "%s | %ds remaining → reversing to %s",
                    signal_source, asset, interval,
                    f"CLOB={clob_mid:.3f}" if clob_mid is not None else f"spot={usd_move:+.1f}USD",
                    time_remaining, signal_direction,
                )

                # next window slug is deterministic — Polymarket pre-creates 10+ ahead
                placed = self._place_reversal(
                    asset=asset, interval=interval,
                    next_start_ts=end_ts,  # next window starts at current window end
                    reverse_side=signal_direction,
                    usd_move=usd_move, clob_mid=clob_mid,
                    signal_source=signal_source,
                    active_markets=active_markets,
                    time_remaining=time_remaining,
                )

                if placed:
                    self._pending[key] = True  # mark as handled regardless of order success
                else:
                    # Market not found in Gamma yet (rare) — retry next tick but log it
                    logger.debug("[FastReversal] Deferring to next tick for %s %sm window=%s", asset, interval, end_ts)

    def _loop(self):
        self._client = _build_client()
        if not self._client:
            logger.warning("[FastReversal] Disabled: CLOB credentials missing")
            return

        logger.info(
            "[FastReversal] Started | CLOB threshold=%.2f/%.2f | "
            "spot_thresholds=%s | monitor=%ds | price=%.2f size=%.4f",
            CLOB_HIGH_THRESHOLD, CLOB_LOW_THRESHOLD,
            self._thresholds, self._monitor_s,
            self._order_price, self._order_size,
        )

        while self.running:
            try:
                active = self._get_active_markets()
                self._detect_and_fire(active)
            except Exception as exc:
                logger.error("[FastReversal] Loop error: %s", exc, exc_info=True)
            time.sleep(self.CHECK_INTERVAL_S)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fast-reversal")
        self._thread.start()

    def stop(self):
        self.running = False


_fast_reversal = FastReversalTrader()


def start_fast_reversal():
    _fast_reversal.start()


def stop_fast_reversal():
    _fast_reversal.stop()
