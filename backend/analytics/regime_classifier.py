"""
Market regime classifier.

Classifies the current market environment into:
  TREND      — spot price moving consistently in one direction
  HIGH_VOL   — large fast moves, elevated uncertainty
  MEAN_REVERT— oscillating price, high vol but low net move
  CHOP       — flat, noisy, no clear direction
  NORMAL     — everything else

Each regime has different signal reliability profiles.
"""

import logging
import time
from typing import Dict, List, Optional

from db import get_connection
from config import config

logger = logging.getLogger(__name__)

REGIMES = ("TREND", "HIGH_VOL", "MEAN_REVERT", "CHOP", "NORMAL")

# Thresholds from config
TREND_THRESH_PCT = config.REGIME_TREND_THRESHOLD_PCT
HIGH_VOL_THRESH_PCT = config.REGIME_HIGH_VOL_THRESHOLD_PCT
CHOP_THRESH_PCT = config.REGIME_CHOP_THRESHOLD_PCT


# Signal reliability multipliers per regime per signal family
# Values > 1 = more reliable in this regime, < 1 = less reliable
REGIME_SIGNAL_WEIGHTS: Dict[str, Dict[str, float]] = {
    "TREND": {
        "momentum": 1.4,
        "cross_asset": 1.3,
        "pattern": 0.9,
        "reversal": 0.6,
        "fvg": 1.2,
        "hotspot": 0.8,
        "impulse_continuation": 1.4,
        "impulse_reversal": 0.5,
    },
    "HIGH_VOL": {
        "momentum": 0.7,
        "cross_asset": 0.8,
        "pattern": 0.6,
        "reversal": 1.1,
        "fvg": 0.8,
        "hotspot": 0.5,
        "impulse_continuation": 0.6,
        "impulse_reversal": 1.3,
    },
    "MEAN_REVERT": {
        "momentum": 0.7,
        "cross_asset": 0.7,
        "pattern": 1.0,
        "reversal": 1.4,
        "fvg": 1.1,
        "hotspot": 1.2,
        "impulse_continuation": 0.5,
        "impulse_reversal": 1.4,
    },
    "CHOP": {
        "momentum": 0.5,
        "cross_asset": 0.6,
        "pattern": 0.7,
        "reversal": 0.8,
        "fvg": 0.7,
        "hotspot": 1.0,
        "impulse_continuation": 0.5,
        "impulse_reversal": 0.7,
    },
    "NORMAL": {
        "momentum": 1.0,
        "cross_asset": 1.0,
        "pattern": 1.0,
        "reversal": 1.0,
        "fvg": 1.0,
        "hotspot": 1.0,
        "impulse_continuation": 1.0,
        "impulse_reversal": 1.0,
    },
}


def _get_recent_spot_prices(asset: str, window_s: int = 120) -> List[float]:
    """Return recent spot prices for an asset, newest last."""
    try:
        conn = get_connection()
        cutoff = int(time.time()) - window_s
        rows = conn.execute(
            """
            SELECT price_usd FROM spot_prices
            WHERE asset = ? AND captured_at >= ?
            ORDER BY captured_at ASC
            """,
            (asset, cutoff),
        ).fetchall()
        conn.close()
        return [float(r[0]) for r in rows if r[0]]
    except Exception as e:
        logger.debug("regime: spot price query failed: %s", e)
        return []


