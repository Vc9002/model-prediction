# Fitted CFB joint-score iteration

Three new research versions now share one fitted joint distribution:
`ncaaf-moneyline-joint-research-20260911-v2`,
`ncaaf-spread-joint-research-20260911-v2`, and
`ncaaf-total-joint-research-20260911-v2`.

The candidate modestly improves moneyline point estimates against research
generation v1, but none of the measured gains is statistically reliable.
It has not established superiority over the production incumbent or executable
profitability. It is integrated with the common fixture, source-rebuild,
outcome-scoring and exact-pair capture interfaces.

## Model and experiment

Theory: separate market heads can disagree, and arbitrary score dispersion can
produce poorly calibrated probabilities. Fit home and away score means and
preserve their paired held-out errors, then price all three markets from the
same score distribution.

The model uses standardized ridge regression with penalties 1, 10 and 100.
The mean home/away absolute error on the selection period chooses the penalty;
penalty 1 won. The scaler and coefficients use only 3,061 training games.
Selection uses 510 games. The 563 calibration games provide paired residuals;
their covariance is approximately [[147.63, -1.71], [-1.71, 125.39]]. No test
rows enter the fit, penalty selection or residual distribution.

For inference, the model adds every calibration residual pair to the predicted
score means, rounds to integer points and clips negative scores to zero. All
markets condition on unequal final scores, consistent with college football
having an eventual winner; no overtime points are invented. About 1.955% of
raw evaluation samples tied before that conditioning. This empirical support
is still an approximation, not a complete drive or overtime simulator.

The exact archived v1 feature rows and complete-date partitions are retained.
Before comparing, all three v1 research models reproduce their saved 878
evaluation outputs with zero error. They are explicitly a **research reference**,
not the production incumbent. The old CFB evaluator's raw-Elo label and default
spread/total lines are not accepted as incumbent/economic evidence here.

## Same 878 historical evaluation games, 112 dates

| Measure | Research v1 reference | Joint v2 candidate |
|---|---:|---:|
| Moneyline accuracy | 68.56% | 69.70% |
| Moneyline scalar Brier | 0.194203 | 0.192886 |
| Moneyline log loss | 0.569047 | 0.564274 |
| Margin absolute error, points | 13.5013 | 13.5319 |
| Total absolute error, points | 12.8466 | 12.8331 |

Date-cluster bootstrap 95% intervals for reference-minus-candidate loss:

- Moneyline Brier: +0.001317, interval [-0.001940, +0.004664].
- Margin absolute error: -0.030601 points, interval [-0.071517, +0.006901].
- Total absolute error: +0.013426 points, interval [-0.009540, +0.041337].

These are reused historical development dates. No fresh confirmation is
claimed. Spread/total contract-probability scores and economics remain null
because exact historical contract lines and executable quotes are unavailable.
The candidate's small improvement in moneyline accuracy does not outweigh
those limitations or demonstrate that every market improved.

## Artifacts and actual forward use

- Fitted artifact and three reloadable serving bundles:
  `outputs/research/cfb_joint_20260911_v2_verified/`.
- Two complete fits produced the same joint artifact SHA-256:
  `577b3be49dc9be51b82089d53893bae7fbe62b7d452bfbb5b5329c92a2b570e3`.
- Current 21-model research generation:
  `outputs/research/generation_20260911_joint_update/`. It retains 18 prior
  research candidates and updates the three CFB candidates. This is not a
  change to production champions.
- Nine actual pregame forecasts across three games replay and independently
  rebuild from captured sources in
  `outputs/research/cfb_joint_forward_20260911_v2_mapped/`.
- Six additional contracts are unavailable: the frozen history has no records
  for Norfolk State Spartans or Richmond Spiders. No team history is fabricated.

The first forward attempt used numeric ESPN team IDs and was refused. The
historical NCAAF source has 5,259 rows keyed by exact team names, with no numeric
team-ID fields. Corrected fixtures preserve provider IDs separately and use
the exact historical names as feature keys. Outcome scoring requires an exact
provider ID/name match before translating a provider result into that legacy
namespace; this is not a fuzzy-name join.

The first bounded observer was stopped before it captured any predictions.
Its replacement is `outputs/research/paired_observer_20260911_v2/`, using
`paired_preparation_20260911_v2/`. It monitors 68 explicit contracts across 20
MLB/NCAAF games every 30 seconds, for at most 24 hours, and retains the first
valid pregame capture. Its report is the live source for actual capture counts.
An observing process is not already a successful comparison.

## Verification and remaining work

The integrated joint-training, shared-generation, forecast, scorer and paired
capture suite passes 70 tests. Tests cover serialization, coherent market
probabilities, integer pushes, fitted residual covariance, corrupt artifacts,
invalid inputs/lines, provider identity mismatches, and observer deadlines.
The actual 15-contract forward attempt records nine passes and six explicit
cold-start failures. Current lint/type checks and exact source snapshots are
in `outputs/research/joint_verification_20260911/`.

Next work is real incumbent-paired outcomes and verified economic evaluation,
followed by further sport-specific fitting. The prior production models and
all prior research versions remain available for rollback and comparison.
