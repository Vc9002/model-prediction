# NCAAF line-source audit (v6 item 7: market-anchored residual feasibility)

AS_OF_UTC: 2026-11-15T02:10:00Z

## Verdict: item 7 BLOCKED

- Rule: proceeds only if a separable set of pregame-lined (spread or total, with a pregame observed_at) events exists outside the reference events and predates the earliest reference event.
- Reference events: 577 (575 with a total line, 576 with a spread line), starts 2026-08-29T16:00:00Z .. 2026-11-14T21:30:00Z (reference file C:/Users/Vince/AppData/Local/Temp/claude/3c4e37e4a6ec2934/f86acfae-6a7a-455a-99d3-5fcdba232520/scratchpad/v5_games.json, sha256 31f2a9b83bec29d8).
- Pregame-lined events outside the reference that PREDATE its earliest event: 0 (max over sources).
- Earliest line-bearing event start in any source: 2026-08-29T16:00:00Z.
- Calendar years with any pregame line: 2026.

Same-season pool outside the reference (not predating, interleaved; counts of pregame-lined events, and the subset with a result in the history files):
- polymarket:repo data/odds/ncaaf: 460 outside, 40 with a result, 0 after the latest reference event.
- polymarket:runtime odds/ncaaf: 524 outside, 40 with a result, 31 after the latest reference event.
- runtime market_quotes.db: 419 outside, 40 with a result, 0 after the latest reference event.
- ledger: live ledgers.db: 30 outside, 5 with a result, 24 after the latest reference event.
- espn scoreboard consensus (DraftKings via ESPN): 25 outside, 0 with a result, 24 after the latest reference event.
- runtime research/college_contract_candidates: 64 outside, 0 with a result, 31 after the latest reference event.
- xlsx:data/model_ledgers/ncaaf-spread-baseline.xlsx: 106 outside, 42 with a result, 24 after the latest reference event.
- xlsx:data/model_ledgers/ncaaf-total-baseline.xlsx: 109 outside, 42 with a result, 24 after the latest reference event.
- xlsx:data/sports/ncaaf/flat.xlsx: 24 outside, 0 with a result, 24 after the latest reference event.
- xlsx:data/sports/ncaaf/main.xlsx: 9 outside, 0 with a result, 9 after the latest reference event.
- auto-buyer ledgers: 17 outside, 0 with a result, 17 after the latest reference event.
- Note: Outside-reference pregame-lined events all start on or after the reference's earliest event, so they are same-season, interleaved with the reference (no walk-forward separation). Polymarket ones are ladders (many alternate rungs, no single line) from 2026-09-04; many are FBS-vs-FCS games with no result in the history files (the known FCS gap), so only the 'with_result_in_history' subset is scorable without a backfill (name-matched, so approximate). Postgame/reconstructed prices are excluded from every count.

Unblock: Pregame spread and total quotes WITH a per-quote observed_at (or an archived pre-kickoff snapshot with fetch time) for NCAAF games of the 2016-2025 seasons, keyed by (start, home, away) or ESPN event_id so they join data/historical/ncaaf_games_all.jsonl (7,965 results, 2016-2026, no lines). Needed volume: enough events before 2026-08-29 to fit and validate the residual on data that is not the reference set. A single consensus line per event (opening and closing, with times) is sufficient; Polymarket ladders only exist from 2026-09-04. Postgame or reconstructed prices stay diagnostic only (docs/AGENTS.md); data/experiments/cfb_research_experiment_spec.json's 'market_seasons 2020-2024' is an aggregate claim with no rows behind it.

## Inventory

