---
last_mapped_commit: 3aaa58949aca8dabf49593091243d6719d859452
last_mapped_at: 2026-09-20
---
# Codebase Structure

**Analysis Date:** 2026-09-20

## Directory Layout

```
model-prediction/                           ← Repository root
├── src/model_prediction/                   ← Main package (Python path: src/)
│   ├── __init__.py                          (version="0.6.0")
│   │
│   ├── cli/                                 ← Command-line interface (entry points)
│   │   ├── __main__.py                      (python -m model_prediction)
│   │   ├── main.py                          (dispatcher, re-exports commands)
│   │   ├── parser.py                        (argparse setup)
│   │   ├── commands.py                      (45KB, large command impl.)
│   │   ├── forecast.py                      (Multi-sport prediction pipeline)
│   │   ├── daily.py                         (Scheduled daily cycle coordinator)
│   │   ├── settle.py                        (Settlement matching & ledger update)
│   │   └── state.py                         (CLI state helpers)
│   │
│   ├── models/                              ← Sport-specific model implementations
│   │   ├── base.py                          (Abstract Model interface)
│   │   ├── learned_market.py                (JSON artifact model loader)
│   │   ├── registry.py                      (Model factory/registry)
│   │   │
│   │   ├── mlb.py                           (Totals/spread: MeasuredEdgeModel)
│   │   ├── mlb_v*.py                        (MLB variants v9, v10)
│   │   ├── mlb_first_inning*.py             (NRFI/YRFI models)
│   │   ├── mlb_structural_v10.py            (Monte-Carlo run-scoring)
│   │   ├── mlb_nrfi.py                      (No-run-first-inning model)
│   │   │
│   │   ├── basketball.py                    (NBA/WNBA margin models)
│   │   ├── nba_structural_v*.py
│   │   ├── wnba_structural_v*.py
│   │   ├── wnba_possession.py               (WNBA pace/possession model)
│   │   │
│   │   ├── college_football.py              (CFB spread/total)
│   │   ├── cfb_*.py                         (CFB variants)
│   │   ├── nfl_structural_v*.py             (NFL models)
│   │   │
│   │   ├── soccer.py                        (Poisson-Dixon-Coles)
│   │   ├── soccer_*.py                      (Soccer variants)
│   │   │
│   │   ├── tennis.py                        (Surface Elo)
│   │   ├── tennis_*.py                      (Tennis variants)
│   │   │
│   │   ├── esports_*.py                     (LoL/CS2/Dota2/Valorant/R6)
│   │   │
│   │   ├── intl_baseball.py                 (KBO/NPB)
│   │   ├── international_baseball_v*.py
│   │   │
│   │   └── meta_calibrator.py               (Ensemble meta-model)
│   │
│   ├── features/                            ← Feature engineering modules (51 files)
│   │   ├── base.py                          (FeatureStore abstraction)
│   │   │
│   │   ├── elo_ratings.py                   (Elo computation)
│   │   ├── trends.py                        (Momentum/trend engine)
│   │   ├── head_to_head.py                  (H2H records)
│   │   │
│   │   ├── mlb_player_availability.py       (MLB pitchers/position players)
│   │   ├── player_availability.py           (Generic availability)
│   │   ├── starter_history.py               (Pitcher stats: ERA, FIP, K-BB)
│   │   ├── starter_state.py
│   │   ├── reliever_availability.py
│   │   ├── pitch_arsenal.py
│   │   ├── pitcher_arsenal_matchup.py
│   │   ├── batter_offense.py
│   │   ├── batter_priors.py
│   │   │
│   │   ├── park_factors.py                  (Static v8 table)
│   │   ├── park_factors_pit.py              (PIT-corrected v9 dynamic)
│   │   ├── mlb_v9_features.py               (v9 specialist features)
│   │   ├── mlb_v10_features.py              (v10 features)
│   │   │
│   │   ├── bullpen.py                       (Relief pitcher profiles)
│   │   ├── bullpen_state.py
│   │   │
│   │   ├── schedule_load.py                 (Rest, back-to-back, travel)
│   │   ├── weather.py                       (Air density, wind)
│   │   ├── air_density.py                   (Weather calculations)
│   │   │
│   │   ├── lineup_strength.py               (Offensive strength)
│   │   ├── lineup_state.py
│   │   ├── projected_offense.py
│   │   │
│   │   ├── multi_horizon_tracker.py
│   │   ├── multi_horizon_health.py
│   │   ├── market_state.py
│   │   ├── line_movement.py
│   │   │
│   │   ├── elo_ratings.py                   (Elo for all sports)
│   │   ├── tennis_surface.py                (Surface Elo modulation)
│   │   │
│   │   ├── wnba_player_logs.py              (WNBA stats)
│   │   ├── wnba_boxscores.py
│   │   ├── wnba_pace_four_factors.py
│   │   ├── wnba_player_impact.py
│   │   │
│   │   ├── nfl_qb_oline.py                  (NFL QB/line stats)
│   │   ├── cfb_features.py
│   │   │
│   │   ├── umpire_state.py                  (MLB umpire tendencies)
│   │   ├── catcher_framing.py
│   │   ├── platoon_matchup.py
│   │   │
│   │   ├── yrfi_nrfi.py                     (First-inning models)
│   │   │
│   │   ├── baselines.py                     (Naive baseline priors)
│   │   ├── hypothesis_ledger.py             (Hypothesis-driven features)
│   │   └── mlb_venue_geocoding.py
│   │
│   ├── data_sources/                        ← External data integrations (23 files)
│   │   ├── espn.py                          (ESPN scoreboards, injuries)
│   │   ├── espn_probables.py                (MLB starter announcements)
│   │   ├── espn_wnba_injuries.py
│   │   │
│   │   ├── mlb_statsapi.py                  (MLB Stats API)
│   │   ├── mlb_lineups.py                   (Lineup captures)
│   │   ├── mlb_injuries.py
│   │   ├── mlb_market_odds.py               (Sportsbook lines)
│   │   │
│   │   ├── polymarket_us.py                 (Polymarket gateway)
│   │   ├── polymarket_execute.py            (Order execution, ED25519 signing)
│   │   │
│   │   ├── api_football.py                  (Soccer results)
│   │   ├── cfb_data.py                      (CFB schedule, scores)
│   │   │
│   │   ├── balldontlie.py                   (NBA/WNBA stats)
│   │   ├── tennis_sackmann.py               (Tennis historical results)
│   │   │
│   │   ├── kalshi.py                        (Kalshi stub, deferred)
│   │   ├── bet_better.py
│   │   ├── sportsdataio.py
│   │   │
│   │   ├── market_warehouse.py              (Market data caching)
│   │   └── provider_capture.py              (Data snapshot utilities)
│   │
│   ├── portfolio/                           ← Real-money execution (12 files)
│   │   ├── auto_buyer_ledger.py             (Auto-buy order tracking)
│   │   ├── auto_executor.py                 (Order execution engine)
│   │   ├── polymarket_kelly.py              (Kelly sizing for Polymarket)
│   │   ├── polymarket_ledger.py             (Order state machine)
│   │   ├── polymarket_scanner.py            (Market discovery)
│   │   ├── polymarket_dispatcher.py
│   │   ├── polymarket_combos.py
│   │   ├── polymarket_ws.py                 (WebSocket stub)
│   │   ├── kalshi_client.py
│   │   ├── manual_bet_ledger.py
│   │   ├── lead_lag.py                      (Order timing)
│   │   └── execution_rehearsal.py           (Dry-run validation)
│   │
│   ├── dashboard/                           ← Web UI backend (10 files, 48KB+ routes)
│   │   ├── routes.py                        (Flask app + all endpoints, 35KB)
│   │   ├── data_service.py                  (Data aggregation layer)
│   │   ├── evidence.py                      (Dashboard analytics)
│   │   ├── picks.py                         (Pick table logic)
│   │   ├── orders.py                        (Order execution UI)
│   │   ├── backtests.py                     (Validation runner)
│   │   ├── matrix.py                        (Performance matrix)
│   │   ├── jobs.py                          (Run scheduler UI)
│   │   ├── status.py                        (Health display)
│   │   └── common.py                        (Shared utilities)
│   │
│   ├── rebuild/                             ← Isolated shadow pipeline (47 files)
│   │   ├── __init__.py
│   │   ├── cli.py                           (Shadow CLI entry)
│   │   ├── shadow_ledger.py                 (87KB, shadow pick recording)
│   │   ├── validation.py                    (Walk-forward splits, training harness)
│   │   ├── decision.py                      (Shadow decision engine)
│   │   ├── ablation.py                      (Feature ablation/importance)
│   │   │
│   │   ├── collectors.py                    (70KB, data collection)
│   │   ├── mlb_features.py                  (53KB, MLB feature eng)
│   │   ├── mlb_shadow_pipeline.py           (Shadow-specific ML pipeline)
│   │   │
│   │   ├── identity.py                      (34KB, event matching)
│   │   ├── metadata.py                      (14KB, schema + metadata)
│   │   ├── calibration.py                   (Validation metrics)
│   │   │
│   │   ├── models/                          (Shadow model factories)
│   │   ├── mlb_v3/                          (Legacy MLB v3 rebuild)
│   │   ├── nfl/                             (NFL rebuild)
│   │   ├── soccer/                          (Soccer rebuild)
│   │   ├── tennis/                          (Tennis rebuild)
│   │   ├── wnba/                            (WNBA rebuild)
│   │   ├── providers/                       (Rebuild-specific data sources)
│   │   │
│   │   └── [many more modules: horizon_builder, ensemble, economic, etc.]
│   │
│   ├── soccer/                              ← Soccer-specific subpackage
│   │   ├── [13 files, specialized soccer pipeline]
│   │
│   ├── esports_titles/                      ← Esports league titles
│   │   ├── [10 configs, game metadata]
│   │
│   ├── [Core domain modules at package top-level]
│   ├── learned_forward.py                   (Generic sport forward model, 200+ lines)
│   ├── production_registry.py                (Model loading & validation)
│   ├── production_store.py                   (Production prediction store)
│   ├── production_canary.py                  (Canary check before predictions)
│   ├── model_lifecycle.py                    (Promotion rules, lifecycle state)
│   ├── model_promotion.py                    (CLI: promote/rollback)
│   ├── champion_challenger.py                (Paired comparison harness)
│   │
│   ├── ledger.py                             (200+KB, Pick ledger CRUD)
│   ├── runtime_ledger_store.py               (SQLite backend)
│   ├── xlsx_ledger.py                        (XLSX read/write)
│   ├── model_ledger.py                       (Per-model ledger variant)
│   ├── main_ledgers.py                       (Multi-tier ledger wrapper)
│   ├── research_ledgers.py
│   │
│   ├── audit.py                             (Audit log append-only)
│   ├── domain.py                            (Enums, dataclasses: PickRequest, etc.)
│   ├── entities.py                          (CanonicalTeam registry)
│   ├── event_identity.py                    (Game/event matching logic)
│   │
│   ├── config.py                            (Config loading + validation)
│   ├── units.py                             (Kelly sizing, exposure)
│   ├── calibration.py                       (Metrics: Brier, ECE, etc.)
│   ├── pricing.py                           (Odds conversion, edge calc)
│   │
│   ├── validation.py                        (Main validation harness)
│   ├── feature_contract.py                  (Feature schema validation)
│   ├── feature_freezer.py                   (Feature caching)
│   ├── feature_regressions.py               (Feature distribution tests)
│   │
│   ├── system_health.py                     (Health check queries)
│   ├── run_supervisor.py                    (Job scheduler)
│   ├── runtime_paths.py                     (Path resolution: repo/runtime roots)
│   │
│   ├── market_blend.py                      (Multi-model ensemble)
│   ├── market_eval.py
│   ├── market_health.py                     (Market monitoring)
│   ├── market_state_vector.py               (Market conditions snapshots)
│   │
│   ├── research_*.py                        (Research utilities, many files)
│   ├── ingest.py                            (Data ingestion CLI)
│   ├── backtester.py                        (Historical simulation)
│   ├── qualification_registry.py             (Qualification tracking)
│   │
│   ├── bans.py                              (Team ban list)
│   ├── eligibility.py                       (Pick eligibility gates)
│   ├── economic_gate.py                     (Unit sizing economics)
│   │
│   ├── [many more core modules: ~100+ at top-level]
│   └── py.typed                             (Marker: package has type hints)
│
├── config/                                   ← Configuration directory
│   ├── production.yaml                      (Model registry, champions, execution gates)
│   ├── model.yaml                           (Feature lists, artifact mappings)
│   ├── tested_features.json                 (Feature coverage inventory)
│   ├── rebuild.yaml                         (Shadow rebuild config)
│   ├── rebuild_sources.yaml                 (Rebuild data source config)
│   │
│   ├── models/                              (Frozen model artifacts)
│   │   ├── mlb-v7-moneyline.json           (Elo+trend coef, checked-in)
│   │   ├── nba-v4-moneyline.json
│   │   ├── wnba-v4-moneyline.json
│   │   ├── [~40 JSON artifacts]
│   │   └── challengers/                     (Experimental models)
│   │
│   └── research/                            (Research-tier configs)
│
├── tests/                                    ← Test suite (245 files, 2,205 tests)
│   ├── conftest.py                          (pytest fixtures, mocking)
│   ├── test_*.py                            (~100 test files)
│   │
│   ├── Test Organization by Component:
│   ├── test_mlb_*.py                        (MLB tests: v9, v10, moneyline, etc.)
│   ├── test_wnba_*.py
│   ├── test_nba_*.py
│   ├── test_tennis_*.py
│   ├── test_soccer_*.py
│   ├── test_esports_*.py
│   ├── test_cfb_*.py
│   │
│   ├── test_validation.py                   (Walk-forward, train/serve parity)
│   ├── test_market_blend.py
│   ├── test_ledger_*.py                     (Ledger operations)
│   ├── test_settlement_*.py
│   ├── test_feature_*.py
│   ├── test_production_*.py                 (Registry, promotion)
│   ├── test_dashboard_*.py
│   ├── test_portfolio_*.py
│   ├── test_run_supervisor.py
│   │
│   ├── test_calibrators.py
│   ├── test_bans.py
│   └── [many more specific tests]
│
├── scripts/                                  ← Utility scripts (159 files, mostly research/audit)
│   ├── run_daily.sh                         (Main scheduled job orchestrator)
│   ├── auto_polymarket_buyer.py             (Auto-buyer runner)
│   │
│   ├── audit_*.py                           (~15 audit scripts)
│   ├── backfill_*.py                        (~10 backfill helpers)
│   ├── correct_*.py                         (~5 correction utilities)
│   ├── check_*.py                           (~10 validation checks)
│   │
│   ├── capture_*.py                         (Market/data capture)
│   ├── build_*.py                           (Dataset building)
│   ├── mlb_*.py                             (MLB-specific scripts)
│   ├── cfb_*.py
│   ├── esports_*.py
│   │
│   ├── archive/                             (Retired scripts)
│   └── [many domain-specific helpers]
│
├── dashboard/                                ← Frontend HTML/JS (built artifact)
│   ├── [15 files, generated or static assets]
│   └── dashboard.html                       (Main UI, 278KB)
│
├── data/                                     ← Untracked operational data (git-ignored)
│   ├── ledgers/                             (SQLite canonical: ledgers.db)
│   ├── production/                          (Production predictions DB)
│   ├── runs.db                              (Job execution history)
│   │
│   ├── main/                                (Main ledger XLSX)
│   ├── flat/                                (Flat ledger XLSX)
│   ├── research/                            (Research ledger XLSX)
│   ├── gated_research/                      (Gated research ledger XLSX)
│   ├── model_ledgers/                       (Per-model ledger XLSX)
│   │
│   ├── odds/                                (Polymarket BBO snapshots, JSONL)
│   ├── historical/                          (Ingested game results, JSONL)
│   ├── providers/                           (Raw provider cache)
│   ├── features/                            (Computed feature vectors, Parquet)
│   ├── archive/                             (Settled/removed picks, git-tracked)
│   ├── snapshots/                           (Point-in-time evidence, git-tracked)
│   ├── logs/                                (Daily/supervisor logs)
│   ├── rebuild/                             (Shadow rebuild state)
│   ├── locks/                               (Lock files, e.g., daily.lock)
│   │
│   └── [many subdirs per sport and feature type]
│
├── docs/                                     ← Documentation (58 files)
│   ├── INDEX.md                             (Master index)
│   ├── PROJECT_STATUS.md                    (Current operational status)
│   ├── DEBUG.md                             (Audit history, bug fixes)
│   ├── ROADMAP.md                           (Engineering + research roadmap)
│   ├── ARCHITECTURE.md                      (Architecture contract)
│   ├── CONVENTIONS.md                       (Code style guide)
│   ├── TESTING.md                           (Test patterns)
│   ├── MASTER.md                            (Session notes, past audit)
│   │
│   ├── CHAMPION_CHALLENGER.md               (Promotion process)
│   ├── V9_RESEARCH_PLAN.md
│   ├── RESEARCH_BACKLOG.md
│   │
│   ├── rebuild/                             (Rebuild-specific docs)
│   │   ├── README.md
│   │   └── [rebuild architecture]
│   │
│   ├── archive/                             (Retired design docs)
│   └── [many domain-specific guides]
│
├── .planning/codebase/                      ← Automated codebase mapping
│   ├── STACK.md                             (Tech stack)
│   ├── INTEGRATIONS.md                      (External integrations)
│   ├── ARCHITECTURE.md                      (System architecture)
│   └── STRUCTURE.md                         (This file)
│
├── ops/launchd/                             ← macOS scheduled jobs
│   ├── com.modelprediction.daily.plist      (4x daily, 00:30/06:00/12:00/18:00 UTC)
│   ├── com.modelprediction.production.plist (Manual/dashboard trigger)
│   └── com.modelprediction.rebuild-shadow.plist
│
├── outputs/rebuild/                         ← CI-generated evidence (git-tracked)
│   └── verification.json                    (Proof of shadow isolation)
│
├── .github/workflows/                       ← CI/CD
│   ├── ci.yml                               (GitHub Actions: ruff + pytest)
│   └── [other workflows]
│
├── pyproject.toml                           ← Python project config
├── uv.lock                                  ← Dependency lockfile (uv package manager)
├── .env.example                             ← Environment template
├── .gitignore                               ← Untracked patterns (data/)
├── .pre-commit-config.yaml                  ← Pre-commit hooks
│
├── README.md                                ← Project overview
├── CLAUDE.md                                ← Working guidelines
├── GATES.md                                 ← Operational gates
├── AGENTS.md                                ← Execution rules for subagents
│
└── [metadata files: .DS_Store, .git/, .venv symlink, etc.]
```

