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

Legacy research and gated-research ledgers are no longer written. Both tiers have had 0 open rows since
2026-10-24 — there is nothing left for them to settle. The auto-buyer's paper-mode read of
`gated_research` (`portfolio/auto_executor.py:1638-1648`) is inert on data grounds alone, and is further
fail-closed by the started-event gate (`:1751`) and the stale-model gate (`:1826`); live mode already
excludes it. Every remaining reader is inventoried, with a verdict, in the audit section below. History
was copied into Flat and Main by `scripts/merge_research_ledgers.py` (settled rows only, latest per pick
key, original pick ids and results kept, provenance in `decision_payload`: `merged_from_tier`,
`merged_from_pick_id`, `merge_tag`). It is idempotent and safe to re-run; as of 2026-11-12 it reports
nothing left to copy (see audit below).

The dashboard no longer has Research or Gated Research tabs; the backtest, audit and pipeline cards that
shared the old research tab live under **Validation & Audit**.

## Legacy-tier reader audit (2026-11-12)

Audit for Phase 1 item 3 of the ledger-improvement plan (quick task 261112-01r). Read-only snapshot of
`ledgers/ledgers.db` (`mode=ro` URI), re-confirmed at execution time and unchanged from the planning-time
snapshot:

| Tier | open | settled | superseded | removed | total | last created_utc | last settled_utc |
|---|---|---|---|---|---|---|---|
| research | 0 | 645 | 582 | 2 | 1229 | 2026-10-22T05:20Z | 2026-10-24T05:41Z |
| gated_research | 0 | 635 | 580 | 2 | 1217 | 2026-10-22T05:20Z | 2026-10-24T05:42Z |

Flat holds 599 rows with `merged_from_tier=research`; Main holds 587 rows with `merged_from_tier=gated_research`
(`merge_tag research-merge-2026-10-22`). `scripts/merge_research_ledgers.py`, run with no flags (dry run,
opens the DB `mode=ro`): **"source settled rows 1280, duplicate rows dropped 72, already present 1208, to
copy 0."**

### Reader inventory

| Reader | File:line | Role | Verdict |
|---|---|---|---|
| Calibration pipeline | `calibration_pipeline.py:216-222` (removed) | Daily step 4b fed the frozen `research` tier into `calibration_health.json` as its own group; the frozen `dota2-tiered-elo-v6` research cohort (n=62, SEVERE, drift 0.6001) was the sole reason `dota2:moneyline:dota2-tiered-elo-v6` appeared in `severe_drift_models` while live Flat (n=281, 0.2114) and Main (n=253, 0.2541) were both MODERATE | FIXED 2026-11-12 — extraction now ingests Main + Flat only (quick task 261112-01r Task 1) |
| Polymarket edge scanner | `portfolio/polymarket_scanner.py:85` (removed term) | Daily step 10's logged-picks index added the research tier; `_lookup_model_prob`'s cross-date fallback meant a frozen research row could price today's market when no same-date Main/Flat row existed, and every settled research contract already duplicates a Flat row in the pooled Platt fit | FIXED 2026-11-12 — index is now `read_picks() + read_flat_picks()` only (quick task 261112-01r Task 2) |
| Daily settlement loops | `cli/daily.py:1375-1391`, report key `step9_gated_research_settlement` | Settles open rows per tier | SETTLE-ONLY — only touches open rows; 0 open rows exist |
| CLI settle | `cli/settle.py:1672-1695` | Same settlement dispatch via the standalone `settle` command | SETTLE-ONLY — only touches open rows; 0 open rows exist |
| Auto-buyer paper-mode gated read | `portfolio/auto_executor.py:1638-1648` | Builds the paper-mode picks pool from Main + gated (research) + Flat | INERT — 0 open rows, nothing writes the tier, live mode already excludes it, and two gates fail closed before a buy: the started-event gate (`:1751`) and the stale-model gate (`:1826`). Only side effect observed is `rejected_not_open` funnel counts |
| Dashboard production evidence | `dashboard/evidence.py:937-941`, `:1024-1036` | Surfaces research rows in a separately labeled `research_ledger` evidence section | LABELED-HISTORICAL — adds only warnings/blockers, excluded from `performance_complete` |
| Dashboard fingerprint | `dashboard_server.py:259-266` | `(mtime, size)` change detector over ledger paths, including the research tiers | INERT — the frozen files never change, so this term never trips the detector |
| Dashboard cache | `dashboard_cache.py:163` | Caches ledger reads, including the legacy tiers | INERT — a cache; adds nothing on its own |
| Ledger parity audit | `ledger_parity.py` | Cross-checks tier row counts | AUDIT/OFFLINE — a parity check, not a live decision path |
| `scripts/audit_best_picks.py` | settled query + open-pick query | Settled query dedups by (sport, event, market, selection, model) with Main first, so it reads historical evidence correctly; open-pick query reads legacy open rows | AUDIT/OFFLINE for the settled query (correct by construction); INERT for the open-pick query (0 open legacy rows) |
| `scripts/audit_ledger_pnl.py` | `ACTIVE_TIERS` | Reconciliation audit | AUDIT/OFFLINE |
| `scripts/model_performance.py` | — | Reads Main only | N/A — does not read legacy tiers |
| One-off/offline scripts | `esports_analysis.py`, `correct_voided_esports_picks.py`, `clean_split_research_ledgers.py`/`research_cleanup.py`, `merge_research_ledgers.py`, `esports_overhaul_research.py` | Historical analysis, cleanup, and the merge tool itself | AUDIT/OFFLINE — not part of any scheduled or live decision path |

