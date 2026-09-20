---
last_mapped_commit: 3aaa58949aca8dabf49593091243d6719d859452
last_mapped_at: 2026-09-20
---
<!-- refreshed: 2026-09-20 -->

# Architecture

**Analysis Date:** 2026-09-20

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                          PRODUCTION ENTRY POINTS                              │
│  Daily Scheduler | Dashboard Web UI | Manual CLI | Run Supervisor            │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
                 ┌─────────────┼─────────────┐
                 ▼             ▼             ▼
         ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
         │   Forecast   │  │ Settlement   │  │ Real-Money   │
         │   Pipeline   │  │ & Ingestion  │  │ Execution    │
         │  cli/daily   │  │ cli/settle   │  │ (Polymarket) │
         └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
                │                  │                 │
                └──────────────────┼─────────────────┘
                                   ▼
        ┌────────────────────────────────────────────────────────────┐
        │              CORE PREDICTION ENGINE                         │
        │                                                             │
        │  ┌─────────────────────────────────────────────────────┐  │
        │  │ learned_forward.py — Point-in-time feature comp.  │  │
        │  │ + logistic regression serving (v7/v4 artifacts)    │  │
        │  └──────────────────┬──────────────────────────────────┘  │
        │                     │                                      │
        │  ┌──────────────────┴──────────────────────────────────┐  │
        │  │                                                      │  │
        │  ▼                                 ▼                    │  │
        │  Per-Sport Generic Models      Per-Sport Custom      │  │
        │  (Elo + Trends):               (Domain-Specific):    │  │
        │  - Baseball (learned_forward)  - MLB totals/spreads  │  │
        │  - Basketball/WNBA             - Soccer (Dixon-Coles)│  │
        │  - Football                    - Tennis (Elo+Surface)│  │
        │  - Soccer moneyline            - Esports (series Elo)│  │
        │  - Tennis baseline             - CFB (structural)    │  │
        │  - Esports (series Elo)        - Intl Baseball       │  │
        │                                                      │  │
        └──────────────────────────────────────────────────────┘
                 │                                     │
                 │            ┌──────────────────────┤
                 │            │                      │
                 ▼            ▼                      ▼
        ┌─────────────────┐  ┌─────────────────────────────────┐
        │  FEATURE STORE  │  │   PRODUCTION REGISTRY & STATE   │
        │                 │  │                                 │
        │ features/       │  │ production_registry.py          │
        │ - mlb_*         │  │ production_store.py             │
        │ - player_avail  │  │ model_ledger.py                 │
        │ - trends        │  │ production_canary.py            │
        │ - park_factors  │  │                                 │
        │ - schedule      │  │ Tracks:                         │
        │ - elo_ratings   │  │ - Champion/Challenger models    │
        │ - weather       │  │ - Serving status per model      │
        │ - etc           │  │ - Evidence status (HISTORICAL..)│
        └─────────────────┘  │ - Locked promotions             │
                             └─────────────────────────────────┘
                                     │
                ┌────────────────────┼────────────────────┐
                │                    │                    │
                ▼                    ▼                    ▼
        ┌─────────────────┐  ┌──────────────────┐  ┌──────────────┐
        │  DATA SOURCES   │  │  LEDGER TIERS    │  │  VALIDATION  │
        │                 │  │                  │  │  & BACKTEST  │
        │ data_sources/   │  │ - Main (MLB/WNBA)│  │              │
        │ - espn.py       │  │   (promoted live)│  │ - validation │
        │ - polymarket_us │  │ - Flat (all)     │  │ - rebuild/   │
        │ - mlb_statsapi  │  │ - Research       │  │ - ablation   │
        │ - mlb_lineups   │  │ - Gated Research │  │ - walkfwd    │
        │ - api_football  │  │ (esports only)   │  │              │
        │ - tennis_etc    │  │                  │  └──────────────┘
        │ - balldontlie   │  │ All canonical in │
        │                 │  │ sqlite + XLSX    │
        └─────────────────┘  │ export (fail-soft)
                             └──────────────────┘
                                     │
                                     ▼
                         ┌────────────────────────┐
                         │  RUNTIME STATE & LOGS  │
                         │                        │
                         │ SQLite DBs:            │
                         │ - ledgers.db (picks)   │
                         │ - runs.db (jobs)       │
                         │ - production.db        │
                         │                        │
                         │ Files:                 │
                         │ - audit.json           │
                         │ - daily_*.log          │
                         │ - supervisor/          │
                         └────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| CLI Entrypoints | Parse args, dispatch to domain | `src/model_prediction/cli/` |
