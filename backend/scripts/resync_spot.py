"""
Full DB Resync — Spot Price Enrichment
=======================================
Fetches 30 days of 1-minute Binance OHLCV data, stores in `historical_spot` table,
then backfills every market_resolutions row with real spot_open/close/change_usd.
Finally rebuilds prev_spot_change_usd / prev_winner_side for consecutive windows.

Run from backend/:  python scripts/resync_spot.py
"""
import bisect
import sys
import time
from pathlib import Path

# Allow imports from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from db import get_connection, init_db

BINANCE = "https://api1.binance.com/api/v3/klines"
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
ASSETS  = list(SYMBOLS.keys())
DAYS    = 30
BATCH   = 1000          # Binance max per request
SLEEP   = 0.12          # ~8 req/s, well under 1200/min limit


def _ensure_historical_spot_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS historical_spot (
            asset       TEXT NOT NULL,
            ts          INTEGER NOT NULL,   -- Unix seconds (1-minute candle open time)
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            PRIMARY KEY (asset, ts)
        );
        CREATE INDEX IF NOT EXISTS idx_hspot_asset_ts ON historical_spot(asset, ts);
    """)
    conn.commit()


def fetch_binance_klines(symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch all 1m candles between start_ms and end_ms from Binance."""
    candles = []
    cursor = start_ms
    while cursor < end_ms:
        resp = requests.get(BINANCE, params={
            "symbol": symbol, "interval": "1m",
            "startTime": cursor, "endTime": end_ms,
            "limit": BATCH,
        }, timeout=20)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        candles.extend(batch)
        cursor = batch[-1][0] + 60_000  # next minute after last candle
        time.sleep(SLEEP)
    return candles


