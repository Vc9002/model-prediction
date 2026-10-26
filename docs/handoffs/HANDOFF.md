# Handoff — open work (updated 2026-10-26)

**Status: OPEN.** Delete this file when every item below is done or explicitly dropped.

## 1. Rename project folder to `E:\MP` (DONE 2026-10-21)

- Old `E:\MP` junction removed by hand. Project folder renamed `E:\model-prediction` → `E:\MP`.
- Seven scheduled tasks repointed to `E:\MP` and verified: `ModelPrediction-AutoBuyer`, `-BackupOffsite`, `-Calibrate`, `-Daily`, `-Production`, `-RebuildShadow`, `ModelPredictionDashboard`.
- Dashboard restarted from `E:\MP`; `GET /` returns 200. `tests/test_dashboard_server.py` passes (72/72).
- Old absolute paths fixed in `scripts/evaluate_mls_overhaul.py`, `scripts/replay_expanded_historical_soccer.py`, `scripts/replay_settled_soccer_strategies.py`, and `scripts/start_dashboard.ps1`.
- Not changed: `MODEL_PREDICTION_RUNTIME_ROOT` (`E:\model-prediction-runtime`) and the `MP-runtime` junction.
- Fixed 2026-10-22: the editable install in `.venv` still pointed at `E:\model-prediction\src`, so `import model_prediction` failed without `PYTHONPATH=src` (every scheduled task except the dashboard would have failed on its next run), and `model-prediction.exe` gave "uv trampoline failed to canonicalize script path". Ran `uv pip install --no-deps -e .` from `E:\MP`; both now work.
- Fixed 2026-10-22: reinstalled pytest, ruff, mypy, hypothesis (same versions, --no-deps); their launchers work. Still stale (unused, runtime packages left alone to avoid breaking a running job): httpx, numpy, fonttools, tqdm, pygments, charset-normalizer, idna, cffi, optype launchers.

Steps, in order: stop the dashboard; remove the `E:\MP` junction by hand; rename `E:\model-prediction` to `E:\MP`; update the seven task actions; verify the dashboard and the next scheduled run start from the new path.

## 2. NCAAF ledger duplicate settled picks (script ready, run manually)

Settled Main rows in `E:\model-prediction-runtime\ledgers\ledgers.db`:

- `cfb-structural-v2`: 148 rows, 116 distinct picks (18 duplicate keys)
- `cfb-spread-v1`: 296 rows, 290 distinct
- `cfb-total-v1`: 294 rows, 293 distinct

Resolved in code: commit `b77a563d` (2026-10-21, 14:59 local) changes re-forecasts to mark the old pick `superseded` instead of duplicating it. No duplicate keys were written after that commit; the most recent one is from 2026-10-20.

Re-counted 2026-10-22 (read-only, settled Main rows, key = event, market, selection, line, model): `cfb-structural-v2` 150 rows / 119 distinct keys (18 duplicate keys), `cfb-spread-v1` 296 / 294 (2), `cfb-total-v1` 294 / 293 (1). 21 duplicate keys in all. The row counts moved because more picks settled since the figures above, and `cfb-spread-v1` is 294 distinct, not 290.

- Duplicates are not identical: re-forecasts changed probability, price and units for 6 keys (for example `cfb-structural-v2` total, event 401856722, over 43.5: 1.25U at 04:57, then 1.5U twice).
- Effect on `cfb-structural-v2` units: all rows summed +2.50, keep-first +9.41, keep-latest +9.09. So counting the duplicates understates it by about 7 units, and keep-first vs keep-latest differ by only 0.3U. `cfb-spread-v1` is -34.14 raw vs -32.14 deduped, `cfb-total-v1` is -4.25 raw vs -3.25.
- Why the duplicates differ (checked): same model artifact hash, but the probability engine's source hash changed between the 05:04 and 10:27 runs on 10-17 (`engine_source_sha256`), so the same inputs produced a different probability and size; later differences are live odds moving between runs.
- Recommended rule: keep the latest row per key and mark the older rows `superseded` (not deleted). It matches what `b77a563d` does going forward. Keep-first vs keep-latest differ by 0.3U on `cfb-structural-v2`.
- Backup taken 2026-10-22 01:38 EDT: `E:\model-prediction-runtime\backups\ledgers\ledgers_20261022T013809_pre-dedupe.db` (12,126 records, integrity ok). The dedupe and the item 5 merge have not been run. Do each only when no scheduled run is writing, and take a fresh backup first if much time has passed.

