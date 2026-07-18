# model-prediction — v3.0

Shadow-first multi-sport prediction engine with Polymarket US integration.
5 production moneyline models, 1 post-hoc filter, 4 research spread/total baselines.

## Production Models

| League | Features | Gate | Holdout | Calls | Hit% | Units | Rest Flip |
|--------|----------|------|---------|-------|------|-------|-----------|
| MLB | elo, trend, park, weather, pitcher | 60.0% | 1,353 | 100 | 66.0% | +26.00 | — |
| WNBA | elo, trend, defense | 50.0% | 172 | 172 | 64.5% | +39.91 | ✅ +10 |
| NBA | elo, trend, defense | 62.0% | 662 | 414 | 78.7% | +208.36 | — |
| NFL | elo, trend | 50.0% | 110 | 110 | 67.3% | +31.27 | ✅ +3 |
| **Combined** | | | | | | **+305.54U** | |

All models use logistic regression with walk-forward 60/20/20 chronological validation. Confidence gates are calibration-supported, not holdout-mined.

## Rest-Fatigue Flip Filter

Active for WNBA, NFL, and MLB. When the model picks a team that has 3+ fewer rest days than the opponent, the pick is flipped to the rested side. Skipped for NBA (model already prices rest correctly) and MLB (no regular-season rest gaps trigger).

```
WNBA: +10 flips, NFL: +3 flips, MLB: 0 flips
```

## Research Models

| League | Model | Status |
|--------|-------|--------|
| MLB | spread-baseline-v1 | Research — will improve as snapshots accumulate |
| NBA | spread-baseline-v1 | Research |
| WNBA | spread-baseline-v1 | Research |
| NFL | spread-baseline-v1 | Research |

Spread uses Elo expected margin. Total uses league average. Will train real models when 60+ days of Polymarket snapshots are available (currently 2 days).

## Data Sources

| Source | Coverage | Cost |
|--------|----------|------|
| ESPN Public API | MLB, NBA, WNBA, NFL scores | Free |
| Polymarket US Gateway | Live odds, BBO snapshots, event discovery | Free |
| The Odds API | Soccer scores (3-day lookback) | Free tier |

## Quick Start

```bash
# Install
cd "model prediction"
python3 -m venv .venv
.venv/bin/pip install -e .

# Dashboard
python3 dashboard_server.py
# Open http://127.0.0.1:8765

# Daily pipeline (or let launchd handle it)
model-prediction daily --date $(TZ=America/New_York date +%Y-%m-%d)

# Bootstrap historical data
model-prediction bootstrap --all --from 2024-01-01 --to $(date +%Y-%m-%d)

# Validate all models
model-prediction validate-models

# Tests
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

## Infrastructure

- **Daily snapshots:** launchd job every 3 hours (`com.modelprediction.daily`)
- **Dashboard:** `com.modelprediction.dashboard` launchd job
- **Backfill:** `model-prediction bootstrap --all` — currently 6,141 MLB / 3,583 NBA / 754 WNBA / 700 NFL games
- **Polymarket snapshots:** captured during daily runs, stored in `data/odds/{sport}/{date}/`

## Configuration

Copy `.env.example` to `.env` and fill in:

```
POLYMARKET_KEY_ID=       # for live trading (optional for shadow)
POLYMARKET_SECRET_KEY=   # for live trading
THE_ODDS_API_KEY=        # for soccer scores
```

Model config in `config/model.yaml`. Artifacts in `config/models/`. Dashboard state in `dashboard/`.

## Project Structure

```
├── config/
│   ├── model.yaml              # Active model configuration
│   └── models/                 # Immutable, hash-verified artifacts
├── src/model_prediction/
│   ├── learned_forward.py      # Forward model + rest flip filter
│   ├── validation.py           # Walk-forward validation pipeline
│   ├── features/               # Elo, trends, park factors, rest
│   ├── models/                 # LearnedMarketArtifact loader
│   └── cli.py                  # CLI entry point
├── data/
│   ├── historical/             # Processed game records
│   ├── raw/                    # Cached ESPN scoreboards
│   ├── odds/                   # Polymarket BBO snapshots
│   └── events.jsonl            # Audit chain
├── dashboard.html              # Single-page dashboard
├── dashboard_server.py         # Dashboard HTTP server
└── tests/                      # Pytest suite
```

## Audit & Integrity

- **Artifact hashes:** every model artifact is SHA-256 verified (`config/models/*.json`)
- **Audit chain:** `data/events.jsonl` — cryptographically linked event log
- **Validation:** `outputs/latest/learned-model-validation.json`

Run full integrity check:
```bash
PYTHONPATH=src .venv/bin/python -c "
# See DEBUG.md for complete 10-step audit protocol
"
```
