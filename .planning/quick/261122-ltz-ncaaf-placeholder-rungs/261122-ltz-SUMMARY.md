---
quick_id: 261122-ltz
status: complete
date: 2026-11-22
---

# Summary: NCAAF placeholder rungs

## Changes
- `pricing.py`: `MAIN_LINE_ASK_BAND` (0.20, 0.80) and `is_two_sided_main_rung`, the one copy
  every main-line selector uses (models/ cannot import cli/). `forecast_market_select` and
  `forecast_nfl_support` now import it; values unchanged.
- `models/college_football.py`: `_fresh_two_sided` = newest capture per rung, then
  `_freshest_capture` over all rows (placeholders included), then the band; used by
  `_match_cfb_spread` and `_match_cfb_total` before the `_balance_then_newest` ranking.

## Review (critical-reviewer, 2 cycles)
- Cycle 1 major, fixed: band before the freshness window let a rung last seen hours earlier be
  priced when the newest capture was all placeholders (the 2026-11-21 stale-rung bug). Pinned by
  `test_ncaaf_fresh_placeholder_still_marks_an_old_two_sided_rung_stale`.
- Cycle 1 major (inferred), investigated and NOT adopted: "untraded indicative rungs sit at
  0.50/0.50" (the NCAAB rule). Live 2026-11-28 NCAAF slate: 0 of 3,413 spread/total rungs at
  0.50 on both sides; 0.50 occurs as often as neighbouring prices (51 vs 33 at 0.49, 58 at 0.51),
  so for NCAAF it is an ordinary price. A guard added in cycle 2 matched nothing live and was
  removed. The ledger's 13-27% of indicative rows at 0.50 is selection (closest to 50% wins).
- Open (operator): the NCAAF capture shortlist (`polymarket_us._prune_line_ladders`, keep=1
  pre-kickoff/day-ahead) ranks by cached long price, so fresh placeholder-only captures will
  now push more NCAAF lines to indicative or ESPN consensus pricing.

## Verification
ruff clean. 202 passed across test_main_line_placeholder_rungs (13) and the NCAAF/NFL/NBA/WNBA
files that exercise these selectors; test_cli `-k "nba or wnba or mlb or market"` 33 passed.
Each NCAAF test fails with its fix reverted.
