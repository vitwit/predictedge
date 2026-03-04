import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Polymarket CLOB
    CLOB_HOST: str = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
    CLOB_API_KEY: str = os.getenv("CLOB_API_KEY", "")
    CLOB_SECRET: str = os.getenv("CLOB_SECRET", "")
    CLOB_PASS_PHRASE: str = os.getenv("CLOB_PASS_PHRASE", "")
    GAMMA_HOST: str = os.getenv("GAMMA_HOST", "https://gamma-api.polymarket.com")

    # Wallet
    PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")
    WALLET_ADDRESS: str = os.getenv("WALLET_PROXY_SAFE", "")

    # OpenAI for AI Co-Pilot
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Database
    DB_PATH: str = os.getenv("DB_PATH", "predictedge.db")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    POLYGON_RPC_URL: str = os.getenv("POLYGON_RPC_URL", "")

    # Data ingestion settings
    TICK_INTERVAL_SECONDS: int = 5
    ASSETS: list = ["BTC", "ETH", "SOL", "XRP", "MATIC"]
    INTERVALS: list = [5, 15, 60]
    DEFAULT_ORDER_SIZE: float = float(os.getenv("DEFAULT_ORDER_SIZE", "5"))
    AUTO_TRADE_LOOP_SECONDS: int = int(os.getenv("AUTO_TRADE_LOOP_SECONDS", "30"))
    TOP_PATTERN_COUNT: int = int(os.getenv("TOP_PATTERN_COUNT", "10"))
    MIN_PATTERN_WIN_RATE_PCT: float = float(os.getenv("MIN_PATTERN_WIN_RATE_PCT", "55"))
    AUTO_CLAIM_INTERVAL_SECONDS: int = int(os.getenv("AUTO_CLAIM_INTERVAL_SECONDS", "600"))

    # Binance WebSocket
    BINANCE_WS_URL: str = "wss://stream.binance.com:9443/ws"
    BINANCE_REST_URL: str = "https://api.binance.com/api/v3"

    # Feature flags
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() == "true"

config = Config()
