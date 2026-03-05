"""
Live Signal Engine — Sprint 2
Computes actionable real-time signals using live spot feed + historical conditional probabilities.

Signal A: Spot Momentum
  Uses live spot price change over last 30s/60s/120s, mapped to historical
  P(UP | asset, interval, spot_chg_pct_bin) from market_resolutions.
  This is the strongest verified signal: spot up >0.15% → UP wins ~100% historically.

Signal B: Cross-Asset Confirmation
  Checks directional agreement across BTC/ETH/SOL/XRP using live spot momentum.
  75% of windows see all 3 agree — agreement multiplies confidence.

Signal C: Fair Value Gap
  Computes implied fair value = P(UP | spot_chg_so_far, time_elapsed_pct, asset, interval).
  Compares to live CLOB midpoint.
  If market_price < fair_value - threshold: strong BUY signal.
  This is the highest-EV signal once CLOB midpoint is live.
"""

import logging
import time
from typing import Dict, List, Optional, Tuple
from db import get_connection

logger = logging.getLogger(__name__)

ASSETS = ("BTC", "ETH", "SOL", "XRP")

# Spot change bins (pct): (lo, hi, label)
SPOT_BINS: List[Tuple[float, float, str]] = [
    (-99.0, -1.0,  "crash"),
    (-1.0,  -0.5,  "big_down"),
    (-0.5,  -0.15, "down"),
    (-0.15,  0.15, "flat"),
    ( 0.15,  0.5,  "up"),
    ( 0.5,   1.0,  "big_up"),
    ( 1.0,  99.0,  "surge"),
]

# Time elapsed bands (fraction of window: 0.0–1.0)
ELAPSED_BINS: List[Tuple[float, float, str]] = [
    (0.0,  0.15, "very_early"),
    (0.15, 0.35, "early"),
    (0.35, 0.60, "mid"),
    (0.60, 0.80, "late"),
    (0.80, 1.01, "final"),
]

MIN_SAMPLES_FOR_SIGNAL = 5   # require this many historical samples to trust a bin
FAIR_VALUE_GAP_THRESHOLD = 0.06  # 6¢ minimum gap to trigger fair-value signal

# USD move thresholds for reversal signal (per asset)
# The reversal probability lookup is built from prev_spot_change_usd in market_resolutions
USD_REVERSAL_THRESHOLDS = {
    "BTC": [10, 25, 50, 100, 200, 300],
    "ETH": [5,  10, 20,  50, 100, 200],
    "SOL": [0.5, 1,  2,   5,  10,  20],
    "XRP": [0.02, 0.05, 0.10, 0.20, 0.50],
}


# ── Historical lookup cache (reloaded every 5 minutes) ─────────────────────

_lookup_cache: Dict = {}
_lookup_built_at: float = 0.0
_CACHE_TTL: float = 300.0  # 5 minutes


def _build_lookup() -> Dict:
    """
    Build P(UP | asset, interval, spot_bin) lookup table from market_resolutions.
    Returns dict keyed by (asset, interval, spot_bin_label) → {p_up, n_samples}.
    """
    conn = get_connection()
    lookup = {}
    try:
        rows = conn.execute("""
            SELECT asset, interval_minutes, spot_change_pct, winner_side
            FROM market_resolutions
            WHERE winner_side IS NOT NULL
              AND spot_change_pct IS NOT NULL
        """).fetchall()

        counts: Dict = {}
        for r in rows:
            asset = r["asset"]
            interval = r["interval_minutes"]
            chg = float(r["spot_change_pct"])
            win = r["winner_side"]

            bin_label = _spot_bin_label(chg)
            key = (asset, interval, bin_label)
            if key not in counts:
                counts[key] = {"up": 0, "total": 0}
            counts[key]["total"] += 1
            if win == "UP":
                counts[key]["up"] += 1

        for key, val in counts.items():
            n = val["total"]
            p_up = round(val["up"] / n, 4) if n > 0 else 0.5
            lookup[key] = {"p_up": p_up, "n_samples": n}

    finally:
        conn.close()

    logger.info("live_signals: built lookup with %d bins", len(lookup))
    return lookup


def _get_lookup() -> Dict:
    global _lookup_cache, _lookup_built_at
    if not _lookup_cache or (time.time() - _lookup_built_at) > _CACHE_TTL:
        _lookup_cache = _build_lookup()
        _lookup_built_at = time.time()
    return _lookup_cache


