---
phase: quick-261112-mpz
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/runtime_paths.py
  - src/model_prediction/snapshot_archives.py
  - src/model_prediction/system_health.py
  - src/model_prediction/cli/commands.py
  - src/model_prediction/cli/forecast.py
  - src/model_prediction/cli/daily.py
  - src/model_prediction/dashboard/routes.py
  - scripts/run_daily.ps1
  - tests/test_system_health.py
  - tests/test_cli.py
  - tests/test_runtime_paths.py
  - tests/test_nfl_joint_market_v3.py
  - tests/test_nfl_joint_market_v3_scoring.py
  - tests/test_nfl_joint_packet_capture.py
autonomous: true
requirements: [QUICK-261112-mpz]

must_haves:
  truths:
    - "system_health's market-capture check reads the same odds root the daily capture writes (runtime root), so a healthy capture is never reported stale."
    - "Scheduled esports/KBO/NPB retraining writes rating artifacts under the runtime root models/ and never rewrites the git-tracked config/models/ frozen artifacts; reads stay rolling-first with frozen fallback."
    - "daily.py's Polymarket edge scanner and odds summary, and the dashboard live capture, use the rerouted runtime odds root, not the checkout's data/odds."
    - "Parallel ingestion jobs run with single-threaded BLAS so seven concurrent interpreters stop exhausting memory at 00:30 ET."
---

<objective>
Close the write/read split-brain between the checkout's `data/odds` and the runtime root `odds/` for every
remaining reader (health, daily scanner/summary, dashboard capture), and stop the daily cycle from
rewriting frozen promoted artifacts because the rolling models directory was never bootstrapped.
Found by the 2026-11-12 full pipeline verification pass.
</objective>

<tasks>
1. `rerouted_data_root()` helper in runtime_paths; snapshot_archives reuses it. Regression test.
2. system_health `_market_capture` reads `runtime_root/odds`. Regression test with runtime_root != repo/data.
3. `_rolling_models_write_dir()` for every artifact WRITE (forecast refreshers, cmd_validate_* --write-artifacts); seed runtime models/ from the current working-tree copies; `git checkout` the frozen artifacts. Regression test.
4. daily.py step10 scanner base_dir + odds_by_sport summary; dashboard routes live capture target -> rerouted root.
5. run_daily.ps1 ingestion jobs: OPENBLAS_NUM_THREADS=1, OMP_NUM_THREADS=1.
6. ruff format the three NFL joint-market test files.
</tasks>
