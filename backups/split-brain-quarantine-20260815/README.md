# Split-brain quarantine — 2026-08-15

`ledgers.db` was a repo-local stray created by an env-less invocation
falling back to `repo/data/` instead of the canonical external runtime
root (`/Users/vincentc9002/model-prediction-runtime/ledgers/ledgers.db`).

Verified before quarantine (2026-08-15, ~03:30 GMT+8):
- ledger_records: 0 rows
- ledger_events:   0 rows
- ledger_runs:     0 rows
→ empty artifact, nothing to merge into canonical state. Removed per the
P0-1b quarantine decision; the canonical runtime ledger is untouched.

Root cause fix: operational entry points (supervisor, canary, promotion,
system_health, dashboard, cli_production) now FAIL CLOSED when
MODEL_PREDICTION_RUNTIME_ROOT is unset instead of silently creating a
second runtime under the repository.

Later the same day: repo-local runs.db, dashboard_cache.db and
data/rebuild/{shadow,metadata}.db were verified stale (Aug 9-14 mtimes,
runtime-root counterparts live and canonical) and quarantined too. The
daily worker's lock, the supervisor leases, the cache DB and frozen
champion snapshots all resolve to the runtime root now.
