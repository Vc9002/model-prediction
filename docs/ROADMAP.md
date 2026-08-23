# Roadmap & Operating Architecture

**Recompiled 2026-08-23.** MLB v8 remains the permanent production champion, completely untouched, until the complete v9 candidate is fully built, validated, calibrated, prospectively evaluated on untouched future games, and explicitly promoted.

---

## Core Rule: Two Completely Separate Tracks

Treat MLB as two completely separate tracks:

```text
PRODUCTION TRACK
mlb-elo-trend-lr-v8
FROZEN
NO feature changes
NO coefficient changes
NO retraining
NO calibration changes
NO serving changes
NO artifact overwrite
NO partial v9 features

RESEARCH TRACK
mlb-v9
isolated challenger
can change freely
cannot affect v8
```

* Nothing from v9 enters v8 incrementally.
* Even if a v9 feature looks excellent, do **not** add it to v8.
* It remains part of the challenger until the complete v9 candidate clears the entire promotion process.

---

# Revised Project Roadmap

## Phase 0 — Freeze v8 Permanently

The current production model remains:

```yaml
active_production_version: mlb-elo-trend-lr-v8
production_artifact: config/models/mlb-elo-trend-lr-v8.json
protected_versions:
  - mlb-elo-trend-lr-v8
```

The v8 artifact is treated as immutable.
Tests fail if research code attempts to:
- Overwrite the artifact `config/models/mlb-elo-trend-lr-v8.json`.
- Regenerate coefficients under the same version.
- Modify its feature list (`elo_probability`, `trend_gap`, `park_factor`, `weather_factor`, `starter_era_gap`, `bullpen_weakness_gap`).
- Change its calibrator or threshold.
- Change its serving definitions.
- Silently point `active_production_version` elsewhere.

### v8's Only Job Now
It is the **champion benchmark**. Every v9 result gets compared against:
```text
v8 predictive performance
v8 calibration
v8 production coverage
v8 prospective performance
```
Do not spend research time trying to make v8 better.

---

## Phase 1 — Clean v9 Research Lineage

```text
main
 ├── production: v8 (frozen)
 │
 └── research/mlb-v9
       challenger development only
```

- Preserve old branch/tag `archive/mlb-v8-reproduction-20260823`.
- Do **not** merge v9 changes into production merely because a single component passes.
- Keep active challenger work isolated until it stabilizes and completes prospective evaluation.

---

## Phase 2 — Repair the v9 Evaluation System

Before adding another feature, make v9 experiments immutable:
- Evaluator consumes `outputs/research/mlb_v9/tables/mlb_v9_feature_table_vN.parquet` directly.
- The feature-building process happens once:
  $$\text{Raw Historical Data} \longrightarrow \text{PIT Feature Builder} \longrightarrow \text{Immutable Parquet} \longrightarrow \text{SHA-256 Hash Manifest}$$
- All model comparisons use that exact matrix.

### Versioned Research Layout
```text
outputs/research/mlb_v9/
    tables/
        mlb_v9_feature_table_v1.parquet
        mlb_v9_feature_table_v2.parquet
    manifests/
        mlb_v9_feature_table_v1.json
        mlb_v9_feature_table_v2.json
    cohorts/
        development_train.json
        development_validation.json
        development_test.json
    evaluations/
        ...
```
*Never rewrite old tables.*

---

## Phase 3 — Define v9's Development/Test Structure

The old v8 holdout has already been inspected repeatedly and is now **development evidence for v9**, not the final untouched v9 test.

```text
LEVEL 1: Historical v9 development (Train split: 3,814 games)
LEVEL 2: Historical model-selection test / research comparison (Validation + Research Test: 2,824 games)
LEVEL 3: Prospective untouched evaluation (Future live games logged before first pitch)
```

The final promotion decision relies heavily on Level 3. Prospective evaluation is critical because confirmed lineups, late scratches, and real-time injuries cannot honestly be reconstructed historically.

---

## Phase 4 — Repair K-BB (Inside v9 Only)

- v8 continues using its historical starter ERA definition without changes.
- Inside v9, the legacy $(K-BB)/IP$ metric is marked:
  ```text
  starter_kbb_per_ip_legacy: VERDICT = VOID (reason: invalid feature definition)
  ```
- v9 defines true rates over Batters Faced ($BF$):
  $$K\% = \frac{K}{BF}, \quad BB\% = \frac{BB}{BF}, \quad K-BB\% = \frac{K-BB}{BF}$$
- v9 tests starter ERA vs true K-BB% vs joint ($K\% + BB\%$).

---

## Phase 5 — Build the Real v9 Information Layer

