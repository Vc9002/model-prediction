---
quick_id: 261122-ltz
slug: ncaaf-placeholder-rungs
date: 2026-11-22
---

# NCAAF: reject one-sided placeholder rungs as the main line

Follow-up to 261122-l17 (NBA/WNBA/NFL/MLB), on operator request.

## Theory
`_match_cfb_spread` / `_match_cfb_total` require both asks present but rank by |long ask - 0.5|
with no band and no latest-per-slug reduction, so a 1c bid / 50c ask rung beats a real market.

## Evidence
2026-11-07 slate (cfb-structural-v4 Flat): Southern Illinois vs Indiana State total 60.5 quoted
1c/50c, under bought at 99c; Morehead State vs Marist -2.5 quoted 5.5c/48c, Marist +2.5 at 94.5c.

## Change
- `pricing.py`: `MAIN_LINE_ASK_BAND` and `is_two_sided_main_rung` (one copy; models/ cannot import cli/).
- `models/college_football.py`: `_latest_two_sided` (newest capture per slug, then the band) before
  `_freshest_capture` in both selectors.
- `cli/forecast_market_select.py`, `cli/forecast_nfl_support.py`: use the shared copy.

## Test
Three NCAAF cases in `tests/test_main_line_placeholder_rungs.py`; revert-and-fail proof.

## Failure mode / rollback
All-placeholder ladders fall back to indicative rows (same selectors), else no Polymarket price.
Revert the commit.
