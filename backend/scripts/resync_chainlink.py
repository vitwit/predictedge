"""
DB Resync — BTC Chainlink Prices (Polymarket Gamma API)
=========================================================
Fetches `eventMetadata.priceToBeat` (the Chainlink BTC/USD price at each window open)
for every BTC market stored in market_resolutions, then derives:

  chainlink_open  = priceToBeat of this window   (= the exact Chainlink open price)
  spot_open       = chainlink_open
  spot_close      = chainlink_open of the NEXT consecutive window (= Chainlink close)
  spot_change_usd = spot_close - spot_open

Note on ETH/SOL/XRP:
  Polymarket's Gamma API does NOT return priceToBeat for ETH/SOL/XRP events.
  The Chainlink oracle feeds for those assets are not exposed via any Polymarket
  public API endpoint. Binance 1m klines (already stored in historical_spot) remain
  the best available proxy and are left untouched for those assets.

Run:  python scripts/resync_chainlink.py
"""
import sys
import time
import json
import threading
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from db import get_connection, init_db

GAMMA        = "https://gamma-api.polymarket.com"
WORKERS      = 3         # conservative – avoids 429s
DELAY        = 0.5       # seconds between requests per worker
TIMEOUT      = 12
COMMIT_N     = 200
# priceToBeat only available for BTC markets created after ~Feb 19 2026
CHAINLINK_CUTOFF_TS = 1771500000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

_rate_lock = threading.Semaphore(WORKERS)
_last_req  = [0.0]
_req_lock  = threading.Lock()


def _rate_limited_get(slug: str) -> float | None:
    with _req_lock:
        now = time.time()
        since = now - _last_req[0]
        if since < DELAY:
            time.sleep(DELAY - since)
        _last_req[0] = time.time()

    for attempt in range(3):
        try:
            r = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=TIMEOUT)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                log.warning(f"  429 on {slug}, sleeping {wait}s")
                time.sleep(wait)
                continue
            if not r.ok or not r.text.strip():
                return None
            data = r.json()
            if data and isinstance(data, list):
                meta = data[0].get("eventMetadata") or {}
                return meta.get("priceToBeat")
            return None
        except Exception as e:
            if attempt == 2:
                log.debug(f"  fetch error {slug}: {e}")
            time.sleep(1)
    return None


def fetch_btc_price_to_beats(slugs: list[str]) -> dict[str, float]:
    results: dict[str, float] = {}
    total = len(slugs)
    done  = [0]

    def _fetch(slug):
        val = _rate_limited_get(slug)
        done[0] += 1
        if done[0] % 200 == 0:
            pct = done[0] / total * 100
            eta_min = (total - done[0]) / max(1, done[0]) * (time.time() - t0) / 60
            log.info(f"  {done[0]:,}/{total:,} ({pct:.0f}%) — fetched {len(results):,}  ETA ~{eta_min:.0f}m")
        return slug, val

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for slug, price in pool.map(_fetch, slugs):
            if price is not None:
                results[slug] = float(price)

    elapsed = time.time() - t0
    coverage = len(results) / total * 100
    log.info(f"  Done: {len(results):,}/{total:,} ({coverage:.1f}%) in {elapsed/60:.1f}m")
    return results


def load_btc_markets(conn) -> list[dict]:
    """Load BTC markets needing Chainlink prices (post cutoff, not yet fetched), newest first."""
    rows = conn.execute(
        "SELECT slug, asset, interval_minutes, start_ts, end_ts "
        "FROM market_resolutions WHERE asset='BTC' AND start_ts >= ? AND chainlink_open IS NULL "
        "ORDER BY interval_minutes, start_ts DESC",
        (CHAINLINK_CUTOFF_TS,)
    ).fetchall()
    return [dict(r) for r in rows]


def build_and_write_btc_rows(conn, markets: list[dict], price_lookup: dict[str, float]) -> int:
    groups = defaultdict(list)
    for m in markets:
        groups[m["interval_minutes"]].append(m)

    updated = 0
    for ivl, windows in groups.items():
        windows.sort(key=lambda x: x["start_ts"])  # must be ascending for chain logic
        ivl_secs = ivl * 60

        for i, win in enumerate(windows):
            slug = win["slug"]
            open_px = price_lookup.get(slug)
            if open_px is None:
                continue

            # Derive close = next window's open
            next_slug = f"btc-updown-{ivl}m-{win['start_ts'] + ivl_secs}"
            close_px  = price_lookup.get(next_slug)
            if close_px is None and i + 1 < len(windows):
                close_px = price_lookup.get(windows[i + 1]["slug"])

            chg_usd = round(close_px - open_px, 6) if close_px is not None else None
            chg_pct = round((close_px - open_px) / open_px * 100, 6) if (close_px and open_px) else None

            conn.execute("""
                UPDATE market_resolutions SET
                    chainlink_open   = ?,
                    spot_open        = ?,
                    spot_close       = ?,
                    open_spot_price  = ?,
                    close_spot_price = ?,
                    spot_change_usd  = ?,
                    spot_change_pct  = ?
                WHERE slug = ?
            """, (open_px, open_px, close_px, open_px, close_px, chg_usd, chg_pct, slug))
            updated += 1

            if updated % COMMIT_N == 0:
                conn.commit()

    conn.commit()
    return updated


