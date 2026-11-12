---
phase: quick-261111-tx4
plan: 01
subsystem: portfolio
tags: [ncaab, paper-trial, auto-buyer, dedup, polymarket, pytest]

# Dependency graph
requires: []
provides:
  - "_paper_position_keys(row) — single game-market identity function shared by shared-paper-ledger rows and candidate pick rows"
  - "_recorded_paper_dedup_keys(paper_root) — fail-closed ledger index replacing the pick-id-only reader"
  - "Composite-key cross-cycle exclusion + intra-cycle seen-set in run_ncaab_spread_paper_trial"
  - "Four regression tests (A-D) reproducing and closing the 2026-11-11 NC Central @ VMI / event_id 203509 duplicate-buy bug"
affects: [ncaab-paper-trial, auto-buyer-worker, dashboard-paper-ledger]

# Actuals (#2632)
actuals:
  tokens: 3400
  tasks: 3
  commits: 2
  plan_head_before: 8e0701e8dcfb8afd965c31aa5ab53c9358d96784
  plan_head_after: 69856d6bc9fdd745b69962653bda81622eb705a2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single identity function shared by both sides of a dedup (ledger rows and candidate rows) to prevent the two sides drifting apart"
    - "Fail-closed ledger read: propagate OSError on an existing-but-unreadable ledger rather than returning an empty index"

key-files:
  created: []
  modified:
    - src/model_prediction/portfolio/ncaab_paper_trial.py
    - tests/test_ncaab_auto_buyer_paper_trial.py

key-decisions:
  - "Composite key is (event_id, market_type) and (away, home, UTC date, market_type), normalized exactly like auto_executor.py's _build_bought_index/_is_already_bought, so the paper trial's identity never drifts from the live buyer's own semantics."
  - "Selection and line are deliberately excluded from the key — the live buyer locks the whole game-market on either line, and pick rows store line as a string while ledger rows store a float."
  - "Over-blocking is the fail-closed direction for a dedup index: an unreadable ledger raises OSError and blocks the whole cycle instead of silently re-opening every held game (T-tx4-03)."
  - "The two pre-existing duplicate pairs already in the shared paper ledger (event_id 203460, event_id 203509) were left untouched — this plan is prevention-only, not retroactive cleanup."

patterns-established:
  - "Game-market identity keys are tuples tagged by kind (\"pick\", \"event_market\", \"matchup_market\") unioned into a single set, so a single intersection check covers all three identity strengths at once."

requirements-completed:
  - Q-tx4-1-composite-key-paper-dedup
  - Q-tx4-2-duplicate-buy-regression-test

coverage:
  - id: D1
    description: "A re-forecast NCAAB pick with a new pick_id for an already-recorded game is excluded from the paper buyer, across cycles"
    requirement: Q-tx4-1-composite-key-paper-dedup
    verification:
      - kind: unit
        ref: "tests/test_ncaab_auto_buyer_paper_trial.py#test_reforecast_of_recorded_paper_fill_is_not_bought_again"
        status: pass
      - kind: unit
        ref: "tests/test_ncaab_auto_buyer_paper_trial.py#test_held_game_blocks_moved_line_opposite_side_and_missing_event_id"
        status: pass
    human_judgment: false
  - id: D2
    description: "Two open picks for the same game inside one cycle (main + flat ledgers) pass only the first to the buyer"
    requirement: Q-tx4-1-composite-key-paper-dedup
    verification:
      - kind: unit
        ref: "tests/test_ncaab_auto_buyer_paper_trial.py#test_same_game_passes_once_per_cycle"
        status: pass
    human_judgment: false
  - id: D3
    description: "An unreadable shared paper ledger fails the cycle closed (raises before the buyer is constructed) instead of silently re-opening every held game"
    requirement: Q-tx4-1-composite-key-paper-dedup
    verification:
      - kind: unit
        ref: "tests/test_ncaab_auto_buyer_paper_trial.py#test_unreadable_paper_ledger_fails_closed"
        status: pass
    human_judgment: false
  - id: D4
    description: "Revert check proves the four new regression tests actually catch the original pick_id-only bug"
    requirement: Q-tx4-2-duplicate-buy-regression-test
    verification:
      - kind: unit
        ref: "tests/test_ncaab_auto_buyer_paper_trial.py (full file, run against the pre-fix module checked out from 8e0701e8)"
        status: pass
    human_judgment: false

# Metrics
duration: ~20min
completed: 2026-11-12
status: complete
---

# Quick Task 261111-tx4: Fix NCAAB Paper Trial Duplicate-Buy Bug Summary

**Composite game-market identity (event_id+market_type, and away/home/date/market_type) now dedups the NCAAB spread-v6 paper trial, replacing a pick_id-only check that let a re-forecast's freshly minted pick_id silently re-buy an already-recorded game.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3/3 completed
- **Files modified:** 2 (`src/model_prediction/portfolio/ncaab_paper_trial.py`, `tests/test_ncaab_auto_buyer_paper_trial.py`)

## Accomplishments

