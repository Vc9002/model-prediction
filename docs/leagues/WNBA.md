# WNBA research contract

## Current implementation

The current learned candidate is a WNBA-specific Elo, opponent-adjusted trend,
and defensive-trend logistic model. The newest report clears the model-accuracy
gate, but the report and active artifact were not reproduced as one release.
Treat it as shadow evidence, not executable edge.

## Decision-time requirements

Train and calibrate separately from the NBA. Add possessions, lineup/on-off
impact, minutes projections, injuries, travel/rest, shot profile, and opponent
adjustments only with point-in-time provenance. Confirm franchise/entity
mappings, current availability, likely minutes, and back-to-back context before
evaluating a live matchup. A season rating cannot stand in for missing players.

Any large model/market disagreement is a missing-information alarm until the
lineup, injuries, rest, exact contract, and executable price are confirmed.