| Forecast Pipeline | Daily batch: ingest scores, fetch features, predict, settle | `cli/daily.py`, `cli/forecast.py` |
| Settlement Engine | Match game outcomes to picks, grade P&L, record audit | `cli/settle.py` |
| Feature Computation | Point-in-time feature vectors per game/matchup | `features/`, `learned_forward.py` |
| Ledger Persistence | SQLite canonical + XLSX export (disposable) | `ledger.py`, `runtime_ledger_store.py` |
| Model Registry | Load config, validate contracts, resolve artifacts, track champions | `production_registry.py` |
| Data Ingestion | Fetch/normalize from ESPN, Polymarket, API-Football, etc. | `data_sources/` |
| Dashboard Server | HTTP service: picks, orders, portfolio, health | `dashboard_server.py`, `dashboard/` |
| Rebuild (Shadow) | Isolated research pipeline, no production write | `rebuild/` |
| Real-Money Execution | Order submission to Polymarket (gated, explicit approval) | `portfolio/`, `polymarket_execute.py` |
| Scheduler/Supervisor | Job execution, lease management, health check | `run_supervisor.py`, `system_health.py` |

## Pattern Overview

**Overall:** Point-in-time lookup system — every decision contains only information observed at or before the decision time. Regret-free audit trail: all mutations logged, no silent failures.

**Key Characteristics:**

- **Shadow-first**: New models and sports start as research (zero units) until promotion gate passes
- **Separation of concerns**: Forecast logic (prediction) decoupled from execution logic (order submission)
- **Fail-closed validation**: Missing data → explicit NO_CALL reason codes, never guesses
- **Audit-first mutations**: Append to audit log BEFORE writing ledger (idempotent recovery)
- **Champion/Challenger architecture**: Exactly one serving model per sport/market; promotion is explicit, tracked, reversible

## Layers

**CLI (Entry Point):**

- Purpose: User-facing commands for forecast, settle, backtest, etc.
- Location: `src/model_prediction/cli/`
- Contains: `main.py` (dispatcher), `forecast.py`, `settle.py`, `daily.py`, `commands.py`
- Depends on: All domain modules below
- Used by: launchd jobs, dashboard, manual operators

**Forecast & Prediction:**

- Purpose: Compute predictions on a schedule or on-demand
- Location: `src/model_prediction/learned_forward.py` (generic sports), `models/` (custom per-sport)
- Contains: Feature composition, model loading, point-in-time probability inference
- Depends on: Feature store, data sources, production registry
- Used by: Daily pipeline, manual forecast CLI, dashboard

**Feature Engineering:**

- Purpose: Compute time-series and matchup features (Elo, park factors, pitcher ERA gaps, etc.)
- Location: `src/model_prediction/features/`
- Contains: 50+ modules, each a domain (player availability, trends, ballpark conditions, etc.)
- Depends on: Data sources, historical game records
- Used by: Forecast engine, validation/walkforward, backtest

**Data Ingestion & Sources:**

- Purpose: Fetch, normalize, and cache external data (ESPN, Polymarket, API-Football, etc.)
- Location: `src/model_prediction/data_sources/`
- Contains: One module per provider; fail-soft on network errors, cache-first where applicable
- Depends on: httpx for HTTP, parsing (json, yaml)
- Used by: Features, validation, settlement, market data refresh

