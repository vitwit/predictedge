# PredictEdge Quant Signals Notebook

This document captures strategy ideas, signal hypotheses, and product use-cases for a professional prediction-market quant platform.  
Purpose: keep a single evolving source of truth we can review and implement incrementally.

---

## 1) Core Goal

Build a data-driven signal engine for short-horizon Polymarket crypto markets (`BTC/ETH/SOL/XRP`, `5m/15m/1h`) that:

- detects repeatable microstructure edges in live orderbooks,
- scores confidence and expected value (EV),
- routes high-conviction opportunities to the auto-trader with risk controls,
- explains _why_ a trade was taken (transparent quant reasoning).

---

## 2) Signal Families (Complete Brainstorm)

### A. Price Behavior Signals

- **Time-In-Zone (Hotspot Magnet)**
  - Example: YES price remains in `67c-72c` for `>=30s`.
  - Features: zone width, dwell time, number of re-tests, bounce count.
  - Hypothesis: long dwell near high-liquidity zones often implies directional bias into settlement.

- **Impulse Move**
  - Example: `>=20c` move in `<=5s`.
  - Labels: continuation, reversal, chop.
  - Features: move size, duration, pre-move spread, post-move depth imbalance.

- **Acceleration / Deceleration**
  - First derivative (velocity) and second derivative (acceleration) of midpoint.
  - Hypothesis: fast move + decaying depth is prone to snapback; fast move + persistent aggressive flow continues.

- **Breakout / Fakeout**
  - Break above prior local high (or below low), then hold/fail.
  - Features: hold duration above level, retest behavior, post-break spread compression.

- **VWAP Deviation Reversion**
  - Distance from short-window VWAP.
  - Hypothesis: extreme deviations revert unless accompanied by strong flow imbalance.

### B. Orderbook Structure Signals

- **Depth Imbalance**
  - `(bid_depth - ask_depth) / (bid_depth + ask_depth)` at multiple distance bands (1c/3c/5c/10c).
  - Hypothesis: persistent imbalance predicts next directional tick and sometimes market winner.

- **Liquidity Walls**
  - Large resting depth at specific prices ("walls").
  - Features: wall size, distance to midpoint, wall refresh/cancel rate.
  - Hypothesis: repeated failure to consume wall indicates reversal; wall pull then breakout indicates continuation.

- **Spread Regime**
  - Tight, normal, wide spread states.
  - Hypothesis: edge reliability differs by spread regime; wide spreads can create false momentum.

- **Microprice Drift**
  - Microprice (depth-weighted best quotes) vs midpoint divergence.
  - Hypothesis: microprice leads midpoint on short horizon.

- **Orderbook Slope / Curvature**
  - Shape of depth curve across levels.
  - Hypothesis: steep one-sided slope signals fragility and break risk.

### C. Flow / Event Signals

- **Trade Flow Imbalance**
  - Rolling net aggressive buys vs sells (if trade feed available).
  - If full trade prints unavailable: infer pressure via quote changes and midpoint jumps.

- **Cancel/Replace Pressure**
  - Orderbook updates showing high cancel rates near one side.
  - Hypothesis: spoof-like behavior increases reversal probability.

- **Quote Update Velocity**
  - Count orderbook updates per second.
  - Hypothesis: activity bursts around key levels can front-run large moves.

- **Gap Risk Events**
  - Sudden spread expansion + depth collapse.
  - Use-case: avoid entries and reduce size.

### D. Regime / Context Signals

- **Underlying Spot Volatility Regime**
  - Spot realized vol over rolling windows (e.g., 30s/2m/10m).
  - Hypothesis: some signals only work in low/high vol regimes.

- **Time-To-Resolution Regime**
  - Early, middle, late stages of each market window.
  - Hypothesis: late-stage behavior can be more deterministic but noisier.

- **Session / Clock Effects**
  - UTC hour/day effects (US/EU overlap, low liquidity hours).

- **Cross-Asset Confirmation**
  - BTC impulse with correlated ETH/SOL response increases confidence.

### E. Outcome Pattern Signals (existing + expanded)

