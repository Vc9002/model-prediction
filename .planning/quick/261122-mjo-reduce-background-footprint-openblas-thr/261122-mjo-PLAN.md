---
phase: quick-261122-mjo
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - dashboard_server.py
  - src/model_prediction/run_supervisor.py
  - src/model_prediction/stealth_runtime.py
  - src/model_prediction/dashboard_cache.py
  - src/model_prediction/dashboard/evidence.py
  - tests/test_run_supervisor.py
  - tests/test_two_stage_and_stealth.py
  - tests/test_dashboard_cache_integrity.py
  - tests/test_dashboard_server.py
autonomous: true
requirements: [quick-261122-mjo]

estimate:
  tokens: 60000
  raw_tokens: 60000
  tasks: 3
  confidence: low

must_haves:
  truths:
    - "Every worker that run_supervisor launches sees OPENBLAS_NUM_THREADS=1 unless the operator already set a value; OMP_NUM_THREADS is never added"
    - "dashboard_server.py sets OPENBLAS_NUM_THREADS=1 (setdefault) before its first model_prediction import, so numpy/scipy load with one BLAS thread"
    - "On Windows, enable_background_priority also sets the process memory priority to MEMORY_PRIORITY_LOW (2); a failure is logged at debug and never raises or changes the return value"
    - "In sqlite authority, a DashboardCache.refresh whose event_count is unchanged does not call store.pick_rows; a changed event_count or force=True still rebuilds every tier"
    - "Only one cache-warmer build runs at a time across the 7 warmer threads"
    - "The warmer fingerprint changes when the canonical ledger's ledger_events grows, the max_age safety net is 1800 s, and _TTL_OVERRIDE stays max_age + 60"
    - "production_evidence parses xlsx ledgers with at most 2 worker threads"
  artifacts:
    - path: "dashboard_server.py"
      provides: "OpenBLAS cap before imports, _WARM_BUILD_LOCK, _canonical_ledger_signal, max_age 1800"
      contains: "_WARM_BUILD_LOCK"
    - path: "src/model_prediction/run_supervisor.py"
      provides: "worker env OpenBLAS cap"
      contains: "OPENBLAS_NUM_THREADS"
    - path: "src/model_prediction/stealth_runtime.py"
      provides: "_set_low_memory_priority best-effort helper"
      contains: "_set_low_memory_priority"
    - path: "src/model_prediction/dashboard_cache.py"
      provides: "freshness check before pick_rows materialisation"
    - path: "src/model_prediction/dashboard/evidence.py"
      provides: "xlsx evidence pool capped at 2"
    - path: "tests/test_dashboard_cache_integrity.py"
      provides: "regression test: no pick_rows on an unchanged event_count"
  key_links:
    - from: "dashboard_server.py _warm"
      to: "dashboard/common.py _cache_rebuild"
      via: "with _WARM_BUILD_LOCK around the _cache_rebuild call (warm lock, then per-key build lock)"
    - from: "dashboard_server.py _fingerprint"
      to: "RuntimePaths.ledgers_db ledger_events"
      via: "_canonical_ledger_signal: SELECT MAX(rowid) over a file:...?mode=ro URI, -1 on any error"
    - from: "run_supervisor._worker_env"
      to: "child worker process env"
      via: "env.setdefault"
---

<objective>
Reduce the MP project's background resource footprint so the scheduled workers and the always-on
dashboard stop interfering with foreground use on a 12-core Windows 11 host. Five changes, all
measured by the orchestrator (facts in the planning context, restated in the why-comments below):
cap OpenBLAS at one thread in the dashboard and in every supervised worker, give the background
process low memory priority, stop the dashboard cache from materialising all four ledger tiers on
every no-op refresh, serialise the cache warmer's builds and teach its fingerprint about the
canonical SQLite ledger (so the 300 s safety net can become 1800 s), and cap the openpyxl evidence
pool at 2 threads.

Purpose: the live dashboard sits at 1.0-1.65 GB working set and 1.5-2.1 GB private bytes after ~27 h,
and the 00:30 ET runs have crashed on OpenBLAS allocation failures and MemoryError.
Output: five small source edits plus regression tests; no schedule, model, pricing, or order changes.

Tracer-first does not apply: these are five independent resource knobs with no layered path to
prove end to end, so the tasks are grouped by subsystem (process-level, cache, warmer).
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md
@.claude/CLAUDE.md
@dashboard_server.py
@src/model_prediction/stealth_runtime.py
@src/model_prediction/dashboard_cache.py

