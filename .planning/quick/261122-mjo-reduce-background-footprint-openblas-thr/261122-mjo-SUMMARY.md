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
commits: 6
plan_head_before: 0c1c4d0426a0220a83ff99def4dacd6ed591b510
plan_head_after: ba1e1ff9d5c94b1d5847a3e2af1cae3cdd056c38
actuals:
  tasks: 3
  code_commits: 5
  docs_commits: 1
  review_cycles: 2
---

# quick-261122-mjo: Reduce background footprint

OpenBLAS is capped at one thread in the dashboard and in every supervised worker. The dashboard and the auto-buyer get Windows low memory priority. A no-op SQLite cache refresh no longer materialises pick rows. Warmer builds are serialised, with a canonical-ledger fingerprint and per-key ages. The xlsx evidence pool is 2 threads.

## Commits

| Hash | What |
| ---- | ---- |
| f4528438 | `OPENBLAS_NUM_THREADS=1` (setdefault) in `dashboard_server.py` before the numpy import and in `run_supervisor._worker_env`; `stealth_runtime._set_low_memory_priority` (MemoryPriority 2), called from `enable_background_priority` |
| 4bbfb0ac | `dashboard_cache._refresh_from_canonical_store` checks each tier's freshness key before calling `store.pick_rows` |
| fee8c318 | `_WARM_BUILD_LOCK` serialises warm builds; `_canonical_ledger_signal` (read-only `MAX(rowid)` of `ledger_events`) joins the fingerprint; evidence pool `max_workers` 8 to 2 |
| e5dc895e | Review fix: per-key age (`_warm_max_age`: 300 s for picks, flat-picks, status; 1800 s for the rest), debounce escape capped at 300 s, module-level `_warm_fingerprint` with a wiring test |
| 5325a269 | Review fix: the debounce escape is timed from the first-seen change, not the last build |
| ba1e1ff9 | Docs (plan, this summary, STATE row, ROADMAP follow-ups) |

## Root causes found

1. **OpenBLAS buffer pools.** numpy and scipy each commit per-thread buffers sized to the 12 cores, about 740 MB of private bytes per process that imports both. Measured in a probe process: 903 MB with the default, 160 MB with `OPENBLAS_NUM_THREADS=1`. `OMP_NUM_THREADS` is deliberately not capped (xgboost/sklearn speed).
2. **No-op cache refresh did full work.** `DashboardCache` built every tier's rows before checking whether the ledger had changed (about 11 s CPU per call).
3. **Concurrent warm builds.** Seven builders ran at once and rebuilt every 300 s regardless of change. `status` (about 59 s CPU) and `production_evidence` (openpyxl over every xlsx ledger, about 43 s and about 1 GB transient on 8 threads) stacked their peaks, and the heap never shrank back (fragmentation). The old process logged snapshot-save `MemoryError` at 02:28 and 02:30 UTC on 2026-11-22.

## Tests

Micro-targeted, `-o addopts='' -q`, `MODEL_PREDICTION_*` unset.

- At execution: `test_two_stage_and_stealth` + `test_run_supervisor` 22 passed; cache integrity + concurrency 6 passed; `test_dashboard_server -k "warm or evidence or fingerprint"` 6 passed.
- Re-run 2026-11-22 23:4x on the final code (after quick-261122-w6z isolated `test_main`): `test_dashboard_server` + cache integrity + cache concurrency 86 passed; `test_run_supervisor` + `test_two_stage_and_stealth` + `test_production_evidence_api` 45 passed. ruff clean.
- Red-then-green proofs:
  - `test_worker_env_caps_openblas_threads` (red: `OPENBLAS=<unset>`);
  - both memory-priority tests (red: no helper; the Windows readback ran and read 2);
  - `test_sqlite_refresh_skips_pick_rows_when_event_count_unchanged` (red `8 == 4`, repeated by restoring HEAD's file);
  - `test_warm_builds_never_overlap` (red: peak concurrency 7);
  - both `_canonical_ledger_signal` tests;
  - `test_warm_fingerprint_changes_when_canonical_ledger_appends` (red with the signal line removed).

## Review cycles (critical-reviewer)

1. **CHANGES REQUIRED.** A single 1800 s age would have slowed picks/flat-picks quote freshness, buy_ready, order reconciliation and status's daily-log steps from about 5 to about 30 minutes. Fixed in e5dc895e.
2. **PASS WITH NITS.** The debounce escape was timed from the last build. Fixed in 5325a269. A remaining nit: the fingerprint test could flake if live repo `data/` files change within the same millisecond window.

## Live measurement (dashboard restarted on 5325a269, PID 5340, BelowNormal)

| | Before (PID 10132, about 27 h up) | First 12 min | After about 6 h (23:03) |
|---|---|---|---|
| Working set | 1,017-1,651 MB | 433-612 MB | 698 MB idle, 774 MB during a rebuild |
| Private bytes | 1,473-2,123 MB | 549-619 MB | 768 MB idle, 828 MB during a rebuild |
| CPU | all 7 views rebuilt at least every 300 s | about 110 s first pass, then about 1-2 s/min | about 0.3 s/min idle; about 4.4 s/min averaged over the 6 h (first pass, 300 s rebuilds of picks/flat-picks/status, and rebuilds after each daily run) |

The first-pass peak working set was 1,325 MB on this restart and 689 MB on the fee8c318 restart. Memory rose about 150 MB over the first 6 hours. Two samples do not show whether it plateaus; ROADMAP tracks it.

Scheduled workers: the three daily runs on the new code took 961, 808 and 914 s (earlier runs at similar hours: 647-1,952 s). That shows no regression. It does not show a speed-up from the cap, because the daily-run speed work in quick-261122-spd landed in the same commits.

## Residuals (tracked in docs/ROADMAP.md)

1. The SWR request path (`_refresh_in_background`) calls `_cache_rebuild` outside `_WARM_BUILD_LOCK`, so a browser request can overlap a warm build (the likely source of the 1.3 GB startup peak).
2. Inputs the fingerprint does not cover (outputs/ validation JSON, production artifacts, events JSON) can lag up to about 31 minutes for the 1800 s keys (evidence, matrix, model-ledgers, performance).
3. Pre-existing:
   - the warmer builds "flat-picks" with `read_flat_picks` while the route uses `_flat_picks_decorated`;
   - `scripts/dedup_ncaaf_settled_picks.py` runs a raw `UPDATE ledger_records` with no `ledger_events` row.
4. Found afterwards and fixed in quick-261122-w6z: the forever warmer hung interpreter exit and leaked out of `test_main_stops_cleanly_on_keyboard_interrupt` into the live `server.log` and `cache_snapshot.pkl`.
