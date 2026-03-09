"""
Streak Reversal Trader — Simple, focused BTC mean-reversion strategy.

Two independent triggers — both place BUY at 40¢ for $100:

1. STREAK REVERSAL
   BTC 15m/5m: If last 6 consecutive results are all UP → BUY DOWN on current market

2. USD MOMENTUM REVERSAL
   BTC 15m/5m: If the just-closed market had spot move ≥ $200 USD (up or down),
   place a reverse order on the CURRENT (next) active market.
   - prev closed UP with spot +$200 → BUY DOWN
   - prev closed DOWN with spot -$200 → BUY UP
"""

import json
import logging
import threading
import time
from typing import List, Optional, Dict

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from py_clob_client.order_builder.constants import BUY

from config import config
from db import get_connection
from ingestion.polymarket import fetch_active_crypto_markets

logger = logging.getLogger(__name__)

# ── Strategy parameters ───────────────────────────────────────────────────────

ORDER_PRICE      = float(config.STREAK_REVERSAL_ORDER_PRICE)
ORDER_SIZE       = float(config.STREAK_REVERSAL_SIZE)
LOOP_SLEEP       = int(config.STREAK_REVERSAL_LOOP_S)
USD_REVERSAL_MIN = float(config.STREAK_USD_REVERSAL_THRESHOLD)   # legacy fallback
USD_REVERSAL_MIN_BY_INTERVAL = {
    5: float(getattr(config, "STREAK_USD_REVERSAL_THRESHOLD_5M", USD_REVERSAL_MIN)),
    15: float(getattr(config, "STREAK_USD_REVERSAL_THRESHOLD_15M", 400.0)),
}

STREAK_TARGETS = [
    {"asset": "BTC", "interval": 15, "streak": 6, "direction": "UP",   "trade_side": "DOWN"},
    {"asset": "BTC", "interval": 15, "streak": 6, "direction": "DOWN", "trade_side": "UP"},
    {"asset": "BTC", "interval":  5, "streak": 6, "direction": "UP",   "trade_side": "DOWN"},
    {"asset": "BTC", "interval":  5, "streak": 6, "direction": "DOWN", "trade_side": "UP"},
]

