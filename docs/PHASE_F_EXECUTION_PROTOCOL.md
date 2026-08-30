# Phase F Autonomous Research Execution Protocol

## Mission

Continue developing the sports prediction research system by discovering reproducible information that improves predictions relative to the decision-time market.

The primary research question is:

**What point-in-time information improves prediction after conditioning on the market state already available at the decision timestamp?**

Do not optimize for impressive backtest results, raw hit rate, feature count, model complexity, or synthetic profitability.

Scientific validity, point-in-time correctness, reproducibility, market-relative improvement, calibration, and prospective generalization take priority.

---

## Required Startup Procedure

At the beginning of every research task:

1. Read `AGENTS.md` (or `docs/AGENTS.md`).
2. Read `docs/PHASE_F_EXECUTION_PROTOCOL.md`.
3. Read `config/research/phase_f_state.yaml`.
4. Read the Phase F section of `docs/ROADMAP.md`.
5. Read the most recent Phase F `manifest.json`, `metrics.json`, and `report.md`.
6. Inspect the current git SHA and working-tree state.
7. Determine the next eligible action from the Phase F state machine.

Do not depend on previous chat messages for authoritative project state when the repository contains a newer state.

---

## Autonomous Execution Rule

Proceed through ordinary research operations without requesting human confirmation.

Examples of operations that may proceed automatically:

- ingesting additional raw historical data;
- validating or reconciling providers;
- deduplicating idempotent raw records;
- producing data-coverage reports;
- executing frozen research harnesses;
- running preregistered diagnostics;
- running chronological OOF evaluation;
- computing bootstrap or permutation inference;
- generating experiment artifacts;
- running tests, mypy, and ruff;
- updating machine-readable research state when a deterministic frozen gate permits the transition;
- updating research documentation with factual experimental results.

Do not stop after each substep merely to report progress.

Continue until reaching a formal stop condition.

---

## Formal Stop Conditions

Stop and request human review if any of the following is required:

- changing a frozen research definition after seeing its result;
- modifying a locked confirmation period;
- changing an immutable preregistration contract;
- weakening an existing promotion or replication threshold;
- changing a production champion;
- changing production fallback behavior;
- enabling automated wagering/orders;
- destructively migrating a production or research database without a safe verified migration path;
- purchasing or provisioning a paid provider credential;
- accepting ambiguous provider licensing or usage terms;
- proceeding despite unresolved PIT leakage;
- proceeding despite unexplained entity-resolution or event-matching corruption;
- overriding a failed scientific gate merely to advance the roadmap.

A disappointing experimental result is not itself a reason to modify the experiment.

---

# Phase F State Machine

## F1R — Multi-Season Replication Protocol

Current priority.

### Dual Operating Sub-Stages
Since prospective collection alone cannot efficiently produce a second MLB season within a single calendar year, F1R operates two concurrent sub-stages:
1. `F1R_HISTORICAL_BACKFILL`: Ingesting and reconciling historical 2024 and 2025 MLB totals and spreads.
2. `F1R_PROSPECTIVE_CAPTURE`: Continuous live quote capture across the active 2026 season.

### Protocol Freeze Hash
To prevent silent optimization or subtle drift of frozen contracts, the F1R replication harness calculates a cryptographic SHA256 digest (`f1r_protocol_hash`) over:
- MarketStateVector v1 schema & definitions
- M0, M0b, and M4-1 mathematical definitions
- Preregistered replication gate thresholds
- Replication panel partitioning logic
- Empirical residual distribution probability formulas
- Within-date fixed effects, clustered bootstrap, and permutation algorithms

Every experiment manifest and state file must record the identical `f1r_protocol_hash`. Any unauthorized alteration triggers `FORMAL_REPLICATION_INVALIDATED`.

### Disambiguated Market Data Counters
To guarantee transparent quote accounting across runs, all reports and state files maintain permanent, non-overloaded counters:
```yaml
market_data:
  raw_quotes_observed: int     # Total lines/records in raw source feeds
  raw_quotes_archived: int     # Valid parsed quote objects stored in warehouse
  unique_quotes: int           # Distinct quote signatures
  eligible_pit_quotes: int     # Quotes satisfying PIT timestamps
  decision_quotes: int         # Quotes matched at decision timestamp (T-30m)
  closing_quotes: int          # Quotes matched at game start / closing
  duplicate_quotes: int        # Identical quote signatures deduplicated
  rejected_quotes: int         # Malformed/unparsable raw lines
```

