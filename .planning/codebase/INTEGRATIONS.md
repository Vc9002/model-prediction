---
last_mapped_commit: 3aaa58949aca8dabf49593091243d6719d859452
last_mapped_at: 2026-09-20
---
# External Integrations

**Analysis Date:** 2026-09-20

## APIs & External Services

**Market Data:**

- **Polymarket US** - Primary prediction market for live pricing, settlement, and order execution
  - SDK/Client: `src/model_prediction/data_sources/polymarket_us.py` (`PolymarketUSClient`)
  - Endpoints: `https://gateway.polymarket.us/v2/` (unauthenticated read-only) and `https://api.polymarket.us/` (authenticated execution)
  - Auth: ED25519-signed requests with `POLYMARKET_API_KEY_ID` and `POLYMARKET_PRIVATE_KEY` env vars
  - Data: Event listings, market orderbooks, current bid/ask prices, market resolution status
  - Coverage: MLB, NBA, WNBA, NFL, NCAAF, soccer (40+ leagues), tennis (WTA/ATP/ITF), esports (LoL/CS2/Dota2/Valorant/R6), KBO, NPB
  - Capture: Prospective BBO snapshots stored in `data/odds/{sport}/{date}/polymarket_snapshots.jsonl`
  - Execution: Real-money order submission via `polymarket_execute.py` (gated, requires explicit CLI flag + confirmation)

- **Kalshi** - CFTC-regulated US prediction exchange (DEFERRED)
  - Client stub: `src/model_prediction/data_sources/kalshi.py` (`KalshiClient`)
  - Status: Deferred (requires US residency; account holder is outside US)
  - Activation path documented in module; requires `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY`
  - Not currently integrated; all production trades execute on Polymarket US only

**Sportsbooks & Game Data:**

- **ESPN** - Official scoreboard for all US sports (MLB, NBA, WNBA, NFL) and soccer
  - Client: `src/model_prediction/data_sources/espn.py` (`ESPNClient`, `ESPNMLBClient`)
  - Base URL: `https://site.api.espn.com/apis/site/v2/sports`
  - Data: Game schedules, live scores, injury status, play-by-play (historical reconstruction for features)
  - No auth required (public endpoint)
  - Cache: Point-in-time snapshots stored in `data/historical/{sport}_games_all.jsonl`
  - Usage: Settlement validation (all US sports), feature engineering (pitcher ERA, bullpen fatigue, etc.)

- **MLB Stats API** - Official MLB stats and game feeds
  - Client: `src/model_prediction/data_sources/mlb_statsapi.py` (`MLBStatsAPIClient`)
  - Base URL: `https://statsapi.mlb.com/api/`
  - Data: Schedule (sportId=1), live_feed (game-level detail), boxscore (player statistics)
  - No auth required
  - Cached snapshots: `data/mlb_statsapi/game_snapshots.jsonl`
  - Usage: Park factors, relief pitcher history, weather data extraction

- **API-Football (api-sports.io)** - Primary soccer results and fixtures (active since 2026-08-26)
  - Client: `src/model_prediction/data_sources/api_football.py` (`APIFootballClient`)
  - Base URL: `https://v3.football.api-sports.io`
  - Auth: Header `x-apisports-key` with `API_FOOTBALL_KEY` env var
  - Endpoints: `GET /fixtures?date=YYYY-MM-DD&league=<id>&season=<YYYY>` (20 leagues covered)
  - Data: Match fixtures, status (NS/1H/HT/2H/FT/AET/PEN), scores, goals
  - Free tier: 100 requests/day across all endpoints
  - Cached snapshots: `data/providers/api_football/soccer/raw/{date}/...`
  - Usage: Soccer score settlement (replaced The Odds API 2026-08-26 after 31+ days of 401 failures)
  - Fallback: ESPN event-ID-based settlement as keyless backup

- **The Odds API** - Legacy sportsbook lines and soccer scores (DEFUNCT)
  - Status: Deferred/removed as of 2026-09-02 (401 errors for 31+ days)
  - Client: Was `sports_data_io.py` (now removed)
  - Replaced by: API-Football for scores, Polymarket US for live lines

**Player & Team Data:**

- **pybaseball** - MLB player season/career statistics
  - Integration: Direct dependency (`src/model_prediction/` features use public methods)
  - Data: Batting/pitching stats, park factors (legacy v8 usage)
  - No auth required