## Directory Purposes

**`src/model_prediction/`** — All Python source code. Organized by layer:

- Entry points (`cli/`)
- Models (`models/`)
- Feature engineering (`features/`)
- Data integration (`data_sources/`)
- Execution (`portfolio/`)
- Web interface (`dashboard/`)
- Research infrastructure (`rebuild/`)
- Core domain logic (top-level modules)

**`config/`** — Frozen configuration and model artifacts

- `production.yaml` — Active model registry
- `model.yaml` — Feature metadata
- `models/*.json` — Versioned, checksummed model coefficients (never re-written by schedule)

**`tests/`** — 245 test files covering all modules. Run: `pytest tests/ -q`

**`scripts/`** — 159 utility scripts for research, audits, data backfill, corrections

**`docs/`** — Durable documentation (INDEX.md is the master)

**`data/`** — Untracked operational state (git-ignored)

- Canonical store: `ledgers/ledgers.db` (SQLite)
- XLSX projections: `main/`, `flat/`, `research/`, `gated_research/`
- Cache: `odds/`, `historical/`, `providers/`, `features/`
- Evidence: `archive/`, `snapshots/` (git-tracked)
- Logs: `logs/`

**`.planning/codebase/`** — Automated codebase documentation (this file)

## Key File Locations

**Entry Points:**

