# Economic Report

Generated from data/rebuild/shadow.db -- every number below is a live query result, not hand-typed.

## Real qualification status

**ECONOMIC_SAMPLE_INSUFFICIENT.** Zero real BET decisions have ever been recorded (all real trade_decisions to date are NO_BET). This is a real data blocker, not a missing-code gap -- the decision engine, ledger, and this report are all real and working; there is simply nothing real to grade an economic outcome from yet.

## Trade decisions

- Total real trade_decisions: 293
- BET: 0
- NO_BET: 293

### By sport

- mlb: 256
- wnba: 24
- tennis: 13

### NO_BET reason codes (real, diagnostic -- shows what's actually gating trades)

- `stale_quote`: 140
- `not_aligned_with_predicted_winner`: 99
- `not_aligned_with_frozen_totals_side`: 48
- `insufficient_depth`: 6

## Downstream ledger state

- predictions recorded: 44
- market_evaluations recorded: 1260
- paper_orders recorded: 0
- settlements recorded: 192
- closing_prices recorded: 0
- runs recorded: 52

### Settlement outcomes (real WIN/LOSS/PUSH for every evaluated side, BET and NO_BET alike)

- LOSS: 96
- WIN: 96

Of 192 real settlements, 0 carry a real captured closing price; the rest have `settled_price = NULL` with an explicit per-row note (Polymarket's public API serves only currently open markets, so no historical closing quote could be recovered for these already-resolved past events -- see `mlb_settle_and_capture_closing.py`'s module docstring).

## What this means

The real NO_BET reason-code breakdown above is the honest diagnostic signal: `stale_quote` and `not_aligned_with_predicted_winner`/`not_aligned_with_frozen_totals_side` dominate over `insufficient_depth` in this session's real data, meaning the current real bottleneck is quote freshness and winner-first alignment (both correct, intended gating behavior per CLAUDE.md's winner-first policy), not primarily the disclosed missing order-book-depth data source. Zero paper_orders means order-book walking has never been exercised against a real fill.

192 real settlements now exist (real WIN/LOSS/PUSH determined from the real final score for every evaluated market/side, including NO_BET rows -- see the breakdown above), but zero carry a real closing price, so CLV still cannot be computed from anything real yet. This is a genuine, disclosed data-timing gap, not a code gap: closing-price capture requires a poller running prospectively, through each event's real market close, which has not yet run continuously for any of these past events.

No PnL, ROI, or CLV figures are reported here -- reporting them from zero real accepted trades would mean fabricating them (real settlement outcomes exist for research/NO_BET rows, per above, but zero real paper fills exist to compute a real PnL/ROI from). Real economic evaluation requires real BET decisions to occur first (more real backfill days, fresher quote collection cadence, or lower-friction markets), then real settlement against real final scores and a real captured closing price.

## Executable edge methodology (real, architectural -- not session-specific)

The system uses only executable Polymarket US order-book BBO data: best_ask is a real executable ask, never a midpoint; conservative_probability is the calibrated model probability minus a real uncertainty margin (bootstrap_uncertainty today -- see FOUNDATION_FROZEN.md's known-blockers list for the remaining calibration/lineup/missingness/model-disagreement components); cost_adjusted_edge = conservative_probability - best_ask - spread/2 - fees. No synthetic -110 pricing is ever used; every edge is computed against a real observed quote or the market fails closed.

## Position sizing (real, implemented, never yet exercised against a real accepted trade)

economic.py implements flat/fixed-fractional/capped-fractional/uncertainty-adjusted Kelly sizing plus event/team/sport/market-type/correlation/daily caps (SizeLimits, Exposure) -- real, tested code (see tests/test_rebuild.py's TestEconomics). It has never processed a real accepted trade end to end, since none have occurred yet; this is disclosed here rather than implied by the code's mere existence.
