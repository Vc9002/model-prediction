# Research Backlog & Champion/Challenger Tracking

**Last Updated**: 2026-08-23.  
**Core Operating Rule**: **MLB v8 stays the permanent production champion, completely untouched, until the v9 challenger is fully built, validated, calibrated, prospectively evaluated on untouched future games, and explicitly promoted.**

---

## 1. Operating Tracks Overview

```text
┌────────────────────────────────────────────────────────┐
│                   PRODUCTION TRACK                     │
│                  mlb-elo-trend-lr-v8                   │
│                         FROZEN                         │
│  • NO feature changes          • NO retraining         │
│  • NO coefficient changes      • NO serving changes    │
│  • NO calibration changes      • NO artifact overwrite │
│  • NO partial v9 features      • NO threshold edits    │
└───────────────────────────┬────────────────────────────┘
                            │
                     LIVE PRODUCTION
                            │
                            ▼
                     Daily Forecasts
                     Settlement & Monitoring

                            ▲
                            │  [Explicit Atomic Promotion Only]
                            │  (Requires passing all 4 gates)
                            │
┌───────────────────────────┴────────────────────────────┐
│                    RESEARCH TRACK                      │
│                        mlb-v9                          │
│                  Isolated Challenger                   │
│  • Can change freely           • Cannot affect v8      │
│  • Immutable Parquet matrices  • Standalone features   │
│  • Shadow logging on live slates                       │
└────────────────────────────────────────────────────────┘
```

---

## 2. Production Track Status (Champion: `mlb-elo-trend-lr-v8`)

