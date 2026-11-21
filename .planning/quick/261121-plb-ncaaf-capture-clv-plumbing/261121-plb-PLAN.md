---
phase: quick-261121-plb
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/data_sources/polymarket_us.py
  - src/model_prediction/cli/settle.py
  - tests/test_polymarket_us.py
  - tests/test_closing_snapshot_identity.py
autonomous: true
requirements:
  - QUICK-261121-plb (NCAAF overhaul plan, step 2 plumbing; docs/ROADMAP.md "Overhaul plan (2026-11-20 ...")
---

# Quick 261121-plb: NCAAF capture and CLV plumbing

No change to champion picks. No ledger mutation. No scheduled-task registration.

## Evidence gathered before editing

- CLV: 158 NCAAF ledger rows (not ~126) carry `closing_implied_probability`.
  Recomputing their closes from the snapshot history: 147 are clean
  pre-kickoff quotes of the row's own event; 11 are closes from ANOTHER
  event's contract that the decision-time join had mis-attached
  (`tsc-cfb-nhc-cencon-2026-10-24-total-56pt5`, New Haven vs Central
  Connecticut, kickoff 16:00Z, on six games kicking 19:30Z-02:00Z; and
  `tsc-cfb-robms-cencon-2026-10-31-total-59pt5` on five games). The six
  10-24 rows closed at 99c: `closing_snapshot` compares the quote's
  observed time with the ROW's kickoff, so the 17:03Z in-play quote of the
  16:00Z game passed as a "pre-kickoff" close.
- Capture: 10-17 moneyline-only came from the Polymarket league feed
  dropping spread/total markets (10-15 22:09Z to 10-21), fixed by
  event-detail hydration in `slate()`; 11-07 was the abandon-on-first-429
  rule, fixed by 79487b2c; 10-10 was 11 consecutive failed daily runs
  (cause not recoverable, logs rotated). Still live: the daily job captures
  only today's Eastern slate (no NCAAF quote before game day), and
  `_round_robin_by_kickoff` keeps an event's markets contiguous, so when the
  600 s budget runs out the late kickoffs lose every market, moneyline
  included.

## Tasks

1. `PolymarketSnapshotStore.closing_snapshot` fails closed: a candidate must
   be observed strictly before both the caller's start and the snapshot's
   own `event_start_utc`, and must not carry a non-OPEN `market_state`.
2. `cli/settle.py::_closing_probability_for_moneyline_pick`: the close must
   belong to the row's event (snapshot `event_title` names both row teams)
   and, for spread/total, to the row's exact line; otherwise CLV stays
   blank and the reason is logged. Regression test on the real 10-24 shape
   (row start 19:30Z, contract start 16:00Z, quotes 11:45Z at 56c and 17:03Z
   at 99c). Mutation-prove each guard.
3. `_round_robin_by_kickoff`: within a league, order by market rung
   (moneyline first, then first spread/total, ...) before kickoff, so every
   event gets a moneyline before any event gets a second line. Regression
   test; mutation-prove.
4. Targeted tests only (`-o addopts='' -q`, no MODEL_PREDICTION_* env), ruff.
5. SUMMARY.md with root causes, the cadence proposal (not registered), and
   parent decisions.