def rebuild_btc_consecutive_links(conn) -> int:
    log.info("[Step 4] Rebuilding prev_spot links for BTC...")
    linked = 0
    for ivl in (5, 15):
        windows = conn.execute("""
            SELECT slug, start_ts, end_ts, spot_change_usd, spot_change_pct, winner_side
            FROM market_resolutions
            WHERE asset='BTC' AND interval_minutes=?
              AND spot_change_usd IS NOT NULL
            ORDER BY start_ts
        """, (ivl,)).fetchall()
        for i in range(1, len(windows)):
            prev, curr = windows[i - 1], windows[i]
            if abs(curr["start_ts"] - prev["end_ts"]) > 30:
                continue
            conn.execute("""
                UPDATE market_resolutions SET
                    prev_spot_change_usd=?, prev_spot_change_pct=?, prev_winner_side=?
                WHERE slug=?
            """, (prev["spot_change_usd"], prev["spot_change_pct"], prev["winner_side"], curr["slug"]))
            linked += 1
    conn.commit()
    log.info(f"  Linked {linked:,} BTC consecutive windows")
    return linked


def print_summary(conn):
    print("\n[Summary]")
    rows = conn.execute("""
        SELECT asset, interval_minutes,
               COUNT(*) AS total,
               SUM(CASE WHEN chainlink_open IS NOT NULL THEN 1 ELSE 0 END) AS with_cl,
               SUM(CASE WHEN spot_change_usd IS NOT NULL AND spot_change_usd != 0 THEN 1 ELSE 0 END) AS with_chg,
               ROUND(AVG(ABS(spot_change_usd)), 2) AS avg_move,
               ROUND(MAX(ABS(spot_change_usd)), 2) AS max_move
        FROM market_resolutions
        GROUP BY asset, interval_minutes ORDER BY asset, interval_minutes
    """).fetchall()
    print(f"  {'Asset':4s} {'Int':3s} {'Total':>7s} {'CL Price':>9s} {'WithChg':>8s} {'AvgΔ':>8s} {'MaxΔ':>9s}")
    print("  " + "-" * 58)
    for r in rows:
        total = r['total']; cl = r['with_cl']; chg = r['with_chg']
        cov = cl / total * 100 if total else 0
        print(f"  {r['asset']:4s} {r['interval_minutes']:3d}m {total:>7,}  {cl:>8,} ({cov:4.1f}%) {chg:>8,} {r['avg_move'] or 0:>8.2f} {r['max_move'] or 0:>9.2f}")

    print("\n  [BTC 5m] Reversal probability by prev Chainlink USD move:")
    buckets = conn.execute("""
        SELECT
            CASE
                WHEN ABS(prev_spot_change_usd) < 50  THEN '<$50'
                WHEN ABS(prev_spot_change_usd) < 100 THEN '$50-100'
                WHEN ABS(prev_spot_change_usd) < 150 THEN '$100-150'
                WHEN ABS(prev_spot_change_usd) < 200 THEN '$150-200'
                ELSE '>$200'
            END bkt,
            MIN(ABS(prev_spot_change_usd)) sort_key,
            COUNT(*) n,
            SUM(CASE WHEN (prev_winner_side='UP' AND winner_side='DOWN') OR
                          (prev_winner_side='DOWN' AND winner_side='UP') THEN 1 ELSE 0 END) rev
        FROM market_resolutions
        WHERE asset='BTC' AND interval_minutes=5
          AND prev_spot_change_usd IS NOT NULL
          AND winner_side IS NOT NULL AND prev_winner_side IS NOT NULL
        GROUP BY bkt ORDER BY sort_key
    """).fetchall()
    for b in buckets:
        pct = b["rev"] / b["n"] * 100 if b["n"] else 0
        bar = "█" * int(pct / 5)
        print(f"    {b['bkt']:12s} n={b['n']:5d}  P(rev)={pct:5.1f}%  {bar}")


if __name__ == "__main__":
    print("=" * 62)
    print("PredictEdge — BTC Chainlink Price Resync")
    print(f"Source: Polymarket Gamma API (eventMetadata.priceToBeat)")
    print(f"Note:   ETH/SOL/XRP unchanged (priceToBeat not in Gamma API)")
    print("=" * 62)

    init_db()
    conn = get_connection()

    # Ensure chainlink_open column exists
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(market_resolutions)")}
    if "chainlink_open" not in existing_cols:
        conn.execute("ALTER TABLE market_resolutions ADD COLUMN chainlink_open REAL")
        conn.commit()

    log.info("[Step 1] Loading BTC market slugs...")
    markets = load_btc_markets(conn)
    slugs   = [m["slug"] for m in markets]
    log.info(f"  {len(slugs):,} BTC markets  (ETA ~{len(slugs) * DELAY / WORKERS / 60:.0f}m)")

    log.info(f"[Step 2] Fetching priceToBeat from Gamma ({WORKERS} workers, {DELAY}s delay)...")
    price_lookup = fetch_btc_price_to_beats(slugs)

    if len(price_lookup) < len(slugs) * 0.3:
        print(f"\n⚠️  Very low coverage ({len(price_lookup)/len(slugs)*100:.1f}%). Check Gamma API access.")
        sys.exit(1)

    log.info("[Step 3] Writing Chainlink prices to DB...")
    n = build_and_write_btc_rows(conn, markets, price_lookup)
    log.info(f"  Updated {n:,} BTC rows")

    rebuild_btc_consecutive_links(conn)
    print_summary(conn)
    conn.close()
    print("\n✅ BTC Chainlink resync complete.")
