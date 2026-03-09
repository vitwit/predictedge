"""
Auto-trading worker:
- Continuously scans top-10 patterns (min 55% win rate)
- If a top pattern matches current streak context, places BUY order at 0.40
  on the predicted next outcome token.
"""
import json
import logging
import threading
import time
from typing import Dict, List, Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from py_clob_client.order_builder.constants import BUY

from analytics.patterns import find_top_patterns
from analytics.edge_monitor import sync_from_resolved_trades, update_edge_stats
from config import config
from db import get_connection
from ingestion.polymarket import fetch_active_crypto_markets
from trading.decision_policy import (
    SignalInputs,
    evaluate as policy_evaluate,
    persist_signal_event_with_order,
)
from trading.risk_manager import get_risk_manager

logger = logging.getLogger(__name__)

DEFAULT_ORDER_PRICE = 0.40
DEFAULT_ORDER_SIZE = config.DEFAULT_ORDER_SIZE
DEFAULT_LOOP_SECONDS = config.AUTO_TRADE_LOOP_SECONDS
MIN_PATTERN_SAMPLES = 20
TOP_PATTERN_COUNT = config.TOP_PATTERN_COUNT
MIN_WIN_RATE_PCT = config.MIN_PATTERN_WIN_RATE_PCT


def _recent_outcomes(asset: str, interval: int, n: int, conn=None) -> List[str]:
    close_conn = conn is None
    if close_conn:
        conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT winner_side
            FROM market_resolutions
            WHERE asset = ? AND interval_minutes = ? AND winner_side IS NOT NULL
            ORDER BY start_ts DESC
            LIMIT ?
            """,
            (asset, interval, n),
        ).fetchall()
        values = [r[0] for r in rows]
        values.reverse()
        return values
    finally:
        if close_conn:
            conn.close()


def _already_executed(slug: str, pattern_str: str, predicted_side: str, conn=None) -> bool:
    close_conn = conn is None
    if close_conn:
        conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id FROM auto_trade_orders
            WHERE slug = ? AND pattern_str = ? AND predicted_side = ?
            LIMIT 1
            """,
            (slug, pattern_str, predicted_side),
        ).fetchone()
        return row is not None
    finally:
        if close_conn:
            conn.close()


