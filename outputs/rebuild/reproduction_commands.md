# Reproduction Commands — Rebuild Platform

**Branch**: rebuild/clean-slate-v1 | **Last verified**: 2026-08-05

## Verify Everything

```bash
cd "model prediction"
git checkout rebuild/clean-slate-v1

# Run all rebuild tests (49)
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_rebuild.py -q

# Run all legacy tests (699)
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ --ignore=tests/test_rebuild.py -q

# Verify all rebuild modules import
env PYTHONPATH=src:. .venv/bin/python -c "from model_prediction.rebuild import *; print('OK')"
```

## Collect MLB Data

```bash
# Single date collection (idempotent)
env PYTHONPATH=src:. .venv/bin/python -c "
from model_prediction.rebuild import MLBCollector, MetadataDB
meta = MetadataDB('data/rebuild/metadata.db')
c = MLBCollector('data/rebuild', meta)
print(c.collect_date('2026-08-05'))
"

# Show what's collected
env PYTHONPATH=src:. .venv/bin/python -c "
from model_prediction.rebuild.storage import NormalizedStore
n = NormalizedStore('data/rebuild/normalized')
for s, ts in n.tables.items():
    for t in ts:
        df = n.read(s, t)
        print(f'{s}/{t}: {df.height} rows')
"
```

## Train the MLB Two-Head Model

```bash
env PYTHONPATH=src:. .venv/bin/python -c "
from model_prediction.rebuild import MLBTwoHeadModel, ChronologicalEvaluator, expanding_folds
import polars as pl

# Load data from medallion store
norm = NormalizedStore('data/rebuild/normalized')
df = norm.read('mlb', 'scoreboard')

# Build feature matrix (requires actual features from collector)
# This is a placeholder — real features come from pybaseball + Open-Meteo
features = ['elo_probability', 'trend_gap', 'starter_era_gap', ...]

# Split chronologically
dates = sorted(df['game_date'].unique().to_list())
folds = expanding_folds(dates, n_splits=5, val_size=30, test_size=60)

# Train and evaluate
model = MLBTwoHeadModel()
evaluator = ChronologicalEvaluator(folds)
result = evaluator.evaluate(df, date_col='game_date', target_col='home_win')
print(evaluator.summary())
"
```

## Calibrate and Ensemble

```bash
env PYTHONPATH=src:. .venv/bin/python -c "
from model_prediction.rebuild import fit_calibrator, Ensemble
from model_prediction.rebuild.validation import log_loss, brier_score, ece

# Fit calibrator on OOF predictions
cal = fit_calibrator('platt', oof_probs, oof_labels)
calibrated = [cal.transform(p) for p in oof_probs]

# Fit ensemble
ens = Ensemble('logistic_stacking')
ens.fit({'mlb': oof_probs, 'xgb': xgb_probs}, oof_labels)
"
```

## Run Economic Evaluation

```bash
env PYTHONPATH=src:. .venv/bin/python -c "
from model_prediction.rebuild import (
    executable_edge, is_tradeable, edge_scaled_units,
    evaluate_portfolio, SizeLimits, Exposure, CorrelationTracker
)
from model_prediction.rebuild.xgboost_stress import run_stress_tests, stress_test_summary

# Calculate edges against real Polymarket BBOs
for pred in predictions:
    edge = executable_edge(pred.prob, conservative, best_ask, spread)
    if is_tradeable(edge, min_edge=0.02):
        # Size the position
        size = edge_scaled_units(pred.prob, conservative, best_ask)
        # Track exposure
        if exposure.can_add(sport, team, event_id, size['units']):
            exposure.add(sport, team, event_id, size['units'])

# Evaluate portfolio
result = evaluate_portfolio(trades)
print(result)

# Stress test
stress = run_stress_tests(trades, result['total_pnl'])
print(stress_test_summary(stress))
"
```

## Monitor Health

```bash
env PYTHONPATH=src:. .venv/bin/python -c "
from model_prediction.rebuild.economic import MonitorState
m = MonitorState(
    source_health={'polymarket_us': 'active', 'espn_public': 'active'},
    calibration_drift=0.01, recent_clv=0.02, recent_roi=0.05
)
print(m.evaluate())  # HEALTHY_SHADOW
print(m.to_dict())
"
```

## Explore Data with DuckDB

```bash
env PYTHONPATH=src:. .venv/bin/python -c "
from model_prediction.rebuild.storage import NormalizedStore
n = NormalizedStore('data/rebuild/normalized')
n.register('mlb', 'scoreboard')
df = n.query('SELECT home_team, away_team, home_score, away_score FROM mlb_scoreboard LIMIT 5')
print(df)
"
```
