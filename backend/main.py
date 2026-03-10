"""
PredictEdge — Backend API
FastAPI server with REST endpoints and WebSocket for real-time data.
"""
import asyncio
import json
import time
import logging
import threading
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import config
from db import init_db, get_connection
from bootstrap.clob_auth import ensure_clob_api_credentials
from ingestion.spot_feed import start_spot_feed, get_spot_price
from ingestion.polymarket import start_clob_ingestion, stop_clob_ingestion, sync_historical_markets
from trading.auto_trader import start_auto_trader, stop_auto_trader
from trading.auto_claimer import start_auto_claimer, stop_auto_claimer
from trading.fast_reversal import start_fast_reversal, stop_fast_reversal
from trading.streak_reversal_trader import start_streak_reversal_trader, stop_streak_reversal_trader
from analytics.streaks import get_current_streaks, get_streak_reversal_stats, get_resolution_history
from analytics.patterns import (
    scan_pattern,
    get_pattern_matrix,
    find_top_patterns,
    get_pattern_predictions_vs_reality,
)
from analytics.momentum import get_momentum_stats, get_peak_trough_heatmap, get_early_period_stats
from analytics.temporal import get_hourly_bias, get_day_of_week_bias, get_session_stats, get_time_remaining_probability
from analytics.correlation import get_asset_correlation_matrix, get_spot_correlation_stats
from analytics.backtester import backtest_streak_reversal, backtest_fade_pump
from analytics.regime_classifier import classify_all_regimes, classify_regime
from analytics.calibration import combined_p_win, refresh_cache as refresh_calib_cache
from analytics.edge_monitor import get_all_edge_stats
from analytics.feature_store import detect_hotspot, detect_impulse
from analytics.llm_gate import get_recent_decisions as get_llm_decisions
from trading.risk_manager import get_risk_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)
HISTORICAL_SYNC_INTERVAL_SECONDS = 300

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        disconnected = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

manager = ConnectionManager()


async def broadcast_loop():
    """Broadcast live market state every 5 seconds to WebSocket clients."""
    while True:
        await asyncio.sleep(5)
        try:
            streaks = get_current_streaks()
            prices = {
                asset: get_spot_price(asset)
                for asset in ["BTC", "ETH", "SOL", "XRP"]
            }
            await manager.broadcast({
                "type": "live_update",
                "timestamp": int(time.time()),
                "streaks": streaks,
                "spot_prices": prices,
            })
        except Exception as e:
            logger.error(f"Broadcast error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PredictEdge starting...")
    init_db()
    ensure_clob_api_credentials()

    # Start spot price feed
    start_spot_feed()

    # Start Polymarket CLOB ingestion
    start_clob_ingestion()

    mode = config.STRATEGY_MODE
    logger.info("Strategy mode: %s", mode)

    if mode == "streak_reversal":
        # FOCUSED MODE: only the BTC streak reversal strategy
        start_streak_reversal_trader()
        logger.info("Running ONLY streak_reversal strategy (BTC 6x UP → DOWN, $%.0f @ %.2f)", config.STREAK_REVERSAL_SIZE, config.STREAK_REVERSAL_ORDER_PRICE)
    elif mode == "all":
        start_auto_trader()
        start_fast_reversal()
        start_streak_reversal_trader()
    else:
        # default: pattern only
        start_auto_trader()
        start_fast_reversal()

    # Start automated winnings claimer (always runs)
    start_auto_claimer()

    # Backfill recent closed market history continuously (non-blocking startup).
    # First run happens immediately, then repeats on a fixed interval.
    def _historical_sync_loop():
        while True:
            try:
                stats = sync_historical_markets(days=30)
                logger.info(
                    "Historical sync completed: fetched=%s upserted=%s",
                    stats.get("markets_fetched", 0),
                    stats.get("rows_upserted", 0),
                )
            except Exception as exc:
                logger.error(f"Historical sync failed: {exc}")
            time.sleep(HISTORICAL_SYNC_INTERVAL_SECONDS)

    threading.Thread(target=_historical_sync_loop, daemon=True).start()
    
    # Warm up calibration cache in background
    def _warm_caches():
        try:
            refresh_calib_cache()
            logger.info("Calibration cache warmed")
        except Exception as e:
            logger.warning("Calibration cache warmup failed: %s", e)
    threading.Thread(target=_warm_caches, daemon=True).start()

    # Start broadcast loop
    asyncio.create_task(broadcast_loop())
    
    logger.info(f"PredictEdge running on {config.HOST}:{config.PORT}")
    yield
    stop_streak_reversal_trader()
    stop_fast_reversal()
    stop_auto_claimer()
    stop_auto_trader()
    stop_clob_ingestion()
    logger.info("PredictEdge shutting down...")


