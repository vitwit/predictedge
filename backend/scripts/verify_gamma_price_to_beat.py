#!/usr/bin/env python3
"""
Verify: next market's price_to_beat = previous market's closing price.

1. Fetches Gamma API /events for consecutive BTC 5m slugs and checks eventMetadata.priceToBeat.
2. Verifies from local DB that prev.spot_close = next.chainlink_open (next.spot_open) and
   prev.spot_change_usd = prev.spot_close - prev.spot_open.
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config
from db import get_connection

import requests

GAMMA = getattr(config, "GAMMA_HOST", "https://gamma-api.polymarket.com")


def fetch_gamma_event(slug: str) -> dict | None:
    """Fetch /events?slug=... and return first event (with eventMetadata.priceToBeat if present)."""
    try:
        r = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=12)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return data[0]
        return None
    except Exception as e:
        print(f"  Error fetching {slug}: {e}")
        return None


def get_price_to_beat_from_gamma(slug: str) -> float | None:
    """Return priceToBeat from Gamma event if present (eventMetadata.priceToBeat)."""
    ev = fetch_gamma_event(slug)
    if not ev:
        return None
    meta = ev.get("eventMetadata") or {}
    ptb = meta.get("priceToBeat")
    return float(ptb) if ptb is not None else None


def verify_from_db(conn, interval_minutes: int = 5):
    """Verify logic from DB: for consecutive markets, prev.spot_close = next.chainlink_open and prev.spot_change_usd = prev.spot_close - prev.spot_open."""
    step = interval_minutes * 60
    rows = conn.execute(
        """
        SELECT slug, start_ts, end_ts, chainlink_open, spot_open, spot_close, spot_change_usd
        FROM market_resolutions
        WHERE asset = 'BTC' AND interval_minutes = ?
          AND chainlink_open IS NOT NULL AND spot_close IS NOT NULL
        ORDER BY start_ts DESC
        LIMIT 20
        """,
        (interval_minutes,),
    ).fetchall()
    rows = [dict(r) for r in rows]
    if len(rows) < 2:
        print(f"  [DB] Need at least 2 consecutive BTC {interval_minutes}m rows with chainlink_open and spot_close.")
        return
    rows.sort(key=lambda x: x["start_ts"], reverse=True)
    ok = 0
    for i in range(len(rows) - 1):
        curr, prev = rows[i], rows[i + 1]
        if curr["start_ts"] - prev["start_ts"] != step:
            continue
        next_open = curr.get("chainlink_open") or curr.get("spot_open")
        prev_close = prev.get("spot_close")
        prev_open = prev.get("chainlink_open") or prev.get("spot_open")
        expected_chg = round(prev_close - prev_open, 6) if (prev_close is not None and prev_open is not None) else None
        actual_chg = prev.get("spot_change_usd")
        close_ok = prev_close is not None and next_open is not None and abs(float(prev_close) - float(next_open)) < 0.01
        chg_ok = expected_chg is not None and actual_chg is not None and abs(float(actual_chg) - float(expected_chg)) < 0.01
        if close_ok and chg_ok:
            ok += 1
    print(f"  [DB] Consecutive pairs where prev.spot_close = next.chainlink_open and spot_change_usd correct: {ok}")


def main():
    print("Verification: next market price_to_beat = previous market close")
    print("=" * 70)

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT slug, start_ts, end_ts, chainlink_open, spot_open, spot_close, spot_change_usd
        FROM market_resolutions
        WHERE asset = 'BTC' AND interval_minutes = 5
        ORDER BY start_ts DESC LIMIT 6
        """
    ).fetchall()
    conn.close()

    def start_ts_from_slug(slug):
        if not slug or "btc-updown-5m-" not in str(slug):
            return None
        try:
            return int(str(slug).split("-")[-1])
        except Exception:
            return None

    ordered = []
    for r in rows:
        d = dict(r)
        slug = d.get("slug")
        st = d.get("start_ts") or start_ts_from_slug(slug)
        ordered.append({**d, "start_ts": st or d.get("start_ts")})
    ordered.sort(key=lambda x: (x.get("start_ts") or 0), reverse=True)

    # 1) Gamma API: try to get priceToBeat for two consecutive slugs
    print("\n1) Gamma API (/events?slug=...)")
    step = 300
    for i in range(len(ordered) - 1):
        curr, prev = ordered[i], ordered[i + 1]
        cs, ps = curr.get("start_ts"), prev.get("start_ts")
        if cs is None or ps is None or cs - ps != step:
            continue
        prev_slug, curr_slug = prev.get("slug"), curr.get("slug")
        print(f"   Previous: {prev_slug}")
        print(f"   Current:  {curr_slug}")
        prev_ptb = get_price_to_beat_from_gamma(prev_slug)
        time.sleep(0.25)
        curr_ptb = get_price_to_beat_from_gamma(curr_slug)
        print(f"   Gamma eventMetadata.priceToBeat — previous: {prev_ptb}, current: {curr_ptb}")
        if prev_ptb is not None and curr_ptb is not None:
            expected_close = curr_ptb
            expected_chg = round(expected_close - prev_ptb, 2)
            print(f"   => Previous close (expected) = current price_to_beat = {expected_close}")
            print(f"   => Previous spot_change_usd (expected) = {expected_chg}")
        else:
            print("   (priceToBeat not present in Gamma response for one or both — API may not expose it for these events)")
        break
    else:
        print("   No consecutive pair found in DB for Gamma slug fetch.")

    # 2) Local DB: verify prev.spot_close = next.chainlink_open and spot_change_usd = close - open
    print("\n2) Local DB (consecutive BTC 5m with chainlink_open + spot_close)")
    conn = get_connection()
    verify_from_db(conn, 5)
    verify_from_db(conn, 15)
    conn.close()
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
