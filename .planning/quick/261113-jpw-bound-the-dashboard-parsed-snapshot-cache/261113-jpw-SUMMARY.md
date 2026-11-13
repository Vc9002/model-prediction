---
status: complete
quick_id: 261113-jpw
---

# System-usage pass: bound the dashboard's parsed-snapshot cache

Executed inline by the orchestrating session. Commit `4fb785cd` (it also carried another session's
already-staged nba-elo-trend-lr-v5 whitelist hunks in `auto_executor.py`,
`tests/test_auto_polymarket_buyer.py`, `docs/NBA_LR_V5_REFIT_2026-11-12.md`; that session was told).

## Measured

- Host: 32 GB RAM, 20 GB pagefile, commit charge 27 of 52 GB at rest. Windows event 2004 (low
  virtual memory) fired 15 times since 11-09; top consumers were games (Overwatch 15 GB, HOI4 15 GB),
  the dashboard python (3.4-3.6 GB), the daily forecast python (3.2 GB), rust-analyzer (2.9 GB).
- Dashboard: `_SNAPSHOT_FILE_CACHE` held every parsed polymarket_snapshots.jsonl forever: 774 files,
  543 MB on disk, measured x4.4 expansion (43 MB file -> 192 MB resident) => ~2.5 GB ceiling.
  `_JSON_FILE_CACHE` is also unbounded but holds small files. Picks/flat picks already come from the
  SQLite dashboard cache (93 MB), not XLSX. The page polls `/api/picks` (22 MB) every 300 s by default.
- Ledger `ledgers.db` 1.12 GB: `ledger_events` audit payloads 660 MB (hash-chained, evidence),
  `ledger_records` 340 MB; 24.6k of 48.6k records are `superseded` (8 daily runs/day re-forecast
  churn), growth 16-49 MB/day.
- Disk: runtime `backups/` 20.6 GB (14 nightly copies of the 1.1 GB ledger, mirrored into OneDrive:
  another 20.6 GB and OneDrive.exe at 1.2 GB RAM); `market_quotes.db` 685 MB; repo `data/raw` 2.9 GB,
  `data/availability` 2.1 GB, `data/backups` 1.1 GB.
- Daily run: ~12 min, bound by network capture (nfl_week_ahead_quotes 145 s, polymarket_slate 33 s).

## Changed

- `dashboard/backtests.py`: snapshot cache is an LRU bounded at 96 MB on-disk bytes (~400 MB parsed).
  Test `tests/test_dashboard_snapshot_cache.py` proves eviction order and fails on eviction-disabled code.

## Recommended, not done (operator decisions)

1. Restart the dashboard to pick up the bounded cache (the live process was started by hand at 13:37
   with the system Python, not the service's venv).
2. Backups: `scripts/backup_runtime_databases.py --keep 14` keeps 15 GB of ledger copies locally and
   again in OneDrive; `--keep 5` for the ledger (or compress) saves ~20 GB across both.
3. Move or drop the 00:30 ET daily trigger: it has failed three nights running on host memory
   pressure while games run; the 06:00 run self-heals.
4. The 8x/day forecast cadence makes 75-80% of ledger rows `superseded`; 4x/day halves audit growth.
