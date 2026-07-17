# model-prediction — Final Release

Shadow-first multi-sport prediction system with Polymarket US market integration.
5 qualified models across learned LR + confidence-gate pipeline.

## Qualified models

| Sport | Calls | Hit Rate | Flat P&L | Artifact |
|---|---|---|---|---|
| MLB | 92 | 60.87% | +14.91U | mlb-elo-trend-lr-v3 |
| NBA | 294 | 67.35% | +84.00U | nba-elo-trend-lr-v3 |
| WNBA | 97 | 65.98% | +25.18U | wnba-elo-trend-lr-v3 |
| NFL | 109 | 60.55% | +17.00U | nfl-elo-trend-lr-v3 |
| SOCCER | 470 | 68.09% | +140.91U | soccer-elo-trend-lr-v1 |

Soccer covers 7 Polymarket leagues: EPL, La Liga, Bundesliga, Serie A, MLS, UCL, World Cup.

## Research models

| Sport | Status |
|---|---|
| Tennis | Baseline — needs data bootstrap |

## Architecture

- **Shared pipeline**: Elo + opponent-adjusted trend logistic regression with learned confidence gate
- **Walk-forward validation**: 60/20/20 chronological split, locked holdout, 65% target
- **Hash-verified artifacts**: Every production model pinned with SHA-256
- **Audit chain**: Append-only event log with cryptographic chain
- **Hard-gated execution**: --execute flag + private key + Y/N confirmation
- **Polymarket integration**: Daily BBO snapshot capture, executable ask filtering (2% minimum edge)
- **Dashboard**: Real-time portfolio, odds, performance, matrix, research tabs

## Daily loop

```bash
model-prediction polymarket-slate --all --date YYYY-MM-DD
model-prediction forecast --sport mlb --date YYYY-MM-DD --log
model-prediction settle --all-unsettled
model-prediction summary
model-prediction daily --date YYYY-MM-DD
```

## Verify

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
python3 dashboard_server.py  # then open http://127.0.0.1:8765/
```