Do not define v9 as "v8 plus one better feature." Define it as a **new player-state model**:
```text
1. TEAM STRENGTH
2. OFFENSE (Empirical-Bayes batter priors, projected lineup quality)
3. STARTING PITCHER (K%, BB%, FIP, expected IP depth, recent-vs-long-term residual)
4. BULLPEN (Reliever talent × availability decay × leverage role)
5. ENVIRONMENT (Park factors PIT, weather vector, schedule rest disparity)
6. MATCHUP INTERACTIONS (Pitcher arsenal × hitter pitch-type profile, platoon splits)
7. LINEUP STATE (Confirmed vs projected batting orders)
```

---

## Phase 6 — PIT Batter Priors

Create batter state **as of game time** using Empirical-Bayes shrinkage toward positional/league priors:
$$\hat{p}_K = \frac{\tau p_{\text{prior}} + K}{\tau + PA}, \quad \hat{p}_{BB} = \frac{\tau p_{\text{prior}} + BB}{\tau + PA}, \quad \hat{p}_{HR} = \frac{\tau p_{\text{prior}} + HR}{\tau + PA}$$
Tracks: PA, K%, BB%, K-BB%, HR/PA, ISO, wOBA, xwOBA, barrel%, hard-hit%, exit velocity, platoon splits, and sample strength.

---

## Phase 7 — Historical Projected Lineup

- For historical games, do not use the target game's final batting order.
- Infer expected participants from pregame signals only ($P(\text{player starts})$ based on starts last 5/10/20, PA last 7/14 days, rest days, active roster status).
$$\text{ProjectedTeamOffense} = \sum_i P_i(\text{start}) \times \text{Talent}_i \times \text{PAWeight}_i$$
- Game-level signals: `projected_offense_quality_gap`, `projected_k_pct_gap`, `projected_bb_pct_gap`, `projected_power_gap`.

---

## Phase 8 — Test Projected Offense

- Ablation ladder evaluated on immutable feature matrix:
  $$\text{v9 Baseline} \longrightarrow +\text{Offense Quality} \longrightarrow +\text{Offense K/BB} \longrightarrow +\text{Offense Power} \longrightarrow +\text{Combined Offense}$$
- Decision metrics: LogLoss, Brier, calibration, date-cluster bootstrap, feature availability, and monthly stability.

---

## Phase 9 — Starter-State Vector v9

Build comprehensive starter profile:
- Talent: K%, BB%, K-BB%, xwOBA allowed, FIP, pitch velocity, CSW%, first-pitch strike%.
- Current Form vs Long-Term: $\text{RecentResidual} = \text{RecentK\%} - \text{ShrunkLongTermK\%}$.
- Handedness platoon splits.

---

## Phase 10 — Expected Starter Innings

Model expected starter depth independently:
- Inputs: season IP/start, recent IP/start, recent pitch counts, rest days, workload trend.
- Derives $\text{ExpectedStarterIP}$ to properly balance starter vs bullpen contribution:
$$\text{GamePitchingQuality} = \text{SPQuality} \times \text{SPExpectedIP} + \text{BullpenQuality} \times (9.0 - \text{SPExpectedIP})$$

---

## Phase 11 — Real Bullpen State (Talent $\times$ Availability $\times$ Role)

For each reliever in the bullpen:
- **Talent ($Q_r$)**: K%, BB%, K-BB%, FIP, xwOBA allowed, CSW%.
- **Workload Availability ($A_r$)**: pitches $1d, 2d, 3d$, appearances last 3 days, consecutive days, rest.
- **Role Importance ($R_r$)**: high-leverage share, late-inning leverage index, closer/setup role.
$$Q_{BP} = \frac{\sum_r Q_r A_r R_r}{\sum_r A_r R_r}$$
Derives `effective_bullpen_quality`, `bullpen_availability_pressure`, `high_leverage_arms_available`.

---

## Phase 12 — BALLDONTLIE Data Acquisition Pipeline

- BALLDONTLIE remains strictly outside the live v8 serving path.
$$\text{BALLDONTLIE} \longrightarrow \text{Raw Provider Capture} \longrightarrow \text{Immutable PIT Storage} \longrightarrow \text{Normalization} \longrightarrow \text{Entity Crosswalk} \longrightarrow \text{v9 Research Features}$$
- Never call BALLDONTLIE inside v8 live serving.
- Endpoints: plate appearances, hitter pitch-type stats, pitcher pitch-type stats, injuries.

---

## Phase 13 — MLB Player Identity Registry

Build `data/entities/mlb_players.json`:
- Crosswalk: `canonical_player_id` $\longleftrightarrow$ `mlb_statsapi_id`, `espn_id`, `balldontlie_id`, name, DOB, bats, throws.
- Resolution hierarchy: stable ID $\to$ verified crosswalk $\to$ name + DOB/team $\to$ manual review. Fail closed (`NO_MATCH`) on ambiguity.

---

## Phase 14 — Pitch Arsenal Matchup Signal