- `src/model_prediction/cli/main.py` — Command dispatcher
- `src/model_prediction/dashboard_server.py` — Flask web server
- `src/model_prediction/run_supervisor.py` — Job orchestrator

**Core Prediction Logic:**

- `src/model_prediction/learned_forward.py` — Generic sport forward model (Elo + trends)
- `src/model_prediction/models/` — Sport-specific custom models

**Feature Store:**

- `src/model_prediction/features/base.py` — FeatureStore abstraction
- `src/model_prediction/features/` — 50+ feature modules

**Ledger & State:**

- `src/model_prediction/ledger.py` — Pick ledger CRUD (200+KB)
- `src/model_prediction/runtime_ledger_store.py` — SQLite backend
- `src/model_prediction/audit.py` — Append-only audit log

**Configuration & Registry:**

- `src/model_prediction/config.py` — Config loading
- `src/model_prediction/production_registry.py` — Model registry + validation
- `config/production.yaml` — Active config

**Validation & Training:**

- `src/model_prediction/validation.py` — Walk-forward harness
- `src/model_prediction/rebuild/` — Clean-slate research pipeline

**Testing:**

- `tests/conftest.py` — pytest fixtures, mocking utilities
- `tests/test_validation.py` — Walk-forward tests, train/serve parity

**Configuration Files:**