**2026-10-26 update**: Count is now 1,000 duplicate keys / 1,026 rows to mark superseded (grown since 10-22 as more games settled and `college-football-v1` included). Fresh backup taken: `E:\model-prediction-runtime\backups\ledgers\ledgers_20261026T171805_pre-ncaaf-dedup.db`. Dedup script at `scripts/dedup_ncaaf_settled_picks.py` — dry-run verified. Run when no scheduled job is writing:

```powershell
cd E:\MP; $env:PYTHONPATH='src;.'; .venv\Scripts\python scripts/dedup_ncaaf_settled_picks.py
```

Still open: run the script above to mark 1,026 rows superseded. Confirm on the next forecast run that no new duplicates appear.

## 3. NCAAF champion — DONE (2026-10-26)

`cfb-structural-v3` is production champion for all three NCAAF markets (`serving_status: production`, `market: multi` in `config/production.yaml`). `cfb-structural-v2` is the rollback. `CFB_PREDICTION_SYSTEM.md` updated to reflect this.

## 4. `cfb-total-v1` backtest does not replicate live — DONE (committed 2026-10-23)

- Backtest (`CFB_PREDICTION_SYSTEM.md` §5): +29.17% ROI over 4,510 games, 2020–2024.
- Live ledger (`CFB_PREDICTION_SYSTEM.md` §7): about -1.1% ROI over 293 distinct picks.

**Audited 2026-10-22 (read-only): the +29.17% is not a market result. Do not cite it, and treat the §5 spread and moneyline rows the same way.**

- `data/historical/ncaaf_games_all.jsonl` has no market data. Of 4,781 games from 2020 on, none has `total_line`, `spread_home_line` or `market_ml_home_prob`.
- `scripts/cfb_research_pipeline.py` (lines ~274-275, ~290) falls back with `g.get("total_line", 54.0)`, `g.get("spread_home_line", 0.0)` and `g.get("market_ml_home_prob") or 0.50`. So every "market" row was scored against a constant: totals against 54.0, spreads against a pick'em line, moneyline against 50%, all at a flat -110 price.
- Why 66.97% hit rate: against a constant 54.0, a model with any game-level signal wins easily (a naive bet would win about 50%: over 47.4%, under 50.5%, push 2.1%). A real closing line already contains that signal, so this says nothing about edge over the market. It is consistent with the live result of about -1% ROI over 293 picks.
- Provenance (found after the above): `docs/SYSTEM_DEFECTS_AND_GAPS_AUDIT.md` A-1/A-2 already records that the original 66.97% / +29.17% came from a seeded RNG simulator with a circular market line, closed when the cohort was replaced by real ESPN games. The §5 table was never regenerated. Rerunning the pipeline on the current file would use the constants above (not re-run). `docs/MASTER.md` line 9 is marked superseded; `docs/PROJECT_STATUS.md` line 5 still says "+29% in the 2020–2024 backtest" without saying it is invalid.
- Features are point-in-time: `_compute_team_state` uses only games strictly before the game start. The one mild exception is `wind_mph` and `temperature_f`, which are the game's own recorded weather, not a forecast at decision time.
- Unexplained: the doc says N = 4,510 eligible games, but the 2020+ cohort in the file is 4,781.
- To get a real number you need historical NCAAF closing or decision-time lines. None are in the repo (only `mlb_market_lines_reconstructed.jsonl` and NFL lines exist). Until then, the live ledger (§7) is the only valid evidence.
- Doc fixed 2026-10-22 (uncommitted, in the working tree): `docs/CFB_PREDICTION_SYSTEM.md` §5 now carries a correction and marks all three rows invalid, and the two other places that leaned on the backtest (header line 5, §7 bullet) were updated. The file has other uncommitted edits, so stage only these hunks when committing.

## 5. Merge research and gated-research ledgers into Flat and Main (DONE 2026-10-22, commits `524b1d78` + follow-up)

