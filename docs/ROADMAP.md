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

## Session outcomes — 2026-08-26 (night continuation)

Fixes and wiring (full trace in `docs/DEBUG.md` 2026-08-26 night section):

- WNBA morning-report PDF parse bug fixed (81/81 real reports parse).
- Bet Better model-feed capture wired (`step1e_bet_better_models`,
  research-only reference evidence, keyless).
- Statcast aggregates wired into the daily (`step5e_statcast_aggregates`).
- Registry-free ban enforcement wired into eligibility (forecast call
  sites still need to thread `bans` — small follow-up).
- `mlb-v9-benchmark` identity rename; World Cup settle-stall guard;
  esports capture/pricing pin; unknown-PM-market-type logging.
- Postponed-game handling (drain-minimal, operator-chosen): `stale_open_rows`
  health check live (22 rows >72h flagged); multi-day reconciliation sweep
  deliberately shelved — use a targeted, identity-scoped, operator-approved
  re-grade if a stuck row needs clearing.
- Daily-run timing instrumentation added; optimization targets come from
  tomorrow's log (today: 6.7–22.9 min per run).
- Soccer capture `data_root` split-brain fixed before it ever fired.

## Session outcomes — 2026-08-27 (system audit & ablation gate pass)

- **Comprehensive System Audit**: Cataloged in [`docs/SYSTEM_DEFECTS_AND_GAPS_AUDIT.md`](SYSTEM_DEFECTS_AND_GAPS_AUDIT.md).
- **MLB NRFI live serving landmine identified AND resolved**: `config/models/mlb-nrfi-v1.json` frozen with `0.690572` holdout logloss; the live forecast path in `cli/forecast.py` had been invoking the unfitted `MLBNRFIModel()` under the same model_version string. Resolved same session (commit `693744b`): live pre-game feature builder (`models/mlb_first_inning_live.py`, starter-name-keyed, exact-match verified vs the batch ledger) + artifact-backed prediction + champion `MLB.nrfi` promotion; `blocked_workflows` is now empty. Caveat: Polymarket has no NRFI market, so no market-side CLV/ROI grading exists for these rows.
- **MLB Weather & Travel ablation completed**: Tested via `scripts/mlb_weather_travel_ablation.py` (gain +0.005484 MAE, 95% CI `[-0.000215, +0.011088]`) $\rightarrow$ `NO_PROMOTION` verdict under the reproduction gate.
- **Static type audit**: 0 Ruff findings; 220 Mypy findings cataloged for progressive type hardening.
- **Full suite verified**: 2,414 tests pass, 3 skipped, 0 fail.

In-flight research (NOT promoted — promotion awaits the operator's command
per 2026-08-26 directive; validation on settled picks, PIT-safe):

1. **First-inning (NRFI) model improvement** — new features + model tuning
   on the locked 1,337-game holdout (baseline: logloss 0.6910 vs incumbent
   0.6945 vs market proxy 0.6950).
2. **WNBA totals improvement** — replace the four hardcoded zero-value
   constants (park/weather/bullpen/travel) with real PIT pace/rest signals,
   WNBA-gated only.
3. **Tennis spread/total pricing** — game-score distribution from the
   Markov engine, walk-forward on settled picks (derivative pricing was
   removed 08-24 as unsupported; this rebuilds it as validated research).

Still open (unchanged): MLB totals absolute-run-environment signal
(repair-order #2 — the one genuinely open documented research gap);
v9 Phase 23 gate criteria conflict (80% vs 90% bootstrap threshold —
operator decision); API-FOOTBALL key provision (operator).

## Market-edge execution program (2026-08-26 operator directive)

The operator's architectural review directive ("execute this plan" —
market-relative qualification, WNBA possessions×PPP rebuild, MLB
first-inning domain model, CLV/economic validation) was reconciled
against live state before execution. Key reconciliations:

- **NRFI P1 items are ~half-built already.** `models/mlb_first_inning.py`
  (2026-08-26) has the PIT feature ledger, hierarchical credibility
  shrinkage, top-of-order composite, platoon share, starter rest. Delta
  vs. the directive: TTO1 Statcast splits, confirmed-lineup top-of-order
  (PIT-gated), half-inning decomposition P(A₁=0)×P(H₁=0), market-prior
  residual formulation, real NRFI quote capture.
- **WNBA possessions infrastructure exists.** `features/wnba_boxscores.py`
  parses FGA/FTA/TOV/OREB/DREB and feeds `wnba_pace_four_factors`; the
  totals model has not been rebuilt around possessions × PPP yet.
- **Promotion gates are already partially market-relative.** Phase 23
  gate 1 is ΔLogLoss/ΔBrier/calibration/bootstrap and gate 4 is CLV —
  the delta is the full economic battery (ROI, profit factor, drawdown,
  stability slices) and demoting hit-rate targets to diagnostics.
- **Market data reality.** Polymarket US snapshot JSONL (executable
  BBOs) exists for mlb/wnba/esports/kbo/npb/soccer/tennis from
  2026-07-17; NRFI/YRFI slugs are **absent** from the odds tree;
  NBA/NFL have zero sources wired (offseason); soccer is stale
  (Odds API 401 ≥31 days, API-Football awaiting operator key).

Execution phases (research track only; v8 frozen invariant unchanged):

- **A — Market-relative evaluation foundation** ✅ (cb9ee38): reusable
  evaluator (`market_eval.py`: Δlogloss, ΔBrier vs market, CLV rate,
  ROI at executable prices, profit factor, max drawdown, date-clustered
  bootstrap) + market-data census script.
- **B — WNBA totals vs market** ✅ (4db2160): incumbent reproduction
  gate PASS; on 89 lined holdout games the market line beats the model
  decisively (MAE 13.12 vs 20.92, Brier 0.252 vs 0.369, ROI −4.1%,
  CLV rate 0.0). Residual probe (45 train rows) does not transfer. The
  rebuild bar is now explicit: beat market MAE ~13.1.
- **B2 — Structural challenger** ✅ (21d0f3e): player-log PIT feed +
  lineup/absence/possessions×PPP features. Honest null: unlearnable
  under the artifact split (boxscore captures start ~2026-07), and the
  market-window probe (49/25 rows) shows no transfer (22.3 vs 21.2).
  Data depth, not architecture, binds WNBA totals.
- **C — MLB first-inning extensions** ✅ (fc25f01): half-inning
  decomposition (+0.0003 logloss, CI straddles zero) and plate-umpire
  features (direction negative) both honest nulls on the locked
  holdout; reproduction gate PASS (0.690434 vs 0.6910). **Blocked
  (data):** real NRFI/YRFI quotes — Polymarket US lists no first-inning
  markets (F5 only, 40k rows); TTO1 — no Statcast split ingestion; F5
  targets — no innings-1-5 runs in any captured source.
- **D — Qualification governance rewrite** ✅ (this commit): Phase 23
  gates rewritten (market-relative predictive gate, full economic
  battery, stability slices, hit-rate diagnostic-only, beta
  calibration, per-market×league calibration, rolling walk-forward);
  80%/90% conflict resolved in favor of 80%.
- **E — Later-phase items** ✅ (a439987): PA-level inning simulator
  (honest null vs incumbent, level-term overshoot in the current
  regime), OOF stack of structural+ridge+market (meta learns to weight
  the market line +1.28) and per-game σ model (ridge Brier 0.445 →
  0.357) — mechanisms delivered and tested in the 6-week window.
  Umpire features delivered under C. Deferred: conformal intervals
  (per-game σ covers the same need at this data depth) and extra
  WNBA travel/fatigue counters (plan itself rates them tiny marginal
  value; existing rest/tz signals already wired).

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
- Guardrail test identity: `tests/test_model_promotion.py::test_mlb_v8_champion_permanently_protected` (L214-236) validates the exact artifact SHA-256 and `active_production_version == "mlb-elo-trend-lr-v8"` — ported from RESEARCH_BACKLOG.md (recreated 08-23, deleted 2026-08-26).

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