- Existing streak-pattern signals can be enhanced with:
  - confidence intervals,
  - regime-conditioned win-rates,
  - sample-size penalties,
  - decayed weighting (recent performance > old).

### F. Meta Signals (Signal-on-Signal)

- **Signal Agreement Score**
  - Multiple independent signals align on same direction.

- **Signal Stability**
  - Direction unchanged for N seconds despite micro fluctuations.

- **Edge Decay Tracker**
  - Win-rate drift and EV drift over time; auto-disable stale signals.

---

## 3) Concrete Hypotheses to Test First

1. **Hotspot Hold**
   - If YES price remains in a 5c band for >=30s, does winner match side of dominant nearby depth?

2. **20c in 5s Shock**
   - Measure continuation vs reversal probabilities by asset/interval/time-left.

3. **Wall Failure**
   - If top wall is tested >=3 times in <=60s and not broken, does opposite side win more often?

4. **Spread + Imbalance Combo**
   - Is edge highest when spread is tight and imbalance persists >=10s?

5. **Late-Window Drift**
   - In final X% of market window, does microprice direction have elevated predictive power?

---

## 4) Data Capture Requirements (for a Quant-Grade Platform)

### Snapshot cadence

- Orderbook snapshots at **1s** (or event-driven + 1s normalization).
- Midpoint/mark + spread at same cadence.
- Spot price at 1s (or faster if available).

### Minimum fields per snapshot

- `ts`, `market_slug`, `condition_id`, `asset`, `interval_minutes`
- `best_bid`, `best_ask`, `midpoint`, `spread_cents`
- depth buckets (`bid_depth_1c/3c/5c/10c`, `ask_depth_1c/3c/5c/10c`)
- optional level arrays for top N levels
- derived: imbalance, microprice, velocity, acceleration, zone_id

### Outcome labeling

- resolved side (`UP/DOWN`)
- resolution timestamp
- pre-resolution feature windows (5s/15s/30s/60s before close)

---

## 5) Signal Scoring Framework

Each live signal should return:

- `direction`: `UP` / `DOWN` / `NO_TRADE`
- `confidence`: calibrated 0-100
- `expected_edge_bps`: EV estimate net of fees/slippage
- `sample_size`: historical N used for estimate
- `regime_fit`: quality score for current regime match
- `reason_codes`: human-readable explanations

### Suggested production score

`score = win_prob * payoff - (1 - win_prob) * loss - fees - slippage_penalty - uncertainty_penalty`

Only trade if:

- `score >= threshold`
- `sample_size >= min_samples`
- `confidence >= min_conf`
- risk checks pass

---

## 6) Risk & Execution Controls

- Max orders per market/window.
- Cooldown after entry.
- No trade in wide-spread/thin-depth conditions.
- Dynamic size: confidence-weighted with hard cap.
- Circuit breaker on consecutive losses / abnormal volatility.
- Kill switch for API errors / stale data / clock drift.

---

## 7) Product Use-Cases (Quant Dashboard)

### Live trader cockpit

- Live hotspot heatmap (price bands + dwell time).
- Impulse detector panel (last 20 shocks, outcome stats).
- Wall monitor (active walls, hit counts, break probabilities).
- Regime panel (volatility, spread state, liquidity quality).
- Signal feed with confidence + EV + reason codes.

### Research mode

- Hypothesis backtest runner by asset/interval/date.
- Distribution plots (edge by regime, confidence calibration).
- Feature importance & drift over time.
- Walk-forward validation report.

### Ops / Reliability

- Data freshness monitor.
- Missing snapshot detection.
- Endpoint health + latency.
- Auto-trader execution quality (fill quality, slippage, missed opportunities).

---

## 8) Recommended Implementation Roadmap

### Phase 1: Data Foundation

- Persist 1s orderbook + spot snapshots for active markets.
- Build feature materialization job.
- Add outcome labels for resolved markets.

### Phase 2: Research Engine

- Implement hypothesis tests for hotspot/impulse/walls.
- Generate win-rate + EV reports with confidence intervals.

### Phase 3: Live Signals

- Real-time signal evaluator with scoring + guardrails.
- Publish structured signal stream for UI + auto-trader.

### Phase 4: Dashboard Pro

