# MLB research contract

## Non-negotiable research policy

MLB models are never frozen. Any formula, coefficient, calibration, feature,
or selection rule may be replaced by a new version at any time. Every change
must still be reproducible and evaluated chronologically: version the candidate,
run a walk-forward ablation, and compare it on a locked holdout. Failed versions
remain research-only with zero units; rejection triggers the next experiment
rather than protecting the incumbent.

Market prices do not enter the independent score model. A timestamp-valid
decision price may enter a clearly labeled residual or decision layer. Closing
prices are labels. Postgame-retrieved prices are diagnostic only.

## Current model families

- **First Read v0.1:** four manual analyst estimates retained as legacy evidence.
- **Trend Engine v0.2:** coherent MLB score simulation for moneyline, run line,
  and totals. It is reproducible but not qualified.
- **Measured Edge:** current zero-unit forward-research margin and totals heads.
- **Probabilistic Totals v0.8:** rejected research challenger; it did not clear
  calibration, provenance, and economic validation gates.
- **Trend Score v2:** backtest-only opponent-adjusted EWMA challenger. It uses
  half-lives 3/10/25 to alter expected runs before deriving ML, spread, and total
  probabilities from a coherent score distribution.

## Trend Score v2 evaluation

The implementation gap is fixed: trend strength is no longer only a post-hoc
cohort label. A counterfactual long-horizon-only forecast is computed for every
record, and the report measures how often and by how much trend changes the
forecast probability.

The model is not good enough. On 1,204 Q2 2026 games, calibrated Brier improved
only marginally over structural baselines: moneyline 0.249749 vs 0.249984,
spread 0.230043 vs 0.230099, and totals 0.249736 vs 0.250203. On the 170-game
July 1-16 holdout it lost in all three markets: moneyline 0.251033 vs 0.247730,
spread 0.235368 vs 0.235163, and totals 0.252391 vs 0.249951.

The July 1-12 reconstructed-price diagnostic also lost to the no-vig market
Brier in every market. Its apparent spread profit is not promotion evidence:
the model Brier was worse than market, the 2% edge subset had only 10 records,
and all prices were retrieved after games rather than captured at decision time.

Decision: reject `mlb-opponent-adjusted-trend-score-v2` and continue iterating.

## Required inputs and known gaps

The intended model uses pitcher quality and platoon matchups, projected lineups,
bullpen workload, park, weather, travel, and rest. Score-only history cannot
reconstruct those inputs point-in-time. Missing values use explicit neutral
fallbacks only to keep calculations executable; a neutral value is not evidence
that the feature was observed.

The highest-priority data work is prospective capture of two-sided executable
prices, confirmed starters and lineups, bullpen availability, park/weather, and
source timestamps. Without those fields, more coefficient tuning is mostly
optimizing a lossy historical proxy.

## Next experiments

1. **Trend ablation and shrinkage:** compare long-horizon only, unadjusted recent
   form, opponent-adjusted trend, and reliability-shrunk trend on the same
   chronological folds.
2. **Totals residual layer:** model timestamped residual total error using
   decision-line context and explicit missingness flags. Keep it labeled
   market-aware and outside the raw score model.
3. **Branched total-intensity head:** estimate absolute run environment separately
   from relative team strength, then reconcile both into one away/home score
   distribution. Do not build a disconnected over/under classifier.
4. **Prospective validation:** collect valid pregame inputs and prices, then
   compare model Brier/log loss/ECE, no-vig market Brier, CLV, and price-aware
   ROI by missing-feature cohort.

No sample-count threshold freezes research. Sample size controls how strongly a
result can be interpreted and whether a version can be promoted.