**Ledger & State Persistence:**

- Purpose: Record every pick, settlement, void, and audit event; canonical truth store
- Location: `src/model_prediction/ledger.py`, `runtime_ledger_store.py`, `model_ledger.py`
- Contains: SQLite canonical store, XLSX fallback export, audit chain, pick manipulation
- Depends on: sqlite3 (stdlib), openpyxl (Excel write)
- Used by: All tiers (Main, Flat, Research, Gated Research), dashboard, reports

**Production Registry & Configuration:**

- Purpose: Load and validate production model configs; track champion/challenger; enforce governance
- Location: `src/model_prediction/production_registry.py`, `config.py`
- Contains: Config parsing, artifact resolution, status tracking, lifecycle validation
- Depends on: YAML files (`config/production.yaml`, `config/model.yaml`)
- Used by: Forecast, settlement, dashboard, CLI commands

**Rebuild (Isolated Research):**

- Purpose: Clean-slate research pipeline for model experimentation; shadow-only, no production write
- Location: `src/model_prediction/rebuild/`
- Contains: Separate feature computation, decision engine, validation harness
- Depends on: Data sources (shared), DuckDB (for high-perf analytics)
- Used by: Research teams, scheduled shadow jobs, offline ablations

**Dashboard & Web:**

- Purpose: Real-time monitoring, pick review, order execution surface (for qualified users)
- Location: `src/model_prediction/dashboard/`, `dashboard_server.py`
- Contains: Flask routes, data aggregation, portfolio logic, token-based auth
- Depends on: Ledger, production registry, system health
- Used by: Operators, analysts, real-money execution

**Portfolio & Execution:**

- Purpose: Real-money order submission, position tracking, Polymarket integration
- Location: `src/model_prediction/portfolio/`
- Contains: Auto-buyer, execution rehearsal, Polymarket Kelly sizing, order ledgers
- Depends on: `polymarket_execute.py`, ledger for audit
- Used by: Dashboard order endpoint, scheduled auto-buyer

**Scheduler & Supervision:**

- Purpose: Orchestrate daily cycles, manage job leases, detect health degradation
- Location: `src/model_prediction/run_supervisor.py`, `system_health.py`
- Contains: Lock-based concurrency, job lifecycle (run, heartbeat, exit), health queries
- Depends on: SQLite `runs.db`, fcntl (file locking)
- Used by: launchd, dashboard "run now" button, CI/monitoring

## Data Flow

### Primary Request Path (Daily Forecast)

1. **Scheduler triggers** → launchd fires `com.modelprediction.daily` 4x/day (`run_supervisor.py`)
2. **Lock acquired** → `data/locks/daily.lock` via fcntl (prevents concurrent runs; busy waits 30s)
3. **Ingest scores** (`cli/daily.py` → `cli/settle.py`) — fetch ESPN/Polymarket game results, apply settlements to previous picks
4. **Build feature vectors** (`cli/forecast.py` → `learned_forward.py`) — per sport:
   - Load champion model from `config/production.yaml`
   - Fetch features (Elo, trends, park factors, etc.) via `features/` modules
   - Perform point-in-time lookup: only use data observed ≤ decision time
5. **Predict & score** — run model (artifact or code-backed) → probability + edge
6. **Unit sizing** (`units.py::edge_scaled_units`) — apply Kelly fraction, min/max caps
7. **Ledger append** — wrap prediction in `PickRequest`, commit to canonical SQLite (and audit log)
8. **XLSX export** — deferred batched rebuild via `defer_sqlite_xlsx_exports()` context
9. **Real-money gate** — check execution gate (`production.yaml::fallback_action` + policy)
10. **Qualified picks → Dashboard** → awaiting manual review or auto-buyer trigger
11. **Daily log** → `data/logs/daily_<date>.log` + run record in `runs.db`

