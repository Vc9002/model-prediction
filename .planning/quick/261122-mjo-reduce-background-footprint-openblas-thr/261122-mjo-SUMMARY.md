---
phase: quick-261122-mjo
plan: 01
subsystem: dashboard / supervisor / process footprint
tags: [performance, openblas, memory-priority, dashboard-cache, warmer]
requirements: [quick-261122-mjo]
key-files:
  modified:
    - dashboard_server.py
    - src/model_prediction/run_supervisor.py
    - src/model_prediction/stealth_runtime.py
    - src/model_prediction/dashboard_cache.py
    - src/model_prediction/dashboard/evidence.py
    - tests/test_run_supervisor.py
    - tests/test_two_stage_and_stealth.py
    - tests/test_dashboard_cache_integrity.py
    - tests/test_dashboard_server.py
status: complete
commits: 5
plan_head_before: 0c1c4d0426a0220a83ff99def4dacd6ed591b510
plan_head_after: fee8c31805ec6781d354cc678dcc2518a7824a8d
actuals:
  tasks: 3
  commits: 3
---

# Phase quick-261122-mjo Plan 01: Reduce background footprint Summary

OpenBLAS capped at one thread in the dashboard and every supervised worker, Windows low memory priority for background processes, a no-op sqlite cache refresh that no longer materialises pick rows, serialised warmer builds with a canonical-ledger fingerprint signal (max_age 1800 s), and a 2-thread xlsx evidence pool.

## Commits

| Task | Hash | Message |
| ---- | ---- | ------- |
| 1 | f4528438 | perf(quick-261122-mjo): cap OpenBLAS at one thread in dashboard and workers, low memory priority on Windows |
| 2 | 4bbfb0ac | perf(quick-261122-mjo): DashboardCache checks freshness before materialising pick_rows |
| 3 | fee8c318 | perf(quick-261122-mjo): serialise warmer builds, fingerprint the canonical ledger, max_age 1800 s, evidence pool of 2 |

## Test results (micro-targeted, `-o addopts='' -q`, MODEL_PREDICTION_* env unset)

- `tests/test_two_stage_and_stealth.py tests/test_run_supervisor.py`: 22 passed (after Task 1).
- `tests/test_dashboard_cache_integrity.py tests/test_dashboard_cache_concurrency.py`: 6 passed (after Task 2).
- `tests/test_dashboard_server.py -k "warm or evidence or fingerprint"`: 6 passed, 70 deselected (3 baseline + 3 new).
- Combined four Task 1/2 files (`test_two_stage_and_stealth`, `test_run_supervisor`, `test_dashboard_cache_concurrency`, `test_dashboard_cache_integrity`): 28 passed (23 baseline + 5 new).
- ruff check and ruff format --check clean on all nine files; the line-anchored source-order check prints "order ok".
- Live read-only smoke (real runtime root, mode=ro): `live ledger signal 106834` (> 0, matches the planner probe).

## Red-then-green evidence

- test_worker_env_caps_openblas_threads: red `'OPENBLAS=1' in 'OPENBLAS=<unset>\nOMP=<unset>\n'`; green after the `_worker_env` setdefault. test_worker_env_keeps_operator_openblas_value passes at HEAD too (it is a guard that setdefault, not assignment, is used; it cannot be red against code that sets nothing).
- test_low_memory_priority_reads_back_low_on_windows and test_low_memory_priority_failure_is_soft: red (AttributeError, no helper); green after `_set_low_memory_priority`. The Windows readback test ran, not skipped, and read MemoryPriority == 2.
- test_sqlite_refresh_skips_pick_rows_when_event_count_unchanged: red at HEAD (`assert 8 == 4`); revert proof repeated by restoring HEAD's dashboard_cache.py (same `8 == 4` failure), then restoring the fix (green).
- test_warm_builds_never_overlap: red at HEAD (`assert 7 == 1`, peak concurrency 7); green with `_WARM_BUILD_LOCK`.
- test_fingerprint_signal_tracks_canonical_ledger_events and test_fingerprint_signal_fails_soft_without_ledger: red (AttributeError, no `_canonical_ledger_signal`); green after it was added. The fail-soft test also asserts ledgers.db is not created by the read-only open.

