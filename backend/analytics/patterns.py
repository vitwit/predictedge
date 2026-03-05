"""
Pattern Lab: scan resolution history for outcome sequence patterns.
"""
from db import get_connection
from itertools import product
from typing import Optional


def scan_pattern(asset: str, interval: int, pattern: list) -> dict:
    """
    Scan the resolution history for a given outcome sequence.
    pattern: list of 'UP' or 'DOWN' strings
    Returns: {occurrences, next_up_count, next_down_count, up_pct, down_pct, last_seen_ts}
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT winner_side, start_ts FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ? AND winner_side IS NOT NULL
        ORDER BY start_ts ASC
    """, (asset, interval)).fetchall()
    conn.close()
    
    if not rows:
        return {"error": "No data"}
    
    winners = [(r[0], r[1]) for r in rows]
    n = len(pattern)
    
    occurrences = 0
    next_up = 0
    next_down = 0
    last_seen = None
    
    for i in range(len(winners) - n):
        window = [winners[i + j][0] for j in range(n)]
        if window == pattern:
            occurrences += 1
            last_seen = winners[i + n - 1][1]
            if i + n < len(winners):
                next_outcome = winners[i + n][0]
                if next_outcome == "UP":
                    next_up += 1
                else:
                    next_down += 1
    
    total = next_up + next_down
    return {
        "asset": asset,
        "interval": interval,
        "pattern": pattern,
        "pattern_str": "→".join(pattern),
        "occurrences": occurrences,
        "next_up_count": next_up,
        "next_down_count": next_down,
        "up_pct": round(next_up / total * 100, 1) if total > 0 else 0,
        "down_pct": round(next_down / total * 100, 1) if total > 0 else 0,
        "sample_count": total,
        "last_seen_ts": last_seen,
    }


def get_pattern_matrix(asset: str, interval: int, seq_len: int = 3) -> list:
    """
    Generate all possible patterns of seq_len and their next-outcome distribution.
    Returns sorted by edge (deviation from 50%).
    """
    patterns = list(product(["UP", "DOWN"], repeat=seq_len))
    results = []
    
    for pattern in patterns:
        result = scan_pattern(asset, interval, list(pattern))
        if result.get("sample_count", 0) >= 10:
            edge = abs(result["up_pct"] - 50) if result.get("up_pct") else 0
            result["edge"] = round(edge, 1)
            results.append(result)
    
    return sorted(results, key=lambda x: -x["edge"])


def find_top_patterns(asset: str, interval: int, min_samples: int = 20) -> list:
    """Find the top predictive patterns by edge magnitude."""
    results = []
    for seq_len in [2, 3, 4, 5]:
        results.extend(get_pattern_matrix(asset, interval, seq_len))
    
    filtered = [r for r in results if r.get("sample_count", 0) >= min_samples]
    return sorted(filtered, key=lambda x: -x.get("edge", 0))[:20]


def _calc_order_pnl(order_size: float, order_price: float, won: bool) -> float:
    if order_price is None or order_price <= 0 or order_price >= 1:
        return 0.0
    if won:
        payout = order_size / order_price
        return payout - order_size
    return -order_size


