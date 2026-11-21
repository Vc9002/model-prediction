---
phase: quick-261121-pkc
plan: 01
status: complete
---

# Quick 261121-pkc: NCAAF pre-kickoff close capture — SUMMARY

No champion change, no ledger mutation, no order. Task added to
`scripts/register_windows_tasks.ps1`, not registered on this machine.

## What changed

- `src/model_prediction/ncaaf_prekickoff_capture.py` (new):
  `capture_ncaaf_prekickoff`, which works as described in the PLAN.
  - Held open-row contracts are captured first, soonest kickoff first. Then the
    moneyline and the most balanced spread and total of every game kicking off in
    (now + 60 s, now + 25 min].
  - It writes through the unchanged `capture_slate_snapshots` to
    `<runtime root>/odds/ncaaf/<ET date>/polymarket_snapshots.jsonl`.
  - The idle gate checks local evidence (ledger plus archive), so an off-day run
    makes no request.
  - The clock is re-read after the event list.
  - A held contract that discovery misses falls back to the archive mapping of
    `pretip_close_capture`.
  - One 240 s deadline covers the run, and it never raises.
- `src/model_prediction/ncaaf_prekickoff_capture_worker.py` (new): worker
  `ncaaf-prekickoff-capture`. Exit 0 for ok/idle/partial, 1 for error. It takes no
  daily lock; the supervisor lease gives exit 75.
- `run_supervisor.py`: worker entry, timeout 360 s.
- `register_windows_tasks.ps1`: `ModelPrediction-NcaafPrekickoffCapture`, every
  10 minutes from 09:05 for 16 h (to 01:05 ET).
- Two old slug bugs fixed. Without these fixes no capture could produce a close
  for the rows involved:
  1. NCAAF moneyline rows record `<contract slug>-home|away`
     (`models/college_football.py:1108`, and v6.2 does the same). No contract has
     that slug, so 0 of 159 NCAAF moneyline rows settled since 2026-11-01 had a
     close. Settlement's NCAAF branch and the capture now map the slug through
     `ncaaf_contract_slug`, which drops a trailing `-home`/`-away` only from an
     `aec-cfb-...-YYYY-MM-DD` slug.
  2. The v6.2 rationale reads `challenger (anchor-blended): ... (aec-cfb-...)`.
     `_extract_market_slug`, and its mirror `_row_slug`, took the first hyphenated
     parenthesized token, so all 336 v6.2 rows had the slug `anchor-blended`. Both
     now prefer a contract-prefixed token (`atc/tsc/asc/mkt/aec`) and fall back to
     any hyphenated token, as before.

## Verification

- `tests/test_ncaaf_prekickoff_capture.py`: 8 tests. The gateway is an in-memory
  `httpx.MockTransport` behind the real client and the real
  `capture_slate_snapshots`, and the clock advances.
  - End to end: the capture writes rows, and `_settle_all_unsettled` records them
    as the close for a v6.2 moneyline row (`-home` slug, anchor-blended
    rationale) and for a spread row on a rung that ladder pruning would otherwise
    drop.
  - Window bounds measured after a 120 s event-list call: a game kicking off
    during the call is excluded, T+60 s is out, T+61 s is in, T+25 min is in,
    T+25 min+1 s is out.
  - A 00:30Z kickoff is filed under its ET date.
  - The archive fallback runs when the event list returns 503.
  - An idle run makes zero requests.
  - Slug resolution; worker registration and exit codes.
- Mutation proof (`pkc_mutate.py` in the session scratchpad): all 11 mutations
  were killed.
  - Capture: keep-slugs dropped, lower bound made inclusive, upper bound made
    exclusive, no clock re-read, no archive fallback, ML suffix kept, wrong sport
    dir, no idle gate.
  - Settle: ML suffix kept, first-hyphen extractor.
  - Pretip: first-hyphen extractor.
- Targeted files (no MODEL_PREDICTION_* env): this file plus
  `test_pretip_close_capture`, `test_ncaaf_settlement_clv_bound`,
  `test_nba_pretip_capture`, `test_nba_anchor_v10_wiring`,
  `test_closing_quote_resolution`, `test_closing_snapshot_identity`,
  `test_nba_settlement_clv_alias`, `test_ncaab_settlement_clv`,
  `test_resolution_settlement`, `test_system_health`,
  `test_nfl_settlement_consensus`, `test_run_supervisor`. All pass except
  `test_run_supervisor::test_worker_registry_commands_exist_on_disk`, which fails
  only because this worktree has no `.venv`. It asserts that the production
  worker's venv python exists, which is unrelated to this change. `ruff check src/ tests/` is clean.

## Live dry run (read-only quotes, scratch runtime root, 2026-11-21)

The scratch root held a copy of the runtime `ledgers.db` and of today's NCAAF
archive.

| Run | Window | Games | Contracts captured | Requests | 429s absorbed | Time |
|---|---|---|---|---|---|---|
| 19:44Z | 50 min (20:00Z + 20:30Z clusters) | 21 | 80 (5 cut by budget) | 153 | 58 | 248 s |
| 20:05Z | 25 min (default; 20:30Z cluster) | 11 discovered + 1 archive fallback | 51, 0 failures | 120 (1 list, 11 detail, 85 /book, retries) | 56, 1 pacer pause | 140 s |

Settlement check after the 20:05Z run. It used the real
`_closing_probability_for_moneyline_pick` against the scratch root, for the
open rows kicking off at 20:30Z:

- 56 of 58 rows have a close (baseline before the run: 0 of 58).
- By market: moneyline 16/18, spread 20/20, total 20/20.
- The 2 misses are Auburn moneyline rows. That book had no bid on the long side,
  so there is no ask for Auburn (`close_ask_unusable`), which is correct.

## Open items / decisions

- Register the task after merge (exact line in `register_windows_tasks.ps1`).
- Already-settled rows keep their blank closes; no backfill was done.
- `_extract_market_slug` is also used by the real-money ticket gate
  (`polymarket_execute.py:427`, the slug must equal the priced contract). For a
  v6.2 row the gate previously compared against `anchor-blended` and refused
  every ticket. It now compares against the real contract slug.
- Busy Saturday clusters (the noon wave) can exceed the 240 s budget on the first
  look. Held contracts go first, and the next run 10 minutes later picks up the
  rest.