Match pitcher repertoire usage against projected lineup pitch-type vulnerabilities:
$$\text{MatchupQuality} = \sum_{\text{pitch}} \text{Usage}_{\text{pitcher},\text{pitch}} \times \text{HitterSkill}_{\text{lineup},\text{pitch}}$$
Shrink small sample pitch types heavily. Returns `arsenal_matchup_quality` and `arsenal_matchup_sample_strength`.

---

## Phase 15 — Prospective Confirmed Lineup Collection

- Accumulate `data/point_in_time/mlb_lineups.jsonl` continuously.
- Do not retroactively backfill historical game rows with future lineup observations.
- Track capture rate, confirmation lead time, late scratches, and West Coast coverage.

---

## Phase 16 — Dual-Horizon v9 Architecture

1. **Early v9 ($T-6\text{h}$ to $T-3\text{h}$)**: Projected participants, PIT starter, bullpen state, weather.
2. **Late v9 ($T-45\text{m}$)**: Confirmed lineup, confirmed starters, latest bullpen/injury status, latest weather.
$$\text{ValueOfLineupConfirmation} = \text{Score}(\text{EarlyV9}) - \text{Score}(\text{LateV9})$$

---

## Phase 17 — Model Family Benchmark on Identical v9 Features

Evaluate estimators on the identical rich v9 feature dataset:
- Standardized Logistic Regression ($L_2$ shrinkage)
- Unconstrained XGBoost
- Monotonic XGBoost (domain physics constraints: $\partial P / \partial \text{Offense} \ge 0$, $\partial P / \partial \text{FIP} \le 0$)

---

## Phase 18 — Chronological XGBoost Tuning

- Expanding date folds (train Jan–Apr $\to$ val May; train Jan–May $\to$ val Jun; train Jan–Jun $\to$ val Jul).
- Hyperparameters: `max_depth` (2–4), `learning_rate` (0.01–0.05), `min_child_weight`, `subsample`, `colsample_bytree`, `reg_alpha`, `reg_lambda`.

---

## Phase 19 — Calibration Discipline

- Out-of-fold / validation fits only (Raw vs Platt vs Beta).
- Never fit calibrators on in-sample predictions.
- Freeze: `base_model_hash`, `calibrator_hash`, `feature_table_hash`.

---

## Phase 20 — Freeze Complete v9 Candidate

- Freeze candidate specification: `mlb-v9-candidate-1`.
- Stop modifying code/features once frozen.
- v8 remains 100% production champion; v9 runs strictly in shadow / flat research.

---

## Phase 21 — Prospective v9 Shadow Logging

Every future live game logs both predictions before first pitch:
```json
{
  "event_id": "...",
  "observed_at_utc": "...",
  "v8_probability": 0.542,
  "v9_probability_raw": 0.578,
  "v9_probability_calibrated": 0.569,
  "v9_features": {...},
  "v9_feature_availability": {...},
  "model_hash": "...",
  "dataset_schema_version": "..."
}
```
No parameter tuning while prospective evaluation is active.

---

## Phase 22 — Champion vs Challenger Paired Comparison

Compare v8 vs v9 directly on prospective games:
- Primary: Paired $\Delta\text{LogLoss} = \text{Loss}_{v9,i} - \text{Loss}_{v8,i}$, Paired $\Delta\text{Brier}$, calibration curve.
- Secondary: AUC, accuracy, selective confidence accuracy.
- Economic: CLV, realized ROI, market edge, drawdown.
- Date-clustered bootstrap (2,000 resamples).

---

## Phase 23 — Formal Promotion Gate

Promotion requires the **complete final candidate** to clear all four gates:
1. **Predictive**: $\Delta\text{LogLoss} < 0$, $\Delta\text{Brier} \le 0$, stable calibration, date bootstrap $P(\text{better}) \ge 80\%$.
2. **Operational**: High serving coverage ($\ge 95\%$), no train/serve skew, zero PIT leakage, latency $< 500\text{ms}$, graceful fallback tested.
3. **Prospective**: Statistically meaningful sample of live untouched games (not 5 games, not 10 bets).
4. **Economic**: Non-degraded executable decision efficiency and CLV.

---

## Phase 24 — Atomic Promotion & Rollback Safeguard

Promotion is atomic:
```yaml
# Before:
active_production_version: mlb-elo-trend-lr-v8

# After explicit promotion approval:
active_production_version: mlb-v9
legacy_research_rollback: mlb-elo-trend-lr-v8
```
v8 remains fully preserved as the rollback target.

---

# Architecture Diagram

