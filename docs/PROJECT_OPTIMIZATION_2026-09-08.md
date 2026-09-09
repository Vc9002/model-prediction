# Project-wide optimization and integration, 2026-09-08

This pass covers shared ledger exports, dashboard caching and database access,
daily orchestration timing, forecast logging integrity, research audit reporting,
test isolation, and static typing. It also preserves and integrates the existing
uncommitted Auto-Buyer, manual-bet, soccer, and college-football work requested
by the operator. Existing model artifacts and active trading settings were not
retuned by this optimization pass.

## Measured improvements

| Path | Before | After | Measurement |
| --- | ---: | ---: | --- |
| Shared XLSX export, 1,000 rows / 120 columns | 4.135s | 0.949s | Median of three unprofiled runs; 4.36x faster |
| Global ledger page ordering, 25,872 rows | 12.088ms | 0.062ms | Median of 25 SQLite queries over copied real ordering keys |
| Concurrent dashboard cache miss | Six builds | One build | Six simultaneous requests for the same key |

The workbook comparison verified identical cell values, types, number formats,
fonts, alignment, and fills, including formulas. The optimization registers
column styles once and copies their compact style arrays for each body cell;
it retains atomic replacement, fsync, formulas, tables, and formula-text escaping.

The database benchmark uses an in-memory copy containing only the ordering
keys, not the full production table. It proves the elimination of the temporary
sort, not a 194x improvement in total HTTP latency. The new index is created
idempotently by the existing RuntimeLedgerStore initialization. No production
database was mutated to run the benchmark. Machine-readable benchmark results
are in outputs/optimization/2026-09-08/.

## Correctness and observability

- Dashboard ledger pagination now uses the complete descending key
  `(created_at_utc, pick_id, ledger_tier)`. Previously a random pick-ID-only
  cursor could skip records. Unambiguous legacy cursors are resolved against
  their actual timestamp; ambiguous cursors are rejected. Page sizes stay
  within 1..500 for predictions/ledgers and 1..200 for runs.
- Dashboard cache misses coordinate by key, so separate endpoints can still
  build concurrently. Cache TTL uses the monotonic clock, and failures release
  the build lock for retry.
- Daily timing now includes deferred XLSX rebuilding and exposes
  `xlsx_export_seconds`. The observed September 8 log had 1,289s shell duration
  versus 711.4s internal duration. That gap motivated profiling; it is not proof
  that all 577.6s were exports. End-to-end production speedup remains unmeasured.
- PAPER mode cannot be overridden into real broker execution by an execution
  flag. Scheduled cycles defer to the saved mode, and the verification override
  also suppresses the pre-cycle live fallback-reconciliation branch. A 12-case
  matrix covers mode, enabled state, and execution override; the two unsafe
  paper/override combinations failed before the fix.
- Qualified WNBA spread logging uses the existing SHADOW_QUALIFIED state rather
  than the nonexistent PRODUCTION enum member. This repairs serving metadata;
  it does not promote a model.
- CFB logging validates against the actual clock. It can no longer backdate
  already-started games to manufacture pregame records, including forced runs.
- The v10 operational audit reports NO_DATA for missing predictions and
  FAIL_INTEGRITY for malformed records or orphaned settlements. The configured
  prospective ledger is absent; capture readiness is not established.
- Type checking is clean across 344 source files. Changes to calibration and
  tennis typing preserve their calculations and do not re-fit artifacts.
- CFB unit tests use isolated deterministic fixtures rather than live ESPN.
  The flat-ledger test now requires all three contracts instead of accepting
  zero logged records. Health fixtures include every registered worker.

## Validation and remaining boundaries

Regressions were verified against the old behavior for audit false positives,
pagination, cache stampedes, CFB backdating, and the WNBA enum failure. Export
equivalence was tested against the HEAD implementation before optimization.

The first full suite reported 2,633 passed, 14 failed, and 3 skipped. Two failures
were live-network-dependent CFB tests (subsequently isolated), three were stale
worker fixtures (subsequently corrected), and nine were localhost socket tests
blocked by the sandbox. The unrestricted retry was initially rejected by the
automatic approval service because its usage allowance was exhausted. After
the retry window passed, the complete HTTP test file passed (23 tests) with
localhost access. Final validation counts are recorded in DEBUG.md.

No order submission, scheduler restart, live settlement, model promotion, or
holdout evaluation was performed for these optimizations. No improved model
accuracy or profitability is claimed. Frozen prospective confirmation still
requires genuine future capture; historical reconstruction is not a substitute.
