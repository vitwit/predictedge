"""
Seed historical data for analytics demo.
Generates realistic synthetic resolution history for BTC/ETH/SOL.
"""
import random
import time
import sqlite3
import math
from db import get_connection

random.seed(42)

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
INTERVALS = [5, 15, 60]

BASE_PRICES = {
    "BTC": 97000.0,
    "ETH": 3200.0,
    "SOL": 185.0,
    "XRP": 2.10,
}

SPOT_MOVES = {
    "BTC": {"5": 80, "15": 180, "60": 500},
    "ETH": {"5": 25, "15": 60, "60": 150},
    "SOL": {"5": 3, "15": 7, "60": 20},
    "XRP": {"5": 0.03, "15": 0.07, "60": 0.20},
}

def generate_market_history(n_days: int = 30) -> None:
    conn = get_connection()
    now = int(time.time())
    
    print(f"[seed] Generating {n_days} days of historical data...")
    
    for asset in ASSETS:
        base_price = BASE_PRICES[asset]
        
        for interval in INTERVALS:
            interval_seconds = interval * 60
            periods_per_day = (24 * 60) // interval
            n_periods = n_days * periods_per_day
            
            # Start from n_days ago
            start_time = now - (n_days * 24 * 3600)
            
            # Track running streak for realistic patterns
            streak = 0
            last_winner = None
            current_spot = base_price
            
            for i in range(n_periods):
                period_start = start_time + i * interval_seconds
                period_end = period_start + interval_seconds
                
                # Skip future periods
                if period_end > now - 300:
                    continue
                
                # Generate realistic market dynamics
                # Streak reversal tendency: after 4+ same direction, bias toward reversal
                reversal_bias = 0.0
                if abs(streak) >= 4:
                    reversal_bias = min(0.15, 0.05 * (abs(streak) - 3))
                
                # Hour-of-day bias (London session UP bias 07-12 UTC)
                hour = (period_start % 86400) // 3600
                hour_bias = 0.0
                if 7 <= hour <= 12:
                    hour_bias = 0.08
                elif 1 <= hour <= 5:
                    hour_bias = -0.04
                
                base_prob = 0.50 + hour_bias
                
                # Apply reversal bias
                if last_winner == "UP":
                    base_prob -= reversal_bias
                elif last_winner == "DOWN":
                    base_prob += reversal_bias
                
                # Determine winner
                winner = "UP" if random.random() < base_prob else "DOWN"
                
                # Generate spot price movement
                avg_move = SPOT_MOVES[asset][str(interval)]
                spot_change = random.gauss(
                    avg_move * 0.3 if winner == "UP" else -avg_move * 0.3,
                    avg_move
                )
                spot_open = current_spot
                spot_close = current_spot + spot_change
                spot_high = max(spot_open, spot_close) + abs(random.gauss(0, avg_move * 0.3))
                spot_low = min(spot_open, spot_close) - abs(random.gauss(0, avg_move * 0.3))
                current_spot = spot_close
                
                # Generate UP token price trajectory
                open_up = random.uniform(0.42, 0.58)
                if winner == "UP":
                    close_up = random.uniform(0.85, 0.99)
                    peak_up = random.uniform(close_up, min(0.99, close_up + 0.08))
                    trough_up = random.uniform(0.30, open_up)
                else:
                    close_up = random.uniform(0.01, 0.15)
                    peak_up = random.uniform(open_up, min(0.85, open_up + 0.20))
                    trough_up = random.uniform(0.01, close_up)
                
                trough_after_peak = trough_up if peak_up > open_up else None
                
                # Checkpoints
                def lerp(a, b, t, noise=0.03):
                    return a + (b - a) * t + random.gauss(0, noise)
                
                up_t5 = lerp(open_up, close_up, 5 / (interval * 60), 0.02)
                up_t30 = lerp(open_up, close_up, 30 / (interval * 60), 0.03)
                up_t60 = lerp(open_up, close_up, 60 / (interval * 60), 0.03)
                up_t120 = lerp(open_up, close_up, 120 / (interval * 60), 0.03)
                
                up_before60 = lerp(open_up, close_up, (interval * 60 - 60) / (interval * 60), 0.02)
                up_before30 = lerp(open_up, close_up, (interval * 60 - 30) / (interval * 60), 0.01)
                up_before10 = lerp(open_up, close_up, (interval * 60 - 10) / (interval * 60), 0.005)
                
                # Clamp prices
                def clamp(v):
                    return max(0.01, min(0.99, v))
                
                slug = f"{asset.lower()}-updown-{interval}m-{period_start}"
                
                # Labels
                first_30s_dir = "UP" if up_t30 > open_up else "DOWN"
                early_conviction = 1 if (winner == "UP" and up_t60 > 0.70) else 0
                false_pump = 1 if (peak_up > 0.75 and trough_after_peak and trough_after_peak < 0.60) else 0
                late_reversal = 1 if (
                    (up_before60 > 0.5 and winner == "DOWN") or 
                    (up_before60 < 0.5 and winner == "UP")
                ) else 0
                clean_res = 1 if (close_up > 0.90 or close_up < 0.10) else 0
                
                spread = random.uniform(0.01, 0.05)
                liq = random.uniform(500, 5000)
                
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO market_resolutions
                        (slug, asset, interval_minutes, start_ts, end_ts,
                         open_up_price, open_spot_price, open_spread,
                         close_up_price, close_spot_price,
                         peak_up_price, trough_up_price, trough_after_peak,
                         spot_open, spot_close, spot_high, spot_low,
                         spot_change_usd, spot_change_pct, spot_range_usd,
                         up_price_at_t5, up_price_at_t30, up_price_at_t60, up_price_at_t120,
                         up_price_before_60s, up_price_before_30s, up_price_before_10s,
                         open_liquidity_5c, close_liquidity_5c,
                         winner_side, resolved_at,
                         first_30s_direction, early_high_conviction, false_pump,
                         late_reversal, clean_resolution, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        slug, asset, interval, period_start, period_end,
                        clamp(open_up), spot_open, spread,
                        clamp(close_up), spot_close,
                        clamp(peak_up), clamp(trough_up),
                        clamp(trough_after_peak) if trough_after_peak else None,
                        spot_open, spot_close, spot_high, spot_low,
                        spot_change, round(spot_change / spot_open * 100, 4),
                        spot_high - spot_low,
                        clamp(up_t5), clamp(up_t30), clamp(up_t60), clamp(up_t120),
                        clamp(up_before60), clamp(up_before30), clamp(up_before10),
                        liq, liq * random.uniform(0.7, 1.3),
                        winner, period_end + random.randint(5, 30),
                        first_30s_dir, early_conviction, false_pump,
                        late_reversal, clean_res, period_start,
                    ))
                except Exception as e:
                    pass
                
                # Update streak
                if winner == last_winner:
                    streak = (streak + 1) if streak >= 0 else -(abs(streak) + 1)
                else:
                    streak = 1 if winner == "UP" else -1
                last_winner = winner
            
            count = conn.execute(
                "SELECT COUNT(*) FROM market_resolutions WHERE asset=? AND interval_minutes=?",
                (asset, interval)
            ).fetchone()[0]
            print(f"  {asset} {interval}m: {count} resolutions seeded")
    
    conn.commit()
    conn.close()
    print("[seed] Historical data seeding complete")


