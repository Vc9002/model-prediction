# MLB Opponent-Adjusted Trend Score v2

## Status

Rejected backtest challenger. Zero units. Not used by the active forward path.
Research continues through new versions; this result does not freeze MLB model
development.

## Forecast design

The model constructs opponent-adjusted team scoring form at exponentially
weighted half-lives of 3, 10, and 25 games. The blended state changes away and
home expected runs, and one coherent Poisson/Skellam score distribution produces
moneyline, spread, and total probabilities.

Calibration is rolling and chronological. A forecast date can use only earlier
completed games and earlier outcomes. Side selection occurs after calibration.
MLB preseason and All-Star games are excluded.

## Causality check

Every forecast is paired with a long-horizon-only counterfactual. In Q2 2026,
trend changed all 3,612 binary market probabilities by 0.015907 on average. In
the July 1-16 holdout it changed 507 of 510 probabilities by 0.014723 on average.
This proves the trend feature affects forecasts rather than merely labeling
results after the fact.

## Limits

Score history cannot reconstruct point-in-time starters, lineups, bullpen
availability, park conditions, or weather. Synthetic reference lines support
probability diagnostics only and never ROI or CLV. ESPN prices retrieved after
games are hard-labeled `timestamp_valid=false` and are diagnostic only.
