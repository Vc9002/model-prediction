---
quick_id: 261113-jb1
slug: harden-runtimeledgerstore-against-transi
date: 2026-11-13
status: complete
commit: de9c71c3
---

# Summary: harden RuntimeLedgerStore against transient sqlite "disk I/O error"

## Root cause

`daily-20261113T053001Z-8f176c` exited 1 because the NCAAB forecast hit
`sqlite3.OperationalError: disk I/O error` in `RuntimeLedgerStore.records()` while
`PickLedger._append_record` re-read the Flat mirror. Windows logged Resource-Exhaustion-Detector
Id 2004 ("low virtual memory condition") at 00:41:49 and 00:42:24 local, inside that run's Step 2
window; Overwatch.exe (3.6 to 4.9 GB) and the daily python.exe (3.4 GB) were the top consumers.
The store mapped 256 MB per connection across 16 forecast threads over a 1.1 GB file; a
memory-mapped page that cannot be committed fails as `SQLITE_IOERR`. Drive, Defender, backup
and maintenance were checked and ruled out (details in `docs/DEBUG.md`, 2026-11-13 entry).

## Changes (commit de9c71c3)

- `src/model_prediction/runtime_ledger_store.py`: `PRAGMA mmap_size=0`, `cache_size=-32000`,
  `_reset_connection()`, bounded reopen-and-retry in `records()` for "disk I/O error" /
  "database is locked" (`_RETRY_DELAYS = (0.5, 1.5, 3.0)`), non-transient errors re-raise at once.
- `tests/test_runtime_ledger_store.py`: four regression tests (pragmas, two transient failures
  then success, bounded give-up, no retry for `no such table`). All four fail on the pre-fix code.
- `docs/DEBUG.md`: dated entry with the trace, the 2004 events, the fix and follow-ups.

## Verification

- `pytest tests/test_runtime_ledger_store.py -o addopts='' -q`: 28 passed.
- `pytest tests/test_daily_cycle_smoke.py tests/test_consolidation_c.py -o addopts='' -q`: 25 passed.
- `ruff check` and `ruff format --check` on both files: clean.

## Follow-ups (not done)

- `PickLedger._append_record` re-reads the whole tier/sport mirror on every append.
- Host memory: about 1 GB free of 31.7 GB while games or rust-analyzer run; the 00:30 ET run
  keeps landing in that window.
