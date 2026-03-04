"""
Real-time spot price feed.
Primary: Binance WebSocket
Fallback: Binance REST API polling
"""
import time
import json
import threading
import logging
import requests
import websocket

from db import get_connection

logger = logging.getLogger(__name__)

BINANCE_WS = "wss://stream.binance.com:9443/stream"
BINANCE_REST = "https://api.binance.com/api/v3/ticker/price"

SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "MATIC": "MATICUSDT",
}

# Shared in-memory price cache
_prices: dict = {}
_lock = threading.Lock()


def get_spot_price(asset: str) -> float | None:
    with _lock:
        return _prices.get(asset.upper())


def set_spot_price(asset: str, price: float):
    with _lock:
        _prices[asset.upper()] = price


def store_spot_price(asset: str, price: float, source: str = "binance"):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO spot_prices (asset, price_usd, source, captured_at) VALUES (?, ?, ?, ?)",
            (asset.upper(), price, source, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_spot_prices_rest():
    """Fetch current prices via REST (fallback)."""
    try:
        symbols = list(SYMBOL_MAP.values())
        resp = requests.get(BINANCE_REST, timeout=5)
        data = resp.json()
        for item in data:
            for asset, sym in SYMBOL_MAP.items():
                if item["symbol"] == sym:
                    price = float(item["price"])
                    set_spot_price(asset, price)
        return True
    except Exception as e:
        logger.error(f"REST spot fetch failed: {e}")
        return False


class BinanceSpotFeed:
    """WebSocket-based Binance spot price feed."""

    def __init__(self):
        self.ws = None
        self.running = False
        self._thread = None

    def _build_stream_url(self) -> str:
        streams = "/".join(f"{sym.lower()}@aggTrade" for sym in SYMBOL_MAP.values())
        return f"{BINANCE_WS}?streams={streams}"

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            stream = data.get("stream", "")
            payload = data.get("data", {})
            if payload.get("e") == "aggTrade":
                symbol = payload.get("s", "")
                price = float(payload.get("p", 0))
                for asset, sym in SYMBOL_MAP.items():
                    if sym == symbol:
                        set_spot_price(asset, price)
                        break
        except Exception as e:
            logger.debug(f"WS message error: {e}")

    def _on_error(self, ws, error):
        logger.warning(f"Binance WS error: {error}")

    def _on_close(self, ws, *args):
        logger.info("Binance WS closed, will reconnect...")
        if self.running:
            time.sleep(3)
            self.start()

    def _on_open(self, ws):
        logger.info("Binance WS connected")

    def start(self):
        self.running = True
        url = self._build_stream_url()
        self.ws = websocket.WebSocketApp(
            url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open,
        )
        self._thread = threading.Thread(
            target=self.ws.run_forever,
            kwargs={"ping_interval": 20, "ping_timeout": 10},
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self.running = False
        if self.ws:
            self.ws.close()


class SpotPricePoller:
    """Fallback: polls Binance REST every 5s and stores to DB."""

    def __init__(self, interval_seconds: int = 5):
        self.interval = interval_seconds
        self._thread = None
        self.running = False

    def _loop(self):
        while self.running:
            try:
                fetch_spot_prices_rest()
                for asset, price in list(_prices.items()):
                    if price:
                        store_spot_price(asset, price, "binance_rest")
            except Exception as e:
                logger.error(f"Spot poller error: {e}")
            time.sleep(self.interval)

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Spot price poller started")

    def stop(self):
        self.running = False


# Singleton instances
_ws_feed = BinanceSpotFeed()
_poller = SpotPricePoller()


def start_spot_feed():
    """Start the spot price feed (WS + REST fallback poller)."""
    # Initial REST fetch to populate cache immediately
    fetch_spot_prices_rest()
    # Start WebSocket
    _ws_feed.start()
    # Start REST poller for DB persistence
    _poller.start()
    logger.info("Spot feed started")


def stop_spot_feed():
    _ws_feed.stop()
    _poller.stop()
