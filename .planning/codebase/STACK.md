---
last_mapped_commit: 3aaa58949aca8dabf49593091243d6719d859452
last_mapped_at: 2026-09-20
---
# Technology Stack

**Analysis Date:** 2026-09-20

## Languages

**Primary:**

- Python 3.11+ - All runtime logic, data processing, modeling, and CLI infrastructure

**Secondary:**

- HTML/JavaScript - Dashboard UI (`dashboard/`, `dashboard.html`, `dashboard_server.py`)
- Shell (Bash) - Scheduled job orchestration (`scripts/run_daily.sh`, launchd integration)

## Runtime

**Environment:**

- Python 3.11+ (requires Python >=3.11, tracked in `pyproject.toml`)
- Virtual environment: `.venv` symlink to `/Users/vincentc9002/.venvs/model-prediction`

**Package Manager:**

- Poetry / pip via `pyproject.toml` (hatchling build backend)
- Lockfile: `pyproject.toml` specifies pinned version ranges for all dependencies

## Frameworks

**Core:**

- Pydantic 2.8+ - Configuration validation and data model definition (`config.py`, domain models)
- httpx 0.27+ - Async HTTP client for all external API calls (Polymarket, ESPN, API-Football, etc.)

**Machine Learning:**

- scikit-learn 1.5+ - Logistic regression, XGBoost compatibility, preprocessing
- XGBoost 3.2+ (Python <3.12) / 3.4+ (Python >=3.12) - Gradient boosting models for multi-sport predictions
- scipy 1.13+ - Statistical distributions (Gamma-Poisson, Normal CDF), optimization
- numpy 2+ - Numerical array operations, matrix math

**Data Processing:**

- pandas 2.2+ - Ledger management (`xlsx_ledger.py`), time-series features, dataset manipulation
- polars 1.40+ - High-performance parallel data ingestion and transformation
- DuckDB 1.5+ - In-process SQL analytics for feature computation and validation

**Testing:**

- pytest 8+ - Test runner and fixtures
- pytest-xdist 3.5+ - Parallel test execution
- hypothesis 6+ - Property-based testing for edge cases
- scipy-stubs, pandas-stubs, types-* - Type hints for external libraries

**Build/Dev:**

- Ruff 0.6+ - Linter and code formatter (pyproject.toml: line-length=110, target-version=py311)
- mypy 1.11+ - Static type checker (python_version=3.14, check_untyped_defs=true)

**File/Data Format:**

- openpyxl 3.1+ - Read/write Excel workbooks for ledger export
- PyYAML 6+ - Configuration file parsing (`config/production.yaml`, `config/model.yaml`)
- pypdf 5+ - PDF parsing for regulatory/documentation extraction
- joblib 1.4+ - Parallel job utilities for model training

**Cryptography & Auth:**

- cryptography 43+ (constraint: <47) - ED25519 signatures for Polymarket authentication

**Sports Data:**

- pybaseball 2.2+ - Official MLB data ingestion (Stats API integration)

## Key Dependencies

**Critical:**

- `httpx` - All external API communication; enables timeout control, retry logic, connection pooling
- `pydantic` - Validation for production configs, market quotes, order execution payloads (fail-closed validation)
- `pandas` / `polars` - Ledger CRUD operations, feature engineering for all sports models
- `XGBoost` - Primary MLB moneyline and spread/total models (`models/mlb.py`)
- `scipy` - Probability distributions: Gamma-Poisson (MLB totals), Normal CDF (WNBA spreads), other sports priors

**Infrastructure:**

- `DuckDB` - Feature store queries, walk-forward validation splits, historical backfill
- `SQLite3` (stdlib) - Canonical ledger store (`data/runs.db`, `data/ledgers/ledgers.db`, `data/production/production.db`)
- `openpyxl` - Fallback XLSX export (SQLite is canonical; XLSX is disposable projection)

## Configuration

**Environment:**

- `MODEL_PREDICTION_RUNTIME_ROOT` - Mutable state location (default: `data/` repo-local, can be external)
- `MODEL_PREDICTION_LEDGER_AUTHORITY` - Ledger backend selector: `sqlite` (canonical, default 2026-08-16) or `xlsx`
- API keys (environment variables, never committed):
  - `POLYMARKET_API_KEY_ID` - Polymarket US retail API authentication
  - `POLYMARKET_PRIVATE_KEY` - ED25519 private key for request signing
  - `API_FOOTBALL_KEY` - API-Football v3 (api-sports.io) for soccer scores
  - `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY` - Kalshi (deferred: US residency required)

**Build:**

- `pyproject.toml` - Central source of truth for dependencies, build config, tool settings
  - `[tool.pytest.ini_options]`: pythonpath includes `.` and `src/`
  - `[tool.ruff]`: line-length 110, target Python 3.11
  - `[tool.mypy]`: strict overrides for sklearn, scipy, openpyxl, pybaseball, etc.

**Sources of Config:**

- `.env` file (ignored, read by `polymarket_execute.py` for credentials if present)
- `config/production.yaml` - Schema v3: model registry, champion/challenger assignments, Polymarket edge flags
- `config/model.yaml` - Model artifact mappings, feature configurations
- `config/models/*.json` - Frozen promoted model artifacts (Elo, logistic regression coefficients)
- YAML configuration also loaded via `config.py::load_config()` with validation

## Platform Requirements

**Development:**

- macOS (Darwin) with launchd job support (`.plist` files in `ops/launchd/`)
- Python virtual environment (tracked via symlink `.venv`)
- Write access to `data/` (mutable ledger, feature cache, logs) or external runtime root
- No database server required (SQLite is embedded)

**Production:**

- macOS with launchd scheduler (daily job fires 4x per day: 00:30, 06:00, 12:00, 18:00 UTC as of 2026-09-04)
- `MODEL_PREDICTION_RUNTIME_ROOT` environment variable must be set for operational entry points (fail-closed)
- Polymarket US network access (https://gateway.polymarket.us, https://api.polymarket.us)
- ESPN network access (https://site.api.espn.com)
- API-Football network access (https://v3.football.api-sports.io) with API key
- Disk space for:
  - `data/odds/` - Polymarket BBO snapshots (JSONL, ~MB/day per league)
  - `data/providers/` - Raw provider cache (hash-stamped snapshots)
  - `data/historical/` - Ingested game results (JSONL)
  - `data/features/` - Computed feature vectors (Parquet)
  - `data/ledgers/` - SQLite canonical store (persistent, ~MB)

## CI/CD

**Local:**

- `.github/workflows/ci.yml` - GitHub Actions on push/PR: ruff lint + pytest (2,205 passing tests as of 2026-08-23)
- Pre-commit hooks: `.pre-commit-config.yaml` (ruff, mypy, pytest hooks available)

**Type Safety:**

- `src/model_prediction/py.typed` marker - Indicates package provides type hints
- mypy overrides for untyped external libraries (sklearn, scipy, openpyxl)
- `test_train_serve_parity_for_v9_features` in `tests/test_validation.py` ensures feature computation parity between training and serving

---

*Stack analysis: 2026-09-20*
