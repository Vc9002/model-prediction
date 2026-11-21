---
phase: quick-261121-dac
plan: 01
status: complete
commits:
  - 1bc15a45 fix(ncaaf): a decision only prices off quotes observed on the game date
  - f916f038 feat(capture): NCAAF day-ahead Polymarket books for D+1 and D+2
  - 53f4ee1e docs(quick-261121-dac): plan and summary
  - 54afcea2 fix(ncaaf): the decision-quote floor is the decision's own ET day
  - (this commit) docs: summary update
---

# Quick 261121-dac: NCAAF day-ahead Polymarket capture

No champion change, no ledger mutation, no scheduled-task change. Not merged into E:\MP.

## Merge gate (operator decision)

Settlement can take a day-ahead quote as the close of a pick priced off the indicative
event list. `PolymarketSnapshotStore.closing_snapshot` has no lower bound. A pick priced
off `polymarket_us_indicative` (no game-day book for its slug) still names a slug:
102 of 102 NCAAF `indicative_slate` ledger rows resolve one through
`_extract_market_slug` (e.g. `asc-cfb-chat-ala-2026-11-21-pos-48pt5`). If the day-ahead
run booked that slug and game day did not, the close is a quote observed before the
decision. Before this branch those rows had a blank CLV. The fix is a close bound of
"observed after the decision". The other session has the same change for NCAAB
uncommitted in E:\MP, in `polymarket_us.py`, so it is not made here to avoid a
collision. Decide whether to land that bound for NCAAF before or with this branch.

## What changed

- `cli/commands.py` `capture_ncaaf_polymarket_day_ahead`: for D+1 and D+2 it calls
  `slate("NCAAF", date)`, then `capture_slate_snapshots` (the game-day writer, so the
  row schema is identical: `observed_at_utc`, `timestamp_valid`, `usage`, `league`, ...)
  with `horizon_hours=None`, `ladder_keep={"NCAAF": 1}` (moneyline plus the most
  balanced spread and total), `priority_leagues={"NCAAF"}`. Rows are filed under
  each game's own ET date, as the NFL week-ahead does. One 180 s deadline covers both
  dates and is checked before each slate fetch, but a `slate()` call already started
  (list fetch plus one detail GET per event) is not cut, so the bound is 180 s plus one
  slate fetch. A date reached after the deadline is skipped.
  `NCAAF_DAY_AHEAD_LADDER_KEEP` is its own constant, not `CAPTURE_LADDER_KEEP["NCAAF"]`:
  the main checkout has an uncommitted change raising that to 12, which multiplies
  game-day /book calls on the same IP-wide limit.
- `cli/daily.py`: `_run_ncaaf_day_ahead_capture` (gated on
  `is_sport_daily_enabled(config, "NCAAF")`, any exception -> `{"status": "error"}`),
  report key `step1n_ncaaf_day_ahead_quotes`, not in `_material_daily_errors`. It runs
  last in `_serialized_polymarket_captures` (slate, NFL week-ahead, NCAAF day-ahead);
  the serializer now takes `*later_captures` and isolates each one.
- `models/college_football.py` `_load_decision_snapshot_rows`: `build_cfb_slate` keeps
  only rows observed from 00:00 ET of the decision's day (the earlier of the game date
  and the decision's ET date) up to the decision time.

## Why the decision guard

`_match_cfb_spread` / `_match_cfb_total` take the most balanced ask across the latest
row of each slug, with no age bound. With day-ahead rows in the game-date file, a rung
quoted yesterday that today's shortlist dropped would outrank today's rung and price
the pick at a day-old ask, stamped `decision_time_executable_quote`. Scheduled runs
only ever wrote today's date, so game-day pricing is unchanged. The runtime archive
holds 11,241 of 168,975 NCAAF rows observed before their file date's ET midnight, all
off-schedule runs (2026-09-05 10,700 at 09-04T07Z; 10-22 170; 10-23 364; 11-05 7).
The first version (1bc15a45) floored at the game date, which would have left a forecast
run ahead of the game with no book. The ledger has 29 such NCAAF rows, decided 2026-10-21
for 10-22 and 10-23. 54afcea2 floors at the decision's own day instead.

For the scheduled game-day forecast, day-ahead rows therefore feed line movement and
CLV only. Letting them price a decision needs a D+1 forecast, which does not exist.

## Readers checked

- Settlement: see the merge gate above. For book-priced picks the decision slug has
  its own game-day quote, so the close is never a day-ahead row.
- `college_contract_evidence.build_candidate_evidence` (not edited; NCAAB shares it):
  per slug, no age bound, so a slug quoted only day-ahead shows as `matched` in the
  candidate packet (`selected_candidate_joins`, research and audit only). It never
  prices a pick.
- `daily.py` odds summary counts only; `cmd_polymarket_clv` per slug;
  `learned_forward.match_executable_quote` is not on the NCAAF path.

## Cost

Per run on Thursday and Friday, for a Saturday slate of about 95-115 events, the
upper bound is:
- 2 event-list fetches;
- one hydration GET per event, about 100-125, unpaced, 4 workers;
- up to 3 /book calls per event, about 300-345, at the 0.5 s pacer, cut at 180 s.

That is about 430-480 requests and up to 180 s (plus one slate fetch) before forecasting,
on 8 runs a day. This is an upper bound and was not run live. One live list fetch for
2026-11-27 (13 events, 211 contracts, all with a cached slate price) pruned to 19:
13 moneylines and 3 each of spread and total, because only 3 games listed lines six days
out. Other days have a handful of midweek games: tens of requests, a few seconds.

## Pre-tip close (other session's `pretip_close_capture.py`, read only)

It is sport-parameterised (`sport="ncaab"` default). It reads open ledger rows kicking
in the next 25 min, maps each slug through its newest archived record, and books just
those contracts (~2 requests per slug) from the 15-minute buyer worker. NCAAF can reuse
it for a pre-kickoff close with `sport="ncaaf"` once it is committed. Day-ahead rows
do not interfere (same slug metadata).

## Tests

`tests/test_ncaaf_day_ahead_capture.py` (6) and `tests/test_cfb_decision_quote_window.py`
(4). They ran with `tests/test_nfl_week_ahead_capture.py`, `test_daily_cfb_pregame_capture_wiring.py`,
`test_cfb_quote_integrity.py`, `test_cfb_indicative_quote.py`, `test_cfb_serving_parity.py`,
`test_audit_fixes_regression.py`, `test_cfb_structural_v4.py`, `test_daily_cycle_smoke.py`,
`test_daily_integrity.py` and `test_cli.py` (`-o addopts=''`, no MODEL_PREDICTION_* env):
all pass. After 54afcea2, the decision-window, capture, CFB and smoke files were rerun:
77 passed. ruff check and format are clean. Each mutation below reverts one guard, and
each fails at least one test:

| Mutation | Tests that fail |
|---|---|
| Floor set to the game date | 1 |
| No floor | 2 |
| No decision ceiling | 2 |
| No budget pass-through | 1 |
| No deadline check before the slate | 1 |
| Ladder keep 2 | 2 |
| Filed under the capture date | 2 |
| No sport gate | 1 |
| No serializer isolation | 1 |

No test runs `run_daily` itself, so the line that hands the third callable to the
serializer is untested. The existing NCAAF pregame wiring has the same gap.

`cfb_serving_code_hash` changes with `college_football.py`, so new NCAAF rows carry a
new `code_revision`.
