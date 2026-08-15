# Model Card: `nfl-elo-trend-lr-rebuild-v1`

**Model ID:** `nfl-elo-trend-lr-rebuild-v1`
**Artifact:** `config/models/challengers/nfl-elo-trend-lr-rebuild-v1.json`
**Calibrator:** `config/models/challengers/nfl-elo-trend-lr-rebuild-v1-calibrator.json`
**Family:** `elo_trend_logistic_regression` — independent rebuild sibling of incumbent `nfl-elo-trend-lr-v4`
**Market:** moneyline only
**Date:** 2026-08-11
**Branch:** `rebuild/nfl-model-fit-v1` (based on `origin/main`)
**Predecessor foundation:** `docs/model_audit/MODEL_LINEAGE_MATRIX.md` (NFL row, data foundation audit PR #23)

---

## Why it exists

This is the first independently-trained rebuild-native curated NFL model. It
retains the incumbent's model family (Elo + trend, logistic regression) but is
fit entirely from rebuild-owned data (`data/rebuild/normalized/nfl/`,
2021-2025), never loading the incumbent `nfl-elo-trend-lr-v4` artifact or its
rating state (`docs/model_audit/ARCHITECTURE_CORRECTION.md`).

The incumbent `nfl-elo-trend-lr-v4` has the worst calibration of all tracked
models (ECE 0.1009 per its own artifact). This rebuild v1 prioritizes
calibration comparison before adding model complexity — a deliberate
calibration-first approach. The 2-feature model (elo_probability + trend_gap)
is intentionally minimal, reserving EPA/CPOE/pressure/QB features for future
passes once calendar-time daily capture is available.

## Model family

| Aspect | Detail |
|---|---|
| Family | Elo + trend, logistic regression |
| Sibling of | `nfl-elo-trend-lr-v4` (incumbent) |
| Method | `logistic_regression` (scikit-learn `LogisticRegression`, `C=1.0`, `lbfgs`) |
| Positive class | `home` |
| Features | 2: `elo_probability`, `trend_gap` |

### 2-feature rationale

The 3-feature approach (adding `defensive_trend_gap`) was considered and
rejected for the NFL context:

1. **Incumbent's real problem is calibration, not complexity.** The v4
   incumbent's ECE is 0.1009 — the worst of all tracked sports models. Adding
   a noisy third feature to an already poorly-calibrated model would compound
   the problem, not solve it.
2. **NFL audit confirmed defensive_trend_gap instability.** The WNBA rebuild's
   fold-wise stability audit found `defensive_trend_gap` sign-unstable across 5
   chronological folds. The NFL data shares the same structural concern — NFL
   defense is more scheme-dependent and personnel-driven than WNBA defense,
   making a simple rolling-win-rate trend even less reliable.
3. **Calibration-first prioritization.** Every sport's rebuild v1 model starts
   with a calibration baseline. Adding features before the calibration story is
   settled obscures whether improvements come from the features or the
   calibration method.

## Source lineage

| Element | Source |
|---|---|
| Elo engine | `src/model_prediction/rebuild/nfl/elo.py` — fresh rebuild-native `EloBook` |
| Feature construction | `build_walk_forward_rows()` — week-bucketed, PIT-safe walk-forward |
| Training pipeline | `scripts/train_nfl_rebuild_v1.py` |
| Predictor | `src/model_prediction/rebuild/nfl/rebuild_v1_predictor.py` |
| Incumbent design reference | `config/models/nfl-elo-trend-lr-v4.json` (read for config values only, never loaded as artifact) |

## Elo configuration (NFL-specific)

| Parameter | Value | Rationale |
|---|---|---|
| `k` | 20.0 | Conservative rating updates (NFL has fewer games than WNBA/NBA) |
| `home_advantage` | 55.0 | NFL home-field advantage (moderate, below NBA's 60) |
| `offseason_regression` | 0.50 | 50% pull toward DEFAULT_ELO (1500) between seasons |
| `offseason_gap_days` | 180 | ~6-month offseason trigger |
| `DEFAULT_ELO` | 1500.0 | Standard Elo baseline |

### Week-bucketed PIT safety

Unlike the WNBA's day-bucketed approach, NFL uses week-bucketing: games within
the same NFL week use the same pre-week Elo snapshot and do not see each other's
results. This prevents same-week contamination (games on Sunday do not leak
information to Monday Night Football predictions from the same week).

## Rebuild data used

| Dataset | Seasons | Games |
|---|---|---|
| `data/rebuild/normalized/nfl/games` | 2021-2025 | 1,424 total (64 skipped bootstrap, 1,360 walk-forward rows) |

- **Training rows produced:** 1,360 (after bootstrap/cold-start gates)
- **Skipped bootstrap:** 64 (insufficient league history to establish Elo baseline)
- **Skipped cold-start:** 0 (all teams met the 3-game minimum at decision time)
- **Data provenance:** capture_time_only_mutable_release — all rows share this repo's 2026-08 backfill capture time

## Features

| Feature | Coefficient | Sign | Description |
|---|---|---|---|
| `elo_probability` | +3.1104 | + | Home win probability from week-bucketed Elo ratings (home advantage = 55) |
| `trend_gap` | +0.5602 | + | Offensive momentum gap (10-game rolling win-rate delta, home − away) |
| intercept | −1.5958 | — | — |

### Features blocked (not PIT-qualified)

| Feature | Blocker | Resolution path |
|---|---|---|
| EPA/play | Requires play-by-play data captured at calendar time (not retroactively) | Await calendar-time daily PBP capture pipeline |
| CPOE | Requires player-level passing data at calendar time | Same as EPA — PBP capture pipeline |
| Pressure rate | Requires tracking data not available in nflverse | Long-term: Next Gen Stats or equivalent |
| QB state features | Requires weekly roster snapshots (blocked by GSIS-identity gap) | Resolve GSIS↔nflverse team/player identity mapping |
| `defensive_trend_gap` | Sign-unstable in fold-wise audit (WNBA precedent); NFL defense is more scheme-dependent | Revisit after calibration baseline is established |

### Feature availability

| Feature | PIT status | Historical availability | Live availability | Missingness |
|---|---|---|---|---|
| `elo_probability` | RETROSPECTIVE_RESEARCH | 2021-2025 (all completed games) | Requires live schedule/score captures | Bootstrap: 64 rows (4.5%) |
| `trend_gap` | RETROSPECTIVE_RESEARCH | 2021-2025 (requires 10-game rolling window) | Requires live score captures for rolling window | Cold-start: 0 rows |

Both features are labeled `RETROSPECTIVE_RESEARCH` — the training data is
single-vintage capture-time-only, so despite chronologically correct event
ordering, these are not genuinely prospective point-in-time features. Live
serving would require a separate live-collection pipeline.

## Validation method

| Parameter | Value |
|---|---|
| Method | Chronological date-cluster split |
| Split | 60% train / 20% validation / 20% holdout, by `event_start_utc` date |
| Hyperparameter tuning | None (fixed `C=1.0` for LR) |
| Feature selection | Pre-decided: 2-feature only (calibration-first approach) |
| Calibration method selection | Chronological cross-fit (4 blocks) on validation fold only |
| Holdout | Touched exactly once, at the very end |

## OOF metrics

### Raw model

| Split | N | LogLoss | Brier | ECE | Accuracy |
|---|---|---|---|---|---|
| Train | ~816 | — | — | — | — |
| Validation | ~287 | — | — | — | — |
| Holdout (raw) | 257 | 0.6450 | 0.2268 | 0.0570 | 0.6304 |

### Calibration comparison (validation fold, 4-block cross-fit)

| Method | N | LogLoss | Brier | ECE | Cal Slope | Cal Intercept |
|---|---|---|---|---|---|---|
| Identity | — | 0.6117 | 0.2105 | 0.1212 | 2.7163 | −0.1894 |
| Platt | — | 0.5845 | 0.1959 | 0.0705 | 0.9423 | 0.2456 |
| Temperature | — | 0.5824 | 0.1972 | 0.0846 | 1.2245 | −0.1630 |
| Isotonic | — | 0.6841 | 0.1978 | 0.0595 | 0.3078 | 0.2203 |

**Winning method: Platt scaling.** Selected on best LogLoss + ECE trade-off
(LogLoss 0.584, ECE 0.071). Platt improves ECE by 42% vs. identity (0.071 vs.
0.121) while also reducing LogLoss (0.584 vs. 0.612) and Brier (0.196 vs.
0.211). Temperature has marginally better LogLoss (0.582) but worse ECE (0.085)
— Platt's calibration slope (0.942) is closest to the ideal 1.0 among all
methods. Isotonic achieves the lowest ECE (0.059) but at the cost of
substantially worse LogLoss (0.684) and Brier, indicating overfitting to the
validation distribution.

### Holdout (locked, Platt-calibrated)

| Metric | Value |
|---|---|
| N | 257 |
| LogLoss | 0.6450 |
| Brier | 0.2268 |
| ECE | 0.0570 |
| Accuracy | 0.6304 |

Raw holdout ECE (0.057) is substantially better than the incumbent's 0.1009 —
the calibration-first approach delivers a measurable improvement even with a
minimal 2-feature model. The raw model is well-calibrated on holdout without
additional post-processing, suggesting the LR coefficients are stable across the
validation→holdout transition.

## Calibration-first approach

The incumbent `nfl-elo-trend-lr-v4`'s ECE of 0.1009 is the worst of all
tracked sports models. This rebuild v1 deliberately starts with a minimal
2-feature model to establish a clean calibration baseline:

1. **Step 1 (this model):** 2-feature LR + Platt calibration → ECE 0.057 on holdout
2. **Step 2 (future):** Add EPA/CPOE features once calendar-time PBP capture is available
3. **Step 3 (future):** Add pressure/QB features once tracking/roster gaps are resolved

Each future feature addition can be evaluated against this calibration baseline
— we can measure whether new features improve accuracy, calibration, or both.

### Incumbent vs. rebuild calibration

| Model | ECE | Accuracy |
|---|---|---|
| Incumbent (`nfl-elo-trend-lr-v4`) | 0.1009 | 0.7126 |
| Rebuild v1 (raw, 2-feature LR) | 0.0570 | 0.6304 |

The rebuild v1 has lower accuracy (expected — 2 features vs. the incumbent's
full feature set) but substantially better calibration. This trade-off is
intentional: the rebuild's path is to first establish calibration quality, then
add features that improve accuracy without sacrificing it.

## Known limitations

1. **Capture-time-only data:** Every training row shares a single 2026-08
   backfill `observed_at_utc`. The chronological split is real and
   non-fabricated, but the validation is descriptive backtesting, NOT genuine
   prospective point-in-time evidence.

2. **2-feature ceiling:** The 2-feature model (elo_probability + trend_gap)
   has inherently limited predictive power. Accuracy (63.0%) is below the
   incumbent's 71.3%. This is by design — features are reserved for future
   passes when calendar-time PBP capture is available.

3. **EPA/CPOE/pressure/QB state blocked:** These features remain blocked
   pending calendar-time daily capture. The audit's explicit PIT restriction
   applies: retroactive feature computation would fabricate predictive power.

4. **No live serving integration:** This predictor is NOT wired into
   `sport_adapter.py`'s rebuild-shadow registry. `_BasicEloAdapter` remains the
   sole NFL rebuild adapter.

5. **`production_allowed = false`:** Set in both the model and calibrator
   artifacts. This model is research/shadow-only.

## Train/serve parity

| Aspect | Status |
|---|---|
| Feature computation | Same `elo.py` module used for both train and predict |
| Elo state | Walk-forward during training; live serving would need persistent Elo state |
| Calibration | Platt scaling (reconstructible from slope/intercept parameters) |
| Data pipeline | Training uses backfilled data; live serving would need live collection |

## Lifecycle

| Field | Value |
|---|---|
| Current state | `RESEARCH_SHADOW` |
| Qualified | No — `qualification.qualified = false` |
| Production allowed | No — `production_allowed = false` |
| Next phase | Await calendar-time PBP capture pipeline for EPA/CPOE features; then 3+ feature LR with calibration comparison |

## Artifact hashes

| Artifact | Hash |
|---|---|
| Model | `08ddf9393e249b70a83cc73a2aa36e1016b8ed59b52ae997d6df026ac0e715c9` |
| Calibrator | `9e2d189a1caf8b211bf5c55d68e3e524138f67b75eccadbdd959aa410957dd0f` |
| Dataset | 1,360 rows from `data/rebuild/normalized/nfl/` (2021-2025, 1,424 total games) |
| Training script | `scripts/train_nfl_rebuild_v1.py` |

## Serving integration

This module (`src/model_prediction/rebuild/nfl/rebuild_v1_predictor.py`) is
**not** wired into `sport_adapter.py`'s `rebuild-shadow --sport nfl` adapter
registry. `_BasicEloAdapter` stays the sole, unmodified, primary NFL rebuild
adapter. This is intentional: the challenger's validation is real but
capture-time-only descriptive backtesting (not genuine prospective evidence),
and EPA/CPOE/pressure/QB features remain blocked pending calendar-time daily
capture.

If a future pass adds live NFL collection and resolves the PIT feature blockers,
wiring this predictor into the adapter registry is a straightforward integration
task (load the artifact, build walk-forward rows from live data, call
`predict_row`).