| Source | Files | Rows (ncaaf) | Event start range | S | T | ML | Pregame S | Pregame T | Pregame S-or-T events | Outside ref | Predating |
|---|---|---|---|---|---|---|---|---|---|---|---|
| history:data/historical/ncaaf_games_all.jsonl | 1 | 7965 | 2016-08-27 .. 2026-11-14 | no | no | no | - | - | 0 | - | - |
| history:data/processed/ncaaf/games.jsonl | 1 | 7965 | 2016-08-27 .. 2026-11-14 | no | no | no | - | - | 0 | - | - |
| history:data/historical other files | 9 | 0 | - .. - | no | no | no | - | - | 0 | - | - |
| history:data/odds/historical | 1 | 0 | - .. - | no | no | no | - | - | 0 | - | - |
| polymarket:repo data/odds/ncaaf | 34 | 161126 | 2026-09-04 .. 2026-11-05 | yes | yes | yes | 811 | 782 | 829 | 460 | 0 |
| polymarket:runtime odds/ncaaf | 41 | 166395 | 2026-09-04 .. 2026-11-15 | yes | yes | yes | 907 | 878 | 926 | 524 | 0 |
| runtime market_quotes.db | 1 | 293287 | 2026-09-04 .. 2026-11-14 | yes | yes | yes | 747 | 709 | 757 | 419 | 0 |
| snapshots:data/market_odds_snapshots.jsonl | 1 | 0 | - .. - | no | no | no | - | - | 0 | - | - |
| snapshots:data/polymarket_us_snapshots.jsonl | 1 | 0 | - .. - | no | no | no | - | - | 0 | - | - |
| ledger: live ledgers.db | 1 | 6129 | 2026-08-29 .. 2026-11-15 | yes | yes | yes | 606 | 605 | 606 | 30 | 0 |
| runtime ledger backups | 7 | - | 2026-08-29 .. - | yes | yes | yes | - | - | 0 | - | - |
| espn scoreboard consensus (DraftKings via ESPN) | 30 | 297 | 2026-11-14 .. 2026-11-15 | yes | yes | yes | 56 | 56 | 56 | 25 | 0 |
| runtime research/college_contract_candidates | 80 | 118450 | 2026-11-05 .. 2026-11-15 | yes | yes | yes | 92 | 94 | 95 | 64 | 0 |
| xlsx:E:/model-prediction-runtime/backups/restore_unreplaced_2026-11-10_20261111T021040Z/E_MP_data_main_ncaaf.xlsx | 1 | 1051 | 2026-09-07 .. 2026-11-08 | yes | yes | yes | 384 | 381 | 438 | 0 | 0 |
| xlsx:data/archive/2026-08-30-ncaaf-synthetic-main-quarantine/flat_ncaaf.xlsx | 1 | 40 | 2026-08-29 .. 2026-08-30 | yes | yes | yes | 8 | 8 | 8 | 0 | 0 |
| xlsx:data/archive/2026-08-30-ncaaf-synthetic-main-quarantine/ncaaf.xlsx | 1 | 16 | 2026-08-29 .. 2026-08-30 | no | yes | no | 0 | 8 | 8 | 0 | 0 |
| xlsx:data/flat/ncaaf.xlsx | 1 | 853 | 2026-08-29 .. 2026-10-04 | yes | yes | yes | 300 | 300 | 300 | 0 | 0 |
| xlsx:data/flat/ncaaf.xlsx.pre_clear.bak | 1 | 853 | 2026-08-29 .. 2026-10-04 | yes | yes | yes | 300 | 300 | 300 | 0 | 0 |
| xlsx:data/main/ncaaf.xlsx | 1 | 829 | 2026-09-07 .. 2026-10-04 | yes | yes | yes | 292 | 292 | 292 | 0 | 0 |
| xlsx:data/main/ncaaf.xlsx.pre_clear.bak | 1 | 829 | 2026-09-07 .. 2026-10-04 | yes | yes | yes | 292 | 292 | 292 | 0 | 0 |
| xlsx:data/model_ledgers/ncaaf-moneyline-baseline.xlsx | 1 | 1822 | 2026-09-04 .. 2026-11-15 | no | no | yes | 0 | 0 | 0 | 0 | 0 |
| xlsx:data/model_ledgers/ncaaf-spread-baseline.xlsx | 1 | 2037 | 2026-09-04 .. 2026-11-15 | yes | no | no | 674 | 0 | 674 | 106 | 0 |
| xlsx:data/model_ledgers/ncaaf-total-baseline.xlsx | 1 | 2042 | 2026-09-04 .. 2026-11-15 | no | yes | no | 0 | 676 | 676 | 109 | 0 |
| xlsx:data/sports/ncaaf/flat.xlsx | 1 | 742 | 2026-08-29 .. 2026-11-15 | yes | yes | yes | 236 | 237 | 281 | 24 | 0 |
| xlsx:data/sports/ncaaf/main.xlsx | 1 | 1088 | 2026-09-07 .. 2026-11-15 | yes | yes | yes | 395 | 399 | 465 | 9 | 0 |
| auto-buyer ledgers | 10 | 42 | 2026-11-11 .. 2026-11-15 | yes | yes | yes | 10 | 28 | 31 | 17 | 0 |
| db:E:/model-prediction-runtime/production/production.db | 1 | 0 | - .. - | no | no | no | - | - | 0 | - | - |
| db:E:/model-prediction-runtime/rebuild/shadow.db | 1 | 0 | - .. - | no | no | no | - | - | 0 | - | - |
| db:E:/MP/data/rebuild/shadow.db | 1 | 0 | - .. - | no | no | no | - | - | 0 | - | - |
| db:E:/model-prediction-runtime/runs.db | 1 | 0 | - .. - | no | no | no | - | - | 0 | - | - |
| db:dashboard_cache.db (derived from ledger) | 1 | 1830 | 2026-08-29 .. 2026-11-15 | yes | yes | yes | - | - | None | - | - |
| experiment spec claim | 1 | 0 | - .. - | no | no | no | - | - | 0 | - | - |