- Add live quant panels and explainability widgets.
- Add strategy monitoring and edge-decay alerts.

### Phase 5: Continuous Learning

- Automated retraining / recalibration cadence.
- Feature drift and regime-shift adaptation.

---

## 9) Evaluation Standards (No Fake Edge)

- Use out-of-sample and walk-forward validation only.
- Track confidence intervals, not just point win-rate.
- Compare against naive baselines (always UP, random, momentum-only).
- Include realistic fees, slippage, and latency assumptions.
- Reject signals with low stability across regimes.

---

## 10) Open Questions to Revisit

- Best snapshot cadence tradeoff (1s vs sub-second).
- Availability and quality of trade prints vs orderbook-only inference.
- Market-specific calibration by interval (`5m` can differ heavily from `1h`).
- Portfolio allocation across simultaneous signals.
- Whether to model joint signals with lightweight ML after rule-based MVP.

---

## 11) Immediate Next Build Candidates

1. Hotspot detector (time-in-zone + dominant depth).
2. Impulse detector (20c/5s continuation vs reversal tables).
3. Wall interaction tracker.
4. Signal schema + scoring API for auto-trader ingestion.

---

This is intentionally broad and long-term.  
We can now prioritize a small set of high-ROI signals and implement them one by one with proper validation.

---

## 12) What Is Missing Today (Gap Analysis)

Current app is strong on descriptive analytics (streaks/patterns/history), but still weak on **execution-grade quant intelligence**.

### Missing in data layer

- No standardized **1s feature store** joining orderbook + spot + tick deltas per market window.
- No robust **quality flags** (missing ticks, stale quotes, outlier spreads, bad price points).
- No persistent **signal snapshots** (what signal fired, when, and under what context).

### Missing in signal engine

- No production **signal fusion** (pattern + orderbook + momentum + regime).
- No calibrated **probability model** (`p(win)` with confidence intervals).
- No **EV gate** after fees/slippage; current flow can trade on edge without net-EV checks.
- No explicit **NO_TRADE** state based on market quality filters.

### Missing in risk/execution

- No portfolio-level **exposure caps** across correlated assets/windows.
- No live **drawdown guardrails** and adaptive risk throttling.
- No execution-quality metrics (fill quality, slippage vs expected).

### Missing in dashboard (quant UX)

- No real-time **signal tape** with reason codes.
- No **hotspot/impulse/wall** live panels.
- No **regime dashboard** (volatility, spread state, liquidity quality).
- No **edge health** panel (signal decay, rolling win-rate, confidence calibration).
- No **strategy explainability** card for each live trade decision.

---

## 13) Prioritized TODO (Implement One by One)

Order below is optimized for fastest path to measurable profitability improvement.

### P0 - Foundation and trust (must-have first)

1. **Data quality guardrails**
   - Implement snapshot validity checks and drop/flag bad rows.
   - Success criteria:
     - <1% missing critical fields per day,
     - no invalid probabilities (`<=0` or `>=1`) in tradable entries.

2. **Signal event logging**
   - Create table for `signal_events` (inputs, score, decision, reason codes, ts).
   - Success criteria:
     - every order links to a signal event id,
     - every rejected decision has explicit reason codes.

3. **Decision policy skeleton**
   - Add deterministic middle-layer contract:
     - input signals -> score -> `APPROVE/REJECT` + reasons.
   - Success criteria:
     - auto-trader never bypasses this layer.

### P1 - First profitable signal bundle

4. **Hotspot detector**
   - Time-in-zone + dominant depth side + hold duration.
   - Success criteria:
     - report win-rate by zone regime with min sample threshold,
     - expose live hotspot confidence in UI.

5. **Impulse detector**
   - Detect `>=Xc in Ys`, classify continuation/reversal.
   - Success criteria:
     - per-asset continuation table with confidence bounds,
     - live impulse panel + cooldown policy.

6. **Spread + liquidity risk gate**
   - Reject entries when spread wide / depth too thin.
   - Success criteria:
     - measurable reduction in bad fills/slippage.

### P2 - Scale and robustness

