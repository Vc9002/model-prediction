# model-prediction

Shadow-first, point-in-time sports prediction using Elo+Trend logistic regression and a learned confidence gate.

## Current qualified moneyline artifacts

| Sport | Hit rate | Calls | Flat P&L at -110 |
|---|---:|---:|---:|
| MLB | 60.87% | 92 | +14.91U |
| NBA | 67.35% | 294 | +84.00U |
| WNBA | 65.98% | 97 | +25.18U |
| NFL | 60.55% | 109 | +17.00U |

These are model-accuracy results, not claims of executable betting profit. The system still needs contemporaneous executable prices to establish trade EV.

## Verify

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
PYTHONPATH=src .venv/bin/python -m model_prediction.cli validate-models \
  --output outputs/latest/learned-model-validation-v2.json
```

Run the ten checks in `DEBUG.md` for chain, artifact, data, import, feature, configuration, and season-filter integrity.

## Daily shadow loop

```bash
model-prediction polymarket-slate --all --date YYYY-MM-DD
model-prediction forecast --sport mlb --date YYYY-MM-DD
model-prediction settle --all-unsettled
model-prediction summary
```

Do not add `--log` or `--execute` merely to inspect a forecast. Execution remains hard-gated and outside this validation run.

## Evidence

- Validation audit: `outputs/latest/learned-model-validation-v2.json`
- Active configuration: `config/model.yaml`
- Versioned artifacts: `config/models/*-elo-trend-lr-v2.json`