## Timestamp basis per source

- history:data/historical/ncaaf_games_all.jsonl: not applicable: game results only, no quotes. Scores/context only. No line, odds or consensus field exists (key scan above).
- history:data/processed/ncaaf/games.jsonl: not applicable: game results only, no quotes. Scores/context only. No line, odds or consensus field exists (key scan above).
- history:data/historical other files: not applicable. Other sports only (mlb_market_lines_reconstructed is MLB); no NCAAF rows.
- history:data/odds/historical: not applicable. mlb_sbr_odds.json only (sportsbook-review MLB); zero NCAAF/college-football mentions.
- polymarket:repo data/odds/ncaaf: per-row observed_at_utc (live gateway BBO capture); pregame iff observed_at <= event start. first folder 2026-09-04, last 2026-11-04
- polymarket:runtime odds/ncaaf: per-row observed_at_utc (live gateway BBO capture); pregame iff observed_at <= event start. first folder 2026-09-04, last 2026-11-14
- runtime market_quotes.db: per-row observed_at_utc; event start joined from polymarket odds files by slug. event_id is the polymarket slug; slug date range 2026-09-04T00:00:00Z .. 2026-11-14T00:00:00Z; 13 (event, market) groups with no start resolved from odds files. Same capture lineage as data/odds/ncaaf (not independent).
- snapshots:data/market_odds_snapshots.jsonl: per-row observed_at_utc. Zero NCAAF rows (MLB/WNBA snapshot files predating the college-football season).
- snapshots:data/polymarket_us_snapshots.jsonl: per-row observed_at_utc. Zero NCAAF rows (MLB/WNBA snapshot files predating the college-football season).
- ledger: live ledgers.db: decision-time observed_at_utc (decision_payload) <= event start; the line itself is the value quoted when the pick was decided. Per-quote market_quote_observed_at_utc is mostly blank. Lines exist only for events a model picked (selection bias: not every game). Event ids are ESPN ids. This is the source the reference events were drawn from.
- runtime ledger backups: same ledger_records schema as the live DB. Snapshots of the same ledger at earlier dates; they add no events beyond the live ledger unless the count above is non-zero.
- espn scoreboard consensus (DraftKings via ESPN): no embedded fetch time; kind uses event state 'pre' at snapshot AND file mtime <= kickoff. Snapshots of started/completed games carry a closing-style line captured after kickoff. events with an odds block: 56 of 297; odds-block provider counts {'DraftKings': 56}; snapshot state counts {'pre': 56}. Folder dates 2026-10-16.. only: same season as the reference events.
- runtime research/college_contract_candidates: quote_observed_at_utc per row (quote_hash + archive path recorded). Decision-time views of the polymarket captures above (same lineage), 2026-11-04 onward.
- xlsx:E:/model-prediction-runtime/backups/restore_unreplaced_2026-11-10_20261111T021040Z/E_MP_data_main_ncaaf.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- xlsx:data/archive/2026-08-30-ncaaf-synthetic-main-quarantine/flat_ncaaf.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable). QUARANTINED synthetic main rows (archive 2026-08-30): not usable.
- xlsx:data/archive/2026-08-30-ncaaf-synthetic-main-quarantine/ncaaf.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable). QUARANTINED synthetic main rows (archive 2026-08-30): not usable.
- xlsx:data/flat/ncaaf.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- xlsx:data/flat/ncaaf.xlsx.pre_clear.bak: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- xlsx:data/main/ncaaf.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- xlsx:data/main/ncaaf.xlsx.pre_clear.bak: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- xlsx:data/model_ledgers/ncaaf-moneyline-baseline.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- xlsx:data/model_ledgers/ncaaf-spread-baseline.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- xlsx:data/model_ledgers/ncaaf-total-baseline.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- xlsx:data/sports/ncaaf/flat.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- xlsx:data/sports/ncaaf/main.xlsx: observed_at_utc (decision time) per row. Best-effort XLSX export of the ledger (disposable).
- auto-buyer ledgers: executed_at_utc of the order. Executed paper orders only; NCAAF auto-buying is disabled so only a handful of rows exist.
- db:E:/model-prediction-runtime/production/production.db: not applicable: no NCAAF rows. Checked for NCAAF line rows; none.
- db:E:/model-prediction-runtime/rebuild/shadow.db: not applicable: no NCAAF rows. Checked for NCAAF line rows; none.
- db:E:/MP/data/rebuild/shadow.db: not applicable: no NCAAF rows. Checked for NCAAF line rows; none.
- db:E:/model-prediction-runtime/runs.db: not applicable: no NCAAF rows. Checked for NCAAF line rows; none.
- db:dashboard_cache.db (derived from ledger): derived cache of ledger picks; no independent quotes. Cache of ledger_records payloads; adds no event beyond the ledger.
- experiment spec claim: none: aggregates only. Claims 4,510 lined games over market_seasons 2020-2024. Generator traced: scripts/cfb_research_pipeline.py loads data/historical/ncaaf_games_all.jsonl, which has no line fields, and evaluates every game with g.get('spread_home_line', 0.0), g.get('total_line', 54.0), g.get('market_ml_home_prob') or 0.50 and a fixed 0.5238 ask (spread/total paths at lines ~274-317, 'eligible_games' = every 2020+ game). So the 4,510 'lined' games carry defaulted constants, not quotes: synthetic, not data. The 2026-08-30 quarantine archive is likewise 'synthetic main'.

