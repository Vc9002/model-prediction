---
phase: quick-261114-mzz
plan: 01
subsystem: data_sources/polymarket_us
tags: [polymarket, bbo-capture, event-detail, ncaab, ncaaf]
requires: []
provides:
  - event-detail quote pass (pass 1) in capture_slate_snapshots
  - uncovered-first ordering of the /book pass (pass 2)
affects: [polymarket-slate capture, forecast quote readers]
tech-stack:
  added: []
  patterns: [fail-closed per market, fail-soft per event]
key-files:
  created: []
  modified:
    - src/model_prediction/data_sources/polymarket_us.py
    - tests/test_polymarket_us.py
    - docs/DEBUG.md
decisions:
  - "captured counts rows from both passes; captured_by_source gives the split"
  - "a /book failure on an already-quoted market is covered_book_failures, not failures"
  - "market_state is mapped to MARKET_STATE_OPEN only from the verified MARKET_STATUS_OPEN"
metrics:
  duration: ~25 min
  completed: 2026-11-14
status: complete
actuals:
  tokens: 40000
  tasks: 3
  commits: 2
plan_head_before: edcbebc1d536c2535bb10c0a2b96c0f294d2330c
plan_head_after: 1c2bc49101dfa5a50ca5c47d900a9d2e2e21103c
---

# Phase quick-261114-mzz Plan 01: Event-detail quote pass Summary

One event-detail GET per upcoming game now quotes every validly quoted slate market ahead of the paced `/book` pass, so the live NCAAB and NCAAF captures wrote 1,619 and 2,107 rows against 203 for the whole 21:00Z daily.

`commits: 2` is measured at SUMMARY time (`git rev-list --count plan_head_before..HEAD`); the final planning-files docs commit follows it and is not counted.

## What changed

- `polymarket_us.py`: `EventDetail` dataclass, `PolymarketUSClient._get_response` (the old `_get` body; `_get` is a `.json()` wrapper) and `event_detail` (parses the CDN `Age` header via `_cache_age_seconds`), `_slate_market_like`, `_annotate_capture` (shared by both passes so every row has the same field set), `_event_detail_quote_book` (fail-closed gate), `_capture_event_detail_quotes` (pass 1), `_uncovered_first` (pass 2 order), `EVENT_DETAIL_MIN_REQUEST_INTERVAL_SECONDS = 0.25`. `_build_snapshot` looks its `reconstruction_status_source` up from `_response_source` (`bbo`, `event_detail`, else `/book`).
- Result keys added, none removed or renamed: `event_detail_events`, `event_detail_captured`, `event_detail_failures`, `captured_by_source`, `covered_book_failures`.
- `tests/test_polymarket_us.py`: eight `test_event_detail_*` tests; the autouse fixture also zeroes the event-detail spacing.
- `docs/DEBUG.md`: dated entry above the BBO-capture entry (commit 1c2bc491).

## Commits

- `e8e2ed47` feat: event-detail quote pass (src + tests, exactly those two files)
- `1c2bc491` docs: DEBUG.md entry (only my 81 lines were staged; see Deviations)

## Test proof (fail before, pass after)

RED run against HEAD's src (before any src edit): `8 failed, 48 deselected`. Failure lines:
`PolymarketUSClient has no attribute 'event_detail'` (T1), `module has no attribute 'EventDetail'`
(T2, T4, T5, T6, T7, T8), `KeyError: 'event_detail_events'` (T3).

After the change: `tests/test_polymarket_us.py` is `55 passed, 1 failed`; the one failure is the allowed
`test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error`. All eight `event_detail` tests pass.

Mutation proof for the two behavioural tests whose RED failure was only the missing class: with `_uncovered_first`
turned into an identity and the covered-failure branch disabled, `test_event_detail_unquoted_crossed_or_closed_markets_fall_through_to_book_pass_first`
and `test_event_detail_covered_market_book_failure_is_not_a_capture_failure` fail (6 others pass); restored, all 8 pass.

Other checks: `ruff check src/ tests/` clean; `tests/test_polymarket_snapshot_store_prefilter.py`,
`tests/test_cfb_indicative_quote.py`, `tests/test_health_monitor_repair.py` pass (11). The full suite was not run.

