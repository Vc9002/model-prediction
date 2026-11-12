---
phase: quick-261111-o7p
plan: 01
subsystem: portfolio
tags: [auto-buyer, polymarket, state-versioning, telemetry, dashboard]

# Dependency graph
requires: []
provides:
  - "AutoExecutionResult.rejected_not_qualified_decision counter, separate from rejected_not_whitelisted"
  - "whitelisted_count counts only picks that cleared every categorical gate (blacklist, whitelist, disabled sport/market)"
  - "Monotonic _state_version on auto_buyer_state.json and the NCAAB paper-trial settings file"
  - "StateVersionConflictError raised instead of silently clobbering a concurrent writer"
  - "run_auto_buyer_cycle re-applies only cycle-owned keys (last_run, last_daily_date) on conflict, preserving an operator's mid-cycle change"
  - "Both dashboard toggle routes (/api/auto-buyer/toggle, /api/auto-buyer/ncaab-paper/toggle) answer 400 refused on conflict"
affects: [auto-buyer, dashboard, ncaab-paper-trial]

# Actuals (#2632)
actuals:
  tokens: 6170
  tasks: 3
  commits: 4
  plan_head_before: 75a2498ecf7fe7bc0550f59bb4defcae9a7fc2da
  plan_head_after: d349715bc3483d8535fcd99abcaecdb21724eefc

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Shared write_versioned_state_file(path, state, temp_path=) helper for read-modify-write conflict detection across multiple JSON state files, used identically by auto_executor.py and ncaab_paper_trial.py"
    - "Cycle-owned-key re-apply on conflict: a long-running job that holds a state snapshot re-reads fresh state on conflict and merges back only the keys it itself owns, rather than retrying its whole stale copy"

key-files:
  created:
    - tests/test_auto_buyer_state_versioning.py
  modified:
    - src/model_prediction/portfolio/auto_executor.py
    - src/model_prediction/portfolio/ncaab_paper_trial.py
    - src/model_prediction/dashboard/routes.py
    - src/model_prediction/cli/daily.py
    - tests/test_auto_polymarket_buyer.py
    - tests/test_dual_auto_buyer_dashboard.py

key-decisions:
  - "Counter name rejected_not_qualified_decision; error class StateVersionConflictError(RuntimeError) so existing (OSError, RuntimeError, TypeError, ValueError) route handlers already refuse it with 400 (per plan's discretion choices)"
  - "Deviated from the plan's literal Task 1 test design: a prior commit on this same branch (75a2498e, 'Add the three hard-coded pick tags', landed just before this plan's planning snapshot) already removed the record_type == RESEARCH_OBSERVATION gate from evaluate_and_execute as an operator directive -- that check no longer exists in the qualification gate. The new test uses three live decision/reason_code-based rejection variants (decision=PASS, decision=HOLD, reason_code=PAPER_CALL_LOW_EDGE) instead of including a record_type-based pick, since that gate is gone. The counter semantics (decision != CALL OR reason_code startswith PAPER_CALL_) and the must_haves truths are otherwise satisfied exactly."
  - "Conflict rule is strictly live > expected (not !=), matching the plan: load_auto_buyer_state can seed from the repo copy when the runtime file is missing, so a missing/older live file must never raise"

requirements-completed:
  - Q-o7p-1-rejection-telemetry
  - Q-o7p-2-state-file-conflict-detection