- `_paper_position_keys(row)` added as the single identity function used for BOTH shared-paper-ledger rows and candidate pick rows, so the two sides of the dedup cannot drift apart. Keys: `("pick", pick_id)`, `("event_market", event_id, market_type)`, `("matchup_market", away, home, start_date, market_type)` — normalized (`str(x or "")`, strip/lower) exactly like `auto_executor.py`'s `_build_bought_index`/`_is_already_bought`.
- `_recorded_paper_pick_ids` (pick-id-only reader) replaced with `_recorded_paper_dedup_keys`, which unions `_paper_position_keys` over every ledger row and now propagates read errors instead of swallowing them.
- `run_ncaab_spread_paper_trial`'s candidate loop now excludes a pick whose keys intersect either the recorded ledger keys (cross-cycle) or the current cycle's own `seen` keys (intra-cycle, main+flat).
- Four new regression tests (A-D) reproduce the real bug and lock in the fix; the existing runner test was isolated from the operator's real runtime paper ledger (`PAPER_AUTO_BUYER_DATA_ROOT`, `capture_unmapped_cohort_markets` monkeypatches added).
- Revert check (CLAUDE.md "Testing conventions") confirmed: against the pre-fix module checked out from `8e0701e8`, exactly the 4 new tests fail (`4 failed, 4 passed`); after restoring, the file is byte-identical to HEAD and reports `8 passed`.
- `auto_executor.py` is byte-identical to `8e0701e8` throughout — the live buyer's own dedup was never touched.

## Task Commits

Each task was committed atomically:

1. **Task 1: Cross-cycle composite-key dedup against the shared paper ledger** — `b7a25ddf` (fix)
2. **Task 2: Same-game dedup within one cycle, fail-closed ledger read, and isolation of the existing runner test** — `69856d6b` (fix)
3. **Task 3: Revert check (the new tests must catch the original bug), then restore** — no commit (verification-only task; file restored to HEAD via `git checkout`, confirmed clean)

**Plan metadata:** handled by orchestrator (STATE.md/ROADMAP.md commit is a separate step, not performed by this executor per task constraints)

_Note: Both TDD tasks followed RED (tests fail against current code) -> GREEN (tests pass after the fix) -> verified via `pytest`, `ruff check`, and `ruff format --check`._

## Files Created/Modified

- `src/model_prediction/portfolio/ncaab_paper_trial.py` — added `_paper_position_keys`, replaced `_recorded_paper_pick_ids` with `_recorded_paper_dedup_keys` (fail-closed), converted the candidate loop's `seen` set to position keys, moved `read_auto_buyer_ledger` to the top-level import.
- `tests/test_ncaab_auto_buyer_paper_trial.py` — added `_install_cohort`/`_write_paper_ledger`/`_VMI_FILL`/`_vmi_pick` test harness and tests A-D; isolated the existing runner test from the real runtime paper root; added the 3 pre-existing uncommitted lines (load_auto_buyer_state monkeypatch + max_event_lookahead_hours assert) that were pinning already-committed lookahead behavior.

## Decisions Made

- Selection and line are deliberately excluded from the identity key (see key-decisions above) — this matches the live buyer's "either line of a spread counts" rule and avoids a string-vs-float line-format mismatch between pick rows and ledger rows.
- Kept `_recorded_paper_dedup_keys`'s existing error-swallow in Task 1 and removed it only in Task 2, per the plan's staged RED/GREEN sequencing for the fail-closed behavior (test D).
- No changes to the return-dict shape, the ledger write call, or the on-disk ledger format — only the pre-filter's exclusion logic changed.

## Deviations from Plan

None — plan executed exactly as written. No Rule 1-4 auto-fixes were needed; the three tasks matched their specified behavior, action, and verification steps precisely.

## Known Stubs

None.

## Threat Flags

None — all STRIDE register items in the plan's `<threat_model>` (T-tx4-01 through T-tx4-06) were mitigated or accepted as specified; no new unaddressed surface was introduced.

## Pre-existing Duplicate Ledger Rows (left untouched, operator decision)

Both real duplicate pairs remain in the shared paper ledger exactly as they were before this fix — this task is prevention-only, not retroactive cleanup:

- **event_id 203460** (NC Central @ VMI, away +8.5): `3f661234757843c9` (2026-11-11 17:48Z) and `09543e74f6354902` (2026-11-11 21:11Z).
- **event_id 203509** (home -8.5): `0f76f199ae594b72` (2026-11-11 17:48Z) and `25d7af5ec6cd4bef` (2026-11-12T00:13Z).

Whether to clean these up (e.g. archive/remove the second fill of each pair) is a separate operator decision — see CLAUDE.md's ledger-mutation rule (`archive_settled_rows`/`remove_open_rows`, never raw deletion).

## Deliberate Semantics

A held game's spread blocks re-entry at **any line or side**, mirroring the live buyer's auto_executor.py rule (comment at ~L1544-1547): holding or having sold one side of a spread must also block the opposite side, or a refreshed pick on the moved line / opposite side would re-enter a game the trial already has a position in.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- The NCAAB paper trial's dedup is now aligned with the live buyer's own identity semantics; no further action needed for this fix to take effect on the next scheduled cycle (`auto_buyer_worker.py` already calls `run_ncaab_spread_paper_trial()` unchanged).
- The two pre-existing duplicate pairs (203460, 203509) remain open for a separate operator cleanup decision.
- `auto_executor.py` is unmodified; the live buyer's own dedup carries no risk from this change.

---
*Quick task: 261111-tx4*
*Completed: 2026-11-12*

## Self-Check: PASSED

- FOUND: src/model_prediction/portfolio/ncaab_paper_trial.py
- FOUND: tests/test_ncaab_auto_buyer_paper_trial.py
- FOUND commit b7a25ddf (ancestor of HEAD)
- FOUND commit 69856d6b (ancestor of HEAD)
- Full test file run after restore: 8 passed
- `git diff --quiet 8e0701e8 -- src/model_prediction/portfolio/auto_executor.py` exits 0 (unchanged)
