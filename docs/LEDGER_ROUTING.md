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

**Current tiers (2026-10-22).** `ledgers/ledgers.db` holds four tiers: `main`, `flat`,
`research`, `gated_research`. The XLSX paths below are old. Live tiers are `main` and
`flat`. Research sports (lol, cs2, dota2, valorant, rainbow_six, kbo, npb) were merged
into `main` and `flat` on 2026-10-22 (next section); `research` and `gated_research`
are legacy history.

## Research ledgers merged into Flat and Main (2026-10-22)

Esports (LoL, CS2, Dota 2, Valorant, Rainbow Six), KBO and NPB no longer have their own
research and gated-research ledgers. They route like every other sport:

- **Flat** gets every priced candidate (what the Research ledger used to hold).
- **Main** gets the rows the gated-research ledger used to hold: `decision == CALL`, which under the
  "every pick is a CALL with units" design includes `PAPER_CALL_*` rows, exactly as Main already does
  for the other sports. Only `QUALIFIED` rows carry real edge; filter on `reason_code` for those.

What changed in code: the seven sports are in `MAIN_LEDGER_SPORTS` and `DUAL_LEDGER_SPORTS`; the daily
run, `forecast --log` and `esports-forecast --log` pass the Flat ledger as the primary ledger and Main as
the gated ledger of `_log_esports_forecast` / `_forecast_international_sport`; and the daily run no
longer clears the legacy ledgers. Settlement needed no change: `_settle_all_unsettled` dispatches on the
row's league, not on the ledger it came from.

Legacy research and gated-research ledgers are no longer written. They stay readable (the auto-buyer still
reads them) and keep settling until their last open rows close. History was copied into Flat and Main by
`scripts/merge_research_ledgers.py` (settled rows only, latest per pick key, original pick ids and
results kept, provenance in `decision_payload`: `merged_from_tier`, `merged_from_pick_id`, `merge_tag`).
Re-run it after the remaining open legacy rows settle; it is idempotent.

The dashboard no longer has Research or Gated Research tabs; the backtest, audit and pipeline cards that
shared the old research tab live under **Validation & Audit**.

## The four ledgers (2026-08-03 layout, historical)

| Ledger | File(s) | Rule |
|---|---|---|
| **Main** | `data/picks.xlsx` | Gated only — CALL decisions (trust-boundary clears + eligibility gate passes) |
| **Flat** | `data/flat_picks.xlsx` | Everything — every candidate, every sport, no edge gate |
| **Research** *(legacy)* | `data/research/{sport}.xlsx` | Historical only — merged into Flat on 2026-10-22 |
| **Gated Research** *(legacy)* | `data/gated_research/{sport}.xlsx` | Historical only — merged into Main on 2026-10-22 |

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

## Research (`data/research/{sport}.xlsx`) — legacy, merged into Flat 2026-10-22

Every candidate for research-only sports. One workbook per sport.

| Sport | Model |
|---|---|
| LOL, CS2, Dota 2, Valorant, Rainbow Six | Platt-scaled neutral series Elo (v5) |
| KBO, NPB | Tie-aware Elo (v2) |

`RESEARCH_LEDGER_SPORTS` in `research_ledgers.py`: lol, cs2, dota2, valorant, rainbow_six, kbo, npb.

SOCCER and TENNIS were removed from Research per operator directive 2026-08-03 — they now route to Main+Flat only.

## Gated Research (`data/gated_research/{sport}.xlsx`) — legacy, merged into Main 2026-10-22

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