## Live proof (read-only GETs, `cli polymarket-slate --date 2026-11-14`, 21:49Z to 22:04Z)

| | events asked | event_detail_captured | captured | by source (detail / bbo / book) | elapsed_seconds | failures | pauses | source_rate_limited |
|---|---|---|---|---|---|---|---|---|
| NCAAB | 34 | 1491 | 1619 | 1491 / 18 / 110 | 367.2 | 0 | 3 | false |
| NCAAF | 30 | 1910 | 2107 | 1910 / 34 / 163 | 531.6 | 162 | 3 | false |

`event_detail_failures` 0 on both; `covered_book_failures` 0 on both; `skipped_after_time_budget` 0 (NCAAB), 160 (NCAAF).
All 162 NCAAF failures are contracts of games already under way (checked against slate start times): 160 `capture_time_budget_exhausted`
in the secondary tier on its 120 s cap, two `invalid_executable_bbo`. No event-detail row has `timestamp_valid` false or a missing ask.

Spot checks (stored event-detail row vs live `/bbo` GET, bid/ask): NCAAB `tsc-cbb-howrd-david-2026-11-14-145pt5` 41.0%/42.0% vs 41.0%/42.0%;
`tsc-cbb-manh-lemyn-2026-11-14-134pt5` 79.0%/81.0% vs 79.0%/81.0%; `tsc-cbb-brown-siena-2026-11-14-154pt5` 57.0%/58.0% vs 57.0%/58.0%.
NCAAF `tsc-cfb-sjst-airf-2026-11-14-total-46pt5` 61.0%/62.0% vs 61.0%/62.0%; `tsc-cfb-oregst-boise-2026-11-14-total-62pt5` 58.0%/59.0% vs 58.0%/59.0%;
`tsc-cfb-lou-ncar-2026-11-14-total-69pt5` 21.0%/22.0% vs 21.0%/22.0%. 6 of 6 match. Rows carry `MARKET_STATE_OPEN`, `quote_cache_age_seconds` 4 to 24 s.

## Reader audit

`grep ... | grep -v data_sources/polymarket_us.py` lists only `research_quote_contracts.py:68` (same as HEAD): no `/bbo` allow-list to extend.
`git diff --quiet` over `research_quote_contracts.py`, `college_contract_evidence.py`, `forecast_market_select.py`, `models/college_football.py` exits 0.
`college_contract_evidence` needs a parseable `transact_time_utc`, so it rejects `/bbo` and event-detail rows alike; pass 2 `/book` rows keep serving it.

## Deviations from Plan

### Judgment calls

**1. Staging `docs/DEBUG.md` by blob.** At execution time `docs/DEBUG.md` carried another session's uncommitted hunks (27 changed lines in the
2026-11-14 prf entry), contrary to the planning-time snapshot. The docs commit holds only my entry: I built HEAD + my entry as a blob and
staged it with `git update-index --cacheinfo`; the working file keeps both, and the other session's hunks stay uncommitted.

**2. Tasks 1 and 2 landed as one commit,** as Task 2 specified (one commit for the two files); Task 1 itself was not committed alone.

**3. Pass 2 still fetches covered markets** (same set and budget, per plan), so the live NCAAB/NCAAF runs still spent 128/197 `/book` contracts after pass 1.

No Rule 1-4 deviations. No auth gates.

## Known Stubs

None.

## Threat Flags

None. Only the gateway's public read endpoint is called (an already-used `/v1/events/slug/{slug}`); no order, execute or portfolio endpoint was touched.

## Left undone / for the operator

- Started-game contracts (secondary tier) still hit the 120 s cap on NCAAF; pass 1 deliberately does not quote started events. Unchanged behaviour.
- The 23:30Z daily is the first scheduled run to use pass 1; its `captured_by_source` is the check.

## Self-Check: PASSED

- `src/model_prediction/data_sources/polymarket_us.py`, `tests/test_polymarket_us.py`, `docs/DEBUG.md`: present.
- Commits `e8e2ed47` and `1c2bc491`: ancestors of HEAD.
