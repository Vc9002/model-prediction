# Soccer v3 normalization correction — October 19, 2026

## Defects and change

The v2 serving path normalized EWMA team strengths by the historical league
mean, then multiplied those relative strengths by a different configured
scoring baseline. This differed from `LeagueSoccerModel`, which uses the same
baseline for its EWMA prior, denominator, and final rates. The serving path
also ignored the fallback config's `shrinkage_prior_games` setting.

`soccer-poisson-dc-v3` uses one consistent configured baseline for named
competitions. Unnamed competitions use their own pre-event league mean and
the existing fallback shrinkage parameters. Their histories remain isolated;
missing leagues cannot borrow another league's history. Sparse unnamed
competitions with invalid empirical scoring scales are explicitly skipped,
without aborting other competitions. Named competitions can retain configured
priors when their observed games are scoreless.

Theory: correcting the scale and honoring existing shrinkage removes distorted
rates. Evidence: the saved San Telmo input used 1.4562 goals/team, versus
1.0204 from its 245 pre-date league games. Its v3 home-win probability is
39.6631% versus v2's 45.4501%; v3 draw probability is 31.1331%.
These are post-hoc sensitivities, not evidence of improved profitability.

## Serving and verification

The active paper forecast config and production registry both select v3;
v2 remains the explicit rollback, with its numerical behavior preserved.
The current direct-wiring paper policy permits the corrective rollout;
v3 evidence is explicitly `unverified`, with high replacement priority.
Soccer auto-buy remains disabled and v3 is not whitelisted. Historical
decisions and today's losses have not been rewritten.

Verification: focused `tests/test_soccer_v3.py`, scoped Ruff, code/snapshot
identity checks, registry/factory parity, and independent code review.
The two normalization regressions also fail when the original v2 methods are
substituted in memory. The read-only diagnostic reproduces all 11 saved
October 19 flat-ledger probabilities and numerical feature fields exactly
before interpreting candidate changes. Its original-payload hashes were
rechecked against SQLite. No orders or ledger writes occur in this diagnostic.

Diagnostic: `scripts/audit_soccer_v3.py --date 2026-10-19 --output <new-file.json>`.
Recorded result: `outputs/research/soccer_v3_fix_20261019_1836/diagnostic.json`.
This uses retained processed history; upstream historical observation times
remain uncertified. It is not a fresh prospective or locked-holdout evaluation.

Failure modes: empirical league rates can remain unreliable in small samples;
configured named-league parameters can drift; fixing normalization does not
establish calibration or trading edge. No sample-size cutoff was selected
from today's seven losses. Fresh paper outcomes must establish performance.

Rollback: select `soccer-poisson-dc-v2` in both active version fields in
`config/model.yaml` and the SOCCER champion slot in `config/production.yaml`,
restore its production status, and update the research queue/compatibility
mirror consistently. Retain v3 and its evidence for diagnosis.