- Done: esports (lol, cs2, dota2, valorant, rainbow_six), KBO and NPB now write every candidate to Flat and the gated CALL subset to Main (`decision == CALL`, which includes `PAPER_CALL_*` rows, same as Main for every other sport). They are in `MAIN_LEDGER_SPORTS` / `DUAL_LEDGER_SPORTS`. The legacy research and gated-research ledgers are no longer written or cleared; they stay readable (the auto-buyer still reads them, and was not touched) and keep settling.
- The "gate fix" proposed below was dropped: Main already holds paper calls for other sports, so mirroring `decision == CALL` is the established behavior.
- History backfilled 2026-10-22 with `scripts/merge_research_ledgers.py --apply` (settled rows, latest row per pick key, through the hash-chained store): Flat 567 rows (-0.44U), Main 555 rows (-1.57U); chain intact; workbook exports rebuilt (14). Pre-merge backup: `E:\model-prediction-runtime\backups\ledgers\ledgers_20261022T041612_pre-research-merge.db`.
- Dashboard: Research / Gated Research tabs and their `/api/research-*` and `/api/gated-research-*` routes removed; the backtest / audit / pipeline cards live under a new Validation & Audit tab. Dashboard restarted and verified (Main shows the 555 rows, Flat the 567).
- Calibration of the merged history (2026-10-22, 465 settled win/loss rows in Flat, read-only): hit 55.7% vs 57.7% average predicted (gap +2.0pp), ECE 0.021; Brier 0.2460 vs 0.2344 for the market's own probability, so the models do not beat the market. Calibrated from 50% to 70% predicted, but the 70-80% bin (n=25) hits 52% vs 72% predicted. The 143 `QUALIFIED` rows hit 49.0% vs 61.5% predicted (Brier 0.2642 vs market 0.2505), so the picks flagged as having edge are the most overconfident. cs2 moneyline (n=315) is the only model with enough data to judge; valorant, kbo and cs2 total are under 20 rows. Calibrator artifacts exist for cs2 (isotonic, n=333), kbo (temperature 0.8), lol and valorant (temperature) and are written by the calibration pipeline; whether they were fit on overlapping tiers (Main, Flat and legacy research hold the same history) is unchecked.
- Regression found and fixed 2026-10-22: after the dashboard cleanup, `mountFilterBars` still wrote to the removed `#filters_R` / `#filters_G`, throwing at page load so no ledger rendered (the Model Picks Ledger showed 0 picks). Fixed and covered by `test_unguarded_filter_bar_mounts_target_elements_that_exist`. Lesson: the node stub-DOM smoke test could not catch it (its fake elements are never null); verify dashboard changes in a real browser (Playwright with `C:\Program Files\Google\Chrome\Application\chrome.exe`, fresh context, click every tab, collect `pageerror`). Verified after the fix: all 17 tabs open with 0 errors, Model Picks Ledger shows 31 active picks, Flat and Validation & Audit load.
- Still to do: (1) check the first real daily run (07:30 EDT 2026-10-22) logs esports/KBO/NPB rows to Flat and Main without errors; (2) 43 legacy research and 43 gated rows were still open: re-run the backfill script after they settle (idempotent); (3) FIXED 2026-10-23: dashboard scripts (`install_dashboard_service.ps1`, `start_dashboard.ps1`) now explicitly set `MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite`, ensuring SQLite authority; (4) calibration now sees these sports in Main and Flat, which may change which esports calibrators get refit; (5) `ledger_coverage.py`, `calibration_pipeline.py` and several `cli/` files have another session's uncommitted edits; (6) FIXED 2026-10-23: `domain.py` (`ALL_SPORTS`) and `cli/state.py` (`FLAT_LEDGER_SPORTS`) updated to include all 19 sports (esports + KBO/NPB).

(Original plan, kept for reference:)

## 5-original. Merge research and gated-research ledgers into Flat and Main (proposed, not started)

Requested: fold the `research` and `gated_research` tiers into `flat` (every candidate) and `main` (gated CALL decisions only).

Current state, checked 2026-10-21 against `ledgers.db`:

- Tiers: `flat` 2,847 rows, `main` 6,528, `research` 1,188, `gated_research` 1,176.
- Research sports (`lol`, `cs2`, `dota2`, `valorant`, `rainbow_six`, `kbo`, `npb`) appear only in `research` and `gated_research`. They are absent from `main` and `flat`.
- `research` and `gated_research` have identical settled counts for every sport (for example `cs2` 436 and 436; `lol` 24 and 24). Whether the gate filters anything is unverified. The `research_ledgers.py` gate logic needs reading before any merge.
- Two path layouts coexist: `sports/{sport}/research.xlsx` and the legacy `research/{sport}.xlsx`, chosen by `research_ledger_path()`.

