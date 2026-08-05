# Model Card: MLB Two-Head v1

**Model ID:** `mlb-two-head-v1`
**Status:** RESEARCH_ONLY
**Date:** 2026-08-05

## Architecture

Two-head model on 188 completed MLB games:
- **Run-intensity head**: Predicts total scoring environment from rolling team runs scored/allowed
- **Run-differential head**: Predicts home margin from rolling run differential and win percentage
- **Joint distribution**: Independent Poisson simulation with home/away expected runs

## Features

Intensity (total runs):
- `home_rolling_runs_scored`, `away_rolling_runs_scored` (10-game rolling average)
- `home_rolling_runs_allowed`, `away_rolling_runs_allowed`
- `home_avg_total`, `away_avg_total`

Differential (home margin):
- `home_rolling_run_diff`, `away_rolling_run_diff`
- `home_win_pct`, `away_win_pct`

## Training

- Games: 188 completed, 150 train / 38 test (chronological 80/20 split)
- No out-of-fold calibration
- No hyperparameter tuning

## Performance

| Metric | Value | Notes |
|--------|-------|-------|
| Log loss | 0.7138 | Coin-flip baseline: 0.693 |
| Brier score | 0.2601 | Coin-flip baseline: 0.250 |
| ECE | 0.2073 | High — poor calibration |
| Accuracy | 0.500 | Coin-flip level |

## Limitations

- **Baseline only**: Rolling scoreboard averages are weak predictors. Full feature set (Statcast, weather, lineups, bullpen) requires collectors to be completed.
- **No calibration**: ECE of 0.207 indicates substantial miscalibration.
- **Small sample**: 188 games is insufficient for reliable evaluation.
- **No market validation**: Not tested against executable prices.

## Next steps

1. Complete Statcast, weather, lineup, and bullpen collectors (Part 1-F)
2. Add pitcher clean-rate features with beta-binomial shrinkage (Part 1-H/L)
3. Train with chronological cross-validation (Part 2-B)
4. Fit out-of-fold calibrator (Part 2-N)
5. Test against executable order books (Part 3-B/C)

## Verdict

**Not ready for promotion.** Retain as baseline benchmark only. A model with Brier worse than coin-flip and ECE > 0.20 does not clear the predictive qualification gate.