- `pyproject.toml` — Python dependencies, tool config (ruff, mypy, pytest)
- `.env` (gitignored) — API credentials
- `.env.example` — Template

## Naming Conventions

**Files:**

- `{sport}*.py` — Sport-specific modules (e.g., `mlb_v9_features.py`, `tennis_surface.py`)
- `test_{name}.py` — Test files (located in `tests/`)
- `{action}_{thing}.py` — Utility scripts (e.g., `audit_market_coverage.py`, `backfill_weather.py`)
- `*_ledger.py` — Ledger implementations (e.g., `ledger.py`, `model_ledger.py`)
- `*_registry.py` — Registry/lookup modules (e.g., `production_registry.py`)

**Directories:**

- `src/model_prediction/{component}/` — Layered components (cli, models, features, etc.)
- `src/model_prediction/rebuild/` — Separate namespace for rebuild-only code
- `tests/` — Flat test file directory (not mirrored structure)
- `scripts/` — Flat script directory (ad-hoc utility scripts)
- `data/{category}/` — Runtime data organized by type (ledgers, odds, historical, etc.)
- `config/` — Static configuration (not `configs/` or `configuration/`)
- `docs/` — Durable documentation

**Python Functions/Classes:**

- `PascalCase` — Classes (e.g., `PickRequest`, `FeatureStore`, `ProductionRegistry`)
- `snake_case` — Functions and variables (e.g., `edge_scaled_units()`, `parse_utc()`)
- `UPPERCASE_SNAKE_CASE` — Constants (e.g., `UNIT_MIN_EDGE`, `SIGNIFICANCE_THRESHOLD`)