### Settlement Path

1. **CLI `settle --sport --date`** or **automatic in daily cycle**
2. **Fetch official scores** — ESPN (most sports), API-Football (soccer), Polymarket (prediction markets)
3. **Match picks to game outcomes** — via `event_identity.py` (sport/date/team/side matching)
4. **Compute P&L** — `_settlement_pnl()`: entry_probability/odds vs. actual result
5. **Grade classification** — WIN/LOSS/DRAW/VOID, loss reason (NO_CALL, DISQUALIFIED, etc.)
6. **Ledger update** — `PickLedger.settle()` → audit log, SQLite, XLSX export
7. **Model ledger mirror** — `model_ledger.py` mirrors settlement to per-model `.xlsx` file
8. **Dashboard refresh** — settled picks appear in portfolio P&L view

### Walk-Forward Validation Path (Training)

1. **Split data** (`validation.py::chronological_split`) — 60% train, 20% validation, 20% locked holdout
2. **Fit on train** — feature extraction + model training (never touch validation/holdout data)
3. **Select thresholds on validation** — edge threshold, unit sizing parameters
4. **Report on locked holdout** — Brier, calibration, P&L simulation; final evidence
5. **Promotion gate** — must beat incumbent and pass locked holdout check before `status: shadow_qualified`
6. **Evidence stored** → `outputs/rebuild/` (git-tracked) or `data/rebuild/` (shadow-only)

### Rebuild (Shadow) Isolation

1. **Separate invocation** (`rebuild-shadow --sport ...`) with own model registry
2. **DuckDB analytics** — high-perf feature computation over entire historical dataset
3. **No production write** — research ledgers only, no Main/Flat mutations
4. **No execution gate** — predictions never reach real-money endpoint
5. **Output** → `data/rebuild/` (untracked, disposable) for offline analysis
6. **CI check** → `outputs/rebuild/verification.json` proves shadow and incumbent don't collide

## State Management

**Canonical Ledger (SQLite):**

- Location: `data/ledgers/ledgers.db` or runtime root equivalent
- Schema: `ledger_records` table (pick_id, decision_time_utc, sport, market_type, horizon, status, etc.)
- Mutations: append-only. Updates via `UPDATE` on settled/voided/archived rows (idempotent)
- Audit: Separate `audit.json` append-only file (atomicity: audit-first, then ledger write)

**Mutable Operational State (SQLite):**

- `data/runs.db` — Job execution history (run_id, lease, heartbeat, exit_code)
- `data/production/production.db` — Production predictions snapshot (via `ProductionPredictionStore`)
- `data/rebuild/metadata.db` — Shadow job tracking, feature cache stats

**Immutable Evidence (Git-tracked):**

- `outputs/rebuild/verification.json` — CI-generated proof of shadow isolation
- `data/archive/` — Settled and removed picks (never deleted)
- `data/snapshots/` — Point-in-time evidence backups

**Configuration (YAML):**

- `config/production.yaml` — Model registry, champion selections, execution gates
- `config/model.yaml` — Feature lists, artifact mappings
- Loaded fresh on every entry point (no process-level caching except in tests)

## Key Abstractions

**PickRequest / PickResult:**

- Purpose: Immutable decision record (inputs and outputs of one prediction)
- Examples: `src/model_prediction/domain.py`
- Pattern: Dataclass with validation; used throughout ledger and audit chain

**FeatureStore:**

- Purpose: Lazy, point-in-time feature computation per game
- Examples: `features/base.py`, every module in `features/`
- Pattern: `games_before(cutoff_date)` returns only games strictly before decision time; raises on missing data

**ModelArtifact / LearnedMarketArtifact:**

- Purpose: Versioned, checksum-validated prediction model definition
- Examples: `models/learned_market.py`
- Pattern: JSON payload with embedded coefficient/tree definitions, loaded and cached at startup

