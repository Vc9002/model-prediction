# Rebuild Market Semantics

The prediction establishes a winner before price is considered. Price can turn
that winner into `NO_BET`; it cannot flip the recommendation to the opponent.
For example, a 60% prediction for A against a 70-cent ask is `NO_BET`, zero
units—not a bet on B. A positive, aligned edge may produce a paper `BET` only.

Every decision binds to the exact event, side, market type, period, signed line,
and timestamp-valid executable quote. Home `-1.5` and away `+1.5` are related
but not interchangeable labels. Whole-number spreads and totals keep push
probability separate from win probability; half-lines have no push mass.

The system fails closed on stale or post-start quotes, closed markets, missing
depth, ambiguous events, wrong lines, missing artifacts, invalid hashes, or
missing calibrators. No inferred line or fabricated contract is acceptable.

Economic reporting distinguishes market evaluations, `BET`/`NO_BET` reason
codes, paper orders, fills, settlements, closing quotes, and CLV coverage. With
no valid fills, PnL and ROI are unavailable—not zero. Missing closing prices
produce unavailable CLV rather than an assumed neutral result.