## Where to Add New Code

**New Sport / Model:**

- Model implementation: `src/model_prediction/models/{sport}.py` or `models/{sport}_{variant}.py`
- Features: `src/model_prediction/features/{sport}_*.py` (domain-specific)
- Tests: `tests/test_{sport}_*.py`
- Data source (if needed): `src/model_prediction/data_sources/{sport}_*.py`

**New Feature (Existing Sport):**

- Feature module: `src/model_prediction/features/{feature_name}.py`
- Wire into `learned_forward.py::_FEATURE_PROVIDERS` dict if generic
- Test: `tests/test_feature_*.py`
- Mark as research (inactive in production) until promotion gate passes

**Validation/Backtest Code:**

- Core validation harness: `src/model_prediction/validation.py` (walk-forward splits)
- Rebuild-specific: `src/model_prediction/rebuild/validation.py` (shadow variants)
- Tests: `tests/test_validation.py`

**Utility / Research Script:**

- Location: `scripts/{purpose}_{thing}.py`
- Runnable standalone: `python scripts/audit_thing.py --arg ...`
- No required import into main package

**Dashboard Endpoint:**

- New route: `src/model_prediction/dashboard/routes.py` (single file for all Flask endpoints, 35KB)
- New data service: `src/model_prediction/dashboard/data_service.py`
- Test: `tests/test_dashboard_*.py`