### Coverage Breadth & Source-Era Analysis
Coverage is evaluated across full seasons (2024, 2025, 2026) to prevent single-season bias. For each season, the evaluation records:
- Matched vs Scheduled game counts and unique date clusters;
- Decision, sharp, soft, and closing market coverage fractions;
- Provider composition: `books_per_game_mean`, `books_per_game_median`, `sharp_books_per_game`, `soft_books_per_game`;
- Decision quote staleness (`decision_quote_age_median_sec`).

### Informational Checkpoints vs Formal Replication Gate
- Intermediate evaluations ($N < 1000$ or $D < 100$ or $S < 2$) are explicitly tagged:
  `checkpoint_type: INFORMATIONAL`
  `decision_authority: NONE`
  The agent must NOT tune models or modify gates based on intermediate trajectories.
- Only upon reaching the preregistered milestone ($N_{games} \ge 1000, N_{dates} \ge 100, N_{seasons} \ge 2$) is the evaluation tagged:
  `checkpoint_type: FORMAL_REPLICATION_GATE`
  `decision_authority: BINDING_GATE`

PIT violations must equal zero ($PIT\_violations = 0$).

---

## Original vs New Replication Panels

Preserve three primary evaluation panels plus per-season diagnostic slices:

### Original Identification Panel
The original 250-game / 27-date sample (2026 identification cohort).

### New Untouched Panel
Only newly acquired games strictly beyond the original 250 identification games.
Additionally exposed as per-season diagnostic slices:
- `NEW_UNTOUCHED_2024`
- `NEW_UNTOUCHED_2025`
- `NEW_UNTOUCHED_2026`

### Pooled Replication Panel
The full multi-season sample used for the official preregistered replication gate ($N \ge 1000$).


---

## F1R Required Models

### M0

Raw frozen market consensus.

### M0b

Chronologically estimated market-level bias correction using training data only.

### M4-1

Chronologically estimated structural-delta model:

R = α + βΔ

where:

R = Actual − Market

and:

Δ = StructuralPrediction − Market.

No validation or test outcome may be used to fit α or β.

---

## Required Identification Diagnostics

Report:

- β raw;
- β within-date;
- date-clustered CI;
- OLS diagnostic SE;
- Pearson correlation;
- Spearman correlation;
- R²;
- within-date fixed-effects result;
- within-date permutation result;
- M0 MAE/RMSE/bias;
- M0b MAE/RMSE/bias;
- M4-1 MAE/RMSE/bias;
- paired M0b versus M4-1 MAE gain;
- date-bootstrap CI for incremental MAE gain;
- P(M4-1 beats M0b);
- effective sample sizes;
- positive/negative delta counts;
- discrepancy bucket results.

Do not report an under-signal or over-signal as qualified when its minimum sample-size requirement is not satisfied.

---

## Phase F1 Replication Gate

Complex M4, M3 interaction confirmation, and M5 remain locked until all currently preregistered requirements are evaluated.

Do not weaken a failed criterion.

If replication fails, classify the failure as one of:

- `FAIL_DATA`
- `FAIL_LEVEL_ONLY`
- `FAIL_PROBABILITY`
- `FAIL_STABILITY`
- `FAIL_INCREMENTAL_ACCURACY` (structural matchup signal exists, beta_within > 0 and temporally stable, but incremental magnitude is insufficient to beat M0b reliably)
- `FAIL_ECONOMIC`
- `INSUFFICIENT_EVIDENCE`

and follow the corresponding research branch instead of escalating model complexity.

---

## F1S — Structural Signal Amplification Protocol

