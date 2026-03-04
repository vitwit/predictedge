"""
Momentum & Mean-Reversion analytics.
Analyzes intra-period price spikes and reversal rates.
"""
from db import get_connection


def get_momentum_stats(asset: str, interval: int) -> dict:
    """
    For price spikes in the first 30s, compute reversal rate.
    Buckets: >+20c, +15-20c, +10-15c, +5-10c, etc.
    """
    conn = get_connection()
    
    rows = conn.execute("""
        SELECT mr.winner_side, mr.open_up_price, mr.up_price_at_t30, mr.up_price_at_t60,
               mr.peak_up_price, mr.trough_after_peak, mr.close_up_price,
               mr.false_pump
        FROM market_resolutions mr
        WHERE mr.asset = ? AND mr.interval_minutes = ?
          AND mr.winner_side IS NOT NULL
          AND mr.open_up_price IS NOT NULL
          AND mr.up_price_at_t30 IS NOT NULL
    """, (asset, interval)).fetchall()
    conn.close()
    
    if not rows:
        return {"buckets": []}
    
    buckets = {
        ">+20c":     {"label": "> +20¢",  "min": 0.20, "max": 1.0,  "reversal": 0, "total": 0, "avg_next_move": []},
        "+15-20c":   {"label": "+15–20¢", "min": 0.15, "max": 0.20, "reversal": 0, "total": 0, "avg_next_move": []},
        "+10-15c":   {"label": "+10–15¢", "min": 0.10, "max": 0.15, "reversal": 0, "total": 0, "avg_next_move": []},
        "+5-10c":    {"label": "+5–10¢",  "min": 0.05, "max": 0.10, "reversal": 0, "total": 0, "avg_next_move": []},
        "-5-+5c":   {"label": "±5¢",      "min": -0.05,"max": 0.05, "reversal": 0, "total": 0, "avg_next_move": []},
        "-10--5c":   {"label": "−5–10¢",  "min": -0.10,"max": -0.05,"reversal": 0, "total": 0, "avg_next_move": []},
        "-20--10c":  {"label": "−10–20¢", "min": -0.20,"max": -0.10,"reversal": 0, "total": 0, "avg_next_move": []},
        "<-20c":     {"label": "< −20¢",  "min": -1.0, "max": -0.20,"reversal": 0, "total": 0, "avg_next_move": []},
    }
    
    for row in rows:
        winner, open_p, t30_p = row[0], row[1], row[2]
        close_p = row[6]
        
        if open_p is None or t30_p is None:
            continue
        
        delta_30s = t30_p - open_p
        
        for key, b in buckets.items():
            if b["min"] <= delta_30s < b["max"]:
                b["total"] += 1
                # Reversal: if spike was UP but closed DOWN (or vice versa)
                spike_dir = "UP" if delta_30s > 0 else "DOWN"
                if winner != spike_dir and abs(delta_30s) > 0.03:
                    b["reversal"] += 1
                if close_p and t30_p:
                    b["avg_next_move"].append(close_p - t30_p)
                break
    
    result_buckets = []
    for key, b in buckets.items():
        if b["total"] > 0:
            reversal_pct = round(b["reversal"] / b["total"] * 100, 1)
            avg_move = round(sum(b["avg_next_move"]) / len(b["avg_next_move"]), 4) if b["avg_next_move"] else 0
            signal = "⚡ STRONG FADE" if reversal_pct > 70 else ("⚡ FADE" if reversal_pct > 60 else ("● Moderate" if reversal_pct > 52 else "— Weak"))
            result_buckets.append({
                "label": b["label"],
                "delta_min": b["min"],
                "delta_max": b["max"],
                "total_samples": b["total"],
                "reversal_count": b["reversal"],
                "reversal_pct": reversal_pct,
                "avg_next_move": avg_move,
                "signal": signal,
            })
    
    return {
        "asset": asset,
        "interval": interval,
        "buckets": result_buckets,
    }


