---
status: complete
quick_id: 261121-xaw
branch: fix/settle-supervisor-2026-11-21
base: 387c0489
commits: [c74ff8e1, a9349f71]
---

# 261121-xaw Summary

Two fixes, two commits, nothing pushed or merged. All pytest runs: `PYTHONPATH=src`, `env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY`, `model_prediction.__file__` = `E:\MP-wt\fix-2026-11-21\src\model_prediction\__init__.py`.

## Fix 1 (c74ff8e1): `_settle_by_resolution` no longer voids on non-binary terminal prices

Files: `src/model_prediction/cli/settle.py`, `tests/test_resolution_settlement.py`.

Change: terminal + prices not `[0.0, 1.0]` -> `logger.warning(slug, prices)` and return `{"pick_id", "reason": "market terminal with non-binary prices [..] for <slug>"}`; `ledger.void` is not called. New module constant `_NON_BINARY_TERMINAL_REASON`. `_TERMINAL_MARKET_STATES` / `_market_is_terminal`, the esports path and `portfolio/auto_buyer_ledger.py:~2173` untouched.

Tests added (in `test_resolution_settlement.py`, the file that already tests this function):
- HALTED and CLOSED with 0.41/0.59 -> pick stays open, reason returned, ledger write raises if touched.
- Clean 0/1 and 1/0 under HALTED/CLOSED still settle win/loss.
- Non-terminal OPEN with 0.41/0.59 -> None.
- Caller-level: ESPN-fallback row 9 days old, non-binary result -> ends in `failures`, not `voided`.

Existing test that pinned old behavior: `test_total_resolution_settles_and_unresolved_or_unclean_stay_pending` asserted `out["voided"]` and `"non-binary" in ledger.voided`. Changed only to assert the new behavior (reason returned, no void, no settle) with a comment explaining why. Not skipped or deleted.

Before (fix reverted via `git show HEAD:... >` file overwrite): `4 failed, 24 passed`:
FAILED test_total_resolution_settles_and_unresolved_or_unclean_stay_pending; FAILED test_terminal_market_with_trading_prices_leaves_the_pick_open[MARKET_STATE_HALTED]; same [MARKET_STATE_CLOSED]; FAILED test_espn_fallback_reports_non_binary_terminal_and_does_not_age_out_to_void (this one fails on the missing constant under the reverted code, not on the void itself).
After (fix restored): `tests/test_resolution_settlement.py` `28 passed`; with `test_ncaaf_settle_fixes.py` + `test_fix_regressions.py`: `109 passed`.

### Callers of `_settle_by_resolution` and handling of the new `reason`-only dict
- settle.py ~142 (KBO/NPB), ~171 (tennis), ~205 (darts), ~230 (EuroLeague): `X or _settle_by_resolution(...)`; a reason dict is not None, so it skips the age-based void branch, is neither voided nor settled, and falls to `failures.append(result)` (tennis: only `UNSUPPORTED_*` prefixes are voided; ours is not one). Correct, no change.
- settle.py ~285 (generic, ESPN has no record): MISHANDLED. Only settled/voided dicts were checked; a reason dict was dropped and execution fell through to the 7-day "event unrecorded on ESPN" void, which would have re-created the push. Fixed minimally: a reason starting with `_NON_BINARY_TERMINAL_REASON` goes to `failures` and `continue`s. Other reason dicts (side ambiguous/unresolved, settle error) keep their old fall-through, deliberately unchanged.
- Esports path does not call this function.

## Fix 2 (a9349f71): supervisor records child exit 75 as `skipped`

Files: `src/model_prediction/run_supervisor.py`, `src/model_prediction/system_health.py`, `tests/test_run_supervisor.py`, `tests/test_system_health.py`.

Change: `exit_code == _LOCK_BUSY_EXIT and note is None` -> status `skipped`, note `worker exited 75 (lock busy; coalesced skip)`; `run_worker` still returns 75. `system_health` skipped-row reason now quotes `row['note']` instead of the hard-coded "(lease held)"; skipped still degrades.

Tests added: exit 75 -> skipped, exit_code 75, note, returns 75; exit 1 still failed, exit 0 still completed; timeout-killed run (wait patched to return 75 after TimeoutExpired) stays failed with timeout note; `system_health` with a daily run exiting 75 -> runs.daily skipped/75, overall DEGRADED, no "last run failed", reason contains the row note.

Before (supervisor reverted): `3 failed, 37 passed`:
FAILED test_worker_exit_75_is_recorded_skipped_and_still_returned (`assert 'failed' == 'skipped'`); FAILED test_system_health.py::test_worker_exit_75_lock_skip_is_degraded_not_down (`assert 'failed' == 'skipped'`); plus the pre-existing failure below. The exit-1/exit-0/timeout tests pass both before and after by design (they pin unchanged behavior).
After: `tests/test_run_supervisor.py tests/test_system_health.py tests/test_system_health_start_offsets.py tests/test_ci_contracts.py`: `1 failed, 44 passed` (the 1 is the pre-existing failure).

Other readers of runs-table `failed`/status (none needed changing):
- `system_health.py:530` (only reader treating `failed` as DOWN; now sees skipped for 75).
- `scripts/health_and_data_gap_monitor.py:302-421`: flags any daily status `!= "completed"`; a 75 skip is still flagged, as a failed row was. Behavior unchanged in direction.
- `dashboard/data_service.py:136` and `run_supervisor.py` CLI status/list: display only. `dashboard/jobs.py:51` already treats 75 as skip.
- `run_supervisor.py:303` orphan-closing sets failed on stale `started` rows (unrelated). `production_store.py` `_RUN_STATUSES` is a different table.

## Baseline / pre-existing failures
Baseline before edits: `1 failed, 139 passed` on test_run_supervisor, test_fix_regressions, test_resolution_settlement, test_ncaaf_settle_fixes, test_system_health, test_system_health_start_offsets. Pre-existing failure: `tests/test_run_supervisor.py::test_worker_registry_commands_exist_on_disk` (needs `.venv/Scripts/python.exe`, absent in this worktree). Not touched.
`tests/test_dashboard_server.py` was started once alongside the others but did not finish in several minutes (slow/hanging); I killed that pytest process and did not run it to completion. It only reads the runs table for display. Not verified.
`ruff check src/ tests/`: clean. `ruff format --check` on all 6 touched files: clean.

## Surprises / open items
- `portfolio/auto_buyer_ledger.py:~2173` also records a push on non-binary outcomes (out of scope, left as is).
- The 207 existing settled-push rows (cs2 ~198, dota2 7, lol 1, darts 1) are not re-graded (operator decision).
- Callers still contain age-based void rules for rows whose resolution returns None (KBO/NPB 36h, tennis/darts/EuroLeague 7d). Those are pre-existing and out of scope; a reason-dict row bypasses them.
- Reason-dict rows now appear in `failures` on every settle run (4x/day) until the market resolves cleanly or is handled manually.

## git diff --stat 387c0489..HEAD
```
 src/model_prediction/cli/settle.py     | 38 ++++++++++-----
 src/model_prediction/run_supervisor.py |  8 +++-
 src/model_prediction/system_health.py  |  2 +-
 tests/test_resolution_settlement.py    | 86 +++++++++++++++++++++++++++++++++-
 tests/test_run_supervisor.py           | 51 ++++++++++++++++++++
 tests/test_system_health.py            | 20 ++++++++
 6 files changed, 188 insertions(+), 17 deletions(-)
```
