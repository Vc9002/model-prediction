<!-- GSD:project-start source:PROJECT.md -->

## Project

**Model Prediction**

A multi-sport event-prediction and trading system: point-in-time feature computation and per-sport models (MLB, WNBA, NFL, soccer, tennis, esports, CFB, international baseball) generate forecasts, which are compared against prediction-market prices (Polymarket) and, where edge exists, traded with real money. Runs on a daily scheduler with a dashboard for monitoring. Built and operated by a single developer (Vincent) as a live production trading system.

**Core Value:** The system must be profitable and accurate in predicting events — forecasts must beat the market (or at minimum identify real edge), and the trading layer must only fire on genuine edge, not noise. If accuracy or profitability erodes, everything else (dashboard polish, coverage breadth) is secondary.

### Constraints

- **Safety architecture**: Model changes must go through the existing challenger/shadow → promotion-gate pipeline, not bypass it, since this is live-money trading
- **Point-in-time correctness**: Any feature/data-source change must preserve PIT correctness — this is the system's most fragile area and its most common bug source
- **Tech stack**: Python 3.11+, existing per-sport model architecture (Elo/trends generic + domain-specific custom models) — new sport coverage should follow established patterns unless there's a clear reason not to
- **External data dependencies**: Soccer forecasting depends on `api_football` (single point of failure); MLB feature extraction depends on `pybaseball` scraping Baseball-Reference (fragile upstream)

<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

## Languages

- Python 3.11+ - All runtime logic, data processing, modeling, and CLI infrastructure
- HTML/JavaScript - Dashboard UI (`dashboard/`, `dashboard.html`, `dashboard_server.py`)
- Shell (Bash) - Scheduled job orchestration (`scripts/run_daily.sh`, launchd integration)

## Runtime

- Python 3.11+ (requires Python >=3.11, tracked in `pyproject.toml`)
- Virtual environment: `.venv` symlink to `/Users/vincentc9002/.venvs/model-prediction`
- Poetry / pip via `pyproject.toml` (hatchling build backend)
- Lockfile: `pyproject.toml` specifies pinned version ranges for all dependencies

## Frameworks

- Pydantic 2.8+ - Configuration validation and data model definition (`config.py`, domain models)
- httpx 0.27+ - Async HTTP client for all external API calls (Polymarket, ESPN, API-Football, etc.)
- scikit-learn 1.5+ - Logistic regression, XGBoost compatibility, preprocessing
- XGBoost 3.2+ (Python <3.12) / 3.4+ (Python >=3.12) - Gradient boosting models for multi-sport predictions
- scipy 1.13+ - Statistical distributions (Gamma-Poisson, Normal CDF), optimization
- numpy 2+ - Numerical array operations, matrix math
- pandas 2.2+ - Ledger management (`xlsx_ledger.py`), time-series features, dataset manipulation
- polars 1.40+ - High-performance parallel data ingestion and transformation
- DuckDB 1.5+ - In-process SQL analytics for feature computation and validation
- pytest 8+ - Test runner and fixtures
- pytest-xdist 3.5+ - Parallel test execution
- hypothesis 6+ - Property-based testing for edge cases
- scipy-stubs, pandas-stubs, types-* - Type hints for external libraries
- Ruff 0.6+ - Linter and code formatter (pyproject.toml: line-length=110, target-version=py311)
- mypy 1.11+ - Static type checker (python_version=3.14, check_untyped_defs=true)
- openpyxl 3.1+ - Read/write Excel workbooks for ledger export
- PyYAML 6+ - Configuration file parsing (`config/production.yaml`, `config/model.yaml`)
- pypdf 5+ - PDF parsing for regulatory/documentation extraction
- joblib 1.4+ - Parallel job utilities for model training
- cryptography 43+ (constraint: <47) - ED25519 signatures for Polymarket authentication
- pybaseball 2.2+ - Official MLB data ingestion (Stats API integration)

## Key Dependencies