coverage:
  - id: D1
    description: "rejected_not_qualified_decision counter is dedicated to decision-qualification rejections (non-CALL decision, PAPER_CALL_ reason code); rejected_not_whitelisted only increments at the whitelist gate; whitelisted_count only counts picks clearing blacklist, whitelist, and disabled-sport-market gates"
    requirement: "Q-o7p-1-rejection-telemetry"
    verification:
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_buyer_counts_non_call_decisions_separately_from_whitelist_rejections"
        status: pass
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_buyer_categorically_rejects_mlb_moneyline_even_when_model_is_whitelisted"
        status: pass
    human_judgment: false
  - id: D2
    description: "rejected_not_qualified_decision is surfaced in run_auto_buyer_cycle's run_summary and in cli/daily.py's auto_buyer_result"
    requirement: "Q-o7p-1-rejection-telemetry"
    verification:
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_auto_buyer_cycle_tracking"
        status: pass
    human_judgment: false
  - id: D3
    description: "Monotonic _state_version on auto_buyer_state.json; legacy files with no key load as version 0 and the first save writes version 1; a second save of the same dict does not self-conflict"
    requirement: "Q-o7p-2-state-file-conflict-detection"
    verification:
      - kind: unit
        ref: "tests/test_auto_buyer_state_versioning.py#test_legacy_state_without_version_loads_as_zero_and_saves_as_one"
        status: pass
      - kind: unit
        ref: "tests/test_auto_buyer_state_versioning.py#test_invalid_state_version_values_load_as_zero"
        status: pass
    human_judgment: false
  - id: D4
    description: "A concurrent write between load and save raises StateVersionConflictError on both the live auto-buyer state file and the NCAAB paper-trial settings file, leaving the other writer's content intact"
    requirement: "Q-o7p-2-state-file-conflict-detection"
    verification:
      - kind: unit
        ref: "tests/test_auto_buyer_state_versioning.py#test_concurrent_write_between_load_and_save_raises_conflict"
        status: pass
      - kind: unit
        ref: "tests/test_auto_buyer_state_versioning.py#test_paper_trial_concurrent_write_between_read_and_save_raises_conflict"
        status: pass
    human_judgment: false
  - id: D5
    description: "An operator's state change made while run_auto_buyer_cycle is in flight survives the cycle's end-of-run save; last_run and last_daily_date are still recorded"
    requirement: "Q-o7p-2-state-file-conflict-detection"
    verification:
      - kind: unit
        ref: "tests/test_auto_buyer_state_versioning.py#test_cycle_end_save_preserves_mid_cycle_operator_change"
        status: pass
    human_judgment: false
  - id: D6
    description: "Both dashboard toggle routes (/api/auto-buyer/toggle, /api/auto-buyer/ncaab-paper/toggle) answer 400 refused on a conflict instead of raising an unhandled exception"
    requirement: "Q-o7p-2-state-file-conflict-detection"
    verification:
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_auto_buyer_dashboard_routes"
        status: pass
      - kind: other
        ref: "code review of src/model_prediction/dashboard/routes.py POST handlers for /api/auto-buyer/toggle and /api/auto-buyer/ncaab-paper/toggle (except tuple includes RuntimeError/StateVersionConflictError)"
        status: pass
    human_judgment: false

duration: ~45min
completed: 2026-11-11
status: complete
---

# Quick Task 261111-o7p: Fix Auto-Buyer Rejection Telemetry Miscategorization Summary

**Dedicated `rejected_not_qualified_decision` counter plus monotonic `_state_version` conflict detection on both Auto-Buyer state files, closing a live-money silent-clobber bug where a mid-cycle operator disable could be reverted by the cycle's own stale end-of-run save.**

## Performance

- **Duration:** ~45 min (resumed mid-session after an interruption between Task 1 and Task 2; timestamps below cover the resumed portion)
- **Tasks:** 3/3 completed
- **Files modified:** 6 (1 new test file, 5 modified)