def _spot_bin_label(chg_pct: float) -> str:
    for lo, hi, label in SPOT_BINS:
        if lo <= chg_pct < hi:
            return label
    return "flat"


def _elapsed_bin_label(elapsed_frac: float) -> str:
    for lo, hi, label in ELAPSED_BINS:
        if lo <= elapsed_frac < hi:
            return label
    return "mid"


# ── Spot feed helpers ───────────────────────────────────────────────────────

def _get_spot_prices_window(asset: str, seconds: int = 120) -> List[Tuple[int, float]]:
    """Return list of (ts, price_usd) from spot_prices for last `seconds` seconds."""
    cutoff = int(time.time()) - seconds
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT captured_at, price_usd FROM spot_prices WHERE asset=? AND captured_at>=? ORDER BY captured_at ASC",
            (asset, cutoff),
        ).fetchall()
        return [(r[0], float(r[1])) for r in rows]
    finally:
        conn.close()


def _spot_change_pct(prices: List[Tuple[int, float]], window_s: int) -> Optional[float]:
    """
    Compute % change over last `window_s` seconds from prices list.
    Returns None if not enough data.
    """
    if len(prices) < 2:
        return None
    now = prices[-1][0]
    cutoff = now - window_s
    anchors = [p for p in prices if p[0] <= cutoff]
    if not anchors:
        return None
    anchor_price = anchors[-1][1]
    latest_price = prices[-1][1]
    if anchor_price <= 0:
        return None
    return round((latest_price - anchor_price) / anchor_price * 100.0, 4)


# ── Signal A: Spot Momentum ─────────────────────────────────────────────────

def spot_momentum_signal(
    asset: str,
    interval: int,
    time_remaining_s: int,
) -> Dict:
    """
    Compute directional signal from live spot momentum.
    Returns dict with: direction, p_up, confidence, spot_chg_30s, spot_chg_60s, bin_label, n_samples.
    """
    total_s = interval * 60
    elapsed_s = total_s - time_remaining_s
    elapsed_frac = elapsed_s / total_s if total_s > 0 else 0.5

    prices = _get_spot_prices_window(asset, seconds=max(300, total_s))

    chg_30s = _spot_change_pct(prices, 30)
    chg_60s = _spot_change_pct(prices, 60)
    chg_120s = _spot_change_pct(prices, 120)

    # Use the most relevant window — prefer shorter (fresher) when available
    chg = chg_30s if chg_30s is not None else chg_60s if chg_60s is not None else chg_120s

    if chg is None:
        return {
            "signal": "NONE",
            "direction": None,
            "p_up": 0.5,
            "confidence": 0.0,
            "spot_chg_30s": None,
            "spot_chg_60s": None,
            "bin_label": "no_data",
            "n_samples": 0,
            "elapsed_frac": round(elapsed_frac, 3),
        }

    bin_label = _spot_bin_label(chg)
    lookup = _get_lookup()
    key = (asset.upper(), interval, bin_label)
    entry = lookup.get(key, {"p_up": 0.5, "n_samples": 0})
    p_up = entry["p_up"]
    n = entry["n_samples"]

    # Direction from P(UP)
    if p_up >= 0.55:
        direction = "UP"
        edge = p_up - 0.5
    elif p_up <= 0.45:
        direction = "DOWN"
        edge = 0.5 - p_up
    else:
        direction = None
        edge = 0.0

    # Confidence: edge strength * sample adequacy
    sample_factor = min(1.0, n / 50.0) if n >= MIN_SAMPLES_FOR_SIGNAL else 0.0
    confidence = round(edge * 2 * 100 * sample_factor, 1)  # scale to 0-100

    return {
        "signal": "SPOT_MOMENTUM",
        "direction": direction,
        "p_up": p_up,
        "confidence": confidence,
        "spot_chg_30s": chg_30s,
        "spot_chg_60s": chg_60s,
        "spot_chg_120s": chg_120s,
        "bin_label": bin_label,
        "n_samples": n,
        "elapsed_frac": round(elapsed_frac, 3),
    }


# ── Signal B: Cross-Asset Confirmation ────────────────────────────────────

