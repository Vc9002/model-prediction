---
status: complete
quick_id: 261112-mpz
---

# Full pipeline verification pass: odds-root split-brain and frozen-artifact rewrite

Executed inline by the orchestrating session (another session was committing to the same tree;
only this task's hunks were staged, `run_daily.ps1` via a HEAD+hunk blob). Code commit `c96f28cf`,
verified in a detached worktree at that SHA (ruff clean, 58 targeted tests green).

## What was verified

- `ruff check src/ tests/ scripts/` clean; `ruff format --check` had three unformatted NFL joint-market
  test files (formatted here).
- Every scheduled worker (daily, calibrate, production, rebuild-shadow, auto-buyer, backup) green on
  its latest run in `runs.db`; the 16:00 ET daily run finished with every step at exit 0 during the pass.
- Dashboard: 36 read-only `/api/*` routes probed, all 200 (POST-only routes 404 on GET as designed);
  `/api/bets` takes ~100 s (authenticated exchange calls) and `/api/export/picks` ~19 s -- slow, not broken,
  and the server is threaded.
- Full `pytest tests/` (operator-requested): 2927 passed, 57 failed. Re-run of the 57 with the
  `MODEL_PREDICTION_*` env cleared: 54 still fail. At committed HEAD in a clean worktree: 39 fail; at the
  handoff's pre-refactor checkpoint `0af19ecd`: 36 fail. 18 fail only in the dirty tree (uncommitted
  `config/model.yaml` scanner flip, local runtime state); 3 regressed between `0af19ecd` and HEAD
  (`test_auto_polymarket_buyer` x2, `test_qualification_integrity[wnba-moneyline-v5]`). Most of the
  rest pin superseded model versions (v5 vs v7, eurolg v1 vs v2, catalog/CI counts) -- test drift,
  not pipeline faults. List in `docs/DEBUG.md` entry.

## Defects fixed

1. `system_health._market_capture` read `repo/data/odds`; capture writes `runtime_root/odds`
   since 2026-11-04 -> every sport flagged stale for 8 days. Now reads the runtime root.
2. `_research_models_dir()` was the WRITE target for esports/KBO/NPB retraining; the rolling dir was
   never created so the frozen fallback was permanent and each daily run rewrote 14 git-tracked
   `config/models` artifacts. New `_rolling_models_write_dir()` bootstraps `runtime_root/models`;
   seeded from the 16:08 retrained copies; frozen files restored from git.
3. `daily.py` step10 edge scanner / odds summary and the dashboard live capture used the repo
   `data/odds`; rerouted via `runtime_paths.rerouted_data_root()` (`snapshot_archives` reuses it).
4. `run_daily.ps1` ingestion jobs: `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1` (00:30 ET MemoryError /
   OpenBLAS allocation failures on 11-11 and 11-12).

## Not done / owed

- 54 failing tests triaged, not fixed (see DEBUG.md). Decide per the handoff: fix drift or hold merge.
- `data/calibration/**` is git-tracked yet rewritten by the calibrate step every run (97 files dirty).
- Health still flags off-season sports (kbo/mlb/npb/wnba) as stale: the 2-day rule has no season model.
- Auto-buyer `0xC0000005` native crashes (2 on 11-12) and the production canary's WNBA-only primary
  (no predictions since 10-31, off-season) are pre-existing and unchanged.