## Accomplishments
- Rejection funnel telemetry no longer conflates two distinct reasons a pick never reaches execution: `rejected_not_qualified_decision` (the model itself never called it) is now separate from `rejected_not_whitelisted` (the model isn't on the whitelist). `whitelisted_count` now only credits picks that cleared blacklist, whitelist, AND the disabled sport/market block.
- `auto_buyer_state.json` and the NCAAB paper-trial settings file both carry a monotonic `_state_version`. Any save that finds the live on-disk version has advanced past what it read raises `StateVersionConflictError` instead of overwriting the other writer's change.
- `run_auto_buyer_cycle` (which holds its state snapshot for the whole cycle -- minutes for a live run) now survives a conflict by reloading fresh state and re-applying only the keys it owns (`last_run`, `last_daily_date`), so an operator's mid-cycle disable of the live buyer is never silently reverted by the cycle's own end-of-run save.
- Both dashboard toggle routes (`/api/auto-buyer/toggle`, `/api/auto-buyer/ncaab-paper/toggle`) now answer `400 refused` on a conflict instead of raising.

## Task Commits

Each task was committed atomically:

1. **Task 1: Tracer — rejected_not_qualified_decision counter end-to-end, post-categorical whitelisted_count** - `e8bb060c` (feat)
2. **Task 2: Versioned auto-buyer state save with conflict detection, cycle-owned-key re-apply, toggle-route refusal** - `ddb319a0` (feat)
3. **Task 3: NCAAB paper-trial save through the versioned helper, route refusal, regression-test revert check** - `d349715b` (feat)

**Note on commit range:** `git rev-list --count` over `75a2498e..d349715b` reports 4 commits. One of these, `fffc753c` ("Tag paper buyer rows shadow and drive dashboard manual orders off pick_tag"), was authored by Vincent directly (not by this plan's execution) and landed concurrently between this plan's Task 1 and Task 2 commits -- confirmed via `git log`/`git show` author and content (touches `dashboard/common.py`, `dashboard/orders.py`, `auto_buyer_ledger.py`, none of which this plan's `files_modified` lists). It is unrelated to this task; this plan's own work is exactly the 3 commits listed above. `actuals.commits: 4` is the measured range count per the SDK contract; the 3 task commits are the plan's actual output.

_No TDD refactor-stage commits were needed -- GREEN implementations matched the RED tests on the first pass for all three tasks._

## Files Created/Modified
- `src/model_prediction/portfolio/auto_executor.py` - Added `rejected_not_qualified_decision` field; renamed the counter incremented at the decision-qualification check; moved `whitelisted_count` increment to after the disabled-sport-market block; added `STATE_VERSION_KEY`, `StateVersionConflictError`, `_coerce_state_version`, `read_state_file_version`, `write_versioned_state_file`; `load_auto_buyer_state`/`save_auto_buyer_state` now version-aware; `run_auto_buyer_cycle`'s final save catches `StateVersionConflictError` and re-applies cycle-owned keys onto fresh state
- `src/model_prediction/portfolio/ncaab_paper_trial.py` - `set_paper_trial_enabled` now saves through the shared `write_versioned_state_file` helper instead of its own manual temp-write
- `src/model_prediction/dashboard/routes.py` - `/api/auto-buyer/toggle` now wraps `toggle_auto_buyer` in a try/except and answers 400 refused; `/api/auto-buyer/ncaab-paper/toggle`'s except tuple gained `RuntimeError`
- `src/model_prediction/cli/daily.py` - `auto_buyer_result` dict now surfaces `rejected_not_qualified_decision`
- `tests/test_auto_polymarket_buyer.py` - New test `test_buyer_counts_non_call_decisions_separately_from_whitelist_rejections`; extended `test_buyer_categorically_rejects_mlb_moneyline_even_when_model_is_whitelisted` and `test_auto_buyer_cycle_tracking`
- `tests/test_auto_buyer_state_versioning.py` (new) - Six tests covering legacy-file backward compat, invalid version coercion, concurrent-write conflicts on both state files, and mid-cycle operator-change preservation
- `tests/test_dual_auto_buyer_dashboard.py` - Updated exact-dict assertions in `test_paper_trial_toggle_is_independent_and_preserves_bucket` to expect `"_state_version": 1`

## Decisions Made
- Counter name `rejected_not_qualified_decision`; `StateVersionConflictError(RuntimeError)` so existing route exception tuples already refuse it with 400 (plan's own discretion choice, followed as specified).
- Conflict rule is strictly `live > expected`, not `!=` (plan's own discretion choice, followed as specified): a missing/older live file (e.g. `load_auto_buyer_state` seeding from the repo copy) must never raise.
- Deviated from the plan's literal Task 1 test design regarding the `record_type == RESEARCH_OBSERVATION` scenario -- see Deviations below.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - stale plan assumption vs. current code] Dropped the record_type == RESEARCH_OBSERVATION test scenario; used two decision-based variants instead of one**
- **Found during:** Task 1 (writing the RED test for `test_buyer_counts_non_call_decisions_separately_from_whitelist_rejections`)
- **Issue:** The plan's behavior spec and must_haves truths describe a pick rejected because of `record_type == RESEARCH_OBSERVATION` landing in the qualification-rejection counter. Reading the live code at `evaluate_and_execute`'s "Mirror Main Ledger qualification" check (~L1633-1639) showed that gate only checks `decision != CALL` and `reason_code.startswith("PAPER_CALL_")` -- there is no `record_type` check anywhere in `auto_executor.py` (grep-verified, zero matches). The adjacent code comment explains why: "Tags name destinations (operator directive 2026-11-11), so a row's legacy record_type no longer blocks it." This change landed in commit `75a2498e` ("Add the three hard-coded pick tags"), which is the exact commit this plan's planning snapshot was taken against (both dated 2026-11-11) -- the plan's context section was evidently written from a moment just before that commit, or before its full effect was traced through this gate.
- **Fix:** Designed the new test with three decision/reason_code-based rejection variants that DO exist in the live gate (`decision="PASS"`, `decision="HOLD"`, `decision="CALL"` + `reason_code="PAPER_CALL_LOW_EDGE"`) instead of one of the three being record_type-based. This still produces `rejected_not_qualified_decision == 3`, `rejected_not_whitelisted == 1`, `whitelisted_count == 0` exactly as the plan's assertions specify -- only the *mechanism* of one of the three rejected picks changed, not the counter semantics or the test's pass/fail outcome.
- **Files modified:** tests/test_auto_polymarket_buyer.py
- **Verification:** RED run confirmed the test fails before the GREEN implementation (missing field); GREEN run confirms all three assertions pass with the live code's actual gate conditions.
- **Committed in:** e8bb060c (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (test design adjusted to match a very recently (same-day) landed, intentional code change that the plan's planning snapshot had not fully traced through this gate).
**Impact on plan:** No scope creep, no behavior change beyond what the plan specified. The counter rename, whitelisted_count reordering, and all must_haves truths are satisfied exactly as written; only one test's input design used a currently-live rejection mechanism instead of a since-removed one.

## Issues Encountered
- Session was interrupted between Task 1 (staged, uncommitted) and Task 2. Verified actual on-disk/git state before resuming rather than trusting the interruption summary at face value: confirmed Task 1's three files were staged exactly as expected via `git status --short` and `git diff`, re-ran Task 1's verify command to confirm it still passed, then committed it before proceeding to Task 2.
- Found two unrelated unstaged test fixups already in the working tree (`tests/test_dashboard_server.py` adding `"pick_tag": "qualified"` to fixtures, `tests/test_ncaab_auto_buyer_paper_trial.py` monkeypatching `load_auto_buyer_state`) that were not part of this plan's `files_modified`. Verified both are necessary baseline fixups (their affected tests pass with the changes present) rather than stray edits, and left them untouched/uncommitted per this plan's scope boundary -- they are not this plan's output. One of the two (`test_dashboard_server.py`'s edit) was subsequently committed by Vincent directly in the unrelated concurrent commit `fffc753c` while this plan's Task 2 work was in progress; `test_ncaab_auto_buyer_paper_trial.py`'s edit remains unstaged in the working tree, untouched by this plan.
- `git commit` ran `ruff format` via the pre-commit hook during the Task 2 commit attempt, which reformatted one long line in `write_versioned_state_file` (wrapped a `.write_text(...)` call) and aborted that commit attempt (pre-commit hooks always abort the first attempt when they modify files). Re-staged the reformatted file, re-verified tests and ruff, and committed successfully on the second attempt -- no logic changed, purely a line-wrap.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Both fixes are additive and backward-compatible: legacy state files without `_state_version` load as version 0 and the first save writes version 1, so no migration step is needed for the live `auto_buyer_state.json` or the NCAAB paper-trial settings file on next deploy.
- `scripts/auto_polymarket_buyer.py` was intentionally left untouched (plan-verified: it prints `whitelisted_count` only and never read `rejected_not_whitelisted` by name), so no follow-up needed there.
- No blockers for closing this quick task.

---
*Quick task: 261111-o7p-fix-auto-buyer-rejection-telemetry-misca*
*Completed: 2026-11-11*

## Self-Check: PASSED

All claimed files found on disk (7 code/test files + this SUMMARY.md). All 3 task commit hashes (`e8bb060c`, `ddb319a0`, `d349715b`) verified as ancestors of HEAD via `git merge-base --is-ancestor`.
