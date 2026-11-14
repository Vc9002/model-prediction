---
quick_id: 261114-prf
slug: daily-write-path-perf-and-test-isolation
date: 2026-11-14
status: complete
---

# Daily write-path performance and test isolation

Found in the 2026-11-14 project audit. The 14:00 daily run spent 1,589 s of 1,886 s in the
forecast step: NCAAF 1,567 s, NCAAB 702 s. A stack sample of the NCAAF slate build and timed
reads of the live ledger (read-only) put the cost in three places, none of them pricing:

- `PickLedger._append_record` reads the whole tier/sport slice for one duplicate check:
  flat/ncaaf 4,216 rows 1.9 s, flat/ncaab 8,442 rows 2.5 s, per append, growing daily. The
  indexed lookup for the one event returns in under 1 ms.
- `record_from_pick_request` reads the model's whole workbook twice and rewrites it once per
  append (0.4 s per read at 1,000 rows).
- `cfb_replay.replay_inputs` re-runs a 200,000-draw simulation it has already run: 93% of the
  128 s slate build, and again at snapshot build, snapshot validation and every ledger write.

Separately, `tests/test_auto_polymarket_buyer.py::test_auto_buyer_cycle_tracking` has written
52 fake `LIVE_EXECUTION` rows (forecast date 2026-08-30) into the live runtime root's
`logs/auto_buyer_cycle_history.jsonl`: three modules resolve `RuntimePaths` at import, before
the autouse fixture clears `MODEL_PREDICTION_RUNTIME_ROOT`.

## Tasks

1. `RuntimeLedgerStore.records/pick_rows` take an optional `event_id`; `_append_record` uses it
   for the duplicate check while the XLSX export is deferred, and falls back to the full read
   otherwise. Test: duplicate still raised, other-event rows not read.
2. `ModelLedger._read_unlocked` serves a copy from a cache keyed on the file's stat; a write
   invalidates it. Test: external rewrite is seen; callers cannot mutate the cache.
3. `cfb_replay` memoizes simulated scores on (engine parameters, RNG state, score means).
   `models/cfb_distribution.py` is not edited: its hash is stamped into every stored snapshot.
   Test: memoized replay equals a cold replay bit for bit.
4. `tests/conftest.py` drops the env var at import; the cycle-tracking test patches the history
   path. Test: the import-time constants do not point at the env root.
5. Targeted test files only, ruff, then apply to the shared checkout between scheduled runs.
