"""
Pattern Lab: scan resolution history for outcome sequence patterns.
"""
from db import get_connection
from itertools import product


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