**Real-Money Execution Feature:**

- Ledger/order tracking: `src/model_prediction/portfolio/{exchange}_ledger.py`
- Execution logic: `src/model_prediction/portfolio/{exchange}_executor.py`
- Gating: Ensure feature is gated behind `execution_gate` in `config/production.yaml`

## Special Directories

**`src/model_prediction/rebuild/`:**

- Purpose: Clean-slate research pipeline (no production writes, shadow-only)
- Generated: No (hand-maintained codebase)
- Committed: Yes
- Isolation: Separate CLI entry point, separate model registry, no cross-module imports to incumbent
- Evidence output: `outputs/rebuild/` (git-tracked) for CI proof

**`data/`:**

- Purpose: Untracked operational state (ledgers, cache, logs)
- Generated: Entirely runtime (never commit, except `archive/` and `snapshots/`)
- Committed: Partial (`data/archive/`, `data/snapshots/` are git-tracked evidence)
- Lifecycle: Disposable except evidence; safe to rm -rf and rebuild on next run

**`config/models/`:**

- Purpose: Frozen, versioned model artifacts (JSON)
- Generated: No (only written by promotion commands, not schedule)
- Committed: Yes
- Note: `challengers/` subdirectory for experimental models (also committed)

**`outputs/rebuild/`:**

