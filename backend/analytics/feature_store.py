"""
Real-time feature store for active markets.

Computes and caches:
  - Hotspot detection (price in 5c band for >=30s)
  - Impulse detection (>=20c move in <=5s)
  - Microprice (depth-weighted midpoint vs actual midpoint)
  - Current spot changes (30s/60s/120s)

Features are computed from price_ticks + spot_prices tables
and optionally enriched with live CLOB orderbook data.
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

from db import get_connection
from config import config

logger = logging.getLogger(__name__)

HOTSPOT_BAND_CENTS = config.HOTSPOT_BAND_CENTS
HOTSPOT_MIN_DWELL_S = config.HOTSPOT_MIN_DWELL_S
IMPULSE_MOVE_CENTS = config.IMPULSE_MOVE_CENTS
IMPULSE_TIME_S = config.IMPULSE_TIME_S


# ── Hotspot Detector ──────────────────────────────────────────────────────────

def detect_hotspot(slug: str) -> Dict:
    """
    Detect if the market price has been dwelling in a 5c zone for >=30s.

    Returns:
      active: bool — is a hotspot currently active?
      zone_center: float — center of zone in cents (0-100)
      dwell_seconds: int — how long price has been in zone
      dominant_side: str — 'UP' or 'DOWN' based on imbalance
      imbalance: float — average buy_side_imbalance during dwell
      confidence: float — 0-1 based on dwell time and consistency
    """
    try:
        conn = get_connection()
        cutoff = int(time.time()) - 120  # last 2 minutes of ticks
        rows = conn.execute(
            """
            SELECT ticked_at, up_price, buy_side_imbalance
            FROM price_ticks
            WHERE slug = ? AND ticked_at >= ?
            ORDER BY ticked_at ASC
            """,
            (slug, cutoff),
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.debug("hotspot: DB query failed: %s", e)
        return _no_hotspot()

    if len(rows) < 4:
        return _no_hotspot()

    prices_cents = [float(r["up_price"]) * 100 for r in rows]
    timestamps = [int(r["ticked_at"]) for r in rows]
    imbalances = [float(r["buy_side_imbalance"] or 0) for r in rows]

    # Scan backwards: find the longest consecutive zone dwell
    latest_price = prices_cents[-1]
    band_lo = latest_price - HOTSPOT_BAND_CENTS / 2
    band_hi = latest_price + HOTSPOT_BAND_CENTS / 2

    # Find first tick that broke out of the band
    first_in_zone = len(rows)  # start with all in zone
    for i in range(len(rows) - 1, -1, -1):
        if band_lo <= prices_cents[i] <= band_hi:
            first_in_zone = i
        else:
            break

    if first_in_zone == len(rows):
        first_in_zone = 0

    dwell_seconds = timestamps[-1] - timestamps[first_in_zone]
    zone_center = (band_lo + band_hi) / 2

    if dwell_seconds < HOTSPOT_MIN_DWELL_S:
        return _no_hotspot()

    # Dominant side based on average imbalance
    zone_imbalances = imbalances[first_in_zone:]
    avg_imbalance = sum(zone_imbalances) / len(zone_imbalances) if zone_imbalances else 0.0
    dominant_side = "UP" if avg_imbalance > 0 else "DOWN"

    confidence = min(1.0, dwell_seconds / (HOTSPOT_MIN_DWELL_S * 3))

    return {
        "active": True,
        "zone_center": round(zone_center, 1),
        "zone_lo": round(band_lo, 1),
        "zone_hi": round(band_hi, 1),
        "dwell_seconds": dwell_seconds,
        "dominant_side": dominant_side,
        "imbalance": round(avg_imbalance, 3),
        "confidence": round(confidence, 3),
        "tick_count": len(rows) - first_in_zone,
    }


def _no_hotspot() -> Dict:
    return {
        "active": False,
        "zone_center": None,
        "zone_lo": None,
        "zone_hi": None,
        "dwell_seconds": 0,
        "dominant_side": None,
        "imbalance": 0.0,
        "confidence": 0.0,
        "tick_count": 0,
    }


# ── Impulse Detector ──────────────────────────────────────────────────────────

def detect_impulse(slug: str) -> Dict:
    """
    Detect a large fast CLOB price move (>=20c in <=5s).

    Returns:
      active: bool — was there an impulse in last 30s?
      move_cents: float — size of move in cents
      duration_s: float — seconds taken
      direction: str — 'UP_SURGE' or 'DOWN_SURGE'
      time_since_s: float — seconds since impulse started
      continuation_probability: float — based on historical data
      reversal_probability: float — based on historical data
      signal_type: str — 'IMPULSE_CONTINUATION' or 'IMPULSE_REVERSAL' or 'NONE'
    """
    try:
        conn = get_connection()
        cutoff = int(time.time()) - 60
        rows = conn.execute(
            """
            SELECT ticked_at, up_price, elapsed_seconds, remaining_seconds
            FROM price_ticks
            WHERE slug = ? AND ticked_at >= ?
            ORDER BY ticked_at ASC
            """,
            (slug, cutoff),
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.debug("impulse: DB query failed: %s", e)
        return _no_impulse()

    if len(rows) < 3:
        return _no_impulse()

    prices_cents = [float(r["up_price"]) * 100 for r in rows]
    timestamps = [int(r["ticked_at"]) for r in rows]
    time_remaining = int(rows[-1]["remaining_seconds"] or 300)

    # Sliding window: look for >=20c move within 5s
    best_impulse = None
    for i in range(len(rows) - 1):
        for j in range(i + 1, len(rows)):
            dt = timestamps[j] - timestamps[i]
            if dt > IMPULSE_TIME_S:
                break
            move = prices_cents[j] - prices_cents[i]
            if abs(move) >= IMPULSE_MOVE_CENTS:
                if best_impulse is None or abs(move) > abs(best_impulse["move_cents"]):
                    best_impulse = {
                        "move_cents": round(move, 1),
                        "duration_s": dt,
                        "start_ts": timestamps[i],
                        "end_ts": timestamps[j],
                        "from_price": prices_cents[i],
                        "to_price": prices_cents[j],
                    }

    if best_impulse is None:
        return _no_impulse()

    move = best_impulse["move_cents"]
    direction = "UP_SURGE" if move > 0 else "DOWN_SURGE"
    time_since = int(time.time()) - best_impulse["end_ts"]

    # Continuation vs reversal probability lookup from historical data
    # Based on time remaining and move size
    cont_prob, rev_prob = _impulse_outcome_probs(
        abs(move), best_impulse["duration_s"], time_remaining
    )

    signal_type = "NONE"
    if cont_prob > 0.60:
        signal_type = "IMPULSE_CONTINUATION"
    elif rev_prob > 0.60:
        signal_type = "IMPULSE_REVERSAL"

    return {
        "active": True,
        "move_cents": round(move, 1),
        "duration_s": best_impulse["duration_s"],
        "direction": direction,
        "time_since_s": time_since,
        "continuation_probability": round(cont_prob, 3),
        "reversal_probability": round(rev_prob, 3),
        "signal_type": signal_type,
        "from_price": round(best_impulse["from_price"], 1),
        "to_price": round(best_impulse["to_price"], 1),
    }


def _impulse_outcome_probs(
    move_cents: float, duration_s: float, time_remaining_s: int
) -> Tuple[float, float]:
    """
    Estimate continuation/reversal probability from historical context.

    General heuristics from market microstructure research:
    - Large fast moves in early window: continuation likely (momentum)
    - Large fast moves in late window: reversal more likely (settlement pressure)
    - Very large moves (>30c): reversal more likely (overextension)
    """
    # Base probabilities
    cont = 0.50
    rev = 0.50

    # Move size effect
    if move_cents >= 30:
        cont -= 0.10
        rev += 0.10
    if move_cents >= 20:
        cont += 0.02
        rev -= 0.02

    # Speed effect (faster = more likely reversal — exhaustion)
    if duration_s <= 2:
        cont -= 0.05
        rev += 0.05

    # Time remaining effect
    if time_remaining_s < 60:
        # Late window: market converging to settlement
        cont -= 0.08
        rev += 0.08
    elif time_remaining_s > 200:
        # Early: momentum can continue
        cont += 0.05
        rev -= 0.05

    # Lookup historical impulse outcomes from market_stats if available
    try:
        conn = get_connection()
        # Check first_5s_direction vs winner in recent markets
        rows = conn.execute(
            """
            SELECT first_5s_direction, winner_side
            FROM market_stats
            WHERE first_5s_delta IS NOT NULL
              AND abs(first_5s_delta) >= ?
              AND winner_side IS NOT NULL
            ORDER BY start_ts DESC
            LIMIT 200
            """,
            (move_cents / 100.0 * 0.8,),  # convert cents to price delta
        ).fetchall()
        conn.close()
        if len(rows) >= 20:
            cont_count = sum(
                1 for r in rows if r["first_5s_direction"] == r["winner_side"]
            )
            hist_cont = cont_count / len(rows)
            # Blend 70% historical, 30% heuristic
            cont = 0.7 * hist_cont + 0.3 * cont
            rev = 1.0 - cont
    except Exception:
        pass

    return (round(min(0.85, max(0.15, cont)), 3), round(min(0.85, max(0.15, rev)), 3))


def _no_impulse() -> Dict:
    return {
        "active": False,
        "move_cents": 0.0,
        "duration_s": 0.0,
        "direction": None,
        "time_since_s": 999,
        "continuation_probability": 0.5,
        "reversal_probability": 0.5,
        "signal_type": "NONE",
        "from_price": None,
        "to_price": None,
    }


# ── Microprice ────────────────────────────────────────────────────────────────

def compute_microprice(
    bid_price: float,
    ask_price: float,
    bid_size: float,
    ask_size: float,
) -> float:
    """
    Microprice = depth-weighted midpoint.
    Leads simple midpoint by shifting toward the side with more depth.
    """
    total = bid_size + ask_size
    if total <= 0:
        return (bid_price + ask_price) / 2.0
    return (bid_price * ask_size + ask_price * bid_size) / total


def microprice_signal(
    bid_price: float,
    ask_price: float,
    bid_size: float,
    ask_size: float,
) -> Dict:
    """
    Returns microprice and its directional signal vs midpoint.

    microprice > midpoint → bullish bias (more ask-side liquidity pushing up)
    microprice < midpoint → bearish bias
    """
    mid = (bid_price + ask_price) / 2.0
    mp = compute_microprice(bid_price, ask_price, bid_size, ask_size)
    drift = mp - mid
    drift_cents = drift * 100

    direction = "UP" if drift > 0 else "DOWN"
    strength = min(1.0, abs(drift_cents) / 3.0)  # 3¢ drift = max strength

    return {
        "midpoint": round(mid, 4),
        "microprice": round(mp, 4),
        "drift_cents": round(drift_cents, 3),
        "direction": direction,
        "strength": round(strength, 3),
    }


# ── Full Feature Bundle ───────────────────────────────────────────────────────

def get_feature_bundle(
    slug: str,
    asset: str,
    interval_minutes: int,
    bid_price: Optional[float] = None,
    ask_price: Optional[float] = None,
    bid_size: Optional[float] = None,
    ask_size: Optional[float] = None,
) -> Dict:
    """
    Compute all features for a market at current time.
    """
    hotspot = detect_hotspot(slug)
    impulse = detect_impulse(slug)

    mp_signal: Dict = {}
    if all(v is not None for v in [bid_price, ask_price, bid_size, ask_size]):
        mp_signal = microprice_signal(bid_price, ask_price, bid_size, ask_size)

    return {
        "slug": slug,
        "asset": asset,
        "interval_minutes": interval_minutes,
        "computed_at": int(time.time()),
        "hotspot": hotspot,
        "impulse": impulse,
        "microprice": mp_signal,
    }