def store_klines(conn, asset: str, candles: list) -> int:
    rows = [
        (asset, int(c[0]) // 1000, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]))
        for c in candles
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO historical_spot (asset, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def build_price_index(conn, asset: str) -> tuple:
    """Return (sorted ts list, sorted price list) for fast bisect lookups."""
    rows = conn.execute(
        "SELECT ts, close FROM historical_spot WHERE asset=? ORDER BY ts ASC", (asset,)
    ).fetchall()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def nearest_price(ts_list, px_list, target_ts: int, max_delta_s: int = 120):
    """Binary-search for the price closest to target_ts. Returns None if too far."""
    if not ts_list:
        return None
    i = bisect.bisect_left(ts_list, target_ts)
    candidates = []
    if i < len(ts_list):
        candidates.append((abs(ts_list[i] - target_ts), px_list[i]))
    if i > 0:
        candidates.append((abs(ts_list[i - 1] - target_ts), px_list[i - 1]))
    if not candidates:
        return None
    candidates.sort()
    if candidates[0][0] > max_delta_s:
        return None
    return candidates[0][1]


def step1_fetch_binance(conn):
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - DAYS * 86_400_000

    for asset, symbol in SYMBOLS.items():
        existing = conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM historical_spot WHERE asset=?", (asset,)
        ).fetchone()
        n_existing = existing[0]
        min_ts = existing[1] or 0
        max_ts = existing[2] or 0

        target_min = start_ms // 1000
        target_max = now_ms // 1000

        # Already fully covered
        if n_existing > 0 and min_ts <= target_min + 300 and max_ts >= target_max - 300:
            print(f"  {asset}: {n_existing:,} candles already current — skipping")
            continue

        # Delta fetch: only missing candles from the gap
        if n_existing > 0 and max_ts > target_min:
            gap_start_ms = (max_ts + 60) * 1000  # resume from 1 min after last stored
            gap_min = (now_ms - gap_start_ms) // 60000
            print(f"  {asset}: delta-fetching {gap_min} missing candles since {time.strftime('%m-%d %H:%M', time.gmtime(max_ts))} ...", flush=True)
            t0 = time.time()
            candles = fetch_binance_klines(symbol, gap_start_ms, now_ms)
            stored = store_klines(conn, asset, candles)
            elapsed = time.time() - t0
            print(f"    → stored {stored:,} new candles in {elapsed:.1f}s")
        else:
            print(f"  {asset}: full fetch {symbol} 1m klines (last {DAYS} days) ...", flush=True)
            t0 = time.time()
            candles = fetch_binance_klines(symbol, start_ms, now_ms)
            stored = store_klines(conn, asset, candles)
            elapsed = time.time() - t0
            print(f"    → stored {stored:,} candles in {elapsed:.1f}s")


def step2_backfill_markets(conn):
    print("\n[Step 2] Backfilling market_resolutions with Binance spot data...")

    price_idx = {a: build_price_index(conn, a) for a in ASSETS}
    print(f"  Price index: {', '.join(f'{a}={len(price_idx[a][0]):,}' for a in ASSETS)} candles")

    updated = 0
    skipped = 0

    markets = conn.execute(
        """SELECT slug, asset, interval_minutes, start_ts, end_ts
           FROM market_resolutions
           WHERE chainlink_open IS NULL   -- skip BTC markets with exact Chainlink prices
           ORDER BY asset, interval_minutes, start_ts"""
    ).fetchall()

    print(f"  Processing {len(markets):,} markets (skipping Chainlink-enriched BTC rows)...")
    for i, m in enumerate(markets):
        asset = m["asset"]
        if asset not in price_idx:
            skipped += 1
            continue

        ts_list, px_list = price_idx[asset]
        open_px  = nearest_price(ts_list, px_list, m["start_ts"], max_delta_s=120)
        close_px = nearest_price(ts_list, px_list, m["end_ts"],   max_delta_s=120)

        if open_px is None or close_px is None:
            skipped += 1
            continue

        # High/low during window
        lo_i = bisect.bisect_left(ts_list, m["start_ts"] - 60)
        hi_i = bisect.bisect_right(ts_list, m["end_ts"] + 60)
        window_px = px_list[lo_i:hi_i]
        spot_high = max(window_px) if window_px else None
        spot_low  = min(window_px) if window_px else None
        spot_range = round(spot_high - spot_low, 4) if spot_high and spot_low else None

        chg_usd = round(close_px - open_px, 6)
        chg_pct = round((close_px - open_px) / open_px * 100, 6) if open_px else None

        conn.execute("""
            UPDATE market_resolutions SET
                spot_open       = ?,
                spot_close      = ?,
                open_spot_price = ?,
                close_spot_price= ?,
                spot_change_usd = ?,
                spot_change_pct = ?,
                spot_high       = ?,
                spot_low        = ?,
                spot_range_usd  = ?
            WHERE slug = ?
        """, (open_px, close_px, open_px, close_px, chg_usd, chg_pct, spot_high, spot_low, spot_range, m["slug"]))
        updated += 1

        if (i + 1) % 5000 == 0:
            conn.commit()
            pct = (i + 1) / len(markets) * 100
            print(f"    {i+1:,}/{len(markets):,} ({pct:.0f}%) — updated={updated:,} skipped={skipped:,}")

    conn.commit()
    print(f"  Done: updated={updated:,} skipped={skipped:,}")
    return updated


def step3_rebuild_consecutive_links(conn):
    print("\n[Step 3] Rebuilding prev_spot_change_usd / prev_winner_side links...")
    linked = 0

    for asset in ASSETS:
        for interval in (5, 15, 60):
            windows = conn.execute("""
                SELECT slug, start_ts, end_ts, spot_change_usd, spot_change_pct, winner_side
                FROM market_resolutions
                WHERE asset=? AND interval_minutes=?
                  AND spot_change_usd IS NOT NULL
                ORDER BY start_ts
            """, (asset, interval)).fetchall()

            for i in range(1, len(windows)):
                prev = windows[i - 1]
                curr = windows[i]
                # Consecutive = curr.start_ts == prev.end_ts (±30s tolerance)
                if abs(curr["start_ts"] - prev["end_ts"]) > 30:
                    continue
                conn.execute("""
                    UPDATE market_resolutions SET
                        prev_spot_change_usd = ?,
                        prev_spot_change_pct = ?,
                        prev_winner_side     = ?
                    WHERE slug = ?
                """, (prev["spot_change_usd"], prev["spot_change_pct"], prev["winner_side"], curr["slug"]))
                linked += 1

    conn.commit()
    print(f"  Linked {linked:,} consecutive window pairs")


def step4_summary(conn):
    print("\n[Summary]")
    rows = conn.execute("""
        SELECT asset, interval_minutes,
               COUNT(*) total,
               SUM(CASE WHEN winner_side IS NOT NULL THEN 1 ELSE 0 END) resolved,
               SUM(CASE WHEN spot_change_usd IS NOT NULL AND spot_change_usd != 0 THEN 1 ELSE 0 END) with_spot,
               SUM(CASE WHEN prev_spot_change_usd IS NOT NULL THEN 1 ELSE 0 END) with_prev,
               ROUND(AVG(ABS(spot_change_usd)), 2) avg_move,
               MAX(ABS(spot_change_usd)) max_move
        FROM market_resolutions
        WHERE spot_change_usd IS NOT NULL
        GROUP BY asset, interval_minutes
        ORDER BY asset, interval_minutes
    """).fetchall()
    print(f"  {'Asset':4s} {'Int':3s} {'Total':>7s} {'Resolved':>8s} {'Spot':>6s} {'Prev':>6s} {'AvgΔ':>8s} {'MaxΔ':>8s}")
    print("  " + "-"*60)
    for r in rows:
        print(f"  {r[0]:4s} {r[1]:3d}m {r[2]:>7,} {r[3]:>8,} {r[4]:>6,} {r[5]:>6,} {r[6]:>8.2f} {r[7]:>8.2f}")


if __name__ == "__main__":
    print("=" * 60)
    print("PredictEdge — Full Spot Data Resync")
    print(f"Fetching {DAYS} days of 1m Binance klines for {', '.join(ASSETS)}")
    print("=" * 60)

    init_db()
    conn = get_connection()
    _ensure_historical_spot_table(conn)

    print(f"\n[Step 1] Fetching Binance 1m klines...")
    step1_fetch_binance(conn)

    n_updated = step2_backfill_markets(conn)
    step3_rebuild_consecutive_links(conn)
    step4_summary(conn)

    conn.close()
    print("\n✅ Resync complete.")
