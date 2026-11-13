---
phase: quick-261113-jpw
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/dashboard/backtests.py
  - tests/test_dashboard_snapshot_cache.py
autonomous: true
requirements: [QUICK-261113-jpw]

must_haves:
  truths:
    - "The dashboard's parsed polymarket snapshot cache is bounded by on-disk bytes with LRU eviction, so the process cannot pin the whole odds tree (543 MB on disk, ~2.5 GB parsed) in memory."
    - "A cache miss re-parses the file; results are identical to the unbounded cache."
---

<objective>
Operator directive "optimize project system usage". Measurement: the dashboard process reached
3.4-3.6 GB and was the top consumer in Windows low-virtual-memory events on 2026-11-12; its
`_SNAPSHOT_FILE_CACHE` kept every polymarket_snapshots.jsonl it ever parsed (774 files, 543 MB on
disk, x4.4 expansion measured on the largest file). Bound it.
</objective>
