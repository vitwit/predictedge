"""
Temporal intelligence: hour-of-day, day-of-week, session bias analytics.
"""
from datetime import datetime, timezone
from db import get_connection


SESSIONS = {
    "Asia": (0, 8),
    "London": (7, 12),
    "NY Morning": (13, 17),
    "NY Afternoon": (17, 21),
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def get_hourly_bias(asset: str, interval: int, lookback_days: int = 90) -> list:
    """Compute UP resolution rate by UTC hour."""
    conn = get_connection()
    import time
    cutoff = int(time.time()) - lookback_days * 86400
    
    rows = conn.execute("""
        SELECT start_ts, winner_side FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ?
          AND winner_side IS NOT NULL
          AND start_ts >= ?
        ORDER BY start_ts ASC
    """, (asset, interval, cutoff)).fetchall()
    conn.close()
    
    hourly = {h: {"up": 0, "total": 0} for h in range(24)}
    
    for ts, winner in rows:
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        hourly[hour]["total"] += 1
        if winner == "UP":
            hourly[hour]["up"] += 1
    
    result = []
    for h in range(24):
        d = hourly[h]
        up_rate = round(d["up"] / d["total"] * 100, 1) if d["total"] > 0 else None
        session = None
        for s_name, (s_start, s_end) in SESSIONS.items():
            if s_start <= h < s_end:
                session = s_name
                break
        
        result.append({
            "hour": h,
            "up_count": d["up"],
            "total": d["total"],
            "up_rate": up_rate,
            "session": session,
            "reliable": d["total"] >= 50,
        })
    
    return result


def get_day_of_week_bias(asset: str, interval: int, lookback_days: int = 90) -> list:
    """Compute UP resolution rate by day of week."""
    conn = get_connection()
    import time
    cutoff = int(time.time()) - lookback_days * 86400
    
    rows = conn.execute("""
        SELECT start_ts, winner_side FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ?
          AND winner_side IS NOT NULL
          AND start_ts >= ?
    """, (asset, interval, cutoff)).fetchall()
    conn.close()
    
    daily = {d: {"up": 0, "total": 0} for d in range(7)}
    
    for ts, winner in rows:
        dow = datetime.fromtimestamp(ts, tz=timezone.utc).weekday()
        daily[dow]["total"] += 1
        if winner == "UP":
            daily[dow]["up"] += 1
    
    return [
        {
            "day": DAY_NAMES[d],
            "day_index": d,
            "up_count": daily[d]["up"],
            "total": daily[d]["total"],
            "up_rate": round(daily[d]["up"] / daily[d]["total"] * 100, 1) if daily[d]["total"] > 0 else None,
        }
        for d in range(7)
    ]


def get_session_stats(asset: str, interval: int) -> list:
    """Compute UP resolution rate by trading session."""
    conn = get_connection()
    
    rows = conn.execute("""
        SELECT start_ts, winner_side FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ?
          AND winner_side IS NOT NULL
    """, (asset, interval)).fetchall()
    conn.close()
    
    session_data = {s: {"up": 0, "total": 0} for s in SESSIONS}
    
    for ts, winner in rows:
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        for s_name, (s_start, s_end) in SESSIONS.items():
            if s_start <= hour < s_end:
                session_data[s_name]["total"] += 1
                if winner == "UP":
                    session_data[s_name]["up"] += 1
                break
    
    descriptions = {
        "Asia": "Lower volume, retail-dominant, tracks Asian equity sentiment",
        "London": "Institutional flow, often trend-initiating",
        "NY Morning": "Highest volume, US retail and institutional, strongest moves",
        "NY Afternoon": "Tapering volume, profit-taking, mean-reverting tendencies",
    }
    
    return [
        {
            "session": s,
            "utc_hours": f"{SESSIONS[s][0]:02d}:00–{SESSIONS[s][1]:02d}:00",
            "up_count": session_data[s]["up"],
            "total": session_data[s]["total"],
            "up_rate": round(session_data[s]["up"] / session_data[s]["total"] * 100, 1) if session_data[s]["total"] > 0 else None,
            "description": descriptions.get(s, ""),
        }
        for s in SESSIONS
    ]


def get_time_remaining_probability(asset: str, interval: int) -> list:
    """
    2D matrix: UP_price_bucket × remaining_seconds_bucket → P(resolves UP)
    """
    conn = get_connection()
    
    rows = conn.execute("""
        SELECT up_price_before_60s, up_price_before_30s, up_price_before_10s,
               close_up_price, winner_side
        FROM market_resolutions
        WHERE asset = ? AND interval_minutes = ?
          AND winner_side IS NOT NULL
          AND up_price_before_60s IS NOT NULL
    """, (asset, interval)).fetchall()
    conn.close()
    
    price_buckets = [
        (0.90, 1.00, "> 0.90"),
        (0.80, 0.90, "0.80–0.90"),
        (0.70, 0.80, "0.70–0.80"),
        (0.60, 0.70, "0.60–0.70"),
        (0.50, 0.60, "0.50–0.60"),
        (0.40, 0.50, "0.40–0.50"),
        (0.30, 0.40, "0.30–0.40"),
        (0.00, 0.30, "< 0.30"),
    ]
    
    time_checkpoints = [
        ("60s", "up_price_before_60s"),
        ("30s", "up_price_before_30s"),
        ("10s", "up_price_before_10s"),
    ]
    
    results = []
    for p_min, p_max, p_label in price_buckets:
        row_data = {"price_bucket": p_label, "p_min": p_min}
        for t_label, col_idx in time_checkpoints:
            col = 0 if col_idx == "up_price_before_60s" else (1 if col_idx == "up_price_before_30s" else 2)
            matching = [r for r in rows if r[col] is not None and p_min <= r[col] < p_max]
            if matching:
                up_count = sum(1 for r in matching if r[4] == "UP")
                row_data[t_label] = {
                    "p_up": round(up_count / len(matching) * 100, 0),
                    "n": len(matching),
                }
            else:
                row_data[t_label] = {"p_up": None, "n": 0}
        results.append(row_data)
    
    return results