- **Tennis Sackmann CSV Data** - Historical ATP/WTA match results
  - Format: Sourced locally from `data/tennis/*.csv` (not fetched live)
  - Client: `src/model_prediction/data_sources/tennis_sackmann.py`
  - Data: Match metadata, scores, points, player IDs, surface, Elo ratings
  - Usage: Surface-specific Elo computation for WTA/ATP models

- **BallDontLie API** - NBA/WNBA player and team statistics
  - Client: `src/model_prediction/data_sources/balldontlie.py` (`BallDontLieClient`)
  - Base URL: `https://api.balldontlie.io`
  - Data: Player season stats, team stats, game logs
  - No auth required (public endpoint)
  - Usage: WNBA availability checks, injury tracking

**Sports Data Aggregators:**

- **Sports Data Io** - Multi-sport data (esports, international leagues)
  - Client: `src/model_prediction/data_sources/sportsdataio.py`
  - Usage: KBO, NPB, esports league standings and results
  - Auth: API key-based (if used)

- **Bet Better** - Betting market aggregation
  - Client: `src/model_prediction/data_sources/bet_better.py`
  - Status: Available but not actively used in current daily pipeline

**Injury & Availability Data:**

- **ESPN Probables** - MLB starting pitcher announcements
  - Client: `src/model_prediction/data_sources/espn_probables.py`
  - Usage: Pitcher selection confirmation before daily forecast
  - No auth required

- **ESPN Injuries** (MLB & WNBA) - Injury status and roster updates
  - Clients: `src/model_prediction/data_sources/mlb_injuries.py`, `wnba_injuries.py`
  - Base URL: ESPN site-API `https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}`
  - Data: Player injury reports, status (out/doubtful/questionable), reserve list
  - Cache: Stored in `data/providers/...`
  - Usage: Feature engineering (availability for ML models), pick eligibility gates

## Data Storage

**Databases:**

- **SQLite (Canonical since 2026-08-16)**
  - Main ledger: `data/ledgers/ledgers.db` (or runtime root equivalent)
  - Canonical store for all pick decisions, settlements, audit chain
  - Schema: `ledger_records` (pick_id, decision_time_utc, sport, market_type, status, etc.)
  - Client: Native Python sqlite3 (stdlib) via `runtime_ledger_store.py`
  - Write authority: `MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite` (default)

- **SQLite (Control Plane & Rebuild)**
  - Runs database: `data/runs.db` - Job execution history, leases, heartbeats (launchd integration)
  - Production store: `data/production/production.db` - Predictions, market snapshots, decisions, runs
  - Rebuild metadata: `data/rebuild/metadata.db`, `data/rebuild/shadow.db` - Shadow-only validation

**Excel Workbooks (Fallback Projection):**

- **Main Ledger**: `data/main/mlb.xlsx`, `data/main/wnba.xlsx`, etc.
  - Sheets: **Picks**, Summary, Review
  - Status: Disposable XLSX export from canonical SQLite (fail-soft on write failure)
  - Rebuilt via `defer_sqlite_xlsx_exports()` context manager (batches writes per daily run)

- **Research Ledgers**: `data/research/{sport}.xlsx` (sport-specific predictions)
- **Gated Research**: `data/gated_research/{sport}.xlsx` (curated esports/research subset)
- **Model Ledgers**: `data/model_ledgers/{model_id}.xlsx` (new architecture, additive per model)

**File Storage:**

- **Local filesystem** - No cloud object storage; all data lives on disk
- Directory structure:
  - `data/odds/` - Polymarket BBO snapshots (JSONL)
  - `data/historical/` - Ingested game results (JSONL)
  - `data/providers/` - Raw provider cache with hash-stamped snapshots
  - `data/features/` - Computed feature vectors (Parquet format)
  - `data/archive/` - Settled/removed picks (git-tracked evidence)
  - `data/snapshots/` - Point-in-time backups (git-tracked)
  - `data/logs/` - Daily pipeline logs, supervisor logs
  - `data/rebuild/` - Shadow rebuild intermediate state (untracked)

**Caching:**

- In-memory: None (no Redis, Memcached, or similar)
- File-based: Snapshot JSONL files serve as cache for provider data
  - Polymarket snapshots: `data/odds/{sport}/{date}/polymarket_snapshots.jsonl`
  - Provider raw: `data/providers/{provider}/{sport}/raw/{date}/...`
- Hash-stamped snapshots prevent re-processing identical responses

## Authentication & Identity

**Auth Provider:**