def get_pattern_predictions_vs_reality(
    asset: Optional[str] = None,
    interval: Optional[int] = None,
    top_n: int = 10,
    recent_limit: int = 50,
) -> dict:
    """
    Compare auto-trader pattern predictions with final outcomes and PnL.
    Includes active/unresolved orders as "ACTIVE".
    """
    conn = get_connection()
    query = """
        SELECT
            o.slug,
            o.asset,
            o.interval_minutes,
            o.pattern_str,
            o.predicted_side,
            o.order_price,
            o.order_size,
            o.status,
            o.error,
            o.created_at,
            o.trigger_type,
            o.trigger_usd_move,
            mr.winner_side
        FROM auto_trade_orders o
        LEFT JOIN market_resolutions mr ON mr.slug = o.slug
        WHERE 1=1
    """
    params = []
    if asset:
        query += " AND o.asset = ?"
        params.append(asset.upper())
    if interval:
        query += " AND o.interval_minutes = ?"
        params.append(int(interval))
    query += " ORDER BY o.created_at DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    trades = []
    grouped = {}

    for r in rows:
        winner_side = r["winner_side"]
        order_status = (r["status"] or "").lower()
        predicted_side = r["predicted_side"]
        order_price = float(r["order_price"] or 0)
        order_size = float(r["order_size"] or 0)

        if order_status == "failed":
            reality = "FAILED"
            won = False
            pnl = 0.0
        elif winner_side in ("UP", "DOWN"):
            won = winner_side == predicted_side
            reality = "WIN" if won else "LOSS"
            pnl = _calc_order_pnl(order_size, order_price, won)
        else:
            won = False
            reality = "ACTIVE"
            pnl = 0.0

        trade = {
            "slug": r["slug"],
            "asset": r["asset"],
            "interval": r["interval_minutes"],
            "pattern_str": r["pattern_str"],
            "predicted_side": predicted_side,
            "winner_side": winner_side,
            "status": order_status or "unknown",
            "reality": reality,
            "order_price": order_price,
            "order_size": order_size,
            "pnl": round(pnl, 2),
            "created_at": r["created_at"],
            "error": r["error"],
            "trigger_type": r["trigger_type"] or "PATTERN",
            "trigger_usd_move": r["trigger_usd_move"],
        }
        trades.append(trade)

        key = (r["pattern_str"], predicted_side)
        if key not in grouped:
            grouped[key] = {
                "pattern_str": r["pattern_str"],
                "predicted_side": predicted_side,
                "total": 0,
                "resolved": 0,
                "wins": 0,
                "losses": 0,
                "active": 0,
                "failed": 0,
                "realized_pnl": 0.0,
                "last_trade_at": r["created_at"],
            }
        g = grouped[key]
        g["total"] += 1
        if r["created_at"] and r["created_at"] > g["last_trade_at"]:
            g["last_trade_at"] = r["created_at"]

        if reality == "WIN":
            g["resolved"] += 1
            g["wins"] += 1
            g["realized_pnl"] += pnl
        elif reality == "LOSS":
            g["resolved"] += 1
            g["losses"] += 1
            g["realized_pnl"] += pnl
        elif reality == "ACTIVE":
            g["active"] += 1
        elif reality == "FAILED":
            g["failed"] += 1

    top_patterns = []
    for g in grouped.values():
        resolved = g["resolved"]
        win_rate = round((g["wins"] / resolved) * 100, 1) if resolved > 0 else None
        item = {
            **g,
            "realized_pnl": round(g["realized_pnl"], 2),
            "win_rate": win_rate,
        }
        top_patterns.append(item)

    top_patterns = sorted(
        top_patterns,
        key=lambda x: (
            x["resolved"] > 0,
            x["realized_pnl"],
            x["wins"],
            x["total"],
            x["last_trade_at"] or 0,
        ),
        reverse=True,
    )[:max(1, int(top_n))]

    recent = trades[:max(1, int(recent_limit))]
    total_orders = len(trades)
    active_orders = sum(1 for t in trades if t["reality"] == "ACTIVE")
    resolved_orders = sum(1 for t in trades if t["reality"] in ("WIN", "LOSS"))
    wins = sum(1 for t in trades if t["reality"] == "WIN")
    losses = sum(1 for t in trades if t["reality"] == "LOSS")
    realized_pnl = round(sum(t["pnl"] for t in trades if t["reality"] in ("WIN", "LOSS")), 2)

    return {
        "summary": {
            "total_orders": total_orders,
            "active_orders": active_orders,
            "resolved_orders": resolved_orders,
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / resolved_orders) * 100, 1) if resolved_orders > 0 else None,
            "realized_pnl": realized_pnl,
        },
        "top_patterns": top_patterns,
        "recent_trades": recent,
    }