When F1R exits with `FAIL_INCREMENTAL_ACCURACY`:
1. **Freeze F1R Result**: Record F1R = FAIL, beta_within = +0.1905, signal_exists = true, incremental_MAE_edge = false.
2. **Keep F2–F8 Locked**: Distribution modeling (F2) cannot rescue a mean model adding essentially zero incremental MAE.
3. **Open F1S Structural Signal Amplification Branch**: Increase the matchup-specific component of the structural model before retesting M4.
4. **Predefined Baseball Regime Diagnostics**: Diagnose where the +0.19 aggregate signal originates across 11 predefined regimes (starter quality, starter uncertainty, bullpen fatigue, lineup quality, platoon advantage, park, weather, market total bucket, favorite strength, day/night, roof/open air).
5. **Feature Prioritization Order**:
   - Starting pitcher depth & TTO degradation (E[IP], E[BF], TTO1/2/3 curve, rest, handedness)
   - Actual lineup quality (confirmed 1-9 batting order, PA weights, Empirical Bayes xwOBA/wOBA, K%, BB%, ISO, Barrel%)
   - Pitcher x lineup matchup (starter K% x lineup K%, BB% x BB%, platoon splits)
   - Bullpen availability tonight (active reliever effective FIP, high-leverage available, pitches 1d-3d, expected bullpen IP)
   - Park / weather conditional physics (air density ratio, fly-ball distance factor, wind out x barrel, temperature x ISO)
6. **Decomposed Structural Scoring Target**:
   Separate away and home scoring expectations decomposed by pitcher phase:
   $$E[Runs_{team}] = E[Runs_{vsStarter}] + E[Runs_{vsBullpen}]$$
   where starter quality and bullpen availability interact through expected innings allocation $E[IP_{SP}]$ and $(9.0 - E[IP_{SP}])$.
7. **MLB Structural v10 Challenger**:
   One single regularized GLM (Ridge / Poisson regression) challenger model. No boosted trees (XGBoost) yet.
8. **Standalone Structural Gate (Without Market)**:
   Require $MAE_{struct, v10} < MAE_{struct, v9}$ and better team-run calibration OOS across chronological OOF walk-forward folds before entering market-relative evaluation.
9. **Market-Relative Incremental Test**:
   Evaluate $\beta_{within, v10}$ and $MAE_{M0b} - MAE_{M4-1(v10)}$. Require incremental MAE edge beyond M0b to exceed the negligible 0.0001 threshold.
10. **New Confirmation Contract**:
    The 2024–2026 multi-season panel is designated as `DEVELOPMENT / VALIDATION`. The next formal confirmation claim requires preregistering a future unseen prospective game window.

---

## F1C — MLB Structural v10 Prospective Confirmation Protocol

### 1. State & Immutability Freeze
Following the F1S development and diagnostic validation phase, all of MLB Structural v10 is frozen:
* `MLBv10FeatureExtractor` feature extraction rules, PA order weighting, and Empirical Bayes shrinkages.
* `MLBStructuralV10Model` Ridge regression formulation, cross-validated penalty parameter, and fixed coefficients fit on the 2024–2026 pre-freeze development panel.
* Fixed calibration parameters: $\hat{c}_{bias} = +0.4724$, $\hat{\alpha} = +0.2619$, $\hat{\beta} = +0.4457$.
* Frozen 5-fold chronological OOF unexplained error distribution $\{e_i\}_{i=1}^{5427}$ ($e_i = (Y_i - M_i) - \hat{\mu}_i^{OOF}$) with half-run continuity interval $P(Push) = P(-0.5 \le R^* < 0.5)$ for integer totals.
* Any modification after prospective collection begins defines `v10.1` and resets confirmation.

#### Cryptographic Protocol Hashes
* `v10_feature_schema_hash`: `107a42b6586e7be2`
* `v10_model_spec_hash`: `6b677efdf92de0cd`
* `v10_confirmation_protocol_hash`: `e9c2b6dcc235c9af`
* `v10_probability_model_hash`: `466f2c81e28b322f`

### 2. Prospective Evaluation Stages
* **Stage C1 — 2026 Prospective Evidence (`PROSPECTIVE_REPLICATION_2026`)**:
  All remaining eligible 2026 MLB regular-season games starting after the freeze timestamp.
* **Stage C2 — 2027 Binding Continuation (`PROSPECTIVE_CONTINUATION_2027`)**:
  If Stage C1 does not reach the required sample size due to regular-season completion, continue the **exact same frozen v10 model** into the 2027 MLB season until the qualification milestone is satisfied. Model coefficients and feature architecture must not be retrained or altered.