app = FastAPI(
    title="PredictEdge API",
    description="Crypto prediction market intelligence platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM market_resolutions").fetchone()[0]
    conn.close()
    return {
        "status": "ok",
        "resolutions": count,
        "spot_prices": {a: get_spot_price(a) for a in ["BTC", "ETH", "SOL", "XRP"]},
        "timestamp": int(time.time()),
    }


# ─── Live Market Data ────────────────────────────────────────────────────

@app.get("/api/live/streaks")
def live_streaks():
    return {"streaks": get_current_streaks(), "timestamp": int(time.time())}


@app.get("/api/live/prices")
def live_prices():
    return {
        "prices": {a: get_spot_price(a) for a in ["BTC", "ETH", "SOL", "XRP"]},
        "timestamp": int(time.time()),
    }


@app.get("/api/markets/recent")
def recent_markets(
    asset: str = Query("BTC"),
    interval: int = Query(5),
    limit: int = Query(50),
):
    return {"markets": get_resolution_history(asset, interval, limit)}


@app.get("/api/markets/stats")
def market_stats_overview():
    conn = get_connection()
    rows = conn.execute("""
        SELECT asset, interval_minutes,
               COUNT(*) as total,
               SUM(CASE WHEN winner_side='UP' THEN 1 ELSE 0 END) as ups,
               SUM(CASE WHEN winner_side='DOWN' THEN 1 ELSE 0 END) as downs,
               SUM(CASE WHEN false_pump=1 THEN 1 ELSE 0 END) as false_pumps,
               SUM(CASE WHEN late_reversal=1 THEN 1 ELSE 0 END) as late_reversals,
               SUM(CASE WHEN clean_resolution=1 THEN 1 ELSE 0 END) as clean
        FROM market_resolutions
        WHERE winner_side IS NOT NULL
        GROUP BY asset, interval_minutes
        ORDER BY asset, interval_minutes
    """).fetchall()
    conn.close()
    
    return {
        "stats": [
            {
                "asset": r[0], "interval": r[1], "total": r[2],
                "ups": r[3], "downs": r[4],
                "up_rate": round(r[3] / r[2] * 100, 1) if r[2] > 0 else 0,
                "false_pumps": r[5], "late_reversals": r[6], "clean": r[7],
            }
            for r in rows
        ]
    }


# ─── Streak Analytics ───────────────────────────────────────────────────

@app.get("/api/analytics/streaks/reversal")
def streak_reversal_stats(
    asset: str = Query(None),
    interval: int = Query(None),
):
    data = get_streak_reversal_stats()
    if asset:
        data = [d for d in data if d["asset"] == asset.upper()]
    if interval:
        data = [d for d in data if d["interval"] == interval]
    return {"data": data}


# ─── Pattern Lab ────────────────────────────────────────────────────────

class PatternRequest(BaseModel):
    asset: str
    interval: int
    pattern: list[str]


@app.post("/api/analytics/patterns/scan")
def scan_pattern_endpoint(req: PatternRequest):
    result = scan_pattern(req.asset.upper(), req.interval, req.pattern)
    return result


@app.get("/api/analytics/patterns/matrix")
def pattern_matrix(
    asset: str = Query("BTC"),
    interval: int = Query(5),
    seq_len: int = Query(3),
):
    data = get_pattern_matrix(asset.upper(), interval, seq_len)
    return {"asset": asset, "interval": interval, "seq_len": seq_len, "patterns": data}