- `httpx` - All external API communication; enables timeout control, retry logic, connection pooling
- `pydantic` - Validation for production configs, market quotes, order execution payloads (fail-closed validation)
- `pandas` / `polars` - Ledger CRUD operations, feature engineering for all sports models
- `XGBoost` - Primary MLB moneyline and spread/total models (`models/mlb.py`)
- `scipy` - Probability distributions: Gamma-Poisson (MLB totals), Normal CDF (WNBA spreads), other sports priors
- `DuckDB` - Feature store queries, walk-forward validation splits, historical backfill
- `SQLite3` (stdlib) - Canonical ledger store (`data/runs.db`, `data/ledgers/ledgers.db`, `data/production/production.db`)
- `openpyxl` - Fallback XLSX export (SQLite is canonical; XLSX is disposable projection)

## Configuration

- `MODEL_PREDICTION_RUNTIME_ROOT` - Mutable state location (default: `data/` repo-local, can be external)
- `MODEL_PREDICTION_LEDGER_AUTHORITY` - Ledger backend selector: `sqlite` (canonical, default 2026-08-16) or `xlsx`
- API keys (environment variables, never committed):
- `pyproject.toml` - Central source of truth for dependencies, build config, tool settings
- `.env` file (ignored, read by `polymarket_execute.py` for credentials if present)
- `config/production.yaml` - Schema v3: model registry, champion/challenger assignments, Polymarket edge flags
- `config/model.yaml` - Model artifact mappings, feature configurations
- `config/models/*.json` - Frozen promoted model artifacts (Elo, logistic regression coefficients)
- YAML configuration also loaded via `config.py::load_config()` with validation

## Platform Requirements

