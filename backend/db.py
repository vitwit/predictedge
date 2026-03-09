import sqlite3
import os
from config import config

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS market_resolutions (
        slug                TEXT PRIMARY KEY,
        asset               TEXT NOT NULL,
        interval_minutes    INTEGER NOT NULL,
        start_ts            INTEGER NOT NULL,
        end_ts              INTEGER NOT NULL,

        open_up_price       REAL,
        open_spot_price     REAL,
        open_spread         REAL,
        close_up_price      REAL,
        close_spot_price    REAL,

        peak_up_price       REAL,
        peak_up_ts          INTEGER,
        trough_up_price     REAL,
        trough_up_ts        INTEGER,
        trough_after_peak   REAL,

        spot_open           REAL,
        spot_close          REAL,
        spot_high           REAL,
        spot_low            REAL,
        spot_change_usd     REAL,
        spot_change_pct     REAL,
        spot_range_usd      REAL,

        up_price_at_t5      REAL,
        up_price_at_t10     REAL,
        up_price_at_t30     REAL,
        up_price_at_t60     REAL,
        up_price_at_t120    REAL,
        spot_at_t5          REAL,
        spot_at_t30         REAL,
        spot_at_t60         REAL,

        up_price_before_60s REAL,
        up_price_before_30s REAL,
        up_price_before_10s REAL,
        spot_before_60s     REAL,
        spot_before_30s     REAL,

        open_liquidity_5c   REAL,
        close_liquidity_5c  REAL,

        winner_side         TEXT,
        resolved_at         INTEGER,
        inferred_at         INTEGER,

        first_30s_direction TEXT,
        early_high_conviction INTEGER,
        false_pump          INTEGER,
        late_reversal       INTEGER,
        clean_resolution    INTEGER,

        created_at          INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS price_ticks (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        slug                TEXT    NOT NULL,
        asset               TEXT    NOT NULL,
        interval_minutes    INTEGER NOT NULL,
        start_ts            INTEGER NOT NULL,

        ticked_at           INTEGER NOT NULL,
        elapsed_seconds     INTEGER NOT NULL,
        remaining_seconds   INTEGER NOT NULL,

        up_price            REAL    NOT NULL,
        down_price          REAL    NOT NULL,
        up_bid              REAL,
        up_ask              REAL,
        spread              REAL,

        spot_price          REAL,
        spot_change_from_open REAL,
        spot_change_pct     REAL,

        liquidity_within_5c REAL,
        liquidity_within_10c REAL,
        buy_side_imbalance  REAL,

        price_delta_5s      REAL,
        price_delta_10s     REAL,
        price_delta_30s     REAL,
        spot_delta_5s       REAL,
        spot_delta_30s      REAL,
        spot_delta_60s      REAL
    );

    CREATE INDEX IF NOT EXISTS idx_ticks_slug_ts ON price_ticks(slug, ticked_at);
    CREATE INDEX IF NOT EXISTS idx_ticks_elapsed ON price_ticks(slug, elapsed_seconds);
    CREATE INDEX IF NOT EXISTS idx_ticks_remaining ON price_ticks(slug, remaining_seconds);

    CREATE TABLE IF NOT EXISTS spot_prices (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        asset       TEXT    NOT NULL,
        price_usd   REAL    NOT NULL,
        source      TEXT    NOT NULL,
        captured_at INTEGER NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_spot_asset_ts ON spot_prices(asset, captured_at);

    CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        slug         TEXT    NOT NULL,
        snapshot_at  INTEGER NOT NULL,
        trigger      TEXT    NOT NULL,

        bid1_price   REAL,  bid1_size   REAL,
        bid2_price   REAL,  bid2_size   REAL,
        bid3_price   REAL,  bid3_size   REAL,
        bid4_price   REAL,  bid4_size   REAL,
        bid5_price   REAL,  bid5_size   REAL,

        ask1_price   REAL,  ask1_size   REAL,
        ask2_price   REAL,  ask2_size   REAL,
        ask3_price   REAL,  ask3_size   REAL,
        ask4_price   REAL,  ask4_size   REAL,
        ask5_price   REAL,  ask5_size   REAL,

        total_bid_5c REAL,
        total_ask_5c REAL,
        mid_price    REAL
    );

    CREATE TABLE IF NOT EXISTS macro_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type      TEXT    NOT NULL,
        headline        TEXT,
        sentiment       TEXT,
        magnitude       TEXT,
        assets_affected TEXT,
        llm_confidence  REAL,
        source          TEXT,
        occurred_at     INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS market_stats (
        slug                    TEXT PRIMARY KEY,
        asset                   TEXT NOT NULL,
        interval_minutes        INTEGER NOT NULL,
        start_ts                INTEGER NOT NULL,

        open_up_price           REAL,
        open_conviction         TEXT,
        spot_volatility_class   TEXT,

        first_5s_direction      TEXT,
        first_5s_delta          REAL,
        first_30s_direction     TEXT,
        first_30s_delta         REAL,
        first_60s_direction     TEXT,
        first_60s_spot_delta    REAL,

        max_up_price            REAL,
        min_up_price            REAL,
        price_range             REAL,
        crossed_70c             INTEGER,
        crossed_80c             INTEGER,
        crossed_30c             INTEGER,
        time_above_60c_pct      REAL,
        time_below_40c_pct      REAL,

        spot_change_total_usd   REAL,
        spot_change_total_pct   REAL,
        spot_change_first60s    REAL,
        spot_change_last60s     REAL,
        spot_max_drawup         REAL,
        spot_max_drawdown       REAL,

        up_price_at_t_minus60   REAL,
        up_price_at_t_minus30   REAL,
        up_price_at_t_minus10   REAL,
        direction_flipped_late  INTEGER,

        tick_count              INTEGER,
        avg_spread              REAL,
        avg_liquidity_5c        REAL,

        winner_side             TEXT,
        computed_at             INTEGER
    );

    CREATE TABLE IF NOT EXISTS strategies (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT,
        config_json TEXT NOT NULL,
        created_at  INTEGER NOT NULL,
        is_active   INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS backtest_results (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id     INTEGER NOT NULL,
        run_at          INTEGER NOT NULL,
        date_from       INTEGER,
        date_to         INTEGER,
        total_trades    INTEGER,
        win_rate        REAL,
        total_pnl       REAL,
        sharpe_ratio    REAL,
        max_drawdown    REAL,
        avg_edge        REAL,
        results_json    TEXT,
        FOREIGN KEY (strategy_id) REFERENCES strategies(id)
    );

    CREATE TABLE IF NOT EXISTS auto_trade_orders (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        slug              TEXT NOT NULL,
        asset             TEXT NOT NULL,
        interval_minutes  INTEGER NOT NULL,
        token_id          TEXT NOT NULL,
        pattern_str       TEXT NOT NULL,
        predicted_side    TEXT NOT NULL,
        order_price       REAL NOT NULL,
        order_size        REAL NOT NULL,
        status            TEXT NOT NULL,
        response_json     TEXT,
        error             TEXT,
        created_at        INTEGER NOT NULL,
        UNIQUE(slug, pattern_str, predicted_side)
    );

    CREATE INDEX IF NOT EXISTS idx_auto_trade_created_at ON auto_trade_orders(created_at);

    CREATE TABLE IF NOT EXISTS auto_claims (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        condition_id      TEXT NOT NULL,
        index_set         INTEGER NOT NULL,
        token_id          TEXT NOT NULL,
        amount_redeemed   TEXT,
        tx_hash           TEXT,
        status            TEXT NOT NULL,
        error             TEXT,
        created_at        INTEGER NOT NULL,
        UNIQUE(condition_id, index_set)
    );

    CREATE INDEX IF NOT EXISTS idx_auto_claims_created_at ON auto_claims(created_at);

    CREATE TABLE IF NOT EXISTS signal_events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        slug              TEXT NOT NULL,
        asset             TEXT NOT NULL,
        interval_minutes  INTEGER NOT NULL,
        pattern_str       TEXT NOT NULL,
        predicted_side    TEXT NOT NULL,

        -- raw signal inputs
        win_rate          REAL,
        edge_pct          REAL,
        sample_count      INTEGER,
        spread_cents      REAL,
        bid_depth_5c      REAL,
        ask_depth_5c      REAL,
        depth_imbalance   REAL,
        spot_vol_30s      REAL,
        time_remaining_s  INTEGER,

        -- computed scores
        ev_score          REAL,
        confidence        REAL,

        -- decision
        decision          TEXT NOT NULL,  -- APPROVE | REJECT
        reject_reasons    TEXT,           -- JSON array of reason codes
        order_id          INTEGER,        -- FK -> auto_trade_orders.id

        created_at        INTEGER NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_signal_events_slug ON signal_events(slug);
    CREATE INDEX IF NOT EXISTS idx_signal_events_created_at ON signal_events(created_at);
    CREATE INDEX IF NOT EXISTS idx_signal_events_decision ON signal_events(decision);

    CREATE TABLE IF NOT EXISTS edge_stats (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_type     TEXT    NOT NULL,
        asset           TEXT    NOT NULL,
        interval_minutes INTEGER NOT NULL,
        window_n        INTEGER NOT NULL DEFAULT 50,
        win_count       INTEGER NOT NULL DEFAULT 0,
        loss_count      INTEGER NOT NULL DEFAULT 0,
        win_rate        REAL    NOT NULL DEFAULT 0,
        avg_ev          REAL    DEFAULT 0,
        is_active       INTEGER DEFAULT 1,
        last_updated    INTEGER NOT NULL,
        UNIQUE(signal_type, asset, interval_minutes)
    );

    CREATE TABLE IF NOT EXISTS portfolio_state (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_at           INTEGER NOT NULL,
        open_positions        TEXT    DEFAULT '[]',
        total_invested        REAL    DEFAULT 0,
        realized_pnl          REAL    DEFAULT 0,
        consecutive_losses    INTEGER DEFAULT 0,
        peak_balance          REAL    DEFAULT 0,
        drawdown_pct          REAL    DEFAULT 0,
        circuit_breaker_until INTEGER DEFAULT 0,
        notes                 TEXT
    );

    CREATE TABLE IF NOT EXISTS llm_decisions (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        slug             TEXT    NOT NULL,
        asset            TEXT    NOT NULL,
        interval_minutes INTEGER NOT NULL,
        model            TEXT    NOT NULL,
        prompt_context   TEXT,
        llm_response     TEXT,
        decision         TEXT    NOT NULL,
        reasoning        TEXT,
        confidence_in    REAL,
        latency_ms       INTEGER,
        created_at       INTEGER NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_llm_decisions_slug ON llm_decisions(slug);
    CREATE INDEX IF NOT EXISTS idx_llm_decisions_created_at ON llm_decisions(created_at);

    CREATE TABLE IF NOT EXISTS market_features (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        slug            TEXT    NOT NULL,
        asset           TEXT    NOT NULL,
        interval_minutes INTEGER NOT NULL,
        captured_at     INTEGER NOT NULL,
        clob_mid        REAL,
        spread_cents    REAL,
        bid_depth_5c    REAL,
        ask_depth_5c    REAL,
        depth_imbalance REAL,
        microprice      REAL,
        hotspot_zone    TEXT,
        hotspot_dwell_s INTEGER,
        hotspot_side    TEXT,
        impulse_cents   REAL,
        impulse_dir     TEXT,
        regime          TEXT,
        spot_vol_30s    REAL,
        spot_change_30s REAL,
        spot_change_60s REAL,
        spot_change_120s REAL
    );

    CREATE INDEX IF NOT EXISTS idx_mf_slug_ts ON market_features(slug, captured_at);
    """)

    conn.commit()
    _migrate(conn)
    conn.close()
    print("[db] Database initialized")


def _migrate(conn):
    """Add new columns to existing tables without breaking existing data."""
    cur = conn.cursor()
    existing = {r[1] for r in cur.execute("PRAGMA table_info(market_resolutions)")}

    new_cols = [
        ("prev_spot_change_usd",  "REAL"),
        ("prev_spot_change_pct",  "REAL"),
        ("prev_winner_side",      "TEXT"),
        ("chainlink_open",        "REAL"),  # Chainlink BTC/USD price at window open (from priceToBeat)
    ]
    for col, col_type in new_cols:
        if col not in existing:
            cur.execute(f"ALTER TABLE market_resolutions ADD COLUMN {col} {col_type}")
            print(f"[db] migrated: added market_resolutions.{col}")

    # auto_trade_orders migrations
    ato_cols = {r[1] for r in cur.execute("PRAGMA table_info(auto_trade_orders)")}
    ato_new = [
        ("trigger_type", "TEXT DEFAULT 'PATTERN'"),  # PATTERN | REVERSAL
        ("trigger_usd_move", "REAL"),                # USD move that triggered reversal
    ]
    for col, col_type in ato_new:
        if col not in ato_cols:
            cur.execute(f"ALTER TABLE auto_trade_orders ADD COLUMN {col} {col_type}")
            print(f"[db] migrated: added auto_trade_orders.{col}")

    conn.commit()