@app.get("/api/analytics/patterns/top")
def top_patterns(
    asset: str = Query("BTC"),
    interval: int = Query(5),
    min_samples: int = Query(20),
):
    data = find_top_patterns(asset.upper(), interval, min_samples)
    return {"asset": asset, "interval": interval, "top_patterns": data}


@app.get("/api/analytics/patterns/predictions-reality")
def pattern_predictions_reality(
    asset: str = Query(None),
    interval: int = Query(None),
    top_n: int = Query(10),
    recent_limit: int = Query(50),
):
    return get_pattern_predictions_vs_reality(
        asset=asset.upper() if asset else None,
        interval=interval,
        top_n=top_n,
        recent_limit=recent_limit,
    )


# ─── USD Reversal Analytics ─────────────────────────────────────────────────

@app.get("/api/analytics/usd-reversal")
def usd_reversal_analytics(
    asset: str = Query(None),
    interval: int = Query(5),
):
    """
    Returns P(reversal | prev_usd_bucket) table for all assets/intervals.
    Shows: if previous 5m window moved +$X, what % of the time does next window reverse?
    """
    from db import get_connection
    from analytics.live_signals import USD_REVERSAL_THRESHOLDS

    conn = get_connection()
    results = []

    assets_q = [asset.upper()] if asset else ["BTC", "ETH", "SOL", "XRP"]

    for a in assets_q:
        thresholds = USD_REVERSAL_THRESHOLDS.get(a, [10, 25, 50, 100, 200])

        # Build full distribution
        all_rows = conn.execute("""
            SELECT prev_spot_change_usd, winner_side, prev_winner_side
            FROM market_resolutions
            WHERE asset=? AND interval_minutes=?
              AND prev_spot_change_usd IS NOT NULL
              AND winner_side IS NOT NULL
              AND prev_winner_side IS NOT NULL
        """, (a, interval)).fetchall()

        for direction in ("up", "down"):
            for thresh in thresholds:
                if direction == "up":
                    subset = [r for r in all_rows if r["prev_spot_change_usd"] >= thresh]
                else:
                    subset = [r for r in all_rows if r["prev_spot_change_usd"] <= -thresh]

                total = len(subset)
                if total == 0:
                    continue
                reversed_n = sum(1 for r in subset if r["winner_side"] != r["prev_winner_side"])
                continued_n = total - reversed_n
                p_rev = round(reversed_n / total * 100, 1)
                p_cont = round(continued_n / total * 100, 1)

                results.append({
                    "asset": a,
                    "interval": interval,
                    "prev_direction": direction,
                    "usd_threshold": thresh,
                    "total": total,
                    "reversed": reversed_n,
                    "continued": continued_n,
                    "p_reversal_pct": p_rev,
                    "p_continuation_pct": p_cont,
                    "signal": "REVERSAL" if p_rev > 55 else "CONTINUATION" if p_cont > 55 else "NEUTRAL",
                })

    conn.close()
    # Sort by significance: most extreme win rate first
    results.sort(key=lambda x: abs(x["p_reversal_pct"] - 50), reverse=True)
    return {"interval": interval, "asset_filter": asset, "rows": results}


# ─── USD Reversal Bins ──────────────────────────────────────────────────────