7. **Regime classifier**
   - Label each window (`trend`, `mean_revert`, `chop`, `high_vol`).
   - Success criteria:
     - signal performance split by regime available in dashboard.

8. **Position sizing model**
   - Confidence- and liquidity-adjusted size with hard caps.
   - Success criteria:
     - reduced drawdowns at same or better Sharpe.

9. **Edge decay monitor**
   - Auto-disable signals with drift/instability.
   - Success criteria:
     - stale signal auto-pauses before prolonged underperformance.

### P3 - Pro quant UI and operations

10. **Live signal tape**
    - Shows score, EV, confidence, reason codes, decision.
11. **Execution quality panel**
    - expected vs realized fill price, slippage and miss rate.
12. **Attribution panel**
    - PnL by signal family, regime, asset, interval.

---

## 14) Immediate Build Queue (Next 3 sprints)

### Sprint 1 (fast win)

- Implement `signal_events` table + decision policy wrapper in auto-trader.
- Add spread/liquidity reject gate.
- Add signal decision logs to Pattern Lab section.

### Sprint 2

- Build hotspot detector + backtest report + live panel.
- Add impulse detector + continuation/reversion analytics.

### Sprint 3

- Add regime classifier and confidence calibration.
- Add portfolio-level risk controls and exposure caps.

---

## 17) Revised Architecture (Mar 2026 — Implementation Target)

### Signal Stack (7 layers, highest to lowest latency)

```
[1] CLOB Microprice Drift        → sub-second, live orderbook
[2] Hotspot Detector             → 30s dwell zone + dominant depth
[3] Impulse Detector             → ≥20c/5s continuation vs reversal
[4] Spot Momentum (calibrated)   → 30s/60s/120s spot move × P(UP|bin)
[5] Cross-Asset Confirmation     → 3+ assets aligned → confidence boost
[6] Fair Value Gap               → P(UP|spot_bin) vs CLOB midpoint
[7] USD Reversal (prev window)   → P(reversal|prev_usd_move bucket)
```

### Calibration Layer (Bayesian P(win))
- Historical lookup table: P(UP | asset, interval, spot_bin, hour_bucket)
- Trained on 29k+ resolved markets in market_resolutions table
- Combined with CLOB market consensus: P_final = 0.5*P_hist + 0.5*P_market
- Confidence interval: Wilson score with n ≥ 30 minimum

### EV Model (per trade)
```
EV = P(win) * (payout_if_win * (1 - fee) - size)
   + P(lose) * (-size)
fee = 0.02 (Polymarket 2%)
slippage = 0.005 per unit (estimated)
Trade only if: EV > 0 AND Kelly_size > 0 AND confidence > min_conf
```

### Kelly Position Sizing
```
b = (1 - price) / price          # net odds on UP at given price
f* = (win_rate * b - loss_rate) / b
size = base_size * (f* / 0.05) * 0.25 * (confidence / 70)
  clamp [0.5x, 3x] base_size
```

### Regime Conditioning
Signal reliability varies by market regime:
- TREND: Momentum signals strong, reversal signals weak
- HIGH_VOL: All signals less reliable, reduce size
- MEAN_REVERT: Reversal signals strong, momentum weak
- CHOP: Skip all pattern/momentum signals, use only extreme FVG
- NORMAL: All signals at baseline reliability

### OpenRouter LLM Gate (borderline cases only)
- Called ONLY when quantitative confidence is 42-62% AND EV > 0
- Model: claude-3-5-haiku (speed) or claude-3-7-sonnet (quality)
- Context: market state, all signal scores, pattern, regime, risk
- Returns: APPROVE/REJECT + reasoning (recorded in llm_decisions table)
- All high-confidence (>62%) and low-confidence (<42%) decisions bypass LLM

### Portfolio Risk Controls
- Max concurrent positions: 4 (one per asset max)
- Max capital at risk: 20% of estimated balance
- Consecutive loss circuit breaker: 5 losses → pause 30min
- Drawdown circuit breaker: 20% drawdown → pause until manual reset
- Kill switch: any API error streak → graceful stop with alert

---

## 16) Deep Signal Gap Analysis — Ground Truth from Data (Mar 2026)

### What the data actually tells us