### 3. Sample Size Milestones
* **Milestone 1 — Interim Analysis (`C1_INTERIM_ANALYSIS`)**: $N_{games} \ge 300$, $N_{dates} \ge 30$. Descriptive interim report only; NO model or protocol changes; continue collection without peeking at binding significance.
* **Milestone 2 — Binding Qualification (`C1_BINDING_QUALIFICATION`)**: $N_{games} \ge 500$, $N_{dates} \ge 50$. Formal binding evaluation of Gates A–F. If 2026 season concludes with $N < 500$, mark `C1 = INSUFFICIENT_EVIDENCE` and continue unchanged into Stage C2 (2027 season).

### 4. Primary Confirmation Comparison
The central formal comparison is **$M0b$ (Bias-Corrected Decision Market)** vs **$M4-1(v10)$**:
$$G = MAE(M0b) - MAE(M4-1_{v10})$$
Primary requirement: $G > 0$ with date-clustered bootstrap probability $P(G > 0) \ge 0.90$.

### 5. Preregistered Confirmation Gates
* **Gate A (Structural Discrimination)**: $\beta_{within, v10} > 0$ with date-clustered 95% CI strictly excluding zero.
* **Gate B (Continuous Incremental Edge)**: $MAE(M4-1_{v10}) < MAE(M0b)$ with $P(G > 0) \ge 0.90$.
* **Gate C (No Catastrophic Bias)**: $|\text{Bias}(M4-1_{v10})| < 0.25$ runs per game.
* **Gate D (Probability Improvement)**: $\text{Brier}(v10) < \text{Brier}(M0)$ or $\text{NLL}(v10) < \text{NLL}(M0)$ with no material degradation in the other.
* **Gate E (Calibration)**: Calibration slope $b_{cal}$ of $M4-1(v10)$ in $[0.85, 1.15]$ (evaluated on prospective outcomes).
* **Gate F (Temporal Stability)**: Positive direction across sufficiently sampled temporal blocks.

### 6. Append-Only Shadow Ledger Structure
Stored in `data/point_in_time/mlb_v10_prospective_ledger.jsonl` with strictly separated immutable record types:
1. `RECORD_TYPE: PREDICTION`:
   `event_id`, `game_start_utc`, `decision_utc`, `created_at_utc`, `market_line`, `market_prob`, `market_state_hash`,
   `v10_pred_away`, `v10_pred_home`, `v10_pred_total`, `v10_pred_margin`, `v10_delta_vs_market`,
   `m0b_prediction`, `m4_1_v10_prediction`, `p_over`, `p_under`, `p_push`,
   `model_spec_hash`, `feature_snapshot_hash`, `probability_model_hash`, `prediction_hash`.
2. `RECORD_TYPE: SETTLEMENT`:
   `prediction_hash`, `event_id`, `actual_away`, `actual_home`, `actual_total`, `actual_margin`, `settled_at_utc`.
3. `RECORD_TYPE: CLOSING_MARKET` (Evaluation-only):
   `prediction_hash`, `event_id`, `closing_line`, `closing_price`, `closing_market_hash`, `closing_quote_observed_at_utc`, `captured_at_utc`.

### 7. Daily Operational Integrity Audit & Strict Blind Policy
* Run `scripts/mlb_v10_daily_operational_audit.py` daily.
* Invariant checks:
  - `PIT_violations == 0` (all inputs observed strictly before $T-30\text{m}$).
  - `duplicate_predictions == 0` (single prediction per game).
  - `late_predictions == 0` (logged before first pitch).
  - `hash_verification_failures == 0`.
* **Strict Blind Policy**: Do NOT calculate or report $\beta_{within}$, MAE gain, Brier score, NLL, or ROI during daily runs. Only inspect model accuracy upon reaching the preregistered milestones ($N \ge 300$ or $N \ge 500$).

### 8. Incident Handling Policies
* **Data-Provider Outage**: Preserve frozen model; mark game ineligible under frozen rules.
* **Cosmetic / Reporting Bug**: Fix reporting without reset if pregame predictions remain unaltered.
* **Feature / Model / Calibration Defect**: **Invalidates confirmation and restarts with v10.1**.
* **Hash Mismatch**: Hard-stop collector and immediately investigate root cause.
* **Missing Pregame Prediction**: Never reconstruct or backfill predictions after first pitch.