def classify_regime(asset: str) -> Dict:
    """
    Classify current market regime for an asset.

    Returns:
      regime: str (TREND/HIGH_VOL/MEAN_REVERT/CHOP/NORMAL)
      net_move_30s_pct: net % move over 30s
      net_move_60s_pct: net % move over 60s
      net_move_120s_pct: net % move over 120s
      range_pct: max-min over 120s as % of mean
      oscillations: number of direction changes in 120s
      confidence: 0-1 confidence in regime label
      signal_weights: dict of signal reliability multipliers
    """
    prices = _get_recent_spot_prices(asset, 130)

    if len(prices) < 5:
        return {
            "regime": "NORMAL",
            "net_move_30s_pct": 0.0,
            "net_move_60s_pct": 0.0,
            "net_move_120s_pct": 0.0,
            "range_pct": 0.0,
            "oscillations": 0,
            "confidence": 0.3,
            "signal_weights": REGIME_SIGNAL_WEIGHTS["NORMAL"],
        }

    latest = prices[-1]
    n = len(prices)

    def _net_pct(from_idx: int) -> float:
        if from_idx >= n:
            return 0.0
        p0 = prices[from_idx]
        if p0 == 0:
            return 0.0
        return (latest - p0) / p0 * 100.0

    # Net moves (indices approximate: 5s cadence → 30s≈6, 60s≈12, 120s≈24 ticks)
    ticks_per_30s = max(1, min(6, n // 4))
    ticks_per_60s = max(1, min(12, n // 2))
    ticks_per_120s = n - 1

    net_30 = _net_pct(n - 1 - ticks_per_30s)
    net_60 = _net_pct(n - 1 - ticks_per_60s)
    net_120 = _net_pct(n - 1 - ticks_per_120s)

    # Range
    hi = max(prices)
    lo = min(prices)
    mean = sum(prices) / len(prices)
    range_pct = ((hi - lo) / mean * 100.0) if mean > 0 else 0.0

    # Count direction oscillations (sign changes in consecutive deltas)
    deltas = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    oscillations = 0
    for i in range(1, len(deltas)):
        if deltas[i - 1] * deltas[i] < 0:
            oscillations += 1
    osc_rate = oscillations / max(len(deltas), 1)

    abs_30 = abs(net_30)
    abs_60 = abs(net_60)
    abs_120 = abs(net_120)

    # Classification logic
    regime = "NORMAL"
    confidence = 0.6

    if abs_120 > HIGH_VOL_THRESH_PCT and osc_rate < 0.4:
        # Large net move, consistent direction
        regime = "TREND"
        confidence = min(1.0, abs_120 / (HIGH_VOL_THRESH_PCT * 2))
    elif range_pct > HIGH_VOL_THRESH_PCT and osc_rate > 0.5:
        # Large range but oscillating
        regime = "HIGH_VOL" if abs_120 > TREND_THRESH_PCT else "MEAN_REVERT"
        confidence = min(1.0, range_pct / (HIGH_VOL_THRESH_PCT * 3))
    elif abs_120 < CHOP_THRESH_PCT and range_pct < CHOP_THRESH_PCT * 3:
        regime = "CHOP"
        confidence = min(1.0, 1.0 - abs_120 / CHOP_THRESH_PCT)
    elif abs_120 >= TREND_THRESH_PCT:
        if osc_rate < 0.3:
            regime = "TREND"
            confidence = 0.65
        else:
            regime = "MEAN_REVERT"
            confidence = 0.6

    return {
        "regime": regime,
        "net_move_30s_pct": round(net_30, 4),
        "net_move_60s_pct": round(net_60, 4),
        "net_move_120s_pct": round(net_120, 4),
        "range_pct": round(range_pct, 4),
        "oscillations": oscillations,
        "osc_rate": round(osc_rate, 3),
        "confidence": round(confidence, 3),
        "signal_weights": REGIME_SIGNAL_WEIGHTS[regime],
    }


def classify_all_regimes() -> Dict[str, Dict]:
    """Classify regime for all four assets."""
    return {asset: classify_regime(asset) for asset in ("BTC", "ETH", "SOL", "XRP")}


def regime_confidence_multiplier(regime: str) -> float:
    """
    Overall confidence multiplier for any signal in this regime.
    High-vol and chop reduce confidence, trend boosts it.
    """
    return {
        "TREND": 1.15,
        "HIGH_VOL": 0.80,
        "MEAN_REVERT": 1.05,
        "CHOP": 0.70,
        "NORMAL": 1.00,
    }.get(regime, 1.0)
