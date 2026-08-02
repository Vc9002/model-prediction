# model-prediction

Shadow-first multi-sport prediction, research, ledger, and local dashboard
system with Polymarket US market-data integration.

**Last updated**: 2026-08-02

The current operational verdict and audit evidence live in
[`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) and [`DEBUG.md`](DEBUG.md).
The checkout is **not release-ready**, and its real-money execution surface
should not be used until the P0 execution-binding and ledger/audit transaction
defects are repaired.

## Current State

| Metric | Value |
|--------|-------|
| Tests | **624 pass** |
| Ruff | 118 findings (79 EXE002 shebang, ~117 baseline) |
| Git | single `main` branch (no other active branches, local or `origin`) |
| CI | `.github/workflows/ci.yml` — ruff + pytest on push/PR |
| Release status | **Blocked** — see `docs/PROJECT_STATUS.md` for why |

Do not infer executable profitability from artifact hit rates, synthetic
`-110` units, shadow-ledger P&L, or a dashboard qualification badge.

## What's Wired (live in `daily`) and What Feeds It

The operating question for this project day-to-day is **not** "is this model
validated" — it's "is this model actually running, and on what data."

| Sport | Wired in `daily`? | Model / features it actually runs on | Ledger it writes to |
|---|---|---|---|
| MLB moneyline | Yes | `learned_forward.py` — Elo + trend logistic regression (v7 artifact) | Main + Flat |
| MLB totals & spread | Yes | `models/mlb.py` `MeasuredEdgeTotalsModel`/margin — Gamma-Poisson mixture Monte-Carlo, priced against real Polymarket lines | Flat only |
| NBA moneyline | Yes | `learned_forward.py` — Elo + trend logistic regression (v4 artifact) | Flat only (not promoted to Main) |
| WNBA moneyline | Yes | `learned_forward.py` — Elo + trend logistic regression (v4 artifact) | Main + Flat |
| NFL moneyline | Yes (offseason) | `learned_forward.py` — Elo + trend logistic regression (v4 artifact) | Flat only (not promoted to Main) |
| Soccer | Yes | Poisson-Dixon-Coles (`soccer-poisson-dc-v1`) — moneyline, totals, BTTS | Main + Flat (operator override 2026-08-02) + Research |
| Tennis | Yes | WTA surface Elo (`tennis-surface-elo-v1`) | Research + Gated Research |
| LOL | Yes | Platt-scaled neutral series Elo (v5) | Research + Gated Research |
| CS2 | Yes | Platt-scaled neutral series Elo (v5) | Research + Gated Research |
| Dota 2 | Yes | Platt-scaled neutral series Elo (v5) | Research + Gated Research |
| Valorant | Yes | Platt-scaled neutral series Elo (v5) | Research + Gated Research |
| Rainbow Six | Yes | Platt-scaled neutral series Elo (v5) | Research + Gated Research |
| KBO | Yes | Tie-aware Elo (v2) | Research + Gated Research (no Polymarket markets exist) |
| NPB | Yes | Tie-aware Elo (v2) | Research + Gated Research (no Polymarket markets exist) |

## Key design decisions (as of 2026-08-02)

- **Main ledger (MLB/WNBA)**: "show everything, I decide" — no automated gate hides
  a real pick. Operator is the final filter.
- **Gated Research (esports/soccer/tennis)**: "Research shows everything, Gated
  should mean something" — curated tier is deliberately tightened.
- **Unit sizing**: 1.0U–2.0U range, with `model_uncertainty` now actually
  haircutting the edge (was a dead parameter for months; fixed 2026-07-31).
- **New Model Ledger** (`model_ledger.py`): additive architecture — one `.xlsx`
  per model identity, no classification field, operator-decision block separate
  from model output. Existing `PickLedger` unchanged; migration not yet cut over.
- **Dashboard auth**: per-session token on the real-money order-execution endpoint
  (added 2026-08-02; was previously unauthenticated).

## Quick start

```bash
# Run tests
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q

# Lint
.venv/bin/ruff check src/ tests/

# See daily forecast
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli daily --date $(TZ=America/New_York date +%Y-%m-%d)

# Verify audit chain
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain

# Dashboard
python3 dashboard_server.py  # then open http://127.0.0.1:8765/
```

## Documentation index

- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) — operational status and release verdict
- [`docs/HISTORY.md`](docs/HISTORY.md) — chronological project history, all phases
- [`DEBUG.md`](DEBUG.md) — full audit history, every bug found/fixed with trace
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — durable architecture contract
- [`docs/LEDGER_ROUTING.md`](docs/LEDGER_ROUTING.md) — which sport goes into which ledger
- [`docs/MODEL_IMPROVEMENTS.md`](docs/MODEL_IMPROVEMENTS.md) — per-sport feature roadmap
- [`docs/ENGINEERING_ROADMAP.md`](docs/ENGINEERING_ROADMAP.md) — code quality, tests, dashboard gaps
- [`docs/AGENTS.md`](docs/AGENTS.md) — execution rules (walk-forward only, protected files, etc.)
- [`docs/FEATURE_REGISTRY.md`](docs/FEATURE_REGISTRY.md) — what's been tested, what must not be re-tested
- [`CLAUDE.md`](CLAUDE.md) — working guidelines auto-loaded into every session
- [`CHECKLIST.md`](CHECKLIST.md) — maintenance checklist (daily/weekly/monthly)