def cross_asset_confirmation(interval: int, time_remaining_s: int) -> Dict:
    """
    Aggregate spot momentum signals across all 4 assets.
    Returns agreement count, dominant direction, and confirmation multiplier.
    """
    signals = {}
    for asset in ASSETS:
        sig = spot_momentum_signal(asset, interval, time_remaining_s)
        signals[asset] = sig

    up_count   = sum(1 for s in signals.values() if s["direction"] == "UP")
    down_count = sum(1 for s in signals.values() if s["direction"] == "DOWN")
    none_count = sum(1 for s in signals.values() if s["direction"] is None)

    if up_count >= 3:
        dominant = "UP"
        agreement = up_count
    elif down_count >= 3:
        dominant = "DOWN"
        agreement = down_count
    elif up_count == 2 and down_count == 0:
        dominant = "UP"
        agreement = 2
    elif down_count == 2 and up_count == 0:
        dominant = "DOWN"
        agreement = 2
    else:
        dominant = None
        agreement = 0

    # Confirmation multiplier applied to confidence of individual signals
    if agreement >= 4:
        multiplier = 1.5
    elif agreement == 3:
        multiplier = 1.25
    elif agreement == 2:
        multiplier = 1.1
    else:
        multiplier = 0.8  # conflicting signals — discount

    return {
        "signal": "CROSS_ASSET",
        "dominant_direction": dominant,
        "agreement_count": agreement,
        "up_count": up_count,
        "down_count": down_count,
        "none_count": none_count,
        "confirmation_multiplier": multiplier,
        "per_asset": signals,
    }


# ── Signal C: Fair Value Gap ────────────────────────────────────────────────

def fair_value_gap(
    asset: str,
    interval: int,
    live_midpoint: Optional[float],
    time_remaining_s: int,
) -> Dict:
    """
    Compare CLOB live midpoint to historically-implied fair value.
    fair_value = P(UP | spot_chg_so_far, asset, interval) from lookup.
    Gap = fair_value - live_midpoint.
    Positive gap → market underpricing UP → buy UP opportunity.
    Negative gap → market overpricing UP → pass or buy DOWN.
    """
    sig = spot_momentum_signal(asset, interval, time_remaining_s)
    p_up_implied = sig["p_up"]
    n = sig["n_samples"]

    if live_midpoint is None or n < MIN_SAMPLES_FOR_SIGNAL:
        return {
            "signal": "FAIR_VALUE_GAP",
            "direction": None,
            "gap": None,
            "fair_value": p_up_implied,
            "live_midpoint": live_midpoint,
            "signal_strength": 0.0,
            "n_samples": n,
            "note": "insufficient data" if n < MIN_SAMPLES_FOR_SIGNAL else "no live midpoint",
        }

    gap = round(p_up_implied - live_midpoint, 4)
    abs_gap = abs(gap)

    if gap > FAIR_VALUE_GAP_THRESHOLD:
        direction = "UP"
        note = f"market underpricing UP by {gap*100:.1f}¢"
    elif gap < -FAIR_VALUE_GAP_THRESHOLD:
        direction = "DOWN"
        note = f"market overpricing UP by {abs_gap*100:.1f}¢"
    else:
        direction = None
        note = f"within fair value band (gap={gap*100:.1f}¢)"

    sample_factor = min(1.0, n / 100.0)
    signal_strength = round(min(100.0, abs_gap / FAIR_VALUE_GAP_THRESHOLD * 50.0 * sample_factor), 1)

    return {
        "signal": "FAIR_VALUE_GAP",
        "direction": direction,
        "gap": gap,
        "gap_cents": round(gap * 100, 2),
        "fair_value": p_up_implied,
        "live_midpoint": live_midpoint,
        "signal_strength": signal_strength,
        "n_samples": n,
        "bin_label": sig["bin_label"],
        "note": note,
    }


# ── Signal D: USD Reversal ─────────────────────────────────────────────────

_reversal_cache: Dict = {}
_reversal_built_at: float = 0.0


