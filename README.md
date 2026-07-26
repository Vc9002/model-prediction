# model-prediction

Shadow-first multi-sport prediction, research, ledger, and local dashboard
system with Polymarket US market-data integration.

**Last updated**: 2026-07-27

The current operational verdict and audit evidence live in
[`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) and [`DEBUG.md`](DEBUG.md).
The checkout is **not release-ready**, and its real-money execution surface
should not be used until the P0 execution-binding and ledger/audit transaction
defects are repaired.

## Current State

| Metric | Value |
|--------|-------|
| Active learned artifacts | MLB v6, NBA/WNBA/NFL v4 |
| Research/override artifacts | Soccer Poisson/DC v1, Esports v4, KBO/NPB v2 identifiers |
| Tests | **436 pass** |
| Audit chain | 16,918 events, 0 breaks, 0 hash mismatches |
| Artifact integrity | 31 valid, 2 mismatched, 33 total |
| Ruff | 117 findings |
| Release status | **Blocked** |

Do not infer executable profitability from artifact hit rates, synthetic
`-110` units, shadow-ledger P&L, or a dashboard qualification badge.

## Ledger Structure

Four ledger tiers with distinct purposes:

| Ledger | File | Purpose |
|--------|------|---------|
| **Main** | `data/picks.xlsx` | Main shadow-call ledger. A row label is not proof of artifact qualification or a placed order. |
| **Flat** | `data/flat_picks.xlsx` | All MLB/NBA/WNBA/NFL production-model decisions. Research/diagnostic only. |
| **Research** | `data/research/{sport}.xlsx` | Separate Soccer, LoL, CS2, Dota 2, Valorant, KBO, and NPB workbooks. Contains only exact-input, executable-quote research-model decisions; a valid low-edge decision remains a zero-unit `NO_CALL`. |
| **Gated Research** | `data/gated_research/{sport}.xlsx` | Separate workbooks for the same seven sports. Strict subset of Research containing only positive-unit calls that clear the sport's configured executable-edge and confidence floors. |

Main and flat use production models. Research-only sports never enter Flat. The
dashboard aggregates the separate sport workbooks for unified Research and
Gated Research views.

The project uses complete-date chronological 60/20/20 validation. Model
accuracy, calibration, diagnostic units, and executable profitability are
separate claims. A model is operationally eligible only when its config,
artifact, current report, tests, and point-in-time evidence agree.

## Research Models

| League | Model | Status |
|--------|-------|--------|
| MLB | v6 probable-starter experiment + spread/total research | Unqualified; prospective starter archive began 2026-07-26, but old validation remains non-PIT |
| NBA | spread-baseline-v1 | Research |
| WNBA | spread-baseline-v1 | Research |
| NFL | spread-baseline-v1 | Research |
| LoL/CS2/DOTA2/Valorant | neutral-series Elo v4 | Config override; dashboard evidence remains research-only |
| Soccer | Poisson/Dixon-Coles full-game 2.5 totals | Research-only; draw-aware and executable-BBO matched |
| KBO/NPB | tie-aware Elo v2 identifiers | Research-only; preview, paired-ledger routing, daily wiring, and `$0.50` tie settlement are active |

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

# Both the installed entry point and module form currently import.
.venv/bin/model-prediction summary
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary

# Recommended daily pipeline. It settles/ingests first, then runs one unified
# forecast process: learned US sports -> main/flat; soccer/esports/KBO/NPB ->
# research, with only valid subsets mirrored to gated research.
bash scripts/run_daily.sh

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
│   ├── learned_forward.py      # Forward model; current WNBA hook can degrade/fail open
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
│   ├── research/               # One research ledger workbook per research-only sport
│   ├── gated_research/         # One gated-subset workbook per research-only sport
│   └── events.jsonl            # Audit chain
├── dashboard.html              # Single-page dashboard
├── dashboard_server.py         # Dashboard HTTP server
└── tests/                      # Pytest suite
```

## Audit & Integrity

- **Artifact hashes:** all 33 JSON artifacts carry SHA-256 fields, but the NBA
  and NFL spread baselines currently mismatch their canonical contents.
- **Audit chain:** `data/events.jsonl` is intact at 16,387 events, but
  ledger/audit reconciliation remains false because 1,150 historical removals
  predate audited removal events.
- **Validation:** `outputs/latest/learned-model-validation.json` is stale for
  this checkout and active MLB v6. It is not a release manifest.
- **Execution:** the current order ticket is not structurally bound to the
  qualified ledger row. Do not use real-money execution.

Run the checks in `DEBUG.md`. Record failures as failures; never rewrite the
documentation to say the scan passed until tests, Ruff, hashes, the chain, config,
artifacts, and the generated report all agree.
