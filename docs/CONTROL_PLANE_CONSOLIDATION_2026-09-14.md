# Control-plane consolidation: verified findings and implementation queue

Local checkout: `8b713cd0944f94fe1dc1100e48b439c920122a88` (September 9),
plus the uncommitted rebuild and other local work. Audit snapshot:
`outputs/research/control_plane_verification_20260914/audit.json`, captured
September 14 at 21:42 EDT. This document does not claim to verify remote HEAD,
GitHub Actions, branch protection, or current account exposure.

## Findings that change the September 4 assessment

| Finding | Verified local state | Consequence |
|---|---|---|
| GitHub snapshot versus local work | Local HEAD is five commits beyond the supplied September 4 snapshot, with substantial uncommitted work | Use the exact code snapshot for validation claims |
| Five reported test failures | All five pass locally; the v4 test previously depended on ignored files | Local passing tests did not establish fresh-checkout reproducibility |
| Mutable order/state files in Git | Removed from tracking by `8ba3576` on September 8; local files remain | No new deletion/migration is needed to remove these files from Git |
| Auto-Buyer mode | The state file actually read by `auto_executor.py` is `data/auto_buyer_state.json`: enabled, **paper** | Enabled does not mean real-money execution |
| Global capital authority | `automated_orders: false` / `manual_orders_only: true` are loaded by the production registry but not consumed by Auto-Buyer | Still requires a common authorization boundary |
| Static allowlist drift | Saved whitelist includes unregistered `soccer-poisson-dc-v2` and `soccer-total-v1` | Whitelisting is not model qualification |
| Configured challengers | All 21 sport/market pointers lack registered challenger entries | Lifecycle integrity now explicitly fails; serving remains available |
| Implementation status | Registry reports 16 implemented and 5 planned lanes after removing unsupported status claims | Import success alone does not prove mechanics validation |
| Champion evidence | Config declares 13 historical-only, 5 predictively-qualified, 3 degraded champions | These are declarations, not a fresh independent qualification audit |
| MLB v10 | Operator serving promotion is explicitly `predictive_only`; Phase F YAML still declares confirmation/ACTIVE | Serving basis, scientific result, and capture freshness are separate facts |
| Phase F supervision | No v10 collector worker in canonical `WORKERS`; no corresponding worker in runtime run history | YAML ACTIVE is not verified worker health |
| Research reset | September 9 AGENTS records the operator's reset of old frozen research restrictions | Preserve old artifacts and results; do not reinstate obsolete authorization locks |
| All-model rebuild | 21 fitted research candidates with replay, inference and increasingly complete pricing support exist | Integrate their identities/evidence; do not restart model development from zero |

The latest successful runtime starts in the snapshot were daily 01:08 UTC,
manual-bet-sync 01:18 UTC on September 15; production 21:44 UTC and rebuild
22:36 UTC on September 14. These run records prove those worker executions,
not model qualification or Phase F capture. The read used the canonical
runtime `runs.db` in SQLite read-only mode.

## Implemented in this pass

### P0-01 — Make the v4 integrity test reproducible

Files: `tests/test_mlb_v9_v4.py`, `tests/fixtures/mlb_v9_v4/`.

Packaged byte-identical copies of the existing 285 KB historical parquet and
its manifest. The test reads its versioned fixture directory rather than
ignored `outputs/research/`, verifies SHA-256 and dimensions, and recomputes
condition number, VIF, and the audit result. The original research files were
preserved. The fixture is regression evidence, not PIT or promotion evidence.

### P0-02 — Stop unbound challenger qualification claims

Files: `src/model_prediction/model_lifecycle.py`, `production_registry.py`,
`qualification_registry.py`; `tests/test_qualification_integrity.py` and
`tests/test_model_lifecycle.py`.

- Lifecycle validation now reports missing, unavailable, wrong-model,
  wrong-market, or hashless challenger entries and missing freeze/protocol
  metadata. It does not mutate champion availability.
- Freeze timestamp and protocol hash are read from the hash-verified artifact.
  A timestamp must be timezone-aware and cannot be in the future.
- Removed the two name-based prospective exceptions and file-existence freeze
  inference. Importable code reports IMPLEMENTED, not MECHANICS_VALIDATED.
- Offline report acceptance requires the exact registered model/artifact,
  explicit non-synthetic origin and a valid integer count.
- Evidence loading requires an expected artifact hash and filters both market
  and exact hash. Rows cannot supply their own authoritative freeze timestamp.
- JSON/Markdown summaries expose qualification errors, identify the evidence
  column as **champion** evidence, and preserve the operator serving basis and
  its predictive-only scope.