Project rules that bind this task (root CLAUDE.md):
- ruff for every changed file; micro-targeted pytest only, always with `-o addopts='' -q`; never the full suite.
- pytest runs WITHOUT MODEL_PREDICTION_* env vars. The executor's shell has MODEL_PREDICTION_RUNTIME_ROOT
  set, so every pytest command below is prefixed with `env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY`
  (tests/conftest.py sets MODEL_PREDICTION_SKIP_DOTENV=1 itself, so the repo .env is not read).
- When fixing a bug, prove the regression test catches it: revert the fix, watch the test fail, restore.
- Comments explain why (measured numbers), not what.
- Do NOT restart the dashboard; the orchestrator restarts it and measures.
- The working tree has unrelated uncommitted changes (data/calibration/*.json and more). All nine files this
  plan touches were clean at HEAD 0c1c4d04. Stage only these files, by explicit path; never `git add -A` or `git add .`.

Baseline at HEAD 0c1c4d04 (recorded by the planner):
- `tests/test_two_stage_and_stealth.py tests/test_run_supervisor.py tests/test_dashboard_cache_concurrency.py tests/test_dashboard_cache_integrity.py`: 23 passed.
- `tests/test_dashboard_server.py -k "warm or evidence or fingerprint"`: 3 passed, 70 deselected.
- ruff 0.16.3 (the venv's) exempts `os.environ.setdefault(...)` between imports from E402 (verified with a probe file).

Interfaces the executor needs (from the current code):
- `dashboard_server.py`: stdlib imports at lines 24-29 (`argparse, os, subprocess, sys, time`, `from http.server import ThreadingHTTPServer`), then `import yaml` (line 31), then the first `from model_prediction...` import at line 33. `import threading as _threading` and `from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor` sit at lines 230-231. `ROOT`, `_log`, `_CACHE`, `_CACHE_LOCK`, `_cache_rebuild`, `_TTL_OVERRIDE` are re-exported from `model_prediction.dashboard.common`. `_warm_caches` (line 234) holds the nested `_fingerprint()` closure (lines 259-280), `max_age = 300.0` (line 283), the `_TTL_OVERRIDE[key] = max_age + 60` loop, and the nested `_warm(item)` loop that calls `_cache_rebuild(key, builder)` at line 308.
- `dashboard/common.py::_cache_rebuild(key, builder)` takes a per-key RLock from `_CACHE_BUILD_LOCKS`; `_refresh_in_background` (the SWR path) also calls it.
- `dashboard/common.py::_runtime_paths()` caches the FIRST successful `RuntimePaths.resolve(...)` process-wide in `_runtime_paths_cache`, and no test resets that cache.
- `RuntimePaths.resolve(repo_root=..., require_external_runtime=True)` raises RuntimeError when MODEL_PREDICTION_RUNTIME_ROOT is unset (and the .env fallback is disabled or silent); `paths.ledgers_db` is `runtime_root / "ledgers" / "ledgers.db"`. `RuntimePaths.for_test(tmp_path)` gives `runtime_root = tmp_path / "runtime"`.
- `runtime_ledger_store.py`: table `ledger_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, ...)`, one row per mutation; `RuntimeLedgerStore(paths)`, `.apply(LedgerMutation(...))`, `.event_count()`, `.pick_rows(tier=...)`, `.close()`.
- `run_supervisor.RunSupervisor._worker_env(self, run_id, log_path) -> dict[str, str]` (line 404) copies `os.environ` and sets PYTHONPATH and RUN_SUPERVISOR_* keys.
- `stealth_runtime.enable_background_priority(idle_only=False) -> bool`: on win32 sets the priority class through `ctypes.windll.kernel32.SetPriorityClass`; callers are `dashboard_server.main()` and `auto_buyer_worker.py:57`.
- `dashboard_cache.DashboardCache._refresh_from_canonical_store(conn, *, force)` (line 147); `_is_fresh(conn, tier, mtime_key)`; `_replace_tier(conn, tier, rows, state_key)`.
- `dashboard/evidence.py`: two ThreadPoolExecutors. Line 961 (inside `production_evidence`) maps `_read_evidence_ledger` over `ledger_paths`; this is the one to cap. Line 1174 maps `_build_model` over `configured_models`; leave it alone.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Process-level caps: OpenBLAS one thread in the dashboard and supervised workers, low memory priority on Windows</name>
  <files>dashboard_server.py, src/model_prediction/run_supervisor.py, src/model_prediction/stealth_runtime.py, tests/test_run_supervisor.py, tests/test_two_stage_and_stealth.py</files>
  <read_first>src/model_prediction/stealth_runtime.py (whole file, 77 lines), src/model_prediction/run_supervisor.py lines 400-413, dashboard_server.py lines 20-35, tests/test_run_supervisor.py lines 1-50, tests/test_two_stage_and_stealth.py lines 1-22</read_first>
  <behavior>
    - test_worker_env_caps_openblas_threads (tests/test_run_supervisor.py): monkeypatch.delenv both OPENBLAS_NUM_THREADS and OMP_NUM_THREADS (raising=False); run `sup.run_worker("daily", command=[sys.executable, "-c", ...])` with a child that prints `OPENBLAS=<value or <unset>>` and `OMP=<value or <unset>>` from os.environ, following the existing test_successful_run_records_completed_row pattern (log at tmp_path / "runtime" / "logs" / "supervisor" / f"{run_id}.log"). Log contains `OPENBLAS=1` and `OMP=<unset>`.
    - test_worker_env_keeps_operator_openblas_value: monkeypatch.setenv("OPENBLAS_NUM_THREADS", "4"); same child; log contains `OPENBLAS=4`.
    - test_low_memory_priority_reads_back_low_on_windows (tests/test_two_stage_and_stealth.py, `pytest.mark.skipif(sys.platform != "win32")`): `stealth_runtime._set_low_memory_priority()` returns True, and a readback through its own `ctypes.WinDLL("kernel32", use_last_error=True)` GetProcessInformation(GetCurrentProcess(), 0, byref(info), sizeof(info)) with a one-field ULONG structure gives MemoryPriority == 2. Set argtypes/restype on that private WinDLL (handle as c_void_p).
    - test_low_memory_priority_failure_is_soft: monkeypatch.setattr(ctypes, "WinDLL", a function raising OSError, raising=False); `_set_low_memory_priority()` returns False without raising, and `enable_background_priority()` still returns a bool.
  </behavior>
  <action>
Write the four tests above first and run them: the two supervisor tests fail at HEAD (`OPENBLAS=<unset>`), the two stealth tests fail with AttributeError (no helper yet). Then implement:

1. dashboard_server.py: directly after the stdlib import block (after `from http.server import ThreadingHTTPServer`, before `import yaml`, which loads no numpy) add `os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")` with a why-comment: numpy and scipy each bundle their own OpenBLAS, which commits a per-thread buffer pool sized to the core count at import (~740 MB private on this 12-core host); measured private bytes after `import dashboard_server` 903 MB -> 160 MB with one thread, working set unchanged; the dashboard does no heavy linear algebra; setdefault so an operator-set value still wins; it must stay above the model_prediction imports because they load numpy transitively. Ruff exempts os.environ modifications from E402, so no noqa is needed.

2. run_supervisor.py `_worker_env`: add `env.setdefault("OPENBLAS_NUM_THREADS", "1")` before `return env`, with a why-comment: same ~740 MB per-process OpenBLAS pool, the 00:30 ET "OpenBLAS error: Memory allocation still failed" / MemoryError crashes, scripts/run_daily.ps1 already caps it for its ingest jobs only, and this is applied to every supervised worker (daily, production, rebuild-shadow). State in the comment that OMP threads are deliberately NOT capped because xgboost/sklearn use OpenMP for training and the daily worker has a 60-minute timeout. Do not set the OMP variable anywhere.

3. stealth_runtime.py: add module-level constants `_PROCESS_MEMORY_PRIORITY = 0` (ProcessMemoryPriority in PROCESS_INFORMATION_CLASS) and `_MEMORY_PRIORITY_LOW = 2`, and a private helper `_set_low_memory_priority() -> bool`:
   - returns False immediately when sys.platform != "win32";
   - uses its own `ctypes.WinDLL("kernel32", use_last_error=True)` instance (not the shared `ctypes.windll.kernel32`, so its argtypes/restype changes do not leak into other callers in the process);
   - sets `GetCurrentProcess.restype = ctypes.c_void_p` (pointer-sized handle), `SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]`, `SetProcessInformation.restype = ctypes.c_int` (Win32 BOOL);
   - passes `ctypes.byref` of a one-field `ctypes.Structure` (`MemoryPriority`, `ctypes.c_ulong`) set to `_MEMORY_PRIORITY_LOW`, with `ctypes.sizeof` of it;
   - on a zero return logs at debug with `ctypes.get_last_error()` and returns False; catches (OSError, AttributeError, ValueError, TypeError), logs at debug, returns False; returns True on success.
   Call it inside `enable_background_priority`'s win32 branch after the SetPriorityClass result (including the BELOW_NORMAL fallback) is settled and before the `if success:` log line, ignoring its return value so `enable_background_priority`'s return stays exactly the SetPriorityClass outcome. Why-comment: low memory priority makes the memory manager trim and repurpose this process's standby pages before a foreground app's, which is the footprint lever CPU priority does not touch. Keep the existing comment explaining why BELOW_NORMAL is used instead of the background-mode flag (that mode starves disk I/O); do not switch modes. Update the function docstring so it describes BELOW_NORMAL/IDLE priority class plus low memory priority (the current text wrongly claims background mode is used).

Note for the summary: auto_buyer_worker.py also calls enable_background_priority, so the auto-buyer gets low memory priority as well; that is intended.
  </action>
  <verify>
    <automated>.venv/Scripts/python.exe -m ruff check dashboard_server.py src/model_prediction/run_supervisor.py src/model_prediction/stealth_runtime.py tests/test_run_supervisor.py tests/test_two_stage_and_stealth.py && .venv/Scripts/python.exe -c "import re,sys; s=open(sys.argv[1],encoding='utf-8').read(); f=lambda p: re.search(p, s, re.M).start(); assert f(r'^os\.environ\.setdefault\(\"OPENBLAS_NUM_THREADS\", \"1\"\)') < f(r'^import yaml') < f(r'^from model_prediction'), 'OpenBLAS cap must precede third-party imports'; print('order ok')" dashboard_server.py && env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY .venv/Scripts/python.exe -m pytest tests/test_two_stage_and_stealth.py tests/test_run_supervisor.py -o addopts='' -q</automated>
  </verify>
  <done>ruff clean on the five files; the line-anchored source-order check prints "order ok" (planner-tested: at HEAD it fails with AttributeError because the cap line is absent; on a patched copy whose comment mentions both import phrases it passes, so the why-comment cannot trip it); the stealth and supervisor test files pass, including the four new tests (the Windows readback test runs, not skipped, on this host) and every pre-existing test in both files. The four new tests were seen failing before the implementation.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: DashboardCache checks freshness before materialising each tier's pick_rows</name>
  <files>src/model_prediction/dashboard_cache.py, tests/test_dashboard_cache_integrity.py</files>
  <read_first>src/model_prediction/dashboard_cache.py lines 116-178 and the `_is_fresh` definition (line 288); tests/test_dashboard_cache_integrity.py lines 1-10 and 63-75 (DashboardCache construction with ledger_store=)</read_first>
  <behavior>
    - test_sqlite_refresh_skips_pick_rows_when_event_count_unchanged: a fake ledger store class with an `events` int, `event_count()` returning it, and `pick_rows(*, tier)` appending tier to a `calls` list and returning []. Build `DashboardCache(tmp_path / "repo-data", db_path=tmp_path / "dashboard-cache.db", authority="sqlite", ledger_store=fake)`.
    - First `refresh()` returns {"main": 0, "flat": 0, "research": 0, "gated_research": 0} and `calls` has 4 entries.
    - Second `refresh()` with events unchanged returns {} and `calls` is still 4 entries (at HEAD it is 8: this is the red case).
    - After `fake.events += 1`, `refresh()` returns all four tiers again and `calls` has 8 entries.
    - `refresh(force=True)` with no change returns all four tiers and `calls` has 12 entries.
  </behavior>
  <action>
Add the test above to tests/test_dashboard_cache_integrity.py and run it: it must fail at HEAD on the second-refresh assertion (8 calls).

Then, in `_refresh_from_canonical_store` (per the orchestrator's measured finding that every no-op refresh materialised all four tiers, ~11 s CPU in the profile, and `_read_split_picks` refreshes once per tier read): read `event_count` once as now; compute `column_key` once, before the tier loop; inside the loop compute `state_key` first and `continue` when `not force and self._is_fresh(conn, tier, state_key)`; only then build `rows` from `store.pick_rows(tier=tier)` (same RETIRED_STATUSES filter and `_dashboard_row` mapping), call `_replace_tier`, and set `counts[tier]`. state_key format, tier order, the owned-store open/close, and the return value stay byte-for-byte identical in meaning. Add a short why-comment above the freshness check citing the ~11 s per no-op refresh.

After the test passes: prove it, per CLAUDE.md, by temporarily restoring the old order (rows built before the freshness check), watching the new test fail, then restoring the fix and re-running.
  </action>
  <verify>
    <automated>.venv/Scripts/python.exe -m ruff check src/model_prediction/dashboard_cache.py tests/test_dashboard_cache_integrity.py && env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY .venv/Scripts/python.exe -m pytest tests/test_dashboard_cache_integrity.py tests/test_dashboard_cache_concurrency.py -o addopts='' -q</automated>
  </verify>
  <done>ruff clean; both dashboard-cache test files pass, including the new test; the new test was observed failing at HEAD and again during the revert proof; the existing test_sqlite_cache_uses_canonical_rows_and_preserves_clv_odds still passes unchanged.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Serialise warmer builds, add the canonical-ledger signal to the fingerprint, raise max_age to 1800 s, cap the evidence xlsx pool at 2</name>
  <files>dashboard_server.py, src/model_prediction/dashboard/evidence.py, tests/test_dashboard_server.py</files>
  <read_first>dashboard_server.py lines 226-331 (warmer); src/model_prediction/dashboard/evidence.py lines 950-968; tests/test_dashboard_server.py lines 1-25 (autouse fixture sets MODEL_PREDICTION_RUNTIME_ROOT to a per-test empty dir) and 2343-2370 (test_cache_warmer_builds_every_entry_without_failures, which asserts no `_log` messages); tests/test_dashboard_cache_integrity.py lines 51-61 (minimal "remove" LedgerMutation shape)</read_first>
  <behavior>
    - test_warm_builds_never_overlap: patch `_log` to collect messages, clear `_CACHE`, and patch the six builder names (status, matrix, production_evidence, model_ledger_comparison, dashboard_picks, read_flat_picks) plus `performance_for_sport` with one tracking builder (accepts *args, **kwargs; under a threading.Lock increments an active counter and records the peak, sleeps 0.05 s, decrements, returns {}); patch read_picks=list and _load_archive like the existing warm test. After `dashboard_server._warm_caches()`: peak == 1, messages == [], and all 7 warmer keys ("status", "matrix", "production-evidence", "model-ledgers", "picks", "flat-picks", "performance:all") are in `_CACHE`. Clear `_CACHE` at the end. At HEAD the peak is greater than 1 (red).
    - test_fingerprint_signal_tracks_canonical_ledger_events: `paths = RuntimePaths.for_test(tmp_path)`, monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(paths.runtime_root)), `store = RuntimeLedgerStore(paths)`, apply one minimal "remove" LedgerMutation (pick_id, operation_id, ledger_tier="main", sport, event_type="remove", created_at_utc, status="removed"); `s1 = dashboard_server._canonical_ledger_signal()` is >= 1; apply a second mutation with a different pick_id/operation_id; `s2 > s1`; close the store.
    - test_fingerprint_signal_fails_soft_without_ledger: with the runtime root pointing at an empty tmp dir, the signal is -1 and `<root>/ledgers/ledgers.db` still does not exist afterwards (read-only open never creates the file); with MODEL_PREDICTION_RUNTIME_ROOT deleted (conftest disables the .env fallback, so resolve raises RuntimeError) the signal is still -1.
  </behavior>
  <action>
Write the three tests first (their names contain "warm" or "fingerprint" so the `-k` filter selects them) and run them: all three fail at HEAD (overlap peak above 1; `_canonical_ledger_signal` missing).

Then in dashboard_server.py:

1. Add `import sqlite3` to the stdlib block and `from model_prediction.runtime_paths import RuntimePaths` to the model_prediction import group (alphabetical: after `model_prediction.dashboard.status`, before `model_prediction.stealth_runtime`).

2. Next to `_threading` / `_ThreadPoolExecutor` (around line 231) add a module-level `_WARM_BUILD_LOCK = _threading.Lock()` with a why-comment: the 7 warmer threads used to rebuild concurrently; a probe of 4 concurrent passes with the cache retained grew working set 617 -> 719 -> 710 -> 1032 MB with a 1.7 GB peak while cache contents were unchanged (stacked build peaks fragment the heap), whereas sequential builds peaked ~1.6 GB and settled ~560 MB.

3. Add a module-level `_canonical_ledger_signal() -> int` above `_warm_caches`: resolve `RuntimePaths.resolve(repo_root=ROOT, require_external_runtime=True).ledgers_db` on each call, deliberately NOT through `_runtime_paths()`, because that helper caches the first resolution process-wide and would pin a test's temporary root for the rest of the process (say so in the comment); open `sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)` (the pattern in ledger_coverage.py:63), run `SELECT MAX(rowid) FROM ledger_events`, close the connection in a finally, return `int(value or 0)`. Catch every Exception and return -1 without calling `_log` (a missing ledger is a normal env-less state, and the existing warm test asserts no log messages). Why-comment: the canonical ledger is a ~1.5 GB WAL database whose file mtime changes even on reads, so its stat is useless as a change signal; ledger_events is append-only with one row per mutation (the same signal DashboardCache keys on), MAX(rowid) is an index seek, and mode=ro never creates the file or checkpoints the WAL.

4. In the nested `_fingerprint()`, append one tuple `("ledgers.db:ledger_events", _canonical_ledger_signal(), 0)` to `stats` before returning, keeping the existing file stats.

5. Change `max_age = 300.0` to `max_age = 1800.0` and rewrite its comment: the fingerprint now covers the canonical ledger, so the age rebuild is only a safety net for inputs it still does not see. Leave the `_TTL_OVERRIDE[key] = max_age + 60` loop unchanged (now 1860 s).

6. In the nested `_warm`, wrap only the `_cache_rebuild(key, builder)` call in `with _WARM_BUILD_LOCK:`. The `_fingerprint()` poll, the bookkeeping, and `_cache_snapshot_save` (already serialised by its own `_SNAPSHOT_LOCK`) stay outside. Never take this lock inside `dashboard/common.py::_cache_rebuild`: the order must stay warm lock, then per-key build lock, and the SWR request path must not block on the warmer.

Then in src/model_prediction/dashboard/evidence.py, in `production_evidence`, change only the pool that maps `_read_evidence_ledger` over `ledger_paths` from `min(len(ledger_paths), 8)` to `min(len(ledger_paths), 2)`, with a why-comment: openpyxl parsing is pure Python and GIL-bound, so 8 threads bought little speed while multiplying the transient peak (989 MB traced in a 43 s status() build); 2 keeps some I/O overlap. Leave the `_build_model` pool untouched.

Finally, run the live read-only smoke in the verify block. It deliberately runs WITHOUT the `env -u` prefix so it resolves the operator's real runtime root and opens the real ~1.5 GB WAL ledger with mode=ro. Because the signal fails silently by design, this smoke is the only proof it works in production, and it is the whole justification for raising max_age from 300 to 1800 s. It must print a value above 0 (planner probe 2026-11-22: MAX(rowid) 106834, 8.5 ms, ledgers.db 1.53 GB). Record the value in the SUMMARY. If it prints 0 or -1, STOP and report; do not commit Task 3.
  </action>
  <verify>
    <automated>.venv/Scripts/python.exe -m ruff check dashboard_server.py src/model_prediction/dashboard/evidence.py tests/test_dashboard_server.py && env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY .venv/Scripts/python.exe -m pytest tests/test_dashboard_server.py -k "warm or evidence or fingerprint" -o addopts='' -q && env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY .venv/Scripts/python.exe -m pytest tests/test_two_stage_and_stealth.py tests/test_run_supervisor.py tests/test_dashboard_cache_concurrency.py tests/test_dashboard_cache_integrity.py -o addopts='' -q && .venv/Scripts/python.exe -c "import dashboard_server as d; v = d._canonical_ledger_signal(); print('live ledger signal', v); assert v > 0, 'signal did not read the live canonical ledger'"</automated>
  </verify>
  <done>The live smoke prints a ledger signal above 0, and its value is recorded for the SUMMARY; ruff clean on all changed files; the `-k "warm or evidence or fingerprint"` selection passes with 6 tests (3 at baseline + 3 new), including the untouched test_cache_warmer_builds_every_entry_without_failures; the four Task 1/2 test files still pass; the three new tests were seen failing at HEAD. The dashboard was NOT restarted.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| dashboard process -> canonical ledger DB | The warmer now opens the production ledgers.db (the paper-trading evidence store) every 30 s per warmer thread |
| supervisor -> worker env | Env vars injected into every scheduled worker |
| dashboard -> Windows memory manager | Process-wide memory priority change |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-mjo-01 | Tampering | `_canonical_ledger_signal` | high | mitigate | `file:...?mode=ro` URI: the connection cannot write, create the file, or checkpoint the WAL; the fails-soft test asserts no ledgers.db is created |
| T-mjo-02 | Denial of service | `_canonical_ledger_signal` vs daily writer | medium | mitigate | single `MAX(rowid)` index seek, 1 s busy timeout, connection closed in finally, any error returns -1 so the warmer keeps polling and never blocks the writer |
| T-mjo-03 | Denial of service | `_WARM_BUILD_LOCK` | medium | mitigate | taken only in `_warm` around `_cache_rebuild` (warm lock, then per-key lock); never inside common.py, so the SWR request path and builders cannot deadlock on it |
| T-mjo-04 | Denial of service | OpenBLAS cap in worker env | low | accept | `setdefault` keeps operator overrides; OMP threads stay uncapped so xgboost/sklearn training keeps its parallelism inside the 60-min daily timeout |
| T-mjo-05 | Denial of service | low memory priority | low | accept | only page-reuse ordering changes; best-effort with debug logging and an unchanged return value; CPU/I-O priority logic is untouched |
| T-mjo-SC | Tampering | npm/pip/cargo installs | high | mitigate | No package installs in this plan; nothing to gate |
</threat_model>

<verification>
- `.venv/Scripts/python.exe -m ruff check dashboard_server.py src/model_prediction/run_supervisor.py src/model_prediction/stealth_runtime.py src/model_prediction/dashboard_cache.py src/model_prediction/dashboard/evidence.py tests/test_run_supervisor.py tests/test_two_stage_and_stealth.py tests/test_dashboard_cache_integrity.py tests/test_dashboard_server.py` is clean.
- `env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY .venv/Scripts/python.exe -m pytest tests/test_two_stage_and_stealth.py tests/test_run_supervisor.py tests/test_dashboard_cache_concurrency.py tests/test_dashboard_cache_integrity.py -o addopts='' -q` passes (23 baseline + 5 new).
- `env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY .venv/Scripts/python.exe -m pytest tests/test_dashboard_server.py -k "warm or evidence or fingerprint" -o addopts='' -q` passes (3 baseline + 3 new).
- Live read-only smoke (no `env -u` prefix): `.venv/Scripts/python.exe -c "import dashboard_server as d; v = d._canonical_ledger_signal(); print('live ledger signal', v); assert v > 0, 'signal did not read the live canonical ledger'"` prints a value above 0.
- Never the full suite. The dashboard is not restarted by the executor.
- `git diff --stat` for the commit lists only the nine files in files_modified.
</verification>

<success_criteria>
- Dashboard and every supervised worker start numpy/scipy with one OpenBLAS thread unless the operator set a value; OMP threads are untouched.
- Windows background processes calling enable_background_priority run at MEMORY_PRIORITY_LOW, best-effort.
- A no-op SQLite-authority cache refresh does zero pick_rows materialisation.
- At most one warm build runs at a time; ledger growth triggers a rebuild through the fingerprint; age rebuilds every 1800 s (TTL override 1860 s).
- production_evidence reads xlsx ledgers with at most 2 threads.
- Every new regression test was observed failing before its fix.
</success_criteria>

<output>
Create `.planning/quick/261122-mjo-reduce-background-footprint-openblas-thr/261122-mjo-SUMMARY.md` when done. Record in it: the test counts per command, the red-then-green evidence for each new test, the commit hash(es), that auto_buyer_worker also inherits low memory priority, and this known residual for the orchestrator: the SWR request path (`_refresh_in_background`) still calls `_cache_rebuild` outside `_WARM_BUILD_LOCK`, so if serialised warm builds run more than 60 s past max_age a request can trigger one overlapping rebuild (TTL stays max_age + 60 by spec). Second residual: inputs the fingerprint still does not cover (for example the outputs/ validation JSON, production artifacts, and events JSON that status() and production_evidence read) can now lag up to about 31 minutes (1800 s plus one 30 s poll) instead of about 6.
</output>
