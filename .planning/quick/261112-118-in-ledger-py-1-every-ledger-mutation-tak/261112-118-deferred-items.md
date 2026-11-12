# Deferred Items — quick task 261112-118

Phase 3 item 2 of the ledger-improvement plan (`C:\Users\Vince\.claude\plans\golden-whistling-haven.md`)
was investigated from live code at planning time and NOT implemented in this
quick task. Recorded here per that plan's own caution: a change that alters an
on-disk export contract two other files already assume is exhaustive needs
explicit scope-down to documentation, not a forced implementation.

## `rebuild_xlsx_projection`'s full-workbook-rebuild cost (ledger.py)

- **What was investigated:** Whether `rebuild_xlsx_projection` (and the
  underlying `write_xlsx_rows_atomic` export path in
  `src/model_prediction/xlsx_ledger.py`) can be made cheaper by splitting the
  export into a live export of open + recently-settled rows and a
  rarely-rebuilt archive export of rows settled beyond some N-day cutoff —
  mirroring the physical-split shape `archive_settled_rows` already uses for
  the canonical ledger itself.
- **Concrete finding:** `write_xlsx_rows_atomic` already carries the
  2026-09-08 column-style-caching optimization (4.36x speedup on a
  1000-row/120-column workbook, per `docs/PROJECT_OPTIMIZATION_2026-09-08.md`).
  There is no further free win inside the current "one workbook holds every
  canonical row" export contract — the write itself (temp file + rename) must
  serialize the complete row set every time, by construction. The only
  remaining lever is splitting the export itself.
- **Why not done now:** Splitting the export changes an on-disk contract that
  two things currently assume is exhaustive:
  - `tests/test_daily_cycle_smoke.py` — its `rebuild_xlsx_projection()` +
    `export_rows()` call asserts full parity against every canonical row via
    `compare(export_rows, canonical_records)["clean"] is True`.
  - `src/model_prediction/ledger_parity.py` — its production parity checker
    also diffs `export_rows()` 1:1 against canonical records for every tier
    (main, flat, research, gated).

  Both would need to merge a live export with an archive export before
  comparing if the split happened, and someone still has to pick the N-day
  cutoff, the archive file's path/naming, and its own rebuild cadence — real
  design decisions, not implementation details.
- **Recommended next action:** Decide the split policy (N-day cutoff, archive
  location, rebuild trigger), update `ledger_parity.py` and
  `tests/test_daily_cycle_smoke.py` in the SAME change that introduces the
  split (not after), then implement `rebuild_xlsx_projection`'s split.