### 9. Downstream Research Unlock Criteria
* If F1C passes all prospective gates $\implies$ **Unlock F2 (Distribution Research)**.
* Interaction terms (MLB-INT-001 through MLB-INT-005) and F3–F8 remain strictly **LOCKED** during confirmation.
* No development of v11 while C1/C2 confirmation runs.

---

# Probability Foundation

The Phase F1 probabilistic comparison must use a simple training-only probability conversion rather than an advanced variance model.

Begin with a training-fold empirical residual distribution.

For a predicted residual mean μ, estimate outcome probabilities using only residual information available from the training fold.

For integer market totals/spreads, explicitly preserve push probability.

Never treat pushes silently as wins or losses.

Report:

- P(over/home cover);
- P(under/away cover);
- P(push), where applicable;
- Brier;
- NLL/log loss using the appropriate outcome formulation;
- ECE;
- calibration slope;
- calibration intercept.

M0 probabilities are derived from decision-time no-vig market prices only.

Closing prices must never be model inputs.

---

# F2 — Distribution Research

F2 unlocks only after F1R passes.

Evaluate the distribution ladder in increasing complexity:

1. empirical training residual distribution;
2. constant-variance Normal;
3. constant-variance Student-t;
4. heteroskedastic distribution.

For heteroskedastic modeling:

μ = f(X)

log σ² = g(X)

Evaluate distribution quality independently from mean quality.

Primary distribution metrics:

- NLL;
- CRPS;
- Brier;
- ECE;
- calibration slope/intercept;
- empirical 50% interval coverage;
- empirical 80% interval coverage;
- empirical 95% interval coverage.

Reject conditional-variance complexity if it does not improve held-out probabilistic/distributional performance.

---

# F3 — M4 Residual Model Ladder

After replication, evaluate:

### M4-0
Intercept only.

### M4-1
Structural delta only.

### M4-2
Ridge regression using a frozen core feature set.

### M4-3
ElasticNet.

### M4-4
Conservative shallow gradient boosting.

Use chronological nested evaluation.

Hyperparameters must be selected using training/validation data only.

The locked confirmation set may be evaluated only after candidate definition is frozen.

Flexible ML receives greater scrutiny than simple models.

If a simple M4 model matches a complex M4 model, prefer the simpler model.

---

# F4 — M3 Feature and Interaction Confirmation

Only preregistered interactions may enter locked confirmation testing.

Current hypotheses include MLB-INT-001 through MLB-INT-005.

Test interactions individually against the same frozen base model before evaluating a combined interaction model.

A failed locked hypothesis is recorded as rejected.

Do not change the expression slightly and reuse the same confirmation data.

A materially new hypothesis requires:

- a new hypothesis ID;
- a new immutable contract;
- future unseen confirmation data.

Use SHAP or other explainability tools only after predictive qualification.

Do not use SHAP to mine the locked test set for new hypotheses.

---

# F5 — Constrained OOF Ensemble

M5 unlocks only after qualified M3/M4 candidates exist.

Only genuine out-of-fold predictions may be used as meta-model inputs.

Use a simple regularized ensemble.

Do not force equal model weights.

Do not force every upstream model to receive a positive weight.

The ensemble exists to improve generalization, not to validate earlier modeling effort.

Evaluate M5 against M0 using the same common sample and the full Phase F promotion battery.

---

# F6 — MLB Spread Reference Implementation

After the MLB totals methodology is established, replicate the same framework for MLB spreads.

Permanent sign convention:

Margin = HomeScore − AwayScore

MarketImpliedHomeMargin = −HomeSpreadLine

Residual:

R_margin = ActualMargin − MarketImpliedHomeMargin.

Do not create an unrelated spread-specific scientific framework.

Reuse market state, PIT rules, residual methodology, calibration, inference, and promotion gates.

---

# F7 — Multi-Sport Replication

After the MLB reference implementation is stable, replicate the methodology in:

- WNBA;
- NBA;
- NFL;
- Soccer.

Sport-specific structural features may differ.

Scientific evaluation methodology does not.

Every sport must independently demonstrate market-relative incremental information.

Success in MLB does not qualify another sport.

---

# Five-Dimensional Qualification Battery

Every candidate must be evaluated across:

## Continuous
- residual MAE;
- RMSE;
- unconditional bias.

