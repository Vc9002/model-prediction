# model-prediction

Shadow-first multi-sport prediction, research, ledger, and local dashboard
system with Polymarket US market-data integration.

**Last updated**: 2026-07-28

The current operational verdict and audit evidence live in
[`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) and [`DEBUG.md`](DEBUG.md).
The checkout is **not release-ready**, and its real-money execution surface
should not be used until the P0 execution-binding and ledger/audit transaction
defects are repaired.

## Current State

| Metric | Value |
|--------|-------|
| Tests | **461 pass** |
| Ruff | 113 findings |
| Git | single `main` branch (no other active branches, local or `origin`) |
| Release status | **Blocked** — see `docs/PROJECT_STATUS.md` for why |

Do not infer executable profitability from artifact hit rates, synthetic
`-110` units, shadow-ledger P&L, or a dashboard qualification badge.

## What's Wired (live in `daily`) and What Feeds It

The operating question for this project day-to-day is **not** "is this model
validated" — it's "is this model actually running, and on what data." That
table:

| Sport | Wired in `daily`? | Model / features it actually runs on | Ledger it writes to |
|---|---|---|---|
| MLB moneyline | Yes | `learned_forward.py` — Elo + trend logistic regression (v6 artifact) | Main + Flat |
| MLB totals & spread | Yes | `models/mlb.py` `MeasuredEdgeTotalsModel`/margin — Gamma-Poisson mixture Monte-Carlo (`config/models/mlb-analyst-poisson-trend-v0.2.yaml`), priced against real Polymarket lines closest to 50/50 | Flat only |
| MLB moneyline (legacy) | **No, by design** | `MeasuredEdgeMarginModel` via `--model legacy-measured-edge` — intentionally retained as an explicit manual rollback path, not part of `daily`; see `_forecast_mlb` docstring | Main, only if manually invoked |
| NBA / WNBA / NFL | Yes | `learned_forward.py` — Elo + trend logistic regression (v4 artifacts) | Main + Flat |
| Soccer totals (2.5 line) | Yes | `models/soccer.py` — Poisson/Dixon-Coles score matrix | Flat Research + Gated Research |
| Soccer moneyline | Yes | Same score matrix; matched against Polymarket's per-team `team_win` Yes/No markets (not a single combined moneyline market) | Flat Research + Gated Research |
| Soccer BTTS | **No** | The model already computes a BTTS probability (`models/soccer.py`), but nothing classifies a BTTS market type on the Polymarket US ingestion side yet, so it's never matched against a price | — |
| Tennis moneyline | Yes | `models/tennis.py` — surface-blended Elo, singles only, **WTA only** (Polymarket US has no ATP market at all; ESPN has no ITF scoreboard, so WTA is the only tour where both a prediction and an executable price can exist). Two data bugs fixed 2026-07-28 (see `DEBUG.md`) — real player probabilities now compute correctly instead of always defaulting to 50%. | Flat Research + Gated Research |
| Esports (LoL/CS2/Dota2/Valorant/Rainbow Six) | Yes | `esports.py` — result-based neutral Elo, Platt-scaled, refreshed from bo3.gg before every forecast (`refresh_recent_matches`). Dota2/Valorant had swapped discipline IDs (fixed 2026-07-28); Rainbow Six added 2026-07-28. | Flat Research + Gated Research |
| Esports (CoD/Rocket League/Overwatch) | **Not buildable** | Polymarket lists these 3 additional esports leagues and real BBO is captured for them daily, but bo3.gg (this project's only esports data source) has no discipline for any of them at all — there is no data to train a real model on | — |
| KBO / NPB | Yes | `international_baseball.py` — tie-aware home-field Elo (result/margin only, no starters/park/weather) | Flat Research + Gated Research |

"Flat Research" ledgers log every model-evaluated candidate with no edge gate;
"Gated Research" is the strict subset that clears that sport's configured
edge/confidence floor. See Ledger Structure below.

## Ledger Structure

Four ledger tiers with distinct purposes:

| Ledger | File | Purpose |
|--------|------|---------|
| **Main** | `data/picks.xlsx` | Main shadow-call ledger. A row label is not proof of artifact qualification or a placed order. |
| **Flat** | `data/flat_picks.xlsx` | Every MLB/NBA/WNBA/NFL production-model decision, plus MLB totals/spread. Research/diagnostic only. |
| **Flat Research** | `data/research/{sport}.xlsx` | Separate Soccer, Tennis, LoL, CS2, Dota 2, Valorant, KBO, and NPB workbooks. Every model-favored candidate for every matched game/market, no edge gate — a valid low-edge decision remains a zero-unit `NO_CALL`. |
| **Gated Research** | `data/gated_research/{sport}.xlsx` | Separate workbooks for the same eight sports. Strict subset of Flat Research containing only positive-unit calls that clear the sport's configured executable-edge and confidence floors. |

Main and flat use production models. Research-only sports never enter the main
Flat ledger — they get their own Flat Research workbook instead. The dashboard
aggregates the separate sport workbooks for unified Flat Research and Gated
Research views.

The project uses complete-date chronological 60/20/20 validation for models
that have been promoted to production. Model accuracy, calibration, diagnostic
units, and executable profitability are separate claims from "is it wired."

## Data Sources

| Source | Coverage | Cost |
|--------|----------|------|
| ESPN Public API | MLB, NBA, WNBA, NFL scores; ATP/WTA tennis scores (singles only, doubles filtered out) | Free |
| Polymarket US Gateway | Live odds, BBO snapshots, event discovery | Free |
| The Odds API | Soccer scores (3-day lookback) | Free tier |
| BO3 public website data | LoL, CS2, Dota 2, Valorant, Rainbow Six Siege best-of match history | Free, no signup/key |
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

# Bootstrap tennis (parses ESPN's ATP/WTA scoreboards into singles-only match
# records; doubles draws are dropped at ingestion) and preview the WTA-only
# moneyline slate -- Polymarket US has no ATP market, so ATP history is built
# but never priced
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli bootstrap --sport tennis --from 2024-01-01 --to YYYY-MM-DD
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli polymarket-slate --sport tennis --date YYYY-MM-DD
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli forecast --sport tennis --date YYYY-MM-DD

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
│   ├── soccer_forward.py       # Soccer totals + moneyline live pricing (BTTS not yet wired)
│   ├── tennis_forward.py       # Tennis moneyline live pricing (WTA only)
│   ├── validation.py           # Walk-forward validation pipeline
│   ├── features/               # Elo, trends, park factors, player availability
│   ├── data_sources/           # ESPN, markets, official WNBA injury reports
│   ├── models/                 # LearnedMarketArtifact loader
│   └── cli.py                  # CLI entry point
├── data/
│   ├── historical/             # Processed game records
│   ├── processed/              # Per-sport normalized game records (gitignored)
│   ├── raw/                    # Cached ESPN scoreboards
│   ├── odds/                   # Polymarket BBO snapshots
│   ├── availability/           # Timestamped official report and research caches
│   ├── esports/                # Versioned LoL/CS2/Dota2/Valorant series backfills and identities
│   ├── international_baseball/ # Official KBO/NPB results, identities, and manifests
│   ├── research/               # One Flat Research ledger workbook per research-only sport
│   ├── gated_research/         # One gated-subset workbook per research-only sport
│   └── events.jsonl            # Audit chain
├── dashboard.html              # Single-page dashboard
├── dashboard_server.py         # Dashboard HTTP server
└── tests/                      # Pytest suite
```

## Audit & Integrity

- **Artifact hashes:** all JSON artifacts carry SHA-256 fields, but the NBA
  and NFL spread baselines currently mismatch their canonical contents.
- **Audit chain:** `data/events.jsonl` is intact (0 breaks), but ledger/audit
  reconciliation remains false because historical removals predate audited
  removal events (1,230 as of 2026-07-27; this number only grows over time by
  design — see `DEBUG.md`).
- **Validation:** `outputs/latest/learned-model-validation.json` is stale for
  this checkout and active MLB v6. It is not a release manifest.
- **Execution:** the current order ticket is not structurally bound to the
  qualified ledger row. Do not use real-money execution.

**Known wiring gaps** (tracked, not release blockers):
- Soccer BTTS: model computes it, but no BTTS market currently exists on
  Polymarket US at all (live-verified 2026-07-27 — only moneyline/spread/
  total/team_win market types were found across every soccer league). Nothing
  to classify until one appears.
- Three esports leagues (CoD, Rocket League, Overwatch) get real BBO captured
  daily with no possible model — bo3.gg has no discipline for any of them.

**Resolved 2026-07-28** (kept here briefly since they were serious): dota2
and valorant esports discipline IDs were swapped (each model was trained on
the other game's history); tennis's `FeatureStore`/`GameRecord` incompatibility
meant every live tennis forecast silently used zero match history and always
computed exactly 50%; combined ATP+WTA tournaments were mistagging WTA
players' matches as ATP. `League.WORLD_CUP` is now fully retired (enum member
removed, not just dropped from live trading). Full detail in `DEBUG.md`.

Run the checks in `DEBUG.md`. Record failures as failures; never rewrite the
documentation to say the scan passed until tests, Ruff, hashes, the chain, config,
artifacts, and the generated report all agree.
