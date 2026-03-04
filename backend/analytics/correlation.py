"""
Cross-asset correlation analytics.
"""
from db import get_connection


def get_asset_correlation_matrix(interval: int) -> dict:
    """Compute pairwise resolution correlation between assets."""
    conn = get_connection()
    assets = ["BTC", "ETH", "SOL", "XRP"]
    
    # Get aligned resolutions (same period start_ts)
    asset_data = {}
    for asset in assets:
        rows = conn.execute("""
            SELECT start_ts, winner_side FROM market_resolutions
            WHERE asset = ? AND interval_minutes = ? AND winner_side IS NOT NULL
            ORDER BY start_ts ASC
        """, (asset, interval)).fetchall()
        asset_data[asset] = {r[0]: r[1] for r in rows}
    
    conn.close()
    
    # Find common timestamps
    all_ts = set()
    for ts_map in asset_data.values():
        all_ts.update(ts_map.keys())
    
    matrix = {}
    for a1 in assets:
        matrix[a1] = {}
        for a2 in assets:
            if a1 == a2:
                matrix[a1][a2] = {"correlation": 1.0, "p_a2_up_given_a1_up": 100.0, "n": 0}
                continue
            
            aligned = [
                ts for ts in all_ts
                if ts in asset_data[a1] and ts in asset_data[a2]
            ]
            
            if len(aligned) < 10:
                matrix[a1][a2] = {"correlation": None, "p_a2_up_given_a1_up": None, "n": 0}
                continue
            
            v1 = [1 if asset_data[a1][ts] == "UP" else 0 for ts in aligned]
            v2 = [1 if asset_data[a2][ts] == "UP" else 0 for ts in aligned]
            
            # Pearson correlation
            n = len(v1)
            mean1 = sum(v1) / n
            mean2 = sum(v2) / n
            
            cov = sum((v1[i] - mean1) * (v2[i] - mean2) for i in range(n)) / n
            std1 = (sum((x - mean1) ** 2 for x in v1) / n) ** 0.5
            std2 = (sum((x - mean2) ** 2 for x in v2) / n) ** 0.5
            
            corr = round(cov / (std1 * std2), 3) if std1 > 0 and std2 > 0 else 0
            
            # P(A2 UP | A1 UP)
            a1_up = [ts for ts in aligned if asset_data[a1][ts] == "UP"]
            both_up = [ts for ts in a1_up if asset_data[a2][ts] == "UP"]
            p_a2_up = round(len(both_up) / len(a1_up) * 100, 1) if a1_up else None
            
            matrix[a1][a2] = {
                "correlation": corr,
                "p_a2_up_given_a1_up": p_a2_up,
                "n": len(aligned),
            }
    
    return {"assets": assets, "interval": interval, "matrix": matrix}


def get_spot_correlation_stats(asset: str, interval: int) -> dict:
    """Analyze how spot price movement correlates with resolution."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT spot_change_usd, winner_side, spot_change_pct
        FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ?
          AND winner_side IS NOT NULL
          AND spot_change_usd IS NOT NULL
        ORDER BY start_ts ASC
    """, (asset, interval)).fetchall()
    conn.close()
    
    buckets = [
        (300, float('inf'), "> +$300"),
        (200, 300, "$200–$300"),
        (100, 200, "$100–$200"),
        (50, 100, "$50–$100"),
        (0, 50, "$0–$50"),
        (-50, 0, "−$50–$0"),
        (-100, -50, "−$100–−$50"),
        (-200, -100, "−$200–−$100"),
        (float('-inf'), -200, "< −$200"),
    ]
    
    result = []
    for b_min, b_max, label in buckets:
        matching = [r for r in rows if b_min <= r[0] < b_max]
        if matching:
            up_count = sum(1 for r in matching if r[1] == "UP")
            result.append({
                "label": label,
                "total": len(matching),
                "up_count": up_count,
                "down_count": len(matching) - up_count,
                "up_pct_of_ups": None,  # would need total UP count
                "down_pct_of_downs": None,
                "up_resolution_rate": round(up_count / len(matching) * 100, 1),
            })
    
    # Add percentage of all UP / DOWN resolutions
    total_up = sum(1 for r in rows if r[1] == "UP")
    total_down = sum(1 for r in rows if r[1] == "DOWN")
    
    for entry in result:
        entry["up_pct_of_all_ups"] = round(entry["up_count"] / total_up * 100, 1) if total_up > 0 else 0
        entry["down_pct_of_all_downs"] = round(entry["down_count"] / total_down * 100, 1) if total_down > 0 else 0
    
    return {
        "asset": asset,
        "interval": interval,
        "total_markets": len(rows),
        "buckets": result,
    }