**Step 2 answered 2026-10-22 (read-only): the gate does not filter, so `gated_research` is effectively a copy of `research`.**

- Data: all 1,178 `gated_research` rows are `decision = CALL`, but only about 26% (311) carry `QUALIFIED`. The rest are `PAPER_CALL_LOW_EDGE`, `PAPER_CALL_MODEL_UNVALIDATED` or `PAPER_CALL_LARGE_DISAGREEMENT_REVIEW`, which the gate exists to remove. Every gated key (599 distinct) is also in `research` (611 distinct keys; 12 only in `research`). `research` holds 1,190 rows for 611 keys, so re-forecast duplicates are present there too.
- Cause: `_downgrade_research_call` (`eligibility.py`) returns `decision = "CALL"` with a `PAPER_CALL_*` reason, as the "every pick is a CALL with units" directive requires. The call sites in `cli/forecast.py` (lines ~3763, 4023, 4257, 4773) set `genuinely_eligible = eligibility.decision == "CALL"` and mirror to the gated ledger on that. A downgraded pick still passes, so the gate's result never reaches the mirror decision.
- What distinguishes a real qualified pick: `reason_code == "QUALIFIED"`, not `decision`.
- Fix to decide before the merge: test the reason code or record type at those four sites. If the merge goes ahead without this, Main would receive every low-edge and unvalidated paper call from research sports. Step 5 below (CALL-only into Main) would not filter anything.
- Not changed. This needs your decision on step 1 first, and `forecast.py` should not be edited while a scheduled run is in progress.

Proposed approach:

1. **Operator decision first.** Research sports are documented as "research-only", but Main is the operator's paper-live set. Moving them into Main makes them count as live picks. Confirm that is intended before writing code.
2. **Find out what the gate does.** Trace `evaluate_gated_research_eligibility` and confirm why the gated count equals the research count. Fix that first if it is broken.
3. **Keep provenance.** Preserve each row's original tier, for example in a `source_tier` field in `decision_payload_json`, instead of overwriting it. Keep `research` and `gated_research` rows readable for history.
4. **Dry-run the migration.** Back up `ledgers.db` first, then count rows per (sport, original tier, decision) before and after. Dedupe on (event, market, selection, line, model), which is the same key the superseding change uses.
5. **Change the router.** Research sports write every candidate to `flat` and only CALL decisions to `main`. Stop writing to the two research tiers.
6. **Update readers.** `ledger_parity._TIER_SPORTS`, `dashboard/picks.py` (`canonical_tiers`), `dashboard_cache.py` (tier loops), `calibration_pipeline.py` (tier from path string), settlement routing, and `docs/LEDGER_ROUTING.md`, which still describes the old four-ledger model.
7. **Verify narrowly.** Run `ledger_parity` reconcile, then only the test files that cover the ledger router, per the repo's fast-iteration rule. Do not run the full suite.

Caution: the working tree has many uncommitted changes, including `config/production.yaml`, `CLAUDE.md`, and calibration files. Stage only the exact paths this change touches.

## 6. Optimize the daily forecast (proposed, not started)

Requested: make the daily forecast faster.

Current state, checked 2026-10-21 against `runs.db` and the supervisor logs:

