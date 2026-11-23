---
phase: quick-261122-w6z
plan: 01
subsystem: dashboard
tags: [dashboard, cache-warmer, test-isolation, interpreter-exit]
requires: []
provides:
  - forever cache-warmer loops run on daemon threads (interpreter exit is no longer joined)
  - autouse conftest guard keeping tests off live dashboard/server.log and cache_snapshot.pkl
  - isolated main() test and a subprocess exit regression test
affects: [dashboard_server.py, tests/conftest.py, tests/test_dashboard_server.py]
tech-stack:
  added: []
  patterns: [daemon threads instead of ThreadPoolExecutor for never-returning loops]
key-files:
  created: []
  modified:
    - dashboard_server.py
    - tests/conftest.py
    - tests/test_dashboard_server.py
key-decisions:
  - "forever=True branch of _warm_caches uses one daemon thread per warm loop; one-pass pool path untouched (additive diff)"
status: complete
commits: 4
plan_head_before: ba1e1ff9d5c94b1d5847a3e2af1cae3cdd056c38
plan_head_after: 3efd5d05
actuals:
  tasks: 2
  commits: 4
  review_cycles: 2
duration: ~10 min
completed: 2026-11-22
---

# Phase quick-261122-w6z Plan 01: Stop dashboard cache warmer hanging process exit Summary

Forever warm loops now run on `cache-warmer:<key>` daemon threads, so a process that started the warmer exits when its main thread returns; the main() test and a conftest guard keep tests off the live dashboard files.

## Commits

| Task | Commit | Message |
| ---- | ------ | ------- |
| 1 | dec50ef6 | test: isolate main() test from live dashboard files and shared cache state |
| 2 RED | 15a644bd | test: add subprocess regression test for forever warmer exit hang |
| 2 GREEN | 22e376e7 | fix: run forever cache-warmer loops on daemon threads so interpreter exit is not joined |
| Review fix | 3efd5d05 | fix: session-wide dashboard live-file guard; soften shutdown comment |

Each commit staged only its own files (conftest.py + test_dashboard_server.py; test_dashboard_server.py; dashboard_server.py).

## Test counts per command

- Task 1 verify (`-k "test_main_stops_cleanly or live_files_never"`): 2 passed in 1.02 s, process exited promptly (no hang).
- Task 2 RED (`-k test_forever_warmer_does_not_block_interpreter_exit`, unfixed `_warm_caches`): 1 failed in 31.00 s.
- Task 2 GREEN / final verify (`-k "warm or test_main_stops_cleanly or live_files_never"`): 7 passed in 1.97 s (new subprocess test 0.66 s).

## RED output (real, against unfixed `_warm_caches`)

```
E           Failed: child hung at interpreter exit (a forever warm loop was joined at shutdown)
E           stdout: 'WARMED\n'
E           stderr: ''

tests\test_dashboard_server.py:2577: Failed
FAILED tests/test_dashboard_server.py::test_forever_warmer_does_not_block_interpreter_exit
1 failed, 79 deselected, 1 warning in 31.00s
```

The child printed WARMED (all 7 keys cached) and then hung at interpreter shutdown until the 30 s timeout: a valid red. GREEN: same test passes in 0.66 s with return code 0.

## Verification gates (all passed)

- `ruff check` and `ruff format --check` clean on all three files.
- Non-comment `cache-warmer:` thread name present in dashboard_server.py; both one-pass pool lines occur exactly once.
- `git diff -U0 ba1e1ff9 -- dashboard_server.py` removes no lines (purely additive, 18 insertions). No peer commit touched dashboard_server.py, so the baseline stayed ba1e1ff9.
- `git rev-list --count ba1e1ff9..HEAD` = 3.

## Deviations from Plan

None to the logic. Two small notes:

- The working tree holds tests/conftest.py and tests/test_dashboard_server.py with CRLF endings (index is LF via autocrlf), contrary to the plan's "all LF" baseline note. Edits preserved CRLF; ruff format and pre-commit pass. dashboard_server.py is LF in the working tree.
- `subprocess.run(..., check=False)` added explicitly to satisfy ruff PLW1510 (behavior identical).

## Known Stubs

None.

## Threat Flags

None.

## Live-file mtimes after the final test run (2026-11-22 23:28 EST)

```
cache_snapshot.pkl  2026-11-22 23:27:44.089 -0500   (live dashboard PID 5340 rewrites this about every 5 min; before the first test run it was 23:22:30)
server.log          2026-11-22 23:07:47.230 -0500   (unchanged across all runs; it was 23:07:47 before the first run)
server.pid          2026-11-22 16:47:07.068 -0500   (unchanged)
jobs.json           2026-11-11 01:56:04.240 -0500   (unchanged)
```

server.log never moved during any of my pytest runs, and the snapshot mtimes are consistent with the live dashboard's own ~5 minute rewrite cadence (23:22:30 then 23:27:44).

## Review cycles (orchestrator, critical-reviewer)

1. **PASS WITH NITS.** There were three minor findings, all fixed in 3efd5d05:
   - The per-test guard was undone at teardown. A thread that outlives its test, which is the shape of the 2026-11-22 leak, would write the live files again.
   - The guard skipped tests that import the dashboard lazily (for example `test_auto_polymarket_buyer.py::test_auto_buyer_dashboard_routes`).
   - The warmer comment overclaimed: a builder's own pool still drains at exit, so exit can wait for one build and may log the shutdown line once.

   The guard is now one session-scoped autouse fixture. It imports `model_prediction.dashboard.common` and assigns `LOG_FILE` and `_SNAPSHOT_FILE` to a session temp directory once, never undone. The now-unused top-level `import sys` was dropped.
2. **PASS.** The remaining nit: every pytest session now imports the dashboard package, so an ImportError there would error every test, not only the dashboard ones.

## Orchestrator verification (after 3efd5d05)

- `test_dashboard_server` + cache integrity + cache concurrency + `test_auto_polymarket_buyer` + `test_consolidation_c` + `test_run_supervisor` + `test_two_stage_and_stealth`: 203 passed in 23.93 s, rc 0. Before the fix, `test_dashboard_server.py` hung at exit.
- Revert proof repeated independently with `dashboard_server.py` restored to ba1e1ff9: "Failed: child hung at interpreter exit", 1 failed in 30.85 s. Fixed: 1 passed in 1.43 s.
- Guard pin test with `--noconftest`: `assert WindowsPath('E:/MP/dashboard') != WindowsPath('E:/MP/dashboard')`, 1 failed. So it does catch a missing guard.
- Live `dashboard/server.log` mtime stayed 23:07:47 through every run.
- The full default suite was not run (project rule). The session-scoped guard affects every test, so its effect outside these seven files has been reviewed but not executed.

## Residuals

1. The live dashboard (PID 5340) still runs the pre-fix code until its next restart, so a Ctrl-C on it before then can still hang. Not restarted: the operator did not ask, and the change only matters at exit.
2. A read-only process listing found no hung pytest processes. Nothing was killed.
3. The orchestrator's first reproduction of the hang (before this plan) ran in `E:/MP` by mistake, because `git worktree add` failed silently. It appended about 10 shutdown lines to the live `server.log` (04:05-04:07 UTC) and may have written `cache_snapshot.pkl` at 23:06:46. By then the test's patches were undone, so the snapshot held real data, and the live dashboard has rewritten it since (23:22:30, 23:27:44).

## Self-Check: PASSED

- Commits dec50ef6, 15a644bd, 22e376e7 exist on refactor/cleanup-2026-11-11.
- Modified files exist: dashboard_server.py, tests/conftest.py, tests/test_dashboard_server.py.
