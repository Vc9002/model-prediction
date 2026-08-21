# Changelog

## 2026-08-20 — MLB YRFI/NRFI, WNBA Four Factors, Cross-Market Consistency, Meta-Calibrator & Lineup Wake Planner Daemon

- **MLB YRFI / NRFI (Yes/No Run First Inning) Predictive Engine**:
  - Implemented point-in-time feature calculation in `src/model_prediction/features/yrfi_nrfi.py`: starter 1st-inning ERA/FIP/WHIP with empirical Bayes credibility shrinkage, Top-3 batting lineup offensive profile, ballpark run environment, and independent half-inning Poisson $\lambda_{\text{top}}, \lambda_{\text{bot}}$ expectations.
  - Implemented `MLBNRFIModel` in `src/model_prediction/models/mlb_nrfi.py`: hybrid decomposed Poisson and logistic probability model with fair odds and EV edge calculation.
  - Empirical walk-forward holdout research backtest in `scripts/mlb_nrfi_research.py` across 6,610 games: **Holdout Brier improved from 0.25113 to 0.24844 ($\Delta\text{Brier} = -0.00268$)** and **Log Loss from 0.69541 to 0.69007 ($\Delta\text{LL} = -0.00534$)**.
  - Unit test suite in `tests/test_mlb_nrfi.py` (5 tests passing).
- **WNBA Pace & Four Factors Modeling**:
  - Implemented `src/model_prediction/features/wnba_pace_four_factors.py`: Dean Oliver's Four Factors normalized for WNBA 40-minute regulation ($eFG\%$, $TOV\%$, $OREB\%$, $FTR$) with Empirical Bayes shrinkage ($n_{\text{prior}}=8$).
  - Implemented `project_wnba_game_total()` computing pace-adjusted combined score projections and team ratings.
  - Unit test suite in `tests/test_wnba_four_factors.py` (3 tests passing).
- **Cross-Market Internal Consistency Engine**:
  - Implemented `src/model_prediction/cross_market_consistency.py`: validates probabilistic monotonicity ($P(\text{Cover } -1.5) \le P(\text{Moneyline Win})$) and complementarity ($P(\text{Over}) + P(\text{Under}) = 1.0$) across betting slates.
  - Unit test suite in `tests/test_cross_market_consistency.py` (3 tests passing).
- **Multi-Sport Shared Meta-Calibrator**:
  - Implemented `src/model_prediction/meta_calibrator.py`: `SharedMetaCalibrator` with multi-sport Platt scaling and Isotonic Regression for tail calibration.
  - Unit test suite in `tests/test_meta_calibrator.py` (2 tests passing).
- **Dashboard Observability & Health APIs**:
  - Added `/api/clv` rolling 30-day closing-line value time series and beat rate calculation in `src/model_prediction/dashboard/status.py` and `routes.py`.
  - Added `/api/capture_health` 7-day BBO prospective snapshot coverage reporting.
  - Unit test suite in `tests/test_dashboard_clv_and_capture.py` (2 tests passing).
- **Lineup Wake Root LaunchDaemon**:
  - Created `ops/launchd/com.vc.mlb-lineup-wake-planner.plist` running `scripts/plan_lineup_wakes.py --apply` to schedule one-time `pmset` wake events ~35m before first pitch, closing the overnight sleep acquisition gap.
- **Operator Push Notifications**:
  - Implemented `notify_operator()` in `src/model_prediction/run_supervisor.py` supporting macOS Notification Center alerts via `osascript` and Slack webhooks.
- **Filesystem & Type Hygiene**:
  - Purged 11 orphaned/broken worktrees from `$HOME`.
  - Added `src/model_prediction/py.typed` marker file and `pyproject.toml` mypy overrides.
  - Verified 0 Ruff findings and **1,938 passing tests** (3 skipped, 0 failing).

- Added `src/model_prediction/champion_challenger.py`: `ProductionRegistry`
  freeze + tamper detection, `FrozenProductionStore`, `PairedComparison`
  (ΔLogLoss/ΔBrier/ΔECE with date-cluster bootstrap), `PromotionVerdict`, and
  settled-picks loader (`load_settled_predictions`,
  `settled_champion_calibration`). CLI: `freeze-production`, `compare-champion`.
  Frozen 13 production champions to `data/production/frozen_champions.json`.
- MLB v9 Phase 1: wired `starter_kbb_gap`, `residual_trend_gap`,
  `bullpen_fatigue_gap` through `validation.py` / `learned_forward.py` /
  `features/starter_history.py`; runner `scripts/mlb_v9_ablation.py`. Ablation:
  residual-trend variant wins (+56.4u vs +43.1u raw trend).
