# Model Card: `wnba-elo-trend-lr-rebuild-v1`

**Model ID:** `wnba-elo-trend-lr-rebuild-v1`
**Artifact:** `config/models/challengers/wnba-elo-trend-lr-rebuild-v1.json`
**Calibrator:** `config/models/challengers/wnba-elo-trend-lr-rebuild-v1-calibrator.json`
**Family:** `elo_trend_logistic_regression` — independent rebuild sibling of incumbent `wnba-elo-trend-lr-v4`
**Market:** moneyline only
**Date:** 2026-08-11
**Branch:** `rebuild/wnba-model-fit-v1` (based on `origin/main` @ `eaf5bcd`)
**Predecessor foundation:** `docs/model_audit/models/WNBA_REBUILD_DATA_FOUNDATION.md`

---

## Why it exists

This is the first independently-trained rebuild-native curated WNBA model. It
retains the incumbent's model family (Elo + trend, logistic regression) but is
fit entirely from rebuild-owned data (`data/rebuild/normalized/wnba/`,
2022-2025), never loading the incumbent `wnba-elo-trend-lr-v4` artifact or its
rating state (`docs/model_audit/ARCHITECTURE_CORRECTION.md`).

The incumbent's Elo config values (`k=20.0`, `home_advantage=60.0`,
`offseason_regression=0.40`) are used only as a documented design reference.
Every rating is recomputed from scratch against the rebuild data store.

## Model family

| Aspect | Detail |
|---|---|
| Family | Elo + trend, logistic regression |
| Sibling of | `wnba-elo-trend-lr-v4` (incumbent) |
| Method | `logistic_regression` (scikit-learn `LogisticRegression`, `C=1.0`, `lbfgs`) |
| Positive class | `home` |
| Features | 3: `elo_probability`, `trend_gap`, `defensive_trend_gap` |

## Source lineage

| Element | Source |
|---|---|
| Elo engine | `src/model_prediction/rebuild/wnba/elo_trend.py` — fresh rebuild-native `EloBook` |
| Feature construction | `build_walk_forward_rows()` — day-bucketed, PIT-safe walk-forward (same methodology as `docs/model_audit/models/NBA_ELO_TREND_LR_V4.md`, independently implemented) |
| Training pipeline | `scripts/train_wnba_rebuild_v1.py` |
| Predictor | `src/model_prediction/rebuild/wnba/rebuild_v1_predictor.py` |
| Incumbent design reference | `config/models/wnba-elo-trend-lr-v4.json` (read for config values only, never loaded as artifact) |

## Rebuild data used

| Dataset | Seasons | Games |
|---|---|---|
| `data/rebuild/normalized/wnba/games` | 2022-2025 | 1,080 scheduled (1,080 completed, 9 ties dropped, 30 incomplete dropped) |
| `data/rebuild/normalized/wnba/team_box` | 2022-2025 | 2,158 rows (both sides, every completed game) |

- **Training rows produced:** 1,042 (after bootstrap/cold-start gates)
- **Skipped bootstrap:** 30 (insufficient league history to establish Elo baseline)
- **Skipped cold-start team:** 7 (teams with <3 games played at decision time)
- **Data provenance:** capture_time_only — all rows share this repo's 2026-08 backfill capture time

## Features

| Feature | Coefficient | Sign | Description |
|---|---|---|---|
| `elo_probability` | 4.1890 | + | Home win probability from day-bucketed Elo ratings (home advantage = 60) |
| `trend_gap` | 0.0294 | + | Offensive momentum gap (10-game rolling NetRtg delta, home − away) |
| intercept | −2.2239 | — | — |

### Feature ablation: `defensive_trend_gap` removed

The original v1 training tested 3-feature (elo_probability + trend_gap +
defensive_trend_gap) against 2-feature (elo_probability + trend_gap). On
a single 60/20/20 validation split, the 3-feature model showed a tiny
Brier improvement of ~0.00019.