USD_REVERSAL_TARGETS = [
    {"asset": "BTC", "interval": 15},
    {"asset": "BTC", "interval":  5},
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _recent_outcomes(asset: str, interval: int, n: int) -> List[str]:
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT winner_side FROM market_resolutions
            WHERE asset = ? AND interval_minutes = ? AND winner_side IS NOT NULL
            ORDER BY start_ts DESC LIMIT ?
            """,
            (asset, interval, n),
        ).fetchall()
        conn.close()
        outcomes = [r[0] for r in rows]
        outcomes.reverse()   # oldest → newest
        return outcomes
    except Exception as e:
        logger.error("streak: _recent_outcomes failed: %s", e)
        return []


def _already_traded(slug: str, trigger_type: str = "STREAK_REVERSAL") -> bool:
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM auto_trade_orders WHERE slug=? AND trigger_type=?",
            (slug, trigger_type),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _already_traded_any(slug: str) -> bool:
    """True if we already placed any order for this slug."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM auto_trade_orders WHERE slug=? LIMIT 1",
            (slug,),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _last_closed_usd_move(asset: str, interval: int) -> Optional[Dict]:
    """Return spot_change_usd and winner_side of the most recently resolved market."""
    try:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT slug, spot_change_usd, winner_side
            FROM market_resolutions
            WHERE asset = ? AND interval_minutes = ?
              AND winner_side IS NOT NULL
              AND spot_change_usd IS NOT NULL
            ORDER BY end_ts DESC LIMIT 1
            """,
            (asset, interval),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error("usd_move query failed: %s", e)
        return None


def _record_order(
    slug: str, asset: str, interval: int, token_id: str,
    pattern_str: str, predicted_side: str, trigger_type: str,
    status: str, response_json=None, error: str = None,
):
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT OR IGNORE INTO auto_trade_orders
            (slug, asset, interval_minutes, token_id, pattern_str, predicted_side,
             order_price, order_size, status, response_json, error, trigger_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slug, asset, interval, token_id,
                pattern_str, predicted_side,
                ORDER_PRICE, ORDER_SIZE,
                status,
                json.dumps(response_json) if response_json else None,
                error,
                trigger_type,
                int(time.time()),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("streak: _record_order failed: %s", e)


def _build_client() -> Optional[ClobClient]:
    if not config.PRIVATE_KEY:
        logger.warning("[streak] PRIVATE_KEY missing — trader disabled")
        return None
    if not (config.CLOB_API_KEY and config.CLOB_SECRET and config.CLOB_PASS_PHRASE):
        logger.warning("[streak] CLOB credentials missing — trader disabled")
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


# ── Main Trader ───────────────────────────────────────────────────────────────

class StreakReversalTrader:
    def __init__(self):
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[ClobClient] = None
        # Prevent reusing the same resolved market as USD trigger source repeatedly.
        # key=(asset, interval) -> source resolved slug already consumed.
        self._usd_source_consumed: Dict[tuple, str] = {}

    def _get_fill_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """
        Get the best available fill price from the CLOB orderbook.
        For BUY: we hit the lowest ask. Cap at ORDER_PRICE if asks are above it.
        """
        try:
            book = self._client.get_order_book(token_id)
            asks = sorted(book.asks or [], key=lambda x: float(x.price))
            bids = sorted(book.bids or [], key=lambda x: -float(x.price))

            if asks:
                best_ask = float(asks[0].price)
                # Use best ask, but cap at our max price
                price = min(best_ask, ORDER_PRICE)
                # Don't buy if best ask is absurdly high (>0.60 = overpaying)
                if best_ask > 0.60:
                    logger.info("[streak] Best ask %.2f too high — skip", best_ask)
                    return None
                return round(price, 2)
            elif bids:
                # No asks — place at midpoint between best bid and ORDER_PRICE
                best_bid = float(bids[0].price)
                price = min(round((best_bid + ORDER_PRICE) / 2, 2), ORDER_PRICE)
                return price
            else:
                return ORDER_PRICE
        except Exception as e:
            logger.debug("[streak] Orderbook fetch failed: %s", e)
            return ORDER_PRICE

    def _place_order(self, market: Dict, asset: str, interval: int,
                     trade_side: str, pattern_str: str, trigger_type: str) -> bool:
        """Place a BUY order at best available price. Returns True on success."""
        slug = market["slug"]
        token_id = market.get("up_token_id") if trade_side == "UP" else market.get("down_token_id")
        if not token_id:
            logger.warning("[streak] No token_id for %s %sm side=%s", asset, interval, trade_side)
            return False

        end_ts = int(market.get("end_ts") or 0)
        if end_ts > 0:
            remaining = end_ts - int(time.time())
            if remaining < 30:
                logger.info("[streak] Too late (%ds left) — skip %s", remaining, slug)
                return False

        # Get best fill price from orderbook
        fill_price = self._get_fill_price(str(token_id))
        if fill_price is None:
            logger.info("[streak] No viable price for %s %sm %s — skip", asset, interval, trade_side)
            return False

        # Adjust size: spend ORDER_SIZE USDC worth at the fill price
        # size = number of outcome tokens to buy
        actual_size = round(ORDER_SIZE / fill_price, 2) if fill_price > 0 else ORDER_SIZE

        try:
            args = OrderArgs(token_id=str(token_id), price=fill_price, size=ORDER_SIZE, side=BUY)
            response = self._client.create_and_post_order(args)
            _record_order(
                slug=slug, asset=asset, interval=interval,
                token_id=str(token_id), pattern_str=pattern_str,
                predicted_side=trade_side, trigger_type=trigger_type,
                status="submitted",
                response_json=response if isinstance(response, dict) else {"r": str(response)},
            )
            logger.info(
                "[streak] ✅ ORDER: %s %sm | BUY %s @ %.2f | $%.0f | %s | slug=%s",
                asset, interval, trade_side, fill_price, ORDER_SIZE, trigger_type, slug,
            )
            return True
        except Exception as exc:
            _record_order(
                slug=slug, asset=asset, interval=interval,
                token_id=str(token_id), pattern_str=pattern_str,
                predicted_side=trade_side, trigger_type=trigger_type,
                status="failed", error=str(exc),
            )
            logger.error("[streak] Order failed for %s: %s", slug, exc)
            return False

    def _loop(self):
        self._client = _build_client()
        if not self._client:
            return

        logger.info(
            "[streak] Started: price=%.2f size=$%.0f | streak triggers=%s | usd_thresholds={5m:$%.0f,15m:$%.0f}",
            ORDER_PRICE, ORDER_SIZE,
            [(t["asset"], t["interval"], t["streak"]) for t in STREAK_TARGETS],
            USD_REVERSAL_MIN_BY_INTERVAL.get(5, USD_REVERSAL_MIN),
            USD_REVERSAL_MIN_BY_INTERVAL.get(15, USD_REVERSAL_MIN),
        )

        while self.running:
            try:
                active = fetch_active_crypto_markets()
                # Deterministically pick the nearest-to-expiry active market per asset/interval.
                now_ts = int(time.time())
                market_map: Dict = {}
                for m in active:
                    if m.get("asset") != "BTC":
                        continue
                    iv = int(m.get("interval_minutes") or 0)
                    if iv not in {5, 15}:
                        continue
                    end_ts = int(m.get("end_ts") or 0)
                    if end_ts <= now_ts:
                        continue
                    key = (m["asset"], iv)
                    prev = market_map.get(key)
                    if prev is None or int(prev.get("end_ts") or 0) > end_ts:
                        market_map[key] = m

                traded_this_cycle = set()

                # ── Trigger 1: Streak Reversal ─────────────────────────────
                for target in STREAK_TARGETS:
                    asset, interval, streak_n = target["asset"], target["interval"], target["streak"]
                    side_needed, trade_side = target["direction"], target["trade_side"]

                    market = market_map.get((asset, interval))
                    if not market:
                        continue
                    slug = market["slug"]

                    if slug in traded_this_cycle:
                        continue
                    if _already_traded_any(slug):
                        continue

                    recent = _recent_outcomes(asset, interval, streak_n)
                    if len(recent) < streak_n:
                        continue
                    if not all(r == side_needed for r in recent[-streak_n:]):
                        continue

                    logger.info(
                        "[streak] 🎯 STREAK: %s %sm | %dx %s → BUY %s",
                        asset, interval, streak_n, side_needed, trade_side,
                    )
                    if self._place_order(
                        market, asset, interval, trade_side,
                        f"{streak_n}x_{side_needed}_STREAK", "STREAK_REVERSAL"
                    ):
                        traded_this_cycle.add(slug)

                # ── Trigger 2: USD Momentum Reversal ───────────────────────
                for target in USD_REVERSAL_TARGETS:
                    asset, interval = target["asset"], target["interval"]

                    market = market_map.get((asset, interval))
                    if not market:
                        continue
                    slug = market["slug"]

                    if slug in traded_this_cycle:
                        continue
                    if _already_traded_any(slug):
                        continue

                    last = _last_closed_usd_move(asset, interval)
                    if not last:
                        continue

                    usd_move = float(last["spot_change_usd"] or 0)
                    prev_winner = last["winner_side"]
                    source_slug = str(last.get("slug") or "")
                    source_key = (asset, interval)

                    # Fire only once per newly closed source market.
                    if source_slug and self._usd_source_consumed.get(source_key) == source_slug:
                        continue

                    usd_threshold = float(USD_REVERSAL_MIN_BY_INTERVAL.get(interval, USD_REVERSAL_MIN))
                    # Strict threshold rule from user:
                    # 5m: > $200, 15m: > $400
                    if abs(usd_move) <= usd_threshold:
                        # Mark source as consumed so stale source is not reused repeatedly.
                        if source_slug:
                            self._usd_source_consumed[source_key] = source_slug
                        continue

                    # Reverse side based on previous winner direction.
                    trade_side = "DOWN" if str(prev_winner).upper() == "UP" else "UP"
                    direction_label = f"+${usd_move:.0f}" if usd_move > 0 else f"-${abs(usd_move):.0f}"

                    logger.info(
                        "[streak] 💰 USD_REVERSAL: %s %sm | prev closed %s (%s USD) → BUY %s",
                        asset, interval, prev_winner, direction_label, trade_side,
                    )
                    if self._place_order(
                        market, asset, interval, trade_side,
                        f"USD_REV_{direction_label}", "USD_REVERSAL"
                    ):
                        traded_this_cycle.add(slug)
                    if source_slug:
                        self._usd_source_consumed[source_key] = source_slug

            except Exception as loop_exc:
                logger.error("[streak] Loop error: %s", loop_exc)

            time.sleep(LOOP_SLEEP)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="StreakReversalTrader")
        self._thread.start()
        logger.info("[streak] Thread started")

    def stop(self):
        self.running = False
        logger.info("[streak] Stopped")


_trader = StreakReversalTrader()


def start_streak_reversal_trader():
    _trader.start()


def stop_streak_reversal_trader():
    _trader.stop()