@app.get("/api/analytics/usd-reversal-bins")
def usd_reversal_bins(
    asset: str = Query("BTC"),
    interval: int = Query(5),
):
    """
    Returns P(reversal) binned in discrete USD windows for each direction.
    Bin sizes are auto-scaled per asset:
      BTC: $20 bins  ETH: $2  SOL: $0.20  XRP: $0.005
    Each row: { bin_label, lo, hi, direction, n, reversed, continued, p_reversal_pct, p_continuation_pct }
    """
    from db import get_connection

    BIN_SIZES = {"BTC": 20.0, "ETH": 2.0, "SOL": 0.20, "XRP": 0.005}
    MAX_BINS  = {"BTC": 300.0, "ETH": 30.0, "SOL": 3.0, "XRP": 0.08}  # cap for last "X+" bin

    a = asset.upper()
    bin_size = BIN_SIZES.get(a, 20.0)
    max_val  = MAX_BINS.get(a, bin_size * 15)

    conn = get_connection()
    rows = conn.execute("""
        SELECT prev_spot_change_usd, winner_side, prev_winner_side
        FROM market_resolutions
        WHERE asset=? AND interval_minutes=?
          AND prev_spot_change_usd IS NOT NULL AND prev_spot_change_usd != 0
          AND winner_side IS NOT NULL AND prev_winner_side IS NOT NULL
    """, (a, interval)).fetchall()
    conn.close()

    # Build bins
    bins: dict = {}
    for r in rows:
        chg = float(r["prev_spot_change_usd"])
        direction = "up" if chg > 0 else "down"
        abs_chg = abs(chg)
        bin_lo = int(abs_chg / bin_size) * bin_size
        bin_hi = bin_lo + bin_size
        is_last = bin_lo >= max_val
        label = f"${bin_lo:.0f}+" if is_last else f"${bin_lo:.0f}–{bin_hi:.0f}"
        # Collapse all large moves into last bin
        if is_last:
            bin_lo = max_val
            bin_hi = None

        key = f"{direction}|{bin_lo}"
        if key not in bins:
            bins[key] = {"lo": bin_lo, "hi": bin_hi, "label": label, "direction": direction, "n": 0, "reversed": 0}
        bins[key]["n"] += 1
        if r["winner_side"] != r["prev_winner_side"]:
            bins[key]["reversed"] += 1

    result = []
    for key, b in sorted(bins.items(), key=lambda x: (x[1]["direction"], x[1]["lo"])):
        n = b["n"]
        rev = b["reversed"]
        cont = n - rev
        p_rev = round(rev / n * 100, 1) if n > 0 else None
        p_cont = round(cont / n * 100, 1) if n > 0 else None
        signal = "REVERSAL" if (p_rev or 0) > 55 else "CONTINUATION" if (p_cont or 0) > 55 else "NEUTRAL"
        result.append({
            "bin_label": b["label"],
            "lo": b["lo"],
            "hi": b["hi"],
            "direction": b["direction"],
            "n": n,
            "reversed": rev,
            "continued": cont,
            "p_reversal_pct": p_rev,
            "p_continuation_pct": p_cont,
            "signal": signal,
        })

    return {
        "asset": a,
        "interval": interval,
        "bin_size": bin_size,
        "total_samples": len(rows),
        "bins": result,
    }


# ─── Momentum Analytics ─────────────────────────────────────────────────

@app.get("/api/analytics/momentum")
def momentum_stats(
    asset: str = Query("BTC"),
    interval: int = Query(5),
):
    return get_momentum_stats(asset.upper(), interval)


@app.get("/api/analytics/peak-trough")
def peak_trough(
    asset: str = Query("BTC"),
    interval: int = Query(5),
):
    return {"data": get_peak_trough_heatmap(asset.upper(), interval)}


@app.get("/api/analytics/early-period")
def early_period(
    asset: str = Query("BTC"),
    interval: int = Query(5),
):
    return get_early_period_stats(asset.upper(), interval)


# ─── Temporal Analytics ─────────────────────────────────────────────────

@app.get("/api/analytics/temporal/hourly")
def hourly_bias(
    asset: str = Query("BTC"),
    interval: int = Query(5),
    lookback_days: int = Query(90),
):
    return {"data": get_hourly_bias(asset.upper(), interval, lookback_days)}


@app.get("/api/analytics/temporal/daily")
def daily_bias(
    asset: str = Query("BTC"),
    interval: int = Query(5),
):
    return {"data": get_day_of_week_bias(asset.upper(), interval)}


@app.get("/api/analytics/temporal/sessions")
def session_stats(
    asset: str = Query("BTC"),
    interval: int = Query(5),
):
    return {"data": get_session_stats(asset.upper(), interval)}