## Residual sweep (files outside the sources above that mention NCAAF)

- scanned 10271 files (skipped dirs: availability, providers, esports, statcast, mlb_statsapi, weather, raw, odds, processed, international_baseball, calibration, player_priors, entities, statsapi, logs, sports, archive, backups; files over 300 MB skipped)
- E:/MP/data/auto_buyer_state.json (7 mentions)
- E:/MP/data/research/ncaaf_v6/history_snapshot.meta.json (5 mentions)
- E:/MP/data/research/ncaaf_v6/ncaaf_games_backfill.jsonl (49134 mentions)
- E:/MP/data/research/ncaaf_v6/tracer/snap.meta.json (3 mentions)
- E:/model-prediction-runtime/audit.jsonl (3 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json (9 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-cfb-disable-20261110-222911.bak (11 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-enable-live-20261111-035321.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-live-off-20261111-035637.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-lookahead-1h-20261111-220014.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-nba-ml-champion-swap-20261111-170439.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-nba-ml-v5-20261113-191519.bak (8 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-nba-v9-whitelist-pull-20261113-135938.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-ncaab-live-run-20261111-031259.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-ncaab-retry-20261111-031747.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-ncaab-side-audit-20261111-032129.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-ncaaf-enable-nba-spread-keep-20261113-140813.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-refresh-fix-20261111-031559.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.json.pre-safety-pause-20261111-030648.bak (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.pre_ncaab_bucket_20261111T034555Z.json (10 mentions)
- E:/model-prediction-runtime/auto_buyer_state.repo-seed.pre-cfb-disable-20261110-222911.bak (8 mentions)
- E:/model-prediction-runtime/dashboard-inline-check.js (3 mentions)
- E:/model-prediction-runtime/dashboard-staged.html (5 mentions)
- E:/model-prediction-runtime/dashboard-staged.js (3 mentions)
- E:/model-prediction-runtime/runs.db (12 mentions)
- E:/model-prediction-runtime/ledgers/ledgers.pre-dropped-settle-20261021T182116Z.db (71232 mentions)
- E:/model-prediction-runtime/ledgers/ledgers.pre-relabel-superseded-20261021T190807Z.db (72163 mentions)

Percentages and lines only; American-odds fields are noted as present, never printed.
