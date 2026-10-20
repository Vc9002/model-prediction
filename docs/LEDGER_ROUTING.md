# Ledger routing: which sport/market goes into which ledger

**NBA wiring updated**: 2026-10-20 against `cli/daily.py`, `cli/forecast.py`,
`domain.py`, `main_ledgers.py`, and `dashboard/common.py`.

## NBA operational wiring (2026-10-20)

NBA moneyline (`nba-elo-trend-lr-v4`), spread (`nba-spread-margin-v2`), and
total (`nba-total-margin-v2`) are wired to Main + Flat in the daily paper pipeline.
The production-sport, ledger-router, and dashboard-reader lists all include NBA.
Main shows eligible paper calls; Flat records the diagnostic slate. Missing
moneyline quotes are visibly flagged as non-executable; derivative forecasts
require real matched lines and do not fabricate markets. Started games are skipped.

On Windows, the scheduled Daily task invokes `run_supervisor run daily` with
`MODEL_PREDICTION_RUNTIME_ROOT=E:\model-prediction-runtime` and
`MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite`. Canonical operational records live in
`ledgers/ledgers.db`; XLSX files are reporting exports. Dashboard and settlement
discover NBA through the same operational tier routing. The separate production
canary database is not the daily Main/Flat ledger.

The remaining sections describe the older 2026-08-03 topology and are historical
where they conflict with this NBA update.

## The four ledgers

| Ledger | File(s) | Rule |
|---|---|---|
| **Main** | `data/picks.xlsx` | Gated only — CALL decisions (trust-boundary clears + eligibility gate passes) |
| **Flat** | `data/flat_picks.xlsx` | Everything — every candidate, every sport, no edge gate |
| **Research** | `data/research/{sport}.xlsx` | Everything — all candidates for research-only sports |
| **Gated Research** | `data/gated_research/{sport}.xlsx` | Gated only — CALL decisions for research-only sports |

## Main (`picks.xlsx`)

Only rows with `decision == "CALL"` (genuinely eligible picks that pass trust-boundary checks).

| Sport | Markets | Gate |
|---|---|---|
| MLB | moneyline, spread, total | `evaluate_eligibility` |
| WNBA | moneyline | `evaluate_eligibility` |
| SOCCER | moneyline, total | `evaluate_gated_research_eligibility` |
| TENNIS | moneyline (WTA + ATP) | `evaluate_gated_research_eligibility` |

`PRODUCTION_SPORTS = ("mlb", "wnba")` in `domain.py`.

## Flat (`flat_picks.xlsx`)

**Every sport, every candidate, no edge gate.** The "show everything" diagnostic ledger.

- MLB (moneyline, spread, total), NBA, WNBA, NFL — via `_forecast_learned_sport` with `flat_mode=True`
- SOCCER (moneyline, total) — via `_forecast_soccer_sport`
- TENNIS (moneyline, WTA+ATP) — via `_forecast_tennis_sport`

## Research (`data/research/{sport}.xlsx`)

Every candidate for research-only sports. One workbook per sport.

| Sport | Model |
|---|---|
| LOL, CS2, Dota 2, Valorant, Rainbow Six | Platt-scaled neutral series Elo (v5) |
| KBO, NPB | Tie-aware Elo (v2) |

`RESEARCH_LEDGER_SPORTS` in `research_ledgers.py`: lol, cs2, dota2, valorant, rainbow_six, kbo, npb.

SOCCER and TENNIS were removed from Research per operator directive 2026-08-03 — they now route to Main+Flat only.

## Gated Research (`data/gated_research/{sport}.xlsx`)

Curated subset of Research: only rows that clear `evaluate_gated_research_eligibility` (minimum edge + confidence).

Same sports as Research. Same gate as SOCCER/TENNIS use for Main.

## Soccer league expansion (2026-08-03)

11 leagues added: Liga MX, NWSL, Scottish Prem, CSL, Allsvenskan, Austrian Bundesliga, Danish Superliga, Russian Premier, Norwegian Eliteserien, Europa League, Conference League. Total: 29 of 64 gateway leagues now priced.

## Tennis ATP (2026-08-03)

ATP wired into `tennis_forward.py` alongside WTA. Dual-tour loop with per-tour Elo, dedup across combined ATP+WTA tournament endpoints.

## Code references

- `domain.py`: `PRODUCTION_SPORTS`, `LEARNED_PRODUCTION_SPORTS`
- `research_ledgers.py`: `RESEARCH_LEDGER_SPORTS`
- `cli.py`: `_forecast_learned_sport`, `_forecast_mlb_totals_flat`, `_forecast_soccer_sport`, `_forecast_tennis_sport`, `_log_esports_forecast`, `_forecast_international_sport`
- `polymarket_us.py`: `POLYMARKET_SPORT_LEAGUES`, `LEAGUE_SLUGS`
- `espn.py`: `LEAGUE_PATHS`, `SPORT_LEAGUES`
