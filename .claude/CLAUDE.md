<!-- GSD:project-start source:PROJECT.md -->

## Project

**Model Prediction**

A multi-sport event-prediction and paper-trading system: point-in-time feature computation and per-sport models generate forecasts, which are compared against prediction-market prices (Polymarket) and, where edge exists, bought in a simulated paper ledger with full market fidelity. A separate, explicitly gated real-money execution path exists and is never run automatically. Production scope since 2026-11-13 is NBA, NCAAF, NFL, MLB, WNBA and NCAAB. The single statement of scope and of the paper/real-money boundary is the root `CLAUDE.md` ("What this project is" and "Scope"); this file defers to it. Runs on a daily scheduler with a dashboard for monitoring. Built and operated by a single developer (Vincent).

**Core Value:** The system must be profitable and accurate in predicting events — forecasts must beat the market (or at minimum identify real edge), and the trading layer must only fire on genuine edge, not noise. If accuracy or profitability erodes, everything else (dashboard polish, coverage breadth) is secondary.

### Constraints

- **Safety architecture**: Model changes must go through the existing challenger/shadow → promotion-gate pipeline, not bypass it; the paper ledger is run as if real capital were at stake, and the gated real-money path shares the same code
- **Point-in-time correctness**: Any feature/data-source change must preserve PIT correctness — this is the system's most fragile area and its most common bug source
- **Tech stack**: Python 3.11+, existing per-sport model architecture (Elo/trends generic + domain-specific custom models) — new sport coverage should follow established patterns unless there's a clear reason not to
- **External data dependencies**: Soccer forecasting depends on `api_football` (single point of failure); MLB feature extraction depends on `pybaseball` scraping Baseball-Reference (fragile upstream)

<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

Versions and the full dependency list live in `pyproject.toml`; only the non-obvious facts are kept here.

- Python 3.11+ (hatchling build). Ruff line-length 110, target py311; mypy with `check_untyped_defs`.
- `httpx` for all external HTTP; `pydantic` for fail-closed config/payload validation; XGBoost + scikit-learn for models; `scipy` for distributions (Gamma-Poisson MLB totals, Normal CDF WNBA spreads); pandas/polars/DuckDB for ledgers, features, and walk-forward splits.
- SQLite is the canonical store (`runs.db`, `ledgers/ledgers.db`, `production/production.db`); XLSX via `openpyxl` is a disposable export.
- Config: `config/production.yaml` (schema v3: model registry + champions), `config/model.yaml` (artifact mappings, features), `config/models/*.json` (frozen promoted artifacts), `.env` (ignored; Polymarket credentials).
- Env vars: `MODEL_PREDICTION_RUNTIME_ROOT` (mutable state location; required by operational entry points, fail-closed) and `MODEL_PREDICTION_LEDGER_AUTHORITY` (`sqlite` default since 2026-08-16, or `xlsx`).
- Scheduler: macOS launchd (`ops/launchd/*.plist`); the daily job fires 4x/day.
- External services: Polymarket US (`gateway.polymarket.us`, `api.polymarket.us`), ESPN (`site.api.espn.com`), API-Football (`v3.football.api-sports.io`, API key required).
- CI: `.github/workflows/ci.yml` (ruff + pytest); pre-commit has ruff, mypy, and pytest hooks. `tests/test_validation.py::test_train_serve_parity_for_v9_features` pins training/serving feature parity.

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

- **Naming:** `snake_case` modules, functions, and variables; leading `_` for private; `build_*` for builders (`build_walk_forward_rows()`); `is_*`/`can_*` for booleans; `UPPER_SNAKE_CASE` constants with an inline comment for purpose; `_CACHE` suffix for module-level caches; exceptions end in `Error`/`Timeout`; `StrEnum` with `UPPER_SNAKE_CASE` members; `PascalCase` dataclasses.
- **Style:** ruff + ruff-format (110 cols), enforced by pre-commit. Ruff is clean as of 2026-08-26 — do not reintroduce findings. Third-party type-ignore overrides cover libraries without stubs (sklearn, scipy, openpyxl, pybaseball, pypdf, polars, duckdb, xgboost).
- **Imports:** relative inside the package (`from .module import ...`); absolute for external packages; tests use either form. Ruff sorts them; multi-line imports use parentheses.
- **Errors:** raise `ValueError`/`TimeoutError` for validation failures; domain errors subclass `Exception` (or `TimeoutError`) with messages that include context (path, timeout, ids). Use `... from None` when the chained cause is noise.
- **Logging:** `_logger = logging.getLogger(__name__)` per module; `_logger.warning(..., exc_info=True)` for recoverable failures; never `print()` for operational output.
- **Functions:** type-hint all parameters and returns; no mutable default arguments; return dataclasses (not tuples) for multi-field results; `None` means failure/no result — return empty containers only when that is semantically correct.
- **Modules:** `__all__` only where it aids clarity; dashboard submodules explicitly re-export in `__init__.py` (`tests/conftest.py` patches `model_prediction.dashboard.status`).

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