- Daily runs are scheduled every 6 hours (04:30, 10:00, 16:00, 22:00 UTC). A completed run took 35.7 minutes (`daily-20261021T220001Z-2a2d49`). Earlier successful runs took 25–45 minutes.
- Reliability is the bigger problem. Of the last 12 `daily` runs, 11 exited with code 1. Two failed after about 1.3 minutes, which points to a startup or early-stage crash. Others failed after 19–45 minutes.
- Corrected 2026-10-22: stage timings do exist. `data\logs\daily_<date>.log` records each wrapper step's duration, and the `daily` report has a `timing` block. (The supervisor log files are still 0 bytes and `note`/`counters` are `None`.) Profile of the good run `daily-20261021T220001Z-2a2d49`: unified daily step 1,740s of the ~36 minutes (settlement 224s, ingestion 93s, calibration 7s); inside it, capture pool 825s, forecast 844s, post-settlement 57s, XLSX export 9.9s. The XLSX-write theory below is wrong.
- Cause of the early exit-1 runs: a `SyntaxError` at `cli/daily.py:1067` (mid-edit state), which failed every step in about 1.3 minutes. The file compiles now. Longer failures on 10-18 to 10-20 were not diagnosed.
- 56 Polymarket `/book` calls hit HTTP 429 in the good run despite the client's retry backoff.
- Changes made 2026-10-22, committed as `e7abd440` (only these hunks; other sessions' uncommitted edits in the same two files were left unstaged): per-task timing `timing.capture_task_seconds` and `timing.forecast_sport_done_seconds` in `cli/daily.py`; a process-wide cap on concurrent gateway requests (default 8, env `POLYMARKET_US_MAX_CONCURRENT`) in `data_sources/polymarket_us.py`. Neither has run live. Next: read those timings from the next run and compare `capture_pool_seconds` (825s) and the 429 count (56) against the figures above.
- First instrumented run, `daily-20261022T050003Z-c51a08` (01:00 EDT), exit 0 on every step (first clean end-to-end run since the `.venv` fix), total 1,395s. `capture_pool_seconds` 84.2 (all of it `polymarket_slate`; every other capture task under 14s). `forecast_seconds` 1,228.8, but the four learned sports finished within 14s, so almost all of it is the research pool (soccer, tennis, esports, hockey, darts and others), which has no per-task timing yet. Not a valid speed comparison: that run's Polymarket book capture got 42 books and 1,813 failures, 1,810 of them `[Errno 11001] getaddrinfo failed` (DNS), 3 were 429s. It also saw 511 events instead of 140 because of the uncommitted league-hydration change. Yesterday's log had no getaddrinfo errors. Cause of the DNS burst not yet determined.
- Probes 2026-10-22 (replayed real `/book` fetches, 16 workers): cap 8, 500 slugs: 841s wall, 1,526 of 1,889 requests were 429, 137 books lost, retry sleep 13,186 worker-seconds (about 98% of wall time; real HTTP time 60s). Cap 2, 150 slugs: 238s, 445/537 429s, 58 lost. Cap off, 150 slugs: 191s, 269/415 429s, 4 lost. The gateway limits by request rate (about 0.4-0.8 successful requests per second), not concurrency, so the cap was reverted (`30831ea9`). No public docs exist for the US gateway's limits (only the international API's); the 429s carry no `Retry-After` header.
- Request volume is the lever. `snapshot()` makes two calls per market (`market(slug)` then `book(slug)`), but `market()` only supplies id, sides, line, start time and team, which the slate's normalized market objects already contain. Dropping it halves requests with no freshness change (books stay live; event-payload quotes are Cloudflare-cached and incoherent, so they cannot replace `/book`). Not implemented yet.
- Risk from the uncommitted league-hydration work: today's slate had 511 events and 1,682 snapshots (alternate spread/total lines for ATP, UEL, UECL, NCAAF and others), against about 140 events yesterday. At the measured rate, about 3,700 requests would take roughly 100 minutes per run if DNS did not fail. Estimate, not measured.
- Implemented 2026-10-22, commit `80f7ba06` (tests: `tests/test_polymarket_us.py`, 25 pass on the committed content; about 316 pass across the capture path, daily cycle, dashboard and auto-buyer; no full suite): (1) `snapshot_from_slate` makes one request per market (the live book) and matches `snapshot` exactly on 4 live NBA markets; (2) `capture_slate_snapshots(horizon_hours=...)`, with the daily slate path passing `POLYMARKET_CAPTURE_HORIZON_HOURS` (default 12; 0 or invalid disables); events starting beyond it are skipped, events already started are still captured, other callers (dashboard, auto-buyer) keep no filter; the result reports `skipped_beyond_horizon`; (3) `_get` retries `ConnectError` / `ConnectTimeout` (DNS) with jittered backoff; (4) optional pacing via `POLYMARKET_US_MIN_INTERVAL_S`, since removed in `efb865e1`: a 1.6s-spaced probe of 150 books took 535s vs 191s unpaced, still saw 56% 429s (65% unpaced) and lost the same 4 books, so it only slowed capture. Not yet measured in a real daily run: check `capture_pool_seconds`, `bbo_capture.captured`, `failures` and `skipped_beyond_horizon` after the next run.
- Sports-skip design (capture side done above; forecast side pending): next is skipping per-sport forecast tasks (the research pool, about 1,200s) when a sport has no event inside the horizon. That needs per-task timing in the research pool first.
- Sports-skip design (original notes): capture books and run forecasts only for events starting within a horizon, driven by `event_start_utc` in the slate, not fixed clock windows. Verified: NFL earliest 2026 kickoff is 9:30 a.m. ET (London/Paris/Munich/Madrid); MLB's 2025 Tokyo Series started 6:10 a.m. ET; tennis, esports, KBO, NPB and soccer start at all hours.
- Confounder: the uncommitted work in `polymarket_us.py` (not mine) extends event-detail hydration to NBA, WNBA, NFL, NCAAF and others, which adds requests per run, and already lowers two pool sizes (16 to 8, 8 to 4). Once it lands, the next run's numbers mix both effects. Do not credit the request cap alone for any change.
- `cli/daily.py` (1,443 lines) already uses thread pools (`max_workers` 4 to 16), so the fetches are probably I/O-bound. `cli/forecast.py` is 5,805 lines.
- The scheduled tasks set `MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite`, but the code default is `xlsx`, and the XLSX export is still written. Measured at 9.9s per run, so it is not a real cost.
- `ModelPrediction-AutoBuyer` runs every 30 minutes and reads the same ledger. Overlap with a daily run is possible, and the `locks/` directory is the place to check.

