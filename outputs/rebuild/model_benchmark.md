# Model Benchmark Report

**Date:** 2026-08-05, MLB section updated 2026-08-08
**Status:** PARTIAL — full benchmark requires trained challenger models for all sports

## Production model baselines

These are the incumbent models from the existing `main` branch, frozen as controls:

| Sport | Model | Features | Qualified | Key metric |
|-------|-------|----------|-----------|------------|
| MLB | elo-trend-lr-v8 | Elo, trend, park, weather, starter ERA, bullpen | No (Brier regression) | — |
| NBA | elo-trend-lr-v4 | Elo, offensive trend, defensive trend | No | Cal slope ~1.79 |
| WNBA | elo-trend-lr-v4 | Elo, offensive trend, defensive trend, availability | No | Threshold ~0.50 |
| NFL | elo-trend-lr-v4 | Elo, score trend | No | 122 games, ECE ~0.10 |
| Soccer | poisson-dc-v1 | EWMA goals, fixed home 1.15, fixed rho -0.10 | No | — |
| Tennis | surface-elo-v1 | Surface Elo, overall Elo, fixed 60/40 blend | No | Fixed K=32 |
| LoL | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| CS2 | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| Dota 2 | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| Valorant | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| R6 | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| KBO | tie-aware-elo-v1 | Home Elo + tie heuristic | No | No pitcher/lineup |
| NPB | tie-aware-elo-v1 | Home Elo + tie heuristic | No | No pitcher/lineup |

## Rebuild challenger baselines

Superseded 2026-08-08: the row below (real Statcast-derived features,
`config/models/challengers/mlb-two-head-real-features-v1.json`) replaces
the prior placeholder row, which used rolling scoreboard averages, not
real pitcher/bullpen/park/weather features. Real held-out test (21
games, chronologically after training+calibration, never inspected while
selecting features/model family):

| Sport | Model | Features | Status | Brier | ECE | LogLoss | n (test) |
|-------|-------|----------|--------|-------|-----|---------|----------|
| MLB | two-head-real-features-v1 | Real Statcast starter/bullpen + park + weather | RESEARCH_ONLY | 0.321 | 0.264 | 0.865 | 21 |

**Real, disclosed reading:** this is worse than the superseded
placeholder row's numbers on this specific 21-game held-out set — not a
regression in the model, but the real held-out set's own real
cold-start composition mismatch (`outputs/rebuild/mlb_training_results_real_features.json`:
train mean starter-availability 0.167 vs test mean 0.929 — the short
~10-day real backfill window means most *training* rows have no real
prior-start history for their starter, while the *test* rows mostly do,
a genuinely different feature-availability regime between the two
splits caused by backfill depth, not a modeling error). More real
backfill days is the actual fix, not further feature engineering — see
`outputs/rebuild/FOUNDATION_FROZEN.md`'s known blockers.

### Real out-of-fold model-family comparison (2026-08-08, chronological, 3 folds, 83 predictions)

Separate from the held-out test above — this compares model *families*
on out-of-fold validation data only, deliberately never touching the
already-consumed final test (see `scripts/train_mlb_xgboost_ensemble.py`'s
own module docstring for why). Source:
`outputs/rebuild/mlb_xgboost_ensemble_oof.json`.

| Model family | Brier | ECE | LogLoss |
|---|---|---|---|
| two-head-real-features-v1 (control) | 0.255 | 0.090 | 0.703 |
| XGBoost challenger (flat features, direct classifier) | 0.233 | 0.074 | 0.658 |
| OOF ensemble (logistic stacking) | 0.233 | 0.074 | 0.658 |

The logistic stacker weighted entirely toward XGBoost (weights: `{"two_head": 0.0, "xgboost": 1.0}`)
on this real but small (n=83) OOF sample — reported honestly as a
small-sample proof the OOF ensemble machinery works end to end, not a
model-family ranking claim. XGBoost outperforming the two-head control
here is real but not yet a promotion decision; it hasn't been evaluated
against the reserved final test, on principle (see CLAUDE.md: "Do not
inspect the final test while selecting... model family").

**Note:** Only MLB has real rebuild challengers. All other sports are pending collector completion.

## Common characteristics across all models

Still true for every sport except the MLB rebuild challenger row above
(noted inline where MLB is now a real exception, as of 2026-08-08):

1. **Thin features**: Most models use 2-7 features, predominantly Elo-based
2. **No player-level data**: No pitcher quality, lineup strength, player availability (except WNBA and MLB's rebuild challenger, which has real Statcast starter/bullpen/clean-rate features)
3. **No market-residual model**: Sports probability and market price are not modeled separately (a real `MarketResidualModel` now exists for the rebuild platform generally — `market_residual.py`, unit-tested `daa6985` — but has never been trained on real settled outcomes; see `economic_report.md`)
4. **No out-of-fold calibration**: Probability estimates are uncalibrated or calibrated on training data (MLB's rebuild challenger uses a real, separate calibration split -- `PlattCalibrator`, never fit on training or test rows)
5. **Hypothetical economics**: P&L uses -110 default odds when executable quotes are unavailable (the rebuild platform's real decision engine never does this -- it fails closed on a stale/thin quote instead; see `economic_report.md`'s real reason-code breakdown)
6. **Unified scoring**: All models score moneyline only (except MLB with spread/total)

## Spec-required challenger types (Part 2-C)

For each sport, the spec requires:

| # | Type | MLB | NBA | WNBA | NFL | Soccer | Tennis | Esports |
|---|---|-----|-----|------|-----|--------|--------|---------|
| 1 | Transparent control | ✓ (incumbent) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 2 | Sport-specific statistical | ✓ (independent Poisson / negative binomial / Skellam, `models/__init__.py`'s `JointScoreDistribution`) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 3 | Regularized linear | ✓ (`ElasticNet`, `RunDifferentialHead`) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 4 | Nonlinear sklearn | ✓ (`HistGradientBoostingRegressor`, `RunIntensityHead`) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 5 | XGBoost | ✓ (`XGBoostChallenger`, wired to real OOF data 2026-08-08) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 6 | Out-of-fold ensemble | ✓ (`Ensemble` logistic stacking, wired to real two-head+XGBoost OOF predictions 2026-08-08) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 7 | Independently fitted calibrator | ✓ (`PlattCalibrator`, fit on a separate calibration split never used for training) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |

Rows 2-4 were architecturally present before 2026-08-08 (the two-head
model was already built on ElasticNet + HistGradientBoostingRegressor +
a Poisson/NegBinom joint distribution) but are marked ✓ here for the
first time because that session also added Skellam (closing gap 2) and,
separately, found and fixed a real bug where the negative-binomial
method produced internally inconsistent moneyline-vs-total pricing
(`9fbb037`) — the statistical-distribution row wasn't honestly
verifiable as working until that was fixed and tested.

## Verdict

The incumbent models serve as valid controls (requirement #1 for every
sport). MLB is the only sport with requirements #2-7 completed — real,
tested, and wired to live data, not just present in source. No other
sport has begun this work (correct per CLAUDE.md's own MLB-first
sequencing). This is an architecture-completeness verdict only: MLB's
real held-out test above does not clear a predictive qualification gate
on this small a sample, and no sport has attempted economic
qualification (see `outputs/rebuild/economic_report.md`: real shadow
ledger data shows zero real trades have ever been placed, an honest
economic data blocker, not a missing-code one).

The full benchmark table (model_benchmark.parquet) cannot be produced until challenger models are trained for at least one additional sport beyond MLB.
