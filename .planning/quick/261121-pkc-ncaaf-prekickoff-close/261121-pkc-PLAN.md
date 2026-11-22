---
phase: quick-261121-pkc
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/ncaaf_prekickoff_capture.py
  - src/model_prediction/ncaaf_prekickoff_capture_worker.py
  - src/model_prediction/run_supervisor.py
  - scripts/register_windows_tasks.ps1
  - tests/test_ncaaf_prekickoff_capture.py
autonomous: true
requirements:
  - QUICK-261121-pkc (NCAAF overhaul plan: pre-kickoff Polymarket close capture)
---

# Quick 261121-pkc: NCAAF pre-kickoff close capture

No champion change, no ledger mutation, no order. The scheduled task is added to
`scripts/register_windows_tasks.ps1` but NOT registered on this machine (operator
registers after merge).

## Why

NCAAF settlement (`cli/settle.py`, the `sport_dir in ("ncaab", "ncaaf")` branch) takes as
the close the latest snapshot of the row's own slug observed strictly after the decision
quote, strictly before kickoff (the earlier of the row's and the contract's start), with
an open book and, for spreads/totals, at the row's own line magnitude. NCAAF snapshots
are written only by the Daily (8x/day) and the day-ahead capture, so most rows have no
qualifying close and no CLV.

## Evidence gathered before editing

- `pretip_close_capture.py` (4596c606): NCAAB held-row capture from the 15-minute
  buyer worker. Reads open rows read-only from `ledgers.db` (`_OPEN_ROWS_SQL`,
  `_row_slug` mirror settlement's `_extract_market_slug`), maps each slug through
  its newest archived record, and calls `capture_slate_snapshots` with
  `client.snapshot(slug)` (2 requests per contract). Zero requests when idle.
- `data_sources/nba_pretip_capture.py` + `nba_pretip_capture_worker.py`: a
  standalone supervisor worker every 10 minutes, window 25 min, one event list,
  one event-detail GET per in-window game, `capture_slate_snapshots` with
  `ladder_keep=1` and open-pick slugs as `ladder_keep_slugs`. No daily lock.
- `capture_slate_snapshots`: NCAAF is in `EVENT_DETAIL_EXCLUDED_LEAGUES`, so no
  pass-1 requests; `_capture_tiers` puts keep-slugs first, round-robin by kickoff;
  `snapshot_from_slate` costs 1 request when the market carries `market_id` +
  `sides`. Rows are appended only after every fetch finishes, so the budget must
  stay well inside the supervisor timeout.
- Runtime ledger (read-only, 2026-11-21 19:38Z): 352 open NCAAF rows (314 flat,
  38 main) across 18 kickoff times 17:00Z-03:30Z; 352/352 resolve a slug.

## Design

`ncaaf_prekickoff_capture.capture_ncaaf_prekickoff(now=<clock>, window_minutes=25, ...)`:

1. Held rows: every open NCAAF ledger row's slug (all tiers) is a keep-slug; rows
   whose ledger kickoff is in (now + 60 s, now + window] are "held in window".
2. Idle gate on local evidence: no held row in window and no archived snapshot
   (today's/tomorrow's ET archive) kicking off in window -> return idle with zero
   exchange requests.
3. Discovery: one `client.events("NCAAF")`; the clock is read again after the
   response; games with kickoff in (observed + 60 s, observed + window] are
   hydrated one by one with `client.event(slug)` and `_normalize_event`.
4. Held slugs in window that discovery did not produce (event list or detail
   failed/429, rung delisted) fall back to the archive mapping of
   `pretip_close_capture` (`_latest_archived` + `_contracts_from_archive`).
5. One `capture_slate_snapshots` call per ET game date, under one wall-clock
   deadline: `ladder_keep={"NCAAF": 1}` (moneyline + most balanced spread and total
   per game), `ladder_keep_slugs` = all open slugs (fetched first, soonest kickoff
   first), written to `<runtime root>/odds/ncaaf/<ET date>/polymarket_snapshots.jsonl`.
6. Never raises; returns a status dict.

Worker `ncaaf-prekickoff-capture` (supervisor, timeout 360 s, budget 240 s), no daily
lock, exit 0 for ok/idle/partial, 1 for error (runtime root missing, nothing captured
because the exchange was unreachable). Task: every 10 minutes from 09:05 to 01:05 ET.

## Tests (`tests/test_ncaaf_prekickoff_capture.py`)

- End to end: a fake client, the real `capture_slate_snapshots`, then settlement's
  `_closing_probability_for_moneyline_pick` and `_settle_all_unsettled` read the
  written rows as the close (moneyline + spread with line/side; a post-00:00Z
  kickoff filed under its ET date).
- Window selection with an advancing clock: a game kicking off during the event-list
  call is excluded; T+61 s in, T+59 s out, T+window in, T+window+1 s out.
- Held slug survives a failed event list (archive fallback); idle makes no request.
- Worker registered with the supervisor; exit codes.
- Mutation-prove: drop keep-slugs, shift a window bound, drop the clock re-read,
  drop the archive fallback.