### Evaluation Verification Contracts (ported from RESEARCH_BACKLOG.md — recreated 08-23, deleted 2026-08-26)

- **No evaluator fallback**: `scripts/mlb_evaluator.py` raises `ABORT_DATASET_CONTRACT_MISMATCH` on a missing dataset — never falls back to a synthetic or partial evaluation.
- **Full 5-hash verification**: verifies `dataset_sha256`, `schema_sha256`, `train_event_ids_sha256`, `validation_event_ids_sha256`, `research_test_event_ids_sha256`, plus cohort JSON equality.
- **Missingness decoupled from numeric equality**: check source-availability flags, never `gap != 0`.
- **Cohort nomenclature**: the holdout cohort is `research_test` / `historical_model_selection_test`.

---

## Phase 3 — Define v9's Development/Test Structure

The old v8 holdout has already been inspected repeatedly and is now **development evidence for v9**, not the final untouched v9 test.

```text
LEVEL 1: Historical v9 development (Train split: 3,814 games)
LEVEL 2: Historical model-selection test / research comparison (Validation + Research Test: 2,824 games)
LEVEL 3: Prospective untouched evaluation (Future live games logged before first pitch)
```

The final promotion decision relies heavily on Level 3. Prospective evaluation is critical because confirmed lineups, late scratches, and real-time injuries cannot honestly be reconstructed historically.

**v1 control baseline (PERMANENT CONTROL BASELINE)** — `outputs/research/mlb_v9/tables/mlb_v9_feature_table_v1.parquet`: 6,638 games (3,814 train / 1,082 val / 1,742 test), standardized 6-feature baseline, LogLoss 0.684707, Brier 0.245772, AUC 0.5700. (Ported from RESEARCH_BACKLOG.md Gate 3.1 — recreated 08-23, deleted 2026-08-26.)

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

**KNOWN ISSUE (found 2026-08-26, unresolved):** `scripts/audit_mlb_v9_feature_distribution.py`
against `outputs/research/mlb_v9/tables/mlb_v9_feature_table_v3.parquet` (6,638 rows) shows
13 of the 24 features below are **dead (zero variance)** — every game gets the identical
fallback constant instead of a real per-game value: all four Starter-State gap features
(`starter_k_pct_gap`, `starter_bb_pct_gap`, `starter_k_bb_gap`, `starter_depth_gap`), all four
Projected-Offense gap features, all three Bullpen-State advantage features, and both Platoon
gap features. `home_expected_starter_ip`/`away_expected_starter_ip` are constant 5.30,
`home/away_projected_woba` constant 0.3150, `home/away_bullpen_effective_fip` constant 3.9000
— consistent with `starter_state_matchup_gaps()`, `projected_offense_matchup_gaps()`,
`bp_engine.evaluate_matchup()`, and `platoon_matchup_gaps()` in
`scripts/mlb_v9_feature_table_v3.py` silently falling through to their `.get(key, default)`
fallback for every row, not a genuine absence of signal. The underlying source data exists and
is populated (`data/mlb_statsapi/game_snapshots.jsonl`, 6,683 rows; both Statcast parquets) —
this points to a lookup/matching bug (team-ID or date-key mismatch against the snapshot) inside
those four feature engines, not missing data. Any v9 ablation result claiming to have measured
Phases 6/9/11 (below) or the offense-projection phase is void until this is root-caused — those
feature families have contributed zero signal in every historical run to date.

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

Promotion requires the **complete final candidate** to clear all four gates.
Criteria rewritten 2026-08-27 per the operator's market-edge directive: the
model is judged on whether it adds information **beyond the market**, not
on its standalone hit rate.

