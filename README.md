# model-prediction

Shadow-first multi-sport prediction, research, ledger, and local dashboard
system with Polymarket US market-data integration.

## Read this first

The current checkout is **not release-ready**. Tests, artifact qualification,
the audit chain, packaging, and documentation had drifted apart. Do not use old
README tables or flat `-110` units as proof of a live betting edge.

See [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) for the verified health
snapshot, current model evidence, architecture, safety boundaries, and repair
order. That document supersedes historical metrics elsewhere when they conflict.

See [`docs/ENGINEERING_ROADMAP.md`](docs/ENGINEERING_ROADMAP.md) for the
software-side punch list: dead code, oversized files worth splitting, a
verified broken dashboard route, missing CI, and non-model/dashboard/
portfolio-layer feature ideas. `model_improvements.md` stays scoped to
per-sport modeling research only.

The project uses complete-date chronological 60/20/20 validation. Model
accuracy, calibration, diagnostic units, and executable profitability are
separate claims. A model is operationally eligible only when its config,
artifact, current report, tests, and point-in-time evidence agree.

## Research Models

| League | Model | Status |
|--------|-------|--------|
| MLB | spread-baseline-v1 | Research — will improve as snapshots accumulate |
| NBA | spread-baseline-v1 | Research |
| WNBA | spread-baseline-v1 | Research |
| NFL | spread-baseline-v1 | Research |
| LoL | neutral-series-elo-v1 | Research — no market-profitability claim |
| CS2 | neutral-series-elo-v1 | Research — legacy CS:GO excluded |
| KBO | tie-aware-elo-v1 | Research — ties valued at 50¢; no profitability claim |
| NPB | tie-aware-elo-v1 | Research — ties valued at 50¢; October omitted to prevent postseason leakage |

Spread, total, F5, and other derivative models remain research-only unless exact
historical contract lines and decision-horizon inputs exist. Never infer
readiness from a hardcoded snapshot-count target.

## Data Sources

| Source | Coverage | Cost |
|--------|----------|------|
| ESPN Public API | MLB, NBA, WNBA, NFL scores | Free |
| Polymarket US Gateway | Live odds, BBO snapshots, event discovery | Free |
| The Odds API | Soccer scores (3-day lookback) | Free tier |
| BO3 public website data | LoL and CS2 best-of match history | Free, no signup/key |
| Official KBO schedule/results | KBO regular-season scores and stable game/team IDs | Free, no signup/key |
| Official NPB English calendar | NPB regular-season scores and stable game links/team codes | Free, no signup/key |

## Quick Start

```bash
# Install
cd "model prediction"
python3 -m venv .venv
.venv/bin/pip install -e .

# Dashboard (open and verify in Dia)
env PYTHONPATH=src:. .venv/bin/python dashboard_server.py --port 8765

# The installed console entry point is currently stale. Use the module form.
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary

# Daily pipeline; this logs and settles rows, so inspect before running.
# Runs the main forecast+log+settle steps AND the flat one-unit forecast+log+
# settle steps against a separate flat ledger in the same invocation.
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli daily --date YYYY-MM-DD

# Bootstrap historical data
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli bootstrap --all --from 2024-01-01 --to YYYY-MM-DD

# Validate without writing artifacts
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli validate-models

# Backfill and validate isolated esports baselines
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli esports-backfill --all --from 2024-01-01
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli validate-esports --titles lol cs2

# Discover all current Polymarket US esports titles and capture research BBOs
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli polymarket-slate --sport esports --date YYYY-MM-DD
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli esports-forecast --title lol --date YYYY-MM-DD

# Backfill, validate, discover, and price separate KBO/NPB research baselines
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli international-baseball-backfill --all --from 2022-01-01
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli validate-international-baseball --leagues kbo npb
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli polymarket-slate --sport npb --date YYYY-MM-DD --timezone Asia/Tokyo
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli international-baseball-forecast --league npb --date YYYY-MM-DD

# Tests
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
```

## Infrastructure

- **Daily snapshots:** scheduled-job configuration exists; verify the live job rather than assuming it is loaded
- **Dashboard:** run `dashboard_server.py` with the project venv and verify `/api/health`
- **Backfill:** counts are derived from the current caches; do not hardcode them in docs
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
│   ├── learned_forward.py      # Forward model + fail-closed feature hooks
│   ├── validation.py           # Walk-forward validation pipeline
│   ├── features/               # Elo, trends, park factors, player availability
│   ├── data_sources/           # ESPN, markets, official WNBA injury reports
│   ├── models/                 # LearnedMarketArtifact loader
│   └── cli.py                  # CLI entry point
├── data/
│   ├── historical/             # Processed game records
│   ├── raw/                    # Cached ESPN scoreboards
│   ├── odds/                   # Polymarket BBO snapshots
│   ├── availability/           # Timestamped official report and research caches
│   ├── esports/                # Versioned LoL/CS2 series backfills and identities
│   ├── international_baseball/ # Official KBO/NPB results, identities, and manifests
│   └── events.jsonl            # Audit chain
├── dashboard.html              # Single-page dashboard
├── dashboard_server.py         # Dashboard HTTP server
└── tests/                      # Pytest suite
```

## Audit & Integrity

- **Artifact hashes:** JSON artifacts carry SHA-256 hashes; hash integrity does not prove current qualification
- **Audit chain:** `data/events.jsonl` is intended to be linked but is currently broken; see project status
- **Validation:** `outputs/latest/learned-model-validation.json`

Run the checks in `DEBUG.md`. Record failures as failures; never rewrite the
documentation to say the scan passed until tests, Ruff, hashes, the chain, config,
artifacts, and the generated report all agree.
