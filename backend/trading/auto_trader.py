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
from config import config
from db import get_connection
from ingestion.polymarket import fetch_active_crypto_markets

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
        conn.execute(
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
    finally:
        if close_conn:
            conn.close()


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

        while self.running:
            try:
                logger.info(
                    "Checking patterns: top_n=%s min_win_rate=%.1f%% order_price=%.2f order_size=%.4f",
                    TOP_PATTERN_COUNT,
                    MIN_WIN_RATE_PCT,
                    self.order_price,
                    self.order_size,
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

                        try:
                            args = OrderArgs(
                                token_id=str(token_id),
                                price=float(self.order_price),
                                size=float(self.order_size),
                                side=BUY,
                            )
                            response = self._client.create_and_post_order(args)
                            _record_order(
                                slug=slug,
                                asset=asset,
                                interval=interval,
                                token_id=str(token_id),
                                pattern_str=pattern_str,
                                predicted_side=predicted_side,
                                price=self.order_price,
                                size=self.order_size,
                                status="submitted",
                                response_json=response if isinstance(response, dict) else {"response": str(response)},
                            )
                            logger.info(
                                "Order SUCCESS: slug=%s asset=%s interval=%sm side=%s price=%.2f size=%.4f pattern=%s",
                                slug,
                                asset,
                                interval,
                                predicted_side,
                                self.order_price,
                                self.order_size,
                                pattern_str,
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
                                size=self.order_size,
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
