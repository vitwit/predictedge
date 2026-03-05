"""
Strategy backtesting engine.
"""
import json
import time
from db import get_connection


def _is_valid_prob(x) -> bool:
    return x is not None and 0.0 < float(x) < 1.0


def backtest_streak_reversal(
    asset: str, interval: int, streak_n: int, direction: str,
    max_price: float, order_size: float = 25.0,
    date_from: int = None, date_to: int = None
) -> dict:
    """
    Backtest streak reversal strategy.
    Enter opposite side when N consecutive same-direction resolutions occur.
    """
    conn = get_connection()
    
    query = """
        SELECT winner_side, open_up_price, close_up_price, start_ts, slug
        FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ? AND winner_side IS NOT NULL
    """
    params = [asset, interval]
    
    if date_from:
        query += " AND start_ts >= ?"
        params.append(date_from)
    if date_to:
        query += " AND start_ts <= ?"
        params.append(date_to)
    
    query += " ORDER BY start_ts ASC"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    if not rows:
        return {"error": "No data"}
    
    trades = []
    equity = [order_size * 20]  # start with some capital for tracking
    streak = 0
    last_winner = None
    
    skipped_invalid_prices = 0

    for i, row in enumerate(rows):
        winner, open_p, close_p, ts, slug = row
        
        # Check if we should enter a trade (previous N periods were all `direction`)
        if streak >= streak_n and last_winner == direction:
            # Enter opposite side
            entry_side = "DOWN" if direction == "UP" else "UP"
            entry_price = open_p if entry_side == "UP" else (1.0 - open_p)

            if not _is_valid_prob(entry_price):
                skipped_invalid_prices += 1
                continue

            if entry_price <= max_price:
                # P&L: if we win, we get $1 per token; we paid entry_price per token
                # For simplicity: bet order_size, payout = order_size / entry_price if wins
                won = winner == entry_side
                payout = (order_size / entry_price) if won else 0
                pnl = payout - order_size
                close_side_price = close_p if entry_side == "UP" else (1.0 - close_p)
                
                trades.append({
                    "slug": slug,
                    "ts": ts,
                    "entry_side": entry_side,
                    "entry_price": round(entry_price, 4),
                    "close_price": round(close_side_price, 4) if close_side_price is not None else None,
                    "won": won,
                    "pnl": round(pnl, 2),
                    "streak_before": streak,
                    "trigger_direction": direction,
                })
                equity.append(equity[-1] + pnl)
        
        # Update streak
        if winner == last_winner:
            streak += 1
        else:
            streak = 1
        last_winner = winner
    
    if not trades:
        return {"error": "No trades triggered", "asset": asset, "interval": interval}
    
    total = len(trades)
    wins = sum(1 for t in trades if t["won"])
    total_pnl = sum(t["pnl"] for t in trades)
    win_rate = round(wins / total * 100, 1)
    avg_edge = round(total_pnl / total, 2)
    
    # Sharpe ratio (simplified)
    pnls = [t["pnl"] for t in trades]
    mean_pnl = total_pnl / total
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / total
    std_pnl = variance ** 0.5
    sharpe = round(mean_pnl / std_pnl * (252 ** 0.5), 2) if std_pnl > 0 else 0
    
    # Max drawdown
    max_dd = 0.0
    peak = equity[0]
    for eq in equity:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
    
    return {
        "asset": asset,
        "interval": interval,
        "strategy": "streak_reversal",
        "config": {
            "streak_n": streak_n,
            "direction": direction,
            "max_price": max_price,
            "order_size": order_size,
        },
        "quality": {
            "skipped_invalid_entry_prices": skipped_invalid_prices,
        },
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "avg_edge": avg_edge,
        "sharpe_ratio": sharpe,
        "max_drawdown": round(max_dd, 2),
        "equity_curve": equity[-50:],  # last 50 points
        "recent_trades": trades[-20:],
    }


def backtest_fade_pump(
    asset: str, interval: int,
    spike_threshold: float = 0.10,
    entry_side_price: float = 0.45,
    order_size: float = 25.0,
    date_from: int = None, date_to: int = None
) -> dict:
    """
    Backtest fade-the-pump strategy:
    When UP price spikes > spike_threshold in first 30s, buy DOWN.
    """
    conn = get_connection()
    
    rows = conn.execute("""
        SELECT winner_side, open_up_price, up_price_at_t30, close_up_price, start_ts, slug
        FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ?
          AND winner_side IS NOT NULL
          AND open_up_price IS NOT NULL
          AND up_price_at_t30 IS NOT NULL
        ORDER BY start_ts ASC
    """, (asset, interval)).fetchall()
    conn.close()
    
    trades = []
    equity = [order_size * 20]
    
    skipped_invalid_prices = 0

    for row in rows:
        winner, open_p, t30_p, close_p, ts, slug = row
        if open_p is None or t30_p is None:
            continue
        
        delta = t30_p - open_p
        
        if delta > spike_threshold:
            # Enter DOWN at ~45¢ (approximate)
            down_price = 1.0 - t30_p
            if not _is_valid_prob(down_price):
                skipped_invalid_prices += 1
                continue
            if down_price <= entry_side_price + 0.10:
                won = winner == "DOWN"
                payout = (order_size / down_price) if won else 0
                pnl = payout - order_size
                
                trades.append({
                    "slug": slug,
                    "ts": ts,
                    "entry_side": "DOWN",
                    "spike_delta": round(delta, 4),
                    "t30_price": round(t30_p, 4),
                    "down_price_entry": round(down_price, 4),
                    "won": won,
                    "pnl": round(pnl, 2),
                })
                equity.append(equity[-1] + pnl)
    
    if not trades:
        return {"error": "No trades triggered"}
    
    total = len(trades)
    wins = sum(1 for t in trades if t["won"])
    total_pnl = sum(t["pnl"] for t in trades)
    win_rate = round(wins / total * 100, 1)
    
    pnls = [t["pnl"] for t in trades]
    mean_pnl = total_pnl / total
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / total
    std_pnl = variance ** 0.5
    sharpe = round(mean_pnl / std_pnl * (252 ** 0.5), 2) if std_pnl > 0 else 0
    
    max_dd = 0.0
    peak = equity[0]
    for eq in equity:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
    
    return {
        "asset": asset,
        "interval": interval,
        "strategy": "fade_pump",
        "config": {
            "spike_threshold": spike_threshold,
            "entry_side_price": entry_side_price,
            "order_size": order_size,
        },
        "quality": {
            "skipped_invalid_entry_prices": skipped_invalid_prices,
        },
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "avg_edge": round(total_pnl / total, 2),
        "sharpe_ratio": sharpe,
        "max_drawdown": round(max_dd, 2),
        "equity_curve": equity[-50:],
        "recent_trades": trades[-20:],
    }
