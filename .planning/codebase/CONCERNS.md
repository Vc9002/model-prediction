---
last_mapped_commit: 3aaa58949aca8dabf49593091243d6719d859452
last_mapped_at: 2026-09-20
---
# Codebase Concerns

**Analysis Date:** 2026-09-20

## Tech Debt

**Shadow Ledger Incomplete Schema Implementation:**

- Issue: `src/model_prediction/rebuild/shadow_ledger.py` defines 16 tables, but 8 have only schema definitions with no insert/query methods implemented yet (marked with TODO comments).
- Files: `src/model_prediction/rebuild/shadow_ledger.py` (lines 151, 173, 190, 207, 223, 250, 472, 490)
- Tables affected:
  - `raw_snapshots` — raw observational snapshots with no write/read API
  - `normalized_observations` — normalized snapshots with no implementation
  - `feature_snapshots` — feature state capture with no methods
  - `dataset_manifests` — dataset tracking with no implementation
  - `model_artifacts` — artifact versioning with no methods
  - `calibration_artifacts` — calibration state with no implementation
  - `closing_prices` — price warehouse with no methods
  - `reviews` — audit reviews with no implementation
- Impact: Any attempt to use these tables will fail at runtime; the contract promises a complete append-only ledger but half of it is a schema-only stub.
- Fix approach: Implement the missing insert/read methods to match the contract in module docstring. Each table needs standardized `record_*()` and `query_*()` methods following the existing patterns for `predictions`, `market_snapshots`, `trade_decisions`, and `settlements`.

**WNBA Injury PDF Parser Has Near-Zero Test Coverage:**

- Issue: `src/model_prediction/data_sources/wnba_injuries.py` contains high-risk untested PDF token extraction logic that is the critical path for WNBA availability data.
- Files: `src/model_prediction/data_sources/wnba_injuries.py` (line 2-4 TODO comment)
- Impact: Real parsing bugs (wrong field names, schema mismatches, PDF format changes) can silently pass without test detection, degrading WNBA availability signals used in predictions.
- Fix approach: Add behavioral test coverage for real WNBA PDF reports (fixture collection from historical reports), test each parsing step (header extraction, status parsing, team mapping), and validate output structure against known-good historical reports.

**Missing Supervised Worker for Phase F Capture:**

- Issue: Research generation and candidate evaluation pipeline lacks a canonical supervised worker to automate decision capture/replay.
- Files: Related logic in `src/model_prediction/champion_challenger.py`, `src/model_prediction/rebuild/` modules
- Impact: Candidate capture/comparison workflows require manual orchestration; operator-directed research iterations cannot be fully automated.
- Fix approach: Build a dedicated worker module that coordinates candidate proposal → validation gate → capture → comparison → promotion decision flow with explicit state tracking.

**21 Challenger Registrations Missing Artifacts:**

- Issue: `config/production.yaml` lists 21 challenger candidates, but their registered artifacts are not present in the model registry.
- Files: `config/production.yaml`, `src/model_prediction/production_registry.py`
- Impact: Champion-challenger lifecycle fails to resolve configured challengers; registry reports load errors rather than candidate availability.
- Fix approach: Implement missing artifact freeze/validation for all 21 candidates (outputs in `outputs/research/generation_20260912_clean_update/`), register artifacts in `config/models/challengers/`, and add hash-validated load checks.

## Known Bugs

**KBO/NPB Stale Open Rows (Postponement Handling Gap):**

- Symptom: 34 open KBO/NPB research rows, oldest 29 days past scheduled start time, never settle.
- Files: Affected rows in `data/gated_research/kbo.xlsx`, `data/gated_research/npb.xlsx`; no settlement logic in `src/model_prediction/cli/settle.py` for KBO/NPB postponement.
- Trigger: Games that are postponed in the source data (KBO cache returns zero games on certain dates; NPB returns placeholder `*` instead of score) are logged as open but never marked resolved.
- Current behavior: Home/away cache parsing returns empty result sets; ESPN lookup fails silently; rows pend forever (44 of 130 KBO rows are permanently stale).
- Workaround: Manual ledger review + archive of permanently stale rows via `archive_settled_rows()` with supersession reasoning.

**NRFI Settlement Stays Pending When First-Inning Scores Missing:**

