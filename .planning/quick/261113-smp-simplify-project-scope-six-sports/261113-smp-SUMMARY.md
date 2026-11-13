---
quick_id: 261113-smp
status: complete
date: 2026-11-13
commit: 166bb2ce
---

# Summary: simplify project scope (six sports, paper + gated real money, research off the critical path)

## What changed

- `a44188a4` **test(quick-261113-smp)**: 17 research-tooling test files moved to
  `tests/research/` (ablation, champion/challenger, experiment registry, replay research,
  rebuild dashboard panel, one-off migrations); `research` added to `norecursedirs`;
  `tests/research/README.md` documents the on-demand command. Six moved files fixed to
  `parents[2]`; the one cross-test import updated. `test_backtester.py` stayed in core because
  `test_validation.py` (train/serve parity) imports its `seed_games`. `ModelPrediction-RebuildShadow`
  Windows task disabled in place (verified `Enabled=False`) and its registration commented out
  in `scripts/register_windows_tasks.ps1`.
- `166bb2ce` **feat(quick-261113-smp)**: `daily_forecast_enabled: false` on the 13 out-of-scope
  sports in `config/model.yaml`; `cli/daily.py` gates the soccer, tennis, esports,
  international-baseball and additional-sport tasks with `is_sport_daily_enabled` (previously
  unconditional), recording `status: off_season, reason: disabled_in_config`;
  `tests/test_focus_sports_config.py` pins the scope.
- docs commit (follows): root `CLAUDE.md` 390 -> 236 lines; new "What this project is" is the
  single statement that the system is paper trading with a gated, never-scheduled real-money
  path; new "Scope" section; dated 2026-08 sections moved verbatim to `docs/DEBUG.md`
  (2026-11-13 entry) and their binding rules kept as "Durable contracts". `.claude/CLAUDE.md`
  no longer says "traded with real money / live production trading system" and defers to the
  root file.

## Evidence

- `is_sport_daily_enabled` after the change: NBA, NCAAF, NFL, NCAAB True; MLB, WNBA False
  (off-season, unchanged); all 13 out-of-scope sports False.
- Default `pytest --collect-only`: 2773 tests, 0 collection errors (first attempt errored on
  `test_validation` -> `test_backtester` import, fixed by keeping that file in core).
- `pytest tests/research --collect-only -o addopts=''`: 134 tests, collected only when asked.
- Targeted runs: `tests/test_focus_sports_config.py` + six path-edited research tests
  (41 passed); `tests/test_daily_integrity.py`, `tests/test_daily_export_timing.py`,
  `tests/test_backtester.py` (11 passed). `ruff check src/ tests/` clean.
- Only this task's hunks of `config/model.yaml` and `scripts/register_windows_tasks.ps1` were
  committed (staged by blob from HEAD); other sessions' unstaged edits to those files,
  `run_daily.ps1`, and `dash.ps1` remain in the working tree untouched.

## Left for the operator

- Ingestion of tennis scores still runs in `run_daily.ps1` (cheap, keeps history continuous);
  drop `tennis` from `$Sports` there if that is unwanted.
- `tests/test_cli.py`'s nine pre-existing failures (HANDOFF.md) are unrelated and untouched.
- Re-enabling a sport is one line in `config/model.yaml`; thawing rebuild is the two commented
  lines in `register_windows_tasks.ps1` plus `Enable-ScheduledTask`.