After running analysis on real DB rows:

1. **open_up_price is mostly 0 or 1 (binary)** — only ~52 rows have a valid midpoint open price.
   This means we have *almost no usable opening market price data* from historical ingestion.
   The data we have is resolved-price (0 or 1), not the live market price at open.

2. **spot_change_pct has strong directional edge** (small sample, but clear):
   - spot up >0.3%: UP wins 84–100% of the time
   - spot down <-0.3%: UP wins 0% of the time
   - spot flat: UP wins ~50%
   This is the strongest raw signal in the DB right now.

3. **Cross-asset alignment is real** (2000 windows):
   - BTC/ETH same direction: 84% of windows
   - BTC/ETH/SOL all same: 75% of windows
   This is a very usable confirmation filter.

4. **Live price ticks exist** (~8000 ticks, 244 slugs).
   Good: we have elapsed/remaining seconds and up_price at each tick.
   Issue: ticks are all for recent markets and mostly near end-of-window (elapsed=257-301s).
   We need early-in-window ticks to build proper entry signals.

5. **Spot prices are fresh** (3s old, continuous feed ✓).
   This is the most reliable live signal input we have right now.

---

### The 3 highest-value signals we can build RIGHT NOW with existing data

#### Signal A: Spot Momentum Signal (immediate, data already available)
- Compute spot_change_pct over last 30s/60s/120s from spot_prices table.
- Cross-reference with historical win-rate for same spot_change_pct bin + asset + interval.
- Expected edge: massive when spot move > 0.3% (84%+ historical win rate).
- Can be live right now — spot feed is running.

#### Signal B: Cross-asset Direction Confirmation (immediate)
- Check current directional signal for all 4 assets.
- If 3+ agree: apply confirmation multiplier to confidence.
- If signals conflict: reduce size or skip.
- Historical base: 75-84% cross-asset agreement rate.

#### Signal C: Fair Value Gap (requires live market price from CLOB)
- Compute: what should the market price be given current spot movement?
- Formula: historical_win_rate(asset, interval, spot_chg_bin) → implied_fair_value
- If live_market_price < fair_value by > 5¢: BUY signal (market is under-pricing this outcome).
- If live_market_price > fair_value by > 5¢: skip or fade.
- This is the single highest-value signal for systematic profit.

---

### Why current signals are weak

The existing pattern-based signals (streak reversal etc.) have a structural problem:
- They look at *past outcomes* (UP/DOWN streaks) as predictors of next outcome.
- These are essentially backward-looking and treat the market as a random walk with autocorrelation.
- They are *not* using the actual information in the market price, spot price, or orderbook.

A market priced at 62¢ with spot up 0.5% and 2min remaining is *fundamentally different*
from a market priced at 62¢ at market open with flat spot. Current system treats them identically.

The missing piece: **current state** (live price + spot + time remaining) as primary signal,
with historical patterns as secondary confirmation only.

---

### Revised signal priority (what to build first)

| # | Signal | Data needed | Build effort | Expected impact |
|---|--------|-------------|--------------|-----------------|
| 1 | Spot momentum (spot_change vs win_rate) | spot_prices ✓ | Low | Very High |
| 2 | Cross-asset confirmation | spot_prices ✓ | Low | High |
| 3 | Fair value gap (market price vs spot-implied) | CLOB midpoint + spot | Medium | Very High |
| 4 | Time-weighted convergence (late-stage price) | price_ticks ✓ | Medium | High |
| 5 | Opening market price bias | Need better open price ingestion | High | High |
| 6 | Orderbook imbalance | CLOB book ✓ (decision_policy) | Medium | Medium |
| 7 | Pattern + regime conditioning | price_ticks partial | High | Medium |

---

## 15) Definition of Done for "Quant-Strong Dashboard"

Dashboard can be called quant-strong when all are true:

- live signals show `score`, `p(win)`, `EV`, `confidence`, and reason codes;
- every trade is attributable to a recorded signal decision;
- strategy has walk-forward validated edge net of fees/slippage;
- risk controls are active and visible (caps, drawdown guard, kill switch);
- edge decay monitoring can auto-disable degraded signals.