- Symptom: NRFI (No Run First Inning) positions remain pending settlement even after games complete if ESPN does not provide first-inning score data.
- Files: `src/model_prediction/cli/settle.py`, ESPN data source integration
- Trigger: ESPN's first-inning score endpoint returns missing/null values; settlement logic correctly refuses to settle on missing data, so positions hang indefinitely.
- Current behavior: Positions marked terminal = False; no settled_at timestamp set; periodic settlement runs check for updated ESPN data but never find it.
- Workaround: Manual settlement review; partial resolution via `void_settled_rows()` for events outside ESPN's data window.

**MLB v9 Train/Serve Parity Divergence (3 Features):**

- Symptom: Three MLB v9 features compute different values in training (`validation.py`) vs. serving (`learned_forward.py`).
- Files:
  - `src/model_prediction/validation.py` (training side)
  - `src/model_prediction/learned_forward.py` (serving side)
  - Features affected: `residual_trend_gap`, `park_factor_pit`, `bullpen_fatigue_gap`
- Impact: Model training uses different feature values than live production serving; held-out test results do not reflect actual serving behavior. Prior v9 ablation results using train-side values are now void.
- Current status: Documented in `docs/PROJECT_STATUS.md` (2026-08-13 deep-audit fix pass); parity now testable via `tests/test_validation.py::test_train_serve_parity_for_v9_features`.
- Fix approach: Ensure literal code sync between `validation.py` and `learned_forward.py` implementations; any new v9 feature must hold both sides in exact sync verified by the parity test.

## Security Considerations

**External API Dependencies at Risk:**

- Risk: Soccer forecasting pipeline depends on `api_football` (API-FOOTBALL key) as primary data source.
- Files: `src/model_prediction/data_sources/api_football.py`
- Current mitigation: ESPN-sourced soccer leagues remain unaffected; Odds API has been fully removed (no fallback path).
- Current status: API-FOOTBALL key provision pending (marked as non-blocking in PROJECT_STATUS.md); dormant Odds-API fallback still in codebase but not invoked.
- Recommendations: Implement explicit fallback for soccer if API-FOOTBALL is unavailable (queue of secondary providers); add circuit-breaker logic to prevent cascading timeouts.

**Polymarket Execution Order Exposure:**

- Risk: Dashboard order execution (BUY/SELL) touches real money; code paths must validate quote freshness and market state before order submission.
- Files: `src/model_prediction/portfolio/auto_executor.py`, `src/model_prediction/dashboard/orders.py`
- Current mitigation: Quote freshness validation (timestamps checked vs. game start); MLB moneyline categorical block (`mlb:moneyline` disabled as of 2026-09-02).
- Recommendations: Maintain explicit sport/market execution policy in production.yaml; add pre-order state verification (market open, game not started); increase logging/audit trail for all order submissions.

**Environment Secrets Storage:**

- Risk: `.env` file exists with sensitive configuration (present but not readable per policy).
- Files: `.env` (forbidden read)
- Current status: File exists; contents not verified in this analysis per security protocol.
- Recommendations: Ensure `.env` is in `.gitignore` (verified); consider rotating sensitive keys periodically (API keys, database credentials).

## Performance Bottlenecks

**Forecast Pipeline Module Size (3,302 lines):**

- Problem: `src/model_prediction/cli/forecast.py` is the largest single module in the codebase.
- Files: `src/model_prediction/cli/forecast.py`
- Cause: Consolidation of forecast entry point, all sport-specific prediction logic, market blending, and ledger writes into one module.
- Improvement path: Extract sport-specific forecast logic into separate modules (`_forecast_mlb()`, `_forecast_wnba()`, etc.) to single 200–400 line implementations; create a dispatch table indexed by sport. This reduces cognitive load and improves testability.

**Validation Pipeline Size (2,980 lines):**

- Problem: `src/model_prediction/validation.py` contains all training-time feature computation, train/validation/test splits, and ablation harness.
- Files: `src/model_prediction/validation.py`
- Cause: Monolithic feature collection + split logic + model training orchestration in one module.
- Improvement path: Extract feature computation into a separate `feature_computation.py` module; keep `validation.py` for split strategy and model evaluation only.

**Ledger Pagination Performance (Improved):**

- Status: Addressed in 2026-09-08 optimization (indexed timestamp/pick/tier; same-key cache consolidation; bounded page sizes).
- Files: `src/model_prediction/dashboard/evidence.py`, ledger pagination logic
- Current state: Performance confirmed 4.36x faster on 1,000-row benchmarks; no further known bottlenecks.

