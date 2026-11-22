---
phase: quick-261121-spt
plan: 01
status: complete
completed: 2026-11-21
---

# quick-261121-spt SUMMARY: NCAAF stale-price test (overhaul step 1)

**Verdict: INCONCLUSIVE** (pre-registered rule `cfb-stale-price-rule-1`, sha256
`5f72f6daf3340d22c30bc968853c0cea2d79636eba4f81040389da1adf568389`, committed in `ee49c5d6` before the
graded run; script sha256 `b4238c32...`). The stale-price premise is neither killed nor supported: even
the generous upper-bound arm's 95% ROI interval includes zero.

## Results (P&L per 1U at the executable ask, fee 0)

| Arm | Market | n | W-L | Hit | Break-even | ROI [95% CI, date-clustered] |
|---|---|---|---|---|---|---|
| ESPN close (upper bound) | pooled | 222 | 118-104 | 53.1% | 46.9% | +13.3% [-3.9%, +27.8%] |
| ESPN close | spread | 99 | 50-49 | 50.5% | 47.0% | +7.3% [-8.5%, +28.9%] |
| ESPN close | total | 123 | 68-55 | 55.3% | 46.7% | +18.1% [-11.9%, +35.5%] |
| DraftKings as-of (clean) | total | 4 | 2-2 | 50.0% | 48.0% | +4.2% [-100%, +108%] |

27 ET-date clusters in the ESPN arm. By-market rows are descriptive only.

## Match rate

1,033 of 1,046 Polymarket spread/total events match a unique 2026 `games.jsonl` game (13 unmatched have
no game row: 11-15 FCS games and unplayed 11-20/11-21 games); 569 of 1,046 have an ESPN 2026 close (was
529 with the loose matcher); 569 of 731 ESPN-lined events are covered.

## Funnel (ESPN arm, outcome-free)

Spreads: 93 integer anchors (no pick), 181 no rung at the anchor before kickoff, 150 a rung but no ask at
or below 48c with size >= 1, 99 picks. Totals: 231 no rung, 166 no qualifying ask, 123 picks.

## Volume

About 20 picks per captured Saturday (42, 52, 12, 3, 13, 1, 27, 31, 3 on the nine Saturdays with valid
captures and an ESPN anchor). Capture is intermittent: 2026-11-07's full-game rows are all
`timestamp_valid` false, 10-17 was moneyline only. Median lead 12 h. Top-of-book ask size median about
3,760 contracts; 5 of 222 picks had fewer than 10.

## Caveats

- Side mapping: spread `long` = the title's FIRST team at `line` (`long.description` == line in every
  row; the `team` field named the first title team in 61,309 of 61,309 resolvable rows). Outcome-free
  check: the latest pre-kickoff midpoint at the ESPN-close rung has quartiles 48.5 / 49.75 / 51.25%
  (spreads, 249 events) and 48.5 / 49.5 / 51.5% (totals, 289), none more than 15 points off 50%. A flipped
  mapping would put these far from 50%.
- `market_type` spread/total also covers half, quarter and team-total markets (about 42% of spread/total rows); only
  the full-game slugs are used. Earlier counts that did not filter slugs mixed these in.
- The ESPN close postdates every quote, so the ESPN arm is an upper bound. The clean DraftKings arm has 4
  picks and decides nothing; it needs the forward capture to run for a season.
- `timestamp_valid` false rows (about 16% of full-game rows) are excluded by rule; any stale prices in
  them are untested.
- In the generated markdown the "Hit - BE" CI is printed with a % sign; the values are percentage points.

## Commits

- `ee49c5d6` script + plan (rule pinned, no returns read)
- outputs/ncaaf/cfb_stale_price_test.{json,md} and this summary: the following commit(s)