- Purpose: CI-generated proof of shadow isolation
- Generated: Yes (only by GitHub Actions workflow)
- Committed: Yes (evidence only)
- Local: May be 404 in developer checkout; not critical

**`.planning/codebase/`:**

- Purpose: Automated codebase mapping (this documentation)
- Generated: Yes (by `/gsd-map-codebase` CLI)
- Committed: Yes (for team reference)
- Refresh: `python /gsd-map-codebase --paths ...` or via skill

## File Size Reference

| Module | Size | Purpose |
|--------|------|---------|
| `ledger.py` | 200+KB | Pick ledger CRUD, atomicity, audit |
| `routes.py` | 35KB | Flask endpoints for dashboard |
| `orders.py` | 48KB | Order execution UI logic |
| `evidence.py` | 48KB | Performance analytics |
| `collectors.py` | 70KB | Data collection (rebuild) |
| `shadow_ledger.py` | 87KB | Shadow pick recording |
| `mlb_features.py` | 53KB | MLB feature engineering (rebuild) |
| `mlb_shadow_pipeline.py` | 53KB | Shadow-specific pipeline |
| `polymarket_execute.py` | 43KB | Order execution + signing |
| `polymarket_us.py` | 36KB | Market data + gateway client |
| `cfb_data.py` | 49KB | CFB schedule/scores |
| `forecast.py` | 50+KB | Multi-sport prediction orchestration |
| `identity.py` | 34KB | Event/game matching |
| Most models | 5-20KB | Per-sport logistic/structural models |
| Most features | 2-10KB | Single feature computation |
| Most tests | 2-10KB | One test file per module/concern |

---

*Structure analysis: 2026-09-20*
