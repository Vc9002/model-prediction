---
quick_id: 261114-prf
status: complete
date: 2026-11-14
commit: d865002b
---

# Summary: the daily write path no longer re-reads the ledger or re-runs simulations

## What changed

- `ae6f8f17` `cfb_replay.py`: `_MemoizedReplayEngine` caches simulated score arrays on engine
  parameters, RNG state and score means (16 entries). `models/cfb_distribution.py` untouched.
- `ae6f8f17` `model_ledger.py`: `_ROWS_CACHE`, a stat-validated read cache; writes drop the entry.
- `ae6f8f17` `runtime_ledger_store.py`: `records` / `pick_rows` accept `event_id`.
- `ae6f8f17` `tests/conftest.py`: drops `MODEL_PREDICTION_RUNTIME_ROOT` at import;
  `test_auto_buyer_cycle_tracking` patches `AUTO_BUYER_CYCLE_HISTORY_PATH`; three stale pins fixed.
- `d865002b` `ledger.py`: `_append_record` reads only the event's rows while the XLSX export is
  deferred; `_read_unlocked(event_id=)`; `_sqlite_xlsx_export_deferred()`.

## Verification

- Targeted files, `-o addopts=''`: `test_ledger`, `test_model_ledger`, `test_cfb_replay`,
  `test_runtime_root_test_isolation`, `test_runtime_ledger_store`, `test_auto_polymarket_buyer`:
  160 passed. Related ledger/buyer/NCAAF files: 180 passed plus the 2 stale pins fixed here.
- Each new test fails with its fix reverted.
- Real data: 2,651 events / 7,147 rows on a backup copy of the live ledger, 0 differences.
- `ruff check src/ tests/` clean. The full suite was not run (project rule).

## Not verified

- The effect on a scheduled daily run's `timing.forecast_sport_done_seconds`. Compare the first
  run after this lands against 2026-11-14 14:00 (cfb 1,567 s, ncaab 702 s).

## Left open

`docs/ROADMAP.md` Horizon 3 item 6: dead `sports/*/adapter.py` layer, mypy not enforced, 52
polluted rows in the live cycle history, ledger size, per-model workbook rewrite, daily log
location, tracked calibration artifacts, runtime root clutter, stale auto-buyer lock wait.