1. **Predictive** (market-relative): paired $\Delta\text{LogLoss} < 0$ and
   $\Delta\text{Brier} \le 0$ **vs the no-vig market probability** (not vs
   0.5), date-cluster bootstrap $P(\text{better}) \ge 80\%$ — this resolves
   the 80%/90% conflict in favor of 80% (the stricter variant is superseded
   by the economic battery below, which is where overfitting shows up).
   Calibration reported by **model × market_type × league** (never shared
   across markets); calibrator (raw/Platt/beta/isotonic, see
   `calibration.BetaCalibrator`) chosen exclusively on out-of-fold
   historical predictions via `validation.rolling_walk_forward_splits`,
   never on the final holdout.
2. **Operational**: High serving coverage ($\ge 95\%$), no train/serve
   skew, zero PIT leakage, latency $< 500\text{ms}$, graceful fallback
   tested. (Unchanged.)
3. **Prospective**: Statistically meaningful sample of live untouched
   games (not 5 games, not 10 bets). (Unchanged.)
4. **Economic** (the primary gate — a model that predicts winners but
   loses money is not promotable): full battery from
   `market_eval.market_relative_report` at timestamped executable prices:
   ROI with date-clustered bootstrap CI excluding 0, profit factor
   $\ge 1$, CLV rate $\ge 50\%$ against the closing no-vig price, realized
   CLV $\ge 0$, no severe drawdown spikes, and non-degraded executable
   decision efficiency.

**Stability slices (required evidence, all four gates):** month-to-month,
favorite/underdog, edge buckets, home/away, and high/low total
environments. A hit-rate floor remains a **diagnostic** (reported, not
gating); the pre-existing `validation.py` hit-rate machinery stays as that
diagnostic evidence.

**Status as of 08-27:** gate text updated; economic battery implemented
(Phase A); market data depth (6-week snapshot window) is the binding
constraint for producing gate-grade evidence — v3 matrix and candidate-2
freeze still pending (v1 control baseline LogLoss 0.6847 / Brier 0.2458;
candidate-1 voided).

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

---

## Secondary Sports Track Status

(Ported from RESEARCH_BACKLOG.md — recreated 08-23, deleted 2026-08-26. This section was removed from ROADMAP.md in the 2026-08-23 reset commit 37e3be6; restoring it here keeps the secondary-sports status in the consolidated roadmap.)

- **Soccer v2**: dynamic Polymarket league discovery (`discover_soccer_leagues`); hierarchical Dixon-Coles bivariate Poisson score matrix with time decay ($w = e^{-\Delta t/\tau}$); separately calibrated Double Chance, Draw No Bet, Clean Sheet, and BTTS distributions.
- **Tennis Surface-Elo**: surface-blended Elo ratings (60% surface, 40% overall) across 26,458 historical matches.
- **WNBA**: hierarchical Empirical-Bayes rotation and minutes shrinkage engine (`features/wnba_player_impact.py`); Four Factors and possession pace modeling (`features/wnba_pace_four_factors.py`); parametric Normal-CDF derivative solver for totals and spreads.
- **NFL**: starting QB state vector (EPA/play, CPOE, P2S%, TWP%) with backup replacement spread penalty (`features/nfl_qb_oline.py`); offensive line protection and health composite index.

---

## Data-expansion backlog (operator proposal, 2026-08-27)

Not scheduled/started. The premise: the project's next real gains are more
likely to come from better data coverage and market context than from a new
model family. Every item below must clear the existing promotion discipline
(`ablation_reproduction_gate`, walk-forward validation, real-numbers ablation
delta) before touching a live feature — adding a data source is not itself a
promotion decision.

**Priority order proposed:**
1. Historical sportsbook odds (opening/closing spread & total, line
   movement, consensus, dispersion) — The Odds API historical snapshots
   (paid, back to 2020) or TheRundown (free tier: 20k/day, 20+
   books/exchanges incl. Polymarket/Polymarket US/Kalshi, unified schema).
   Target: `Predict(Y - Market)` instead of `Predict(Y)`.