def seed_spot_prices(n_hours: int = 48):
    """Seed spot price history."""
    conn = get_connection()
    now = int(time.time())
    start = now - n_hours * 3600
    
    for asset in ASSETS:
        price = BASE_PRICES[asset]
        t = start
        while t <= now:
            change = random.gauss(0, price * 0.001)
            price += change
            price = max(price * 0.95, price)
            conn.execute(
                "INSERT INTO spot_prices (asset, price_usd, source, captured_at) VALUES (?,?,?,?)",
                (asset, round(price, 4), "seed", t),
            )
            t += 5
    
    conn.commit()
    conn.close()
    print(f"[seed] Spot prices seeded for {n_hours}h")


def seed_macro_events():
    """Seed sample macro events."""
    events = [
        ("fomc", "Fed holds rates at 5.25-5.50%", "negative", "high", '["BTC","ETH"]', 0.85),
        ("cpi", "CPI comes in at 3.2%, below consensus 3.5%", "positive", "medium", '["BTC","ETH","SOL"]', 0.78),
        ("nfp", "NFP adds 256k jobs, above 220k estimate", "negative", "medium", '["BTC"]', 0.65),
        ("etf_approval", "SEC approves spot BTC ETF options", "positive", "extreme", '["BTC"]', 0.92),
        ("exchange_hack", "Major DeFi protocol exploited for $150M", "negative", "high", '["ETH","SOL"]', 0.88),
        ("fomc", "FOMC minutes signal dovish pivot ahead", "positive", "high", '["BTC","ETH"]', 0.81),
        ("war_escalation", "Geopolitical tensions escalate in Middle East", "negative", "medium", '["BTC","ETH"]', 0.72),
    ]
    
    conn = get_connection()
    now = int(time.time())
    
    for i, (etype, headline, sentiment, magnitude, assets, confidence) in enumerate(events):
        occurred_at = now - (i * 86400 * 3) - random.randint(0, 86400)
        conn.execute("""
            INSERT INTO macro_events (event_type, headline, sentiment, magnitude, assets_affected, llm_confidence, source, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (etype, headline, sentiment, magnitude, assets, confidence, "cryptopanic", occurred_at))
    
    conn.commit()
    conn.close()
    print("[seed] Macro events seeded")


def run_all():
    generate_market_history(30)
    seed_spot_prices(48)
    seed_macro_events()