### Phase 2 (full retirement) readiness

Data preconditions are already met: 0 open rows in both `research` and `gated_research`, and the merge
dry run reports nothing left to copy. **Retirement is safe now on data grounds; the remaining work is
code removal only.** "Retirement" means readers stop reading — the rows stay in `ledgers.db` as history
and are never deleted, per CLAUDE.md's ledger-mutation rule (`archive_settled_rows`/`remove_open_rows`
only, never raw deletion).

Concrete removal targets for Phase 2:

- The auto-buyer paper-mode gated read (`auto_executor.py:1638-1648`).
- The daily step 9 legacy settlement loops (`cli/daily.py:1375-1391` and the
  `step9_gated_research_settlement` report key).
- The `cli/settle.py:1672-1695` legacy settlement loops.
- The dashboard fingerprint and production-evidence research paths
  (`dashboard_server.py:265-266`, `dashboard/evidence.py:940` and `:1024-1036`).
- The `dashboard_cache.py` legacy-tier handling.
- `ledger_parity.py`'s research tiers.
- `scripts/audit_ledger_pnl.py`'s `ACTIVE_TIERS`.
- `research_ledgers.py` itself, once every reader above is removed.

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
| NBA | moneyline, spread, total | `evaluate_eligibility` |
| WNBA | moneyline | `evaluate_eligibility` |
| NFL | moneyline, spread, total | `evaluate_eligibility` |
| SOCCER | moneyline, total | `evaluate_gated_research_eligibility` |
| TENNIS | moneyline (WTA + ATP) | `evaluate_gated_research_eligibility` |

`PRODUCTION_SPORTS = ("mlb", "nba", "wnba", "nfl")` in `domain.py` (NBA and NFL added since
this table was first written; `MAIN_LEDGER_SPORTS` in `main_ledgers.py` is the fuller list
covering every sport routed into Main, including esports/KBO/NPB merged 2026-10-22).

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
- `main_ledgers.py`: `MAIN_LEDGER_SPORTS`
- `cli/state.py`: `DUAL_LEDGER_SPORTS`
- `research_ledgers.py`: `RESEARCH_LEDGER_SPORTS` — legacy, frozen 2026-10-22; see audit
- `cli/forecast.py`: `_forecast_learned_sport`, `_forecast_mlb_totals_flat`, `_forecast_soccer_sport`, `_forecast_tennis_sport`, `_log_esports_forecast`, `_forecast_international_sport`
- `polymarket_us.py`: `POLYMARKET_SPORT_LEAGUES`, `LEAGUE_SLUGS`
- `espn.py`: `LEAGUE_PATHS`, `SPORT_LEAGUES`
