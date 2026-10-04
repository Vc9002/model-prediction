# Second research iteration — 2026-09-09

Built a second MLB candidate that preserves the current model's information,
and repaired two causes of missing WNBA replay evidence. The candidate is
usable for research inference, but does not establish a reliable predictive
or profitable upgrade. No champion registration or existing model artifact
was changed, and this work submitted no orders.

## WNBA investigation and recording fixes

All 22 incomplete contexts are missing `defensive_trend_gap`, dated July 28
through August 3. This is the older explicit-field serialization gap. The
current learned serving path computes the feature, but fixed lists of ledger
columns make new features easy to omit again.

The two probability mismatches are a separate issue. The serving path can
apply a WNBA player-availability probit transform after the fitted artifact.
It previously recorded neither the full transform nor its historical margin
dispersion. The retained player-prior files for August 23 and 24 have observation
times August 24 03:21 UTC and August 25 03:26 UTC respectively, later than the
decisions being replayed. The as-of loader correctly refuses both. This is a
plausible explanation for the mismatches, not independent proof of their exact
original adjustments. Their original probabilities were not overwritten or
relabeled as reproduced.

New learned moneyline decisions now carry a self-contained snapshot of:

- The entire hash-verified incumbent artifact and every input it requires,
  preserving full numeric precision and feature names without a fixed whitelist.
- The base home probability and final served home probability.
- The applied or skipped serving transform, including points adjustment,
  historical margin sigma, threshold, availability evidence, and fallback reason.
- Event identity, start time, observation time, and a snapshot hash.

The snapshot survives `PickRequest`, the decision audit, SQLite feature payload,
XLSX export, and settlement. The evaluator supports this new evidence format.
Validation rejects artifact/input corruption, inconsistent transformations,
wrong decision identity/probability, and future observation timestamps. Legacy
rows retain their old schema; unavailable inputs are never fabricated.

Player-prior refreshes now archive both the previous daily file and every new
version under `data/player_priors/wnba/snapshots/{date}/{content_hash}.json`.
The daily file remains an atomic compatibility view. The as-of loader searches
the immutable versions and rejects conflicting priors with identical observation
times. Later refreshes therefore cannot erase earlier priors on this write path.
Existing overwritten versions cannot be restored merely by changing this code.

This instrumentation covers the common MLB/WNBA/NBA/NFL learned moneyline
serving path. The fourteen other model families still require their own exact
input/state capture and replay adapters. Capturing a calculation does not by
itself certify the historical observability of every underlying source.

Evidence: `outputs/research/wnba_replay_20260909/report.json`, with code snapshots.
Pre-change source copies: `outputs/research/wnba_replay_20260909_prechange/`.
No live ledger backfill or historical prior mutation was performed.

## MLB v2 candidate

Artifact: `outputs/research/mlb_incumbent_residual_20260909_v2_verified/model.json`.
Runner: `scripts/train_incumbent_residual.py`.
Inference: `model_prediction.incumbent_residual.predict_artifact(path, features)`;
the input columns must follow the artifact's `feature_names`.

The candidate starts with the exact v8 logit and fits a regularized correction
using its six original inputs plus the 21 generic history features from the
first generation. Standardization is fitted on training rows only. Four
predeclared selection options are the unchanged incumbent and three correction
penalties, 0.1, 1, and 10. Selection-period log loss chose penalty 1. The JSON
artifact contains its fitted parameters and the complete v8 control artifact.
No parameter was selected from the final evaluation period.

All 371 source decisions replay the exact incumbent before training. Complete
UTC dates split into 211 training games (August 10–27), 83 selection games
(August 28–September 2), and 77 evaluation games (September 3–8). These dates
were examined during the previous experiment: this is reused historical
development data, not fresh confirmation. Six evaluation dates are also too
few to make strong uncertainty claims.

| Same 77 evaluation games | Incumbent v8 | New residual v2 |
|---|---:|---:|
| Accuracy | 61.04% | 57.14% |
| Brier score, lower is better | 0.242385 | 0.239842 |
| Log loss, lower is better | 0.677901 | 0.672683 |

Brier improvement is +0.002543, with a date-cluster bootstrap 95% interval of
[-0.003008, +0.008742]. The interval does not clear the required 0.002 minimum
effect. The verdict is **NO_RELIABLE_GAIN**.

Of those 77 games, 68 have the previously verified archived quotes. The same
$5-per-event, fee-aware taker simulation from the first comparison produces:

| Same 68 quote opportunities | Incumbent v8 | New residual v2 |
|---|---:|---:|
| Hypothetical trades | 44 | 55 |
| Net P&L | -$14.335 | -$16.065 |
| ROI on deployed cost | -6.82% | -6.13% |
| Maximum settled-P&L drawdown | $25.865 | $40.365 |

The candidate's less-negative ROI does not mean it earned more money: it
deployed more capital and lost $1.73 more. Its paired P&L gain per opportunity
is -$0.0254, with 95% interval [-$0.3950, +$0.4498]. Both lose, and the candidate
has larger drawdown. These are hypothetical fills, not realized trading
results; CLV and actual-fill metrics remain unavailable. The fee and execution
assumptions are documented in `INCUMBENT_COMPARISON_2026-09-09.md`.

## Verification and next work

203 focused tests passed across the new replay/archival logic, residual model,
learned serving, WNBA availability, CLI, canonical ledger, settlement, original
research generation, and incumbent evaluation. Scoped Ruff and mypy pass.
The verified training run reproduces the first v2 model bytes and metrics.
Existing production model/config hashes remain unchanged. This is not a new
full-suite verification or a deployment of the new model.

The next bottleneck is the amount and coverage of decision-time evidence.
Extend exact state capture to the other fourteen families, obtain current-model
cohorts for the five empty markets, and collect a fresh forward evaluation for
the new candidate. Do not keep tuning thresholds or penalties on these same
six evaluation dates until a positive number appears.
