# model-prediction

Shadow-first sports prediction and Polymarket US market-error research.
Every pick is paper-tracked; real-money execution sits behind a hard gate
that requires an explicit user command, the `--execute` flag,
`POLYMARKET_PRIVATE_KEY`, and a Y/N confirmation.

## Qualified models

| Sport | Market | Hit Rate | Calls | Flat P&L | Artifact |
|---|---|---|---|---|---|
| MLB | moneyline | 60.87% | 92 | +14.91U | `mlb-elo-trend-lr-v2.json` |
| NBA | moneyline | 67.35% | 294 | +84.00U | `nba-elo-trend-lr-v2.json` |
| WNBA | moneyline | 65.98% | 97 | +25.18U | `wnba-elo-trend-lr-v2.json` |
| NFL | moneyline | 60.55% | 109 | +17.00U | `nfl-elo-trend-lr-v2.json` |

These are locked-holdout model-accuracy results at -110 flat stakes.
They do not establish executable betting profit without contemporaneous
Polymarket executable asks.

## Install

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Python >= 3.11
```

## Daily loop

```bash
model-prediction polymarket-slate --all --date YYYY-MM-DD
model-prediction forecast --sport mlb --date YYYY-MM-DD --log
model-prediction settle --all-unsettled
model-prediction summary
model-prediction daily --date YYYY-MM-DD      # all of the above, plus Polymarket odds
```

The `daily` command captures Polymarket US BBO snapshots, runs learned-LR
forecasts for all qualified sports, logs matched quotes, settles completed
picks, and produces a summary. Production forecasts use the learned LR +
confidence-gate path (`--model learned`, the default).

## Dashboard

```bash
python3 dashboard_server.py       # then open http://127.0.0.1:8765/
```

Read-only viewer over data/, outputs/latest/, and stored Polymarket
snapshots, with confirmed quick actions that shell out to this CLI.

## Data & evaluation

```bash
model-prediction bootstrap --sport nba --from 2025-10-01
model-prediction features --sport nba --date YYYY-MM-DD
model-prediction backtest --sport nba --start YYYY-MM-DD --end YYYY-MM-DD
model-prediction validate --all    # qualification audit -> outputs/latest/
```

Qualification is accuracy-first: >= 50 selective calls at >= 60% hit rate on a
locked holdout, every complete qualifying month positive at -110. Brier/calibration
are secondary reports; ROI and market-price economics are diagnostics only.
See DEBUG.md for the full audit protocol.

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```