Proposed approach:

1. **Failure causes (diagnosed 2026-10-21; wrapper logs are `data/logs/daily_<date>.log`, not the empty supervisor logs):**
   - 1.3-minute exit-1 runs (00:30, 06:00, 12:00 EDT on Oct 21): `SyntaxError` at `cli/daily.py:1067` (stray comma). Every step imports the CLI package, so all steps failed. Current tree parses cleanly.
   - 12:53 run, daily step exit 1: `ImportError: cannot import name 'RETIRED_STATUSES' from 'model_prediction.domain'`. Current tree defines it, and the imports load.
   - Oct 20 maintenance step exit 1: `cannot import name 'run_maintenance' from model_prediction.log_maintenance`. Current tree imports it.
   - Current state: the 18:00 EDT run and the 22:00 UTC run exited 0 on every step. Remaining noise is transient `WinError 10060` connection timeouts during ingestion.
   - Root cause of all three: the scheduled jobs run the uncommitted working tree, and it was being edited while jobs ran. Committing a known-good state, and not editing during a scheduled run, would prevent this.
   - Not yet verified: the next scheduled run (04:30 UTC) end to end.
2. **Add per-stage timing. (COMPLETED 2026-10-23, commits `e7abd440` + `61901fc4`.)** Per-capture-task and per-sport completion times are now tracked across both the learned pool (`DAILY_LEARNED_SPORTS`) and ALL tasks in `research_pool` (`soccer`, `tennis`, `cfb`, `esports`, `intl_baseball`, `hockey`, `darts`, `ncaab`, `euroleague`, `nbl`, `mlb_totals`, `mlb_nrfi`, `wnba_spread`, `wnba_total`, `nba_spread`, `nba_total`, `nfl_spread`, `nfl_total`) under `timing.forecast_sport_done_seconds`.
3. **Cut the write path.** Batch all ledger writes for a run into one SQLite transaction. Move the XLSX export out of the forecast path into a separate step that runs after the run, or runs less often.
4. **Cache source fetches per run.** Fetch each schedule, scoreboard, and odds feed once per run and share it across sports and markets, instead of refetching per sport.
5. **Widen parallelism only where it is I/O-bound.** Run sports concurrently in a bounded pool. Keep the point-in-time checks intact: any change must still leave every decision at its real decision time.
6. **Skip work that is already done.** Forecasts that are already logged and unchanged should not be recomputed. The superseding change (`b77a563d`) makes this safe to check by key.
7. **Check the schedule.** If runs routinely take 35–45 minutes against a 6-hour interval, the schedule is not the constraint. Confirm there is no overlap with the 30-minute auto-buyer.

Target, to be confirmed after step 2: zero exit-1 runs over a week, and a daily run measured in a few minutes rather than 35–45.

Verification: run a single sport's forecast with timing enabled and compare its output rows to the current code. Do not run the full suite, per the repo's fast-iteration rule.

## Done when

Items 1 through 6 are each either resolved or explicitly dropped, and this file has been deleted.
