---
phase: quick-261123-oy5
plan: 01
subsystem: scheduler
tags: [windows, process-priority, run_supervisor]
status: complete
requirements: [quick-261123-oy5]
key-files:
  modified:
    - src/model_prediction/run_supervisor.py
    - tests/test_run_supervisor.py
    - docs/ROADMAP.md
    - docs/PROJECT_STATUS.md
    - docs/DEBUG.md
    - .claude/CLAUDE.md
actuals:
  tasks: 2
  commits: 3
plan_head_before: da7de5616d39c18d359cff5b0e8f19990e6f6573
plan_head_after: a3c454d9
commits: 3
completed: 2026-11-23
---

# Phase quick-261123-oy5 Plan 01: Worker low memory priority Summary

`run_supervisor.main()`'s `run <worker>` branch now calls `enable_background_priority()` after the usage check and before `run_worker`, so every scheduled worker tree is born BELOW_NORMAL (0x4000) with memory priority 2. `status`, `runs`, usage errors and in-process `run_worker` callers are never demoted.

## Commits

- `09cf9ceb` feat(quick-261123-oy5): run_supervisor main() demotes itself so every scheduled worker tree runs at BELOW_NORMAL and low memory priority (run_supervisor.py, tests/test_run_supervisor.py)
- `638ebb13` docs(quick-261123-oy5): record worker low memory priority and the w6z dashboard restart (ROADMAP, PROJECT_STATUS, DEBUG, .claude/CLAUDE.md)

## Red evidence (before the call existed; import only)

Test d `test_main_run_worker_tree_inherits_low_priority`:

```
E       AssertionError: stdout='PRIORITY mem=5 class=0x20\n' stderr=''
E       assert 5 == 2
```

Test e `test_main_run_demotes_once_before_the_worker`:

```
E       AssertionError: assert ['run_worker:w'] == ['background', 'run_worker:w']
E         At index 0 diff: 'run_worker:w' != 'background'
E         Right contains one more item: 'run_worker:w'
```

Test f passed at red (expected: nothing demoted yet). Run result: `2 failed, 1 passed`.

## Placement mutation (test f)

Added an extra `enable_background_priority()` as the first line inside the try block (before `cmd = args[0]`); test f failed:

```
E       AssertionError: assert 'background' not in ['background', 'background', 'background', 'background', 'background']
1 failed, 1 warning in 0.71s
```

Mutation removed; final diff of run_supervisor.py is only the import, the why-comment and the call.

## Green

- Test d alone: `1 passed, 1 warning in 0.88s`
- `tests/test_run_supervisor.py tests/test_two_stage_and_stealth.py`: `25 passed, 1 warning in 2.28s` (zero skips)
- `ruff check` and `ruff format --check` clean on both .py files. All six touched files are 100% CRLF.

## Deviations from Plan

1. **[Rule 1 - Bug in plan's test design] Driver relays the grandchild's output.** With the real `model_prediction.run_supervisor` import chain loaded, a grandchild launched with inherited stdout wrote nothing to the test's pipe (returncode 0, empty stdout), while a plain `python -c` child inherited fine. Reproduced in a standalone driver. The stub's `run_worker` now runs the grandchild with `capture_output=True` and writes its stdout back, so the PRIORITY line still comes from the real grandchild process. Root cause of the lost inheritance was not chased; it does not affect the priority measurement.
2. **MODEL_PREDICTION_SKIP_DOTENV=1 departs from the orchestrator's "strip every MODEL_PREDICTION_* var" wording, on purpose.** Every MODEL_PREDICTION_* var is stripped, then SKIP_DOTENV=1 is set so the child can never read the operator's `.env` (live runtime root, Polymarket credentials), matching the 261122-w6z subprocess test.
3. **Line endings.** The Edit tool converted whole files to LF on larger edits. All six files were converted back to CRLF with a small Python rewrite (line-ending only) before each commit; verified 0 LF-only lines.
4. **Placement mutation form.** Done as an extra call before `cmd = args[0]` rather than a literal move; it fails test f for the same reason.
5. The pre-commit hook stashed and restored peer sessions' unstaged files during both commits; nothing of theirs was staged or lost.

## Known Stubs

None.

## Self-Check: PASSED

Commits 09cf9ceb and 638ebb13 exist on refactor/cleanup-2026-11-11; the six modified files exist; SUMMARY not committed (orchestrator handles it).

## Orchestrator verification and review

- Independent revert proof (call replaced by `pass`, then `git checkout`): inheritance test `assert 5 == 2`, ordering test `assert ['run_worker:w'] == ['background', 'run_worker:w']`, 2 failed; restored, 2 passed.
- Extra probe: a grandchild started through PowerShell `Start-Job` (the shape of run_daily.ps1's ingest step) under a demoted parent reports `mem=2 class=0x4000`, so `Start-Job` hosts inherit too.
- Review cycle 1 (critical-reviewer): PASS WITH NITS. Two minor findings, both fixed in `a3c454d9`:
  1. The baseline guard skipped as "already demoted" even when the probe itself failed. It now asserts the probe parsed.
  2. POSIX `os.nice(10)` stacked (supervisor plus the auto-buyer's own call gave nice 19). `enable_background_priority` now raises niceness only up to the target. A new test, `test_posix_nice_is_raised_to_the_target_not_stacked`, failed `assert 19 == 10` with the fix reverted and passes with it.
- Review cycle 2: PASS. One optional nit left as is: the POSIX log line says "(10)" even when inherited niceness is higher.
- Final: `tests/test_run_supervisor.py tests/test_two_stage_and_stealth.py`, 26 passed in 2.51 s; ruff check and format clean. Full suite not run (project rule).

## Executor deviation explained

The executor saw a grandchild's inherited stdout vanish once `model_prediction` was imported. Cause: `src/model_prediction/__init__.py` adds `CREATE_NO_WINDOW` to every `subprocess.Popen` on Windows, and a child created with that flag and no explicit std handles gets its own hidden console. Production consequence (pre-existing, not changed here): `daily_lock.py:101` launches the inner `run_daily.ps1` that way, so every `daily-*.log` under the supervisor log root is 0 bytes. Logged as an open item in `docs/ROADMAP.md`.

## Residuals

- The first scheduled run on this code is the 19:30 ET daily (the 18:00 ET run started before `09cf9ceb`). Scheduled workers run S4U and cannot be probed from the interactive session, so the live effect rests on the tests and host probes.
- Task Scheduler priority 7 already gave BELOW_NORMAL CPU, so for scheduled runs the new effect is low memory priority. Manual `run <worker>` invocations now also get BELOW_NORMAL.
