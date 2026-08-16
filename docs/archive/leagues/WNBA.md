# WNBA research contract

## Current implementation

The current learned candidate is a WNBA-specific Elo, opponent-adjusted trend,
and defensive-trend logistic model. The newest report clears the model-accuracy
gate, but the report and active artifact were not reproduced as one release.
Treat it as shadow evidence, not executable edge.

The player-availability infrastructure now includes an official PDF collector,
timestamped ESPN event-injury snapshots, normalized player/status merging,
player-name mapping, a projected-minutes × impact feature contract, and a
fail-closed forward hook. An explicit ESPN status can fill an official-report
omission; two explicit sources that disagree force a no-call. It is **not
active in the production artifact**: `wnba-elo-trend-lr-v3` has no availability
coefficient.

The expanded May 14–July 20 reconstruction recovered 208 official reports for
180 scheduled matchups. V3 produced 169 candidates; 164 were settled and 142
had conflict-free, fully mapped availability inputs. On that paired subset,
winner accuracy moved from 71.83% to 71.13%, while Brier improved from 0.21278
to 0.20680 (delta -0.00599; paired bootstrap 95% interval -0.01076 to
-0.00119). The 132 games before the original July 17 audit showed the same
pattern: accuracy fell by one game while Brier improved by 0.00590. That is
evidence of probability signal, not promotion evidence: the PDFs were recovered
retrospectively and the impact prior is shrunk raw box plus/minus rather than
WNBA RAPM. See
`outputs/wnba_availability_expanded/WNBA_AVAILABILITY_DECISION_REVIEW.md`.

## Decision-time requirements

Train and calibrate separately from the NBA. Add possessions, lineup/on-off
impact, minutes projections, injuries, shot profile, and opponent
adjustments only with point-in-time provenance. Confirm franchise/entity
mappings, current availability, and likely minutes before
evaluating a live matchup. A season rating cannot stand in for missing players.

Any large model/market disagreement is a missing-information alarm until the
lineup, injuries, exact contract, and executable price are confirmed.

Simple rest, back-to-back, schedule-density, consistency, hot/cold, and
schedule-availability additions failed the 2026-07-20 isolated audit. Keep them
out of the predictive roadmap; schedule context is operational only.

Run `model-prediction wnba-availability-capture --event-id <ESPN_EVENT_ID>`
before a slate to archive the latest official report and the matching ESPN
event statuses. If either team has not submitted, a listed player does not map
to a pregame prior, the report is stale, the sources explicitly conflict, or
the rotation priors do not sum to 190–210 minutes, an artifact requesting
availability must return a `NO_CALL_AVAILABILITY_*` reason.