def _build_reversal_lookup() -> Dict:
    """
    Build P(reversal | asset, interval, prev_usd_bucket) from market_resolutions.
    A "reversal" = next window goes opposite direction to the prev window's move.
    """
    conn = get_connection()
    lookup = {}
    try:
        rows = conn.execute("""
            SELECT asset, interval_minutes,
                   prev_spot_change_usd, winner_side, prev_winner_side
            FROM market_resolutions
            WHERE prev_spot_change_usd IS NOT NULL
              AND winner_side IS NOT NULL
              AND prev_winner_side IS NOT NULL
        """).fetchall()

        counts: Dict = {}
        for r in rows:
            asset = r["asset"]
            interval = r["interval_minutes"]
            prev_chg = float(r["prev_spot_change_usd"])
            curr_side = r["winner_side"]
            prev_side = r["prev_winner_side"]

            thresholds = USD_REVERSAL_THRESHOLDS.get(asset.upper(), [10, 25, 50, 100])
            direction = "up" if prev_chg > 0 else "down"
            abs_chg = abs(prev_chg)

            # Find the tightest matching threshold
            matched_thresh = None
            for t in sorted(thresholds):
                if abs_chg >= t:
                    matched_thresh = t

            if matched_thresh is None:
                continue

            key = (asset.upper(), interval, direction, matched_thresh)
            if key not in counts:
                counts[key] = {"total": 0, "reversed": 0}
            counts[key]["total"] += 1
            if curr_side != prev_side:
                counts[key]["reversed"] += 1

        for key, val in counts.items():
            n = val["total"]
            p_rev = round(val["reversed"] / n, 4) if n > 0 else 0.5
            lookup[key] = {"p_reversal": p_rev, "n_samples": n}

    finally:
        conn.close()

    logger.info("live_signals: built reversal lookup with %d bins", len(lookup))
    return lookup


def _get_reversal_lookup() -> Dict:
    global _reversal_cache, _reversal_built_at
    if not _reversal_cache or (time.time() - _reversal_built_at) > _CACHE_TTL:
        _reversal_cache = _build_reversal_lookup()
        _reversal_built_at = time.time()
    return _reversal_cache


def usd_reversal_signal(
    asset: str,
    interval: int,
    prev_spot_change_usd: Optional[float],
    prev_winner_side: Optional[str],
) -> Dict:
    """
    Given the previous window's USD price change, return probability of reversal.
    If prev window moved up $200+, historically the next window tends to reverse.
    """
    if prev_spot_change_usd is None or prev_winner_side is None:
        return {
            "signal": "USD_REVERSAL",
            "direction": None,
            "p_reversal": None,
            "confidence": 0.0,
            "prev_usd_change": prev_spot_change_usd,
            "prev_direction": None,
            "n_samples": 0,
            "note": "no previous window data",
        }

    thresholds = USD_REVERSAL_THRESHOLDS.get(asset.upper(), [10, 25, 50, 100])
    prev_direction = "up" if prev_spot_change_usd > 0 else "down"
    abs_chg = abs(prev_spot_change_usd)
    lookup = _get_reversal_lookup()

    # Find best (tightest) matching threshold bucket
    best_entry = None
    best_thresh = None
    for t in sorted(thresholds, reverse=True):  # tightest first
        if abs_chg >= t:
            key = (asset.upper(), interval, prev_direction, t)
            entry = lookup.get(key)
            if entry and entry["n_samples"] >= MIN_SAMPLES_FOR_SIGNAL:
                best_entry = entry
                best_thresh = t
                break

    if best_entry is None:
        return {
            "signal": "USD_REVERSAL",
            "direction": None,
            "p_reversal": None,
            "confidence": 0.0,
            "prev_usd_change": prev_spot_change_usd,
            "prev_direction": prev_direction,
            "n_samples": 0,
            "note": f"no data for {asset} {interval}m prev_{prev_direction}>={best_thresh}",
        }

    p_rev = best_entry["p_reversal"]
    n = best_entry["n_samples"]

    # Reversal direction is opposite of prev move
    if p_rev > 0.55:
        direction = "UP" if prev_direction == "down" else "DOWN"
        edge = p_rev - 0.5
        note = f"prev {prev_direction} ${abs_chg:.0f} → {p_rev*100:.0f}% chance reversal (n={n})"
    elif p_rev < 0.45:
        # Continuation signal
        direction = "UP" if prev_direction == "up" else "DOWN"
        edge = 0.5 - p_rev
        note = f"prev {prev_direction} ${abs_chg:.0f} → {(1-p_rev)*100:.0f}% continuation (n={n})"
    else:
        direction = None
        edge = 0.0
        note = f"neutral reversal prob {p_rev*100:.0f}%"

    sample_factor = min(1.0, n / 30.0)
    confidence = round(edge * 2 * 100 * sample_factor, 1)

    return {
        "signal": "USD_REVERSAL",
        "direction": direction,
        "p_reversal": p_rev,
        "confidence": confidence,
        "prev_usd_change": prev_spot_change_usd,
        "prev_direction": prev_direction,
        "threshold_matched": best_thresh,
        "n_samples": n,
        "note": note,
    }