## Market Relative
- Δ Brier versus M0;
- Δ NLL/log loss versus M0;
- CLV line;
- CLV price.

## Calibration
- ECE;
- calibration slope;
- calibration intercept;
- interval coverage.

## Economic
- executable ROI;
- date-clustered bootstrap ROI CI;
- profit factor;
- maximum drawdown.

## Stability
- season partitions;
- rolling temporal partitions;
- line buckets;
- market-dispersion regimes;
- favorite/underdog or Over/Under direction when adequately sampled.

Small regimes must be classified as `INSUFFICIENT_EVIDENCE`, not pass or fail.

---

# Data and Leakage Rules

All features must be reproducible at the decision timestamp.

Require:

observed_at_utc <= as_of_utc < event_start_utc

for decision-time information.

Closing information is evaluation-only.

Actual outcomes are target-only.

Provider revisions observed after the decision timestamp cannot modify a historical decision snapshot.

Do not silently fill missing advanced features with plausible neutral values.

Use explicit missing/fallback indicators where required.

---

# Raw Data Preservation

Raw provider payloads must remain immutable once archived.

Derived schemas may evolve.

Maintain:

Raw → Normalized → PIT Snapshot → Feature Table → Model.

Never wire external API responses directly into a production model without an archived normalized/PIT layer.

---

# Experiment Artifacts

Every experiment must create:

`manifest.json`

`metrics.json`

`report.md`

The manifest must include at minimum:

- experiment ID;
- code commit SHA;
- dataset snapshot hash;
- market-state version;
- hypothesis-contract hash if applicable;
- feature schema version;
- training period;
- validation period;
- test period;
- random seed;
- sample sizes;
- execution timestamp.

Metrics must remain machine-readable.

Reports may interpret results but must not overwrite the underlying metrics.

---

# Testing Requirements

After research code changes, run the relevant focused tests.

Before declaring a phase/gate complete, run:

- full pytest suite;
- mypy;
- ruff.

Do not claim repository-wide verification from only targeted tests.

---

# Production Isolation

Research success does not automatically modify production.

Unless a separate explicit production-promotion request is authorized:

- do not change current champions;
- do not change fallback behavior;
- do not enable automated orders;
- do not change production exposure or unit logic;
- do not convert a research result directly into serving behavior.

# Formal Failure Router

When an evaluation fails or displays an adverse condition, the state machine routes the investigation deterministically:

| Failure Mode | Empirical Indicator | State Machine Action |
|---|---|---|
| `FAIL_DATA` | PIT violations > 0, match failures, corrupted timestamps | Hard stop. Audit and fix raw parser/warehouse invariants. |
| `FAIL_LEVEL_ONLY` | $\beta_{within} \le 0$ or $MAE_{M4-1} \ge MAE_{M0b}$ | Model does not discriminate matchups. Improve domain features. Do NOT unlock M4-2. |
| `FAIL_PROBABILITY` | Continuous $\beta_{within} > 0$ and $MAE$ improves, but Brier/NLL degrades | Mean prediction works, but distribution fails. Open F2 distribution research only. |
| `FAIL_STABILITY` | Pooled passes, but permutation $p \ge 0.05$ or a season flips sign | Investigate temporal regime shifts or sample variance. |
| `FAIL_ECONOMIC` | Prediction metrics pass, but execution simulation fails (slippage/vig) | Keep predictive research distinct from execution policy research. |
| `INSUFFICIENT_EVIDENCE` | $N < 1000$ or $D < 100$ or $S < 2$ | Continue historical multi-season backfill and prospective capture via Data Track. |
| `PASS` | All 7 gate criteria satisfied on pooled multi-season sample | Unlock F2 Distribution Modeling stage. |

---

# Default Behavior After Every Completed Experiment

After obtaining a result:

1. Validate data integrity.
2. Generate experiment artifacts.
3. Evaluate the currently frozen gate.
4. Classify the result.
5. Update machine-readable Phase F state.
6. Update the factual research status documentation.
7. Determine the next eligible action.
8. Continue automatically if no formal stop condition has been reached.

Do not ask what to do next when the Phase F state machine already defines the next action.

Do not optimize the process toward a desired conclusion.

Negative, null, and insufficient-evidence results are valid research outcomes.