## Fragile Areas

**Point-in-Time (PIT) Correctness (Highest Risk):**

- Files: All files touching decision timestamps; critical areas: `src/model_prediction/learned_forward.py`, `src/model_prediction/features/`, `src/model_prediction/data_sources/`
- Why fragile: CLAUDE.md (section "The one invariant that matters most") documents this as "by a wide margin, the single most common source of real bugs." Examples from project history:
  - KBO/NPB timestamp-ordering bug: captured timestamp BEFORE slow data-build call; slow call's internal `utc_now()` leaked, silently zeroing every real pick for months with no error surfaced.
  - MLB transaction-date same-day ambiguity: date-only field compared to full timestamp.
  - Weather timing: wind-speed snapshot vs. game-start timestamp ordering.
- Safe modification: When adding features or decision logic, explicitly check every timestamp comparison's granularity (date-only vs. datetime). Capture `utc_now()` once at the entry point; never call it again inside slow data-fetch loops. Use `observed_at_utc <= decision_time_utc` as the canonical form; never assume "newer snapshot" = "better data."

**KBO/NPB Data Availability (Playoff/Postponement):**

- Files: `src/model_prediction/data_sources/` (KBO/NPB parsers), `src/model_prediction/international_baseball.py`, settlement logic
- Why fragile: Live cache returns empty result sets or placeholder values (`*`, empty `<span>`) when games are postponed; the logging path correctly refuses to proceed but leaves rows permanently open. No signal to mark a game as permanently unavailable/void.
- Safe modification: Add explicit postponement detection (cache returns zero rows for a date range, or placeholder markers detected); implement a separate `void_settled_rows()` call for permanently postponed games with clear reason codes.

**WNBA Availability Snapshots (Stale Data):**

- Files: `src/model_prediction/data_sources/wnba_injuries.py`, `src/model_prediction/features/player_availability.py`
- Why fragile: Snapshots are 1.5 days stale (reported in data-gap audit); live predictions may use outdated injury information. Historical gaps: 22 missing legacy defensive-trend inputs for WNBA contexts (July 28–August 3 window).
- Safe modification: Add timestamp validation in availability-using features; fail closed (NO_CALL reason) if snapshot is older than threshold. Document the PIT contract: all snapshots used must have `observed_at_utc <= decision_time_utc`.

**Historical Data Gaps (Incomplete State):**

- Files: Multiple ledger/feature modules; documented in PROJECT_STATUS.md and DEBUG.md
- Why fragile: Five incumbent cohorts have no settled decision records; fourteen markets lack stored inputs/state for replay. All 22 WNBA legacy defensive-trend inputs missing for July 28–August 3. 7,263 esports/soccer/KBO/NPB rows lack market-snapshot lineage.
- Safe modification: Before relying on historical data for training or validation, verify complete state via explicit `validate_complete_context()` check; log/skip rows with missing required columns rather than imputing or guessing values.

## Scaling Limits

**KBO/NPB Postponement Handling (44 of 130 Rows Permanently Stale):**

- Current capacity: 44 stale KBO rows (oldest 29 days); handled only via manual archive.
- Limit: Beyond ~5% stale-row ratio, manual archival becomes operationally infeasible.
- Scaling path: Implement automatic postponement detection (cache zero-rows signal, placeholder detection); add scheduled task to void/archive permanently postponed games with audit reasoning.

**Ledger Scale (3,240 Active Rows Missing Market Metadata):**

- Current capacity: Main ledger has 3,240 rows without full market context; settlement/pricing work-arounds require fallback logic.
- Limit: Market blend decisions may fail if metadata is incomplete; pricing lookups require full contract/provider/market-ID mapping.
- Scaling path: Audit remaining 3,240 rows; for each, either recover full metadata (scoreboard + market provider ID linking) or explicitly archive as unrecoverable.

## Dependencies at Risk

**pybaseball (Legacy Baseball Data Source):**

- Risk: Upstream dependency (`pybaseball>=2.2,<3`) may break if Baseball-Reference scraping infrastructure changes.
- Impact: MLB feature extraction (pitcher stats, batter stats, plate discipline) depends on this scraper.
- Alternative: MLB StatsAPI provides official JSON alternative for most signals; gradual migration to official API recommended.