- MLB v9 Phase 2: `park_factor_at()` PIT-correct park factors
  (`features/park_factors.py`), wired into walk-forward validation.
- MLB distribution methods: `simulate_game` gains
  `gamma_poisson`/`negative_binomial`/`independent_poisson`; new
  `compare_distribution_methods()` prices ML/spread/total from one coherent
  joint draw; wired through `MeasuredEdgeMarginModel`/`MeasuredEdgeTotalsModel`.
  NB is the first serious challenger (runnable, not yet promoted).
- WNBA spread fix: `wnba-spread-baseline-v1` predicted moneyline not spread
  (never used the line); replaced with `wnba-spread-margin-v1`
  (`P(away_cover)=Φ(line; margin, 10.5)`). `config/model.yaml` spread/total
  refs corrected.
- Settlement routing fix: model-ledger mirror now targets canonical
  `data/model_ledgers/` (threaded `model_ledgers_dir` through `PickLedger`,
  `main_ledgers.py`, `research_ledgers.py`); previously per-tier subdirs
  diverged from the dashboard/loader's canonical read. See
  `docs/archive/SETTLEMENT_GAP.md`.
- Cleanup: 38 obsolete files removed (12 `*.previous.json`, retired config
  models, 4 dead rebuild models). Config root 63 → 27 files.
- Docs: `docs/CHAMPION_CHALLENGER.md`, `docs/SETTLEMENT_GAP.md` added (now archived);
  `CLAUDE.md` updated.

## 2026-07-26 — full DEBUG audit and documentation truth reset

- Ran the current `DEBUG.md` health, integrity, runtime, lint, source, pipeline,
  model, data-source, and test audit without applying code/config/model/ledger
  fixes or executing orders.
- Recorded 410 passing and 4 failing tests, 117 Ruff findings, 31 valid and 2
  mismatched artifact hashes, and an intact 16,387-event audit chain with
  unresolved historical ledger/audit reconciliation.
- Verified the editable install, console entry point, critical imports, CLI
  summary, live dashboard health/status/matrix APIs, and a no-log MLB dry
  forecast.
- Replaced the stale DEBUG baseline. The old guide incorrectly claimed 322
  green tests, no artifact hashes, no KBO/NPB settlement, zero tests for
  multiple critical modules, indefinite file locks, CWD-dependent registry
  config, and a duplicate `_sigmoid`.
- Documented new P0 blockers: execution tickets are not bound to the exact
  qualified ledger row, ledger mutation is not atomic with audit append, MLB
  probable-starter validation is not point-in-time, WNBA availability can fail
  open, and artifact qualification/quote timestamp validity are not enforced
  at the first classification/pricing step.
- Updated README, project status, architecture, execution protocol, and repair
  queue to distinguish predictor-quality metrics from executable economics and
  to keep the checkout explicitly non-release-ready.

## 2026-07-20 — esports/KBO/NPB unit tracking, confidence-gate fix, BBO collection

- Fixed a no-op confidence-gate selector in `esports.py`: threshold selection
  picked whichever gate had the most observations, which always resolved to
  the loosest threshold (0.0) and never actually gated anything. Now selects
  by the diagnostic `units_at_minus_110` result on validation, verified to
  hold on the untouched locked test. Commit `51899ea`.
- Added `units_at_minus_110` reporting to `_metrics()` in both `esports.py`
  and `international_baseball.py` (KBO/NPB), so validation/holdout
  profitability is visible directly. KBO/NPB treat ties as a push (0 P&L),
  not a loss.
- Investigated why LoL/CS2 diagnostic numbers looked inflated: partly the
  gate no-op above, and more fundamentally real esports contract lines are
  skewed (70/30, 60/40) rather than flat `-110`, so the flat-stake diagnostic
  overstates edge. KBO/NPB lines are comparatively even, so the diagnostic is
  a closer (but still not executable) proxy there.
- Started real per-side moneyline BBO capture for esports, KBO, and NPB
  (`BBO_CAPTURE_SPORTS` in `data_sources/polymarket_us.py` now covers all
  7 sports: mlb, nba, wnba, nfl, esports, kbo, npb). Snapshots land in
  `data/odds/<sport>/<date>/`.
- Reviewed a concurrent roadmap-challenger factorial experiment
  (`src/model_prediction/roadmap_challenger.py`,
  `outputs/roadmap_challenger/ROADMAP_CHALLENGER_DECISION_DOSSIER.md`): 0 of
  64 tested feature combinations across MLB/NBA/WNBA/NFL clear the full
  statistical screen. `schedule_available` is structurally degenerate
  (near-constant). No production model or config was changed by that
  experiment.