### Components

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
| Real-Money Execution | Order submission to Polymarket (gated, explicit per-request approval; never scheduled) | `portfolio/`, `polymarket_execute.py` |
| Scheduler/Supervisor | Job execution, lease management, health check | `run_supervisor.py`, `system_health.py` |

Dependency order is strictly acyclic: CLI → Features/Models → Data sources → Ledger. Rebuild is an isolated tree that shares only data sources with the incumbent system.

### Patterns

- **Shadow-first**: new models and sports start as research (zero units) until the promotion gate passes
- **Separation of concerns**: forecast logic (prediction) is decoupled from execution logic (order submission)
- **Fail-closed validation**: missing data → explicit `NO_CALL_*` reason codes, never guesses
- **Audit-first mutations**: append to the audit log BEFORE writing the ledger (idempotent recovery)
- **Champion/Challenger**: exactly one serving model per sport/market; promotion is explicit, tracked, reversible

### State

- Ledger: `data/ledgers/ledgers.db` (or runtime-root equivalent), `ledger_records` table; append-only, with idempotent `UPDATE` on settled/voided/archived rows. Audit trail is a separate append-only `audit.json` per tier (audit-first, then ledger write).
- `data/runs.db` — job execution history (run_id, lease, heartbeat, exit_code)
- `data/production/production.db` — production predictions (via `ProductionPredictionStore`)
- `data/rebuild/metadata.db` — shadow job tracking, feature cache stats
- `outputs/rebuild/verification.json` — CI-generated proof of shadow isolation
- `data/archive/` — settled and removed picks (never deleted); `data/snapshots/` — point-in-time evidence backups
- Config (`config/production.yaml`, `config/model.yaml`) is loaded fresh on every entry point; no process-level caching except in tests.

### Entry Points

- Scheduled (launchd → supervisor): `com.modelprediction.daily` → `run_supervisor run daily` → `cli/daily.py`; `com.modelprediction.production` → `run_supervisor run production` → `cli_production.py`; `com.modelprediction.rebuild-shadow` → `run_supervisor run rebuild-shadow`
- CLI: `python -m model_prediction forecast|settle|validate ...`
- Dashboard: `python dashboard_server.py` (Flask, port 8765); routes `/picks`, `/orders`, `/portfolio`, `/execute` (real-money, token-gated)
- Rebuild: `python -m model_prediction.rebuild cli run ...` (separate entry point, no production writes)

### Constraints

- **Threading:** single-threaded main flow (no asyncio); forecast fans out per sport via `ThreadPoolExecutor` in `cli/forecast.py`.
- **Global state:** config is loaded per entry point; ledger and audit are file-locked. No module-level mutable singletons except `ProductionRegistry` (loaded fresh).
- **Database:** SQLite WAL for concurrent readers; single writer per table via fcntl file lock; no transactions spanning multiple SQLite databases.
- **Point-in-time correctness:** hard invariant — every feature lookup preserves chronological order of information (`observed_at_utc`).

### Error Handling

- **Feature unavailable** → `NO_CALL_*` reason code (explicit, audit-logged)
- **Model load failure** → disable that sport/market, log error, continue with fallback
- **Data source error** → retry with exponential backoff; on timeout, log warning and skip (research picks); for Main picks, escalate to operator
- **Ledger write failure** → audit append already succeeded; XLSX export deferred, may retry; operator notified
- **Real-money gate failure** → pick is recorded as `QUALIFIED_SHADOW_CALL` (not executed); requires manual review before submission

### Logging and Auth

- Logs: `data/logs/daily_<date>.log` (INFO), `data/logs/supervisor/<run_id>.log`; dashboard logs to stderr/stdout only. Structured audit events go to `audit.json` per ledger tier.
- Validation: config at load (`config.py::validate_config`), ledger rows on write (`_validate_pick_row`), features via `features/base.py::FeatureStore` (raise on missing data instead of guessing).
- Dashboard real-money endpoint uses a per-session UUID token. Polymarket requests are ED25519-signed with credentials from env vars (`.env` or launchd); API-Football uses header keys; ESPN and the MLB Stats API are public.

<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

| Skill | Use when | Path |
|-------|----------|------|
| develop-model-prediction | Adding or improving a model, feature, league, market, data source, calibration, validation rule, artifact, or backtest, or checking whether a claimed improvement is real. Keep changes versioned, shadow-first, fail-closed. | `.codex/skills/develop-model-prediction/SKILL.md` |
| forecast-model-picks | Current model calls, slate forecasts, shadow/flat ledger updates, settlement, CLV, performance, team bans, loss diagnosis. Never place, modify, or cancel a real-money order. | `.codex/skills/forecast-model-picks/SKILL.md` |
| operate-model-dashboard | Starting, inspecting, testing, or safely modifying the local dashboard and its view state. Never submit, cancel, or modify exchange orders without a separate explicit real-money request and confirmation. | `.codex/skills/operate-model-dashboard/SKILL.md` |
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->