- No OAuth or third-party identity provider
- Custom credential model per integration:
  1. Polymarket US: ED25519-signed requests with API key ID + private key
  2. API-Football: Header-based API key
  3. Kalshi (deferred): API key + secret key
  4. Others: Mostly public endpoints (no auth required)

**Implementation:**

- `src/model_prediction/data_sources/polymarket_execute.py` - ED25519 signature generation and authenticated order submission
- `.env` file (gitignored) loaded at module import time in `polymarket_execute.py`
- Environment variables read explicitly (never fallback to file, but file is convenience for local dev)
- Dashboard token-based per-session auth: `dashboard_server.py` validates tokens for real-money execution endpoint

## Monitoring & Observability

**Error Tracking:**

- None (no Sentry, Rollbar, or similar)
- All errors logged to files or printed to stdout/stderr

**Logs:**

- **Daily pipeline**: `data/logs/daily_<date>.log` (run outcome, step results, errors)
- **Supervisor logs**: `data/logs/supervisor/{run_id}.log` (job scheduling, lease state, exit codes)
- **Dashboard logs**: Streamed to console (no persistent file by default)
- **Provider snapshots**: Hash-stamped JSONL captures for audit trail
- Logging framework: Python `logging` module with file/console handlers

**Audit Trail:**

- Append-only `audit.json` files per ledger tier (Main, Flat, Research, Gated Research)
- Captures all mutations: append, settle, void, archive, remove, update
- Separate from XLSX/SQLite to enable reconstruction if crash occurs mid-write

**Health Checks:**

- `src/model_prediction/system_health.py` - Queries `data/runs.db` for job status
- Reports: DOWN / DEGRADED / HEALTHY with reasons (e.g., "last job failed", "last job > 24h old")
- No external monitoring service; health status queryable via CLI

## CI/CD & Deployment

**Hosting:**

- Bare macOS machine (local development or dedicated ops Mac)
- No cloud hosting (AWS, GCP, Azure)
- launchd scheduler: 4x daily jobs (00:30, 06:00, 12:00, 18:00 UTC)

**CI Pipeline:**

- GitHub Actions: `.github/workflows/ci.yml`
- Triggers: Push to any branch, PR to main
- Steps: ruff lint (code style), mypy (type checking), pytest (2,205 tests)
- Status: All green (clean lint, full type coverage, all tests passing)
- No code deployment; repository is source of truth

**Job Scheduling:**

- launchd (macOS native):
  - `com.modelprediction.daily` - Main daily pipeline (4x/day)
  - `com.modelprediction.production` - Production order submission (triggered by daily or dashboard)
  - `com.modelprediction.rebuild-shadow` - Shadow rebuild validation (research-only, no production write)
- All jobs run through `src/model_prediction/run_supervisor.py` (unified scheduler)
- Lock file: `data/locks/daily.lock` (fcntl-based, prevents concurrent runs)

## Environment Configuration

**Required env vars:**

- `POLYMARKET_API_KEY_ID` - Polymarket US API key ID
- `POLYMARKET_PRIVATE_KEY` - Polymarket US ED25519 private key (PEM format)
- `API_FOOTBALL_KEY` - API-Football v3 key (optional if soccer scoring is disabled)

**Optional env vars:**

- `MODEL_PREDICTION_RUNTIME_ROOT` - External mutable state location (default: `data/`)
- `MODEL_PREDICTION_LEDGER_AUTHORITY` - `sqlite` (default) or `xlsx`
- `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY` - Kalshi (deferred)

**Secrets location:**

- `.env` file (gitignored, never committed, loaded at module import)
- Environment variables (set by launchd `.plist` files or shell profile)
- No external secret manager (Vault, AWS Secrets Manager, etc.)

**Config files:**

- `config/production.yaml` - Production model registry, champion selections, execution gates
- `config/model.yaml` - Feature lists, model metadata
- Both loaded via `src/model_prediction/config.py::load_config()`

## Webhooks & Callbacks

**Incoming:**

- None (system is pull-based, no webhook endpoints exposed)

**Outgoing:**

- None (system does not post to external webhooks)

**Order Submission:**

- Polymarket: Direct REST API POST to `https://api.polymarket.us/` (no callback; orders are queried synchronously)
- Order state machine: `OPEN` → (`FILLED` | `PARTIAL` | `CANCELLED` | `REJECTED`) queried via order-status endpoint

---

*Integration audit: 2026-09-20*
