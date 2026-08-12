# Predictive Report — Rebuild Platform

**Generated**: 2026-08-07 | **Branch**: rebuild/clean-slate-v1 @ `e03688f` | **Tests**: 885 pass, 1 skipped

## Status

MLB is the one sport with a real, trained, evaluated model — see
`outputs/rebuild/model_cards/mlb-two-head-v1.md` for full detail. Held-out
predictive quality is genuinely **RESEARCH_ONLY / inconclusive** (n=18-21,
accuracy 0.38-0.44 on a coin-flip-adjacent sample), not a pass. Every other
sport below remains untrained architecture, correctly out of scope per
CLAUDE.md's own sequencing (MLB must clear its foundation gate first) —
this section of the report is otherwise unchanged from the 2026-08-05
version since no work has occurred there.

## Model Inventory

| Model | Module | Method | Features | Status |
|---|---|---|---|---|
| MLB two-head (real features) | `models/__init__.py` | RunIntensity (HGBM) + RunDifferential (ElasticNet) → Poisson sim | Real Statcast starter/bullpen/park/weather, 126 real matched games | **Trained, RESEARCH_ONLY** — see model card |
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

## Real Predictive Quality — MLB (the only sport with a real result)

| Metric | Target | Real held-out result (n=21, consumed once) |
|---|---|---|
| Log loss | < baseline model | 0.8652 (baseline 0.693 — worse) |
| Brier | < 0.25 | 0.3211 (0.2832 on n=18 quality-filtered) |
| ECE | < 0.05 | 0.2638 (poorly calibrated at this sample size) |
| Accuracy | > 0.50 | 0.381 (0.444 quality-filtered) |
| Coverage | > 80% of events | 126/150 completed games matched (84%) |
| PIT violations | 0 | 0 — `point_in_time_join()`'s hard assertion + `mlb_features.py`'s own strict-inequality filtering, both tested |

Real split (`outputs/rebuild/mlb_split_manifest.json`): train 84 games
(2026-07-26 to 2026-08-01), calibration 21 games (2026-08-01 to
2026-08-02), final test 21 games (2026-08-02 to 2026-08-04),
`final_test_consumed=true`. With n=18-21 the standard error is ~10-11% —
this result is **inconclusive**, not evidence of either real skill or a
broken model. More real backfill days is the stated highest-leverage next
step, not further feature or ensemble work.

**Not yet built for MLB**: the multi-model-family OOF ensemble Part 2
specifies (control + statistical distribution + regularized linear +
HistGradientBoosting + XGBoost challengers, combined via chronological
OOF stacking) — only the one two-head architecture above is trained.
`conservative_probability`'s `model_disagreement` component is
consequently unavailable (see model card).

## Feature Ablation Groups (MLB)

10 predeclared groups: elo_ratings, trend_momentum, starter_quality, bullpen,
lineup_quality, park_factors, weather, schedule, player_availability, clean_rates.
Isolated and cumulative ablation via chronological validation ready to run —
not yet run against the real feature set above.
