#!/usr/bin/env python3
"""
Verify BTC 5m/15m resolution data: local DB vs Gamma API.
Shows why streak reversal / USD reversal may or may not have triggered.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

# Run from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config
from db import get_connection

try:
    import requests
except ImportError:
    requests = None

GAMMA_BASE = getattr(config, "GAMMA_HOST", "https://gamma-api.polymarket.com")


def _ts_to_iso(ts: int) -> str:
    if ts is None:
        return "N/A"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def query_local_btc_resolutions(interval_minutes: int, limit: int = 30):
    """Recent BTC resolutions from local DB."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT slug, start_ts, end_ts, winner_side, spot_change_usd, resolved_at
        FROM market_resolutions
        WHERE asset = 'BTC' AND interval_minutes = ?
        ORDER BY end_ts DESC
        LIMIT ?
        """,
        (interval_minutes, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_gamma_btc_closed(limit: int = 50):
    """Fetch closed crypto markets from Gamma (BTC 5m/15m only)."""
    if not requests:
        return []
    out = []
    params = {
        "closed": "true",
        "tag_slug": "crypto",
        "limit": min(limit, 100),
        "offset": 0,
    }
    try:
        resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=15)
        resp.raise_for_status()
        batch = resp.json()
    except Exception as e:
        print(f"Gamma API error: {e}")
        return []
    if not isinstance(batch, list):
        return []
    now_ts = int(time.time())
    for m in batch:
        slug = (m.get("slug") or "").lower()
        if "btc" not in slug and "bitcoin" not in slug:
            continue
        if "5m" in slug or "5-min" in slug:
            interval = 5
        elif "15m" in slug or "15-min" in slug:
            interval = 15
        else:
            continue
        end_date = m.get("endDate") or m.get("end_date")
        if end_date:
            try:
                # ISO format
                from datetime import datetime
                dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                end_ts = int(dt.timestamp())
            except Exception:
                end_ts = 0
        else:
            end_ts = 0
        if end_ts > now_ts:
            continue
        close = m.get("outcomePrices") or m.get("closePrice")
        if isinstance(close, str):
            try:
                close = json.loads(close)
            except Exception:
                close = [None, None]
        winner = None
        if isinstance(close, list) and len(close) >= 2:
            up = float(close[0]) if close[0] is not None else 0
            if up >= 0.999:
                winner = "UP"
            elif up <= 0.001:
                winner = "DOWN"
        out.append({
            "slug": m.get("slug", ""),
            "interval": interval,
            "end_ts": end_ts,
            "winner_side": winner,
        })
    out.sort(key=lambda x: -x["end_ts"])
    return out[:limit]


def main():
    print("=" * 60)
    print("BTC 5m / 15m resolution verification (local DB vs Gamma)")
    print("=" * 60)

    for interval in [5, 15]:
        print(f"\n--- BTC {interval}m (local DB) ---")
        rows = query_local_btc_resolutions(interval, limit=25)
        if not rows:
            print("  No rows in local DB.")
        else:
            print(f"  Total recent (local): {len(rows)}")
            latest = rows[0]
            print(f"  Latest: end_ts={latest.get('end_ts')} ({_ts_to_iso(latest.get('end_ts'))})")
            print(f"          slug={latest.get('slug', '')[:60]}...")
            print(f"          winner_side={latest.get('winner_side')} spot_change_usd={latest.get('spot_change_usd')}")
            print("  Last 10 (end_ts desc):")
            for r in rows[:10]:
                print(f"    {_ts_to_iso(r.get('end_ts'))}  winner={r.get('winner_side')}  spot_chg_usd={r.get('spot_change_usd')}  {r.get('slug', '')[:50]}")

    # Streak check (what the trader uses)
    print("\n--- Streak check (last 6 outcomes per interval) ---")
    conn = get_connection()
    for interval in [5, 15]:
        rows = conn.execute(
            """
            SELECT winner_side FROM market_resolutions
            WHERE asset = 'BTC' AND interval_minutes = ? AND winner_side IS NOT NULL
            ORDER BY start_ts DESC LIMIT 6
            """,
            (interval,),
        ).fetchall()
        outcomes = [r[0] for r in rows]
        outcomes.reverse()  # oldest -> newest
        six_up = len(outcomes) == 6 and all(o == "UP" for o in outcomes)
        six_down = len(outcomes) == 6 and all(o == "DOWN" for o in outcomes)
        print(f"  BTC {interval}m: last_6 = {outcomes}  -> 6xUP={six_up} 6xDOWN={six_down}")
    conn.close()

    # USD reversal check (last closed market's spot_change_usd)
    print("\n--- USD reversal check (last closed market) ---")
    conn = get_connection()
    for interval in [5, 15]:
        thresh = 200 if interval == 5 else 400
        row = conn.execute(
            """
            SELECT slug, spot_change_usd, winner_side
            FROM market_resolutions
            WHERE asset = 'BTC' AND interval_minutes = ?
              AND winner_side IS NOT NULL AND spot_change_usd IS NOT NULL
            ORDER BY end_ts DESC LIMIT 1
            """,
            (interval,),
        ).fetchone()
        if row:
            d = dict(row)
            move = d.get("spot_change_usd") or 0
            passes = abs(move) > thresh
            print(f"  BTC {interval}m: last closed spot_change_usd={move}  threshold=>{thresh}  PASS={passes}  winner={d.get('winner_side')}")
        else:
            print(f"  BTC {interval}m: no row with winner_side and spot_change_usd")
    conn.close()

    # Gamma API sample
    print("\n--- Gamma API (closed crypto, BTC 5m/15m sample) ---")
    gamma = fetch_gamma_btc_closed(30)
    if not gamma:
        print("  No Gamma results or request failed.")
    else:
        print(f"  Fetched {len(gamma)} closed BTC 5m/15m markets from Gamma.")
        for g in gamma[:8]:
            print(f"    {_ts_to_iso(g['end_ts'])}  {g['interval']}m  winner={g.get('winner_side')}  slug={g.get('slug', '')[:50]}")

    # Count local by interval
    conn = get_connection()
    for interval in [5, 15]:
        total = conn.execute(
            "SELECT COUNT(*) FROM market_resolutions WHERE asset='BTC' AND interval_minutes=?",
            (interval,),
        ).fetchone()[0]
        with_winner = conn.execute(
            "SELECT COUNT(*) FROM market_resolutions WHERE asset='BTC' AND interval_minutes=? AND winner_side IS NOT NULL",
            (interval,),
        ).fetchone()[0]
        print(f"\n  Local DB BTC {interval}m: total={total} with winner_side={with_winner}")
    # Strategy data checklist: gaps that would cause us to miss triggers
    print("\n--- Strategy data checklist (don't miss opportunities) ---")
    print("  Data flow: Gamma tag-based sync (pages 1-8) + primary /markets → market_resolutions.")
    print("  winner_side: from Gamma outcomePrices. spot_change_usd: Gamma eventMetadata.priceToBeat only (next market's price_to_beat = prev close). No backfill.")
    missing_spot = conn.execute(
        """SELECT COUNT(*) FROM market_resolutions
           WHERE asset='BTC' AND interval_minutes IN (5,15) AND winner_side IS NOT NULL AND spot_change_usd IS NULL
             AND end_ts >= ?""",
        (int(time.time()) - 48 * 3600,),
    ).fetchone()[0]
    if missing_spot:
        print(f"  NOTE: {missing_spot} recent BTC 5m/15m rows have winner_side but NULL spot_change_usd (Gamma priceToBeat not in API for those). USD reversal needs spot_change_usd.")
    else:
        print("  OK: Recent BTC 5m/15m resolutions have spot_change_usd set (USD reversal can fire).")
    conn.close()
    print()


if __name__ == "__main__":
    main()