```text
                         ┌────────────────────┐
                         │   MLB V8 CHAMPION  │
                         │      FROZEN        │
                         └─────────┬──────────┘
                                   │
                            LIVE PRODUCTION
                                   │
                                   ▼
                            outcomes/evidence


       ══════════════════════════════════════════════
       COMPLETELY SEPARATE ISOLATED RESEARCH BOUNDARY
       ══════════════════════════════════════════════


Raw PIT data
     │
     ├── batter history
     ├── starters
     ├── relievers
     ├── weather
     ├── lineups
     ├── BALLDONTLIE
     └── pitch types
             │
             ▼
       MLB V9 FEATURES
             │
             ▼
    immutable feature table
             │
             ├── LR
             ├── XGB
             └── monotonic XGB
                    │
                    ▼
               calibration
                    │
                    ▼
            V9 CANDIDATE FREEZE
                    │
                    ▼
             prospective shadow
                    │
                    ▼
              V8 vs V9 paired
                    │
           ┌────────┴────────┐
           │                 │
       V9 loses          V9 clearly wins
           │                 │
      keep V8          explicit promotion
                             │
                             ▼
                          V9 live
                          V8 rollback
```

---

# Revised Priority Queue (32-Step Sequential Execution)

### Phase 1 — Quarantine Synthetic Prototypes & Safety Hardening [COMPLETED ✅]
1. `research`: Mark MLB v9 feature-table-v2 synthetic prototype as `VOID_SYNTHETIC_PROTOTYPE` in manifest. [✅ DONE]
2. `research`: Mark `mlb-v9-candidate-1` artifact as `VOID_INVALID_FEATURE_PROVENANCE` (preserved for audit, never promoted). [✅ DONE]
3. `safety`: Disable mock prospective shadow capture; fail closed on void candidates. [✅ DONE]
4. `safety`: Make promotion gate fail closed without real verified evidence files. [✅ DONE]
5. `safety`: Remove retroactive `observed_at` timestamp manipulation from v9 benchmark forecaster. [✅ DONE]
6. `safety`: Make v9 prospective ledgers append-only with event deduplication. [✅ DONE]

### Phase 2 — Real PIT Source Adapters & Data Foundations [IN PROGRESS ⚙️]
7. `data`: Ingest real batter Statcast game metrics (BIP, hard-hit, barrel, xwOBA) with explicit missingness flags.
8. `data`: Ingest real batter vs-hand (L/R) plate appearance tagging with hierarchical shrinkage.
9. `data`: Wire real starter Statcast state by player and decision time (CSW%, velo, xwOBA allowed).
10. `refactor`: Make `bullpen_state.py` (`PointInTimeBullpenEngine`) the single canonical v9 bullpen authority. [✅ DONE]
11. `data`: Build PIT reliever appearance + roster/role adapter strictly satisfying `game_start < decision_time`.
12. `data`: Populate real pitch arsenal summaries (fastball velo, breaking/offspeed usage, repertoire entropy).

### Phase 3 — Real Feature Table v3 & Integrity Audit
13. `research`: Build `mlb_v9_feature_table_v3.parquet` from verified point-in-time observed data.
14. `research`: Add feature distribution, std > 0, and correlation integrity audit before fitting.
15. `research`: Freeze v3 manifest with per-family source hashes and cohort event IDs.

### Phase 4 — Disciplined Feature-Family Ablation Ladder
16. `research`: R0 Control (Standardized 6-feature baseline: Elo, trend, PIT park, weather, starter ERA, bullpen weakness).
17. `research`: R1 Real Projected Offense.
18. `research`: R2 Real Starter State (K%, BB%, CSW%, velo, depth).
19. `research`: R3 Real Canonical Bullpen State.
20. `research`: R4 Real Platoon Matchup Interactions.
21. `research`: R5 Real Pitch Arsenal Summary.
22. `research`: Freeze retained feature set based on 2,000 date-clustered bootstrap paired deltas.

### Phase 5 — Model Architecture & Candidate-2 Freeze
23. `research`: Compare standardized L2 LR vs Elastic-Net LR vs Monotonic XGBoost on identical v3 matrix.
24. `research`: Fit out-of-fold calibration (identity, Platt, beta).
25. `artifact`: Freeze `mlb-v9-candidate-2.json` with complete scaler, imputer, hashes, and fail-closed contract.
26. `test`: Verify 100% train/serve parity for every candidate-2 feature.

### Phase 6 — True Prospective Shadow & Promotion Gating
27. `ops`: Wire actual frozen candidate-2 predictor into paired shadow harness.
28. `ops`: Begin untouched v8 vs v9 prospective shadow chain with strict `observed_at < event_start` enforcement.
29. `ops`: Keep confirmed-lineup hourly capture running continuously.
30. `research`: Evaluate paired performance only after minimum sample size ($\ge 200$ games, $\ge 30$ dates) and MDE power.
31. `governance`: Execute formal four-gate promotion evaluation against signed artifacts.
32. `operator`: Atomic production promotion cutover if and only if candidate-2 passes all four gates.