**ProductionRegistry:**

- Purpose: Resolved view of all in-production models
- Examples: `production_registry.py`
- Pattern: Singleton-style, validate on load, fail-closed per model, `champion(sport, market)` lookup

**LedgerMutation / RuntimeLedgerStore:**

- Purpose: Atomic, idempotent ledger operations with WAL recovery
- Examples: `runtime_ledger_store.py`
- Pattern: Transaction-like interface (begin, add rows, commit), replayable from audit log

## Entry Points

**Scheduled (launchd):**

- `com.modelprediction.daily` (4x/day) → `run_supervisor run daily` → `cli/daily.py --date ...`
- `com.modelprediction.production` (manual trigger) → `run_supervisor run production` → `cli_production.py`
- `com.modelprediction.rebuild-shadow` (research job) → `run_supervisor run rebuild-shadow`

**Manual CLI:**

- `python -m model_prediction forecast --sport MLB --date 2026-09-20`
- `python -m model_prediction settle --sport MLB --date 2026-09-20`
- `python -m model_prediction validate --athlete --sport ...` (backtest)

**Dashboard Web:**

- `python dashboard_server.py` → Flask, port 8765
- Routes: `/picks`, `/orders`, `/portfolio`, `/execute` (real-money, token-gated)

**Rebuild Research:**

- `python -m model_prediction.rebuild cli run ...` (separate entry point, no production writes)

## Architectural Constraints

- **Threading:** Single-threaded main flow (no asyncio at the process level). Forecast fans out via `ThreadPoolExecutor` for per-sport predictions in `cli/forecast.py`.
- **Global state:** Config loaded per entry point (not cached). Ledger and audit are file-locked for concurrent safety. No module-level mutable singletons except `ProductionRegistry` (loaded fresh).
- **Circular imports:** None. Strict acyclic dependency order: CLI → Features/Models → Data sources → Ledger. Rebuild is an isolated tree, no crossover to incumbent except data sources.
- **Database:** SQLite WAL mode (Write-Ahead Logging) for concurrent readers; single writer per table via fcntl file lock. No transactions that span multiple SQLite databases.
- **Point-in-time correctness:** Hard invariant. Every feature lookup must preserve chronological order of information (observed_at_utc). Tests verify this via `test_train_serve_parity_for_v9_features`.

## Error Handling

**Strategy:** Fail-closed for decisions, fail-soft for observability.

**Patterns:**

- **Feature unavailable** → NO_CALL_* reason code (explicit, audit-logged)
- **Model load failure** → Disable that sport/market, log error, continue with fallback
- **Data source error** → Retry with exponential backoff; if timeout, log warning and skip (research picks); for Main picks, escalate to operator
- **Ledger write failure** → Audit log append succeeded; XLSX export deferred, may retry; operator notified
- **Real-money gate failure** → Pick is recorded as QUALIFIED_SHADOW_CALL (not executed); requires manual review before submission

## Cross-Cutting Concerns

**Logging:**

- Python `logging` module with file + console handlers
- Daily: `data/logs/daily_<date>.log` (INFO level)
- Supervisor: `data/logs/supervisor/<run_id>.log`
- Dashboard: stderr/stdout (no file by default)
- Structured audit events appended to `audit.json` per ledger tier

**Validation:**

- Config validation at load time (`config.py::validate_config`) — fail loudly on schema mismatch
- Ledger row validation on write (`_validate_pick_row`) — ensure required fields present
- Feature contract validation (`features/base.py::FeatureStore`) — raise on missing data instead of guess

**Authentication:**

- Dashboard token: Per-session UUID generated at login, validated for real-money endpoint
- Polymarket: ED25519-signed requests, credentials from env vars (`.env` or `launchd`)
- Other APIs: Header-based keys (API-Football) or public endpoints (ESPN, MLB Stats API)

---

*Architecture analysis: 2026-09-20*