The initial eight adversarial tests failed before the changes. Their failures
demonstrated the bugs independently of the current configuration. Additional
tests cover freeze metadata, report identity, cross-market evidence and
operator-promotion reporting. The existing serving test now explicitly expects
the current lifecycle failure, then separately checks every serving champion.
It does not turn the unresolved registration state into a passing gate.

Rollback consists of reverting these source/test changes; no champion pointer,
old model artifact, runtime setting, or order was changed by this pass.

## Remaining queue, in commit-sized order

### P0-03 — Register actual research candidates and reconcile pointers

Files: `config/production.yaml`, `src/model_prediction/production_registry.py`,
`research_generation.py`, `research_forward.py`, `qualification_registry.py`;
new immutable binding manifests and `tests/test_qualification_integrity.py`.

Use the existing September 12 generation manifest to bind each new model ID,
sport/market, artifact, inference implementation, source hashes, training data,
calibration, freeze timestamp and evaluation protocol. Preserve the legacy
research-target names as historical queue entries. Do not assign their names
to different fitted models. Support the generation artifact format explicitly
rather than rewriting artifact bytes to imitate production JSON.

Acceptance: all 21 new candidates resolve and reproduce archived forecasts;
tampering any required dependency refuses the candidate; champion routing is
unchanged. Every active challenger pointer either resolves or is explicitly
planned. No synthetic or retrospective row becomes prospective through the
registration step. A model card links actual results and missing evidence.

### P0-04 — Enforce one capital authorization boundary

Files: `src/model_prediction/portfolio/auto_executor.py`,
`data_sources/polymarket_execute.py`, `execution_ticket.py`,
`production_registry.py`, `dashboard_server.py`, `config/production.yaml`;
`tests/test_auto_buyer_mode_boundary.py`, `tests/test_execution_gate.py`,
`tests/test_auto_polymarket_buyer.py`, and a dedicated authorization test module.

Make the production execution policy the global permission boundary; the
runtime Auto-Buyer switch requests operation within that permission. Enforce
it at the final order-submission boundary, including fallback/top-up paths.
Require exact registered artifact identity, explicit sport/market approval,
eligible evidence and existing context/quote/risk checks. Retain allowlists
only as additional restrictions. Define any operator exception as an audited,
expiring, artifact-specific authorization with an explicit reason; never infer
one from an enabled switch. Keep serving, predictive qualification, economic
qualification and execution eligibility separate.

Acceptance: a static whitelist, direct buyer construction, dashboard one-off
override, fallback or top-up cannot bypass the global denial. Paper mode stays
paper. Missing/invalid policy and unbound/degraded/historical-only/synthetic
evidence refuse submission. Revoking permission between quote and submit stops
the order. Existing position reconciliation remains available independently of
permission to add exposure. Tests use fake executors and never submit orders.

### P0-05 — Supervise prospective capture and derive freshness

Files: `src/model_prediction/run_supervisor.py`,
`scripts/mlb_v10_prospective_shadow.py`,
`scripts/run_daily.sh`, `scripts/run_production.sh`, `ops/launchd/`,
`dashboard/production.py`, `config/research/phase_f_state.yaml`;
`tests/test_run_supervisor.py` and dedicated capture-health tests.

Register capture and integrity audit as explicit workers. Reuse leases,
run IDs and failure recording. Connect scheduling to the actual T-minus window;
do not assume a three-hour trigger will capture a 30-minute horizon. Derive
capture ACTIVE/STALE/FAILED/NO_DATA from eligible opportunities, append-only
prediction times and successful audit runs. Preserve Phase F's historical
protocol results and report the operator research reset separately.

Acceptance: one worker owns a capture window, duplicate wakes are idempotent,
post-start predictions are rejected, missed eligible windows are visible,
empty off-season slates differ from failed capture, and a failing audit cannot
leave health ACTIVE. Ordinary service restart must not rewrite frozen rows.

### P0-06 — Tie release claims to the exact tested revision

Files: `.github/workflows/ci.yml`, `scripts/generate_rebuild_verification.py`,
deployment/promotion entry points, `src/model_prediction/model_promotion.py`.

Require a passing test receipt for the exact deployed source/artifact revision.
Retain failing checks as failures. Configure required GitHub checks when
publishing this work; local test output alone is not a remote CI receipt.

Acceptance: stale receipts, dirty-source mismatches and red checks block
promotion/release. A clean checkout includes all required test fixtures and
runs without ignored local research products. This gate concerns software
verification; it does not replace the separate evidence gate.

### P1-01 — Consolidate context, paired replay and evidence counting

Files: `model_lifecycle.py`, `research_pairing.py`, `research_forward.py`,
`research_incumbent_evaluation.py`, `research_scoring.py`,
`scripts/evaluate_ready_challengers.py`, `scripts/evaluate_captured_pairs.py`,
`scripts/evaluate_mlb_residual_tournament.py` and canonical runtime storage.