def _record_order(
    slug: str,
    asset: str,
    interval: int,
    token_id: str,
    pattern_str: str,
    predicted_side: str,
    price: float,
    size: float,
    status: str,
    response_json: Optional[Dict] = None,
    error: Optional[str] = None,
    conn=None,
):
    close_conn = conn is None
    if close_conn:
        conn = get_connection()
    try:
        now = int(time.time())
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO auto_trade_orders
            (slug, asset, interval_minutes, token_id, pattern_str, predicted_side,
             order_price, order_size, status, response_json, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slug,
                asset,
                interval,
                token_id,
                pattern_str,
                predicted_side,
                price,
                size,
                status,
                json.dumps(response_json) if response_json is not None else None,
                error,
                now,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if close_conn:
            conn.close()


def _live_market_snapshot(client: ClobClient, token_id: str, market: Dict, interval: int) -> Dict:
    """
    Pull live spread, depth, imbalance, and time-remaining for a market.
    Returns a dict of microstructure fields for the decision policy.
    Falls back gracefully if CLOB call fails.
    """
    snap = {
        "spread_cents": 0.0,
        "bid_depth_5c": 0.0,
        "ask_depth_5c": 0.0,
        "depth_imbalance": 0.0,
        "time_remaining_s": interval * 60,
    }
    try:
        book = client.get_order_book(token_id)
        bids = sorted(book.bids or [], key=lambda x: -float(x.price))
        asks = sorted(book.asks or [], key=lambda x: float(x.price))
        if bids and asks:
            best_bid = float(bids[0].price)
            best_ask = float(asks[0].price)
            snap["spread_cents"] = round((best_ask - best_bid) * 100, 2)
            mid = (best_bid + best_ask) / 2.0
            bid_depth = sum(float(b.size) * float(b.price) for b in bids if mid - float(b.price) <= 0.05)
            ask_depth = sum(float(a.size) * float(a.price) for a in asks if float(a.price) - mid <= 0.05)
            snap["bid_depth_5c"] = round(bid_depth, 2)
            snap["ask_depth_5c"] = round(ask_depth, 2)
            total_depth = bid_depth + ask_depth
            if total_depth > 0:
                snap["depth_imbalance"] = round((bid_depth - ask_depth) / total_depth, 4)
    except Exception as e:
        logger.debug("Snapshot fetch failed for token=%s: %s", token_id, e)

    try:
        start_ts = int(market.get("start_ts") or 0)
        end_ts = int(market.get("end_ts") or 0)
        if end_ts > 0:
            snap["time_remaining_s"] = max(0, end_ts - int(time.time()))
        elif start_ts > 0:
            elapsed = int(time.time()) - start_ts
            snap["time_remaining_s"] = max(0, interval * 60 - elapsed)
    except Exception:
        pass

    return snap


def _build_client() -> Optional[ClobClient]:
    if not config.PRIVATE_KEY:
        logger.warning("Auto trader disabled: PRIVATE_KEY is missing")
        return None
    if not (config.CLOB_API_KEY and config.CLOB_SECRET and config.CLOB_PASS_PHRASE):
        logger.warning("Auto trader disabled: CLOB API credentials are missing")
        return None

    creds = ApiCreds(
        api_key=config.CLOB_API_KEY,
        api_secret=config.CLOB_SECRET,
        api_passphrase=config.CLOB_PASS_PHRASE,
    )
    # signature_type=2 + funder is typically required for proxy safe wallets.
    return ClobClient(
        host=config.CLOB_HOST,
        chain_id=137,
        key=config.PRIVATE_KEY,
        creds=creds,
        signature_type=2,
        funder=config.WALLET_ADDRESS or None,
    )


def _evaluate_best_signal(asset: str, interval: int) -> Optional[Dict]:
    all_patterns = find_top_patterns(asset, interval, min_samples=MIN_PATTERN_SAMPLES)
    if not all_patterns:
        return None

    eligible = []
    for p in all_patterns:
        up_pct = float(p.get("up_pct", 0) or 0)
        down_pct = float(p.get("down_pct", 0) or 0)
        win_rate = max(up_pct, down_pct)
        if win_rate >= MIN_WIN_RATE_PCT:
            p = dict(p)
            p["win_rate"] = win_rate
            eligible.append(p)

    if not eligible:
        return None

    top_patterns = sorted(
        eligible,
        key=lambda x: (x.get("win_rate", 0), x.get("edge", 0), x.get("sample_count", 0)),
        reverse=True,
    )[:TOP_PATTERN_COUNT]

    matched = []
    conn = get_connection()
    try:
        for p in top_patterns:
            pattern = p.get("pattern") or []
            if not pattern:
                continue
            current = _recent_outcomes(asset, interval, len(pattern), conn=conn)
            if current == pattern:
                predicted = "UP" if float(p.get("up_pct", 0)) >= float(p.get("down_pct", 0)) else "DOWN"
                matched.append(
                    {
                        "asset": asset,
                        "interval": interval,
                        "pattern": pattern,
                        "pattern_str": p.get("pattern_str"),
                        "edge": float(p.get("edge", 0)),
                        "win_rate": float(p.get("win_rate", max(p.get("up_pct", 0), p.get("down_pct", 0)))),
                        "predicted_side": predicted,
                    }
                )
    finally:
        conn.close()

    if not matched:
        return None
    return sorted(matched, key=lambda x: (x["win_rate"], x["edge"]), reverse=True)[0]


class AutoTrader:
    def __init__(
        self,
        loop_seconds: int = DEFAULT_LOOP_SECONDS,
        order_price: float = DEFAULT_ORDER_PRICE,
        order_size: float = DEFAULT_ORDER_SIZE,
    ):
        self.loop_seconds = loop_seconds
        self.order_price = order_price
        self.order_size = order_size
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[ClobClient] = None

    def _loop(self):
        self._client = _build_client()
        if not self._client:
            return

        # Initialize edge monitor from historical trades
        try:
            sync_from_resolved_trades()
        except Exception as e:
            logger.warning("edge monitor sync failed: %s", e)

        # Periodically update edge stats from new resolutions
        _last_edge_sync = [int(time.time())]

        while self.running:
            try:
                # Re-sync edge stats every 10 minutes
                now = int(time.time())
                if now - _last_edge_sync[0] > 600:
                    try:
                        sync_from_resolved_trades()
                        _last_edge_sync[0] = now
                    except Exception as e:
                        logger.warning("edge monitor re-sync failed: %s", e)

                rm = get_risk_manager()
                risk_state = rm.get_state()
                if risk_state["circuit_breaker_active"]:
                    rem = risk_state["circuit_breaker_remaining_s"]
                    logger.info("Circuit breaker active (%ds remaining) — skipping loop", rem)
                    time.sleep(self.loop_seconds)
                    continue

                logger.info(
                    "Checking patterns: top_n=%s min_win_rate=%.1f%% order_price=%.2f order_size=%.4f"
                    " | open_positions=%d invested=%.2f pnl=%.2f streak=%d",
                    TOP_PATTERN_COUNT,
                    MIN_WIN_RATE_PCT,
                    self.order_price,
                    self.order_size,
                    risk_state["open_position_count"],
                    risk_state["total_invested"],
                    risk_state["realized_pnl"],
                    risk_state["consecutive_losses"],
                )
                active = fetch_active_crypto_markets()
                by_asset_interval = {
                    (m["asset"], int(m["interval_minutes"])): m
                    for m in active
                    if m.get("asset") in {"BTC", "ETH", "SOL", "XRP"} and m.get("interval_minutes") in {5, 15, 60}
                }

                for asset in ("BTC", "ETH", "SOL", "XRP"):
                    for interval in (5, 15, 60):
                        market = by_asset_interval.get((asset, interval))
                        if not market:
                            continue

                        signal = _evaluate_best_signal(asset, interval)
                        if not signal:
                            continue
                        logger.info(
                            "Pattern matched: asset=%s interval=%sm pattern=%s predicted=%s win_rate=%.1f edge=%.1f",
                            asset,
                            interval,
                            signal.get("pattern_str"),
                            signal.get("predicted_side"),
                            float(signal.get("win_rate", 0)),
                            float(signal.get("edge", 0)),
                        )

                        slug = market["slug"]
                        predicted_side = signal["predicted_side"]
                        token_id = market["up_token_id"] if predicted_side == "UP" else market.get("down_token_id")
                        if not token_id:
                            continue

                        pattern_str = signal["pattern_str"] or "UNKNOWN"
                        conn = get_connection()
                        try:
                            if _already_executed(slug, pattern_str, predicted_side, conn=conn):
                                continue
                        finally:
                            conn.close()

                        # ── Decision Policy gate ─────────────────────────────
                        snap = _live_market_snapshot(self._client, str(token_id), market, interval)
                        # Enrich snapshot with CLOB mid
                        bids_raw = []
                        asks_raw = []
                        clob_mid = None
                        try:
                            book = self._client.get_order_book(str(token_id))
                            bids_raw = sorted(book.bids or [], key=lambda x: -float(x.price))
                            asks_raw = sorted(book.asks or [], key=lambda x: float(x.price))
                            if bids_raw and asks_raw:
                                clob_mid = (float(bids_raw[0].price) + float(asks_raw[0].price)) / 2.0
                        except Exception:
                            pass

                        sig_inputs = SignalInputs(
                            slug=slug,
                            asset=asset,
                            interval_minutes=interval,
                            pattern_str=pattern_str,
                            predicted_side=predicted_side,
                            win_rate=float(signal.get("win_rate", 0)),
                            edge_pct=float(signal.get("edge", 0)),
                            sample_count=int(signal.get("sample_count", 0)),
                            spread_cents=snap["spread_cents"],
                            bid_depth_5c=snap["bid_depth_5c"],
                            ask_depth_5c=snap["ask_depth_5c"],
                            depth_imbalance=snap["depth_imbalance"],
                            time_remaining_s=snap["time_remaining_s"],
                            clob_mid=clob_mid,
                            bid_size=float(bids_raw[0].size) if bids_raw else None,
                            ask_size=float(asks_raw[0].size) if asks_raw else None,
                            order_price=float(self.order_price),
                            order_size=float(self.order_size),
                        )
                        decision = policy_evaluate(sig_inputs)
                        if not decision.approved:
                            logger.info(
                                "Policy REJECT: %s %s %sm %s | reasons=%s ev=%.4f conf=%.0f",
                                pattern_str, asset, interval, predicted_side,
                                decision.reject_reasons, decision.ev_score, decision.confidence,
                            )
                            continue
                        # ────────────────────────────────────────────────────

                        # Use Kelly-recommended size, capped by config
                        actual_size = min(decision.recommended_size, float(self.order_size) * 3.0)

                        try:
                            args = OrderArgs(
                                token_id=str(token_id),
                                price=float(self.order_price),
                                size=float(actual_size),
                                side=BUY,
                            )
                            response = self._client.create_and_post_order(args)
                            order_id = _record_order(
                                slug=slug,
                                asset=asset,
                                interval=interval,
                                token_id=str(token_id),
                                pattern_str=pattern_str,
                                predicted_side=predicted_side,
                                price=self.order_price,
                                size=actual_size,
                                status="submitted",
                                response_json=response if isinstance(response, dict) else {"response": str(response)},
                            )
                            persist_signal_event_with_order(sig_inputs, decision, order_id)

                            # Register with risk manager
                            get_risk_manager().open_position(
                                token_id=str(token_id),
                                slug=slug,
                                asset=asset,
                                interval=interval,
                                size=actual_size,
                                price=self.order_price,
                                signal_type="PATTERN",
                            )

                            logger.info(
                                "Order SUCCESS: slug=%s asset=%s interval=%sm side=%s "
                                "price=%.2f size=%.4f(kelly) pattern=%s ev=%.4f conf=%.0f "
                                "calib_p=%.3f regime=%s llm=%s",
                                slug, asset, interval, predicted_side,
                                self.order_price, actual_size, pattern_str,
                                decision.ev_score, decision.confidence,
                                decision.calibrated_p_win, decision.regime,
                                decision.llm_decision or "n/a",
                            )
                        except Exception as exc:
                            _record_order(
                                slug=slug,
                                asset=asset,
                                interval=interval,
                                token_id=str(token_id),
                                pattern_str=pattern_str,
                                predicted_side=predicted_side,
                                price=self.order_price,
                                size=actual_size,
                                status="failed",
                                error=str(exc),
                            )
                            logger.error("Auto order failed for %s: %s", slug, exc)
            except Exception as loop_exc:
                logger.error("Auto trader loop error: %s", loop_exc)
            time.sleep(self.loop_seconds)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Auto trader started")

    def stop(self):
        self.running = False


_auto_trader = AutoTrader()


def start_auto_trader():
    _auto_trader.start()


def stop_auto_trader():
    _auto_trader.stop()