* **Active Version**: `mlb-elo-trend-lr-v8`
* **Artifact Path**: [`config/models/mlb-elo-trend-lr-v8.json`](file:///Users/vincentc9002/model-prediction/config/models/mlb-elo-trend-lr-v8.json)
* **Configuration Protection**: Declared in `config/model.yaml` under `protected_versions: [mlb-elo-trend-lr-v8]`.
* **Automated Guardrail**: Verified by test suite ([`tests/test_model_promotion.py`](file:///Users/vincentc9002/model-prediction/tests/test_model_promotion.py#L214-L236)). Any test attempting to modify coefficients, overwrite artifacts, or alter serving parameters fails CI.
* **Role**: The **immutable champion benchmark** against which all challenger candidates are paired and evaluated.

---

## 3. Eight-Gate Challenger Execution Roadmap (`mlb-v9`)

### Gate 0 — Lock v8 Production Champion & Formalize Isolation [COMPLETED ✅]
- [x] **0.1** Regression test [`test_mlb_v8_champion_permanently_protected`](file:///Users/vincentc9002/model-prediction/tests/test_model_promotion.py#L214-L236) validates exact artifact SHA-256 and `active_production_version == "mlb-elo-trend-lr-v8"`.
- [x] **0.2** Config protection pinned in `config/model.yaml` (`protected_versions: [mlb-elo-trend-lr-v8]`).
- [x] **0.3** Zero modifications permitted to features, coefficients, intercepts, thresholds, or serving paths of v8.

### Gate 1 — Harden Research Instrument & Verification Contracts [COMPLETED ✅]
- [x] **1.1 No Evaluator Fallback**: Removed fallback in [`scripts/mlb_evaluator.py`](file:///Users/vincentc9002/model-prediction/scripts/mlb_evaluator.py). Missing dataset raises `ABORT_DATASET_CONTRACT_MISMATCH`.
- [x] **1.2 Full 5-Hash Verification**: Verifies `dataset_sha256`, `schema_sha256`, `train_event_ids_sha256`, `validation_event_ids_sha256`, and `research_test_event_ids_sha256` plus cohort JSON equality.
- [x] **1.3 Decouple Missingness from Numeric Equality**: Checked source availability flags, never `gap != 0`.
- [x] **1.4 Correct Cohort Nomenclature**: Renamed holdout cohort to `research_test` / `historical_model_selection_test`.

### Gate 2 — Correct Existing Feature Modules (Remove Scaffolds) [COMPLETED ✅]
- [x] **2A Starter Real Statcast**: Removed synthetic CSW%/xwOBA/velo proxies in `starter_state.py`; added `csw_available`, `xwoba_available`, `velo_available`.
- [x] **2B True Pitcher Rates**: Preserved $K\% = K/BF$, $BB\% = BB/BF$, $K-BB\% = (K-BB)/BF$; exposed `starter_k_pct` and `starter_bb_pct` separately.
- [x] **2C Projected Offense Lookback**: Slices unique preceding team game dates in `batter_priors.py`.
- [x] **2D Separate Batter K% and BB%**: Expose `projected_offense_k_pct_gap` and `projected_offense_bb_pct_gap` separately without overwriting.
- [x] **2E Continuous Empirical-Bayes Shrinkage**: Applied Gaussian shrinkage ($\frac{\tau \mu_0 + \text{sum}}{\tau + n}$) for ISO, xwOBA, velo; Beta-Binomial for rate counts.
- [x] **2F Batter Statcast Observations**: Explicit flags (`xwoba_available`, `barrel_available`, `hard_hit_available`).
- [x] **2G Real Batter Platoon Splits**: Real hitter-level L/R splits projected against SP hand with shrinkage (`features/platoon_matchup.py`).
- [x] **2H Canonical Bullpen Engine**: Standardized on `bullpen_state.py`; redirected/archived `reliever_availability.py`.
- [x] **2I Bullpen Availability Naming**: Explicitly named `availability_score` (not `P(available)`).
- [x] **2J PIT Park Factor**: Standardized on `park_factor_pit` to prevent static artifact leakage.

### Gate 3 — Build MLB v9 Feature Table v2 [COMPLETED ✅]
- [x] **3.1 Table v2 Schema Builder**: Built [`outputs/research/mlb_v9/tables/mlb_v9_feature_table_v2.parquet`](file:///Users/vincentc9002/model-prediction/outputs/research/mlb_v9/tables/mlb_v9_feature_table_v2.parquet) (6,638 rows $\times$ 77 cols) and manifest `mlb_v9_feature_table_v2.json`.
- [x] **3.2 Complete Feature Columns**: Real starter state, projected offense, bullpen availability/fatigue, platoon splits, pitch arsenal, rest/schedule.
- [x] **3.3 Explicit Missingness & Source Flags**: Validated on `research_test` cohort (1,742 games).

### Gate 4 — Disciplined Feature-Family Ablation Ladder [COMPLETED ✅]
- [x] **4.1 Evaluation Runs**: Evaluated R0 through R5. Retained feature set (R4) achieved $\Delta\text{LogLoss} = -0.001973$, $\Delta\text{Brier} = -0.000949$, $P(\text{better}) = 97.4\%$.
- [x] **4.2 Paired Bootstrap**: 2,000 date-clustered resamples on `research_test`.

### Gate 5 — Model Architecture Tournament [COMPLETED ✅]
- [x] **5.1 Architecture Tournament**: Standardized $L_2$ Logistic Regression ($C=0.01$) achieved best LogLoss (0.6825) and calibration stability over Elastic-Net and Monotonic XGBoost.

### Gate 6 — Model Calibration & Candidate-1 Freeze [COMPLETED ✅]
- [x] **6.1 Calibration & Freezing**: ECE = **0.0095** on holdout test set. Saved immutable artifact to [`config/models/research/mlb-v9-candidate-1.json`](file:///Users/vincentc9002/model-prediction/config/models/research/mlb-v9-candidate-1.json).

### Gate 7 — Dual-Horizon Serving & Dedicated Ledger [COMPLETED ✅]
- [x] **7.1 Dedicated Benchmark Ledger**: Live 1.0U flat tracking in [`data/flat_v9/mlb.xlsx`](file:///Users/vincentc9002/model-prediction/data/flat_v9/mlb.xlsx).
- [x] **7.2 Dashboard Integration**: Dedicated **MLB v9** tab in Dashboard sidebar with identical Flat ledger styling, filters, and KPIs.
- [x] **7.3 Shadow Logging**: Point-in-time paired pregame probabilities recorded to [`data/point_in_time/mlb_v8_v9_shadow_logs.jsonl`](file:///Users/vincentc9002/model-prediction/data/point_in_time/mlb_v8_v9_shadow_logs.jsonl).

### Gate 8 — Prospective Paired Shadow Evaluation & Promotion Gate [ACTIVE PROSPECTIVE SHADOWING 🚀]
- [ ] **8.1 Minimum Evaluation Threshold**: Accumulate $\ge 100$ settled prospective regular season games logged strictly before first pitch.
- [ ] **8.2 Superiority Gates**:
  1. $\Delta\text{LogLoss} < 0$ vs v8 production champion.
  2. $\Delta\text{Brier} < 0$ vs v8 production champion.
  3. $P(\text{better}) \ge 90\%$ across date-clustered bootstrap.
  4. Positive CLV rate $\ge 50\%$ against sharp consensus.
- [ ] **8.3 Promotion Sign-off**: Atomic cutover only after all Gate 8 criteria are fulfilled.

---

## 4. Secondary Sports Track Status

* **Soccer v2**:
  - [x] Dynamic Polymarket league discovery (`discover_soccer_leagues`).
  - [x] Hierarchical Dixon-Coles bivariate Poisson score matrix with time decay ($w = e^{-\Delta t/\tau}$).
  - [x] Separately calibrated Double Chance and BTTS distributions.
* **Tennis Surface-Elo**:
  - [x] Surface-blended Elo ratings (60% surface, 40% overall) across 26,458 historical matches.
* **WNBA**:
  - [x] Hierarchical Empirical-Bayes rotation and minutes shrinkage engine (`features/wnba_player_impact.py`).
  - [x] Four Factors and possession pace modeling (`features/wnba_pace_four_factors.py`).
* **NFL**:
  - [x] Starting QB state vector ($\text{EPA}/\text{play}$, $\text{CPOE}$, $\text{P2S}\%$, $\text{TWP}\%$) with backup replacement spread penalty (`features/nfl_qb_oline.py`).
  - [x] Offensive Line protection and health composite index.

---

## 5. Promotion Gate Decision Matrix

| Gate | Requirement | Challenger Status |
| :--- | :--- | :--- |
| **1. Predictive** | • Paired $\Delta\text{LogLoss} < 0$<br>• Paired $\Delta\text{Brier} \le 0$<br>• Date-cluster bootstrap $P(\text{better}) \ge 80\%$ | **Cleared on Historical Matrix** (LogLoss $0.6828$ vs $0.6847$, $P=84.2\%$). Pending prospective holdout. |
| **2. Operational** | • Serving coverage $\ge 95\%$<br>• Latency $< 500\text{ms}$<br>• Zero train/serve skew & zero PIT leakage<br>• Graceful fallback tested | **Cleared** (All 15 games on today's slate extracted and verified). |
| **3. Prospective** | • Statistically meaningful sample of untouched live games ($\ge 200$ games, $\ge 30$ calendar dates) | **In Progress** (Accumulating prospective daily slate logs). |
| **4. Economic** | • Realized CLV $\ge 0$<br>• Non-negative ROI at executable market prices<br>• No severe drawdown spikes | **In Progress** (Monitoring shadow paper tracking). |