**xgboost Version Pinning (Python 3.11/3.12 Split):**

- Risk: Two separate version ranges in `pyproject.toml` for xgboost (3.2–3.3 for Python <3.12; 3.4+ for >=3.12).
- Files: `pyproject.toml`
- Impact: Dependency resolution complexity; testing must cover both paths.
- Mitigation: Currently addressed via explicit version pinning; keep synchronized.

**External Sports Data APIs (ESPN, Polymarket, API-FOOTBALL):**

- Risk: ESPN, Polymarket, and API-FOOTBALL are external services with no uptime guarantee.
- Impact: Pipeline failures if any service is unavailable; settlement and live pricing blocked.
- Current mitigation: Fail-closed error handling in data-source modules; ESPN HTTP errors in settled-tennis re-verification now contained per-row (2026-09-02 fix).
- Recommendations: Maintain circuit-breaker logic for cascading failures; implement health monitoring for each external dependency.

## Missing Critical Features

**Exact Archive Linkage for Non-MLB Markets:**

- Problem: Only MLB moneyline has full archive-to-contract linkage (194 verified events with exact quotes/fees/provider-IDs). Other 13 sports lack this.
- Files: `src/model_prediction/domain.py` (MarketQuote warehouse), individual sport pricing adapters
- Impact: Candidate vs. incumbent paired economics cannot be evaluated for non-MLB markets; profitability comparison remains incomplete.
- Blocking: Feature evaluation for CFB, WNBA, soccer, tennis, esports requires this linkage before promotion decisions can be made.

**Prospective Payout Reconciliation (Nonbinary Contracts):**

- Problem: Current pricing path handles moneyline/spread/total. Integer/void/BTTS terms and conflicting provider metadata remain unavailable.
- Files: Pricing adapters in `src/model_prediction/` (no dedicated module for nonbinary rules)
- Impact: BTTS, player props, and unusual markets cannot be priced; candidate evaluation limited to standard binary markets.
- Blocking: Full sport coverage requires explicit payout-table specification and provider-independent normalization.

**Unified Evidence Aggregation for Challenger Registry:**

- Problem: No canonical log of which candidate has been evaluated on which markets with which results; operator decisions rely on scattered verification reports.
- Files: Evidence scattered across `outputs/research/*/` directories; no centralized registry in `production_registry.py`.
- Impact: Promotion decisions lack a single source of truth; risk of promoting based on partial or outdated evidence.
- Blocking: Full champion-challenger lifecycle requires explicit evidence binding: candidate → artifact hash → market family → evaluation dates → result summary.

## Test Coverage Gaps

**WNBA Injury Module Behavioral Coverage:**

- What's not tested: Real PDF parsing (token extraction, header parsing, status/team mapping), handle for malformed PDF structure, graceful degradation on API unavailability.
- Files: `src/model_prediction/data_sources/wnba_injuries.py`
- Risk: Silent parsing failures; wrong extraction of injury status or team mapping; no error surfacing if PDF format changes.
- Priority: High — this is the highest-risk untested path in WNBA availability.

**KBO/NPB Postponement Handling:**

- What's not tested: Postponement detection (cache empty, placeholder values), idempotent void/archive logic, settlement behavior for postponed games.
- Files: Related KBO/NPB parsing, settlement logic
- Risk: Postponed games remain open indefinitely; no automated recovery.
- Priority: High — currently causing 44 permanently stale rows.

**Historical Data Replay (14 Markets with Missing State):**

- What's not tested: Replay adapters for markets lacking complete stored inputs; handling of missing model context.
- Files: `src/model_prediction/features/` (replay adapters), `src/model_prediction/rebuild/` (shadow replay)
- Risk: Candidate evaluation on historical data silently fails or uses incomplete feature state.
- Priority: Medium — blocking full incumbent-comparison evaluation for 14 markets.

**Settlement Edge Cases (Phantom Ties, Tennis Surface Inference):**

- What's not tested: KBO phantom-tie handling (3 real 3-3 ties vs. 0 phantom 0-0 in canonical sqlite); tennis surface inference degradation.
- Files: Settlement logic, ESPN data adapters
- Risk: Incorrect settlement classification; ledger P&L inaccuracies.
- Priority: Medium — low-frequency events but high impact when they occur.

---

*Concerns audit: 2026-09-20*
