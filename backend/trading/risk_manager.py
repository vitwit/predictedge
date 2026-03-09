"""
Portfolio-level risk manager.

Tracks:
  - Open positions (asset → position info)
  - Realized PnL and consecutive losses
  - Drawdown from peak
  - Circuit breakers (consecutive loss / drawdown)

All state is persisted to the portfolio_state table so it
survives restarts.
"""

import json
import logging
import threading
import time
from typing import Dict, List, Optional

from db import get_connection
from config import config

logger = logging.getLogger(__name__)

MAX_CONCURRENT = config.MAX_CONCURRENT_POSITIONS
MAX_CAPITAL_PCT = config.MAX_CAPITAL_AT_RISK_PCT / 100.0
LOSS_LIMIT = config.CONSECUTIVE_LOSS_LIMIT
LOSS_PAUSE_S = config.CONSECUTIVE_LOSS_PAUSE_S
DRAWDOWN_LIMIT = config.DRAWDOWN_LIMIT_PCT / 100.0
BALANCE = config.ESTIMATED_BALANCE_USDC


class RiskManager:
    """Thread-safe portfolio risk manager."""

    def __init__(self):
        self._lock = threading.Lock()
        self._positions: Dict[str, Dict] = {}   # token_id → {slug, asset, size, price, placed_at}
        self._realized_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._peak_balance: float = BALANCE
        self._circuit_breaker_until: int = 0    # unix ts; 0 = no breaker
        self._total_invested: float = 0.0
        self._loaded = False
        self._load_state()

    # ── State persistence ─────────────────────────────────────────────────────

    def _load_state(self):
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT * FROM portfolio_state ORDER BY snapshot_at DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row:
                self._positions = json.loads(row["open_positions"] or "[]")
                if isinstance(self._positions, list):
                    self._positions = {}  # reset if old list format
                self._total_invested = float(row["total_invested"] or 0)
                self._realized_pnl = float(row["realized_pnl"] or 0)
                self._consecutive_losses = int(row["consecutive_losses"] or 0)
                self._peak_balance = float(row["peak_balance"] or BALANCE)
                self._circuit_breaker_until = int(row["circuit_breaker_until"] or 0)
                self._loaded = True
                logger.info(
                    "[risk] State loaded: positions=%d invested=%.2f pnl=%.2f streak=%d cb_until=%d",
                    len(self._positions), self._total_invested, self._realized_pnl,
                    self._consecutive_losses, self._circuit_breaker_until,
                )
        except Exception as e:
            logger.warning("[risk] State load failed (starting fresh): %s", e)

    def _save_state(self, notes: str = ""):
        try:
            conn = get_connection()
            now = int(time.time())
            current_balance = BALANCE + self._realized_pnl
            if current_balance > self._peak_balance:
                self._peak_balance = current_balance
            drawdown_pct = max(0.0, (self._peak_balance - current_balance) / max(self._peak_balance, 1)) * 100

            conn.execute(
                """
                INSERT INTO portfolio_state
                (snapshot_at, open_positions, total_invested, realized_pnl,
                 consecutive_losses, peak_balance, drawdown_pct, circuit_breaker_until, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    json.dumps(self._positions),
                    self._total_invested,
                    self._realized_pnl,
                    self._consecutive_losses,
                    self._peak_balance,
                    drawdown_pct,
                    self._circuit_breaker_until,
                    notes,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("[risk] State save failed: %s", e)

    # ── Circuit breaker ───────────────────────────────────────────────────────

    def _circuit_breaker_active(self) -> bool:
        return int(time.time()) < self._circuit_breaker_until

    def _activate_circuit_breaker(self, duration_s: int, reason: str):
        self._circuit_breaker_until = int(time.time()) + duration_s
        logger.warning(
            "[risk] CIRCUIT BREAKER ACTIVATED: %s (pause=%ds until %d)",
            reason, duration_s, self._circuit_breaker_until,
        )
        self._save_state(f"CB:{reason}")

    def reset_circuit_breaker(self):
        with self._lock:
            self._circuit_breaker_until = 0
            self._consecutive_losses = 0
            self._save_state("manual_reset")
            logger.info("[risk] Circuit breaker manually reset")

    # ── Open position tracking ────────────────────────────────────────────────

    def open_position(
        self,
        token_id: str,
        slug: str,
        asset: str,
        interval: int,
        size: float,
        price: float,
        signal_type: str = "PATTERN",
    ) -> bool:
        """
        Register a new open position.
        Returns True if accepted, False if blocked by risk limits.
        """
        with self._lock:
            reason = self._pre_trade_check(asset, size)
            if reason:
                logger.warning("[risk] Pre-trade rejected: %s", reason)
                return False

            self._positions[token_id] = {
                "slug": slug,
                "asset": asset,
                "interval": interval,
                "size": size,
                "price": price,
                "signal_type": signal_type,
                "placed_at": int(time.time()),
            }
            self._total_invested += size
            self._save_state(f"open:{asset}:{slug}")
            logger.info(
                "[risk] Position opened: %s %s size=%.2f total_invested=%.2f open=%d",
                asset, slug, size, self._total_invested, len(self._positions),
            )
            return True

    def close_position(
        self, token_id: str, won: bool, realized_pnl: float = 0.0
    ):
        """Register position closure and update P&L tracking."""
        with self._lock:
            pos = self._positions.pop(token_id, None)
            if pos:
                self._total_invested = max(0, self._total_invested - pos["size"])

            self._realized_pnl += realized_pnl

            if won:
                self._consecutive_losses = 0
                logger.info("[risk] Trade WON pnl=%.2f consecutive_losses reset", realized_pnl)
            else:
                self._consecutive_losses += 1
                logger.info(
                    "[risk] Trade LOST pnl=%.2f consecutive_losses=%d",
                    realized_pnl, self._consecutive_losses,
                )
                if self._consecutive_losses >= LOSS_LIMIT:
                    self._activate_circuit_breaker(
                        LOSS_PAUSE_S,
                        f"{self._consecutive_losses}_consecutive_losses",
                    )

            # Check drawdown
            current_balance = BALANCE + self._realized_pnl
            if current_balance > self._peak_balance:
                self._peak_balance = current_balance
            drawdown = (self._peak_balance - current_balance) / max(self._peak_balance, 1)
            if drawdown >= DRAWDOWN_LIMIT:
                self._activate_circuit_breaker(
                    86400,  # 24h — require manual reset
                    f"drawdown_{drawdown*100:.1f}pct_exceeds_{DRAWDOWN_LIMIT*100:.0f}pct_limit",
                )

            self._save_state("close:" + ("win" if won else "loss"))

    # ── Pre-trade checks ──────────────────────────────────────────────────────

    def _pre_trade_check(self, asset: str, size: float) -> Optional[str]:
        """Returns rejection reason string or None if OK."""
        if self._circuit_breaker_active():
            remaining = self._circuit_breaker_until - int(time.time())
            return f"CIRCUIT_BREAKER_ACTIVE:{remaining}s_remaining"

        if len(self._positions) >= MAX_CONCURRENT:
            return f"MAX_POSITIONS:{len(self._positions)}>={MAX_CONCURRENT}"

        # One position per asset
        for pos in self._positions.values():
            if pos["asset"] == asset:
                return f"ASSET_ALREADY_OPEN:{asset}"

        # Capital at risk check
        max_capital = BALANCE * MAX_CAPITAL_PCT
        if self._total_invested + size > max_capital:
            return f"CAPITAL_LIMIT:invested={self._total_invested:.1f}+{size:.1f}>{max_capital:.1f}"

        # Drawdown check
        current_balance = BALANCE + self._realized_pnl
        drawdown = (self._peak_balance - current_balance) / max(self._peak_balance, 1)
        if drawdown >= DRAWDOWN_LIMIT:
            return f"DRAWDOWN_LIMIT:{drawdown*100:.1f}%>={DRAWDOWN_LIMIT*100:.0f}%"

        return None

    def can_trade(self, asset: str, size: float) -> tuple:
        """Returns (allowed: bool, reason: str)."""
        with self._lock:
            reason = self._pre_trade_check(asset, size)
            return (reason is None), (reason or "OK")

    # ── Portfolio state ───────────────────────────────────────────────────────

    def get_state(self) -> Dict:
        with self._lock:
            current_balance = BALANCE + self._realized_pnl
            drawdown = max(0, (self._peak_balance - current_balance) / max(self._peak_balance, 1))
            return {
                "open_positions": list(self._positions.values()),
                "open_position_count": len(self._positions),
                "total_invested": round(self._total_invested, 2),
                "realized_pnl": round(self._realized_pnl, 2),
                "consecutive_losses": self._consecutive_losses,
                "peak_balance": round(self._peak_balance, 2),
                "current_balance": round(current_balance, 2),
                "drawdown_pct": round(drawdown * 100, 2),
                "circuit_breaker_active": self._circuit_breaker_active(),
                "circuit_breaker_until": self._circuit_breaker_until,
                "circuit_breaker_remaining_s": max(
                    0, self._circuit_breaker_until - int(time.time())
                ),
                "max_concurrent": MAX_CONCURRENT,
                "loss_limit": LOSS_LIMIT,
                "drawdown_limit_pct": DRAWDOWN_LIMIT * 100,
            }


# Singleton
_risk_manager = RiskManager()


def get_risk_manager() -> RiskManager:
    return _risk_manager
