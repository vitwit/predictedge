"""
Edge decay monitor.

Tracks rolling win-rate per signal type × asset × interval.
Auto-disables signals that show sustained underperformance.

Signal types: PATTERN, REVERSAL, HOTSPOT, IMPULSE, FVG, MOMENTUM
"""

import logging
import time
from typing import Dict, List, Optional

from db import get_connection
from config import config

logger = logging.getLogger(__name__)

WINDOW_N = config.EDGE_MONITOR_WINDOW       # rolling window size (50 trades)
MIN_WIN_RATE = config.EDGE_MONITOR_MIN_WIN_RATE / 100.0  # 0.45

SIGNAL_TYPES = ("PATTERN", "REVERSAL", "HOTSPOT", "IMPULSE", "FVG", "MOMENTUM")


def update_edge_stats(signal_type: str, asset: str, interval: int, won: bool, ev: float = 0.0):
    """
    Update rolling edge stats after a trade resolves.
    Called by auto_trader when a market resolves.
    """
    try:
        conn = get_connection()
        now = int(time.time())

        # Get current stats
        row = conn.execute(
            """
            SELECT win_count, loss_count, avg_ev, is_active
            FROM edge_stats
            WHERE signal_type = ? AND asset = ? AND interval_minutes = ?
            """,
            (signal_type, asset, interval),
        ).fetchone()

        if row:
            w = int(row["win_count"]) + (1 if won else 0)
            l = int(row["loss_count"]) + (0 if won else 1)
            total = w + l
            # Rolling window: only keep last WINDOW_N
            if total > WINDOW_N:
                excess = total - WINDOW_N
                if won:
                    w = max(0, w - excess // 2)
                    l = max(0, l - (excess - excess // 2))
                else:
                    l = max(0, l - excess)
            wr = w / max(1, w + l)
            avg_ev_new = (float(row["avg_ev"] or 0) * 0.9 + ev * 0.1)
            is_active = 1 if wr >= MIN_WIN_RATE or (w + l) < 20 else 0

            if not row["is_active"] and is_active:
                logger.info("[edge_monitor] Signal %s %s %sm RE-ACTIVATED (wr=%.1f%%)", signal_type, asset, interval, wr * 100)
            elif row["is_active"] and not is_active:
                logger.warning("[edge_monitor] Signal %s %s %sm AUTO-DISABLED (wr=%.1f%% < %.0f%%)", signal_type, asset, interval, wr * 100, MIN_WIN_RATE * 100)

            conn.execute(
                """
                UPDATE edge_stats
                SET win_count = ?, loss_count = ?, win_rate = ?, avg_ev = ?,
                    is_active = ?, last_updated = ?
                WHERE signal_type = ? AND asset = ? AND interval_minutes = ?
                """,
                (w, l, wr, avg_ev_new, is_active, now, signal_type, asset, interval),
            )
        else:
            # First record
            wr = 1.0 if won else 0.0
            conn.execute(
                """
                INSERT INTO edge_stats
                (signal_type, asset, interval_minutes, window_n,
                 win_count, loss_count, win_rate, avg_ev, is_active, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    signal_type, asset, interval, WINDOW_N,
                    1 if won else 0, 0 if won else 1,
                    wr, ev, now,
                ),
            )

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("[edge_monitor] update failed: %s", e)


def is_signal_active(signal_type: str, asset: str, interval: int) -> bool:
    """Returns True if the signal is currently active (not auto-disabled)."""
    try:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT is_active, win_count, loss_count
            FROM edge_stats
            WHERE signal_type = ? AND asset = ? AND interval_minutes = ?
            """,
            (signal_type, asset, interval),
        ).fetchone()
        conn.close()
        if row is None:
            return True  # No data yet — allow by default
        total = int(row["win_count"]) + int(row["loss_count"])
        if total < 15:
            return True  # Too few samples — don't disable yet
        return bool(row["is_active"])
    except Exception:
        return True  # Fail open — allow signal


def get_all_edge_stats() -> List[Dict]:
    """Return all edge stats for the dashboard."""
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT signal_type, asset, interval_minutes,
                   win_count, loss_count, win_rate, avg_ev, is_active, last_updated
            FROM edge_stats
            ORDER BY asset, interval_minutes, signal_type
            """
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("[edge_monitor] get_all failed: %s", e)
        return []


def sync_from_resolved_trades():
    """
    Backfill edge_stats from auto_trade_orders + market_resolutions.
    Call once on startup to initialize rolling stats from history.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT o.asset, o.interval_minutes, o.predicted_side,
                   o.trigger_type, o.created_at,
                   mr.winner_side
            FROM auto_trade_orders o
            LEFT JOIN market_resolutions mr ON mr.slug = o.slug
            WHERE o.status = 'submitted'
              AND mr.winner_side IS NOT NULL
            ORDER BY o.created_at ASC
            """
        ).fetchall()
        conn.close()

        for row in rows:
            signal_type = row["trigger_type"] or "PATTERN"
            asset = row["asset"]
            interval = int(row["interval_minutes"])
            won = row["predicted_side"] == row["winner_side"]
            update_edge_stats(signal_type, asset, interval, won)

        logger.info("[edge_monitor] synced %d resolved trades into edge_stats", len(rows))
    except Exception as e:
        logger.error("[edge_monitor] sync_from_resolved_trades failed: %s", e)
