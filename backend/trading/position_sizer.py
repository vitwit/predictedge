"""
Kelly criterion position sizer for prediction market trades.

Kelly fraction for binary outcome (buy UP at price p):
  - If UP wins: net return = (1 - p) / p
  - If DOWN wins: lose stake
  - Kelly: f* = win_rate - (1 - win_rate) * p / (1 - p)
  - Fractional Kelly: 0.25 * f* (safety factor)
  - Scale relative to base_size with confidence adjustment
"""

import logging
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

KELLY_FRACTION = config.KELLY_FRACTION          # 0.25 (fractional Kelly)
KELLY_BASELINE_F = config.KELLY_BASELINE_F      # 0.05 (reference Kelly at base_size)
KELLY_MIN_MULT = config.KELLY_MIN_MULT          # 0.5x min
KELLY_MAX_MULT = config.KELLY_MAX_MULT          # 3.0x max
CONF_TARGET = config.KELLY_CONF_TARGET          # 70 — target confidence for 1x sizing


def kelly_fraction(win_rate_pct: float, price: float) -> float:
    """
    Compute raw Kelly fraction for a binary prediction market bet.

    Args:
      win_rate_pct: historical/calibrated win probability (0-100)
      price: order price (0-1), e.g., 0.40

    Returns:
      Kelly fraction (uncapped), negative means no edge.
    """
    if price <= 0.01 or price >= 0.99:
        return 0.0
    p = win_rate_pct / 100.0
    b = (1.0 - price) / price  # net odds (bet 1, win b)
    f = p - (1.0 - p) / b       # Kelly formula
    return f


def compute_size(
    win_rate_pct: float,
    price: float,
    confidence: float,
    base_size: float,
    regime: str = "NORMAL",
) -> float:
    """
    Compute optimal bet size using fractional Kelly criterion.

    Args:
      win_rate_pct: calibrated win probability (0-100)
      price: order price (0-1)
      confidence: policy confidence score (0-100)
      base_size: default order size (from config DEFAULT_ORDER_SIZE)
      regime: market regime ('TREND'/'HIGH_VOL'/'CHOP'/etc.)

    Returns:
      Bet size in USDC (clamped to [0.5x, 3x] base_size)
    """
    f = kelly_fraction(win_rate_pct, price)
    if f <= 0:
        logger.debug("Kelly: no edge (f=%.4f win_rate=%.1f price=%.2f)", f, win_rate_pct, price)
        return base_size * KELLY_MIN_MULT

    frac_kelly = f * KELLY_FRACTION  # fractional Kelly

    # Scale relative to baseline: baseline_f corresponds to 1x base_size
    size_mult = frac_kelly / KELLY_BASELINE_F

    # Confidence adjustment: scale by confidence / target_confidence
    conf_adj = min(1.5, max(0.5, confidence / CONF_TARGET))
    size_mult *= conf_adj

    # Regime adjustment
    regime_adj = {
        "TREND": 1.10,
        "HIGH_VOL": 0.70,
        "MEAN_REVERT": 1.00,
        "CHOP": 0.60,
        "NORMAL": 1.00,
    }.get(regime, 1.0)
    size_mult *= regime_adj

    # Clamp
    size_mult = min(KELLY_MAX_MULT, max(KELLY_MIN_MULT, size_mult))
    final_size = round(base_size * size_mult, 2)

    logger.debug(
        "Kelly: f=%.4f frac=%.4f mult=%.2f conf_adj=%.2f regime=%s(%s) → size=%.2f",
        f, frac_kelly, size_mult / conf_adj / regime_adj,
        conf_adj, regime, regime_adj, final_size,
    )
    return final_size


def ev_per_unit(win_rate_pct: float, price: float, fee: float = 0.02, slippage: float = 0.005) -> float:
    """
    Expected value per unit size after fees and slippage.

    Returns profit/loss per $1 invested.
    """
    p = win_rate_pct / 100.0
    eff_price = min(0.99, price + slippage)
    payout = 1.0 / eff_price  # tokens per dollar
    net_win = payout * (1 - fee) - 1.0  # net after fee per dollar invested
    net_lose = -1.0
    return p * net_win + (1 - p) * net_lose
