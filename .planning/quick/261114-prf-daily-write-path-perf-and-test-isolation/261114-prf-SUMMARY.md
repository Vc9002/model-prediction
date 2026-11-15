---
quick_id: 261114-prf
status: complete
date: 2026-11-14
commit: 41a27c73
---

# Summary: the daily write path no longer re-reads the ledger or re-runs simulations

## What changed

- `dd6fa737` `cfb_replay.py`: `_MemoizedReplayEngine` caches simulated score arrays on engine
  parameters, RNG state and score means (16 entries). `models/cfb_distribution.py` untouched.
- `dd6fa737` `model_ledger.py`: `_ROWS_CACHE`, a stat-validated read cache; writes drop the entry.
- `dd6fa737` `runtime_ledger_store.py`: `records` / `pick_rows` accept `event_id`.
- `dd6fa737` `tests/conftest.py`: drops `MODEL_PREDICTION_RUNTIME_ROOT` at import;
  `test_auto_buyer_cycle_tracking` patches `AUTO_BUYER_CYCLE_HISTORY_PATH`; three stale pins fixed.
- `41a27c73` `ledger.py`: `_append_record` reads only the event's rows while the XLSX export is
  deferred; `_read_unlocked(event_id=)`; `_sqlite_xlsx_export_deferred()`.

## Verification

- Targeted files, `-o addopts=''`: `test_ledger`, `test_model_ledger`, `test_cfb_replay`,
  `test_runtime_root_test_isolation`, `test_runtime_ledger_store`, `test_auto_polymarket_buyer`:
  160 passed. Related ledger/buyer/NCAAF files: 180 passed plus the 2 stale pins fixed here.
- Each new test fails with its fix reverted.
- Real data: 2,651 events / 7,147 rows on a backup copy of the live ledger, 0 differences.
- `ruff check src/ tests/` clean. The full suite was not run (project rule).

## Timing

NCAAF forecast seconds, from `timing.forecast_sport_done_seconds`:

| Run (ET) | cfb s | ncaab s | Before the fixes |
|---|---|---|---|
| 14:00 | 1,567 | 702 | yes |
| 16:00 | 472 | 195 | yes (started before the cherry-pick landed) |
| 18:30 | 206 | not compared | no, first run on the fixed tree |

The drop from 14:00 to 16:00 came from changes other than these fixes (the BBO pacing commit
and fewer contracts), so the 18:30 run is the only one that tests these fixes. Contract counts were
not normalised; compare per contract logged before crediting the fix.

## Not verified

- A per-contract comparison. Contract counts per run are not yet extracted.

## Left open

`docs/ROADMAP.md` Horizon 3 item 6: dead `sports/*/adapter.py` layer, mypy not enforced, 52
polluted rows in the live cycle history, ledger size, per-model workbook rewrite, daily log
location, tracked calibration artifacts, runtime root clutter, stale auto-buyer lock wait.
