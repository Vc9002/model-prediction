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

### Gate 3 — MLB v9 Feature Table Artifacts
- [x] **3.1 Table v1 (Immutable Standardized Research Control)**: [`outputs/research/mlb_v9/tables/mlb_v9_feature_table_v1.parquet`](file:///Users/vincentc9002/model-prediction/outputs/research/mlb_v9/tables/mlb_v9_feature_table_v1.parquet) (6,638 games: 3,814 train / 1,082 val / 1,742 test). Standardized 6-feature baseline: LogLoss **0.684707**, Brier **0.245772**, AUC **0.5700**. (PERMANENT CONTROL BASELINE ✅)
- [x] **3.2 Table v2 Manifest (QUARANTINED)**: [`outputs/research/mlb_v9/manifests/mlb_v9_feature_table_v2.json`](file:///Users/vincentc9002/model-prediction/outputs/research/mlb_v9/manifests/mlb_v9_feature_table_v2.json) classified as `VOID_SYNTHETIC_PROTOTYPE` due to deterministic proxy transformations.
- [ ] **3.3 Table v3 (Real Observed PIT Player-State)**: Build `mlb_v9_feature_table_v3.parquet` from verified Statcast game metrics, batter vs-hand PA records, canonical bullpen engine, and pitch arsenal summaries with full feature distribution integrity audit.

### Gate 4 — Disciplined Feature-Family Ablation Ladder (v3 Matrix)
- [ ] **4.1 R0 Control**: 6-feature standardized LR baseline on v3.
- [ ] **4.2 R1 Projected Offense**: Real batter priors with continuous Gaussian/Beta-Binomial shrinkage.
- [ ] **4.3 R2 Starter State**: Real CSW%, velo, xwOBA allowed, and expected depth.
- [ ] **4.4 R3 Canonical Bullpen State**: `PointInTimeBullpenEngine` talent × workload availability × leverage role.
- [ ] **4.5 R4 Platoon Matchups**: Real batter vs-hand splits with hierarchical shrinkage.
- [ ] **4.6 R5 Arsenal Summary**: Real pitch repertoire mix and entropy.
- [ ] **4.7 Paired Bootstrap**: 2,000 date-clustered resamples on holdout cohort.

### Gate 5 — Model Architecture Tournament & Freezing
- [ ] **5.1 Architecture Tournament**: Standardized $L_2$ LR vs Elastic-Net LR vs Monotonic XGBoost.
- [ ] **5.2 Calibration**: Out-of-fold Platt / Beta calibration.
- [x] **5.3 Candidate-1 Classification (QUARANTINED)**: [`config/models/research/mlb-v9-candidate-1.json`](file:///Users/vincentc9002/model-prediction/config/models/research/mlb-v9-candidate-1.json) classified as `VOID_INVALID_FEATURE_PROVENANCE` (preserved for audit evidence).
- [ ] **5.4 Candidate-2 Freeze**: Freeze `config/models/research/mlb-v9-candidate-2.json` with complete scaler, imputer, hashes, and fail-closed contract.

### Gate 6 — True Prospective Paired Shadow & Promotion Gating
- [ ] **6.1 Shadow Logging**: Real-time pregame logging (`observed_at < event_start`) of v8 champion vs frozen candidate-2 into append-only ledger.
- [ ] **6.2 Minimum Sample & MDE Gate**: Accumulate $\ge 200$ prospective games across $\ge 30$ unique dates.
- [ ] **6.3 4-Gate Promotion Evaluation**:
  1. $\Delta\text{LogLoss} < 0$ vs v8 ($P \ge 90\%$).
  2. $\Delta\text{Brier} \le 0$ vs v8.
  3. Clean operational serving ($\ge 95\%$ coverage, zero PIT violations).
  4. Positive CLV rate ($\ge 50\%$) against sharp consensus.
- [ ] **6.4 Atomic Promotion Cutover**: Switch `active_production_version` in `config/production.yaml` only upon passing all four gates.

---

## 4. Secondary Sports Track Status

* **Soccer v2**:
  - [x] Dynamic Polymarket league discovery (`discover_soccer_leagues`).
  - [x] Hierarchical Dixon-Coles bivariate Poisson score matrix with time decay ($w = e^{-\Delta t/\tau}$).
  - [x] Separately calibrated Double Chance, Draw No Bet, Clean Sheet, and BTTS distributions.
* **Tennis Surface-Elo**:
  - [x] Surface-blended Elo ratings (60% surface, 40% overall) across 26,458 historical matches.
* **WNBA**:
  - [x] Hierarchical Empirical-Bayes rotation and minutes shrinkage engine (`features/wnba_player_impact.py`).
  - [x] Four Factors and possession pace modeling (`features/wnba_pace_four_factors.py`).
  - [x] Parametric Normal-CDF derivative solver for totals and spreads.
* **NFL**:
  - [x] Starting QB state vector ($\text{EPA}/\text{play}$, $\text{CPOE}$, $\text{P2S}\%$, $\text{TWP}\%$) with backup replacement spread penalty (`features/nfl_qb_oline.py`).
  - [x] Offensive Line protection and health composite index.

---

## 5. Promotion Gate Decision Matrix

| Gate | Requirement | Challenger Status |
| :--- | :--- | :--- |
| **1. Predictive** | • Paired $\Delta\text{LogLoss} < 0$<br>• Paired $\Delta\text{Brier} \le 0$<br>• Date-cluster bootstrap $P(\text{better}) \ge 90\%$ | **Pending v3 Matrix & Candidate-2 Freeze** (v1 control baseline: LogLoss $0.6847$, Brier $0.2458$; candidate-1 voided). |
| **2. Operational** | • Serving coverage $\ge 95\%$<br>• Latency $< 500\text{ms}$<br>• Zero train/serve skew & zero PIT leakage<br>• Fail-closed contract enforced | **Active Infrastructure** (`PointInTimeBullpenEngine` canonicalized, fail-closed contracts live). |
| **3. Prospective** | • Statistically meaningful sample of untouched live games ($\ge 200$ games, $\ge 30$ calendar dates) | **Gated on Candidate-2 Freeze** (Mock shadow rows quarantined). |
| **4. Economic** | • Realized CLV $\ge 0$<br>• Non-negative ROI at executable market prices<br>• No severe drawdown spikes | **Gated on Candidate-2 Freeze**. |

