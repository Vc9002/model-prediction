# Current System Audit — model-prediction

**Audit date**: 2026-08-05
**Branch**: rebuild/clean-slate-v1
**Git SHA**: 09bc24e

## Summary

The existing system has solid infrastructure (point-in-time features, immutable hashes, audit chain, exact market matching) but thin models. Every active sport model uses Elo-based logistic regression with 2-7 features. Economic evaluation uses synthetic -110 pricing rather than executable quotes. Excel is the primary database. The system is a useful benchmark and data source — not a foundation to imitate.

## What the daily pipeline actually runs

`scripts/run_daily.sh` executes three steps with file locking:

1. **Settle**: `cli settle --all-unsettled` — grades open picks via ESPN scores or Polymarket resolution
2. **Ingest**: `cli ingest mlb/nba/wnba/nfl` for yesterday + today — updates `data/historical/{sport}_games_all.jsonl`
3. **Forecast**: `cli daily --date ... --skip-settlement` — one unified pass writing to all four ledgers

## Per-sport model audit

### MLB Moneyline (v8) — `learned_forward.py`
- Logistic regression on Elo + trend + starter_era_gap + bullpen + park + availability
- **qualified=false** — Brier regressed vs v7. Operator override to production.
- Uses real per-starter ERA (v8) vs team-level ERA gap (v7)
- 60.81% holdout hit rate, 148 calls
- **Verdict**: Retain as benchmark. Too few features, no coherent run distribution.

### MLB Spread/Totals (Measured Edge v3) — `models/mlb.py`
- Gamma-Poisson Monte Carlo simulation from Trend Engine
- Shared relative-run formula used for both spread and totals
- Spread: 60.0% hit rate, correlation 0.208. Marginally improved.
- Totals: 52.9% hit rate, correlation 0.041. Getting *worse*.
- **Verdict**: Replace with separate run-intensity and run-differential heads.

### NBA Moneyline (v4) — `learned_forward.py`
- Logistic regression on Elo + trend_gap + defensive_trend_gap only
- Calibration slope 1.79 — severe probability-shape error
- 73.66% hit rate but probabilities are miscalibrated
- **Verdict**: Rebuild with possessions, player impact, availability, lineups.

### WNBA Moneyline (v4) — `learned_forward.py`
- Same 3-feature structure as NBA with threshold effectively calling the whole slate
- 67.48% hit rate
- **Verdict**: Independent model with stronger shrinkage needed.

### NFL Moneyline (v4) — `learned_forward.py`
- Logistic regression on Elo + trend_gap only (2 features)
- 122 evaluation games, ECE ~0.10
- **Verdict**: Replace with drive-based model using QB state, EPA, pace, weather.

### Soccer — `models/soccer.py`
- Poisson-Dixon-Coles with 4 hardcoded constants: HOME_GOAL_BOOST=1.15, DC_RHO=-0.10, BTTS calibrations
- EWMA attack/defense strengths. No dynamic parameters.
- **Verdict**: Good control model. Everything should be learned from data.

### Tennis — `models/tennis.py`
- Fixed K=32 Elo, fixed 60% surface / 40% overall blend, constant 0.05 uncertainty
- No serve/return features in the actual forecast
- **Verdict**: Replace with dynamic surface tuning, serve/return state, inactivity.

### Esports (5 titles) — `esports.py`
- Per-title neutral Elo with Platt scaling
- Organization-based, not roster/player/map/patch based
- Hand-set recency and tier multipliers
- **Verdict**: Rebuild per-title with roster, map, patch, format features.

### KBO/NPB — `international_baseball.py`
- Home Elo + flat or Elo-gap tie heuristic
- No pitcher, lineup, bullpen, park, or run model
- **Verdict**: Retain as benchmark. League-specific run distributions needed.

## Structural problems found

1. **All learned models share one pipeline** (`learned_forward.py`) — 694 lines serving MLB/NBA/WNBA/NFL with identical methodology but different artifacts
2. **No calibration fitted on independent data** — Platt coefficients are hardcoded (soccer) or fitted on training data (esports)
3. **No ensemble** — single model per sport, no out-of-fold stacking
4. **Hypothetical pricing** — `-110` used for economic evaluation, not executable Polymarket BBOs
5. **Excel as database** — all four ledgers are .xlsx files with no ACID transactions
6. **cli.py (4,411 lines)** — 48 subcommands, near-zero test coverage
7. **dashboard_server.py (4,782 lines)** — monolithic with inline HTML
8. **12 orphaned modules** — ~1,800 lines of likely-dead code
9. **17 hardcoded thresholds** across 7 files
10. **Audit chain doesn't cross-check ledgers** — `verify-chain` confirms log integrity but never compares against ledger contents

## What to keep

- Point-in-time cutoff (`FeatureStore.games_before()`)
- Immutable SHA-256 artifact hashing
- Exact market contract matching against Polymarket
- Chronological walk-forward validation framework
- Settlement pipeline (ESPN + Polymarket resolution)
- Audit chain infrastructure (JSONL with hash linking)
- Entity registry pattern
- File locking pattern (fcntl.flock with timeout)

## What to replace

- All sport-specific models (keep as frozen benchmarks only)
- Excel-based storage (migrate to Parquet + SQLite)
- Hardcoded thresholds (move to learned or config-validated values)
- Monolithic CLI and dashboard (split into packages)
- Synthetic -110 pricing (use real executable Polymarket BBO data)

## Next actions (ordered)

1. Build medallion storage layer: `data/rebuild/{raw,normalized,features,markets}`
2. Build canonical identity schema in SQLite
3. Add new dependencies: xgboost, pybaseball, polars, pyarrow, duckdb, pandera, statsmodels, optuna
4. Build MLB source collector on pybaseball + Open-Meteo + Polymarket BBO
5. Build MLB feature store with the full pitcher/lineup/bullpen/environment spec
6. Produce current_model_baselines.parquet from frozen artifacts