def get_peak_trough_heatmap(asset: str, interval: int) -> list:
    """
    Peak → Trough heatmap: false pump detector.
    Returns matrix of [peak_bucket x trough_bucket] with % resolved DOWN.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT peak_up_price, trough_after_peak, winner_side
        FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ?
          AND peak_up_price IS NOT NULL
          AND trough_after_peak IS NOT NULL
          AND winner_side IS NOT NULL
    """, (asset, interval)).fetchall()
    conn.close()
    
    peak_buckets = [0.50, 0.60, 0.70, 0.80, 0.90]
    trough_buckets = [0.30, 0.40, 0.50, 0.60, 0.70]
    
    matrix = {}
    
    for row in rows:
        peak, trough, winner = row
        if peak is None or trough is None:
            continue
        
        peak_b = None
        for pb in sorted(peak_buckets, reverse=True):
            if peak >= pb:
                peak_b = pb
                break
        
        trough_b = None
        for tb in sorted(trough_buckets, reverse=True):
            if trough >= tb:
                trough_b = tb
                break
        
        if peak_b is not None and trough_b is not None and peak_b > trough_b:
            key = (peak_b, trough_b)
            if key not in matrix:
                matrix[key] = {"total": 0, "down": 0}
            matrix[key]["total"] += 1
            if winner == "DOWN":
                matrix[key]["down"] += 1
    
    result = []
    for (peak_b, trough_b), counts in matrix.items():
        if counts["total"] >= 5:
            down_pct = round(counts["down"] / counts["total"] * 100, 1)
            result.append({
                "peak_min": peak_b,
                "trough_min": trough_b,
                "down_pct": down_pct,
                "sample_count": counts["total"],
                "signal": "STRONG" if down_pct > 70 else ("MODERATE" if down_pct > 55 else "WEAK"),
            })
    
    return sorted(result, key=lambda x: (-x["peak_min"], x["trough_min"]))


def get_early_period_stats(asset: str, interval: int) -> dict:
    """First-5s through first-60s price movement correlation with resolution."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT open_up_price, up_price_at_t5, up_price_at_t30, up_price_at_t60,
               winner_side, spot_at_t30, spot_open
        FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ?
          AND winner_side IS NOT NULL
          AND open_up_price IS NOT NULL
          AND up_price_at_t5 IS NOT NULL
    """, (asset, interval)).fetchall()
    conn.close()
    
    if not rows:
        return {}
    
    # Bucket by first-5s change
    buckets_5s = {
        ">+5c": {"min": 0.05, "up": 0, "down": 0, "total": 0},
        "+2-5c": {"min": 0.02, "max": 0.05, "up": 0, "down": 0, "total": 0},
        "flat": {"min": -0.02, "max": 0.02, "up": 0, "down": 0, "total": 0},
        "-2-5c": {"min": -0.05, "max": -0.02, "up": 0, "down": 0, "total": 0},
        "<-5c": {"max": -0.05, "up": 0, "down": 0, "total": 0},
    }
    
    t5_distribution = []
    t30_distribution = []
    
    for row in rows:
        open_p, t5, t30, t60, winner = row[0], row[1], row[2], row[3], row[4]
        if open_p is None or t5 is None:
            continue
        
        delta_5s = t5 - open_p
        
        bucket_label = None
        if delta_5s > 0.05:
            bucket_label = ">+5c"
        elif delta_5s > 0.02:
            bucket_label = "+2-5c"
        elif delta_5s >= -0.02:
            bucket_label = "flat"
        elif delta_5s >= -0.05:
            bucket_label = "-2-5c"
        else:
            bucket_label = "<-5c"
        
        if bucket_label and bucket_label in buckets_5s:
            b = buckets_5s[bucket_label]
            b["total"] += 1
            if winner == "UP":
                b["up"] += 1
            else:
                b["down"] += 1
        
        t5_distribution.append({"delta": round(delta_5s, 4), "winner": winner})
        if t30:
            t30_distribution.append({"delta": round(t30 - open_p, 4), "winner": winner})
    
    result_5s = []
    labels_order = [">+5c", "+2-5c", "flat", "-2-5c", "<-5c"]
    display_labels = {
        ">+5c": "> +5¢ (surge)",
        "+2-5c": "+2¢ to +5¢",
        "flat": "−2¢ to +2¢",
        "-2-5c": "−2¢ to −5¢",
        "<-5c": "< −5¢ (drop)",
    }
    
    for key in labels_order:
        b = buckets_5s[key]
        if b["total"] > 0:
            result_5s.append({
                "label": display_labels[key],
                "total": b["total"],
                "up_count": b["up"],
                "down_count": b["down"],
                "up_pct": round(b["up"] / b["total"] * 100, 1),
                "down_pct": round(b["down"] / b["total"] * 100, 1),
            })
    
    return {
        "asset": asset,
        "interval": interval,
        "first_5s_buckets": result_5s,
        "total_markets": len(rows),
    }