## Deviations from Plan

- [Rule 3 - lint] After adding `import sqlite3` to dashboard_server.py ruff's isort flagged ordering (sqlite3 sorts before subprocess); fixed with `ruff check --fix`. No behavior change.
- Otherwise the plan executed as written.

## Notes for the orchestrator

- auto_buyer_worker.py also calls `enable_background_priority`, so the auto-buyer now gets low memory priority too (intended). Return value of `enable_background_priority` is unchanged (SetPriorityClass outcome only).
- The dashboard was NOT restarted and no scheduled task was touched; restart and measurement are the orchestrator's step. The running dashboard (PID 10132) still has the old code until restarted.
- Docs artifacts (SUMMARY/STATE/PLAN) were not committed, per the constraints; ROADMAP not updated.

## Residuals

1. The SWR request path (`_refresh_in_background`) still calls `_cache_rebuild` outside `_WARM_BUILD_LOCK`, so if serialised warm builds run more than 60 s past max_age a request can trigger one overlapping rebuild (TTL stays max_age + 60 = 1860 s by spec).
2. Inputs the fingerprint still does not cover (outputs/ validation JSON, production artifacts, events JSON read by status() and production_evidence) can now lag up to about 31 minutes (1800 s plus one 30 s poll) instead of about 6.

## Review cycles (orchestrator, critical-reviewer)

- Cycle 1, CHANGES REQUIRED. The major finding was that the 1800 s age rebuild also slowed "picks"/"flat-picks" quote freshness, buy_ready and order reconciliation, plus status's daily-log steps (inputs the fingerprint cannot see), from about 5 to about 30 minutes. Fixed in e5dc895e:
  - Per-key age via `_warm_max_age`: picks, flat-picks and status keep 300 s; evidence, matrix, model-ledgers and performance use 1800 s. `_TTL_OVERRIDE` is per key at max age + 60.
  - The debounce escape is capped at 300 s.
  - The closure became the module-level `_warm_fingerprint()`, with a wiring test that fails when the ledger signal line is removed (red-then-green proven).
- Cycle 2, PASS WITH NITS. The minor finding was that the escape was timed from the last build, so the first change after a quiet spell skipped the debounce. Fixed in 5325a269: it is now timed from when the change was first seen.
- Residual 2 above now applies only to the 1800 s keys (evidence, matrix, model-ledgers, performance).
- Pre-existing, out of scope:
  - The warmer builds "flat-picks" with `read_flat_picks` while the route uses `_flat_picks_decorated`.
  - `scripts/dedup_ncaaf_settled_picks.py` runs a raw `UPDATE ledger_records` with no ledger_events row, which neither DashboardCache nor the warm signal sees.

## Live measurement (orchestrator, dashboard restarted on 5325a269, PID 5340)

| | Before (PID 10132, ~27 h up) | After (12 min of 1-min samples) |
|---|---|---|
| Working set | 1,017-1,651 MB | 433-612 MB |
| Private bytes | 1,473-2,123 MB | 549-619 MB |
| CPU | rebuilt all 7 views at least every 300 s, status ~59 s each | ~110 s first warm pass, then ~1-2 s/min |

Priority BelowNormal. The peak working set reached 1,325 MB during the first warm pass on this restart, and 689 MB on the fee8c318 restart.

## Threat Flags

None beyond the plan's threat model. The new ledger connection is `mode=ro`, 1 s timeout, closed in finally, and returns -1 on any error.

## Self-Check: PASSED

- Commits f4528438, 4bbfb0ac, fee8c318 present in `git log` since plan_head_before; commit count measured as 3.
- All nine files_modified paths changed in those commits; no deletions in the final commit.
