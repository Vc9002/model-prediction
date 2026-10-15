# MLB Offseason Rebuild Roadmap (2026–2027)

**Target Milestone: Opening Day 2027 (March 2027)**  
**Authoritative Control: `docs/ROADMAP.md` (Phases 23–24)**

---

## 1. Executive Context & Motivation

Throughout the 2026 regular season, MLB models presented a dangerous divergence between paper accounting and real-market execution:
1. **Paper Margin vs. Polymarket Truth**: In the local pick ledger at flat $-110$ odds, `mlb-elo-trend-lr-v8` logged $+34.25\text{U}$ ($58.5\%$ hit rate on $487$ picks). However, in realistic fee-aware order-book simulations on Polymarket US (`docs/INCUMBENT_COMPARISON_2026-09-09.md`), v8 produced **$-91.40$ ($-14.27\%$ ROI)** over $194$ quotes and **$-14.34$ ($-6.82\%$ ROI)** over $68$ quotes.
2. **Missing Calibration**: The v8 artifact carries no calibration sub-object, systematically overpredicting favorite win rates by $\sim 8\text{pp}$ (pricing $57\%$ actual occurrences at $\sim 65\%$). In exchange pricing with a $6\%$ taker fee coefficient, buying expensive favorite contracts resulted in persistent negative CLV.
3. **Data Gaps & Lookahead Leakage**: Older v8 features relied on a static $7,926$-game park factor table (2024–2026 retroactive averages) and unaligned Open-Meteo forecasts vs. historical weather.
4. **Postseason Breakdown**: During the October postseason (Division & Championship Series), starter IP dropped below $4.0$ innings, and manager high-leverage bullpen usage invalidated regular-season starter rolling stats (totals fell to $37.5\%$ win rate, $-6.78\text{U}$).

---

## 2. Milestone Summary: Rebuilt MLB v9 Candidate 2

As of October 2026, the challenger track was stabilized and rebuilt:
* **Dataset**: `mlb_v9_feature_table_v4.parquet` ($6,678$ games: $3,814$ train, $1,082$ val, $1,782$ test). Collinearity audit passed (condition number $5.25$, max VIF $5.59$).
* **Features ($14$ PIT Features)**:
  1. `elo_probability`
  2. `trend_gap`
  3. `park_factor_pit`
  4. `rest_disparity`
  5. `back_to_back_gap`
  6. `starter_k_pct_gap`
  7. `starter_bb_pct_gap`
  8. `starter_depth_gap`
  9. `projected_woba_gap`
  10. `projected_iso_gap`
  11. `projected_k_pct_gap`
  12. `projected_bb_pct_gap`
  13. `bullpen_fip_advantage`
  14. `bullpen_freshness_advantage`
* **Artifact**: `config/models/research/mlb-v9-candidate-2.json` (self-hash verified).
* **Calibration**: Platt scaling fit on validation split (slope $1.0416$, intercept $0.0$, ECE $0.0203$).
* **Out-of-Fold Performance on 1,782 Settled Test Games**:
  * LogLoss: $0.684007$ vs. v8's $0.685811$ ($\Delta = -0.001815$)
  * Brier Score: $0.245458$ vs. v8's $0.246333$ ($\Delta = -0.000879$)
  * Accuracy: $56.85\%$ vs. v8's $55.78\%$
  * Paired Date-Clustered Bootstrap ($2,000$ resamples): $P(\text{v9} > \text{v8}) = \mathbf{82.1\%}$.

---

