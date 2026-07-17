# model-prediction — v2.0

Shadow-first multi-sport prediction with Polymarket US integration.
5 qualified models, automated daily pipeline.

## Qualified Models

| Sport | Calls | Hit Rate | P&L | Artifact |
|---|---|---|---|---|
| MLB | 92 | 60.87% | +14.91U | mlb-elo-trend-lr-v3 |
| NBA | 294 | 67.35% | +84.00U | nba-elo-trend-lr-v3 |
| WNBA | 97 | 65.98% | +25.18U | wnba-elo-trend-lr-v3 |
| NFL | 109 | 60.55% | +17.00U | nfl-elo-trend-lr-v3 |
| SOCCER (binary) | 268 | 60.07% | +39.40U | soccer-elo-trend-lr-v1 |
| SOCCER (3-way) | 53 | 66.04% | +13.82U | soccer-elo-trend-lr-v1 |
| **Combined** | **913** | — | **+195U** | |

Soccer covers 7 Polymarket leagues: EPL, La Liga, Bundesliga, Serie A, MLS, UCL, World Cup.

## Data Sources

| Source | Coverage | Cost |
|---|---|---|
| ESPN Public API | MLB, NBA, WNBA, NFL, top 7 soccer leagues | Free |
| The Odds API | 12 smaller soccer leagues (scores, 3-day lookback) | Free tier |
| OpenLigaDB | Bundesliga 1/2/3. Liga (full seasons since 2002) | Free |
| Polymarket US Gateway | Live odds, BBO snapshots, event discovery | Free |

## Daily Pipeline

Runs automatically at 6 AM via launchd:
```
model-prediction daily --date YYYY-MM-DD
```

Steps: Polymarket slate → soccer score collection → forecast + log → settle → summary.

## Quick Start

```bash
# Dashboard
dash

# Run today's pipeline
model-prediction daily --date $(TZ=America/New_York date +%Y-%m-%d)

# Collect soccer scores from Odds API
model-prediction collect-scores

# Validate all models
model-prediction validate-models

# Tests
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

## Revert
```
git checkout v1.0.0
```
