# Predictive Report — Rebuild Platform

**Generated**: 2026-08-05 | **Branch**: rebuild/clean-slate-v1 | **Tests**: 49 pass

## Status

The rebuild platform architecture is complete. All 7 sport models are built and importable.
No models have been trained on real data — the collector backfill has not yet been run.
This report documents the architecture and expected behavior when data becomes available.

## Model Inventory

| Model | Module | Method | Features | Status |
|---|---|---|---|---|
| MLB two-head | `models/__init__.py` | RunIntensity (HGBM) + RunDifferential (ElasticNet) → Poisson sim | 10 feature groups, ~40 features | Architecture complete, untrained |
| NBA possessions | `models/basketball.py` | Possessions (Ridge) + 4× Efficiency (HGBM) → normal approx | Pace, Four Factors, projected minutes | Architecture complete |
| WNBA possessions | `models/basketball.py` | Same as NBA with stronger shrinkage | Pace, Four Factors, roster continuity | Architecture complete |
| NFL drive-based | `models/nfl.py` | Drive count (HGBM) + EPA (Ridge) → binomial sim | QB state, EPA, pace, weather | Architecture complete |
| Soccer dynamic DC | `models/soccer.py` | Learned attack/defense via SGD, no hardcoded constants | Dynamic attack/defense, league strength | Architecture complete |
| Tennis serve/return | `models/tennis.py` | Surface Elo (dynamic blend) + serve/return logistic | Surface ratings, serve/return points won | Architecture complete |
| Esports per-title | `models/esports.py` | Per-title Elo with confidence shrinkage, game→series | Rosters, region, tournament, patch | Architecture complete |
| KBO/NPB | `models/kbo_npb.py` | League-specific Poisson simulation | Starter quality, run environment, tie rate | Architecture complete |

## Validation Architecture

- **Nested chronological CV**: Expanding/rolling folds grouped by complete event dates
- **Metrics**: Log loss, Brier, ECE, calibration curves, directional accuracy
- **Bootstrap**: Date-cluster and team-cluster 1,000-sample bootstrap
- **Untouched final test**: Newest 20% of data, locked until final evaluation
- **No random K-fold**: All validation is walk-forward chronological

## Calibration Pipeline

- Platt scaling (sigmoid calibration on out-of-fold predictions)
- Isotonic regression (non-parametric, min 100 samples)
- Temperature scaling (single-parameter, grid search)
- All fitted on data disjoint from base-model training
- Stored as separately hashed, mutually bound artifacts

## Ensemble

- Equal-weight average
- Inverse-log-loss weighting
- Nonnegative logistic stacking on logits
- Stacker sees only out-of-fold predictions

## Expected Predictive Quality (when trained)

| Metric | Target | Current |
|---|---|---|
| Log loss | < baseline model | Not yet trained |
| Brier | < 0.25 | Not yet trained |
| ECE | < 0.05 | Not yet trained |
| Calibration slope | 0.85–1.15 | Not yet trained |
| Coverage | > 80% of events | Architecture supports full coverage |
| PIT violations | 0 | Architecture enforces PIT cutoff |

## Feature Ablation Groups (MLB)

10 predeclared groups: elo_ratings, trend_momentum, starter_quality, bullpen,
lineup_quality, park_factors, weather, schedule, player_availability, clean_rates.
Isolated and cumulative ablation via chronological validation ready to run.
