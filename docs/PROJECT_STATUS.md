# Project status and source of truth

**Last verified**: 2026-08-02, single `main` branch. **624 tests pass, 0 fail.**

This document is the operational status entry point. `DEBUG.md` contains the
full audit evidence and reproduction commands. Historical metrics in old
reports, changelog entries, model cards, and rollback artifacts are not current
operational truth.

**Operating note**: day-to-day work on this project prioritizes *wiring and
features* over validation metrics — is a model actually running in `daily`,
and on what data, not its hit rate or promotion-gate status. The release
verdict below is a separate, narrower claim about real-money execution safety
specifically, and remains unchanged by wiring work.

## Active model versions (2026-08-02)

| Sport | Active artifact | Status | Hit rate | Qualification |
|---|---|---|---|---|
| MLB moneyline | `mlb-elo-trend-lr-v7` | shadow_qualified (override) | 58.0% locked-holdout | `qualified=false` (does not clear 60% bar); honest rebuild, no broken coefficient |
| MLB spread | `measured-edge-margin-v2` | active_research | — | Flat only, zero-unit |
| MLB totals | `measured-edge-totals-v2` | active_research | — | Flat only, zero-unit |
| NBA moneyline | `nba-elo-trend-lr-v4` | shadow_qualified | 73.66% | `qualified=true` |
| WNBA moneyline | `wnba-elo-trend-lr-v4` | shadow_qualified | 67.48% | `qualified=true` |
| NFL moneyline | `nfl-elo-trend-lr-v4` | shadow_qualified | 71.26% | `qualified=true` (offseason) |
| Soccer | `soccer-poisson-dc-v1` | shadow_qualified (operator override) | 62.5% locked-holdout | No walk-forward artifact exists; override not genuine promotion |
| LOL | `lol-tiered-elo-v5` | shadow_qualified (override) | — | v5 Platt-scaled, proper scoring rules |
| CS2 | `cs2-tiered-elo-v5` | shadow_qualified (override) | — | v5 Platt-scaled |
| Dota 2 | `dota2-tiered-elo-v5` | shadow_qualified (override) | — | v5 Platt-scaled |
| Valorant | `valorant-tiered-elo-v5` | shadow_qualified (override) | — | v5 Platt-scaled |
| Rainbow Six | `rainbow_six-tiered-elo-v5` | research | — | v5 Platt-scaled |
| KBO | `kbo-tie-aware-elo-v2` | shadow_qualified (override) | — | Tie-aware, zero-unit research only |
| NPB | `npb-tie-aware-elo-v2` | shadow_qualified (override) | — | Tie-aware, zero-unit research only |
| Tennis | `tennis-surface-elo-v1` | research | — | WTA only |

## Ledger routing (definitive: `docs/LEDGER_ROUTING.md`)

- **Main** (`picks.xlsx`): MLB moneyline, WNBA moneyline, Soccer (override) — real-sized calls
- **Flat** (`flat_picks.xlsx`): NBA, NFL moneyline (zero-unit) + MLB spread/totals (zero-unit)
- **Research** (`data/research/{sport}.xlsx`): Esports (5 titles), Tennis, KBO, NPB — all candidates
- **Gated Research** (`data/gated_research/{sport}.xlsx`): Curated subset clearing per-sport edge/confidence bars
- **Model Ledgers** (`data/model_ledgers/`): New per-model-identity architecture (additive; existing pipeline unchanged)

## Runtime snapshot (2026-08-02)

- **Tests**: 624 passed, 0 failed
- **Ruff**: 118 findings (79 are EXE002 shebang-on-test-files; 47 of those are in dashboard_server.py which is excluded from the main `src/` count; baseline ≈117)
- **Audit chain**: 43,304 events, 0 breaks, 0 hash mismatches
- **Git**: Single `main` branch; working tree has 82 dirty files (session 2026-08-02 work uncommitted)
- **CI**: `.github/workflows/ci.yml` — ruff + pytest on push/PR
- **Dashboard**: Live at `127.0.0.1:8765`, launchd-managed, token-based auth on orders
- **Daily pipeline**: Running through 2026-08-02, logs in `data/logs/`
- **Console entry point**: `.venv/bin/model-prediction` works
- **BBO capture**: Active across 8 sports (`data/odds/`)

## Release verdict

**Not release-ready.** The real-money execution surface should not be used until
P0 defects are repaired:

1. Execution tickets are not bound to the exact qualified ledger row
2. Ledger mutation and audit append are not atomic
3. Artifact qualification and quote `timestamp_valid` are not enforced at the
   classification/pricing step
4. `config/model.yaml` references `market-residual-v1.json` which doesn't exist
5. `mlb-spread-baseline-v1.json` is used for both spread AND total research
6. Two artifact hashes are mismatched (`nba-spread-baseline-v1.json`,
   `nfl-spread-baseline-v1.json`)

Do not infer executable profitability from artifact hit rates, synthetic
`-110` units, shadow-ledger P&L, or a dashboard qualification badge.

## Bugs found and fixed since last verification (2026-07-28 → 2026-08-02)

1. **Unit sizing dead parameter** — `model_uncertainty` accepted at 6 call sites but never read; fixed 2026-07-31
2. **NPB destructive overwrite** — historical data overwritten on each forecast run
3. **CLV scanning only Main** — Flat/Research/Gated never got closing prices
4. **Weather park-factor key collision** — A's/River Cats silently zeroed weather
5. **MLB rehab-assignment marker missing** — 291 real transactions skipped in availability feature
6. **MLB same-day transaction ambiguity** — strict `<` not `<=` for PIT safety
7. **Roster snapshots captured but never read** — dead-weight capture in availability pipeline
8. **Dashboard token-based auth** — real-money order endpoint now requires per-session token (was unauthenticated)
9. **SELL-path P&L formula** — BUY and SELL now use single canonical settlement function

## Safe command forms

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
.venv/bin/model-prediction --help
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary
```

Commands with `--write-artifacts`, `--log`, settlement, ledger cleanup, ban
mutation, dashboard POST routes, `daily`, `execute`, or `sell-position` change
state. They require separate authorization appropriate to the risk.

## Repair order

1. Hard-block real-money execution until tickets are bound to the exact ledger
   row and all economics are recomputed server-side.
2. Make ledger mutation and audit append recoverable as one transaction; add
   failure-injection and retry tests.
3. Enforce artifact qualification, quote `timestamp_valid`, and fail-closed
   availability semantics at the classification step.
4. Fix the two mismatched artifact hashes and the `market-residual-v1.json`
   config reference without overwriting rollback artifacts.
5. Point MLB total research at a total artifact, not the spread artifact.
6. Make exposure-check-plus-append transactional and enforce one writer across
   all ledgers.
7. Fix secret redaction, timestamp age, discovery pagination, and economic-CI
   semantics.
8. Replace `pkill -f` dashboard startup with PID-file approach.
9. Split `cli.py` and `dashboard_server.py` into packages; add `tests/test_cli.py`.
10. Migrate ledger storage to SQLite for ACID guarantees.
