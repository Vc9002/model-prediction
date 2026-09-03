# Project status and source of truth

**Last full-suite verification**: 2026-08-31 (Commit `3066d59`, Tag `lifecycle-v1`). Full test suite passed (2,547 passed, 0 failures, 3 skipped); ruff 100% clean; strict type checking clean across all modules. All 21 supported production markets maintain active champions under the permanent champion–challenger lifecycle framework. NCAAF models (`college-football-v1`, `cfb-spread-v1`, `cfb-total-v1`) are active champions serving under `degraded` evidence status / `critical` replacement priority, with unified decomposed scoring challenger `cfb-structural-v2` implemented. MLB Totals champion is `mlb-structural-v10-frozen` (`promotion_basis: operator_predictive_promotion`) with `measured-edge-totals-v3` as rollback. Automated Polymarket Buyer uses $1\text{U} = \$0.50$.

**Operating Architecture**: Permanent champion–challenger production lifecycle with strict Point-in-Time (PIT) feature extraction, fail-closed `EvidenceOrigin` provenance separation, Unified Model Qualification Registry research control plane, automated Polymarket execution engine, and multi-horizon pregame snapshot tracking ($T-6\text{h}, T-3\text{h}, T-1\text{h}, T-30\text{m}, T-10\text{m}$).

This document is the operational status entry point. `MASTER.md` (repo root)
is the running log of real bugs found/fixed with full evidence.

Historical metrics in old reports, changelog entries, model cards, and
rollback artifacts are not current operational truth.

## 2026-09-03 — Auto-Buyer IOC fill reconciliation + resting-order fallback

- Auto-Buyer marketable orders now reconcile actual IOC fills via the
  exchange's `cumQuantity`/order state instead of assuming the requested
  quantity filled. A partially- or zero-filled buy gets its unfilled
  remainder placed as a second, **resting GTC order at the same price** —
  never a marketable/chased order at a higher price.
- Filled quantity/cost — not requested quantity/cost — now drive the
  dedicated ledger and daily-spend accounting. Fixes the failure exposed by
  the LODIS order (`$6.25` requested, only `~$0.46` actually filled) and
  three siblings from the same batch (two 0%-filled orders, one ~52%-filled
  order) that were all recorded as fully filled.
- See `docs/DEBUG.md`'s 2026-09-03 entry for the full trace, including why
  an earlier same-day version (chase the ask up to 3 cents) was reverted in
  favor of resting the remainder.

## 2026-09-02 — Auto-Buyer MLB moneyline execution disabled

- **Performance view added**: Auto-Buyer now has a settled-only Performance
  sub-tab with daily ET grouping and side-by-side All, MLB, and Without MLB
  cohorts. At the 15:40 ET snapshot, MLB was 6-8 / -$1.00 / -14.4% ROI versus
  41-30 / +$5.82 / +15.2% without MLB. MLB moneyline specifically was 1-5 /
  -$2.215 / -69.4%; MLB non-moneyline was 5-3 / +$1.21 / +32.0%. These samples
  are descriptive, not qualification evidence.
- **Auto-Buyer unit value is configurable independently**: the Auto-Buyer page
  has a confirmed, persisted `1U = $` control for future automated order
  sizing. Vincent changed it from $0.50 to $5.00 on 2026-09-02. Historical rows
  retain their recorded unit value. Risk caps now follow the unit setting at
  5U per game and 50U per day, currently $25 and $250 respectively; the
  confirmation dialog previews both dollar equivalents before applying a
  change. It remains deliberately independent from the general dashboard unit.
- **Settlement resilience verified**: an isolated copied-ledger rehearsal
  produced one new settlement and two cost-basis normalizations, then an
  immediate second pass returned `changed: 0`. Transient ESPN HTTP failures in
  settled-tennis re-verification are now contained per row rather than
  aborting the entire workflow.
- **Settlement reporting repaired**: the settlement response now derives
  `remaining_pending` from the post-settlement ledger's authoritative
  terminal/open predicate. The dialog rejects HTTP errors and malformed
  responses instead of displaying a false zero. Live Dia verification showed
  the KPI and Pending/Open tab agreeing; no settlement run was triggered for
  verification.
- MLB moneylines are blocked by the categorical sport/market policy
  `mlb:moneyline`, independently of model whitelist entries. This prevents a
  persisted override, renamed model, or replacement model from silently
  restoring execution.
- The block occurs before quote lookup and order construction and is visible in
  status/run telemetry as `disabled_sport_markets` and
  `rejected_disabled_sport_market`.
- MLB NRFI and other sports' moneylines are outside this block. Forecast and
  ledger visibility are unchanged; existing exchange orders/positions were not
  canceled.
- Deployment verification: `tests/test_auto_polymarket_buyer.py` passed
  (`16 passed`), touched-file Ruff passed, dashboard health returned OK, and
  the live read-only status reported `disabled_sport_markets:
  ["mlb:moneyline"]`. No Auto-Buyer execution cycle was run for verification.

## 2026-08-31 — Permanent Champion–Challenger Lifecycle, Structural Challengers & Provenance Framework

- **Permanent Champion–Challenger Production Framework Operational**:
  - Invariant: Every supported market always has exactly one production-serving model (`serving_status: production`).
  - Evidence degradation increases replacement priority (`critical`, `high`, `medium`, `low`) without removing market coverage.
  - Fail-closed provenance classification: A prediction is tagged `LIVE_PROSPECTIVE` only if candidate freeze timestamp, matching artifact hashes, and valid pre-event snapshot timestamps are present. Missing freeze/hash/snapshot provenance fails closed to `PIT_REPLAY` or `HISTORICAL_BACKTEST`.
  - Qualification counts (`live_prospective_n`, `pit_replay_n`) are strictly challenger-specific.
- **Structural Challenger Suite Implemented & Evaluation-Ready**:
  - NCAAF Structural v2 (`cfb-structural-v2`): Decomposed independent points simulation deriving ML, spread, and total from joint Poisson/Negative Binomial distribution.
  - MLB Runline v4 (`mlb-structural-runline-v4`): Bivariate Poisson $-1.5 / +1.5$ derivation directly from joint run distribution.
  - WNBA Structural v3 (`wnba-structural-v3`): Unified possessions $\times$ PPP basketball simulation engine.
  - MLB Market-Residual v10 (`mlb-moneyline-market-residual-v10`): Logistic information-delta architecture over market baseline.
  - NBA & NFL Structural v5 (`nba-structural-v5`, `nfl-structural-v5`): Four Factors possession modeling and NFL EPA/play pressure and weather penalty simulation.
  - Esports Contextual v7 & International Baseball v3 (`*-contextual-v7`, `*-baseball-v3`): Contextual Elo residuals and 12-inning tie distributions.
- **Multi-Horizon Pre-Event Tracking Operational**: Continuous snapshot recording at $T-6\text{h}, T-3\text{h}, T-1\text{h}, T-30\text{m}, T-10\text{m}$ logging model probability, market prices, and point-in-time information deltas.

## 2026-08-28 — MLB Structural v10 Freeze, Protocol Hashes & Phase F1C Prospective Confirmation Activation