- macOS (Darwin) with launchd job support (`.plist` files in `ops/launchd/`)
- Python virtual environment (tracked via symlink `.venv`)
- Write access to `data/` (mutable ledger, feature cache, logs) or external runtime root
- No database server required (SQLite is embedded)
- macOS with launchd scheduler (daily job fires 4x per day: 00:30, 06:00, 12:00, 18:00 UTC as of 2026-09-04)
- `MODEL_PREDICTION_RUNTIME_ROOT` environment variable must be set for operational entry points (fail-closed)
- Polymarket US network access (https://gateway.polymarket.us, https://api.polymarket.us)
- ESPN network access (https://site.api.espn.com)
- API-Football network access (https://v3.football.api-sports.io) with API key
- Disk space for:

## CI/CD

- `.github/workflows/ci.yml` - GitHub Actions on push/PR: ruff lint + pytest (2,205 passing tests as of 2026-08-23)
- Pre-commit hooks: `.pre-commit-config.yaml` (ruff, mypy, pytest hooks available)
- `src/model_prediction/py.typed` marker - Indicates package provides type hints
- mypy overrides for untyped external libraries (sklearn, scipy, openpyxl)
- `test_train_serve_parity_for_v9_features` in `tests/test_validation.py` ensures feature computation parity between training and serving

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

## Naming Patterns

- Module files use `snake_case`: `production_canary.py`, `learned_forward.py`, `feature_contract.py`
- Directories use `snake_case`: `data_sources/`, `features/`, `cli/`, `rebuild/`
- Test files use `test_` prefix: `test_backtester.py`, `test_validation.py`, `test_lifecycle.py`
- Use `snake_case` for all function names: `walk_forward_backtest()`, `_acquire_exclusive_lock()`, `build_learned_moneyline_slate()`
- Private functions prefixed with single underscore: `_parse_snapshot_time()`, `_component_stats()`, `_load_indexes()`
- Public functions have no prefix: `evaluate_locked_holdout()`, `can_create_qualified_call()`
- Functions that return boolean frequently use `is_` or `can_` prefixes: `can_create_qualified_call()`, `is_expired()`
- Builder functions use `build_` prefix: `build_walk_forward_rows()`, `build_policy_artifact()`, `build_production_artifact()`
- Use `snake_case` for all variable names: `home_team`, `market_probability`, `locked_holdout_accuracy`
- Constants use `UPPER_SNAKE_CASE`: `LOCK_TIMEOUT_SECONDS`, `AUDIT_SCHEMA_VERSION`, `UNIT_MIN_EDGE`
- Module-level constants document their purpose inline: `LOCK_TIMEOUT_SECONDS = 30  # blocks indefinitely with no built-in timeout`
- Cached module-level dicts use `_CACHE` suffix: `_LEAGUE_RATES_CACHE`, `_PLAYER_INDEX_CACHE`, `_TEAM_GAME_INDEX_CACHE`
- Custom exceptions end with `Error` or `Timeout`: `AuditLockTimeout`, `EntityResolutionError`, `MarketBlendBlockedError`
- Enums use `StrEnum` from `enum` module, with `UPPER_SNAKE_CASE` members: `League.MLB`, `MarketType.MONEYLINE`, `ModelState.RESEARCH`
- Dataclass names use `PascalCase`: `BanEntry`, `ValidationRow`, `ChampionSnapshot`, `SettledBlendEvidence`

## Code Style

- Line length: 110 characters (configured in `pyproject.toml`)
- Use `ruff-format` for consistent formatting (enforced via pre-commit hook)
- No custom line-break rules beyond ruff defaults
- Linter: `ruff` v0.6+
- Run via: `python -m pytest` (with PYTHONPATH set) and `.venv/bin/ruff check src/ tests/`
- Pre-commit hook: ruff linting + ruff-format (see `.pre-commit-config.yaml`)
- Target Python version: 3.11+ (configured as `target-version = "py311"` in pyproject.toml)
- Status: Pre-existing baseline was ~118 findings; cleaned to zero (2026-08-26, verified clean), do not reintroduce
- MyPy enabled with strict settings:
- Run: `mypy` (configured in `pyproject.toml`, third-party stubs allowed)
- Third-party type-ignore overrides: sklearn, scipy, openpyxl, pybaseball, pypdf, polars, duckdb, xgboost (all lack stubs)

## Import Organization

- No explicit path aliases; use relative imports within the package (`from .module import ...`)
- Absolute imports from external packages: `from model_prediction.validation import ...`
- In test files, both forms are used (`from model_prediction.X import ...` and relative imports)
- Separate groups with single blank line
- Within each group, sort alphabetically (ruff/import-sorting enforces this)
- Multi-line imports use parentheses (not backslash continuation)

## Error Handling

- Raise standard exceptions for validation failures: `ValueError`, `TimeoutError`
- Define custom exceptions as `Exception` subclasses for domain-specific errors: `AuditLockTimeout(TimeoutError)`, `EntityResolutionError(Exception)`
- Raise with descriptive messages that include context: `"could not acquire lock on {path} within {timeout}s -- another process may be hung while holding it"`
- Use `... from None` to suppress exception chaining when it's not relevant to the user

## Logging

- Logger per module: `_logger = logging.getLogger(__name__)` at module level
- Use `_logger.warning()` for recoverable issues that the system can continue despite: `_logger.warning("production store unavailable %s; cycle's predictions will not recorded", path, exc_info=True)`
- Use `_logger.info()` for operational milestones (rarely used in this codebase)
- Never use `print()` for operational output (always use logger)
- Include `exc_info=True` when logging exception context: `_logger.warning("...", exc_info=True)`

## Comments

- Explain *why*, not *what* — avoid restating code that's already clear from reading it
- Document hidden constraints: "Point-in-time correctness: a decision made at time T may only use information with observed_at_utc <= T"
- Document workarounds for specific real bugs: "fcntl.flock(LOCK_EX) blocks indefinitely with no built-in timeout -- a hung holder (not crashed; POSIX auto-releases on process death) would otherwise wedge every other appender forever"
- Document non-obvious invariants: "Mutable operator state lives at the runtime root, not in the repo checkout"

## Function Design

- Use explicit parameter names (no positional-only patterns)
- Type-hint all parameters: `def append(self, event_type: str, subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:`
- Use default arguments sparingly; when needed, default to `None` or safe constants
- Avoid mutable default arguments (e.g., `dict()` or `[]`)
- Always type-hint return values: `-> dict[str, Any]`, `-> Optional[float]`
- Return structured data (dicts, dataclasses) from complex operations, not tuples
- Use dataclasses for multi-field returns (e.g., `ChampionSnapshot`, `ValidationRow`)
- Single `None` return indicates failure or no result; return empty containers only when semantically correct

## Module Design

- Modules explicitly export public symbols in `__all__` (when needed for clarity); otherwise rely on naming convention (no leading underscore = public)
- Private functions/classes use leading underscore
- The `__init__.py` in `src/model_prediction/` is minimal (version marker only)
- Dashboard submodules use explicit re-exports in `__init__.py` (see conftest.py's patching strategy for `model_prediction.dashboard.status`)

## Dataclass & Type Patterns

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

## System Overview

```text

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

- **Shadow-first**: New models and sports start as research (zero units) until promotion gate passes
- **Separation of concerns**: Forecast logic (prediction) decoupled from execution logic (order submission)
- **Fail-closed validation**: Missing data → explicit NO_CALL reason codes, never guesses
- **Audit-first mutations**: Append to audit log BEFORE writing ledger (idempotent recovery)
- **Champion/Challenger architecture**: Exactly one serving model per sport/market; promotion is explicit, tracked, reversible

## Layers

- Purpose: User-facing commands for forecast, settle, backtest, etc.
- Location: `src/model_prediction/cli/`
- Contains: `main.py` (dispatcher), `forecast.py`, `settle.py`, `daily.py`, `commands.py`
- Depends on: All domain modules below
- Used by: launchd jobs, dashboard, manual operators
- Purpose: Compute predictions on a schedule or on-demand
- Location: `src/model_prediction/learned_forward.py` (generic sports), `models/` (custom per-sport)
- Contains: Feature composition, model loading, point-in-time probability inference
- Depends on: Feature store, data sources, production registry
- Used by: Daily pipeline, manual forecast CLI, dashboard
- Purpose: Compute time-series and matchup features (Elo, park factors, pitcher ERA gaps, etc.)
- Location: `src/model_prediction/features/`
- Contains: 50+ modules, each a domain (player availability, trends, ballpark conditions, etc.)
- Depends on: Data sources, historical game records
- Used by: Forecast engine, validation/walkforward, backtest
- Purpose: Fetch, normalize, and cache external data (ESPN, Polymarket, API-Football, etc.)
- Location: `src/model_prediction/data_sources/`
- Contains: One module per provider; fail-soft on network errors, cache-first where applicable
- Depends on: httpx for HTTP, parsing (json, yaml)
- Used by: Features, validation, settlement, market data refresh
- Purpose: Record every pick, settlement, void, and audit event; canonical truth store
- Location: `src/model_prediction/ledger.py`, `runtime_ledger_store.py`, `model_ledger.py`
- Contains: SQLite canonical store, XLSX fallback export, audit chain, pick manipulation
- Depends on: sqlite3 (stdlib), openpyxl (Excel write)
- Used by: All tiers (Main, Flat, Research, Gated Research), dashboard, reports
- Purpose: Load and validate production model configs; track champion/challenger; enforce governance
- Location: `src/model_prediction/production_registry.py`, `config.py`
- Contains: Config parsing, artifact resolution, status tracking, lifecycle validation
- Depends on: YAML files (`config/production.yaml`, `config/model.yaml`)
- Used by: Forecast, settlement, dashboard, CLI commands
- Purpose: Clean-slate research pipeline for model experimentation; shadow-only, no production write
- Location: `src/model_prediction/rebuild/`
- Contains: Separate feature computation, decision engine, validation harness
- Depends on: Data sources (shared), DuckDB (for high-perf analytics)
- Used by: Research teams, scheduled shadow jobs, offline ablations
- Purpose: Real-time monitoring, pick review, order execution surface (for qualified users)
- Location: `src/model_prediction/dashboard/`, `dashboard_server.py`
- Contains: Flask routes, data aggregation, portfolio logic, token-based auth
- Depends on: Ledger, production registry, system health
- Used by: Operators, analysts, real-money execution
- Purpose: Real-money order submission, position tracking, Polymarket integration
- Location: `src/model_prediction/portfolio/`
- Contains: Auto-buyer, execution rehearsal, Polymarket Kelly sizing, order ledgers
- Depends on: `polymarket_execute.py`, ledger for audit
- Used by: Dashboard order endpoint, scheduled auto-buyer
- Purpose: Orchestrate daily cycles, manage job leases, detect health degradation
- Location: `src/model_prediction/run_supervisor.py`, `system_health.py`
- Contains: Lock-based concurrency, job lifecycle (run, heartbeat, exit), health queries
- Depends on: SQLite `runs.db`, fcntl (file locking)
- Used by: launchd, dashboard "run now" button, CI/monitoring

## Data Flow

### Primary Request Path (Daily Forecast)

### Settlement Path

### Walk-Forward Validation Path (Training)

### Rebuild (Shadow) Isolation

## State Management

- Location: `data/ledgers/ledgers.db` or runtime root equivalent
- Schema: `ledger_records` table (pick_id, decision_time_utc, sport, market_type, horizon, status, etc.)
- Mutations: append-only. Updates via `UPDATE` on settled/voided/archived rows (idempotent)
- Audit: Separate `audit.json` append-only file (atomicity: audit-first, then ledger write)
- `data/runs.db` — Job execution history (run_id, lease, heartbeat, exit_code)
- `data/production/production.db` — Production predictions snapshot (via `ProductionPredictionStore`)
- `data/rebuild/metadata.db` — Shadow job tracking, feature cache stats
- `outputs/rebuild/verification.json` — CI-generated proof of shadow isolation
- `data/archive/` — Settled and removed picks (never deleted)
- `data/snapshots/` — Point-in-time evidence backups
- `config/production.yaml` — Model registry, champion selections, execution gates
- `config/model.yaml` — Feature lists, artifact mappings
- Loaded fresh on every entry point (no process-level caching except in tests)

## Key Abstractions

- Purpose: Immutable decision record (inputs and outputs of one prediction)
- Examples: `src/model_prediction/domain.py`
- Pattern: Dataclass with validation; used throughout ledger and audit chain
- Purpose: Lazy, point-in-time feature computation per game
- Examples: `features/base.py`, every module in `features/`
- Pattern: `games_before(cutoff_date)` returns only games strictly before decision time; raises on missing data
- Purpose: Versioned, checksum-validated prediction model definition
- Examples: `models/learned_market.py`
- Pattern: JSON payload with embedded coefficient/tree definitions, loaded and cached at startup
- Purpose: Resolved view of all in-production models
- Examples: `production_registry.py`
- Pattern: Singleton-style, validate on load, fail-closed per model, `champion(sport, market)` lookup
- Purpose: Atomic, idempotent ledger operations with WAL recovery
- Examples: `runtime_ledger_store.py`
- Pattern: Transaction-like interface (begin, add rows, commit), replayable from audit log

## Entry Points

- `com.modelprediction.daily` (4x/day) → `run_supervisor run daily` → `cli/daily.py --date ...`
- `com.modelprediction.production` (manual trigger) → `run_supervisor run production` → `cli_production.py`
- `com.modelprediction.rebuild-shadow` (research job) → `run_supervisor run rebuild-shadow`
- `python -m model_prediction forecast --sport MLB --date 2026-09-20`
- `python -m model_prediction settle --sport MLB --date 2026-09-20`
- `python -m model_prediction validate --athlete --sport ...` (backtest)
- `python dashboard_server.py` → Flask, port 8765
- Routes: `/picks`, `/orders`, `/portfolio`, `/execute` (real-money, token-gated)
- `python -m model_prediction.rebuild cli run ...` (separate entry point, no production writes)

## Architectural Constraints

- **Threading:** Single-threaded main flow (no asyncio at the process level). Forecast fans out via `ThreadPoolExecutor` for per-sport predictions in `cli/forecast.py`.
- **Global state:** Config loaded per entry point (not cached). Ledger and audit are file-locked for concurrent safety. No module-level mutable singletons except `ProductionRegistry` (loaded fresh).
- **Circular imports:** None. Strict acyclic dependency order: CLI → Features/Models → Data sources → Ledger. Rebuild is an isolated tree, no crossover to incumbent except data sources.
- **Database:** SQLite WAL mode (Write-Ahead Logging) for concurrent readers; single writer per table via fcntl file lock. No transactions that span multiple SQLite databases.
- **Point-in-time correctness:** Hard invariant. Every feature lookup must preserve chronological order of information (observed_at_utc). Tests verify this via `test_train_serve_parity_for_v9_features`.

## Error Handling

- **Feature unavailable** → NO_CALL_* reason code (explicit, audit-logged)
- **Model load failure** → Disable that sport/market, log error, continue with fallback
- **Data source error** → Retry with exponential backoff; if timeout, log warning and skip (research picks); for Main picks, escalate to operator
- **Ledger write failure** → Audit log append succeeded; XLSX export deferred, may retry; operator notified
- **Real-money gate failure** → Pick is recorded as QUALIFIED_SHADOW_CALL (not executed); requires manual review before submission

## Cross-Cutting Concerns

- Python `logging` module with file + console handlers
- Daily: `data/logs/daily_<date>.log` (INFO level)
- Supervisor: `data/logs/supervisor/<run_id>.log`
- Dashboard: stderr/stdout (no file by default)
- Structured audit events appended to `audit.json` per ledger tier
- Config validation at load time (`config.py::validate_config`) — fail loudly on schema mismatch
- Ledger row validation on write (`_validate_pick_row`) — ensure required fields present
- Feature contract validation (`features/base.py::FeatureStore`) — raise on missing data instead of guess
- Dashboard token: Per-session UUID generated at login, validated for real-money endpoint
- Polymarket: ED25519-signed requests, credentials from env vars (`.env` or `launchd`)
- Other APIs: Header-based keys (API-Football) or public endpoints (ESPN, MLB Stats API)

<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

| Skill | Description | Path |
|-------|-------------|------|
| develop-model-prediction | Design, implement, test, and document model or data-pipeline changes in the model-prediction repository. Use when Vincent asks to add or improve a model, feature, league, market, data source, calibration, validation rule, artifact, backtest, or point-in-time evidence pipeline, or asks whether a claimed improvement is real. Keep changes versioned, reproducible, shadow-first, and fail-closed. | `.codex/skills/develop-model-prediction/SKILL.md` |
| forecast-model-picks | Forecast, log, settle, review, and diagnose sports picks in the model-prediction repository. Use when Vincent asks for current model calls, exact model inputs, a slate forecast, a shadow or flat ledger update, settlement, CLV, performance, team bans, or a loss diagnosis across MLB, NBA, WNBA, NFL, soccer, tennis, esports, KBO, or NPB. Never place, modify, or cancel a real-money order. | `.codex/skills/forecast-model-picks/SKILL.md` |
| operate-model-dashboard | Start, inspect, test, troubleshoot, and safely modify the local model-prediction dashboard and its view state. Use when Vincent asks to open the dashboard, debug dashboard data or controls, inspect matrix, ledger, or order status, change display behavior, clear or restore local rows, update unit-value settings, or verify the dashboard in Dia. Never submit, cancel, or modify exchange orders without a separate explicit real-money request and confirmation. | `.codex/skills/operate-model-dashboard/SKILL.md` |
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

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
