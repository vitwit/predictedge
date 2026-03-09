"""
Calibrated P(UP) model using historical market_resolutions data.

Builds a Bayesian lookup table:
  P(UP | asset, interval, spot_bin, hour_bucket)

Also provides a combined estimate blending historical probability
with the live CLOB market price (market consensus).

No external ML libraries required — pure Python + sqlite.
"""

import math
import time
import logging
from typing import Dict, Optional, Tuple

from db import get_connection

logger = logging.getLogger(__name__)

# Spot change % bins (boundaries in %)
SPOT_BINS = [-2.0, -0.8, -0.4, -0.2, -0.08, 0.0, 0.08, 0.2, 0.4, 0.8, 2.0]

# Hour-of-day buckets (UTC)
HOUR_BUCKETS = [(0, 6, "ASIA"), (6, 14, "LONDON"), (14, 22, "NEW_YORK"), (22, 24, "ASIA")]

# Minimum samples for a reliable estimate
MIN_SAMPLES = 20

# Cache refresh interval (seconds)
_CACHE_TTL = 600

_cache: Dict = {}
_cache_built_at: float = 0.0


def _spot_bin(pct: float) -> str:
    for i, boundary in enumerate(SPOT_BINS):
        if pct <= boundary:
            label = f"{'%.2f' % (SPOT_BINS[i-1] if i > 0 else -99)}_to_{'%.2f' % boundary}"
            return label
    return f"{'%.2f' % SPOT_BINS[-1]}_to_inf"


def _hour_bucket(hour: int) -> str:
    for lo, hi, label in HOUR_BUCKETS:
        if lo <= hour < hi:
            return label
    return "ASIA"


def _wilson_lower(successes: int, n: int, z: float = 1.645) -> float:
    """Wilson score interval lower bound (90% CI)."""
    if n == 0:
        return 0.5
    p = successes / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    delta = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, min(1.0, (center - delta) / denom))


def _wilson_upper(successes: int, n: int, z: float = 1.645) -> float:
    if n == 0:
        return 0.5
    p = successes / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    delta = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, min(1.0, (center + delta) / denom))


def _build_cache() -> Dict:
    """Build the calibration lookup table from historical data."""
    global _cache, _cache_built_at
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT asset, interval_minutes, spot_change_pct,
                   strftime('%H', datetime(start_ts, 'unixepoch')) AS hour_str,
                   winner_side
            FROM market_resolutions
            WHERE winner_side IN ('UP', 'DOWN')
              AND spot_change_pct IS NOT NULL
              AND spot_change_pct != 0
            """
        ).fetchall()
        conn.close()

        table: Dict = {}
        for row in rows:
            asset = row["asset"]
            interval = int(row["interval_minutes"])
            pct = float(row["spot_change_pct"] or 0)
            hour = int(row["hour_str"] or 0)
            winner = row["winner_side"]

            sbin = _spot_bin(pct)
            hbucket = _hour_bucket(hour)

            # Full key
            key = (asset, interval, sbin, hbucket)
            if key not in table:
                table[key] = {"up": 0, "total": 0}
            table[key]["total"] += 1
            if winner == "UP":
                table[key]["up"] += 1

            # Asset+interval+spot only (ignoring hour — broader key)
            key2 = (asset, interval, sbin)
            if key2 not in table:
                table[key2] = {"up": 0, "total": 0}
            table[key2]["total"] += 1
            if winner == "UP":
                table[key2]["up"] += 1

            # Asset+interval only (broadest fallback)
            key3 = (asset, interval)
            if key3 not in table:
                table[key3] = {"up": 0, "total": 0}
            table[key3]["total"] += 1
            if winner == "UP":
                table[key3]["up"] += 1

        _cache = table
        _cache_built_at = time.time()
        logger.info("[calibration] cache built: %d keys, %d rows", len(table), len(rows))
        return table
    except Exception as e:
        logger.error("[calibration] cache build failed: %s", e)
        return {}


def _get_cache() -> Dict:
    global _cache, _cache_built_at
    if not _cache or time.time() - _cache_built_at > _CACHE_TTL:
        _build_cache()
    return _cache


def calibrated_p_win(
    asset: str,
    interval_minutes: int,
    spot_change_pct: float,
    hour_utc: Optional[int] = None,
) -> Tuple[float, float, float, int]:
    """
    Returns (p_win, ci_lower, ci_upper, sample_n) for an UP prediction.

    Uses hierarchical fallback:
      1. asset + interval + spot_bin + hour_bucket
      2. asset + interval + spot_bin
      3. asset + interval (baseline)
      4. 0.5 (no data)
    """
    table = _get_cache()
    if hour_utc is None:
        hour_utc = int(time.strftime("%H", time.gmtime()))

    sbin = _spot_bin(spot_change_pct)
    hbucket = _hour_bucket(hour_utc)

    # Try each key in order of specificity
    for key in [
        (asset, interval_minutes, sbin, hbucket),
        (asset, interval_minutes, sbin),
        (asset, interval_minutes),
    ]:
        entry = table.get(key)
        if entry and entry["total"] >= MIN_SAMPLES:
            n = entry["total"]
            up = entry["up"]
            p = up / n
            return (
                round(p, 4),
                round(_wilson_lower(up, n), 4),
                round(_wilson_upper(up, n), 4),
                n,
            )

    return (0.5, 0.35, 0.65, 0)


def combined_p_win(
    asset: str,
    interval_minutes: int,
    spot_change_pct: float,
    clob_mid: Optional[float] = None,
    hour_utc: Optional[int] = None,
    predicted_side: str = "UP",
) -> Dict:
    """
    Combine historical calibration with live CLOB market consensus.

    Returns a dict with:
      - p_win: blended probability of our predicted side winning
      - p_hist: historical-only estimate
      - p_market: market-implied probability (from CLOB mid)
      - ci_lower, ci_upper: confidence interval
      - sample_n: historical sample count
      - calibration_edge: p_hist - 0.5 (directional bias)
      - fvg: fair value gap (p_hist - p_market), + means market underpricing
    """
    p_hist, ci_lo, ci_hi, n = calibrated_p_win(
        asset, interval_minutes, spot_change_pct, hour_utc
    )

    # Flip for DOWN side
    if predicted_side == "DOWN":
        p_hist = 1.0 - p_hist
        ci_lo, ci_hi = 1.0 - ci_hi, 1.0 - ci_lo

    # Market-implied probability
    p_market = None
    if clob_mid is not None and 0 < clob_mid < 1:
        p_market = clob_mid if predicted_side == "UP" else 1.0 - clob_mid

    # Blend
    if p_market is not None:
        p_win = 0.55 * p_hist + 0.45 * p_market
    else:
        p_win = p_hist

    # Fair Value Gap: positive = we think market is UNDERpricing this outcome
    fvg = round(p_hist - p_market, 4) if p_market is not None else 0.0

    return {
        "p_win": round(p_win, 4),
        "p_hist": round(p_hist, 4),
        "p_market": round(p_market, 4) if p_market is not None else None,
        "ci_lower": round(ci_lo, 4),
        "ci_upper": round(ci_hi, 4),
        "sample_n": n,
        "calibration_edge": round(p_hist - 0.5, 4),
        "fvg": fvg,
        "fvg_cents": round(fvg * 100, 1),
    }


def refresh_cache():
    """Force refresh of calibration cache. Call periodically."""
    _build_cache()