## 3. Offseason 5-Phase Workstreams (Winter 2026 – Spring 2027)

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   MLB OFFSEASON ROADMAP (2026-2027)                    │
├───────────────────┬────────────────────────────────────────────────────┤
│ Phase 1 (Nov-Dec) │ Real Statcast Pitch/Batted Ball Ingestion Pipeline │
├───────────────────┼────────────────────────────────────────────────────┤
│ Phase 2 (Jan)     │ Next-Gen Architecture: Market-Residual v10         │
├───────────────────┼────────────────────────────────────────────────────┤
│ Phase 3 (Jan-Feb) │ Automated Calibration & Drift Sentry Integration   │
├───────────────────┼────────────────────────────────────────────────────┤
│ Phase 4 (Feb)     │ Postseason / Weather Regime Split Modeling         │
├───────────────────┼────────────────────────────────────────────────────┤
│ Phase 5 (Mar)     │ Spring Training 2027 Prospective Shadow & Gate     │
└───────────────────┴────────────────────────────────────────────────────┘
```

### Phase 1: Real Statcast PIT Ingestion Pipeline (Nov – Dec 2026)
* **Objective**: Ingest true pitch-level and batted-ball Statcast data through MLB StatsAPI without retroactive lookahead leakage.
* **Deliverables**:
  1. `data/statcast/pitcher_csw_rolling.parquet`: Rolling $250$-pitch CSW% (Called Strike + Whiff Rate), Stuff+, and Location+ per starter.
  2. `data/statcast/batter_xwoba_rolling.parquet`: Rolling $100$-PA expected weighted on-base average ($\text{xwOBA}$), barrel rate, and hard-hit percentage ($95+\text{ mph}$).
  3. Pre-game timestamp validation: Features must have observation timestamps strictly $\ge 30\text{ minutes}$ prior to official first pitch.

### Phase 2: Next-Gen Architecture: Market-Residual v10 (Jan 2027)
* **Objective**: Transition production moneyline and totals modeling from raw unconstrained logistic regressions to **Market-Residual Shrinkage** (`mlb-moneyline-market-residual-v10`).
* **Equation**:
  $$\text{logit}(P_{\text{true}}) = \text{logit}(P_{\text{market}}) + X \cdot \beta$$
* **Prior Evidence**: Offline tournament on real chronological games demonstrated that `M2_Residual_L2` beat both the market baseline $M0$ ($\Delta\text{LogLoss} = -0.0036$) and v8 incumbent ($\Delta\text{LogLoss} = -0.0173$).
* **Deliverables**:
  1. Freeze production weights for `MLBMarketResidualV10Model` (`src/model_prediction/models/mlb_market_residual_v10.py`).
  2. Implement market liquidity and depth checks (only bet when displayed ask depth $\ge \text{target order size}$).

### Phase 3: Automated Calibration & Drift Sentry (Jan – Feb 2027)
* **Objective**: Eliminate uncalibrated favorite drift before models touch capital.
* **Deliverables**:
  1. Wire `src/model_prediction/calibration_pipeline.py` to run weekly during the season.
  2. Automated Platt / Beta recalibration triggers when $\text{ECE} > 0.04$ or Brier score regresses by $>0.005$.
  3. Calibrator artifacts stored under `data/calibration/models/mlb-*.json` and checked for monotonic probability ordering.

### Phase 4: Postseason & Environmental Regime Separation (Feb 2027)
* **Objective**: Prevent postseason bullpen dynamics and extreme weather distortions from wrecking full-season models.
* **Deliverables**:
  1. Regular season vs. postseason flag in feature generation: Postseason weights cap starter depth at $4.0\text{ IP}$ and double bullpen leverage weights.
  2. Aerodynamic air density modeling: Combine real elevation, game-time barometric pressure, temperature, and humidity to estimate run environment scaling for totals.

### Phase 5: Spring Training 2027 Prospective Shadow & Promotion Gate (Mar 2027)
* **Objective**: Clear all four non-negotiable governance gates before Opening Day 2027.
* **Gating Criteria (`scripts/mlb_v9_promotion_gate.py`)**:
  1. **Gate 1 (Predictive)**: $\Delta\text{LogLoss} < 0$, $\Delta\text{Brier} \le 0$, $P(\text{better}) \ge 80\%$ on $2,000$ bootstrap resamples.
  2. **Gate 2 (Operational)**: $\ge 95\%$ serving coverage, latency $< 500\text{ ms}$, zero missingness crashes.
  3. **Gate 3 (Prospective)**: $\ge 200$ settled games across $\ge 30$ unique dates logged in `mlb_v8_v9_shadow_logs.jsonl` prior to live execution.
  4. **Gate 4 (Economic)**: Positive rolling CLV ($\ge +1.5\text{pp}$) and positive executable ROI after $6\%$ Polymarket taker fees.

---

## 4. Verification Checkpoint

```bash
# Verify v9 Candidate 2 artifact and self-hash integrity
pytest tests/test_mlb_v9_candidate_2.py -v

# Run the 4-gate promotion evaluator (shadow state)
python scripts/mlb_v9_promotion_gate.py --candidate mlb-v9-candidate-2
```