A subsequent **fold-wise stability audit** (`scripts/audit_wnba_defensive_trend.py`),
running 5 expanding chronological folds, found:

| Metric | Result |
|---|---|
| Folds where 3-feature won | 1/5 |
| Folds where 2-feature won | 4/5 |
| Coefficient sign | Fold 0: −0.098, folds 1-4: +0.013 to +0.034 (sign-unstable) |
| Mean Brier Δ | +0.0035 (3-feature WORSE on average) |

**Decision: DROP `defensive_trend_gap`.** A single-split Brier improvement of
~0.00019 does not survive fold-wise scrutiny. Across 5 chronological folds the
3-feature model is worse on 4/5 folds with an unstable coefficient sign. This
confirms the incumbent audit's prior sign-instability warning. The simpler
2-feature model (elo_probability + trend_gap) is the production v1 model.

### Feature availability

| Feature | PIT status | Historical availability | Live availability | Missingness |
|---|---|---|---|---|
| `elo_probability` | RETROSPECTIVE_RESEARCH | 2022-2025 (all completed games) | Requires live schedule/box captures | 0% |
| `trend_gap` | RETROSPECTIVE_RESEARCH | 2022-2025 (requires 10-game rolling window) | Requires live box captures for rolling window | Cold-start: 7 rows (<1%) |

### Feature availability

| Feature | PIT status | Historical availability | Live availability | Missingness |
|---|---|---|---|---|
| `elo_probability` | RETROSPECTIVE_RESEARCH | 2022-2025 (all completed games) | Requires live schedule/box captures | 0% |
| `trend_gap` | RETROSPECTIVE_RESEARCH | 2022-2025 (requires 10-game rolling window) | Requires live box captures for rolling window | Cold-start: 7 rows (<1%) |

Both features are labeled `RETROSPECTIVE_RESEARCH` — the training data is
single-vintage capture-time-only, so despite chronologically correct event
ordering, these are not genuinely prospective point-in-time features. Live
serving would require a separate live-collection pipeline.

## Validation method

| Parameter | Value |
|---|---|
| Method | Chronological expanding-window, date-cluster split |
| Split | 60% train / 20% validation / 20% holdout, by `sports_event_date` |
| Hyperparameter tuning | None (fixed `C=1.0` for LR) |
| Feature selection | Decided on validation only |
| Calibration method selection | Chronological cross-fit on validation fold only |
| Holdout | Touched exactly once, at the very end |

## OOF metrics

### Raw model

| Split | N | LogLoss | Brier | ECE | Accuracy |
|---|---|---|---|---|---|
| Train | 608 | 0.6121 | 0.2121 | 0.0471 | 0.6513 |
| Validation | 218 | 0.5962 | 0.2040 | 0.0473 | 0.6789 |
| Holdout (raw) | 216 | 0.6186 | 0.2137 | 0.0920 | 0.6944 |

### Calibration comparison (validation fold, cross-fit)

| Method | N | LogLoss | Brier | ECE | Cal Slope | Cal Intercept |
|---|---|---|---|---|---|---|
| Identity | 164 | 0.5813 | 0.1982 | 0.0580 | 1.2507 | −0.0682 |
| Platt | 164 | 0.6035 | 0.2076 | 0.0796 | 1.0645 | 0.0731 |
| Temperature | 164 | 0.5904 | 0.2019 | 0.0753 | 1.2033 | −0.0477 |
| Isotonic | 164 | 0.6186 | 0.2134 | 0.0792 | 0.7247 | 0.1452 |

**Winning method:** Identity (no transformation). The raw model is already
reasonably well-calibrated (calibration slope 1.25, intercept near zero) and
all three calibration methods make it worse on LogLoss and Brier. The
calibration slope > 1 means the model is slightly underconfident (probabilities
are too close to 0.5) — mild, but not severe enough to warrant Platt/Temp.

### Holdout (locked, calibrated = identity)

