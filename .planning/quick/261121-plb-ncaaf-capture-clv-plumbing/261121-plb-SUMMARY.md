---
phase: quick-261121-plb
plan: 01
status: complete
commits:
  - 0630faa2 fix(clv): a Polymarket close is never quoted after the contract's own kickoff
  - acd665f9 fix(clv): settlement close must match the row's line and side by whole word
  - 83640460 fix(capture): breadth-first /book queue so a cut budget still quotes every game
  - (this commit) fix(clv): log every blank-CLV reason; pin the removed long-side default
---

# Quick 261121-plb: NCAAF capture and CLV plumbing (overhaul step 2)

No champion-pick change, no ledger mutation, no scheduled-task change.

## CLV root causes (fixed)

1. `data_sources/polymarket_us.py` `PolymarketSnapshotStore.closing_snapshot`
   (base :932-957) bounded candidates only by the ledger row's kickoff. Six
   10-24 flat rows (19:30Z-02:00Z) had been priced at decision time on the New
   Haven vs Central Connecticut total (`tsc-cfb-nhc-cencon-2026-10-24-total-56pt5`,
   kickoff 16:00Z; the CCSU name collision), so its 17:03Z in-play Under ask of
   99c became their close (CLV +0.43). Now: latest quote observed strictly before
   the earlier of the row's and the contract's own `event_start_utc`, from a book
   whose `market_state` is OPEN (or absent, for legacy rows). Shared by every
   settle path and the auto-buyer.
2. `cli/settle.py` `_closing_probability_for_moneyline_pick`, Over/Under branch
   (base :673-692): substring match, so "Thundering Herd", "Thunder",
   "Sounders", "Sunderland" read as Under. Now whole-word. The base's
   "unmatched selection -> long side" default is removed: an unproven pairing
   falls through to the name match, which declines.
3. Same function: a spread/total close must be quoted at the row's own line
   (magnitude), else blank. Every blank path now logs a reason
   (`no_pre_kickoff_open_quote`, `close_line_mismatch`,
   `close_spread_side_unresolved`, `close_side_unresolved`, `close_ask_unusable`).
   Not persisted in the ledger (no schema field).

Known limit, not fixable at close time: a row mis-joined at decision time to
another game's contract still gets that contract's pre-kickoff close (the six
99c rows now read 56c). A title-vs-team identity check was planned (PLAN task 2)
and dropped: on real rows it failed clean NFL (142), NBA (248) and hockey (88)
closes whose titles use abbreviations ("PHI Eagles vs. JAC Jaguars"). A
start-time check cannot catch same-kickoff collisions either.

## Real-data check (branch vs base a3cbdac7, recomputed closes)

3,497 settled/archived/superseded rows with a stored close in NCAAF, NBA, NFL,
MLB, WNBA, NCAAB and soccer. Differences from base:
- NCAAF 6 archived 10-24 totals: 99c -> 56c (fix 1).
- 4 base side swaps fixed (fix 2): `cbf96945def24f2a` NBA OKC home (stored and
  base 31c, now 70c), `bc6b84b5dcc4423e` NCAAB Marshall home (stored and base
  47c, now 54c); `c34634562baf41ef` NCAAF South Alabama away (base 45.5c, stored
  and now 55c); `fa8b084a8dfe4d78` NBA OKC (base 27c, stored and now 74c).
  The first two STORED closes are on the wrong side.
- 146 soccer double-chance/draw closes now blank. Checked `c9c0207f026f4cf5`:
  a 1X pick priced at 76c (No on the away-win market) stored the Yes ask 25c as
  its close. Those stored closes are on the wrong side; blank is correct.
- 2 MLB rows (`75891af5a01045fa`, `a3a0c298a555442a`) now blank: their only
  pre-game quote is a HALTED book (12 h pre-game).
- Everything else identical. Two NCAAF settled rows (`399cecbac0914a12`,
  `90ad1825056a49cb`) store an earlier quote than the latest pre-kickoff one
  under base and branch alike; cause unverified.
- Still-settled collision row: `2b9c7715d9304cf5` (South Carolina vs Tennessee,
  flat total) priced on the Charleston Southern vs Tennessee State contract,
  same kickoff. All 11 rows on the two CCSU contracts were archived 11-18.

## Capture root causes

- 10-17 moneyline only: the league feed carried only winner markets from
  10-15 22:09Z to 10-21 (subagent-reported from capture files); fixed by
  event-detail hydration in `slate()` (`polymarket_us.py:594-609`, first in
  a5e7d1a9, 10-23, verified with `git log -S HYDRATED_LEAGUES`).
- 11-07 (38 events, none quoted pre-kickoff): the first 429 abandoned the
  queue; NCAAF contracts failed `source_rate_limited_after_http_429`
  (E:\MP\data\logs\daily_2026-11-07.log:30189 onward, verified). Fixed by
  79487b2c (pacer, per-league round-robin, tiers).
- 10-10 (53 events, 4 quoted pre-kickoff): subagent-reported 11 consecutive
  daily runs failing within 2 s (10-08 04:30Z to 10-10 16:00Z, runs.db); only
  the 22:00Z run captured, after most kickoffs. Cause unknown (logs rotated).
- Still live, fixed here: `_round_robin_by_kickoff` kept an event's markets
  together, so a cut 600 s budget lost late kickoffs entirely. Now breadth-first:
  every moneyline, then each event's most balanced line per type, then the rest.
  `test_priority_league_shortlists_each_game_before_other_sports` was updated
  to the breadth-first order (same four contracts).
- Still live, cadence (not changed): capture runs only inside the daily job
  (8 triggers 00:30-20:30 local, `scripts/register_windows_tasks.ps1:32`) and
  only for today's Eastern slate (`daily.py:298-306`; `slate()` date filter
  `polymarket_us.py:582`). No NCAAF quote is taken before game day, and the
  stored "close" is the last daily run before kickoff, up to ~3 h stale.

## Proposals (operator decision, not done)

- Day-ahead NCAAF capture: a third callable in `_serialized_polymarket_captures`
  (`daily.py:167`) for D+1 (D+2 on Thursdays) with `ladder_keep={"NCAAF": 2}`,
  `max_capture_seconds` ~240, filed under its own date. It spends /book budget;
  whether a Saturday already overruns one run is a projection (no Saturday has
  run on 79487b2c + this branch yet; check `failures_by_league` 11-21).
- True close: a short pre-kickoff capture (e.g. every 30 min on game days, only
  contracts of open picks kicking within 45 min), registered by the operator.
- Persist the blank-CLV reason in the ledger (schema field).
- Whether HALTED pre-game books should count as closes (now they do not).

## Tests

`pytest tests/test_closing_snapshot_identity.py tests/test_polymarket_us.py
tests/test_polymarket_snapshot_store_prefilter.py tests/test_ncaab_settlement_clv.py
tests/test_fix_regressions.py tests/test_cli.py -o addopts=''` (no
MODEL_PREDICTION_* env): 180 passed, 1 failed:
`test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error`, which
fails identically at base (the test patches `main_ledger`/`flat_ledger`, but
`_open_pick_slugs()` without a sport reads `existing_*_ledgers`). Also
`tests/test_daily_cycle_smoke.py` passed. Mutation proofs (each reverted guard
fails its test): own-kickoff bound 3 tests; OPEN-state 1; line 1; whole-word
Over/Under 1; removed long-side default 1; rung ordering 1. ruff clean.