@app.get("/api/analytics/temporal/time-remaining")
def time_remaining_probability(
    asset: str = Query("BTC"),
    interval: int = Query(5),
):
    return {"data": get_time_remaining_probability(asset.upper(), interval)}


# ─── Correlation Analytics ──────────────────────────────────────────────

@app.get("/api/analytics/correlation/matrix")
def correlation_matrix(interval: int = Query(5)):
    return get_asset_correlation_matrix(interval)


@app.get("/api/analytics/correlation/spot")
def spot_correlation(
    asset: str = Query("BTC"),
    interval: int = Query(5),
):
    return get_spot_correlation_stats(asset.upper(), interval)


# ─── Macro Events ───────────────────────────────────────────────────────

@app.get("/api/macro/events")
def macro_events(limit: int = Query(20)):
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, event_type, headline, sentiment, magnitude,
               assets_affected, llm_confidence, source, occurred_at
        FROM macro_events ORDER BY occurred_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return {
        "events": [
            {
                "id": r[0], "event_type": r[1], "headline": r[2],
                "sentiment": r[3], "magnitude": r[4],
                "assets_affected": r[5], "confidence": r[6],
                "source": r[7], "occurred_at": r[8],
            }
            for r in rows
        ]
    }


# ─── Live Signals ───────────────────────────────────────────────────────────

@app.get("/api/trading/live-signals")
def live_signals_endpoint(
    interval: int = Query(5),
):
    """
    Returns the current live signal bundle for all 4 assets at given interval.
    Includes spot momentum, cross-asset confirmation, and fair value gap.
    """
    from analytics.live_signals import evaluate_all_signals
    from ingestion.polymarket import fetch_active_crypto_markets

    # Try to get live CLOB midpoint for each active market
    midpoints: dict = {}
    try:
        active = fetch_active_crypto_markets()
        for m in active:
            if int(m.get("interval_minutes", 0)) == interval:
                asset = m.get("asset", "")
                mp = m.get("midpoint") or m.get("up_price")
                if asset and mp:
                    midpoints[asset.upper()] = float(mp)
    except Exception:
        pass

    results = {}
    for asset in ["BTC", "ETH", "SOL", "XRP"]:
        # Estimate time_remaining from current clock
        import time as _time
        now = _time.time()
        window_s = interval * 60
        time_remaining_s = int(window_s - (now % window_s))
        midpoint = midpoints.get(asset)
        bundle = evaluate_all_signals(asset, interval, time_remaining_s, live_midpoint=midpoint)
        results[asset] = bundle

    return {"interval": interval, "signals": results, "timestamp": int(time.time())}


# ─── Signal Events ──────────────────────────────────────────────────────────

@app.get("/api/trading/signal-events")
def signal_events_list(
    asset: str = Query(None),
    interval: int = Query(None),
    decision: str = Query(None),   # APPROVE | REJECT
    limit: int = Query(100),
):
    conn = get_connection()
    q = """
        SELECT id, slug, asset, interval_minutes, pattern_str, predicted_side,
               win_rate, edge_pct, sample_count,
               spread_cents, bid_depth_5c, ask_depth_5c, depth_imbalance,
               time_remaining_s, ev_score, confidence,
               decision, reject_reasons, order_id, created_at
        FROM signal_events
        WHERE 1=1
    """
    params: list = []
    if asset:
        q += " AND asset = ?"
        params.append(asset.upper())
    if interval:
        q += " AND interval_minutes = ?"
        params.append(interval)
    if decision:
        q += " AND decision = ?"
        params.append(decision.upper())
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(q, params).fetchall()
    conn.close()

    events = []
    for r in rows:
        e = dict(r)
        try:
            e["reject_reasons"] = json.loads(e["reject_reasons"] or "[]")
        except Exception:
            e["reject_reasons"] = []
        events.append(e)

    conn2 = get_connection()
    totals = conn2.execute(
        "SELECT decision, COUNT(*) FROM signal_events GROUP BY decision"
    ).fetchall()
    conn2.close()
    totals_dict = {r[0]: r[1] for r in totals}

    return {
        "events": events,
        "totals": {
            "APPROVE": totals_dict.get("APPROVE", 0),
            "REJECT":  totals_dict.get("REJECT", 0),
        },
    }