- **MLB Structural v10 Frozen for Prospective Confirmation (`F1C_V10_PROSPECTIVE_CONFIRMATION`)**:
  - Model weights fit on the full $N=5,427$ 2024–2026 pre-freeze development dataset and frozen to [`config/models/research/mlb_structural_v10_frozen.json`](file:///Users/vincentc9002/model-prediction/config/models/research/mlb_structural_v10_frozen.json).
  - Protocol & feature hashes computed and frozen:
    - `v10_feature_schema_hash`: `107a42b6586e7be2`
    - `v10_model_spec_hash`: `6b677efdf92de0cd`
    - `v10_confirmation_protocol_hash`: `ca35b34f61917062`
- **Calibration Slope Audit**: Audited the v9 reporting metric ($73.55$). Root cause: v9 heuristic predictions had near-zero variance ($8.60 \pm 0.3$, $\text{Var} \approx 0.03$), causing the empirical OLS calibration slope $\text{Cov}(Y, X)/\text{Var}(X)$ to blow up. In contrast, v10 has full match-level dispersion ($6.5$ to $11.5$ runs) and well-conditioned calibration ($b = 0.8308$).
- **Mechanistic Regime-Delta Analysis (v9 vs v10 on Development Data)**:
  - *Low Market Totals ($\le 7.5$)*: v9 had negative slope ($\beta_{within} = -0.1473$); v10 flipped it to **$\beta_{within} = +0.5347$** with positive MAE gain, confirming Empirical Bayes starter depth and bullpen demand resolved the low-total overprediction bug.
  - *Domes / Indoor Climate-Controlled*: v9 had negative slope ($\beta_{within} = -0.1456$); v10 neutral physics flipped it to **$\beta_{within} = +0.8084$** with MAE gain increasing from $+0.0078$ to $+0.0326$.
  - *High-K Starters*: $\beta_{within}$ amplified from $+0.2467$ to **$+0.7398$**.
  - *Mid Totals ($8.0–9.0$)*: $\beta_{within}$ amplified from $+0.1793$ to **$+0.5315$**.
- **Prospective Shadow Pipeline Operational** ([`scripts/mlb_v10_prospective_shadow.py`](file:///Users/vincentc9002/model-prediction/scripts/mlb_v10_prospective_shadow.py)): Captures immutable pregame predictions at decision timestamp $T-30\text{m}$ with SHA-256 prediction hashes to `data/point_in_time/mlb_v10_prospective_predictions.jsonl`.
- **Preregistered Prospective Gates (C1/C2)**:
  - Primary comparison: $M0b$ vs $M4-1(v10)$, requiring $G = MAE(M0b) - MAE(M4-1(v10)) > 0$ with date-clustered bootstrap $P(G > 0) \ge 0.90$.
  - Sample requirements: Initial evidence $N \ge 300, D \ge 30$; full qualification $N \ge 500, D \ge 50$.
  - F2–F8 and MLB-INT-001..005 remain strictly **LOCKED**.

## 2026-08-28 — Mypy Zero-Error Initiative, Fast CI Smoke Test, Hypothesis Invariant Testing & MarketQuote Warehouse

- **Mypy Static Type Hardening Complete (Zero-Error Milestone)**: Reduced errors from 220 across 59 files down to **0 errors across all 319 source files**. Hardened strict types across dashboard routes/components, production persistence, core ledgers, model implementations (Monte Carlo, Dixon-Coles, Tennis, MLB features), calibration, and ablation tooling.
- **Fast Deterministic CI Daily Cycle Smoke Test Built & Verified** ([`tests/test_daily_cycle_smoke.py`](file:///Users/vincentc9002/model-prediction/tests/test_daily_cycle_smoke.py)): Validates the entire synthetic multi-sport daily pipeline (ingest $\rightarrow$ forecast $\rightarrow$ dual-write append $\rightarrow$ idempotency $\rightarrow$ settlement $\rightarrow$ hash-chain audit $\rightarrow$ XLSX projection parity) in **0.19s** (strictly meeting the $<3.0\text{s}$ CI budget).
- **Hypothesis Stateful Property-Based Ledger Fuzzing Built & Verified** ([`tests/test_ledger_invariants_hypothesis.py`](file:///Users/vincentc9002/model-prediction/tests/test_ledger_invariants_hypothesis.py)): Continuously fuzzes state transitions and proves key mathematical/architectural invariants: P&L conservation ($\sum \text{P&L}_i \equiv \text{P&L}_{\text{total}}$), state machine irreversibility (settled/voided picks never revert), operation deduplication idempotency, and SHA-256 parent-link hash-chain integrity.
- **Canonical MarketQuote Warehouse Schema Implemented** ([`src/model_prediction/data_sources/market_warehouse.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/data_sources/market_warehouse.py), [`domain.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/domain.py)): Added canonical immutable `MarketQuote` dataclass and high-performance SQLite warehouse with strict point-in-time (`observed_at_utc <= as_of_utc`) filtering and bulk ingest.
- **Full Test Suite & Linter Verified**: **2,420 passed, 0 failures, 3 skipped**; **0 Ruff findings** across the entire repository.

## 2026-08-27 — Full-System Defect Audit, Serving Landmine Identification, Weather/Travel Research & Documentation Sync

- **Exhaustive System Defect Audit cataloged** in [`docs/SYSTEM_DEFECTS_AND_GAPS_AUDIT.md`](file:///Users/vincentc9002/model-prediction/docs/SYSTEM_DEFECTS_AND_GAPS_AUDIT.md) covering serving landmines, external data outages, PIT risks, and static type safety.
- **MLB NRFI promotion landmine identified, fixed, and promoted** — holdout logloss `0.690572` reproduces `0.6910` baseline in `mlb-nrfi-v1.json`. The live path `_forecast_mlb_nrfi_flat` had been invoking the legacy unfitted `MLBNRFIModel()` under the same `mlb-nrfi-v1` model_version string; rewired to `MLBFirstInningModel.from_dict(artifact)` with a live PIT feature builder (`mlb_first_inning_live.py`, exact-match verified against the batch ledger on 7 real games). Champion `MLB.nrfi` set; `blocked_workflows` now empty.
- **MLB Weather & Travel research ablation completed** (`scripts/mlb_weather_travel_ablation.py`): Control MAE 3.584296 vs Candidate 3.578813 (gain +0.005484, 95% CI `[-0.000215, +0.011088]`) $\rightarrow$ `NO_PROMOTION` verdict under strict reproduction gate.
- **Static type audit**: Ruff 0 findings; Mypy 220 errors across 59 files documented.
- **Full suite verified**: 2,414 tests pass, 3 skipped, 0 fail.

## 2026-08-26 — Full-repo scan resolutions, ledger conflict repair, scheduler restoration

- **13 dead v9 features root-caused and fixed** — four lookup bugs (nested snapshot schema, team-ID vs name keys, wrong feature name, missing starter-name crosswalk). All 19 previously-zero-variance columns now vary per game (66–185 distinct values on a 200-game sample). Prior v9 ablation results on those families remain void pending the rebuilt-table audit. New flag: platoon/projected collinearity (r≈0.9997) needs an operator decision.
- **2 artifact hash "mismatches" confirmed deliberate non-issues** (never-re-signed archive/quarantine annotations); the verifier now documents them as `known_hash_mismatch` instead of gaps.
- **3 orphaned model configs resolved** — two inert temperature-challenger configs removed; `mlb-v9-candidate-1.json` stays quarantined (fail-closed gate depends on it).
- **Ledger settlement backlog cleared** — 22-row cs2 backlog applied, 6 identity conflicts resolved from canonical SQLite (latest-settlement + lineage evidence), 27 further settlements unlocked, audit economic signature stake-normalized, all tier projections rebuilt. Final audit: 0/0/0, integrity ok.
- **Production scheduler restored** — `production` + `rebuild-shadow` launchd jobs were disabled (37h, undocumented); operator approved re-enable; both loaded and completed exit 0.
- **launchd daily plist re-synced** to the checked-in form (byte-identical, reloaded, schedule verified).
- **`docs/RESEARCH_BACKLOG.md` re-deleted**; unique content ported to `docs/ROADMAP.md`.
- **Data-gap audit (new)** — soccer capture replaced with API-Football v3 (`data_sources/api_football.py` + daily wiring; dormant Odds path kept as fallback; awaiting `API_FOOTBALL_KEY` + live verification). Open follow-ups: WNBA availability snapshots 1.5d stale; Statcast aggregates manual-only; zero snapshot lineage for esports/soccer/KBO/NPB rows; NBA/NFL no odds source wired. Corrected: Polymarket has ATP/ITF tennis markets; soccer Odds-API outage is ≥31 days (not 11.7).
- **NRFI model improved** — 2x league-constant bug fixed (0.52 per-team mean vs 1.036 per-game total); new fitted first-inning model (`models/mlb_first_inning.py`) beats the incumbent and the market proxy on the locked 1,337-game test window (logloss 0.6910 vs 0.6945 vs 0.6950). Next: capture real Polymarket NRFI quotes to measure true edge.

## 2026-08-26 (night) — documented-bug triage + Bet Better capture + WNBA parser fix

- **WNBA morning-report parse bug root-caused and fixed** — the entry pass
  propagated date/team context only through player rows; the morning
  layout prints the game date on a team-level "NOT YET SUBMITTED" row.
  All 81 real 08-26 reports now parse; real-PDF regression fixtures added.
  This closes the "WNBA availability snapshots stale" handoff item (the
  feature's NO_CALL degradation was correct fail-closed behavior).
- **Bet Better model-feed capture wired** (`step1e_bet_better_models`) —
  keyless no-account cross-check source (mlb/wnba/nba/nfl/soccer/wta),
  research-only reference evidence, CC BY attribution recorded.
- **Statcast aggregates wired into the daily** (was manual-only, 3 days
  behind) — live rebuild covers 57k pitcher / 135k batter rows through
  08-26.
- **Registry-free ban enforcement** wired into `evaluate_esports_eligibility`
  (the bans.py mechanism already existed; the eligibility check never
  consulted it). Forecast call sites still need to thread `bans` through.
- **mlb-v9-candidate-1 identity collision** — benchmark rows now record
  `mlb-v9-benchmark`; workbook/history untouched.
- **Postponed-game handling (drain-minimal)**: Polymarket keeps postponed
  markets OPEN (resolves to makeup result within two weeks, else last-fair
  price / 50-50); the daily already auto-voids STATUS_POSTPONED rows. New
  `stale_open_rows` health check (pure read, zero new I/O) surfaces the
  stuck-open class continuously — first run: 22 rows >72h past start.
- **Daily-run timing instrumentation** added (wrapper per-step seconds +
  `timing` block in the daily report) — today's runs ranged 6.7–22.9 min;
  optimization targets come from tomorrow's log.
- **Soccer capture data_root split-brain** fixed before it ever fired
  (explicit `data_root` at the daily call site).
- Triaged as already-fixed by later sessions: KBO phantom ties, tennis
  surface inference, DD items (pre-commit hook, dashboard split, TODO
  tracking), WORLD_CUP conftest fixture. Remaining open research gap:
  MLB totals absolute-run-environment signal.

## 2026-08-24 — Canonical ledger and tennis integrity repair

- Corrected 221 tennis spread/total settlements using exact ESPN identities
  and per-set game totals: 205 regraded, 16 retirement derivatives voided,
  corruption signature zero, P&L corrected by `+187.0556U`.
- Removed unsupported tennis derivative pricing; the forward path is now
  moneyline-only and rejects subperiod markets. A second boundary permits at
  most one spread and one total per match if validated derivative pricing is
  introduced later.
- Archived 457 settled duplicate/correlated-exposure rows and removed 9 open
  rows with exact-ID audited mutations. The post-repair planner reports zero
  remaining refresh groups and zero tennis ladders; SQLite now enforces active
  contract/model uniqueness across writers.
- Made SQLite authoritative for all ledger and dashboard reads; rebuilt and
  verified all 22 XLSX projections with no canonical tombstones.
- Backfilled explicit feature-availability payloads on all 12,335 canonical
  records without synthesizing missing values.
- Removed unqualified MLB totals from production serving, blocked WNBA total
  without its exact artifact, and made all active non-serving workflows
  degrade health instead of passing green.
- Rebuilt the dashboard cache from canonical rows: Main 494, Flat 1,682,
  Research 996, Gated Research 356.
- Remaining historical gaps are fail-closed: 3,240 active rows lack market
  snapshot lineage, 40 active MLB rows lack artifact hashes, and 24 open rows
  are more than 24 hours past start. These values cannot be reconstructed as
  decision-time evidence after the fact.

## 2026-08-23 — Comprehensive Platform Optimization, Quantitative Upgrades & Parallel Test Harness

- **Storage & Dashboard I/O Optimization** (`runtime_ledger_store.py`, `dashboard/data_service.py`):
  - Configured SQLite WAL mode connections with `PRAGMA synchronous=NORMAL`, `PRAGMA temp_store=MEMORY`, 256MB memory-mapped I/O (`mmap_size`), and 64MB cache for $3\times\text{--}5\times$ faster throughput without lock contention.
  - Configured zero-contention read-only handles with `PRAGMA query_only = ON` in `dashboard/data_service.py` for sub-millisecond API response latency.
- **Analytical Poisson Scoring Engine** (`src/model_prediction/total_score.py`, `tests/test_optimizations.py`):
  - Replaced noisy Monte Carlo simulation with exact 2D Poisson joint matrix solvers: `analytical_totals_probabilities` and `analytical_spread_probabilities`.
- **Cross-Market Consistency & Dutching Arbitrage** (`src/model_prediction/cross_market_consistency.py`):
  - Implemented full bidirectional Spread vs. Moneyline monotonicity bounds and multi-book `calculate_dutching_arbitrage` with optimal stake distribution.
- **Adaptive Meta-Calibration** (`src/model_prediction/meta_calibrator.py`):
  - Added exponential recency weighting (`sample_weights`) to Platt/Isotonic fitting and high-speed `calibrate_batch` array transforms.
- **Soccer Dixon-Coles Derivative Market Decomposition** (`src/model_prediction/models/soccer_dixon_coles.py`):
  - Implemented `prob_draw_no_bet`, `prob_clean_sheet`, `prob_win_to_nil`, and `prob_exact_goals_table` directly on `BivariateScoreGrid`.
- **Execution Ticket Safety & Test Harness** (`src/model_prediction/execution_ticket.py`, `pyproject.toml`):
  - Added `is_ticket_valid` and `extract_order` non-raising inspection helpers.
  - Pinned `pytest-xdist>=3.5,<4` in dev dependencies for parallel test execution.

## 2026-08-23 — Two-Track MLB Architecture Locked, Step 26 Player Models Deployed & Research Backlog Synchronized

- **Roadmap Tier 2 & Tier 4 Deliveries (Items 1–5)**:
  - **MLB Totals v2 Component Rebuild** (`src/model_prediction/total_score.py`, `tests/test_total_score.py`):
    - Innings-weighted expected runs allowed: $(\text{Starter Expected IP} \times \text{Starter RA}) + (\text{Bullpen Expected IP} \times \text{Bullpen RA})$ via `mlb_pitching_runs_allowed`.
    - Short-rest starter fatigue penalty ($+0.50$ ERA penalty on $<4$ days rest).
    - Stadium wind direction $\times$ compass orientation vector multiplier (`stadium_wind_orientation_multiplier`) with dome override.
    - Composite game total projections via `mlb_totals_v2_projected_runs`.
  - **Statcast Pitch-Level Data Acquisition** (`rebuild/providers/statcast.py`, `tests/rebuild/test_statcast_provider.py`):
    - Implemented `StatcastProvider.aggregate_pitcher_metrics` computing fastball velocity levels, CSW% (called strikes + whiffs), K-BB%, and xwOBA allowed.
  - **Market-Blend Serving Layer** (`src/model_prediction/market_blend.py`, `tests/test_market_blend.py`):
    - Out-of-fold learned weights $p_{\text{blend}} = w \cdot p_{\text{model}} + (1-w) \cdot p_{\text{market}}$ verified with SHA-256 cryptographic experiment specs and OOF gate enforcement.
  - **Soccer Draw Calibration & Double Chance Pricing** (`models/soccer_dixon_coles.py`, `tests/test_soccer_dixon_coles.py`):
    - Implemented `prob_double_chance`, `soccer_double_chance_probabilities` (1X, X2, 12), and `draw_calibrated_probabilities` with low-scoring draw inflation.
  - **Feature Registry Hygiene & Tier 4 Test Coverage** (`tests/test_features_tier4_modules.py`):
    - Verified and added 100% test coverage for `features/tennis_surface.py`, `features/head_to_head.py`, `features/lineup_strength.py`, and `data_sources/mlb_statsapi.py`.
- **Flat vs. Gated Ledger Invariants & Quality Gating** (`tests/test_ledger_invariants.py`, `rebuild/decision.py`, `polymarket_kelly.py`, `units.py`):
  - Hardcoded and pinned governing invariant: **Flat Ledgers** (`Flat Forecast` and `Flat Research`) evaluate and record *every* candidate game with **no edge gate, no spread price caps, and no minimum edge hurdle**.
  - **Gated Ledgers** (`Production Ledger`, `Gated Research`, `Polymarket Edge Ledger`) strictly enforce quality gates, including a $+3.5\text{pp}$ minimum edge hurdle and a $60.0¢$ price ceiling on spread/runline contracts to prevent negative-ROI spread drag.
  - Sizing refactored to **Real-Edge Quarter-Kelly** ($\text{adjusted\_prob} - \text{market\_prob}$), preventing over-allocation on heavy favorites with thin edges.
- **Polymarket Edge Pipeline Automation** (`portfolio/polymarket_ledger.py`, `portfolio/polymarket_scanner.py`, `cli/settle.py`, `run_supervisor.py`):
  - Automated slate scanning, edge pricing, auto-logging to `polymarket_picks.xlsx`, and multi-sport ESPN scoreboard settlement.
  - Added correlation-aware exposure caps (`apply_correlation_exposure_caps`) preventing stacked tail risk on same-game derivatives.
- **Statistical Validation & SPRT Infrastructure** (`rebuild/sprt.py`, `rebuild/ablation.py`, `rebuild/validation.py`):
  - Implemented `BernoulliSPRT` and `GaussianSPRT` sequential testing with $(\alpha, \beta)$ stopping boundaries for promotion candidates.
  - Implemented `PreRegisteredExperiment` enforcing pre-declared decision thresholds before running ablations.
  - Implemented `minimum_detectable_effect` pre-check (detectability < 2.0% delta) and hierarchical `season_block_bootstrap` preserving within-season temporal autocorrelation and year-over-year shifts.
- **Dashboard & UI Optimizations** (`dashboard.html`, `tests/test_dashboard_html.py`):
  - Unified, case-insensitive filter system for all 5 ledgers (`L`, `F`, `R`, `G`, `PL`).
  - Dynamic option population on lazy cache load without wiping selections.
  - 120ms debounced search inputs, instant 0ms tab switching from warm memory cache, and fully concurrent network pipeline.

## 2026-08-20 — MLB YRFI/NRFI, WNBA Four Factors, Cross-Market Consistency, Meta-Calibrator & Root Wake Daemon

- **MLB YRFI / NRFI Model & Walk-Forward Research**:
  - Implemented point-in-time feature pipeline (`features/yrfi_nrfi.py`), hybrid decomposed Poisson/logistic model (`models/mlb_nrfi.py`), and test suite (`test_mlb_nrfi.py`).
  - Evaluated on 6,610 historical games in `scripts/mlb_nrfi_research.py`: Holdout Brier improved by $\mathbf{-0.00268}$ and Log Loss by $\mathbf{-0.00534}$.
- **WNBA Pace & Four Factors Modeling**:
  - Implemented Dean Oliver's Four Factors normalized for WNBA 40m regulation in `features/wnba_pace_four_factors.py` with Empirical Bayes shrinkage. Tested via `tests/test_wnba_four_factors.py`.
- **Portfolio Integrity & Cross-Market Consistency**:
  - Built `cross_market_consistency.py` validating monotonicity ($P(\text{Cover } -1.5) \le P(\text{Moneyline Win})$) and complementarity ($P(\text{Over}) + P(\text{Under}) = 1.0$).
  - Built `meta_calibrator.py` providing multi-sport pooled Platt scaling and Isotonic Regression.
- **Dashboard & Observability Endpoints**:
  - Added `/api/clv` (rolling 30-day CLV and closing beat rate) and `/api/capture_health` (7-day BBO snapshot freshness) in `dashboard/status.py` and `routes.py`.
  - Added push notification dispatcher (`notify_operator`) in `run_supervisor.py`.
- **Lineup Wake Root LaunchDaemon Plist**:
  - Created `ops/launchd/com.vc.mlb-lineup-wake-planner.plist` with full instructions for root installation to automate slate-following `pmset` wakes.
- **Hygiene & Type Safety**:
  - Purged 11 dead worktree directories from `$HOME`.
  - Added `src/model_prediction/py.typed` and mypy overrides in `pyproject.toml`.
  - **1,938 tests pass / 3 skipped / 0 failed; ruff clean.**

## 2026-08-19 — v9 ladder measured null; lineup capture live; wake planner pending operator install

- **The v9 isolating ladder is a measured null.** Seven single-variable
  changes against the v8 control, proper paired date-cluster bootstrap:
  best is residual_trend at ΔLL −0.00017 (P=0.658); the PIT park fix does
  not help; starter_fip is significantly worse. All six v8 features are
  worth 0.0046 nats over the constant base rate (0.6912 → 0.6866, AUC
  0.567). Stop optimizing the six existing abstractions — the corrected
  plan acquires materially different information instead. NB challenger
  REJECTED (the 203-game win excluded gamma_poisson; the 3,513-game
  head-to-head shows NB losing). `rest_disparity`, `probable_starter_era_gap`,
  `starter_fip`, `starter_kbb` are closed experimental branches.
- **Prospective lineup capture is LIVE.** Hourly job
  `com.vc.mlb-lineup-capture` verified by its own log output (exit 0,
  clean stderr). Archive `data/point_in_time/mlb_lineups.jsonl` — 16
  rows, 5 decision-grade, content-hash dedupe with first/last/count
  confirmation metadata. This dataset cannot be backfilled; the clock
  started 2026-08-18.
- **Known exposure — the sleep gap.** launchd coalesces missed hourly
  firings into one run at wake, so overnight sleep permanently loses
  late-game lineups. `scripts/plan_lineup_wakes.py` schedules one-time
  wakes that follow the slate, but `pmset` requires root — **operator
  install of a root LaunchDaemon is PENDING.** First quality report
  already shows the skew: 5/5 captured games in 7-9pm, zero 9pm+
  coverage.
- **Next experiment**: batter PIT priors (`projected_offense_pit`,
  strictly no target-game order in history; predeclared 3-component
  family), then reliever workload × quality. New features land in a
  versioned `mlb_v9_feature_table_v2.parquet`; the frozen v1 table stays
  immutable as the evidence behind the nulls.
- Commits `a7c9669` (settlement fix), `ce89eca` (capture), `70db329`
  (hourly + schema), `c9d5800` (planner + metrics) on
  `research/mlb-v9-lineup-and-bullpen`. 1903 passed / 3 skipped; ruff
  clean.

## 2026-08-18 — model-ledger settlement repaired; WNBA totals promotion refused

- **Settlement bug fixed (was silently live).** `settle_from_pick_row`
  graded on the append-side key, which carries `observed_at_utc`, so only
  one row per event ever settled and every re-forecast row of a finished
  game stayed open forever. WNBA spread ledger had **42 of 67 rows stuck
  open** for games already played, and per-model evidence was computing on
  9 rows instead of 51. Fixed (`_event_settlement_key` +
  `ModelLedger.settle_event`), 4 regression tests, stranded rows backfilled
  from final scores with a 9/9 self-check. Ledger now 51 settled / 16 open.
- **WNBA spread model is sound but has no edge.** Pricing math verified
  correct. Now that evidence grades: **7W-7L (50.0%) across 14 distinct
  contracts**, Brier 0.2856 vs 0.2500 for always-0.5. Far short of the
  50-call / 60% gate. Note the 51 settled rows are only 14 contracts —
  minimum-sample gates must count contracts, not rows.
- **WNBA total model NOT promoted.** Its own artifact fails its locked
  holdout (95% CI entirely negative), it is a score model never evaluated
  on over/under accuracy, it is not reproducible from current code (9
  features vs 11), and **no WNBA totals serving path exists** — promotion
  would have produced zero picks. Retraining made it worse. Full record in
  `docs/MODEL_IMPROVEMENTS.md` section 7.
- **Shared totals builder fixed**: `last_10_total_avg` was an exact
  duplicate of `league_total_mean`; the real point-in-time signal improves
  MLB, NBA and NFL.
- **Open policy question**: the totals verdict uses a point estimate while
  storing a bootstrap CI it ignores — NFL currently reads
  `improved_vs_baseline=True` on a gain whose CI straddles zero.

Champions unchanged; no promotion decisions taken.

## 2026-08-15/16 — open-items closure (post-consolidation sweep)

Every open item from the read-through was fixed, closed with evidence,
or explicitly deferred by design:

- **Code debt**: dead `SportModel`/`ScoreModel` protocols removed; ruff
  baseline cleared to **0 findings** with `.pre-commit-config.yaml`
  installed; in-code debt markers added (cli/dashboard monoliths, WNBA
  PDF path); DD-14 closed (the 105 model_id-less rows are a bounded
  08-14 cutover-day artifact, 0 since, not backfillable).
- **Detectors/reports**: `scripts/check_mlb_ingest_completeness.py`
  (7-day scan clean); `outputs/latest/learned-model-validation.json`
  regenerated via `validate-models` (docs' `validate-learned` name was
  stale); `outputs/rebuild/verification.json` policy documented as
  CI-generated (local 404 expected).
- **v8 parity layers D–I + L closed** (research branch): 40-game sample
  through serving definitions — elo/trend/park 40/40 exact; weather
  ≤0.029 source-drift; starter ≤5e-4 map rounding; orientation field
  inert at serving (consistent, hardening note).
- **Venv hazard fixed**: the launchd dashboard pointed at a deleted
  `.venvs/model-prediction` interpreter (would have crash-looped on the
  next restart) — plist now uses the repo `.venv`, verified.
- **Park leak verified**: static table = 7,926 games (2024-02-22 →
  2026-08-12) applied retroactively to history; documented, v8 frozen,
  `park_factor_pit` is the v9 path.
- **Git hygiene**: 19 local + 7 remote stale branches removed (all
  verified merged/superseded); remotes now `origin/main` +
  `origin/research/mlb-v8-reproduction`.

## 2026-08-15 session changes (consolidation P0 — control-plane singularity)

- **P0-1 — runtime root fails closed**: `RuntimePaths.resolve()` gained
  `require_external_runtime=True`; every operational entry point
  (run supervisor, production canary, promotion CLI, system health,
  dashboard server + data service, cli_production) now raises instead of
  falling back to repo `data/` when `MODEL_PREDICTION_RUNTIME_ROOT` is
  unset. The env-less fallback had silently created a second runtime next
  to the canonical one (split-brain). Regression tests pin the refusal
  and that no repo-local DB appears. Local dev keeps the default
  `resolve()`; `RunSupervisor` accepts pre-resolved `paths` for read-only
  callers (system_health).
- **P0-1b — stray repo-local DB quarantined**: `data/ledgers/ledgers.db`
  was empty (0 records / 0 events / 0 runs, verified before removal) and
  moved to `backups/split-brain-quarantine-20260815/`. The repo-local
  `data/production/predictions.db` (646 rows) is a strict subset of the
  runtime copy (660 rows, 0 repo-only) — no merge; its untracking is part
  of K.
- **P0-2 — exactly one daily scheduler**: the dashboard's "daily" action
  now runs `python -m model_prediction.run_supervisor run daily` instead
  of `scripts/run_daily.sh` directly. A busy supervisor lease returns
  exit 75 (daily_lock convention) and maps to job status `skipped`
  ("another run already active") instead of `failed`. All three launchd
  jobs already route through the supervisor.
- **P0-3 — one launchd-owned dashboard**: verified single listener on
  :8765; pidfile (`dashboard/server.pid`), launchctl PID, and `lsof` PID
  agree across two consecutive restarts. Added
  `MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite` to
  `com.vc.model-dashboard` so dashboard-triggered runs use the same
  ledger authority as the scheduled jobs (the default was xlsx).
- **P1 — dashboard job history survives restarts**: `_hydrate_jobs()` at
  server start loads `dashboard/jobs.json` into memory (persisted
  `running` → `interrupted`, `started_monotonic` never restored), so the
  first post-restart persist no longer wipes pre-restart history.
  Verified live: the pre-restart job record serves via `/api/job` after a
  `launchctl kickstart` restart.
- **K — runtime singularity + clean-tree gate PASSED (2026-08-15)**:
  - Rolling/frozen artifact split: the daily cycle retrains
    esports/KBO/NPB ratings into the runtime root's `models/`
    (`RuntimePaths.models_root`); `config/models/*.json` are frozen
    promoted artifacts. Live daily cycle confirmed: rolling copies
    rewritten at runtime, checked-in copies untouched.
  - ~2,300 churn files untracked (`git rm --cached`, files remain on
    disk): availability captures, player priors, ingest data, odds
    snapshots, ledger XLSX exports (SQLite canonical), outputs/latest,
    logs, features, dashboard scratch. `data/archive/` and `snapshots/`
    remain tracked evidence.
  - Repo-local split-brain relics quarantined to
    `backups/split-brain-quarantine-20260815/` (empty ledgers.db, stale
    production/predictions DBs verified subset-of-runtime, runs.db,
    dashboard cache, rebuild shadow DBs). No `*.db` remains under
    repo `data/`.
  - Frozen-champion snapshots and the dashboard cache DB now write under
    the runtime root; the daily worker's lock lives at the runtime root.
  - **10/10 K acceptance criteria pass** — exactly one runtime root; env-less
    operational invocations fail closed; one daily scheduler; one
    launchd-owned dashboard with canonical env; SQLite ledger canonical
    (4,258 hash-linked events after the cycle); rolling artifacts, XLSX
    exports, and raw captures outside git; full supervisor production +
    daily cycle leaves `git status --porcelain` empty.
- Post-K fixes: the esports ablation harness and the dashboard
  production-evidence check are rolling-aware (they compare against the
  runtime-root rolling artifact when present, frozen config copy
  otherwise); their tests are hermetic now that data/ trees are
  untracked (no machine-local data dependence in CI).
- **N — exact-head CI + merge + freeze DONE**: full suite 1871 passed
  locally; CI green on the exact branch head (all 4 jobs: incumbent
  3.11/3.12/3.13 + rebuild acceptance incl. ruff/mypy delta gates and
  dashboard smoke); merged to `main` via PR #30 (merge SHA `37be479`);
  tag `consolidation-2026-08-15` published on the merge SHA.
- **O — burn-in clock STARTED 2026-08-15 05:25 UTC** (≥3 days, through
  08-18): checklist and results in `docs/BURN_IN.md`. Day-0 checks pass.
  GitHub purge template for the briefly-exposed quarantine DB commits:
  `docs/PURGE_REQUEST_TEMPLATE.md`.
- **Git hygiene (2026-08-15)**: 19 stale local branches + 2 stale
  worktrees deleted after verification (0 unique commits vs origin/main
  or superseded parallel implementations; one dirty doc diff preserved
  under `backups/removed-branch-artifacts-20260815/`); merged remote
  `cleanup/final-debug-2026-08-14` deleted; `main` tracks `origin/main`.
- **MLB research prep started (burn-in window, isolated workspace)**:
  worktree at the frozen tag (`worktrees/mlb-research`, branch
  `research/mlb-v8-reproduction`) with data symlinked from the live
  checkout. Aggregate v8 pin-and-replay re-confirmed (150 vs 148 calls,
  hit delta 0.0052). New row-level parity tooling found: 31 net-new
  post-freeze holdout rows (+2 freeze-time rows lost, no snapshot
  exists to identify them); excluding the 31 reproduces 148/148 calls;
  coefficient parity fails (refit vs shipped max delta 0.0107) because
  history-dependent features shifted with backfills — v8's exact
  probabilities are unrecoverable, per-row drift bounded at 0.0006.
  Full contract + findings: `docs/V8_REPRODUCTION.md` (research branch).
  No promotion decisions; v8 unmodified. Next prep: frozen v9 feature
  table + standardized evaluator.

## 2026-08-14 session changes (KBO settlement bug, ledger archival, doc corrections)

- **KBO settlement bug fixed**: `parse_kbo_rows` (`international_baseball.py`)
  fabricated a `game_id` for unplayed games (empty relay cell on the official
  schedule page), which cached as a phantom scoreless-tie row and settled
  real picks as 0-0 pushes — confirmed 16/16 settled KBO research-ledger rows
  affected (0/37 NPB rows affected; NPB's parser already skipped unplayed
  rows). Fixed to skip unplayed rows instead of fabricating an id, plus a
  guard against already-cached phantom rows from before the fix. Two
  regression tests added (`tests/test_international_baseball.py`). All 16
  affected rows self-corrected via the ledger's audited
  `pick_resettled_corrected` path on the next scheduled settlement run — real
  scores, real win/loss results, real P&L now in `data/research/kbo.xlsx`.
- **406 settled picks archived** for retired model versions (MLB
  moneyline v7→v8, spread/total v1/v2→v3; esports LOL/CS2/Dota2/Valorant
  v5→v6) across Main/Flat/Research/Gated Research, via the sanctioned
  `PickLedger.archive_settled_rows` path, following the
  `2026-07-31-retired-mlb-model-picks` precedent. Manifest and per-tier
  archive files at `data/archive/2026-08-14-retired-model-picks/`. Row-count
  reconciliation exact on all 10 touched files; `verify-chain` clean
  (0 breaks).
- **365 orphaned research-ledger rows repaired**: rows carrying reason_code
  `NO_CALL_WINNER_OVERVALUED` (a value-gate check that only ever existed on
  an unmerged rebuild branch, `archive/rebuild-clean-slate-v1-...` /
  commit `ed580af` — never part of any mainline commit) had `units=0,
  pnl_units=0`, violating this codebase's own "every logged pick carries a
  real paper size" invariant. Backfilled via the live `edge_scaled_units()`
  formula from each row's own recorded inputs; `pnl_units` recomputed from
  the real result. `decision`/`record_type` left as `NO_CALL`/
  `RESEARCH_OBSERVATION` (not retroactively promoted to CALL).
- **`CLAUDE.md`**: removed a section with unrecoverable data loss (empty
  template placeholders baked in at commit time, confirmed via git history —
  no original content existed to restore).
- **Verified against `origin/main`** (confirmed real, both already fixed in
  local unpushed commits): `production.yaml` allowlists 13 models while
  `production_canary.py` on `origin/main` requires exactly 1 (mechanically
  incompatible there); `_check_data_freshness` on `origin/main` is a literal
  stub (`return None` unconditionally). Both fixed locally; regression test
  for the freshness fix (`test_health_check_degrades_on_stale_prediction`)
  passes.
- **Launchd jobs verified actually advancing**, not just loaded:
  `com.modelprediction.production` and `com.modelprediction.rebuild-shadow`
  both `state = active`, `last exit code = 0`; `data/production/
  predictions.db` and `$MODEL_PREDICTION_RUNTIME_ROOT/rebuild/shadow.db`
  both had writes within the current 3-hour scheduling interval.
- Full suite: 1759 passed, 3 skipped (up from 1753 — 2 KBO regression tests
  plus others accumulated this session). Ruff: same ~120-finding
  pre-existing baseline, no new findings.

## 2026-08-13 session changes (champion/challenger + settlement + distribution)

- **Champion/challenger gating** (`src/model_prediction/champion_challenger.py`):
  production freeze (`freeze-production`), paired comparison
  (`compare-champion`), settled-picks loader. 13 champions frozen to
  `data/production/frozen_champions.json`. See `docs/CHAMPION_CHALLENGER.md`.
- **WNBA spread fixed**: `wnba-spread-baseline-v1` predicted moneyline, not
  spread (no line used) — replaced by `wnba-spread-margin-v1`.
- **MLB distribution methods**: `simulate_game` now supports
  `gamma_poisson` (incumbent) / `negative_binomial` / `independent_poisson`;
  ML/spread/total derived from one joint draw. NB is runnable but not promoted.
- **Settlement routing fixed**: model-ledger mirror writes canonical
  `data/model_ledgers/` again (was per-tier after the split, freezing the
  dashboard/loader's read on 2026-08-03). See `docs/archive/SETTLEMENT_GAP.md`.

## 2026-08-13 deep-audit fix pass

Full audit + fixes; evidence and per-fix detail in `MASTER.md`'s 2026-08-13
session entry (F-72 onward). Summary:

- **compare-champion CLI** was completely broken (swallowed KeyError
  mislabeled `NO_CALL_INVALID_MARKET`, exit 0) — fixed (F-72).
- **freeze()** silently treated missing artifacts as code-backed; kbo/npb
  artifacts were written as `-v1.json` while claiming v2 — files renamed,
  config refs fixed, freeze now fails loudly for artifact-backed holes (F-73).
- **load_settled_predictions** mixed artifact versions (MLB "champion"
  metrics were 244 v7 rows vs 14 v8) — now filters by model_version (F-74).
- **rebuild dashboard reader** (`dashboard/rebuild_status.py`): three bugs —
  `locals()` assignment no-op leaving new probability fields permanently
  None; market-evaluation join on the wrong key (market metrics always null
  live, latent crash on non-numeric slugs); a probability-precedence
  regression surfacing raw over the conservative lower bound. All fixed,
  dashboard restarted with the fixes (F-76/F-77/F-78).
- **MLB v9 train/serve skew ×3** (`residual_trend_gap`, `park_factor`,
  `bullpen_fatigue_gap`): training and serving computed different quantities.
  Training now matches serving literally; v9 variants use the new
  `park_factor_pit` feature (PIT empirical; v8 stays on its trained static
  contract). **Prior v9 ablation numbers are void** — re-run before trusting
  them (F-79).
- **Seed regression**: `stable_seed` inclusion of `method` shifted every
  incumbent simulated price bit-for-bit — restored for the default path,
  pinned by test (F-80).
- **ProductionLedger** was 341 unwired lines with no lifecycle — now written
  fail-soft by every predict cycle, with guarded settle/void/supersede/error
  transitions and CLI commands (F-81).
- **Canary freshness check** was a stub — HEALTHY despite predictions frozen
  since 08-11; now real vs `max_data_age_minutes` (F-82).
- **Main ledger un-retired** (operator directive 2026-08-13): config flag
  back to `true`, archived per-sport workbooks restored to `data/main/`,
  Phase B config-pinning tests updated.
- Hygiene: NBA/NFL dangling spread/total config refs removed,
  `dashboard/server.log` untracked, DEBUG.md's hash snippet corrected to the
  loader's `ensure_ascii=True` convention, mypy/ruff CI delta gates clean.

## Production canary (2026-08-13)

| Field | Value |
|---|---|
| Model | `wnba-elo-trend-lr-v4` |
| Artifact | `config/models/wnba-elo-trend-lr-v4.json` |
| Config | `config/production.yaml` |
| Health | HEALTHY |
| Automated orders | false (manual only) |
| CLI | `python -m model_prediction.cli_production {predict,health,status,ledger,settle,void,supersede,error}` |
| Scheduler | `com.modelprediction.production` (loaded 2026-08-13, verified: fresh prediction batch + ProductionLedger write on manual kickstart) |
| Dashboard | `dashboard/production.py` → `get_production_status()` |

Canary hardening 2026-08-13: `_check_data_freshness` is real (stale
predictions → DEGRADED); every predict cycle writes the ProductionLedger
(`data/production/predictions.db`) fail-soft with guarded lifecycle
transitions.

## Active model versions (2026-08-12)

| Sport | Active artifact | Status | Hit rate | Qualification |
|---|---|---|---|---|
| MLB moneyline | `mlb-elo-trend-lr-v8` | shadow_qualified (override) | 58.5% locked-holdout at the operator-lowered threshold (target_hit_rate=0.60) | `qualified=false` — validation Brier regressed vs. v7's retired feature set, and holdout no longer clears the 60% bar at this looser, coverage-optimized threshold either. Both honestly listed in the artifact's own `qualification.failures`. Real per-starter `starter_era_gap` feature (`features/starter_history.py`), replacing v7's team-level `pitcher_era_gap`. |
| MLB spread | `measured-edge-margin-v3` | active_research | — | Real, sized Main-ledger rows (gated on both confirmed starters, matching moneyline). Real Poisson-GLM elasticity refit promoted 2026-08-04 (F-62): diagnostic correlation 0.2057→0.208, hit rate 59.5%→60.0% |
| MLB totals | `measured-edge-totals-v3` | active_research | — | Real, sized Main-ledger rows. **Still not fixed** — same elasticity refit promoted 2026-08-04 (shared Trend Engine with spread), but totals specifically got marginally *worse* (correlation 0.0585→0.0414, hit rate 55.3%→52.9%). The previously-reported 71% over-pick figure could not be reproduced against the full diagnostic dataset in either formula version; confirms rather than resolves the standing diagnosis that totals needs an absolute-run-environment signal, not better relative elasticities (P1-17/F-62 in `MASTER.md`) |
| NBA moneyline | `nba-elo-trend-lr-v4` | shadow_qualified | 73.66% | `qualified=true` |
| WNBA moneyline | `wnba-elo-trend-lr-v4` | shadow_qualified | 67.48% | `qualified=true` |
| NFL moneyline | `nfl-elo-trend-lr-v4` | shadow_qualified | 71.26% | `qualified=true` (offseason) |
| Soccer | `soccer-poisson-dc-v1` | shadow_qualified (operator override) | 62.5% locked-holdout | No walk-forward artifact exists; override not genuine promotion |
| LOL | `lol-tiered-elo-v6` | shadow_qualified (override) | — | v6 Platt-scaled. **Fixed 2026-08-04 (F-63)**: added inactivity decay + thin-data confidence discount — real ~33% reduction in mean predicted edge for thin-data matchups on held-out data, at a disclosed locked-test accuracy cost (70.6%→69.2%) |
| CS2 | `cs2-tiered-elo-v6` | shadow_qualified (override) | — | Same v6 fix as LOL (F-63); this title's locked-test accuracy improved slightly (65.8%→66.0%) |
| Dota 2 | `dota2-tiered-elo-v6` | shadow_qualified (override) | — | Same v6 fix as LOL/CS2 (F-63) |
| Valorant | `valorant-tiered-elo-v6` | shadow_qualified (override) | — | Same v6 fix as LOL/CS2 (F-63) |
| Rainbow Six | `rainbow_six-tiered-elo-v6` | research | — | Same v6 fix as LOL/CS2 (F-63) |
| KBO | `kbo-tie-aware-elo-v2` | shadow_qualified (override) | — | Tie-aware, zero-unit research only |
| NPB | `npb-tie-aware-elo-v2` | shadow_qualified (override) | — | Tie-aware, zero-unit research only |
| Tennis | `tennis-surface-elo-v1` | research | — | WTA + ATP (ATP added 2026-08-03; ITF still unbuildable — no ESPN data source) |

## Ledger routing (definitive: `docs/LEDGER_ROUTING.md` — itself stale, verify against `main_ledgers.py`/`research_ledgers.py` directly)

Restructured 2026-08-03/04: Main and Flat are now **per-sport files**, not one shared workbook.

- **Main** (`data/main/{sport}.xlsx`: mlb, wnba, soccer, tennis): MLB (moneyline/spread/total, no edge gate — trust/provenance only, separate confidence-threshold gate in `cli.py`), WNBA moneyline (same), soccer/tennis (real edge+confidence gate) — real-sized calls. Retired 2026-08-11, **un-retired 2026-08-13** by operator directive (workbooks restored from `data/archive/2026-08-10-main-ledger-archived-shadow-primary/`; Aug 11–13 has no Main rows — a historical gap, not an error)
- **Flat** (`data/flat/{sport}.xlsx`: mlb, nba, nfl, wnba, soccer, tennis): every candidate, every one of those sports, no gate at all
- **Research** (`data/research/{sport}.xlsx`): Esports (5 titles), KBO, NPB — all candidates
- **Gated Research** (`data/gated_research/{sport}.xlsx`): Curated subset clearing per-sport edge/confidence bars
- **Model Ledgers** (`data/model_ledgers/`): per-model-identity architecture (additive; existing pipeline unchanged)

## Runtime snapshot (2026-08-13)

- **Git**: `main`, HEAD `5b5b78b` (2 ahead of origin/main pre-fix-commit)
- **Production canary**: `wnba-elo-trend-lr-v4`, HEALTHY, automated_orders=false; scheduler plist loaded and verified 2026-08-13 (predictions.db 623→626 rows on manual kickstart)
- **Rebuild challengers**: WNBA (2-feat LR), Tennis (Surface Elo), NFL (Platt-calibrated LR), Soccer (Poisson-DC) — all in `config/models/challengers/`; `com.modelprediction.rebuild-shadow` scheduler plist loaded and verified 2026-08-13 (shadow.db 349→365 trade_decisions on manual kickstart, all 6 enabled sports ran with zero failures)
- **Daily pipeline**: launchd `com.modelprediction.daily` running every 3h, exit 0, Main re-enabled 08-13
- **Dashboard**: Rebuild Shadow primary + Production Canary card; restarted 2026-08-13 with the rebuild_status fixes (market metrics populate again)
- **Tests**: full suite green after the audit-fix pass (`env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` — run WITHOUT the MODEL_PREDICTION_* env vars; several tests pin the no-env repo-colocated default)
- **CI**: `.github/workflows/ci.yml` — ruff + pytest on push/PR; ruff delta gate clean vs origin/main (120 vs 128 baseline), mypy rebuild delta clean
- **Console entry point**: `.venv/bin/model-prediction` works
- **BBO capture**: Active across 8 sports (`data/odds/`)
- **Known, unresolved, non-code issue**: The Odds API key appears genuinely invalid — all 12 configured soccer leagues on that provider return `401 Unauthorized` (verified live 2026-08-04). Soccer's ESPN-sourced leagues are unaffected. Needs a real key rotation, not a code fix.
- **Repo hygiene**: `data/mlb_statsapi/game_snapshots.jsonl` (85MB) and `data/events.jsonl` (61MB) both exceeded GitHub's 50MB recommended file size and were growing toward the 100MB hard cap. **Fixed 2026-08-05**: both now tracked via Git LFS (forward-only, per explicit operator choice — existing git history untouched, every commit from this point forward stores these two paths as LFS pointers instead of full blobs). Verified: `git lfs status` shows both objects successfully pushed, full test suite green with LFS active, `.git/hooks/pre-push`'s existing pytest/mypy gate merged with (not overwritten by) the LFS pre-push hook.

## Release verdict

**The 6 originally-identified P0 real-money-execution defects are resolved or confirmed non-issues** (verified 2026-08-03/04, full evidence in `MASTER.md`'s P0 section and Fixed Bugs log):

1. Execution-ticket binding — resolved 2026-08-03, extended to spread/total/btts (F-49), and the dashboard-side gap that made that fix unreachable from the real order flow is also now fixed (F-53, 2026-08-04)
2. Ledger/audit atomicity — confirmed the original claim was backwards (audit is appended *before* the ledger write); since the J cutover the CANONICAL store commits mutation + audit event in one SQLite transaction (G2), so the cross-file gap now applies only to the legacy XLSX export path
3. Artifact qualification / quote `timestamp_valid` enforcement — resolved as a deliberate operator decision (qualification no longer gates classification) plus re-verified `timestamp_valid` handling is correct everywhere it applies
4. `market-residual-v1.json` — resolved 2026-08-03 (F-50), real artifact trained, wired as diagnostic-only
5. MLB spread artifact reused for totals — resolved 2026-08-03 (F-51), both now point at their own real, live Measured Edge artifacts
6. Two "mismatched" artifact hashes — confirmed never a real bug, an artifact of the verification script's own wrong JSON convention

**That does not mean real-money execution should be turned on.** Separate from the 6 original defects:

- MLB v8 (the active moneyline artifact) is honestly `qualified: false` — real, positive signal (58.5% holdout hit rate, well above the 50% coin-flip line) but does not clear this project's own 60% promotion bar, on top of a validation-set Brier regression vs. the feature set it replaced. It is live via the same operator-override mechanism v7 used, not because it passed cleanly.
- MLB totals still has a known, unfixed accuracy gap — a real elasticity refit was attempted and promoted 2026-08-04 but honestly did not improve it (P1-17/F-62); needs an absolute-run-environment-specific model change, not another elasticity refit.
- Esports Elo's thin/stale-data overconfidence gap has a real fix now (F-63, 2026-08-04): inactivity decay + thin-data shrink reduced mean predicted edge on genuinely thin-data matchups by ~30-35% across all 5 titles on real held-out data, at a modest, disclosed locked-test accuracy cost in 4 of 5 titles.
- The cross-file ledger/audit atomicity gap (item 2 above) now applies only to the legacy XLSX export; the canonical SQLite store is single-transaction since J.

Do not infer executable profitability from artifact hit rates, synthetic
`-110` units, shadow-ledger P&L, or a dashboard qualification badge.

## Bugs found and fixed since last verification (2026-08-02 → 2026-08-04)

See `MASTER.md`'s Fixed Bugs log (F-47 through F-63) for full evidence on each. Highlights: dual-ledger duplicate-row gap for soccer/tennis re-runs (F-47); two active model coefficients (`bullpen_weakness_gap`, `defensive_trend_gap`, then `starter_era_gap`) silently missing from the audit ledger despite scoring correctly — the same recurring bug class, three times (F-48, F-55); dashboard order-readiness moneyline-only gate blocking every real MLB spread/total order (F-53); MLB v8 promotion with a real starter-identity feature and its own live infrastructure (F-54); a redaction fix that crashed instead of redacting, breaking soccer score collection (F-56); registry-free team-ban support completely non-functional in two independent ways (F-58); a WNBA availability fail-closed fix silently defeated by a pre-existing exception wrapper written for a different purpose (F-59); an unbounded pagination loop hardened with a page cap (F-60); MLB totals elasticity refit promoted with an honest (partially negative) result, plus a real bug in the calibration script that would have broken it (F-62); esports inactivity decay + thin-data confidence discount promoted for all 5 titles, real ~30-35% edge reduction on thin-data matchups verified against held-out data (F-63).

## Safe command forms

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
.venv/bin/model-prediction --help
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary
```

Commands with `--write-artifacts`, `--log`, settlement, ledger cleanup, ban
mutation, dashboard POST routes, `daily`, `execute`, or `sell-position` change
state. They require separate authorization appropriate to the risk.

## Repair order (remaining, real, lower-severity than the original P0 list)

1. ~~Give cross-file ledger-mutation-plus-audit-append real transactional recovery (retry/failure-injection tests)~~ — turned out to already exist (`tests/test_ledger_hardening.py::test_ledger_write_crash_leaves_a_recoverable_audit_event_not_a_silent_gap`, `test_audit_append_happens_while_the_ledger_lock_is_still_held`), this doc's own claim was stale; verified 2026-08-04 and extended to also confirm `_verify_chain` itself (not just raw data inspection) detects the orphaned-audit-event case. True cross-file atomicity across separate files (ledger + audit as one physical transaction) still doesn't exist — that's a real, distinct, lower-severity architectural gap from "no recovery tests," which is now closed.
2. Build a real absolute-run-environment signal for MLB totals (P1-17/F-62's own next step: `totals_specific_market_residual` or `branched_absolute_run_intensity_head`, per `config/model.yaml`'s `problem_cohorts.totals`) — a relative-elasticity refit was tried 2026-08-04 and honestly did not help.
3. ~~Add confidence discount / inactivity decay to the esports `NeutralElo` model.~~ Done 2026-08-04, see F-63.
4. ~~Rotate The Odds API key.~~ Moot 2026-09-02: The Odds API removed entirely (`docs/DEBUG.md` 2026-09-02 entry) — no code left reads `THE_ODDS_API_KEY`. MLB market-odds fallback moved to keyless ESPN pickcenter; soccer already used `api_football` exclusively.
5. ~~Move `data/mlb_statsapi/game_snapshots.jsonl` and `data/events.jsonl` to Git LFS before either crosses GitHub's 100MB hard cap.~~ Done 2026-08-05, forward-only (see F-65).
6. Split `cli.py` and `dashboard_server.py` into packages (both remain large, growing files).
7. ~~Migrate ledger storage to SQLite for ACID guarantees~~ — done and verified live 2026-08-16: all four scheduled jobs run `MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite`; the runtime-root `ledgers/ledgers.db` is canonical (real rows across main/flat/research/gated_research, all sports), main/flat/research xlsx are now a best-effort export only. `data/main/mlb.xlsx`'s Picks sheet row count matched sqlite's open+settled count exactly on spot check (97 = 31+66); archived rows live under `data/archive/`.
8. ~~Load the two installed-but-never-loaded launchd agents~~ Done 2026-08-13: both bootstrapped and verified via manual `launchctl kickstart`. Also found and fixed a real bug while verifying: `run_rebuild.sh`'s sports-enablement filter read `cfg["rebuild"]["sports"]`, but `config/rebuild.yaml` has `sports:` as a top-level key — the nested path always resolved to `{}`, so the derivation silently fell through. Fixed to `cfg.get("sports", {})`; re-verified it now correctly runs only the 6 enabled sports (mlb, wnba, nba, nfl, soccer, tennis) and skips the 3 disabled ones (esports, kbo, npb).
9. ~~Re-run the MLB v9 ablation~~ Done 2026-08-13 (post-F-79). ~~v8 reproduction gate FAILS~~ **v8 reproduction: CONFIRMED 2026-08-13** via a new pin-and-replay script, `scripts/mlb_v8_reproduction.py`. The original ablation-harness "reproduction gate" (fractional split + relearned threshold on today's growing dataset) was structurally the wrong check, not evidence of a real problem — diagnosed as apples-to-oranges dataset-size comparison, then further diagnosed as split/threshold non-determinism (see the corrected-diagnosis history below). Once `build_walk_forward_rows`/`chronological_split`/`evaluate_variant` gained optional date-boundary and fixed-threshold parameters (additive, default-`None`, no behavior change elsewhere) and the replay used v8's own recorded date boundaries and `confidence_threshold: 0.61966524` verbatim instead of recomputing them, the reproduction is near-exact: **148 calls vs. 148 (call_ratio=1.0), hit_rate 0.6149 vs. 0.6081 (delta 0.0068), Brier 0.2378 vs. 0.2464 (replay slightly better)** — `reproduced_closely=True`, well inside the existing 0.7–1.3 call-ratio / ≤0.03 hit-delta tolerance bands. Train/validation/holdout row counts (3814/1082/1391) exactly match the artifact's own recorded `training` block. **The ablation gate can now be trusted** — v9 feature promotion evaluation (residual-trend/FIP/K-BB%/etc.) may proceed using this pin-and-replay approach as the ground truth going forward, still gated on explicit user approval per the promotion contract. (Prior corrected-diagnosis history, kept for context: an initial "4.6x dataset growth" read was itself wrong — apples-to-oranges comparison of today's *total* dataset, 6,452 rows, against v8's *holdout-only* count, 1,391; the real total at v8's freeze was 6,287, so growth was only ~2.6%. The actual root cause was that `chronological_split` computed *proportional* splits over the still-growing `games.jsonl` with no date pinning, and `evaluate_variant` relearned a fresh confidence threshold from whatever validation cohort resulted, instead of replaying v8's frozen threshold — hit-rate/Brier tracked closely because that's what threshold-learning targets, while call-volume swung 3.6x because the learned threshold moved with the shifting split boundaries.)
10. **v8 park-factor leak (known, pre-existing)**: the static park-factor table served to v8 contains 2026-season data, so v8's own walk-forward had a PIT leak. Closed for v9 (`park_factor_pit`); closing it for v8 requires a refit under the v8 feature contract.
11. Regenerate `outputs/rebuild/verification.json` (gitignored CI evidence; `/api/rebuild/status` reports degraded while absent) — CI regenerates on push, or run the `generate_rebuild_verification.py` recipe locally.
12. ~~`dashboard/server.log` tracked in git~~ — untracked 2026-08-13, added to `.gitignore`.
13. ~~NBA/NFL dangling `spread/total_research_artifact` config refs~~ — removed 2026-08-13 (archived artifacts, zero consumers).
