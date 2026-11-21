---
phase: quick-261121-plb
plan: 01
status: complete
commits:
  - 0630faa2 fix(clv): a Polymarket close is never quoted after the contract's own kickoff
  - acd665f9 fix(clv): settlement close must match the row's line and side by whole word
  - 83640460 fix(capture): breadth-first /book queue so a cut budget still quotes every game
---

# Quick 261121-plb: NCAAF capture and CLV plumbing (overhaul step 2)

No champion-pick change, no ledger mutation, no scheduled-task change.

## CLV root causes (fixed)

1. `data_sources/polymarket_us.py` `PolymarketSnapshotStore.closing_snapshot`
   (HEAD :932-957) bounded candidates only by the ledger row's kickoff. Six
   10-24 flat rows (19:30Z-02:00Z) had been priced at decision time on the
   New Haven vs Central Connecticut total (`tsc-cfb-nhc-cencon-2026-10-24-total-56pt5`,
   kickoff 16:00Z; the CCSU collision), so its 17:03Z in-play Under ask of 99c
   became their close (CLV +0.43). Now: latest quote observed strictly before the
   earlier of the row's and the contract's own `event_start_utc`, from a book
   with `market_state` OPEN. Shared by every settle path and the auto-buyer.
2. `cli/settle.py` `_closing_probability_for_moneyline_pick`, Over/Under branch
   (HEAD :671-690): substring match, so "Thundering Herd" / "Thunder" /
   "Sounders" / "Sunderland" read as Under. The 10-20 South Alabama (away)
   moneyline closed off Marshall's ask in a recomputation (stored 55c,
   HEAD recompute 45.5c). Now whole-word, and an unproven pairing falls through
   to the name match (declines) instead of defaulting to the long side.
3. Same function: a spread/total close must be quoted at the row's own line
   (magnitude), else blank. Reasons are logged
   (`no_pre_kickoff_open_quote`, `close_line_mismatch`); no ledger column.

Real-data check (158 NCAAF rows with a stored close; 121 settled, 26
superseded, 11 archived): recomputed closes match HEAD except the six 99c rows,
now 56c. All 11 rows on the two collided CCSU contracts are already archived
(11-18). Three settled rows differ from their stored close under HEAD and new
code alike (snapshots changed after settlement), not from this change.

## Capture root causes

- 10-17 moneyline only: Polymarket's league feed carried only winner markets
  from 10-15 22:09Z to 10-21; fixed by event-detail hydration in `slate()`
  (`polymarket_us.py:594-609`, landed in a5e7d1a9).
- 11-07 (38 events, none quoted pre-kickoff): first 429 abandoned the queue;
  NCAAF got 0 of 154 and 0 of 630 contracts (daily_2026-11-07.log). Fixed by
  79487b2c (pacer, per-league round-robin, tiers).
- 10-10 (53 events, 4 quoted pre-kickoff): 11 consecutive daily runs failed
  within 2 s (10-08 04:30Z to 10-10 16:00Z, runs.db); only the 22:00Z run
  captured, after most kickoffs. Cause not recoverable (supervisor logs rotated).
- Still live, fixed here: `_round_robin_by_kickoff` kept an event's markets
  together, so a cut 600 s budget lost late kickoffs entirely. Now breadth-first:
  every moneyline, then each event's most balanced line per type, then the rest.
- Still live, cadence (not changed): capture runs only inside the daily job
  (8 triggers, `scripts/register_windows_tasks.ps1:32`) and only for today's
  Eastern slate (`daily.py:298-306`, `slate()` date filter `polymarket_us.py:582`).
  No NCAAF quote is taken before game day, and the "close" is the last daily run
  before kickoff (up to ~2.5 h stale), not a true close.

## Proposals (operator decision, not done)

- Day-ahead NCAAF capture: a third callable in `_serialized_polymarket_captures`
  (`daily.py:167`) capturing NCAAF for D+1 (and D+2 on Thursdays) with
  `ladder_keep={"NCAAF": 2}`, `max_capture_seconds` ~240, filed under its own
  date. Costs /book budget the game-day capture already exhausts on Saturdays.
- True close: a short pre-kickoff capture task (e.g. every 30 min on Saturdays,
  only contracts of open picks kicking within 45 min), registered only by the
  operator.
- Persist the CLV-blank reason in the ledger (needs a schema field).

## Tests

`pytest tests/test_closing_snapshot_identity.py tests/test_polymarket_us.py
tests/test_polymarket_snapshot_store_prefilter.py tests/test_ncaab_settlement_clv.py
tests/test_fix_regressions.py tests/test_cli.py tests/test_daily_cycle_smoke.py -o addopts=''`:
all pass except `test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error`,
which fails identically at HEAD (pre-existing). Mutation proofs: removing the
own-kickoff bound fails 3 tests; the OPEN-state check 1; the line check 1; the
whole-word match 1; the rung ordering 1. ruff clean.