## 2026-07-20 — documentation truth reset

- Added `docs/PROJECT_STATUS.md` as the current source-of-truth entry point.
- Removed stale production tables and replaced them with verified report-level status plus explicit release blockers.
- Recorded the current test, Ruff, artifact-hash, audit-chain, packaging, and working-tree results without claiming a clean release. The suite moved from one failure to green during review because another writer changed the MLB expectation; the artifact inconsistency remains documented.
- Standardized examples on `env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli` while the installed console entry point is broken.
- Reframed model accuracy, diagnostic `-110` units, and executable profitability as separate claims.
- Documented the real-money CLI/dashboard surfaces and retained shadow/zero-unit defaults.
- Replaced the old final-status TODO with an integrity-first repair queue.

## 2026-07-17 (later — bug sweep + hardening)

- Fixed audit hash serialization: wrote event hashes with compact `separators=(",", ":")` matching computation, so chain is verifiable from JSONL alone.
- Fixed Polymarket executor: `token_id` now resolved from market slug via `client.get_token_id()` instead of passing the slug directly.
- Fixed empty `observed_at_utc=""` crashing `parse_utc()` in eligibility staleness check — added `.strip()` guard.
- Fixed `bans.py` `_entries()` catching `EntityResolutionError` so a stale ban config entry no longer blocks the entire CLI.
- Fixed `FixedPlattCalibrator.transform()` accepting `[0, 1]` boundary values (was `(0, 1)` strict, now auto-clips like `TrainablePlattCalibrator`).
- Fixed config drift: `maximum_data_age_hours` and `maximum_unreviewed_disagreement` now flow from `config/model.yaml` through the forecast path to `evaluate_eligibility`.
- Fixed float comparison in `evaluate_locked_holdout`: added `1e-12` epsilon so exactly-60% models don't fail on rounding.
- Fixed `normalize_no_vig` rejecting probabilities `>= 1` (was only checking `<= 0`).
- Fixed CLI dispatch fallthrough: replaced `else:` with explicit `elif args.command == "review-loss":` and final `else: raise ValueError(...)`.
- Added 256-entry LRU cap to `ESPNClient` and `ESPNMLBClient` `_get` response caches.
- Added `handle.flush()` to `MarketOddsSnapshotStore.append()` for crash resilience.
- Added Polymarket US odds summary to `daily` command output (`bbo_capture` metadata + per-sport snapshot counts).
- Fixed 4 stale docstrings in model files (nba/wnba/nfl/basketball "research state" → shadow-qualified).
- Fixed stale event count in TODO.md scan record (852 → 865).
- Ledger cleared and re-initialized; backup preserved at `data/.backup_2026-07-17/`.
- Removed redundant `input/INPUT.md` (content now in `input/TODO.md` + `input/PROMPT.md`).
- Updated root `README.md` with qualified models table and accurate daily-flow description.
- 120/120 tests pass, ruff clean.

## 2026-07-17

- Corrected monthly qualification: complete months with 10 or more called picks are binding; complete months below 10 calls are insufficient; an incomplete terminal month is provisional.
- Added 9-call, 10-call, losing-complete-month, and incomplete-terminal-month regression tests.
- Generated and activated hash-verified v2 artifacts for MLB, NBA, WNBA, and NFL while preserving all v1 files.
- Requalified WNBA at 65.98%, 97 calls, +25.18U. July 2026 has 27 calls and is provisional because the month is incomplete—not because it is below 10.
- Qualified NFL at the reproducible 60.55%, 109 calls, +17.00U. February has 2 calls and is non-binding.
- Tested a learned trailing-30-day adaptive MLB home-field feature. It fell from 60.87%/92/+14.91U to 60.42%/96/+14.73U and was rejected.
- Proved confidence-gap gating is exactly `2 * max_probability - 1`; it cannot change ordering or create away selections at an equivalent threshold and was rejected as redundant.
- Audited MLB pitcher data: 4,325 of 4,785 raw events contain both starters and ERA, but retrospective caches are not point-in-time valid. Pitcher training was blocked to prevent leakage.
- Added explicit multi-market readiness reporting. NBA/WNBA spreads and totals lack contract lines; MLB reconstructed full-game lines are timestamp-invalid; F5 and YRFI/NRFI lack required point-in-time inputs.
- Corrected DEBUG checks for canonical audit-chain hash, active nested artifact schema, artifact-derived thresholds, intentional rollback references, and Eastern-time MLB season filtering.
- Wrote the final evidence report to `outputs/latest/learned-model-validation-v2.json`.
