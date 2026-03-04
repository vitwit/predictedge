"""
Streak analysis: consecutive resolution sequences per asset × interval.
"""
from db import get_connection


def get_current_streaks() -> list:
    """Compute the current streak (consecutive same direction) for each asset × interval."""
    conn = get_connection()
    results = []
    
    assets = conn.execute("SELECT DISTINCT asset FROM market_resolutions").fetchall()
    intervals = conn.execute("SELECT DISTINCT interval_minutes FROM market_resolutions").fetchall()
    
    for (asset,) in assets:
        for (interval,) in intervals:
            rows = conn.execute("""
                SELECT winner_side, start_ts FROM market_resolutions
                WHERE asset = ? AND interval_minutes = ? AND winner_side IS NOT NULL
                ORDER BY start_ts DESC LIMIT 20
            """, (asset, interval)).fetchall()
            
            if not rows:
                continue
            
            streak = 0
            direction = rows[0][0]
            
            for row in rows:
                if row[0] == direction:
                    streak += 1
                else:
                    break
            
            # Get last 10 outcomes for display
            last_10 = [r[0] for r in rows[:10]]
            last_10.reverse()
            
            results.append({
                "asset": asset,
                "interval": interval,
                "streak_length": streak,
                "direction": direction,
                "last_10": last_10,
                "last_ts": rows[0][1] if rows else None,
            })
    
    conn.close()
    return results


def get_streak_reversal_stats() -> list:
    """
    For each streak length N, compute the probability of reversal.
    Returns: [{streak_n, direction, reversal_pct, sample_count, asset, interval}]
    """
    conn = get_connection()
    results = []
    
    for asset in ["BTC", "ETH", "SOL", "XRP"]:
        for interval in [5, 15, 60]:
            rows = conn.execute("""
                SELECT winner_side, start_ts FROM market_resolutions
                WHERE asset = ? AND interval_minutes = ? AND winner_side IS NOT NULL
                ORDER BY start_ts ASC
            """, (asset, interval)).fetchall()
            
            if len(rows) < 10:
                continue
            
            winners = [r[0] for r in rows]
            
            # For each position, compute running streak length
            streak_outcomes = {}  # (direction, streak_len) -> [next_outcomes]
            
            streak_len = 1
            for i in range(1, len(winners)):
                if winners[i] == winners[i - 1]:
                    streak_len += 1
                else:
                    # Record outcome after this streak
                    key = (winners[i - 1], streak_len)
                    if key not in streak_outcomes:
                        streak_outcomes[key] = []
                    if i < len(winners):
                        streak_outcomes[key].append(winners[i])
                    streak_len = 1
            
            for (direction, n), next_outcomes in streak_outcomes.items():
                if len(next_outcomes) < 5:
                    continue
                
                reversals = sum(1 for o in next_outcomes if o != direction)
                reversal_pct = round(reversals / len(next_outcomes) * 100, 1)
                
                results.append({
                    "asset": asset,
                    "interval": interval,
                    "direction": direction,
                    "streak_n": n,
                    "reversal_pct": reversal_pct,
                    "sample_count": len(next_outcomes),
                    "continuation_pct": round(100 - reversal_pct, 1),
                })
    
    conn.close()
    return sorted(results, key=lambda x: (-x["streak_n"], -x["sample_count"]))


def get_resolution_history(asset: str, interval: int, limit: int = 50) -> list:
    """Get recent market history for an asset × interval (resolved + open)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT mr.slug,
               COALESCE(mr.winner_side, 'OPEN') AS winner_side,
               mr.open_up_price,
               COALESCE(
                   mr.close_up_price,
                   (SELECT pt.up_price
                    FROM price_ticks pt
                    WHERE pt.slug = mr.slug
                    ORDER BY pt.ticked_at DESC
                    LIMIT 1),
                   mr.open_up_price
               ) AS close_up_price,
               COALESCE(
                   mr.spot_change_usd,
                   CASE
                       WHEN mr.open_spot_price IS NOT NULL THEN (
                           (SELECT pt.spot_price
                            FROM price_ticks pt
                            WHERE pt.slug = mr.slug
                            ORDER BY pt.ticked_at DESC
                            LIMIT 1) - mr.open_spot_price
                       )
                       ELSE NULL
                   END
               ) AS spot_change_usd,
               mr.spot_change_pct,
               mr.start_ts,
               mr.end_ts,
               mr.clean_resolution,
               mr.false_pump,
               mr.late_reversal
        FROM market_resolutions
        AS mr
        WHERE mr.asset = ? AND mr.interval_minutes = ?
        ORDER BY mr.start_ts DESC
        LIMIT ?
    """, (asset, interval, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