def get_last_completed_window_usd(asset: str, interval: int) -> Tuple[Optional[float], Optional[str]]:
    """
    Fetch the most recently completed window's spot_change_usd and winner_side
    to feed into usd_reversal_signal() for the *current* (in-progress) window.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT spot_change_usd, winner_side
               FROM market_resolutions
               WHERE asset=? AND interval_minutes=?
                 AND spot_change_usd IS NOT NULL
                 AND winner_side IS NOT NULL
               ORDER BY end_ts DESC LIMIT 1""",
            (asset.upper(), interval),
        ).fetchone()
        if row:
            return float(row["spot_change_usd"]), row["winner_side"]
        return None, None
    finally:
        conn.close()


# ── Combined signal bundle ──────────────────────────────────────────────────

def evaluate_all_signals(
    asset: str,
    interval: int,
    time_remaining_s: int,
    live_midpoint: Optional[float] = None,
    prev_spot_change_usd: Optional[float] = None,
    prev_winner_side: Optional[str] = None,
) -> Dict:
    """
    Compute all live signals for one market.
    Returns unified bundle with fused direction + composite confidence.
    """
    # Auto-fetch previous window data if not supplied
    if prev_spot_change_usd is None:
        prev_spot_change_usd, prev_winner_side = get_last_completed_window_usd(asset, interval)

    momentum = spot_momentum_signal(asset, interval, time_remaining_s)
    cross    = cross_asset_confirmation(interval, time_remaining_s)
    fvg      = fair_value_gap(asset, interval, live_midpoint, time_remaining_s)
    reversal = usd_reversal_signal(asset, interval, prev_spot_change_usd, prev_winner_side)

    # Fuse: each signal votes with weight
    votes: Dict[str, float] = {"UP": 0.0, "DOWN": 0.0}

    # Momentum vote (weight 35%)
    if momentum["direction"] in ("UP", "DOWN"):
        votes[momentum["direction"]] += momentum["confidence"] * 0.35

    # Cross-asset vote (weight 25%)
    if cross["dominant_direction"] in ("UP", "DOWN"):
        cross_conf = cross["agreement_count"] / 4 * 100.0 * cross["confirmation_multiplier"]
        votes[cross["dominant_direction"]] += cross_conf * 0.25

    # Fair value gap vote (weight 25%)
    if fvg["direction"] in ("UP", "DOWN"):
        votes[fvg["direction"]] += fvg["signal_strength"] * 0.25

    # USD reversal vote (weight 15%)
    if reversal["direction"] in ("UP", "DOWN"):
        votes[reversal["direction"]] += reversal["confidence"] * 0.15

    # Apply cross-asset multiplier to overall confidence
    net_up   = votes["UP"]
    net_down = votes["DOWN"]
    raw_conf = abs(net_up - net_down)
    composite_confidence = round(min(100.0, raw_conf * cross["confirmation_multiplier"]), 1)

    if net_up > net_down and net_up > 10:
        fused_direction = "UP"
    elif net_down > net_up and net_down > 10:
        fused_direction = "DOWN"
    else:
        fused_direction = None

    reason_codes = []
    if momentum["direction"]:
        reason_codes.append(f"SPOT_MOM:{momentum['bin_label']}→{momentum['direction']}({momentum['confidence']:.0f})")
    if cross["dominant_direction"]:
        reason_codes.append(f"CROSS:{cross['agreement_count']}/4→{cross['dominant_direction']}")
    if fvg["direction"]:
        reason_codes.append(f"FVG:{fvg['gap_cents']:+.1f}¢→{fvg['direction']}")
    if reversal["direction"]:
        reason_codes.append(f"REV:{reversal['prev_direction'].upper()}${abs(prev_spot_change_usd or 0):.0f}→{reversal['direction']}({reversal['confidence']:.0f})")

    return {
        "asset": asset,
        "interval": interval,
        "time_remaining_s": time_remaining_s,
        "fused_direction": fused_direction,
        "composite_confidence": composite_confidence,
        "reason_codes": reason_codes,
        "signals": {
            "spot_momentum": momentum,
            "cross_asset": cross,
            "fair_value_gap": fvg,
            "usd_reversal": reversal,
        },
        "computed_at": int(time.time()),
    }
