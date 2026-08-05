# Model Benchmark Report

**Date:** 2026-08-05
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

| Sport | Model | Features | Status | Brier | ECE | LogLoss |
|-------|-------|----------|--------|-------|-----|---------|
| MLB | two-head-v1 | Rolling scoreboard averages (10-game) | RESEARCH_ONLY | 0.260 | 0.207 | 0.714 |

**Note:** Only MLB has a rebuild challenger. All other sports are pending collector completion.

## Common characteristics across all models

1. **Thin features**: Most models use 2-7 features, predominantly Elo-based
2. **No player-level data**: No pitcher quality, lineup strength, player availability (except WNBA)
3. **No market-residual model**: Sports probability and market price are not modeled separately
4. **No out-of-fold calibration**: Probability estimates are uncalibrated or calibrated on training data
5. **Hypothetical economics**: P&L uses -110 default odds when executable quotes are unavailable
6. **Unified scoring**: All models score moneyline only (except MLB with spread/total)

## Spec-required challenger types (Part 2-C)

For each sport, the spec requires:

| # | Type | MLB | NBA | WNBA | NFL | Soccer | Tennis | Esports |
|---|---|-----|-----|------|-----|--------|--------|---------|
| 1 | Transparent control | ✓ (incumbent) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 2 | Sport-specific statistical | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 3 | Regularized linear | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 4 | Nonlinear sklearn | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 5 | XGBoost | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 6 | Out-of-fold ensemble | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 7 | Independently fitted calibrator | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |

## Verdict

The incumbent models serve as valid controls (requirement #1 for every sport). No sport has requirements #2-7 completed. The rebuild MLB two-head model is the only active challenger and is a baseline only — it does not clear the predictive qualification gate (Brier > 0.25, ECE > 0.10).

The full benchmark table (model_benchmark.parquet) cannot be produced until challenger models are trained for at least one additional sport beyond MLB.