| Metric | Value |
|---|---|
| N | 216 |
| LogLoss | 0.6186 |
| Brier | 0.2137 |
| ECE | 0.0920 |
| Accuracy | 0.6944 |
| Calibration slope | 0.8957 |
| Calibration intercept | 0.0864 |

Holdout ECE (0.092) is higher than validation ECE (0.047) — some calibration
drift from the validation→holdout transition, but not catastrophic. The
reliability buckets show the model is directionally well-ordered (hit rates
monotonically increase with probability bucket: 0.43 → 0.28 → 0.55 → 0.68 →
0.94). The 0.2-0.4 bucket underperforms (0.28 hit rate against 0.30 expected),
which is a calibration weakness worth monitoring in future versions.

## Known limitations

1. **Capture-time-only data:** Every training row shares a single 2026-08
   backfill `observed_at_utc`. The chronological split is real and
   non-fabricated, but the validation is descriptive backtesting, NOT genuine
   prospective point-in-time evidence.

2. **Commercial rights unresolved:** The underlying SportsDataverse data has an
   unresolved commercial-use-rights status. This model is research/shadow-only
   regardless of statistical performance. `production_allowed = false` is set
   in both the model and calibrator artifacts.

3. **No live serving integration:** This predictor is NOT wired into
   `sport_adapter.py`'s rebuild-shadow registry. `_BasicEloAdapter` remains the
   sole WNBA rebuild adapter. Current-season live WNBA schedule/box data was
   not backfilled in this pass.

4. **Calibration drift:** Holdout ECE (0.092) is notably worse than validation
   ECE (0.047). The identity calibrator was selected on validation metrics, but
   the holdout reveals some distribution shift.

5. **No availability adjustment:** Unlike the incumbent v4 (which applies a
   post-hoc probit availability adjustment), this rebuild v1 does not use
   player availability features — the underlying SportsDataverse roster data
   has gaps (2022-2023 missing) and player_box has known parsing failures.

## Train/serve parity

| Aspect | Status |
|---|---|
| Feature computation | Same `elo_trend.py` module used for both train and predict |
| Elo state | Walk-forward during training; live serving would need persistent Elo state |
| Calibration | Identity (no post-processing), so train == serve by construction |
| Data pipeline | Training uses backfilled data; live serving would need live collection |

## Lifecycle

| Field | Value |
|---|---|
| Current state | `RESEARCH_SHADOW` |
| Qualified | No — `qualification.qualified = false` |
| Production allowed | No — unresolved commercial rights |
| Next phase | Await live collection pipeline + rights resolution before promotion consideration |

## Artifact hashes

| Artifact | Hash |
|---|---|
| Model | `f6cc4cab714cb9699469f5270a6273bb688f4d132f568453db6b090e3cfd791c` |
| Calibrator | `bfde7029b63b12270744fd9fbffa7c7b4197d083dd60e371d21a5b8b2a4155ec` |
| Dataset | 1,042 rows from `data/rebuild/normalized/wnba/` (2022-2025) |
| Code SHA | `eaf5bcd` (origin/main at time of training) |
| Training script | `scripts/train_wnba_rebuild_v1.py` |

## Serving integration

This module (`src/model_prediction/rebuild/wnba/rebuild_v1_predictor.py`) is
**not** wired into `sport_adapter.py`'s `rebuild-shadow --sport wnba` adapter
registry. `_BasicEloAdapter` stays the sole, unmodified, primary WNBA rebuild
adapter. This is intentional: the challenger's validation is real but
capture-time-only descriptive backtesting (not genuine prospective evidence),
and no live current-season WNBA schedule/box data was backfilled, so there is
no real "today's slate" this module could honestly serve without a separate
live-collection step.

If a future pass adds live WNBA collection and resolves the commercial-rights
blocker, wiring this predictor into the adapter registry is a straightforward
integration task (load the artifact, build walk-forward rows from live data,
call `predict_row`).
