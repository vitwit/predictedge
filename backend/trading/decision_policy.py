"""
Decision Policy — the complete intelligence layer between signal detection and order execution.

Architecture:
  1. Quick-reject gates (no DB / API calls)
  2. Live market snapshot enrichment (CLOB + spot)
  3. Calibrated P(win) model
  4. Regime conditioning (reliability multipliers)
  5. Feature enrichment (hotspot + impulse + microprice)
  6. Live signal fusion (momentum + cross-asset + FVG + reversal)
  7. EV gate (Kelly-adjusted expected value)
  8. Portfolio risk gate (exposure + circuit breaker)
  9. LLM synthesis gate (borderline confidence only)
  10. Final approve/reject + dynamic size

Every decision (APPROVE + REJECT) is persisted to signal_events.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import config
from db import get_connection
from analytics.live_signals import evaluate_all_signals
from analytics.calibration import combined_p_win
from analytics.regime_classifier import classify_regime, regime_confidence_multiplier
from analytics.feature_store import get_feature_bundle
from analytics.edge_monitor import is_signal_active
from analytics import llm_gate
from trading.risk_manager import get_risk_manager
from trading.position_sizer import compute_size, ev_per_unit

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

MIN_WIN_RATE_PCT: float = config.MIN_PATTERN_WIN_RATE_PCT
MIN_SAMPLE_COUNT: int   = 20
MIN_EV_PER_UNIT: float  = 0.01        # min EV per $1 invested after fees
MAX_SPREAD_CENTS: float = 12.0
MIN_DEPTH_USDC: float   = 50.0
MIN_IMBALANCE: float    = -0.20
COOLDOWN_SECONDS: int   = 90
POLYMARKET_FEE: float   = 0.02
SLIPPAGE_EST: float     = 0.005
EARLY_WINDOW_SKIP_S: int = 15
LATE_WINDOW_SKIP_S: int  = 20
MIN_CONFIDENCE: float    = 35.0        # absolute minimum — below this always REJECT
MIN_CALIBRATED_P: float  = 0.52        # calibrated P(win) must exceed this


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class SignalInputs:
    slug: str
    asset: str
    interval_minutes: int
    pattern_str: str
    predicted_side: str

    win_rate: float = 0.0
    edge_pct: float = 0.0
    sample_count: int = 0

    spread_cents: float = 0.0
    bid_depth_5c: float = 0.0
    ask_depth_5c: float = 0.0
    depth_imbalance: float = 0.0
    spot_vol_30s: float = 0.0
    time_remaining_s: int = 300

    clob_mid: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None

    live_signal_bundle: Optional[Dict] = None

    order_price: float = 0.40
    order_size: float = 10.0


@dataclass
class DecisionResult:
    decision: str                        # "APPROVE" | "REJECT"
    reject_reasons: List[str] = field(default_factory=list)
    ev_score: float = 0.0
    confidence: float = 0.0
    calibrated_p_win: float = 0.0
    recommended_size: float = 0.0
    regime: str = "NORMAL"
    llm_used: bool = False
    llm_decision: str = ""
    llm_reasoning: str = ""
    approved: bool = False

    def __post_init__(self):
        self.approved = self.decision == "APPROVE"


# ── EV calculation ────────────────────────────────────────────────────────────

def _compute_ev(win_rate: float, order_price: float, order_size: float) -> float:
    """EV using pattern-based win rate."""
    p = win_rate / 100.0
    eff_price = min(0.99, order_price + SLIPPAGE_EST)
    payout = order_size / eff_price
    net_win = payout * (1 - POLYMARKET_FEE) - order_size
    ev = p * net_win + (1 - p) * (-order_size)
    return round(ev, 4)


# ── Confidence scoring ────────────────────────────────────────────────────────

def _compute_confidence(
    sig: SignalInputs,
    calib: Dict,
    regime_mult: float,
    live_bundle: Dict,
) -> float:
    import math

    # Win rate: 55% → 0pts, 80%+ → 40pts
    wr = float(sig.win_rate)
    wr_score = min(40.0, max(0.0, (wr - MIN_WIN_RATE_PCT) / (80.0 - MIN_WIN_RATE_PCT) * 40.0))

    # Sample size: log-scale 20..500 → 0..15pts
    ss_score = min(15.0, max(0.0, math.log10(max(1, sig.sample_count) / MIN_SAMPLE_COUNT) / math.log10(500 / MIN_SAMPLE_COUNT) * 15.0))

    # Spread quality: tight → 15pts
    sp_score = min(15.0, max(0.0, (MAX_SPREAD_CENTS - sig.spread_cents) / (MAX_SPREAD_CENTS - 3.0) * 15.0))

    # Depth imbalance alignment: 15pts
    if sig.predicted_side == "UP":
        imb_score = min(15.0, max(0.0, (sig.depth_imbalance + 1.0) / 2.0 * 15.0))
    else:
        imb_score = min(15.0, max(0.0, (1.0 - sig.depth_imbalance) / 2.0 * 15.0))

    base = wr_score + ss_score + sp_score + imb_score  # 0–85 pts

    # Calibration bonus (0–15 pts): calibrated p_win vs 0.5
    calib_edge = float(calib.get("calibration_edge", 0))
    if sig.predicted_side == "DOWN":
        calib_edge = -calib_edge
    calib_score = min(15.0, max(0.0, calib_edge * 100.0 * 0.6))
    base += calib_score

    # FVG bonus (0–10 pts): market is mispriced in our favor
    fvg = float(calib.get("fvg", 0))
    if sig.predicted_side == "DOWN":
        fvg = -fvg
    fvg_score = min(10.0, max(0.0, fvg * 100.0 * 0.5))
    base += fvg_score

    # Live signal bonus/penalty (up to ±20 pts)
    live_bonus = 0.0
    if live_bundle:
        fused = live_bundle.get("fused_direction")
        live_conf = float(live_bundle.get("composite_confidence", 0))
        cross = live_bundle.get("signals", {}).get("cross_asset", {})
        multiplier = float(cross.get("confirmation_multiplier", 1.0))

        if fused == sig.predicted_side:
            live_bonus = min(20.0, live_conf * 0.20 * multiplier)
        elif fused and fused != sig.predicted_side:
            live_bonus = -min(20.0, live_conf * 0.25)

    base += live_bonus

    # Regime multiplier
    final = base * regime_mult

    return round(min(100.0, max(0.0, final)), 1)


# ── Gate evaluators ───────────────────────────────────────────────────────────

def _gate_edge_monitor(sig: SignalInputs, reasons: List[str]) -> bool:
    """Block if signal type has been auto-disabled due to edge decay."""
    if not is_signal_active("PATTERN", sig.asset, sig.interval_minutes):
        reasons.append(f"EDGE_DECAY:PATTERN_{sig.asset}_{sig.interval_minutes}m_auto_disabled")
        return False
    return True


def _gate_win_rate(sig: SignalInputs, reasons: List[str]) -> bool:
    if sig.win_rate < MIN_WIN_RATE_PCT:
        reasons.append(f"WIN_RATE:{sig.win_rate:.1f}<{MIN_WIN_RATE_PCT}")
        return False
    return True


def _gate_sample_size(sig: SignalInputs, reasons: List[str]) -> bool:
    if sig.sample_count < MIN_SAMPLE_COUNT:
        reasons.append(f"SAMPLE_SIZE:{sig.sample_count}<{MIN_SAMPLE_COUNT}")
        return False
    return True


def _gate_calibration(sig: SignalInputs, calib: Dict, reasons: List[str]) -> bool:
    """Require calibrated P(win) >= threshold AND sample_n >= min."""
    p_win = float(calib.get("p_win", 0.5))
    n = int(calib.get("sample_n", 0))
    if n < 30:
        return True  # insufficient calibration data — skip gate
    if p_win < MIN_CALIBRATED_P:
        reasons.append(f"CALIBRATED_P:{p_win:.3f}<{MIN_CALIBRATED_P}(n={n})")
        return False
    return True


def _gate_ev(sig: SignalInputs, ev: float, reasons: List[str]) -> bool:
    ev_unit = ev_per_unit(sig.win_rate, sig.order_price, POLYMARKET_FEE, SLIPPAGE_EST)
    if ev_unit <= MIN_EV_PER_UNIT:
        reasons.append(f"EV_UNIT:{ev_unit:.4f}<={MIN_EV_PER_UNIT}")
        return False
    return True


def _gate_spread(sig: SignalInputs, reasons: List[str]) -> bool:
    if sig.spread_cents > MAX_SPREAD_CENTS:
        reasons.append(f"SPREAD:{sig.spread_cents:.1f}c>{MAX_SPREAD_CENTS}c")
        return False
    return True


def _gate_depth(sig: SignalInputs, reasons: List[str]) -> bool:
    if sig.bid_depth_5c <= 0 and sig.ask_depth_5c <= 0:
        return True
    our_depth = sig.bid_depth_5c if sig.predicted_side == "UP" else sig.ask_depth_5c
    if our_depth < MIN_DEPTH_USDC:
        reasons.append(f"DEPTH:{our_depth:.1f}<{MIN_DEPTH_USDC}USDC")
        return False
    return True


def _gate_imbalance(sig: SignalInputs, reasons: List[str]) -> bool:
    if sig.bid_depth_5c <= 0 and sig.ask_depth_5c <= 0:
        return True
    if sig.predicted_side == "UP" and sig.depth_imbalance < MIN_IMBALANCE:
        reasons.append(f"IMBALANCE:{sig.depth_imbalance:.2f}<{MIN_IMBALANCE}(vs UP)")
        return False
    if sig.predicted_side == "DOWN" and sig.depth_imbalance > -MIN_IMBALANCE:
        reasons.append(f"IMBALANCE:{sig.depth_imbalance:.2f}>{-MIN_IMBALANCE}(vs DOWN)")
        return False
    return True


def _gate_regime(sig: SignalInputs, regime: str, reasons: List[str]) -> bool:
    """Block trades in CHOP regime (signals are noise)."""
    if regime == "CHOP":
        reasons.append(f"REGIME:CHOP_market_no_signal_edge")
        return False
    return True


def _gate_cooldown(sig: SignalInputs, reasons: List[str]) -> bool:
    try:
        conn = get_connection()
        cutoff = int(time.time()) - COOLDOWN_SECONDS
        row = conn.execute(
            """
            SELECT o.status, mr.winner_side
            FROM auto_trade_orders o
            LEFT JOIN market_resolutions mr ON mr.slug = o.slug
            WHERE o.asset = ? AND o.interval_minutes = ?
              AND o.status = 'submitted'
              AND o.created_at >= ?
            ORDER BY o.created_at DESC
            LIMIT 1
            """,
            (sig.asset, sig.interval_minutes, cutoff),
        ).fetchone()
        conn.close()
        if row:
            winner = row["winner_side"]
            if winner and winner != sig.predicted_side:
                reasons.append(f"COOLDOWN:recent_loss_on_{sig.asset}_{sig.interval_minutes}m")
                return False
    except Exception as e:
        logger.warning("Cooldown gate DB error: %s", e)
    return True


def _gate_time_window(sig: SignalInputs, reasons: List[str]) -> bool:
    total_s = sig.interval_minutes * 60
    elapsed = total_s - sig.time_remaining_s
    if elapsed < EARLY_WINDOW_SKIP_S:
        reasons.append(f"TIME_WINDOW:too_early({elapsed}s,skip<{EARLY_WINDOW_SKIP_S}s)")
        return False
    if sig.time_remaining_s < LATE_WINDOW_SKIP_S:
        reasons.append(f"TIME_WINDOW:too_late({sig.time_remaining_s}s,skip<{LATE_WINDOW_SKIP_S}s)")
        return False
    return True


def _gate_live_signal_conflict(sig: SignalInputs, live_bundle: Dict, reasons: List[str]) -> bool:
    if not live_bundle:
        return True
    fused = live_bundle.get("fused_direction")
    confidence = float(live_bundle.get("composite_confidence", 0))
    if fused and fused != sig.predicted_side and confidence > 50:
        reasons.append(
            f"LIVE_CONFLICT:signals_say_{fused}(conf={confidence:.0f})_vs_{sig.predicted_side}"
        )
        return False
    return True


def _gate_portfolio_risk(sig: SignalInputs, size: float, reasons: List[str]) -> bool:
    rm = get_risk_manager()
    allowed, reason = rm.can_trade(sig.asset, size)
    if not allowed:
        reasons.append(f"RISK:{reason}")
        return False
    return True


def _gate_hotspot_conflict(
    sig: SignalInputs, features: Dict, reasons: List[str]
) -> bool:
    """If hotspot is strongly signaling the opposite direction, block."""
    hotspot = features.get("hotspot", {})
    if not hotspot.get("active"):
        return True
    dominant = hotspot.get("dominant_side")
    conf = float(hotspot.get("confidence", 0))
    if dominant and dominant != sig.predicted_side and conf > 0.7:
        reasons.append(f"HOTSPOT_CONFLICT:zone_{hotspot['zone_center']}c_dominant_{dominant}(conf={conf:.2f})")
        return False
    return True


def _gate_minimum_confidence(confidence: float, reasons: List[str]) -> bool:
    if confidence < MIN_CONFIDENCE:
        reasons.append(f"LOW_CONFIDENCE:{confidence:.0f}<{MIN_CONFIDENCE}")
        return False
    return True


# ── Main evaluate function ────────────────────────────────────────────────────

def evaluate(sig: SignalInputs) -> DecisionResult:
    """
    Run full 10-stage decision pipeline.
    Returns DecisionResult with decision, reasons, ev, confidence, recommended_size.
    """
    reasons: List[str] = []
    ev = _compute_ev(sig.win_rate, sig.order_price, sig.order_size)

    # Stage 1: Quick reject gates (no external calls)
    if not _gate_win_rate(sig, reasons):
        return _reject(sig, ev, 0.0, {}, "NORMAL", reasons)
    if not _gate_sample_size(sig, reasons):
        return _reject(sig, ev, 0.0, {}, "NORMAL", reasons)
    if not _gate_time_window(sig, reasons):
        return _reject(sig, ev, 0.0, {}, "NORMAL", reasons)
    if not _gate_edge_monitor(sig, reasons):
        return _reject(sig, ev, 0.0, {}, "NORMAL", reasons)

    # Stage 2: Regime classification
    regime_info = classify_regime(sig.asset)
    regime = regime_info.get("regime", "NORMAL")
    regime_mult = regime_confidence_multiplier(regime)

    if not _gate_regime(sig, regime, reasons):
        return _reject(sig, ev, 0.0, {}, regime, reasons)

    # Stage 3: Live signals
    if sig.live_signal_bundle is None:
        try:
            sig.live_signal_bundle = evaluate_all_signals(
                sig.asset, sig.interval_minutes,
                sig.time_remaining_s, live_midpoint=sig.clob_mid,
            )
        except Exception as e:
            logger.warning("live_signals failed: %s", e)
            sig.live_signal_bundle = {}

    live_bundle = sig.live_signal_bundle or {}

    # Stage 4: Calibrated P(win)
    spot_30s_pct = 0.0
    try:
        signals = live_bundle.get("signals", {})
        mom = signals.get("spot_momentum", {})
        spot_30s_pct = float(mom.get("spot_change_30s", 0) or 0) * 100
    except Exception:
        pass

    hour_utc = int(time.strftime("%H", time.gmtime()))
    calib = combined_p_win(
        sig.asset, sig.interval_minutes,
        spot_30s_pct, sig.clob_mid, hour_utc, sig.predicted_side,
    )

    # Stage 5: Feature enrichment (hotspot + impulse + microprice)
    features: Dict = {}
    try:
        b1, b2 = sig.bid_size, sig.ask_size
        bid_p = None
        ask_p = None
        if sig.spread_cents > 0 and sig.clob_mid:
            spread = sig.spread_cents / 100.0
            bid_p = sig.clob_mid - spread / 2
            ask_p = sig.clob_mid + spread / 2
        features = get_feature_bundle(
            sig.slug, sig.asset, sig.interval_minutes,
            bid_price=bid_p, ask_price=ask_p,
            bid_size=b1, ask_size=b2,
        )
    except Exception as e:
        logger.debug("feature_bundle failed: %s", e)

    # Stage 6: Compute confidence
    confidence = _compute_confidence(sig, calib, regime_mult, live_bundle)

    if not _gate_minimum_confidence(confidence, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)

    if not _gate_calibration(sig, calib, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)
    if not _gate_ev(sig, ev, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)
    if not _gate_spread(sig, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)
    if not _gate_depth(sig, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)
    if not _gate_imbalance(sig, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)
    if not _gate_hotspot_conflict(sig, features, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)
    if not _gate_live_signal_conflict(sig, live_bundle, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)
    if not _gate_cooldown(sig, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)

    # Stage 7: Dynamic position sizing
    recommended_size = compute_size(
        sig.win_rate, sig.order_price, confidence,
        sig.order_size, regime,
    )

    # Stage 8: Portfolio risk gate (with recommended size)
    if not _gate_portfolio_risk(sig, recommended_size, reasons):
        return _reject(sig, ev, confidence, calib, regime, reasons)

    # Stage 9: LLM synthesis gate (borderline decisions only)
    llm_used = False
    llm_dec = ""
    llm_reason = ""
    conf_min = config.LLM_GATE_CONF_MIN
    conf_max = config.LLM_GATE_CONF_MAX

    if conf_min <= confidence <= conf_max:
        try:
            context = {
                "slug": sig.slug,
                "asset": sig.asset,
                "interval_minutes": sig.interval_minutes,
                "predicted_side": sig.predicted_side,
                "order_price": sig.order_price,
                "win_rate": sig.win_rate,
                "sample_count": sig.sample_count,
                "confidence": confidence,
                "ev_score": ev,
                "pattern_str": sig.pattern_str,
                "regime": regime,
                "time_remaining_s": sig.time_remaining_s,
                "live_signals": live_bundle.get("signals", {}),
                "calibration": calib,
                "hotspot": features.get("hotspot", {}),
                "impulse": features.get("impulse", {}),
            }
            llm_result = llm_gate.evaluate(context)
            llm_used = llm_result.get("called", False)
            llm_dec = llm_result.get("decision", "SKIP")
            llm_reason = llm_result.get("reasoning", "")

            if llm_dec == "REJECT":
                reasons.append(f"LLM_REJECT:{llm_reason[:80]}")
                return _reject(sig, ev, confidence, calib, regime, reasons, llm_used, llm_dec, llm_reason)
        except Exception as e:
            logger.warning("LLM gate failed: %s", e)

    # Stage 10: APPROVE
    result = DecisionResult(
        decision="APPROVE",
        reject_reasons=[],
        ev_score=ev,
        confidence=confidence,
        calibrated_p_win=float(calib.get("p_win", 0.5)),
        recommended_size=recommended_size,
        regime=regime,
        llm_used=llm_used,
        llm_decision=llm_dec,
        llm_reasoning=llm_reason,
    )
    _persist_signal_event(sig, result, order_id=None)

    logger.info(
        "APPROVE %s %s %sm %s | wr=%.1f%% calib_p=%.3f ev=%.4f conf=%.0f size=%.2f "
        "regime=%s fvg=%.1fc llm=%s",
        sig.pattern_str, sig.asset, sig.interval_minutes, sig.predicted_side,
        sig.win_rate, float(calib.get("p_win", 0.5)),
        ev, confidence, recommended_size, regime,
        float(calib.get("fvg_cents", 0)),
        llm_dec or "n/a",
    )
    return result


def _reject(
    sig: SignalInputs,
    ev: float,
    confidence: float,
    calib: Dict,
    regime: str,
    reasons: List[str],
    llm_used: bool = False,
    llm_dec: str = "",
    llm_reason: str = "",
) -> DecisionResult:
    result = DecisionResult(
        decision="REJECT",
        reject_reasons=reasons,
        ev_score=ev,
        confidence=confidence,
        calibrated_p_win=float(calib.get("p_win", 0.5)) if calib else 0.5,
        recommended_size=sig.order_size,
        regime=regime,
        llm_used=llm_used,
        llm_decision=llm_dec,
        llm_reasoning=llm_reason,
    )
    _persist_signal_event(sig, result, order_id=None)
    logger.debug(
        "REJECT %s %s %sm %s | reasons=%s ev=%.4f conf=%.0f",
        sig.pattern_str, sig.asset, sig.interval_minutes,
        sig.predicted_side, reasons, ev, confidence,
    )
    return result


# ── Persistence ───────────────────────────────────────────────────────────────

def _persist_signal_event(
    sig: SignalInputs, result: DecisionResult, order_id: Optional[int]
) -> Optional[int]:
    try:
        conn = get_connection()
        cur = conn.execute(
            """
            INSERT INTO signal_events (
                slug, asset, interval_minutes, pattern_str, predicted_side,
                win_rate, edge_pct, sample_count,
                spread_cents, bid_depth_5c, ask_depth_5c, depth_imbalance,
                spot_vol_30s, time_remaining_s,
                ev_score, confidence,
                decision, reject_reasons, order_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sig.slug, sig.asset, sig.interval_minutes,
                sig.pattern_str, sig.predicted_side,
                sig.win_rate, sig.edge_pct, sig.sample_count,
                sig.spread_cents, sig.bid_depth_5c, sig.ask_depth_5c,
                sig.depth_imbalance, sig.spot_vol_30s, sig.time_remaining_s,
                result.ev_score, result.confidence,
                result.decision,
                json.dumps(result.reject_reasons),
                order_id,
                int(time.time()),
            ),
        )
        conn.commit()
        event_id = cur.lastrowid
        conn.close()
        return event_id
    except Exception as e:
        logger.error("Failed to persist signal_event: %s", e)
        return None


def persist_signal_event_with_order(
    sig: SignalInputs, result: DecisionResult, order_id: Optional[int]
) -> Optional[int]:
    return _persist_signal_event(sig, result, order_id)
