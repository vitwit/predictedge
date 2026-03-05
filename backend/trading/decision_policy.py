"""
Decision Policy — intelligence layer between signal detection and order execution.

Every candidate trade must pass through evaluate() before being sent to the exchange.
The policy runs a set of sequential gates; the first failed gate aborts the trade
and records the reason code. All decisions (APPROVE and REJECT) are persisted to
the signal_events table for full auditability.

Gates (in order):
  1. WIN_RATE      — pattern win-rate >= minimum threshold
  2. SAMPLE_SIZE   — enough historical samples for statistical confidence
  3. EV            — expected value after fees/slippage > 0
  4. SPREAD        — live spread not too wide (market is liquid)
  5. DEPTH         — enough resting depth on our side (can fill)
  6. IMBALANCE     — orderbook depth imbalance supports our direction
  7. COOLDOWN      — not re-entering the same asset/interval too soon after a loss
  8. TIME_WINDOW   — not too early or late in the market window to get a clean fill
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import config
from db import get_connection
from analytics.live_signals import evaluate_all_signals

logger = logging.getLogger(__name__)

# ── Configurable thresholds (can be moved to config.py later) ──────────────

MIN_WIN_RATE_PCT: float = config.MIN_PATTERN_WIN_RATE_PCT   # e.g. 55.0
MIN_SAMPLE_COUNT: int   = 20
MIN_EV_THRESHOLD: float = 0.0          # net EV must be > this after fees
MAX_SPREAD_CENTS: float = 12.0         # reject if spread > 12¢
MIN_DEPTH_USDC:   float = 50.0         # require >= $50 resting on our side within 5¢
MIN_IMBALANCE:    float = -0.20        # reject only when imbalance strongly opposes us
COOLDOWN_SECONDS: int   = 120          # minimum seconds between trades on same asset+interval
POLYMARKET_FEE:   float = 0.02         # 2% fee both sides
SLIPPAGE_EST:     float = 0.005        # 0.5¢ slippage estimate
EARLY_WINDOW_SKIP_S: int = 15          # skip first 15s of market window (noisy open)
LATE_WINDOW_SKIP_S:  int = 20          # skip last 20s of window (thin market)


# ── Data containers ─────────────────────────────────────────────────────────

@dataclass
class SignalInputs:
    slug: str
    asset: str
    interval_minutes: int
    pattern_str: str
    predicted_side: str

    # From pattern engine
    win_rate: float = 0.0
    edge_pct: float = 0.0
    sample_count: int = 0

    # From live market snapshot
    spread_cents: float = 0.0
    bid_depth_5c: float = 0.0
    ask_depth_5c: float = 0.0
    depth_imbalance: float = 0.0    # (bid-ask)/(bid+ask), range [-1, 1]
    spot_vol_30s: float = 0.0       # abs spot % change over last 30s
    time_remaining_s: int = 300     # seconds left in current window

    # Live signal bundle (populated by policy, not required from caller)
    live_signal_bundle: Optional[Dict] = None

    # Execution params
    order_price: float = 0.40
    order_size: float = 10.0


@dataclass
class DecisionResult:
    decision: str                       # "APPROVE" | "REJECT"
    reject_reasons: List[str] = field(default_factory=list)
    ev_score: float = 0.0
    confidence: float = 0.0
    approved: bool = False

    def __post_init__(self):
        self.approved = self.decision == "APPROVE"


# ── EV and confidence helpers ────────────────────────────────────────────────

def _compute_ev(win_rate: float, order_price: float, order_size: float) -> float:
    """Expected value per order after Polymarket fees and slippage estimate."""
    p = win_rate / 100.0
    effective_price = order_price + SLIPPAGE_EST
    if effective_price <= 0 or effective_price >= 1:
        return -999.0
    payout_if_win = order_size / effective_price
    net_if_win    = payout_if_win * (1 - POLYMARKET_FEE) - order_size
    net_if_lose   = -order_size
    ev = p * net_if_win + (1 - p) * net_if_lose
    return round(ev, 4)


def _compute_confidence(sig: SignalInputs, ev: float) -> float:
    """
    Heuristic confidence score 0-100.
    Components:
      - Win rate contribution (40 pts max)
      - Sample size (20 pts max, saturates at 200 samples)
      - Spread quality (20 pts max)
      - Depth/imbalance alignment (20 pts max)
    """
    # Win rate: 55% -> 0pts, 75%+ -> 40pts
    wr_score = min(40.0, max(0.0, (sig.win_rate - MIN_WIN_RATE_PCT) / (75.0 - MIN_WIN_RATE_PCT) * 40.0))

    # Sample size: log-scale 20..200 -> 0..20pts
    import math
    ss_score = min(20.0, max(0.0, math.log10(max(1, sig.sample_count) / MIN_SAMPLE_COUNT) / math.log10(200 / MIN_SAMPLE_COUNT) * 20.0))

    # Spread: tight (<=3¢) -> 20pts, wide (>=MAX) -> 0pts
    sp_score = min(20.0, max(0.0, (MAX_SPREAD_CENTS - sig.spread_cents) / (MAX_SPREAD_CENTS - 3.0) * 20.0))

    # Depth imbalance alignment:
    #   For UP prediction: positive imbalance (more bids) is good
    #   For DOWN prediction: negative imbalance (more asks) is good
    if sig.predicted_side == "UP":
        imb_score = min(20.0, max(0.0, (sig.depth_imbalance + 1.0) / 2.0 * 20.0))
    else:
        imb_score = min(20.0, max(0.0, (1.0 - sig.depth_imbalance) / 2.0 * 20.0))

    base = wr_score + ss_score + sp_score + imb_score   # 0–80 pts

    # Live signal bonus/penalty (up to ±20 pts)
    live_bonus = 0.0
    bundle = sig.live_signal_bundle
    if bundle:
        fused = bundle.get("fused_direction")
        live_conf = float(bundle.get("composite_confidence", 0))
        cross = bundle.get("signals", {}).get("cross_asset", {})
        multiplier = float(cross.get("confirmation_multiplier", 1.0))

        if fused == sig.predicted_side:
            # Live signals agree — bonus proportional to confidence
            live_bonus = min(20.0, live_conf * 0.20 * multiplier)
        elif fused and fused != sig.predicted_side:
            # Live signals disagree — penalty
            live_bonus = -min(20.0, live_conf * 0.20)

    return round(min(100.0, max(0.0, base + live_bonus)), 1)


# ── Gate evaluators ──────────────────────────────────────────────────────────

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


def _gate_ev(sig: SignalInputs, ev: float, reasons: List[str]) -> bool:
    if ev <= MIN_EV_THRESHOLD:
        reasons.append(f"EV:{ev:.4f}<={MIN_EV_THRESHOLD}")
        return False
    return True


def _gate_spread(sig: SignalInputs, reasons: List[str]) -> bool:
    if sig.spread_cents > MAX_SPREAD_CENTS:
        reasons.append(f"SPREAD:{sig.spread_cents:.1f}c>{MAX_SPREAD_CENTS}c")
        return False
    return True


def _gate_depth(sig: SignalInputs, reasons: List[str]) -> bool:
    if sig.bid_depth_5c <= 0 and sig.ask_depth_5c <= 0:
        # No depth data available — skip gate rather than block
        return True
    our_depth = sig.bid_depth_5c if sig.predicted_side == "UP" else sig.ask_depth_5c
    if our_depth < MIN_DEPTH_USDC:
        reasons.append(f"DEPTH:{our_depth:.1f}<{MIN_DEPTH_USDC}USDC")
        return False
    return True


def _gate_imbalance(sig: SignalInputs, reasons: List[str]) -> bool:
    if sig.bid_depth_5c <= 0 and sig.ask_depth_5c <= 0:
        return True
    # Only reject if imbalance strongly opposes our direction
    if sig.predicted_side == "UP" and sig.depth_imbalance < MIN_IMBALANCE:
        reasons.append(f"IMBALANCE:{sig.depth_imbalance:.2f}<{MIN_IMBALANCE}(against UP)")
        return False
    if sig.predicted_side == "DOWN" and sig.depth_imbalance > -MIN_IMBALANCE:
        reasons.append(f"IMBALANCE:{sig.depth_imbalance:.2f}>{-MIN_IMBALANCE}(against DOWN)")
        return False
    return True


def _gate_cooldown(sig: SignalInputs, reasons: List[str]) -> bool:
    """Block re-entry on same asset+interval if last order was a LOSS within cooldown window."""
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
            predicted = sig.predicted_side
            if winner and winner != predicted:
                reasons.append(f"COOLDOWN:recent_loss_on_{sig.asset}_{sig.interval_minutes}m")
                return False
    except Exception as e:
        logger.warning("Cooldown gate DB error: %s", e)
    return True


def _gate_live_signal_conflict(sig: SignalInputs, reasons: List[str]) -> bool:
    """
    Reject if live signals (spot momentum + cross-asset) strongly contradict pattern prediction.
    Passes if live signals agree, are neutral, or have insufficient data.
    """
    bundle = sig.live_signal_bundle
    if not bundle:
        return True  # no live data — pass through

    fused = bundle.get("fused_direction")
    confidence = float(bundle.get("composite_confidence", 0))

    # Only block if live signals are strong AND opposed to our prediction
    if fused and fused != sig.predicted_side and confidence > 40:
        reasons.append(
            f"LIVE_CONFLICT:signals_say_{fused}(conf={confidence:.0f})_vs_predicted_{sig.predicted_side}"
        )
        return False
    return True


def _gate_time_window(sig: SignalInputs, reasons: List[str]) -> bool:
    total_s = sig.interval_minutes * 60
    elapsed = total_s - sig.time_remaining_s
    if elapsed < EARLY_WINDOW_SKIP_S:
        reasons.append(f"TIME_WINDOW:too_early({elapsed}s_elapsed,skip<{EARLY_WINDOW_SKIP_S}s)")
        return False
    if sig.time_remaining_s < LATE_WINDOW_SKIP_S:
        reasons.append(f"TIME_WINDOW:too_late({sig.time_remaining_s}s_remaining,skip<{LATE_WINDOW_SKIP_S}s)")
        return False
    return True


# ── Main evaluate function ────────────────────────────────────────────────────

def evaluate(sig: SignalInputs) -> DecisionResult:
    """
    Run all gates in order. First failure aborts and returns REJECT.
    Returns DecisionResult with decision, reasons, ev_score, confidence.
    """
    reasons: List[str] = []
    ev = _compute_ev(sig.win_rate, sig.order_price, sig.order_size)
    confidence = _compute_confidence(sig, ev)

    # Populate live signal bundle if not already provided
    if sig.live_signal_bundle is None:
        try:
            live_mid = None  # CLOB midpoint unavailable at gate eval time
            sig.live_signal_bundle = evaluate_all_signals(
                sig.asset, sig.interval_minutes, sig.time_remaining_s, live_midpoint=live_mid
            )
        except Exception as e:
            logger.warning("live_signals eval failed: %s", e)
            sig.live_signal_bundle = {}

    gates = [
        lambda: _gate_win_rate(sig, reasons),
        lambda: _gate_sample_size(sig, reasons),
        lambda: _gate_ev(sig, ev, reasons),
        lambda: _gate_spread(sig, reasons),
        lambda: _gate_depth(sig, reasons),
        lambda: _gate_imbalance(sig, reasons),
        lambda: _gate_cooldown(sig, reasons),
        lambda: _gate_time_window(sig, reasons),
        lambda: _gate_live_signal_conflict(sig, reasons),
    ]

    for gate in gates:
        if not gate():
            result = DecisionResult(
                decision="REJECT",
                reject_reasons=reasons,
                ev_score=ev,
                confidence=confidence,
            )
            _persist_signal_event(sig, result, order_id=None)
            logger.debug(
                "REJECT %s %s %sm %s | reasons=%s ev=%.4f conf=%.0f",
                sig.pattern_str, sig.asset, sig.interval_minutes,
                sig.predicted_side, reasons, ev, confidence,
            )
            return result

    result = DecisionResult(
        decision="APPROVE",
        reject_reasons=[],
        ev_score=ev,
        confidence=confidence,
    )
    bundle = sig.live_signal_bundle or {}
    live_dir = bundle.get("fused_direction", "—")
    live_conf = bundle.get("composite_confidence", 0)
    live_reasons = bundle.get("reason_codes", [])
    logger.info(
        "APPROVE %s %s %sm %s | win_rate=%.1f%% edge=%.1f ev=%.4f conf=%.0f "
        "spread=%.1fc depth_imb=%.2f | live_dir=%s live_conf=%.0f live=%s",
        sig.pattern_str, sig.asset, sig.interval_minutes, sig.predicted_side,
        sig.win_rate, sig.edge_pct, ev, confidence,
        sig.spread_cents, sig.depth_imbalance,
        live_dir, live_conf, live_reasons,
    )
    return result


# ── Persistence ───────────────────────────────────────────────────────────────

def _persist_signal_event(
    sig: SignalInputs,
    result: DecisionResult,
    order_id: Optional[int],
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
    sig: SignalInputs,
    result: DecisionResult,
    order_id: Optional[int],
) -> Optional[int]:
    """Call after order is submitted to link the approved signal_event to the order."""
    return _persist_signal_event(sig, result, order_id)
