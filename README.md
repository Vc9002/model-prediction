# model-prediction

Shadow-first multi-sport prediction, research, ledger, and local dashboard
system with Polymarket US market-data integration.

**Last updated**: 2026-08-05 | **748 tests pass, 0 fail** (699 legacy + 49 rebuild) | **Python 3.14**

> **This document is the AI-ready master reference.** It contains every fact
> needed to understand, rebuild, or improve this project. Supporting detail
> lives in [`MASTER.md`](MASTER.md) (running bug-fix log, 841 lines),
> [`DEBUG.md`](DEBUG.md) (full audit history, 2,993 lines),
> [`docs/`](docs/) (architecture, roadmap, ledger routing), and
> [`config/model.yaml`](config/model.yaml) (the single source of config truth).

The checkout is **not release-ready** for real-money trading. The real-money
execution surface is blocked on execution-binding and ledger/audit transaction
defects — see [Known Issues](#known-issues--technical-debt).

---

## Table of Contents

1. [Current State](#current-state)
2. [Architecture Overview](#architecture-overview)
3. [Complete File Map](#complete-file-map)
4. [Model Catalog](#model-catalog)
5. [Data Pipeline](#data-pipeline)
6. [Ledger System](#ledger-system)
7. [CLI Command Reference](#cli-command-reference)
8. [Configuration Reference](#configuration-reference)
9. [Dashboard](#dashboard)
10. [Setup from Scratch](#setup-from-scratch)
11. [Testing](#testing)
12. [Known Issues & Technical Debt](#known-issues--technical-debt)
13. [Improvement Roadmap](#improvement-roadmap)
14. [AI Rebuild Instructions](#ai-rebuild-instructions)
15. [Documentation Index](#documentation-index)

---

## Current State

| Metric | Value |
|---|---|
| Tests | **699 pass, 0 fail** (75 test files) |
| Ruff | ~118 findings (79 EXE002 shebang, baseline ~117) |
| Git | `main` (legacy) + `rebuild/clean-slate-v1` (new platform) |
| Rebuild tests | **49 pass, 0 fail** |
| CI | `.github/workflows/ci.yml` — ruff + pytest on push/PR |
| Python | 3.11+, managed via `.venv` |
| Release status | **Blocked** — P0 defects in execution-binding, ledger/audit transactions |

---

## Architecture Overview

### Decision Flow (end to end)

```
┌─────────────────────────────────────────────────────────────┐
│                      DATA SOURCES                           │
│  ESPN (scores/schedule)    Polymarket US (prices/resolution) │
│  bo3.gg (esports)          KBO/NPB scrapers                  │
│  The Odds API (sportsbooks)  Sackmann CSV (tennis history)   │
│  MLB StatsAPI (deep stats)  Football-Data (soccer enrich)    │
└──────────┬──────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────┐    ┌──────────────────────────┐
│   INGESTION       │    │     HISTORICAL BACKFILL    │
│ data/raw/{sport}/ │    │ data/historical/           │
│ {date}/*.json     │    │ {sport}_games_all.jsonl   │
│ (immutable cache) │    │ (append-only, deduped)    │
└────────┬─────────┘    └────────────┬─────────────┘
         │                           │
         └───────────┬───────────────┘
                     ▼
┌──────────────────────────────────────────────────────────────┐
│                  FEATURE COMPUTATION                          │
│  FeatureStore.games_before() — point-in-time, no lookahead   │
│  Elo ratings, trend engine, park factors, pitcher ERA gap,   │
│  starter history, player availability, schedule load,         │
│  bullpen profiles, weather, tennis surface Elo                │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│                     PER-SPORT MODELS                          │
│  ┌──────────────┐ ┌────────────┐ ┌──────────────────┐        │
│  │ learned_      │ │ models/    │ │ soccer/tennis/    │        │
│  │ forward.py    │ │ mlb.py     │ │ esports/          │        │
│  │ Elo+trend LR  │ │ Measured   │ │ international_    │        │
│  │ (MLB/NBA/WNBA │ │ Edge       │ │ baseball.py       │        │
│  │  /NFL)        │ │ (spread/   │ │ (specialized)     │        │
│  │               │ │  totals)   │ │                   │        │
│  └──────┬───────┘ └─────┬──────┘ └────────┬──────────┘        │
└─────────┼───────────────┼─────────────────┼──────────────────┘
          │               │                 │
          ▼               ▼                 ▼
┌──────────────────────────────────────────────────────────────┐
│                       GATES                                   │
│  1. candidate.call — model's own confidence threshold         │
│  2. ask-edge gate — model_prob - executable_ask >= min_edge   │
│  3. eligibility.py — trust-boundary checks only               │
│     (banned teams, stale data, model validation/provenance)   │
│  4. evaluate_gated_research_eligibility — extra filter for    │
│     Research→Gated Research promotion                         │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│                    LEDGER ROUTING                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐    │
│  │ Main     │ │ Flat     │ │ Research │ │ Gated        │    │
│  │ data/    │ │ data/    │ │ data/    │ │ Research     │    │
│  │ main/    │ │ flat/    │ │ research/│ │ data/gated_  │    │
│  │ {sport}. │ │ {sport}. │ │ {sport}. │ │ research/    │    │
│  │ xlsx     │ │ xlsx     │ │ xlsx     │ │ {sport}.xlsx │    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘    │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│               SETTLEMENT & REVIEW                             │
│  cli settle — grade via ESPN scores / Polymarket resolution   │
│  cli review-loss — classify cause                             │
│  cli update-closing — attach closing lines for CLV            │
│  cli verify-chain — audit log integrity check                 │
└──────────────────────────────────────────────────────────────┘
```

### Key Design Principles

- **Market isolation**: market prices NEVER enter any independent outcome model.
  Market residual/decision layers are separate and labeled.
- **Point-in-time only**: no lookahead. Features computed from data available
  strictly before event start.
- **Versioned artifacts**: every model version has a SHA-256-hashed JSON artifact
  in `config/models/`. Old versions are never overwritten.
- **Walk-forward validation**: chronological split: 60% train, 20% threshold
  selection, 20% locked holdout.
- **Audit chain**: every ledger mutation appends to `data/events.jsonl` first.
  `cli verify-chain` replays and detects tampering.
- **No fabricated contracts**: spread, total, F5, YRFI/NRFI markets are only
  created when real, exact historical lines exist.
- **Operator-filter, not model-filter**: Main ledger shows everything eligible;
  the operator decides what to act on.

---

## Complete File Map

### Source (`src/model_prediction/`)

```
src/model_prediction/
├── __init__.py                 # Package: v0.6.0
├── cli.py                      # ⚠ 4,411 lines — 48 subcommands, near-zero test coverage
├── dashboard_server.py         # ⚠ ~4,800 lines — monolithic
│
├── domain.py                   # Core types: League, MarketType, PickRequest, PickStatus, etc.
├── config.py                   # Config loading, validation, path resolution
│
├── ledger.py                   # PickLedger: xlsx-backed, audit-chained (1,485 lines)
├── model_ledger.py             # ModelLedger: new per-model-identity ledger (586 lines)
├── xlsx_ledger.py              # Low-level xlsx read/write with atomic rename
├── audit.py                    # AuditLog: JSONL chain with fcntl locking
│
├── pricing.py                  # Odds conversion, no-vig normalization, pick grading
├── units.py                    # Edge-scaled Kelly sizing, Exposure tracking
├── eligibility.py              # Trust-boundary checks, CALL/NO_CALL decisions
├── calibration.py              # Platt scaling, identity calibration, Brier
├── validation.py               # Qualification: thresholds, monthly grading, holdout
├── lifecycle.py                # Model state machine: can_create_qualified_call, etc.
│
├── learned_forward.py          # Elo+trend LR moneyline for MLB/NBA/WNBA/NFL (694 lines)
├── forward.py                  # MLB Measured Edge paired slate builder (211 lines)
├── soccer_forward.py           # Soccer Poisson/Dixon-Coles forecast (563 lines)
├── tennis_forward.py           # WTA+ATP surface Elo forecast (314 lines)
├── esports.py                  # Platt-scaled neutral Elo for 5 esports titles (1,156 lines)
├── international_baseball.py   # Tie-aware Elo for KBO/NPB (1,186 lines)
│
├── ingest.py                   # Raw cache + processed JSONL + historical bootstrap
├── backtester.py               # Walk-forward chronological backtester (797 lines)
├── entities.py                 # CanonicalTeam entity registry
├── bans.py                     # TeamBanList for trust-boundary checks
├── main_ledgers.py             # Per-sport Main/Flat ledger paths
├── research_ledgers.py         # Per-sport Research/Gated Research paths
├── research_io.py              # Atomic write, backup, canonical JSON, SHA-256
├── research_cleanup.py         # Cleanup utilities
├── source_policy.py            # Data source policy
├── daily_lock.py               # Daily pipeline file lock
├── feature_contract.py         # Feature contract definitions
├── total_score.py              # Total score modeling
├── economic_gate.py            # Economic gate thresholds
├── experiment_design.py        # Experiment design utilities
├── point_in_time.py            # Point-in-time snapshot utilities
├── mlb_baseline_refresh.py     # MLB baseline refresh automation
├── roadmap_challenger.py       # Roadmap challenger utilities
├── verification_checklist.py   # Automated checklist verification
├── wnba_availability_evaluation.py  # WNBA player availability impact
├── production_feature_ablation.py   # Production feature ablation testing
│
├── models/                     # Per-sport model implementations
│   ├── base.py                 # GamePrediction, ScoreSimulation, ScoreModel protocol
│   ├── registry.py             # MODEL_SPECS for every sport
│   ├── mlb.py                  # Trend Engine + MeasuredEdgeMargin/Totals (647 lines)
│   ├── soccer.py               # Poisson-Dixon-Coles goal model (213 lines)
│   ├── tennis.py               # Surface-blended Elo
│   ├── basketball.py           # NBA/WNBA shared model
│   ├── nba.py                  # NBA-specific model
│   ├── wnba.py                 # WNBA-specific model
│   ├── nfl.py                  # NFL-specific model
│   ├── learned_market.py       # LearnedMarketArtifact: LR artifact loader
│   └── market_residual.py      # MarketResidualModel: market-adjusted layer
│
├── features/                   # Feature computation (no market prices)
│   ├── base.py                 # FeatureStore: games_before(), GameRecord
│   ├── elo_ratings.py          # Chronological Elo ratings
│   ├── trends.py               # TrendEngine: EWMA momentum, opponent-adjusted
│   ├── park_factors.py         # MLB park factors
│   ├── weather.py              # Weather factors
│   ├── schedule_load.py        # Rest days, back-to-back, games-in-7
│   ├── starter_history.py      # Per-starter rolling ERA/FIP from MLB StatsAPI
│   ├── starting_pitcher.py     # Starting pitcher identification
│   ├── bullpen.py              # Bullpen profiles and recent relief lines
│   ├── team_runs.py            # Team-level rolling runs-allowed
│   ├── head_to_head.py         # Head-to-head history
│   ├── lineup_strength.py      # Lineup strength estimation
│   ├── player_availability.py  # General player availability
│   ├── mlb_player_availability.py  # MLB-specific IL/roster tracking
│   └── tennis_surface.py       # Tennis surface-specific features
│
└── data_sources/               # External API clients
    ├── espn.py                 # ESPN public scoreboards + market parsing
    ├── espn_probables.py       # ESPN probable starter snapshots
    ├── espn_wnba_injuries.py   # ESPN WNBA injury reports
    ├── polymarket_us.py        # Polymarket US gateway client (740 lines)
    ├── polymarket_execute.py   # Real-money order execution (⚠ gated)
    ├── mlb_market_odds.py      # MLB market odds snapshot store
    ├── mlb_statsapi.py         # MLB StatsAPI deep data
    ├── mlb_injuries.py         # MLB injury reports
    ├── wnba_injuries.py        # WNBA injury reports
    ├── the_odds_api.py         # The Odds API sportsbook lines
    ├── sportsdataio.py         # SportsDataIO (optional upgrade)
    ├── odds_soccer_scores.py   # Soccer scores from odds feed
    ├── football_data.py        # Football-Data.org soccer enrichment
    ├── tennis_sackmann.py      # Sackmann tennis CSV archive
    └── kalshi.py               # Kalshi (deferred, requires US residency)
```

### Data (`data/`)

```
data/
├── raw/{sport}/{date}/         # Immutable raw API responses
├── processed/{sport}/games.jsonl         # Normalized, deduped by event_id
├── historical/{sport}_games_all.jsonl    # Complete historical record (append-only)
├── main/{sport}.xlsx           # Main ledger — per-sport since 2026-08-03
├── flat/{sport}.xlsx           # Flat ledger — per-sport, every candidate
├── research/{sport}.xlsx       # Research ledger — per-sport, research-only sports
├── gated_research/{sport}.xlsx # Gated Research — curated subset
├── model_ledgers/{model-id}.xlsx  # New per-model-identity ledger
├── odds/{sport}/{date}/        # Polymarket daily snapshots
├── events.jsonl                # Audit chain (append-only)
├── audit_log.jsonl             # Audit log
├── polymarket_us_snapshots.jsonl  # Full Polymarket snapshot archive
├── market_odds_snapshots.jsonl    # MLB market odds snapshot archive
├── entities/teams.json         # CanonicalTeam entity registry
├── esports/{title}/            # Esports match history + manifest
├── international_baseball/     # KBO/NPB match history
├── features/                   # Point-in-time feature snapshots
├── point_in_time/              # PIT snapshot archive
├── player_priors/              # Player prior distributions
├── availability/               # Player availability snapshots
├── mlb_statsapi/               # MLB StatsAPI game snapshots
├── statsapi/                   # StatsAPI cache
├── logs/daily_{date}.log       # Daily pipeline output
├── locks/                      # Pipeline lock files
└── archive/                    # Archived data
```

### Configuration

```
config/
├── model.yaml                  # ⚠ 522 lines — single source of config truth
└── models/                     # Versioned model artifacts (JSON, SHA-256 hashed)
    ├── mlb-elo-trend-lr-v7.json
    ├── mlb-elo-trend-lr-v8.json
    ├── measured-edge-margin-v3.json
    ├── measured-edge-totals-v3.json
    ├── nba-elo-trend-lr-v4.json
    ├── wnba-elo-trend-lr-v4.json
    ├── nfl-elo-trend-lr-v4.json
    └── ...
```

### Scripts

```
scripts/
├── run_daily.sh                # Locked daily pipeline: settle → ingest → forecast
├── install_dashboard_service.sh # macOS launchd service installer
├── migrate_to_model_ledgers.py  # Migration to new ModelLedger
├── migrate_to_per_sport_ledgers.py  # Migration to per-sport Main/Flat
├── mlb_measured_edge_compare_settled.py  # Compare settled Measured Edge picks
├── mlb_measured_edge_calibrate.py        # Calibrate Measured Edge model
├── mlb_elasticity_refit.py               # Refit elasticities
├── esports_analysis.py                   # Esports analysis tools
└── clean_split_research_ledgers.py       # Clean up split research ledgers
```

---

## Model Catalog

### MLB Moneyline
- **Module**: `learned_forward.py`
- **Method**: Elo ratings + exponentially-weighted trend engine features →
  logistic regression probability
- **Active artifact**: `mlb-elo-trend-lr-v8` (operator-directed promotion)
- **Status**: `shadow_qualified` (override — artifact's own `qualified=false`)
- **Key features**: `elo_probability`, `trend_gap`, `defensive_trend_gap`,
  `starter_era_gap` (v8: real per-starter ERA replacing v7's team-level
  `pitcher_era_gap`), `bullpen_weakness_gap`, `park_factor`, player
  availability
- **Validation**: 60.81% hit rate on locked holdout (148 calls), but Brier
  regressed vs v7 (0.24702 vs 0.24655). v7 available as rollback.
- **Min edge**: 5% (vig-inclusive ask edge)
- **Ledger routing**: Main + Flat

### MLB Spread (Measured Edge Margin)
- **Module**: `models/mlb.py` → `MeasuredEdgeMarginModel`
- **Method**: Trend Engine score simulation → Gamma-Poisson mixture Monte Carlo →
  margin probability priced against real Polymarket lines
- **Active artifact**: `measured-edge-margin-v3`
- **Status**: `active_research` (real, sized Main-ledger rows, gated on
  confirmed starters)
- **Key features**: offense/starter/bullpen/park/weather elasticities
- **Validation**: 60.0% hit rate, diagnostic correlation 0.208
- **Ledger routing**: Main + Flat

### MLB Totals (Measured Edge Totals)
- **Module**: `models/mlb.py` → `MeasuredEdgeTotalsModel`
- **Method**: Same Trend Engine simulation → Gamma-Poisson Monte Carlo →
  total probability priced against real Polymarket lines
- **Active artifact**: `measured-edge-totals-v3`
- **Status**: `active_research`
- **Known issue**: Totals head doesn't benefit from elasticity refit the way
  margin does. Diagnostic correlation regressed (0.0585→0.0414). Separate
  absolute run-intensity head planned.
- **Ledger routing**: Main + Flat

### NBA Moneyline
- **Module**: `learned_forward.py`
- **Method**: Elo + trend logistic regression
- **Active artifact**: `nba-elo-trend-lr-v4`
- **Status**: `shadow_qualified` (registered but not promoted to Main)
- **Key features**: `elo_probability`, `trend_gap`, `defensive_trend_gap`,
  player availability
- **Regression**: 35% Elo regression to mean (NBA-specific)
- **Ledger routing**: Flat only

### WNBA Moneyline
- **Module**: `learned_forward.py`
- **Method**: Elo + trend logistic regression
- **Active artifact**: `wnba-elo-trend-lr-v4`
- **Status**: `shadow_qualified`
- **Key features**: Same as NBA but WNBA constants, 40% Elo regression
- **Known issue**: 78.3% total baseline suspiciously high
- **Ledger routing**: Main + Flat

### NFL Moneyline
- **Module**: `learned_forward.py`
- **Method**: Elo + trend logistic regression
- **Active artifact**: `nfl-elo-trend-lr-v4`
- **Status**: `shadow_qualified` (offseason, not promoted to Main)
- **Key features**: 50% Elo regression (largest carry-over)
- **Ledger routing**: Flat only

### Soccer
- **Module**: `soccer_forward.py` + `models/soccer.py`
- **Method**: Independent Poisson goal model with Dixon-Coles low-score
  correction (ρ=-0.10, home boost 1.15)
- **Active artifact**: `soccer-poisson-dc-v1`
- **Status**: `research` (Main-entry eligible via gated-research gate)
- **Markets**: 3-way moneyline (home/draw/away), O/U 2.5 total, BTTS
  (Platt-calibrated separately)
- **Key features**: EWMA attack/defense strengths shrunk toward league mean
- **Coverage**: 29 of 64 gateway leagues priced
- **Critical note**: Soccer draws are full LOSSES (unlike KBO/NPB which
  settle at 50¢). Three independent Yes/No Polymarket contracts.
- **Ledger routing**: Main + Flat

### Tennis
- **Module**: `tennis_forward.py` + `models/tennis.py`
- **Method**: Surface-blended Elo (60% surface-specific, 40% overall), WTA+ATP
- **Active artifact**: `tennis-surface-elo-v1`
- **Status**: `research` (Main-entry eligible via gated-research gate)
- **Markets**: Moneyline only, singles only (doubles excluded at ingest)
- **Key features**: Overall Elo, per-surface Elo, singles-only ESPN history
- **ATP**: Added 2026-08-03 (was `no market` before)
- **Ledger routing**: Main + Flat

### Esports (LOL, CS2, Dota2, Valorant, Rainbow Six)
- **Module**: `esports.py`
- **Method**: Platt-scaled neutral series Elo (v5), venue-neutral
- **Data source**: bo3.gg API
- **Status**: `research` only
- **Key properties**: No home-field advantage, never pools titles, series is
  unit of observation
- **Confidence**: Thin-data matchups shrunk toward 0.5 (v6 fix)
- **Ledger routing**: Research + Gated Research

### KBO / NPB
- **Module**: `international_baseball.py`
- **Method**: Tie-aware Elo (v2)
- **Status**: `research` only (no Polymarket markets for execution)
- **Key property**: Decisive-result probability + independent tie probability,
  valued as P(win) + 0.5 × P(tie)
- **Data sources**: KBO: koreabaseball.com scraping, NPB: npb.jp HTML parsing
- **Ledger routing**: Research + Gated Research

---

## Data Pipeline

### 1. Data Sources

| Source | Sports | Type | Auth |
|---|---|---|---|
| ESPN public API | MLB, NBA, WNBA, NFL, Soccer, Tennis | Scores, schedules, standings | None |
| Polymarket US Gateway | All | Market prices, resolution | None |
| bo3.gg API | Esports (5 titles) | Match history, teams | Key (in config) |
| The Odds API | Soccer, US sports | Sportsbook lines | `THE_ODDS_API_KEY` |
| MLB StatsAPI | MLB | Deep stats, game snapshots | None |
| koreabaseball.com | KBO | Schedule, results | None (scraped) |
| npb.jp | NPB | Calendar, results | None (scraped) |
| Sackmann CSV | Tennis (WTA/ATP) | Historical match data | None (static) |
| Football-Data.org | Soccer | League enrichment | None |

### 2. Ingestion Pipeline

```
cli ingest --sport mlb --date 2026-08-04
```

1. Fetch ESPN scoreboard for date → cache raw JSON in `data/raw/{sport}/{date}/`
2. Parse completed games → append to `data/processed/{sport}/games.jsonl`
3. Append to `data/historical/{sport}_games_all.jsonl` (deduped by event_id)
4. Everything is idempotent — re-running is a no-op

Historical backfill:
```
cli bootstrap --sport mlb --from 2024-03-20 --to 2026-08-04
```
Fetches one date at a time, rate-limited (0.6s between calls).

### 3. Feature Computation

Point-in-time via `FeatureStore.games_before(sport, event_start)`:
- Returns all games strictly before the event's start time
- Features computed on-the-fly from this historical window
- No lookahead — dropping a feature snapshot at any time and recomputing
  gives identical values

Available features (by module):
- `elo_ratings.py`: Chronological Elo, expected home win
- `trends.py`: EWMA offensive/defensive momentum, consistency, hot/cold
- `park_factors.py`: MLB park effects
- `weather.py`: Game-time weather factors
- `schedule_load.py`: Rest days, back-to-back, games in last 7
- `starter_history.py`: Per-starter rolling ERA/FIP from boxscore history
- `team_runs.py`: Team-level rolling runs-allowed
- `bullpen.py`: Bullpen profiles, recent relief innings
- `player_availability.py`: General roster availability
- `mlb_player_availability.py`: MLB-specific IL/40-man tracking
- `head_to_head.py`: Head-to-head record
- `lineup_strength.py`: Estimated lineup quality
- `tennis_surface.py`: Surface-specific Elo components

### 4. Model Execution Flow (per candidate)

```
1. Model produces model_probability and model_uncertainty
2. candidate.call — model's internal confidence threshold
3. Ask-edge gate — model_prob - executable_ask >= min_edge (vig-inclusive)
4. evaluate_eligibility() — trust-boundary:
   - Banned team? → NO_CALL
   - Stale data? → NO_CALL
   - Model retired/unvalidated? → NO_CALL
   - Missing provenance (hash, revision)? → NO_CALL
   - Pass → CALL (disagreement/exposure no longer gate)
5. edge_scaled_units() — uncertainty-haircut sizing (1.0U–2.0U)
6. append_evaluated() → all applicable ledgers + audit event
```

### 5. Daily Pipeline (`scripts/run_daily.sh`)

```
┌─ acquire lock (exit 75 if busy) ─┐
│ Step 1: cli settle --all-unsettled        (idempotent)
│ Step 1b: cli ingest mlb/nba/wnba/nfl      (yesterday + today)
│ Step 2: cli daily --date --skip-settlement (unified slate)
└─ release lock ───────────────────┘
```

---

## Ledger System

### Four-Ledger Architecture

| Ledger | Location | Rule | Sports |
|---|---|---|---|
| **Main** | `data/main/{sport}.xlsx` | CALL decisions only | MLB, WNBA, Soccer, Tennis |
| **Flat** | `data/flat/{sport}.xlsx` | Every candidate, no gate | All learned sports |
| **Research** | `data/research/{sport}.xlsx` | All candidates (research sports) | LOL, CS2, Dota2, Valorant, R6, KBO, NPB |
| **Gated Research** | `data/gated_research/{sport}.xlsx` | CALL only (research sports) | Same as Research |

Split into per-sport files on 2026-08-03 (was two monolithic files).

### Model Ledger (New, Not Yet Cut Over)

`model_ledger.py` implements an additive per-model-identity architecture:
- One `.xlsx` per model identity (e.g. `mlb-moneyline-elo-trend-lr.xlsx`)
- No `model_state` or `record_type` classification field
- Model output block: immutable, never touched by operator action
- Operator decision block: separate columns, same row
- Written alongside PickLedger on every `append_evaluated`
- Migration scripts: `scripts/migrate_to_model_ledgers.py`

### Audit Chain

- `data/events.jsonl`: JSONL with SHA-256 chain of hashes
- Every ledger mutation appends to audit BEFORE writing the ledger row
- `cli verify-chain`: replays the log, confirms no row was mutated out-of-band
- `data/audit_log.jsonl`: separate audit log file
- Locked via `fcntl.flock(LOCK_EX)` with 30s timeout

### Settlement

```
cli settle --all-unsettled
```
- Grades every open pick across ALL ledgers
- US sports: ESPN scoreboards
- Esports/Soccer: Polymarket resolution
- Baseball ties (KBO/NPB): settles at 0.50 (push — 2-outcome market)
- Soccer draws: FULL LOSS (3 independent Yes/No contracts, not push)
- Expired non-binary Polymarket settlement → void
- `cli review-loss`: classify cause (bad_luck, missing_information, etc.)
- `cli update-closing`: attach closing lines for CLV without mutating original

---

## CLI Command Reference

Every command uses: `env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli <subcommand>`

### Core Pipeline

| Command | Purpose |
|---|---|
| `daily --date YYYY-MM-DD` | Full daily: settle + forecast + log |
| `daily --date YYYY-MM-DD --skip-settlement` | Forecast only (used after separate settle) |
| `settle --all-unsettled` | Grade every open pick across all ledgers |
| `ingest --sport mlb --date YYYY-MM-DD` | Fetch + cache ESPN scores |
| `ingest --sport all --date YYYY-MM-DD` | Ingest all US sports |

### Forecasting

| Command | Purpose |
|---|---|
| `forecast --all --date YYYY-MM-DD --log --replace-today` | Full Main ledger forecast |
| `flat-forecast --all --date YYYY-MM-DD --log` | Full Flat ledger forecast |
| `esports-forecast --all --date YYYY-MM-DD --log` | Esports Research/Gated forecast |
| `international-forecast --all --date YYYY-MM-DD --log` | KBO/NPB Research/Gated forecast |

### Backfill & Bootstrap

| Command | Purpose |
|---|---|
| `bootstrap --sport mlb --from YYYY-MM-DD --to YYYY-MM-DD` | Historical ESPN backfill |
| `bootstrap-entities` | Merge team lists into entity registry |
| `esports-backfill --title lol --from YYYY-MM-DD` | Esports match history backfill |
| `international-backfill --league kbo --from YYYY-MM-DD` | KBO/NPB historical backfill |

### Validation & Research

| Command | Purpose |
|---|---|
| `backtest --sport mlb --from YYYY-MM-DD --to YYYY-MM-DD` | Walk-forward chronological backtest |
| `validate --sport mlb` | Model validation against artifact |
| `total-validate` | Validate totals models |
| `verify-chain` | Replay audit log, confirm integrity |
| `score-research` | Compute reason/edge without logging |

### Ledger Management

| Command | Purpose |
|---|---|
| `void --pick-id <id>` | Void a pick |
| `review-loss --pick-id <id> --classification <cause>` | Classify a loss |
| `update-closing --pick-id <id> --closing-line -110` | Attach closing odds |
| `ban-team add --league MLB --team-id <id>` | Ban a team |
| `ban-team remove --league MLB --team-id <id>` | Unban a team |
| `ban-team list` | List all banned teams |

### Polymarket

| Command | Purpose |
|---|---|
| `polymarket-slate` | Capture current Polymarket BBOs |
| `polymarket-ledger-prices --date YYYY-MM-DD` | Refresh live quotes for open picks |
| `polymarket-clv` | Compute probability CLV |

### Execution (⚠ Heavily Gated, Currently Blocked)

| Command | Purpose |
|---|---|
| `execute` | Place a real-money Polymarket order |
| `sell-position` | Close an existing position |

### Other

| Command | Purpose |
|---|---|
| `features` | Compute point-in-time feature snapshot |
| `call` | Freeze one pre-game prediction manually |
| `collect-scores` | Pull recent soccer scores from The Odds API |

---

## Configuration Reference

All configuration is in `config/model.yaml` (522 lines). Key sections:

### `project`
```yaml
project:
  execution_mode: live_manual
  ledger_path: data/picks.xlsx           # Overridden by main_ledgers.py
  audit_path: data/events.jsonl
  polymarket_snapshot_path: data/polymarket_us_snapshots.jsonl
  market_odds_snapshot_path: data/market_odds_snapshots.jsonl
  entity_registry_path: data/entities/teams.json
  maximum_data_age_hours: 12
```

### `bankroll`
```yaml
bankroll:
  unit_value_usd: 5.00
  kelly_fraction: 0.5
  min_pick_units: 1.0          # Range widened 2026-07-31
  max_pick_units: 2.0
  max_daily_units: 5.0
  max_league_daily_units: 3.0
  max_event_units: 2.0
  max_team_daily_units: 3.0
  unit_increment: 0.25
  min_edge: 0.02
```

### `models.{SPORT}`
Each sport section contains:
- `status`: `research`, `shadow_candidate`, `shadow_qualified`, `degraded`, `suspended`, `retired`
- `origin`: `statistical_model`, `analyst_estimate`, `market_baseline`, `synthetic_test`
- `active_production_version`: artifact name
- `production_artifact`: path to JSON artifact
- `min_edge`: sport-specific edge threshold
- `iteration_policy`: continuous improvement rules

### Environment Variables (`.env`)
```
THE_ODDS_API_KEY=            # Required for soccer score collection
SPORTSDATAIO_API_KEY=        # Optional upgrade
MODEL_PREDICTION_CONFIG=config/model.yaml
MODEL_PREDICTION_ROOT=       # Override auto-detected project root
MODEL_PREDICTION_LEDGER=     # Override ledger path
MODEL_PREDICTION_AUDIT=      # Override audit path
POLYMARKET_KEY_ID=           # Required for real-money execution
POLYMARKET_SECRET_KEY=       # Required for real-money execution
POLYMARKET_PRIVATE_KEY=      # Required for real-money execution
POLYMARKET_WALLET_ADDRESS=   # Required for real-money execution
```

### Model Iteration Policy
```yaml
model_iteration_policy:
  status: continuous
  parameter_freezes_allowed: true
  require_versioned_change: true
  require_walk_forward_ablation: true
  require_locked_holdout_before_promotion: true
```

### Validation Gates
```yaml
selection_gate:
  target_called_hit_rate: 0.6
  minimum_locked_holdout_calls: 50
  qualification_metric: locked_holdout_hit_rate
  missing_artifact_action: no_call
```

---

## Dashboard

**Start**: `python3 dashboard_server.py` → `http://127.0.0.1:8765/`

The dashboard is a thin trigger+read layer. Every button shells out to the
exact same CLI commands used by the scheduled pipeline — one code path for
forecast and settlement regardless of invocation method.

### Dashboard Tabs
- **Main** — MLB, WNBA, Soccer, Tennis picks (CALL decisions)
- **Flat** — Every sport, every candidate (diagnostic baseline)
- **Research** — Esports + international baseball (all candidates)
- **Gated** — Curated subset of Research
- **Matrix** — Cross-sport view of all picks
- **Logs** — Daily pipeline logs
- **Tests** — Run pytest from dashboard
- **Execute** — Real-money order surface (authenticated, blocked)

### Architecture Issues
- `dashboard_server.py` is ~4,800 lines, monolithic
- `/api/scan` route calls nonexistent function → 500 on every request
- Startup uses `pkill -f` (forbidden — should be PID-file)
- No template separation (HTML inline)

---

## Setup from Scratch

### Prerequisites
- Python 3.11+
- macOS or Linux (uses `fcntl` for file locking)

### Step-by-Step

```bash
# 1. Clone and enter
cd "model prediction"

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -e ".[dev]"

# 4. Configure environment
cp .env.example .env
# Edit .env — at minimum, set THE_ODDS_API_KEY for soccer
# For real-money execution, also set POLYMARKET_* variables

# 5. Verify setup
env PYTHONPATH=src:. .venv/bin/python -c "from model_prediction.cli import main; print('OK')"

# 6. Run tests
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
# Expect: 699 passed

# 7. Bootstrap historical data (this takes a while)
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli bootstrap \
  --sport mlb --from 2024-03-20 --to $(date +%Y-%m-%d)

# 8. Bootstrap entities
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli bootstrap-entities

# 9. Run first daily forecast
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli daily \
  --date $(TZ=America/New_York date +%Y-%m-%d) --skip-settlement

# 10. Start dashboard (optional)
python3 dashboard_server.py
# Open http://127.0.0.1:8765/
```

### Install as Scheduled Job (macOS)
```bash
bash scripts/install_dashboard_service.sh
# Registers launchd plist for run_daily.sh
```

---

## Testing

```bash
# All tests
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q

# Specific test file
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_cli.py -q

# With coverage
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ --cov=src/model_prediction

# Lint
.venv/bin/ruff check src/ tests/
```

### Test Structure
- 75 test files, 699 tests, 0 failures
- Organization mirrors source: `test_cli.py`, `test_ledger.py`, `test_eligibility.py`, etc.
- Key gaps: `cli.py` has a dedicated test file but minimal coverage of 4,411-line module
- `dashboard_server.py` has tests but coverage is thin
- Conftest at `tests/conftest.py`

---

## Known Issues & Technical Debt

Prioritized by severity.

### P0 — Block Release
1. **Execution-ticket binding**: real-money order tickets don't atomically bind
   to ledger picks. A crash between order placement and ledger update creates
   orphaned positions with no shadow record.
2. **Non-atomic ledger/audit writes**: `ledger.py` appends to audit before
   writing the ledger row, but the two are separate files/locks. A crash
   between them creates audit events with no matching ledger rows. Recovery
   is theoretically possible but not automated.
3. **Non-PIT MLB v6 starter data**: v6 artifact used starter data that wasn't
   point-in-time (fixed in v7/v8, but historical picks logged against v6 have
   potential lookahead contamination).

### P1 — Active Bugs
4. **MLB ingest sometimes misses completed games**: ESPN API returns data but
   Ingestor doesn't process some events.
5. **`/api/scan` dashboard route broken**: calls nonexistent function
   `capture_snapshots` — raises `ImportError` on every request.
6. **WNBA total baseline 78.3% suspiciously high**: needs investigation.
7. **Audit chain reconciliation gap**: `verify-chain` confirms audit log
   integrity but doesn't cross-check ledger contents against it.
8. **`config/model.yaml` references `market-residual-v1.json`** which doesn't exist.
9. **`mlb-spread-baseline-v1.json` reused for both spread and total** research
   — should be separate artifacts.
10. **`nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json`** have
    mismatched canonical hashes.

### P2 — Architecture & Code Quality
11. **`cli.py` (4,411 lines)**: 48 subcommands, zero dedicated test file
    (test_cli.py exists but minimal). Should be split into `cli/` package.
    See `MASTER.md` §DD-6.
12. **`dashboard_server.py` (~4,800 lines)**: monolithic. Recommended split
    into `dashboard/` package.
13. **12 orphaned source modules**: ~1,800 lines of dead code, never imported
    or tested. See `MASTER.md` for full list.
14. **Dashboard startup uses `pkill -f`**: explicitly forbidden by project
    instructions. Replace with PID-file approach.
15. **No pre-commit lint hook**: Ruff only runs in CI or manually.
16. **Multiple hardcoded thresholds**: `units.py` has `min_edge=0.02`,
    `unit_increment=0.25` as defaults but should read from config.
17. **Spread/total artifacts**: NBA/NFL have 0 snapshots (offseason). When
    seasons start, need 60+ days of data to train real spread/total models.
18. **Tennis FeatureStore incompatibility**: tennis rows use `winner`/`loser`
    schema, not `away_team`/`home_team`. `FeatureStore.load_games("tennis")`
    silently returns empty. Tennis forward reads raw JSONL directly instead.

### P3 — Nice to Have
19. **Migrate ledger storage to SQLite**: would give ACID guarantees, fix the
    atomicity problem, and enable real querying.
20. **Model Ledger migration**: cut over from PickLedger to ModelLedger as
    primary storage.
21. **Validate rest-flip filter**: verified profitable for WNBA/NFL, test NBA.
22. **NBA/NFL spread/total**: train real models when seasons start.
23. **MLB v8 re-validation**: artifact is `qualified=false` despite operator
    promotion. Need to either fix Brier regression or accept it.
24. **KBO/NPB Polymarket integration**: currently no markets exist, but when
    they do, wire into execution path.

---

## Improvement Roadmap

Ordered by impact and feasibility. Exact files to modify are noted.

### Phase 1: Critical Fixes (Must Do)

1. **Fix execution-ticket binding** (`data_sources/polymarket_execute.py`,
   `ledger.py`): make order placement atomic with ledger append, or implement
   recovery/verification that can detect orphaned orders.

2. **Fix `/api/scan` dashboard route** (`dashboard_server.py`): replace
   `capture_snapshots` import with real `capture_slate_snapshots` call using
   a live `PolymarketUSClient` and discovered events.

3. **Replace `pkill -f` with PID-file** (`dashboard_server.py`): write PID on
   startup, read/kill by PID on shutdown.

### Phase 2: Code Quality (High Impact)

4. **Split `cli.py` into package** (`src/model_prediction/cli/`):
   - `cli/__init__.py` — main entry, arg parsing
   - `cli/forecast.py` — forecast, flat-forecast, esports-forecast, etc.
   - `cli/settle.py` — settle, void, review-loss, update-closing
   - `cli/backfill.py` — bootstrap, ingest, esports-backfill
   - `cli/validate.py` — backtest, validate, verify-chain
   - `cli/execute.py` — execute, sell-position
   - `cli/dashboard_triggers.py` — dashboard button commands

5. **Split `dashboard_server.py` into package** (`src/model_prediction/dashboard/`):
   - `dashboard/server.py` — Flask/FastAPI app
   - `dashboard/routes.py` — route definitions
   - `dashboard/templates/` — HTML templates
   - `dashboard/static/` — CSS/JS assets

6. **Remove orphaned source modules**: identify and delete all 12 dead modules
   (~1,800 lines). Run full test suite after each removal.

7. **Add pre-commit lint hook**: `.pre-commit-config.yaml` with ruff.

### Phase 3: Architecture Improvements

8. **Migrate ledger to SQLite**:
   - Design schema matching current xlsx columns
   - Write migration script for existing data
   - Implement ACID transactions for ledger+audit writes
   - Replace `PickLedger` with SQLite-backed implementation
   - This fixes the P0 atomicity issue

9. **Cut over to ModelLedger**:
   - Complete `scripts/migrate_to_model_ledgers.py`
   - Wire ModelLedger into daily pipeline alongside PickLedger
   - After validation period, make ModelLedger primary

10. **Consolidate hardcoded thresholds**:
    - Move `UNIT_MIN_EDGE`, `UNIT_INCREMENT` from `config.py` module-level
      constants into `config/model.yaml`
    - Read them in `units.py` from config, not from defaults
    - Add config validation for all thresholds

### Phase 4: Model Improvements

11. **MLB v8 re-validation**: resolve Brier regression (0.24702 vs v7's
    0.24655). Options: add interaction terms, try different calibration,
    or accept and document the trade-off (higher hit rate at cost of
    Brier quality).

12. **MLB totals head**: implement separate absolute run-intensity head
    (`models/mlb.py`) — the current elasticity refit improved margin but
    hurt totals. See `config/model.yaml` for the documented plan.

13. **NBA/NFL spread/total**: when seasons start, accumulate 60+ days of
    Polymarket snapshots, then train real spread/total models using the
    same Measured Edge architecture as MLB.

14. **WNBA total baseline audit**: investigate the 78.3% baseline. If
    it's real signal, promote. If it's a data artifact, fix.

15. **Tennis ATP validation**: ATP was just wired (2026-08-03). Run
    walk-forward validation on the ATP-specific Elo path.

16. **Soccer league coverage**: currently 29 of 64 gateway leagues. Expand
    to all 64 that Polymarket lists.

### Phase 5: Operations

17. **CI improvements**: add test coverage reporting, add mypy to CI,
    add dashboard smoke test.

18. **Monitoring**: add health-check endpoint to dashboard, add alerting
    for failed daily runs, add game-count growth monitoring.

19. **Model registry UI**: add dashboard page showing all model versions,
    their status, validation metrics, and qualification evidence.

---

## AI Rebuild Instructions

If you're an AI agent rebuilding or improving this project, read these docs
**in order** before making any changes:

1. **This README** — architecture, models, data flow, known issues
2. **`docs/PROJECT_STATUS.md`** — current operational verdict, active versions
3. **`docs/ARCHITECTURE.md`** — durable architecture contract, invariants
4. **`docs/LEDGER_ROUTING.md`** — which sport goes into which ledger
5. **`docs/ENGINEERING_ROADMAP.md`** — prioritized code quality items
6. **`docs/AGENTS.md`** — execution rules (walk-forward only, protected files)
7. **`docs/FEATURE_REGISTRY.md`** — what's been tested, what must not be re-tested
8. **`docs/AI_REBUILD_GUIDE.md`** — step-by-step rebuild instructions
9. **`MASTER.md`** — running log of every bug found/fixed (841 lines)
10. **`DEBUG.md`** — full audit history, reproduction commands (2,993 lines)
11. **`config/model.yaml`** — single source of config truth (522 lines)
12. **`CLAUDE.md`** — working guidelines auto-loaded into every session

### Critical Rules (from `docs/AGENTS.md`)

- **Walk-forward only**: never let features from `t > T` leak into a prediction at `T`
- **Locked holdout**: never peek at holdout data during model development
- **Never hardcode weights or thresholds**: load from hash-verified artifacts
- **Protected files**: NBA model, WNBA model, `data/historical/*`,
  every file in `config/models/*`
- **New versions alongside old**: never overwrite or delete rollback artifacts
- **Reject retrospective features** that can't prove observability before event start
- **Don't invent contracts**: no spread/total/F5/YRFI without exact historical lines
- **Shadow calls are not orders**: never log or execute during model validation
- **Market isolation**: market prices never enter outcome models

---

## Rebuild Platform (branch: `rebuild/clean-slate-v1`)

A clean-slate rebuild of the data platform and model architecture. The legacy
system is frozen as a benchmark; the rebuild is a separate, independently
testable system.

### Architecture (18 modules, 49 tests)

| Layer | Modules | What |
|---|---|---|
| **Medallion Storage** | `storage.py` | RawStore (immutable gzip JSONL), NormalizedStore (Parquet + DuckDB), FeatureStore (PIT snapshots), MarketStore (timestamped BBOs). 11 provenance columns. |
| **Metadata** | `metadata.py` | SQLite DB — 14 sources, 51 source-sport mappings, hash-chained audit trail |
| **Identity** | `identity.py` | CanonicalIdentity with effective dates, Jaccard fuzzy matching (fails closed <90%) |
| **Collectors** | `collectors.py` | MLB (ESPN + pybaseball + Open-Meteo + Polymarket) + NBA/NFL/soccer/tennis/esports stubs |
| **Validation** | `validation.py` | Nested chronological CV, expanding/rolling folds, date-cluster bootstrap, log loss/Brier/ECE |
| **Models** | `models/` | MLB two-head (intensity + differential), NBA possessions×efficiency, NFL drive-based, soccer dynamic Dixon-Coles, tennis serve/return Elo, esports per-title, KBO/NPB tie-aware |
| **Calibration** | `calibration.py` | Platt, isotonic, temperature scaling — all on OOF data |
| **Ensemble** | `ensemble.py` | Equal-weight, inverse-log-loss, nonnegative logistic stacking |
| **Market Residual** | `market_residual.py` | LR on market-side features, cost-adjusted executable edge |
| **Economics** | `economic.py` | Kelly sizing, Exposure tracker, portfolio eval with bootstrap CIs, 8 health states |
| **Horizons** | `horizons.py` | Early/mid/late feature schemas per sport, horizon separation validation |
| **Missingness** | `missingness.py` | FeatureRecord with availability/source/age/reason, beta-binomial shrinkage, empirical Bayes |
| **XGBoost/Stress** | `xgboost_stress.py` | Conservative XGBoost challenger, 13 stress scenarios with pass/fail verdicts |
| **Tests** | `tests/test_rebuild.py` | 49 tests — leakage, identity, calibration, ensemble, stress, monitoring, horizons |

### Quick Start (Rebuild)

```bash
git checkout rebuild/clean-slate-v1
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_rebuild.py -q
# 49 passed

# MLB collector end-to-end
env PYTHONPATH=src:. .venv/bin/python -c "
from model_prediction.rebuild import MLBCollector, MetadataDB
meta = MetadataDB('data/rebuild/metadata.db')
c = MLBCollector('data/rebuild', meta)
print(c.collect_espn_scoreboard('2026-08-05'))
"
```

See [`docs/REBUILD_PLAN.md`](docs/REBUILD_PLAN.md) for the full 3-part rebuild specification.

---

## Documentation Index

| Document | Purpose |
|---|---|
| `README.md` | **You are here** — AI-ready master reference |
| `MASTER.md` | Running bug-fix log, every issue found with full evidence (841 lines) |
| `DEBUG.md` | Full audit history, reproduction commands for every bug (2,993 lines) |
| `CHECKLIST.md` | Daily/weekly/monthly maintenance checklist |
| `CLAUDE.md` | Working guidelines auto-loaded into every Claude session |
| `docs/PROJECT_STATUS.md` | Operational status, active model versions, release verdict |
| `docs/ARCHITECTURE.md` | Durable architecture contract, validation contract, invariants |
| `docs/LEDGER_ROUTING.md` | Per-sport ledger routing rules with code references |
| `docs/ENGINEERING_ROADMAP.md` | Code quality, dashboard gaps, portfolio-layer roadmap |
| `docs/MODEL_IMPROVEMENTS.md` | Per-sport feature roadmap |
| `docs/HISTORY.md` | Chronological project history, all phases |
| `docs/AGENTS.md` | Execution rules for AI agents (walk-forward, protected files, etc.) |
| `docs/FEATURE_REGISTRY.md` | What's been tested, what must not be re-tested |
| `docs/CHANGELOG.md` | Version history |
| `docs/AI_REBUILD_GUIDE.md` | Step-by-step rebuild instructions for AI agents |
| `docs/EVAL_METHODOLOGY_BRIEF.md` | Evaluation methodology |
| `docs/PROMPT.md` | Project prompt/context |
| `docs/TODO.md` | Open to-do items |
| `config/model.yaml` | Single source of config truth (522 lines) |
| `pyproject.toml` | Python project metadata, dependencies, tool config |
| `.env.example` | Environment variable template |
