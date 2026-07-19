# Project repair queue — 2026-07-20

Current evidence and metrics live in `docs/PROJECT_STATUS.md`. Do not mark this
project final while tests, lint, artifacts, config, reports, and audit state
disagree.

## P0 — integrity and qualification

- [ ] Make the active MLB artifact unqualified or regenerate a valid new version; its current `qualified: true` conflicts with sub-60% metrics and failure reasons.
- [ ] Restore `tests/test_config.py::test_configured_production_artifact_state_matches_locked_audit` as a contract test; another writer weakened it during review to accept the inconsistent MLB flag.
- [ ] Reconcile NFL config/status with its failed monthly qualification gate.
- [ ] Reconcile Soccer config/report status with the model registry, which still reports research.
- [ ] Repair all nine audit-chain breaks and document the recovery without deleting historical evidence.

## P1 — reproducible operation

- [ ] Repair editable installation so `.venv/bin/model-prediction --help` imports the package.
- [ ] Fix the three current Ruff errors.
- [ ] Reproduce validation and active artifacts from one stable checkout; verify hashes and exact report/artifact equality.
- [ ] Replace broad `pkill -f` dashboard startup and system-browser opening with process-safe startup and Dia verification.
- [ ] Fix or retire the launchd installer that uses the system Python without the project import path.

## P2 — evidence quality

- [ ] Continue prospective executable BBO and closing-snapshot capture.
- [ ] Add decision-time starters, lineups, bullpen usage, weather, and availability records with observed/effective timestamps.
- [ ] Keep MLB/NBA/WNBA/NFL spreads and totals research-only until exact historical contract lines exist.
- [ ] Keep F5, YRFI/NRFI, LoL, CS2, KBO, NPB, Tennis, and World Cup fail-closed or zero-unit until their declared gates pass.

## Verified scan record

2026-07-20 current working tree: 190 passed after another writer changed the
MLB test expectation; Ruff 3 errors; 28 JSON artifact hashes with 0 mismatches;
6,712 audit lines with 9 breaks;
installed console entry point broken; working tree five commits ahead of
`origin/main` with extensive additional changes.

## Historical rollback

Use Git history and versioned artifacts deliberately. Do not run a broad
checkout or reset in a dirty working tree.