# ─── Backtesting ────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy: str  # "streak_reversal" | "fade_pump"
    asset: str
    interval: int
    streak_n: int = 4
    direction: str = "DOWN"
    max_price: float = 0.48
    order_size: float = 25.0
    spike_threshold: float = 0.10
    date_from: Optional[int] = None
    date_to: Optional[int] = None


@app.post("/api/backtest")
def run_backtest(req: BacktestRequest):
    if req.strategy == "streak_reversal":
        result = backtest_streak_reversal(
            asset=req.asset.upper(),
            interval=req.interval,
            streak_n=req.streak_n,
            direction=req.direction.upper(),
            max_price=req.max_price,
            order_size=req.order_size,
            date_from=req.date_from,
            date_to=req.date_to,
        )
    elif req.strategy == "fade_pump":
        result = backtest_fade_pump(
            asset=req.asset.upper(),
            interval=req.interval,
            spike_threshold=req.spike_threshold,
            order_size=req.order_size,
            date_from=req.date_from,
            date_to=req.date_to,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {req.strategy}")
    
    return result


# ─── Strategies ─────────────────────────────────────────────────────────

class StrategyCreate(BaseModel):
    name: str
    description: str = ""
    config_json: dict


@app.post("/api/strategies")
def create_strategy(req: StrategyCreate):
    conn = get_connection()
    now = int(time.time())
    cur = conn.execute("""
        INSERT INTO strategies (name, description, config_json, created_at)
        VALUES (?, ?, ?, ?)
    """, (req.name, req.description, json.dumps(req.config_json), now))
    conn.commit()
    strategy_id = cur.lastrowid
    conn.close()
    return {"id": strategy_id, "name": req.name}


@app.get("/api/strategies")
def list_strategies():
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, name, description, config_json, created_at, is_active
        FROM strategies ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return {
        "strategies": [
            {
                "id": r[0], "name": r[1], "description": r[2],
                "config": json.loads(r[3]), "created_at": r[4], "is_active": r[5],
            }
            for r in rows
        ]
    }


# ─── AI Co-Pilot ────────────────────────────────────────────────────────

class CopilotQuery(BaseModel):
    query: str
    context: dict = {}


@app.post("/api/copilot")
async def ai_copilot(req: CopilotQuery):
    """AI co-pilot powered by OpenRouter (or OpenAI fallback) with access to platform data."""
    import requests as _requests

    if not config.OPENROUTER_API_KEY and not config.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Set OPENROUTER_API_KEY (or OPENAI_API_KEY) in .env")

    try:
        # Get current context
        streaks = get_current_streaks()
        streak_summary = ", ".join([
            f"{s['asset']} {s['interval']}m: {s['streak_length']}x {s['direction']}"
            for s in streaks[:5]
        ])

        conn = get_connection()
        resolution_count = conn.execute("SELECT COUNT(*) FROM market_resolutions").fetchone()[0]
        conn.close()

        system_prompt = f"""You are the PredictEdge AI Co-Pilot, an expert on crypto prediction markets.

You have access to a database of {resolution_count} market resolutions for BTC, ETH, SOL, XRP across 5m, 15m, 1h intervals.

Current streaks: {streak_summary}

You help traders:
1. Understand patterns and edges in prediction markets
2. Interpret statistical results
3. Design and evaluate strategies
4. Assess risk

Always include confidence intervals, sample sizes, and statistical caveats.
Keep responses concise and actionable."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": req.query},
        ]

        # Prefer OpenRouter; fall back to OpenAI
        if config.OPENROUTER_API_KEY:
            model = config.OPENROUTER_MODEL or "anthropic/claude-3-5-haiku"
            resp = _requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://predictedge.vitwit.com",
                    "X-Title": "PredictEdge Co-Pilot",
                },
                json={"model": model, "messages": messages, "max_tokens": 800, "temperature": 0.3},
                timeout=20,
            )
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"]
        else:
            from openai import OpenAI
            client = OpenAI(api_key=config.OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=messages,
                max_tokens=800,
            )
            answer = response.choices[0].message.content

        return {
            "answer": answer,
            "data": None,
            "suggested_actions": ["Verify with Backtest", "View Pattern Lab", "Set Alert"],
        }
    except Exception as e:
        return {"answer": f"AI Co-Pilot error: {str(e)}", "data": None, "suggested_actions": []}


# ─── Quant Intelligence API ─────────────────────────────────────────────────

@app.get("/api/quant/regime")
def api_quant_regime():
    """Current market regime for all assets."""
    return classify_all_regimes()


@app.get("/api/quant/regime/{asset}")
def api_quant_regime_asset(asset: str):
    """Current market regime for a specific asset."""
    return classify_regime(asset.upper())


@app.get("/api/quant/edge-health")
def api_quant_edge_health():
    """Rolling edge statistics per signal type × asset × interval."""
    return {"stats": get_all_edge_stats()}


@app.get("/api/quant/portfolio-state")
def api_quant_portfolio_state():
    """Current portfolio risk state."""
    return get_risk_manager().get_state()


@app.post("/api/quant/circuit-breaker/reset")
def api_quant_cb_reset():
    """Manually reset circuit breaker."""
    get_risk_manager().reset_circuit_breaker()
    return {"status": "reset", "state": get_risk_manager().get_state()}


@app.get("/api/quant/signal-tape")
def api_quant_signal_tape(limit: int = Query(50, le=200)):
    """Last N signal decisions with full context."""
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT se.*, o.order_size, o.trigger_type
            FROM signal_events se
            LEFT JOIN auto_trade_orders o ON o.id = se.order_id
            ORDER BY se.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return {"events": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/quant/hotspot/{asset}/{interval_minutes}")
def api_quant_hotspot(asset: str, interval_minutes: int):
    """Current hotspot zone for a market."""
    try:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT slug FROM price_ticks
            WHERE asset = ? AND interval_minutes = ?
            ORDER BY ticked_at DESC LIMIT 1
            """,
            (asset.upper(), interval_minutes),
        ).fetchone()
        conn.close()
        if not row:
            return {"active": False, "slug": None}
        slug = row["slug"]
        result = detect_hotspot(slug)
        result["slug"] = slug
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/quant/impulse/{asset}/{interval_minutes}")
def api_quant_impulse(asset: str, interval_minutes: int):
    """Current impulse detection for a market."""
    try:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT slug FROM price_ticks
            WHERE asset = ? AND interval_minutes = ?
            ORDER BY ticked_at DESC LIMIT 1
            """,
            (asset.upper(), interval_minutes),
        ).fetchone()
        conn.close()
        if not row:
            return {"active": False, "slug": None}
        slug = row["slug"]
        result = detect_impulse(slug)
        result["slug"] = slug
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/quant/calibration")
def api_quant_calibration(
    asset: str = Query(...),
    interval_minutes: int = Query(...),
    spot_change_pct: float = Query(0.0),
    clob_mid: Optional[float] = Query(None),
    predicted_side: str = Query("UP"),
):
    """Calibrated P(win) estimate for a potential trade."""
    return combined_p_win(asset.upper(), interval_minutes, spot_change_pct, clob_mid, predicted_side=predicted_side)


@app.get("/api/quant/llm-decisions")
def api_quant_llm_decisions(limit: int = Query(20, le=100)):
    """Recent LLM gate decisions."""
    return {"decisions": get_llm_decisions(limit)}


@app.get("/api/quant/order-performance")
def api_quant_order_performance(limit: int = Query(100, le=500)):
    """
    Execution + outcome metrics for auto trade orders.
    placed   = all order attempts
    executed = accepted/submitted to CLOB
    success  = resolved winner matches predicted_side
    loss     = resolved winner opposite predicted_side
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT
                o.id,
                o.slug,
                o.asset,
                o.interval_minutes,
                o.predicted_side,
                COALESCE(o.trigger_type, 'PATTERN') AS trigger_type,
                o.status,
                o.order_price,
                o.order_size,
                o.response_json,
                o.error,
                o.created_at,
                r.winner_side
            FROM auto_trade_orders o
            LEFT JOIN market_resolutions r ON r.slug = o.slug
            ORDER BY o.created_at DESC
            """
        ).fetchall()
        conn.close()

        def _empty_bucket():
            return {
                "placed": 0,
                "executed": 0,
                "failed": 0,
                "resolved": 0,
                "wins": 0,
                "losses": 0,
            }

        total = _empty_bucket()
        by_interval: dict[int, dict] = {}
        by_trigger: dict[str, dict] = {}
        recent = []

        for idx, r in enumerate(rows):
            row = dict(r)
            interval = int(row.get("interval_minutes") or 0)
            trigger = str(row.get("trigger_type") or "PATTERN")
            status = str(row.get("status") or "").lower()
            predicted = str(row.get("predicted_side") or "").upper()
            winner = (row.get("winner_side") or None)
            winner_up = str(winner).upper() if winner else None
            resolved = winner_up in {"UP", "DOWN"}

            # placed = all attempts
            total["placed"] += 1
            by_interval.setdefault(interval, _empty_bucket())["placed"] += 1
            by_trigger.setdefault(trigger, _empty_bucket())["placed"] += 1

            executed = status == "submitted"
            failed = status == "failed"
            if executed:
                total["executed"] += 1
                by_interval[interval]["executed"] += 1
                by_trigger[trigger]["executed"] += 1
            if failed:
                total["failed"] += 1
                by_interval[interval]["failed"] += 1
                by_trigger[trigger]["failed"] += 1

            result = None
            if executed and resolved:
                total["resolved"] += 1
                by_interval[interval]["resolved"] += 1
                by_trigger[trigger]["resolved"] += 1
                if predicted == winner_up:
                    total["wins"] += 1
                    by_interval[interval]["wins"] += 1
                    by_trigger[trigger]["wins"] += 1
                    result = "WIN"
                else:
                    total["losses"] += 1
                    by_interval[interval]["losses"] += 1
                    by_trigger[trigger]["losses"] += 1
                    result = "LOSS"

            if idx < limit:
                order_id = None
                try:
                    payload = json.loads(row.get("response_json") or "{}")
                    order_id = payload.get("orderID")
                except Exception:
                    order_id = None
                recent.append(
                    {
                        "id": row["id"],
                        "slug": row["slug"],
                        "asset": row["asset"],
                        "interval_minutes": interval,
                        "predicted_side": predicted,
                        "trigger_type": trigger,
                        "status": row["status"],
                        "order_price": row["order_price"],
                        "order_size": row["order_size"],
                        "order_id": order_id,
                        "error": row["error"],
                        "created_at": row["created_at"],
                        "winner_side": winner_up,
                        "resolved": resolved,
                        "result": result,
                    }
                )

        def _with_rate(d: dict):
            dd = dict(d)
            dd["win_rate_pct"] = round((dd["wins"] / dd["resolved"]) * 100, 2) if dd["resolved"] > 0 else None
            return dd

        interval_rows = []
        for k in sorted(by_interval.keys()):
            item = _with_rate(by_interval[k])
            item["interval_minutes"] = k
            interval_rows.append(item)

        trigger_rows = []
        for k in sorted(by_trigger.keys()):
            item = _with_rate(by_trigger[k])
            item["trigger_type"] = k
            trigger_rows.append(item)

        return {
            "summary": _with_rate(total),
            "by_interval": interval_rows,
            "by_trigger": trigger_rows,
            "recent_orders": recent,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ─── WebSocket ──────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial state
        streaks = get_current_streaks()
        prices = {a: get_spot_price(a) for a in ["BTC", "ETH", "SOL", "XRP"]}
        await websocket.send_json({
            "type": "initial_state",
            "streaks": streaks,
            "spot_prices": prices,
            "timestamp": int(time.time()),
        })
        
        while True:
            data = await websocket.receive_text()
            # Handle client messages (subscriptions, etc.)
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT, reload=False)
