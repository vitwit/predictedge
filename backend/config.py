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

    # OpenAI for AI Co-Pilot (and LLM gate fallback)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "openai/gpt-5-nano")

    # Database
    DB_PATH: str = os.getenv("DB_PATH", "predictedge.db")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8888"))
    POLYGON_RPC_URL: str = os.getenv("POLYGON_RPC_URL", "")

    # Data ingestion settings
    TICK_INTERVAL_SECONDS: int = 5
    ASSETS: list = ["BTC", "ETH", "SOL", "XRP", "MATIC"]
    INTERVALS: list = [5, 15, 60]
    DEFAULT_ORDER_SIZE: float = float(os.getenv("DEFAULT_ORDER_SIZE", "5"))
    AUTO_TRADE_LOOP_SECONDS: int = int(os.getenv("AUTO_TRADE_LOOP_SECONDS", "30"))
    TOP_PATTERN_COUNT: int = int(os.getenv("TOP_PATTERN_COUNT", "10"))
    MIN_PATTERN_WIN_RATE_PCT: float = float(os.getenv("MIN_PATTERN_WIN_RATE_PCT", "55"))

    # Fast Reversal Trader — triggers on large USD moves OR extreme CLOB prices at window close
    # Comma-separated: BTC:200,ETH:20,SOL:2,XRP:0.10
    REVERSAL_USD_THRESHOLDS: str = os.getenv("REVERSAL_USD_THRESHOLDS", "BTC:200,ETH:20,SOL:2,XRP:0.10")
    REVERSAL_ORDER_SIZE: float = float(os.getenv("REVERSAL_ORDER_SIZE", str(float(os.getenv("DEFAULT_ORDER_SIZE", "5")))))
    REVERSAL_ORDER_PRICE: float = float(os.getenv("REVERSAL_ORDER_PRICE", "0.40"))
    REVERSAL_MONITOR_WINDOW_S: int = int(os.getenv("REVERSAL_MONITOR_WINDOW_S", "45"))
    REVERSAL_ORDER_DELAY_S: int = int(os.getenv("REVERSAL_ORDER_DELAY_S", "0"))  # 0 = fire immediately when next market appears
    REVERSAL_CLOB_HIGH: float = float(os.getenv("REVERSAL_CLOB_HIGH", "0.82"))  # UP token > this → strong UP → reverse DOWN
    REVERSAL_CLOB_LOW:  float = float(os.getenv("REVERSAL_CLOB_LOW",  "0.18"))  # UP token < this → strong DOWN → reverse UP
    AUTO_CLAIM_INTERVAL_SECONDS: int = int(os.getenv("AUTO_CLAIM_INTERVAL_SECONDS", "600"))

    # Binance WebSocket
    BINANCE_WS_URL: str = "wss://stream.binance.com:9443/ws"
    BINANCE_REST_URL: str = "https://api.binance.com/api/v3"

    # OpenRouter LLM gate (synthesis for borderline decisions)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3-5-haiku")
    LLM_GATE_ENABLED: bool = os.getenv("LLM_GATE_ENABLED", "true").lower() == "true"
    LLM_GATE_CONF_MIN: float = float(os.getenv("LLM_GATE_CONF_MIN", "42"))   # call LLM below this
    LLM_GATE_CONF_MAX: float = float(os.getenv("LLM_GATE_CONF_MAX", "62"))   # call LLM above this = no LLM

    # Risk manager
    MAX_CONCURRENT_POSITIONS: int = int(os.getenv("MAX_CONCURRENT_POSITIONS", "4"))
    MAX_CAPITAL_AT_RISK_PCT: float = float(os.getenv("MAX_CAPITAL_AT_RISK_PCT", "20"))
    CONSECUTIVE_LOSS_LIMIT: int = int(os.getenv("CONSECUTIVE_LOSS_LIMIT", "5"))
    CONSECUTIVE_LOSS_PAUSE_S: int = int(os.getenv("CONSECUTIVE_LOSS_PAUSE_S", "1800"))
    DRAWDOWN_LIMIT_PCT: float = float(os.getenv("DRAWDOWN_LIMIT_PCT", "20"))
    ESTIMATED_BALANCE_USDC: float = float(os.getenv("ESTIMATED_BALANCE_USDC", "200"))

    # Kelly position sizing
    KELLY_FRACTION: float = float(os.getenv("KELLY_FRACTION", "0.25"))
    KELLY_BASELINE_F: float = float(os.getenv("KELLY_BASELINE_F", "0.05"))
    KELLY_MIN_MULT: float = float(os.getenv("KELLY_MIN_MULT", "0.5"))
    KELLY_MAX_MULT: float = float(os.getenv("KELLY_MAX_MULT", "3.0"))
    KELLY_CONF_TARGET: float = float(os.getenv("KELLY_CONF_TARGET", "70"))

    # Regime classifier
    REGIME_TREND_THRESHOLD_PCT: float = float(os.getenv("REGIME_TREND_THRESHOLD_PCT", "0.40"))
    REGIME_HIGH_VOL_THRESHOLD_PCT: float = float(os.getenv("REGIME_HIGH_VOL_THRESHOLD_PCT", "0.50"))
    REGIME_CHOP_THRESHOLD_PCT: float = float(os.getenv("REGIME_CHOP_THRESHOLD_PCT", "0.08"))

    # Hotspot detector
    HOTSPOT_BAND_CENTS: float = float(os.getenv("HOTSPOT_BAND_CENTS", "5"))
    HOTSPOT_MIN_DWELL_S: int = int(os.getenv("HOTSPOT_MIN_DWELL_S", "30"))
    IMPULSE_MOVE_CENTS: float = float(os.getenv("IMPULSE_MOVE_CENTS", "20"))
    IMPULSE_TIME_S: int = int(os.getenv("IMPULSE_TIME_S", "5"))

    # Edge monitor
    EDGE_MONITOR_WINDOW: int = int(os.getenv("EDGE_MONITOR_WINDOW", "50"))
    EDGE_MONITOR_MIN_WIN_RATE: float = float(os.getenv("EDGE_MONITOR_MIN_WIN_RATE", "45"))

    # Streak Reversal Trader (focused BTC streak mean-reversion)
    STREAK_REVERSAL_SIZE: float = float(os.getenv("STREAK_REVERSAL_SIZE", "100"))
    STREAK_REVERSAL_ORDER_PRICE: float = float(os.getenv("STREAK_REVERSAL_ORDER_PRICE", "0.40"))
    STREAK_REVERSAL_LOOP_S: int = int(os.getenv("STREAK_REVERSAL_LOOP_S", "15"))
    STREAK_USD_REVERSAL_THRESHOLD: float = float(os.getenv("STREAK_USD_REVERSAL_THRESHOLD", "200"))
    STREAK_USD_REVERSAL_THRESHOLD_5M: float = float(
        os.getenv("STREAK_USD_REVERSAL_THRESHOLD_5M", os.getenv("STREAK_USD_REVERSAL_THRESHOLD", "200"))
    )
    STREAK_USD_REVERSAL_THRESHOLD_15M: float = float(
        os.getenv("STREAK_USD_REVERSAL_THRESHOLD_15M", "400")
    )

    # Strategy mode: which traders to run
    # streak_reversal  — only the 6x UP reversal strategy (current focus)
    # all              — run all traders (pattern + fast_reversal + streak_reversal)
    # pattern          — only pattern-based auto trader
    STRATEGY_MODE: str = os.getenv("STRATEGY_MODE", "streak_reversal")

    # Feature flags
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() == "true"

config = Config()
