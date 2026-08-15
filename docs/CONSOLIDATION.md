# Infrastructure Consolidation — status, targets, and operator actions

Executed 2026-08-14 from the operator's 20-item consolidation plan.
This document is the single status page for the whole program; per-phase
bug detail lives in `DEBUG.md`.

## What is done (committed + pushed to `cleanup/final-debug-2026-08-14`)

**A. Control plane**
- **A-1 Production registry** — `config/production.yaml` schema v3
  (`models:` with implementation types `json_artifact` /
  `code_backed_model` / `rating_engine`, `champions:` map);
  `production_registry.py` validates every enabled entry at load,
  fail-closed per model. Test pins the checked-in config resolving all
  13 models.
- **A-2 Run supervisor** — `run_supervisor.py`: run_id, per-worker
  fcntl lease, heartbeat, captured logs, outcome rows + counters in
  `runs.db` (SQLite WAL). Workers: daily / production / rebuild-shadow.
- **A-3 Truthful health + promotion** — `system_health.py` derives
  HEALTHY/DEGRADED/DOWN from registry contracts, run rows, prediction
  records, source capture (7-28 day activity window; offseason is
  informational). `model_promotion.py`: atomic promote/rollback with
  hash freeze, rollback pointer, promotion records.

**B. Data plane**
- **RuntimePaths everywhere** — all canary/control-plane mutable state
  resolves through `runtime_paths.py` (runtime root when
  `MODEL_PREDICTION_RUNTIME_ROOT` is set, repo `data/` otherwise);
  `migrate_legacy_state()` carries legacy files over exactly once.
- **ProductionPredictionStore** — narrow API over
  `production/production.db`: predictions with identity key
  `(event_id, model_id, market_type, horizon, decision_time_utc)`,
  decisions, market snapshots, keyset pagination, SQL aggregation,
  xlsx via explicit `cli_production export`.
- **NOT cut over yet (deliberate boundary)**: the incumbent shadow
  ledgers (main/flat/research xlsx). They are written by the live 3h
  daily pipeline and verified by the audit chain — swapping them to
  SQLite needs the operator's explicit go and a parity-checked
  cutover, not a silent mid-session writer swap. `research.db` schema
  path exists in RuntimePaths; the store pattern is proven by
  production.db.

**B/C. Research**
- **Ingest provenance** — every normalized row carries `raw_source`,
  `raw_hash` (content hash of the exact raw payload), `parser_version`.
- **Feature freezer** — `feature_freezer.py` produces a frozen PIT
  feature table (all candidate features + availability flags via
  `validation.build_walk_forward_rows`) with a manifest (dataset hash,
  feature-schema hash, git sha).
- **Experiment registry** — `experiment_registry.py`: every challenger
  run records experiment_id, git_sha, model/incumbent ids, dataset +
  feature-schema hashes, fold definition, hyperparameters, calibrator,
  OOF metrics, artifact hashes, verdict; invalidated results become
  `status=void` with a reason, never deleted.

**C. Presentation / execution**
- **Read-only data service** — `dashboard/data_service.py` mounted at
  `/api/data/*` (predictions with server-side pagination + cursor,
  counts via SQL GROUP BY, runs, promotions, health, cheap
  `/api/data/versions` fingerprint). All connections `mode=ro` — the
  dashboard cannot mutate a database.
- **Canonical event identity** — `event_identity.py`: ESPN ids are
  canonical; provider ids map onto them through a registry table
  (`map_same_event` for real same-event matches).
- **Execution ticket boundary** — `execution_ticket.py`: HMAC-signed,
  5-minute-ticket contract with the signing secret under the runtime
  root (mode 0600, never in git). A CI-contract test scans the rebuild
  package: no import path to execution machinery or production
  persistence.
- **Observability** — workers publish structured counters
  (`RUN_SUPERVISOR_METRICS_PATH` sidecar: events_seen, predictions,
  no_bet) which the supervisor stores on the run row; cli_production
  publishes them when running under the supervisor.

## Performance targets (item 19)

- Common dashboard DB reads < 50-100 ms (indexed, paginated, no Excel
  in hot requests).
- No Excel parsing in any `/api/data/*` path.
- A prediction is written transactionally once; re-running a job is
  idempotent (identity key), never a duplicate row.
- Dashboard startup makes no external API calls.
- Normal scheduled operation must not modify the git working tree
  (Phase B cut the canary/control-plane writes out of the checkout;
  the incumbent shadow ledgers still write repo `data/` until the
  approved cutover).

## verification.json policy (item 17)

CI already generates `outputs/rebuild/verification.json` in the Python
3.14 job and uploads it as a workflow artifact; a 404 for that path on
GitHub does not mean verification is broken. Policy: bind each
verification artifact to its exact git SHA and retain it as CI
evidence/release metadata; do not commit generated verification output
to the tree.

## Operator actions — updated 2026-08-14 afternoon

1. ~~Rewire launchd to the supervisor~~ **DONE** — all three plists call
   `run_supervisor run <worker>` (backups as `*.plist.pre-supervisor`),
   linted, bootstrapped, attended runs verified (see DEBUG.md).
2. ~~Load production + rebuild-shadow agents~~ **DONE** — loaded and
   producing (predictions + counters, shadow.db advancing). The
   08-11→08-14 gap is explicit, not backfilled.
3. ~~Shadow-ledger SQLite cutover~~ **EXECUTED (J)** — dual-write
   proven over multiple attended live cycles (22/22 tier-sport parity,
   both integrity chains GREEN), then
   `MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite` flipped in all three
   launchd plists and the agents reloaded: the runtime store commits
   FIRST (canonical; failure aborts the mutation) and the XLSX becomes
   a best-effort EXPORT written to its existing repo paths. The legacy
   audit chain + reader remain live for the overlap cycle. Remaining:
   K relocates the export files under the runtime root and cleans the
   working tree (classification in
   docs/WORKING_TREE_CLASSIFICATION.md).
4. ~~Orphaned modules~~ — deletion authorized; executing.
5. **Soccer Odds API key** — no replacement key available; soccer stays
   explicitly DEGRADED as a provider signal (not blocking).