Reuse the implemented replay adapters and immutable source archives. Bind
exact event, participants, market, period, line, decision timestamp/horizon,
quotes, features and both artifacts. Count unique eligible events/dates;
duplicate horizons are not independent games. Validate conflicting outcomes
and score the challenger's selected side, never copy an incumbent win/loss
label blindly. Replace the legacy horizon-file/event-ID-only join and all
pseudo-incumbent comparisons. Route each public qualification metric through
this engine.

Acceptance: exact incumbent replay, same-context pairing, tamper refusal,
duplicate/conflict exclusions, chronological origin verification, and null
economics for missing executable quotes. Reports include market/champion/
candidate proper scores, calibration, coverage and date-clustered uncertainty;
CLV and cost-adjusted results have explicitly separate cohorts.

### P1-02 — Measure served-value distributions

Files: `production_canary.py`, `cli_production.py`, `ledger.py`,
`dashboard/production.py`, canonical runtime queries and new health tests.

Compute per-model/market counts, exact-0.5 fraction, unique values, probability
spread, quote age, missing/fallback features, feature variance and
model–market disagreement. Preserve NO_DATA/INSUFFICIENT_SAMPLE rather than
inventing green health. Compare observed distributions with declared training
or approved benchmark ranges, with explicit sample-size rules.

Acceptance: a finite normalized but constant-0.5 market feed is detected;
one sparse event is not treated as a robust distribution; benchmark-serving
forecasts remain visible while actionable eligibility reflects the anomaly.

### P1-03 — Generate status from the canonical registry and queue

Files: `qualification_registry.py`, `scripts/generate_multisport_status.py`,
`dashboard/rebuild_status.py`, `docs/PROJECT_STATUS.md`, `docs/ROADMAP.md`.

Generate current identities, counts, errors and next actions, link immutable
reports, and retain dated history separately. Publish implemented versus
validated versus qualified as distinct states. The rebuild is an experiment
and component incubator, not another production authority.

Acceptance: missing reports, unknown artifact IDs, absent workers and stale
evidence are visible. No static table overrides the canonical state.

### P2 — Sport-by-sport evidence and model iteration

1. **NCAAF:** compare the actual current incumbent and rebuilt joint-score
   candidates using real decision-time ML/spread/total contracts. Historical
   comparisons against a prior rebuild generation are a different baseline.
2. **MLB:** retain v10's exact existing artifact for prospective comparisons;
   collect genuine T-minus lineup/starter/bullpen/weather/market inputs for
   moneyline residual work. Retain clean NRFI's negative results.
3. **WNBA:** use the new candidate/replay/pricing support with chronological
   availability and possession features; report executable-price coverage.
4. **NFL/NBA:** capture availability and market histories before adding model
   complexity. Football's current season increases the cost of missed capture.
5. **Tennis/Soccer:** regenerate orientation-sensitive evidence and make
   provider identity, competition coverage and settlement rules explicit.
6. **Esports:** fit PIT contextual effects and test inactivity/roster shifts;
   independently validate event identity and result timing in settlements.
7. **KBO/NPB:** prioritize reliable pitcher/lineup sources and tie rules;
   source breadth comes before additional model families.

For every lane: use chronological inner-fold selection, OOF-only ensembles,
market and exact-incumbent baselines, feature-family ablations, and an untouched
prospective cohort. Repeated use of a historical holdout is development, not
independent confirmation. Predictive improvement and executable profitability
remain distinct requirements. The existing 21-model generation has not yet
established both.

## Verification

The full suite passed **2,990 tests, 3 skipped**, in 354.22 seconds. A final
synthetic-origin safeguard and regression assertion were added during that
run; the affected 65-test suite passed against the final code. Scoped mypy
passed for three registry/lifecycle modules; Ruff passed across all 102
changed/new Python files. The v4 integrity test also passed in an isolated
temporary directory containing only its test and packaged fixtures.

Both source snapshots and exact logs are recorded under
`outputs/research/control_plane_verification_20260914/validation.json`.
Its status is deliberately `SOFTWARE_CHECKS_PASS_LIFECYCLE_INTEGRITY_FAIL`:
the software correctly reports 21 unregistered challenger lanes. Remote CI
was not verified, and the changes have not been committed or pushed.

The earlier 2,964-pass run belongs to the saved snapshot under
`outputs/research/pricing_verification_20260914/`. Additional local changes to
CFB, esports and ledger code arrived after that run; its result must not be
presented as validation of those later changes. This pass preserved those
edits and did not change live settings or existing model artifact bytes.
