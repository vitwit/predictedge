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
