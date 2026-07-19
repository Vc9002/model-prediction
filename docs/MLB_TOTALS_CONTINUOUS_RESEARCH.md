# MLB totals continuous research plan

## Status

Continuous, versioned, zero-unit research. No totals model is frozen. The current
forward model remains a comparison baseline only; it receives no special
protection from replacement.

## Evidence

Totals are the clearest failure cohort. The earlier 162-game control produced
0.26210 model Brier versus 0.24982 market Brier and -13.43% flat ROI. Trend Score
v2 did not repair the problem: its July 1-16 Brier was 0.252391 versus a 0.249951
structural baseline, and its reconstructed-price July 1-12 sample was worse than
the no-vig market with negative ROI at every meaningful edge threshold.

## Experiment sequence

1. Build a totals-specific residual layer from point-in-time run-environment
   inputs, decision total, no-vig market probability, and missingness flags.
2. If that fails, build a separate absolute run-intensity head and reconcile it
   with relative team strength into one coherent score distribution.
3. Evaluate every version with chronological training, walk-forward validation,
   and a locked untouched holdout.

A standalone binary over/under classifier is prohibited because it can disagree
with the same model's moneyline and run-line score distribution.

## Evaluation requirements

Report Brier, log loss, ECE, reliability buckets, no-vig market Brier, CLV,
price-aware ROI, sample size, and confidence intervals. Break out availability
cohorts for starters, lineups, bullpen, park, weather, travel/rest, and market
timestamps. Reconstructed postgame prices are diagnostic only and cannot
qualify a candidate.

Research never pauses at a sample threshold. Promotion does: a candidate must
beat both the declared incumbent baseline and market baseline on untouched,
timestamp-valid data with enough observations to rule out a one-week anomaly.