2. Real historical weather (Open-Meteo historical archive) to replace
   placeholder `weather = 1.0` / `travel = 0.0` values in the totals feature
   builder; store raw observations (temp, dew point, humidity, pressure,
   wind speed/gust/direction, precip), derive park-relative wind
   in/out/cross vectors from stadium bearing afterward — don't collapse to
   a single `weather_factor` before storing.
3. Venue + travel geography — one-time geocoded venue table (lat/lon,
   elevation, timezone, park dimensions), used to compute real
   distance/timezone-change/rest features per team-game instead of proxies.
4. Player availability + lineups as full **snapshot** history (status at
   each observation time, not just game-day truth) — same PIT-safety
   requirement as everything else in this codebase; a live-only signal
   (e.g. today's roster) can never be used for a past decision time.
5. Deeper player/team stat tables (Statcast batter/pitcher extensions,
   NBA/WNBA lineup-possession data, NFL snap-share/injury participation).
6. Play-by-play/period data for pace, possession, and inning/quarter state
   features.
7. Cross-provider reconciliation (starter/line consensus + disagreement) as
   a data-quality signal, plus a source-reliability layer instead of a
   single hardcoded provider per field.

**Structural pieces**, if/when this is pursued: a `data/market_history/`
warehouse (canonical `MarketQuote` schema: event_id, sport, market_type,
provider, book, selection, line, price, observed_at_utc, is_main_line);
standardized provider adapters (`fetch/normalize/validate/snapshot`) so a
new API doesn't leak provider-specific JSON into model code; a canonical
entity/alias graph (team/player/venue/event IDs, provider→canonical alias
table) rather than joining on names — this codebase's MLB v9 experience
already showed how badly identifier mismatches can silently zero out whole
feature families; per-source daily coverage/drift health checks, run before
training or forecasting, not just as an afterthought.

**Explicitly out of scope until an operator decision**: any new paid API
subscription (The Odds API historical, TheRundown beyond its free tier);
any new third-party credential. Do not add a provider integration that
requires a paid key without confirming first.

---

## Testing & Quality Engineering Roadmap

A structured testing initiative to harden the platform, prevent regressions, and enforce strict compile-time and runtime guarantees:

### 1. Mypy Static Type Hardening (Zero-Error Initiative)
- **Current Baseline**: 220 errors across 59 files (`.venv/bin/mypy src/model_prediction`).
- **Target**: 0 Mypy errors with strict type checking enabled across `src/model_prediction`.
- **Phased Milestones**:
  - `T1.1`: Type-annotate dashboard route handlers and query parameters (`src/model_prediction/dashboard/routes.py`, `orders.py`, `backtests.py`).
  - `T1.2`: Resolve dictionary-to-primitive casting and type mismatches in ledger stores (`src/model_prediction/portfolio/polymarket_ledger.py`, `runtime_ledger_store.py`).
  - `T1.3`: Fix Future/ThreadPoolExecutor return type mismatches in daily fanout (`src/model_prediction/cli/daily.py`).
  - `T1.4`: Enforce `mypy` as a mandatory pre-commit and CI blocking gate.

### 2. Fast Deterministic CI Daily Cycle Smoke Test
- **Target**: Run a full synthetic slate end-to-end (ingest $\rightarrow$ forecast $\rightarrow$ settle $\rightarrow$ ledger write $\rightarrow$ dashboard cache build) in $< 3.0$ seconds inside CI.
- **Implementation**: Pure in-memory SQLite (`:memory:`) with mock HTTP fixtures for ESPN, Polymarket, and StatsAPI feeds, validating all multi-sport workflows on every git push without external network I/O.

### 3. Hypothesis Stateful Property-Based Ledger Testing
- **Target**: Continuous fuzzing of ledger transitions, split-brain protections, and financial invariants.
- **Invariants Verified**:
  - P&L conservation: $\sum \text{P&L}_{\text{individual}} \equiv \text{P&L}_{\text{aggregate}}$.
  - State machine irreversibility: Once settled or voided, a pick row can never transition back to open.
  - Dedup idempotency: Re-running a forecast across an identical slate never generates duplicate active calls.

### 4. Parallel Benchmark & Latency Profiling Harness
- **Current Run Time**: 6.7 to 22.9 minutes per scheduled daily run.
- **Target**: Reduce daily run time to $< 60$ seconds.
- **Action**: Profile network bottlenecks using `cProfile` and migrate sequential HTTP queries in [`cli/daily.py`](../src/model_prediction/cli/daily.py) to concurrent `httpx.AsyncClient` pipelines with strict rate-limiting.

---

## Public API Ecosystem Survey & Data Enhancement Roadmap

A systematic evaluation of the public API catalog ([`public-apis/public-apis`](https://github.com/public-apis/public-apis)) to identify high-leverage data sources for expanding model accuracy, sports coverage, environmental physics, and market context without unnecessary paid subscriptions.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PUBLIC APIS RECONNAISSANCE MATRIX                        │
├───────────────────┬─────────────┬─────────────┬─────────────┬───────────────┤
│ Domain / Category │ Target APIs │ Auth Type   │ Cost        │ Model Utility │
├───────────────────┼─────────────┼─────────────┼─────────────┼───────────────┤
│ Sports & Scores   │ API-Football│ apiKey      │ Freemium    │ Soccer 1X2/Tot│
│                   │ balldontlie │ No Auth     │ Free        │ NBA/WNBA Pace │
│                   │ TheSportsDB │ apiKey      │ Free        │ Entity Metadata│
│                   │ Ergast/OpenF1│ No Auth    │ Free        │ Racing (Exp.) │
├───────────────────┼─────────────┼─────────────┼─────────────┼───────────────┤
│ Weather & Physics │ Open-Meteo  │ No Auth     │ Free        │ Air density ρ │
│                   │ NWS / NOAA  │ User-Agent  │ Free        │ Wind & Press. │
│                   │ OpenWeather │ apiKey      │ Freemium    │ Fallback T/H  │
├───────────────────┼─────────────┼─────────────┼─────────────┼───────────────┤
│ Geocoding & Travel│ OpenRoute   │ apiKey      │ Free Tier   │ Road distance │
│                   │ Positionstack│ apiKey     │ Free Tier   │ Venue geocoding│
│                   │ Aviationstack│ apiKey     │ Freemium    │ Charter flights│
├───────────────────┼─────────────┼─────────────┼─────────────┼───────────────┤
│ Esports & Gaming  │ OpenDota    │ No Auth/Key │ Free        │ Dota 2 draft  │
│                   │ Riot Games  │ apiKey      │ Free (Dev)  │ LoL / Val PBP │
│                   │ PandaScore  │ apiKey      │ Freemium    │ CS2/R6 events │
├───────────────────┼─────────────┼─────────────┼─────────────┼───────────────┤
│ Markets & Liquidity│ Polymarket  │ No Auth/Key │ Free        │ US BBO CLOB   │
│                   │ Kalshi      │ RSA / Key   │ Free        │ Event spreads │
│                   │ CoinGecko   │ No Auth     │ Free        │ Settlement gas│
└───────────────────┴─────────────┴─────────────┴─────────────┴───────────────┘
```

### 1. Sports & Live Match Feeds
1. **`API-FOOTBALL` (RapidAPI / API-Sports)**
   - **Scope**: Live scores, lineups, player ratings, substitutions, and xG for 1,000+ soccer competitions.
   - **Status in Repo**: Adapter written in [`src/model_prediction/data_sources/api_football.py`](../src/model_prediction/data_sources/api_football.py).
   - **Action**: Provision `API_FOOTBALL_KEY` to restore soccer live tracking and Dixon-Coles settlement.
2. **`balldontlie` (NBA / Basketball Data)**
   - **Scope**: Boxscores, player game logs, shot charts, advanced pace, and team efficiency metrics.
   - **Status in Repo**: Adapter live in [`src/model_prediction/data_sources/balldontlie.py`](../src/model_prediction/data_sources/balldontlie.py).
   - **Roadmap Enhancement**: Integrate player-level usage and offensive rating priors to replace flat team averages.
3. **`TheSportsDB` (Cross-Sport Entity & Venue Master Table)**
   - **Scope**: Canonical team aliases, stadium capacities, surface types, and league metadata.
   - **Roadmap Enhancement**: Use to seed the canonical entity crosswalk graph, preventing join collisions.
4. **`Ergast Developer API / OpenF1` (Motorsports Expansion)**
   - **Scope**: Lap times, pit stops, tire degradation, and circuit weather telemetries.
   - **Roadmap Enhancement**: Candidate for future high-frequency prediction market expansion (Formula 1 driver match-ups).

### 2. Weather, Aerodynamics & Environmental Physics
1. **`Open-Meteo Historical Archive & Forecast API`**
   - **Scope**: High-resolution hourly temperature, relative humidity, barometric pressure, dew point, wind speed, wind gusts, and wind direction.
   - **Auth / Cost**: No API key required; completely free for open non-commercial research.
   - **Roadmap Application**: Derive Bahill air density ($\rho$) and stadium-relative wind vectors ($W_{\text{in/out}}, W_{\text{cross}}$) for MLB/NFL totals modeling.
2. **`National Weather Service (NWS / weather.gov) API`**
   - **Scope**: Station-level surface observations and Terminal Aerodrome Forecasts (TAF) with official barometric altimeter settings.
   - **Auth / Cost**: Free public US government data (requires descriptive `User-Agent`).
   - **Roadmap Application**: Serves as a zero-cost, high-reliability live pre-game weather verification feed.

### 3. Geocoding, Circadian Logistics & Travel Distance
1. **`OpenRouteService / OSRM (Open Source Routing Machine)`**
   - **Scope**: High-precision driving/transit matrices and travel duration factoring in traffic and terrain.
   - **Roadmap Application**: Compute real ground travel fatigue for regional basketball (WNBA) and minor leagues where teams travel by bus rather than charter aircraft.
2. **`Positionstack / OpenStreetMap Nominatim`**
   - **Scope**: Forward and reverse geocoding for stadiums, neutral-site arenas, and training facilities.
   - **Roadmap Application**: Automatically resolve one-off exhibition and neutral-site venues (e.g. London Series, Field of Dreams, Tokyo Dome) into verified lat/lon coordinates.

### 4. Esports Micro-Data Feeds
1. **`OpenDota API`**
   - **Scope**: Complete match replay parsing, hero drafting synergy, gold/XP net worth graphs, and combat logs.
   - **Roadmap Application**: Replace flat team-level Elo with hero-composition drafting advantage matrices.
2. **`Riot Games Developer API (LoL & Valorant)`**
   - **Scope**: Official match timelines, champion bans/picks, dragon/Baron neutral objective timing, and round economy.
   - **Roadmap Application**: Predict map outcomes based on live draft synergy and champion mastery vectors.

### 5. Multi-Exchange Liquidity & Consensus Feeds
1. **`Polymarket CLOB WebSocket & REST API`**
   - **Scope**: Level 2 orderbook depth, best bid/ask (BBO), last traded price, and volume across all prediction markets.
   - **Roadmap Application**: Real-time arbitrage detection and sub-second line movement execution.
2. **`Kalshi API`**
   - **Scope**: Regulated US market event contract odds and settlement determinations.
   - **Roadmap Application**: Multi-book Dutching arbitrage and consensus line formation.

---


