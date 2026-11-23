# DEBUG.md — Current Project Audit and Reproduction Guide

## 2026-11-22 — Background footprint; forever warmer hung exit and leaked out of tests (quick-261122-mjo, quick-261122-w6z)

Operator: "optimize the entire project so it can run in the background and won't interfere with my normal process", then "check and investigate more, and clean up documentation".

- **Dashboard memory (mjo, f4528438..5325a269).** The dashboard held 1.0-1.65 GB working set and 1.5-2.1 GB private. It logged snapshot-save `MemoryError` at 02:28 and 02:30 UTC. Causes:
  - OpenBLAS per-thread pools sized to 12 cores, about 740 MB commit per numpy+scipy process. A probe measured 903 MB, and 160 MB with `OPENBLAS_NUM_THREADS=1`.
  - `DashboardCache` built every tier before checking freshness, about 11 s CPU per no-op refresh.
  - 7 warm builds ran concurrently and rebuilt every 300 s regardless of change. `status` took about 59 s and `production_evidence` about 43 s, with about 1 GB transient on an 8-thread openpyxl parse.

  Fixes:
  - `OPENBLAS_NUM_THREADS=1` via setdefault in `dashboard_server.py` and in `run_supervisor._worker_env` (OMP is not capped).
  - Low memory priority in `stealth_runtime.enable_background_priority` (dashboard and auto-buyer only).
  - Freshness is checked first.
  - `_WARM_BUILD_LOCK` serialises warm builds.
  - The fingerprint includes `MAX(rowid)` of `ledger_events` (read-only).
  - Per-key age: 300 s for picks, flat-picks and status; 1800 s for the rest.
  - The evidence pool has 2 threads.

  Measured on PID 5340: 433-612 MB working set in the first 12 min; 698-728 MB working set and 768-783 MB private at 6-7 h; about 0.3 s/min CPU idle and about 4.3 s/min averaged.

  The three daily runs after the cap took 961, 808 and 914 s. That shows no regression, but it is not evidence of a speed-up, because quick-261122-spd landed in the same commits.
- **Exit hang (w6z, dec50ef6..3efd5d05).** `_warm_caches(forever=True)` ran its 7 never-returning `_warm` loops in a `ThreadPoolExecutor`. concurrent.futures' atexit hook (`threading._register_atexit`) joins executor workers, so any process whose `main()` returned (Ctrl-C, a `serve_forever` error, pytest) never exited.
  - While hung, builders that submit to their own pools logged `RuntimeError('cannot schedule new futures after interpreter shutdown')` every 30 s. `dashboard/server.log` has 13,701 such lines since 2026-10-07.
  - Repro: `pytest tests/test_dashboard_server.py -k test_main_stops_cleanly -o addopts='' -q` printed `1 passed in 2.63s`, then hung until a 150 s timeout killed it.
  - Fix: the forever branch runs one daemon `threading.Thread` per loop (`cache-warmer:<key>`). The one-pass pool path is unchanged.
  - Regression test: `test_forever_warmer_does_not_block_interpreter_exit` runs a subprocess with a 30 s timeout. It fails on ba1e1ff9 ("child hung at interpreter exit", 30.85 s) and passes in 1.4 s.
- **Test leak into live files (w6z, same root).** `test_main_stops_cleanly_on_keyboard_interrupt` called the real `main()`. That loaded the live `cache_snapshot.pkl`, wrote the live `server.log` and set `_SWR`/`_WARMING`. It also started the forever warmer, which ran under later tests' monkeypatches and saved the live snapshot.
  - Every odd `server.log` line has this one cause: `ValueError ... not in the subpath of ...test_promotion_allowed_reflect0` (a later test's patched ROOT), `TypeError ... test_sell_still_allowed_before_the_game_starts.<locals>.<lambda>()` (a later test's patched builder), and the shutdown spam.
  - Fix: the test stubs the warmer, snapshot load, jobs hydrate and priority, and restores the shared dicts. A session-scoped autouse fixture in `tests/conftest.py` redirects `common.LOG_FILE` and `common._SNAPSHOT_FILE` to a temp dir for the whole session and never undoes it, so leaked threads also hit the sink.
  - Pin test: `test_dashboard_live_files_never_resolve_to_the_repo_dashboard_dir` fails under `--noconftest`.
- **Not bugs:**
  - The auto-buyer run at 02:00 UTC took 926 s because it waits for the daily lock by design (`test_auto_buyer_worker_pair` pins a wait of 600-1500 s).
  - Daily runs 11x/day (00:30, then every 90 min 07:00-21:00 ET).
  - Scheduled workers run S4U at Task Scheduler priority 7 (BelowNormal CPU). Their command lines and handles are not readable from an interactive session, so check them through `runs.db`, not a process probe.
- **Open (ROADMAP "Background footprint"):**
  - The daily, production, calibrate and capture workers do not lower their memory priority.
  - The dashboard still runs pre-w6z code until its next restart.
  - Dashboard memory grew about 150 MB over 7 h; whether it plateaus is unconfirmed.
  - Evidence still reads xlsx.
  - SWR request rebuilds run outside `_WARM_BUILD_LOCK`.

## 2026-11-22 — Placeholder rungs priced as the main line; MLB single-inning totals (quick-261122-l17)

- **Bug:** NBA/WNBA/NFL main-line selectors ranked rungs by |long ask − 0.5| only. A rung with no real bid (2¢ / 49¢) beat a real market and its other side was priced at 1 − bid = 98–99¢. Reproduced: BKN@MEM 2026-11-14 05:35Z (real 227.5 at 35¢/47¢ lost to 229.5 at 2¢/49¢, under at 98¢), UTA@LAL 11-18 (whole ladder 1¢/51¢), PHI@DET 11-04 (+19.5 at 94¢). The NFL 11-12 band checked the long side only. Size: NBA 26 of ~2,400 checkable spread/total rows (23 superseded, 3 settled, ~+0.01u), NFL 2 (before 11-12), WNBA 16.
- **Fix:** `forecast_market_select._is_two_sided_rung` requires both asks in 20–80% (the NFL band), after latest-per-slug (WNBA now reduces to latest per slug too, and filters quotes to `observed_at` like NBA); NFL band covers the short ask. No qualifying rung → NBA/WNBA skip the game this run; NFL falls back to ESPN consensus as before. MLB `_is_full_game_market` rejects `-i<N>-` single-inning totals (captured 09-29..10-28 as `total` at ~47.5% mid; no pick ever priced on one). Tests: `tests/test_main_line_placeholder_rungs.py` (real ladders, each fails with its fix reverted).
- **Not bugs (ralph NBA loop flags):** WNBA spread orientation is correct (snapshot team vs ML favourite 196/201, ledger home/away vs slug 131/131; clean-rung home cover 45/77); NFL period rungs exist only in pre-10-21 snapshots; MLB F5 excluded since v1.0.0; NCAAB name mismatch was the research loader only.
- **NCAAF follow-up (quick-261122-ltz):** `_match_cfb_spread/_total` now take the newest capture per rung, apply the 30-min freshness window over all rows, then the band (`pricing.is_two_sided_main_rung`, now the single copy). Band-before-freshness would have revived the 2026-11-21 stale-rung bug (review finding, pinned by a test). The NCAAB "0.50 = untraded" indicative rule does not hold for NCAAF: live 11-28 slate had 0 rungs at 0.50/0.50 and 0.50 is as common as 0.49/0.51.
- **Open:** ranking inside the band still uses the long ask; the NCAAF capture shortlist (keep=1) ranks by cached long price, so placeholder-only fresh captures push lines to indicative/consensus pricing.

## 2026-11-22 — Daily-run speed: model-ledger spool and event-horizon skip (quick-261122-spd)

Operator: "Skip per-sport tasks when a sport has no event in the horizon, and batch ledger writes. Goal: zero exit-1 runs over a week." Commits `1771e8f1`, `d5b1bdca`, `553c2380`, `b1c85e93` (docs `c0df9a09`), landed ff-only with `runs.db` idle.

- **Exit-1 causes, 11-12 to 11-22 (3 of 91 daily runs): no code defect.** `daily-20261112T053001Z-2e5da5` and `daily-20261113T053001Z-8f176c` (00:30 ET, Event 2004 low virtual memory, sqlite disk I/O error); `daily-20261120T190001Z-44375a` (ingest exit 0xC0000374 heap corruption, access violation at the polars import); `daily-20261119T170001Z-82f30b` (bugcheck 0x133). Unexplained: `daily-20261113T210002Z-523953` (0x40010004 after 13 s) and `daily-20261115T063307Z-07c95d` (143, killed at 900.2 s, off-schedule start). Zero exit-1 is not reachable in code while the RAM fault persists; no ingest retry was added (it would only turn a hardware fault green).
- **Timing (53 reports):** mean 1,086 s; capture pool 731 s (polymarket_slate 534 s, request-rate bound); forecast phase 284 s; NFL tasks already cost 7 s on no-game days, so the skip rule alone saves about 15-20 s.
- **Spool (`model_ledger.py`):** each Main/Flat append rewrote a whole per-model xlsx (about 90% of an append). Under `defer_sqlite_xlsx_exports`, `ModelLedger.append_prediction` now writes an fsync'd `<name>.xlsx.pending.jsonl`; reads, the settle hook and every `_write_unlocked` merge it deduped by `prediction_id`; the workbook is rebuilt once at the outermost exit; crashed spools fold at the next daily start under `<data_root>/model_ledgers` and the ledger path's `model_ledgers`. PickLedger audit-first ordering and the SQLite authority are untouched. Benchmark on a temp ledger: 492 ms to 41 ms per append at 600 rows; the production saving (100-150 s) is an estimate from a forecast_seconds regression, not measured.
- **Event horizon (`cli/event_horizon.py`, wired in `cli/daily.py`):** one ESPN scoreboard probe per daily-enabled league at `forecast_observed_at`; skips NBA/NFL/WNBA/MLB-moneyline and NBA/NFL/WNBA spread and total only when the scoreboard is well formed and no event starts after the decision time. Any fetch error, malformed payload or missing/unparseable/timezone-less date means the task runs. NCAAF (prices started games too), MLB totals and NRFI stay ungated. `_clear_today_open`/`_restore_unreplaced` are untouched.
- **Review (critical-reviewer, read-only): pass with two minor findings.** (1) Fixed in `b1c85e93`: a torn spool tail followed by another append glued two lines and dropped an acknowledged row; `_append_spool` now writes a leading newline when the last byte is not one, with a test that fails without the fix. (2) Left, documented: an empty-but-200 scoreboard counts as "no games" and would skip that league's tasks for one run (about 10 runs a day, picks resume next run).
- **Tests:** 302 passed across 22 targeted files in the rebased worktree, 129 in the live tree after the merge; ruff clean. Revert proofs: forcing deferral off failed 5 spool tests; making fetch error or bad date skip failed 4 horizon tests. Known: the dashboard's direct xlsx read lags model-ledger rows until the run's flush.
- **Also 2026-11-22:** eight CS2 aliases added to `data/esports/team_aliases.json` (git-ignored; backup `team_aliases.json.pre-8-aliases-20261122.bak`): Permitta Esports, Wave Esports, Grêmio Esports, UNiTY esports, Diamant Esports, uust _esports, largadosypelados -> `largados_e_pelados`, LFO UKRAINE -> `LFO 7`; each verified against the live BO3 team list. Leo Team, BBL and MORROW stay unaliased. A stale locked agent worktree under `.claude/worktrees/` (made by a failed agent launch) and its branch were removed after checking it was clean and on `origin/main`. NCAAB addendum v4 scorecards read: P1 DEV_ELIGIBLE, P2 NOT_ELIGIBLE, no edge over the closing line (HANDOFF section 4).

## 2026-11-22 — The 205 void esports rows are forfeits, not mis-voids (quick-261122-esv, read-only investigation)

Operator: "go ahead" on finding another results source for the 205 rows still void after the BO3 re-grade. Nothing was edited or applied.

- **Dry run** (`scripts/regrade_nonbinary_esports_voids.py`): 205 candidates, 0 re-grades (CS2 197, Dota2 7, LoL 1); skip reasons 167 "no finished BO3 series near the event start", 38 "team name does not resolve". The one earlier re-grade (`671c5cf4932049de`, Flat, loss -1.5U) is intact in `ledger_records`.
- **Root cause of the 167:** `Bo3EsportsClient._match_page` asks BO3 only for `status=finished`, so forfeits never reach the script. Queried by team-id pair with all statuses: 74 of 117 distinct events have a BO3 `defwin` within 0 to 0.8 h of the event (128 rows, scores 1-0 or 0-1 as in a walkover), 1 more is `canceled`. Another 33 unresolved-name rows (18 events) show an event-time `defwin` on the opponent's side. In total 162 of 205 rows are an event-time forfeit or cancellation; 43 rows have no BO3 record at the event time (never played, or not listed; not verified). Range 2026-08-05 to 2026-10-05; tiers flat 117, main 86, research 1, gated_research 1.
- **Polymarket agrees.** Its rules settle a forfeit at the last fair market price. All 117 markets are `MARKET_STATUS_RESOLVED` with non-binary `outcomePrices` and `settlementPx` (114 tier-1 pre-close mids 0.31 to 0.94; 3 tier-3/4 with `settlementPx` 0). The payload has no winner field. Control: 8 random settled CS2/Dota2 win/loss rows were all binary. This is consistent with the `cli/settle.py` comment (2026-09-14): cleanly decided matches that showed non-binary prices were the ~260 mis-voids, already re-graded against BO3; what remains is the forfeit set.
- **Windows and aliases do not help.** With the pair resolved, 12 h and 24 h give 0 events with exactly one finished series; 48 h to 72 h give 4 events (7 rows), all of which also have an event-time `defwin` (the match is an adjacent-day rematch), so widening would assign wrong winners to forfeits. Alias additions would re-grade zero rows. Proposed aliases for the 11 unresolved names (not applied; `data/esports/team_aliases.json`, `cs2` key): Permitta Esports, Wave Esports, Grêmio Esports, UNiTY esports, Diamant Esports, uust _esports, largadosypelados (-> `largados_e_pelados`), LFO UKRAINE (-> `LFO 7`, medium confidence). "Leo Team" and "BBL" are ambiguous in the name table and need an ID-based alias (code); "MORROW" (10 rows) is not in BO3's team list and is probably a rebrand of "benched gods" (unverified).
- **Other sources not tested.** The repo integrates only BO3; OpenDota is a stub; `source_policy.py` excludes Liquipedia and the Riot API. A probe of PandaScore/HLTV/lolesports was denied by the permission classifier and not retried.
- **Decision:** leave all 205 as pushes. Open and small: a push books 0U while the real contract settles at the last price, so grading at `settlementPx` would be more faithful if paper PnL is ever reconciled with fills. One caveat on the single re-graded row: its market was non-binary like the others, so the -1.5U loss follows the BO3 winner, not the exchange's last-price settlement.

## 2026-11-22 — Handoff archive: research logs moved out of HANDOFF.md (quick-261122-hnd)

Verbatim copy of `docs/handoffs/HANDOFF.md` as it stood before the 2026-11-22 trim. The research logs (NCAAB, NFL 7-12, NCAAF, NBA v10, daily-speed notes) are history now; HANDOFF.md keeps only open decisions and live threads. Section 2 (best-picks audit overrides) was already done: the audit was hardened in quick-261120-gaq/w6i and the framework doc is committed.

**Status: OPEN.** Delete this file when every item below is done or explicitly dropped.
Finished and removed: the `refactor/cleanup-2026-11-11` triage (the branch is the live branch at
`7a234da4`; the 12 failures it listed all pass now: `test_cli`, `test_five_blind_spot_guards`,
`test_forecast_runtime_regressions`, `test_tennis_markov`, 111 passed on 2026-11-22), the 2026-11-22
review fixes and the helper dedupe (merged, worktrees and branches removed). History is in git,
`DEBUG.md` and `ROADMAP.md`. Nothing is pushed.

### Next action

Decide the items in section 0 (operator decisions left by the 2026-11-22 review fixes), then
section 1. The scheduled tasks run the working tree, so do not edit source while a run is in
progress (check `runs.db` for `started` rows first).

### 0. Review-fix follow-ups (2026-11-22; decisions only, no code owed)

Detail in the `ROADMAP.md` section "2026-11-22 — Project-review fixes and the decisions they leave".

- **Non-binary terminal markets stay open.** `_settle_by_resolution` no longer voids them; a market
  that legitimately settles non-binary needs a hand-void. Decide on an age-out void.
  `portfolio/auto_buyer_ledger.py` (~2173) still books a push on non-binary outcomes.
- **Esports non-binary pushes: re-graded where possible (2026-11-22).** BO3 confirmed 1 of 206; the other
  205 (CS2 197, Dota2 7, LoL 1, across Main/Flat/research/gated) stay pushes because BO3 has no series for them or a
  team name does not resolve. Closing them out needs another results source or alias additions; not owed.
- **v9 unread/defaulted artifact keys** are logged and pinned by `tests/test_nba_v9_artifact_keys.py`
  only. Making them take effect is a new version through `model_promotion`.
- **From the review, not touched:** paper quote age is unbounded (`max_quote_age_seconds` unset),
  and the live-order path in the auto-buyer worker is gated only by the saved `mode` (`paper`).

### 1. NCAAB (updated 2026-11-21 evening, quick 261121-hmc)

- **Serving verified:** the 18:30 ET Daily 2026-11-21 wrote NCAAB moneyline rows as
  `ncaab-moneyline-v7m` (hash `5ea1cb06…`), spread `ncaab-spread-v7`, total `ncaab-total-v6`.
- **Next action:** read the addendum v4 scorecards (`outputs/ncaab/research_261120/P1_player_availability/`, `P2_returning_production_early/`).
- **Done 2026-11-21:** `ncaab-moneyline-v7m` (pure margin probability, A_ml_w0) promoted for the NCAAB
  moneyline only (`e865f518`, operator-approved after an all-season check: better log loss on 856 D1
  games, slightly worse on 178 priced games, market beats both). Rollback order: moneyline first
  (`model_promotion rollback --sport NCAAB --market moneyline` returns `ncaab-spread-v7`), then spread.
  Buyer state v979 blacklists v7m (backup `*.pre-ncaab-v7m-20261121-165215.bak`). The forward-check
  cohort SQL must include v7m moneyline rows (model_id `ncaab-moneyline-v7m`).
- **Spread/total research (261121-hmc):** totals grid T1-T3 not eligible; v6 stays. Player boxes
  2022-2026 backfilled (`data/research/ncaab_external/espn_player_boxes/`, 31,039 events) and PIT
  player features built; addendum v4 (`5d4a3da5`, K spread 25) pins P1 availability and P2 returning
  production. Confirmation needs 2014-2021 boxes, fetched only if a candidate is DEV_ELIGIBLE.
- **06:00 Daily 2026-11-21 verified:** 69/69 NCAAB v7 rows and 16/16 total rows carry
  `model_artifact_hash`; all NCAAB calibrators served identity; trial evaluated v7 (8 candidates, 0
  orders); pre-tip capture idle before tips (87 open rows, none in window).
- **Done 2026-11-21:** `ncaab-spread-v7` promoted for moneyline + spread (`c3f97a4a`; rollback
  `ncaab-spread-v6` via `model_promotion rollback`, both markets). The `models.NCAAB.margin_engine` yaml
  key is retired and fails the slate closed. Slate replay: v7 id served probabilities identical to the
  v6-id/v7-engine serving (46/46). Live state: buyer state v937 blacklists v7 (NCAAB stays disabled in
  all three markets); paper trial trades v7 only (backups `*.pre-ncaab-v7-id-*.bak`).
- **Calibration changed (operator-approved):** NCAAB fits on `model_probability_raw`, engine-pure pools
  (v6-labelled rows priced by the v7 engine fit v7), serving through `load_serving_calibrator` (sample
  floor + OOF Brier gain). At the change all NCAAB calibrators resolved to identity (raw). Watch the
  Daily `result["calibration"]` decisions.
- **CLV:** NCAAB closes must be observed strictly after the decision; the 15-min buyer worker captures
  pre-tip snapshots (NCAAB, tips within 25 min). First real captures from ~10:40 ET 2026-11-21. Check
  settled NCAAB rows from 11-21 carry a close != decision price. The resolution-settle and auto-buyer
  ledger paths got the same rule on 2026-11-21 (`9adde04e`, NCAAB/NCAAF, forward only). Open: NBA paper
  rows have the same zero-CLV close (33 of 48); a top-up buy can still take a close observed between
  the first fill and the top-up.
- **Forward check (re-pinned by engine):** split on
  `COALESCE(json_extract(json_extract(decision_payload_json,'$.model_input_snapshot_json'),'$.features.margin_engine'),'v6')`;
  v7 cohort = (v6 id AND engine v7) OR v7 id. Spread 5-8% edge since 11-12 was 13-16 (n=29) at review.
- **Research verdicts (dev window, 22,947 games):** no candidate has edge over the closing line; only
  A_ml_w0 is DEV_ELIGIBLE. B1/B3 (scale) help cover log loss slightly but worsen moneyline; B2 cap,
  C1/C2 per-possession, D1 preseason prior not eligible. Evidence: `docs/ncaab_review_2026-11-20/`,
  `outputs/ncaab/research_261120/`.
- **Team matching fixed (`cb1b980f`):** mascot kept when a spread lists first; UCA/UNCW/NAU aliases.
- **Data:** CBBD key in `.env` as `CBBD_API_KEY` (3,000 calls/month shared with CFBD; ~176 used);
  cbbdata has no valid key (client inert); Kaggle Massey dropped. History boxes 2021-22+ in
  `data/research/ncaab_external/espn_boxscores/`.
- **Still open:** NCAAB auto-buyer stays disabled by operator state; ledger `model_probability` column is
  raw while the payload is served (documented; readers fixed in best-pick audit and ledger API).

### 2. Best-picks audit script (offered, not done)

`scripts/audit_best_picks.py` `audit_candidate` hardcodes CONFIRMED_BEST for NBA spreads >=8% edge
on double-digit dogs, NCAAB overs 8-12%, and NCAAB moneyline; none replicate from the ledger
(NBA spread bucket recomputes to 4W-6L, n=10; `ncaab-total-v6` is 4W-10L at >=12%). Remove the
overrides or make the verdict compute from the bucket and require
`market_quote_provenance == decision_time_executable_quote`, then update
`docs/BEST_PICKS_AUDIT_FRAMEWORK.md` (it has uncommitted edits in the tree).

### 3. NBA moneyline whitelist — v4 eligibility decision

`nba-moneyline-v9` was removed from `DEFAULT_WHITELIST_MODELS` and its artifact marked an unverified
placeholder (`quick-261112-11j`, 2026-11-12). The serving champion `nba-elo-trend-lr-v4` is still NOT whitelisted,
so NBA moneyline cannot be bought. That is left as-is deliberately: adding it is a trading-eligibility
change and lr-v4's registry evidence is `historical_only`. Decide. The refit is done
(`nba-elo-trend-lr-v5`, quick-261112-kj3, `docs/NBA_LR_V5_REFIT_2026-11-12.md`): registered as the
NBA moneyline challenger (shadow, historical_only), not promoted -- on identical holdout rows it is
indistinguishable from v4 (Brier 0.1889 vs 0.1892); it only refreshes the evidence window and lowers the
threshold to 52.5%. **Promoted 2026-11-13** (`promo-20261113T184744Z-6b0b74`, quick-261113-j3m); v4 is the rollback. Whitelisted 2026-11-13 by operator directive ("nba ml can stay in auto buyer"): code
constant (quick-261113-jqw) and the live `auto_buyer_state.json` whitelist. **Still open and now the only
gate:** the buyer rejects every `PAPER_CALL_*` row, and all NBA moneyline Main rows are
`PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED` while v5's `evidence_status` is `historical_only` (7 of 7 Main rows
on 2026-11-12..13). Flip v5 to `predictively_qualified` in `config/production.yaml` if NBA moneyline should
actually be bought; until then the whitelist entry is inert.

### 3b. Calibration pipeline vs serving transforms (new 2026-11-12)

The nightly calibration pipeline rewrites `data/calibration/models/*_calibrator.json` and the NBA total
slate / NFL slates / auto-buyer read them at serving time. Since `quick-261112-11j` an artifact is only applied with
own-model fit provenance and >= 100 outcomes, but the pipeline can still change a served transform with no
promotion record. Decide whether that is acceptable or whether serving must pin a hash.

### 4. NFL v8 challenger (replayed 2026-11-15: not promotable; keep unqualified)

`nfl-spread-structural-v8` projects the same margin as v7 in every game; only the distribution
differs (discrete PMF with push mass), so it cannot repair a mean with no signal.

- 1,489 completed games, flat 1u at a 52.4% ask: Brier 0.2494 vs v7 0.2496 (market 0.2500); favoured
  side 51.4% vs 52.7%. At its own 3.5% gate: 75 bets 42-33 +5.18u, of which 2022 (inside the PMF fit
  window) is +5.18u. Out of sample (2023+): 48 bets 26-22 +1.64u at 3.5%, 18 bets 9-9 -0.82u at 5%.
  Fires cluster on 2.5/3.5/6.5/7.5 (46 bets 28-18), a post-hoc slice priced at an unshaded ask.
- 60 settled Flat spread rows at their own line and ask (`scripts/replay_nfl_v8_settled.py`, repaired
  2026-11-15 to re-price pre-11-11 four-column snapshots from the current store; output
  `outputs/research/nfl_v8_settled_replay.json`): Brier ask 0.2447, fixed v7 0.2513, v8 0.2531.
  `promotion_blocked: true` (v7 parity 7 of 24 rows within one point).
- Possible use: valuing half points around 3 and 7 against real shaded two-sided asks, once a few weeks
  of the week-ahead captures exist. Not a serving candidate.

### 5. Daily forecast speed and reliability

- 2026-11-11 daily log: cycle is bound by throttled network I/O (Polymarket BBO capture 540s with
  4 workers and a 429 guard, auto-buyer cycle 272s, NCAAB settlement 73s). The gateway limits by
  request rate, not concurrency; request volume is the only lever.
- Done: one request per market (`snapshot_from_slate`), capture horizon `POLYMARKET_CAPTURE_HORIZON_HOURS`
  (default 12), DNS retry, per-task timing under `timing.forecast_sport_done_seconds`.
- Open: skip per-sport forecast tasks (research pool, about 1,200s in the 2026-10-22 profile) when a
  sport has no event inside the horizon; batch ledger writes into one transaction; cache source
  fetches per run. Target: zero exit-1 runs over a week. Verify with a single-sport run, not the full suite.

### 6. NCAAF

- `cfb-structural-v4` is champion; no challenger is configured (`challengers.NCAAF` all null).
- 2026-11-12 (`quick-261112-0xd`): every NCAAF finding in `docs/MODEL_REVIEW_2026-11-11.md` is fixed —
  production now serves the v4 class from the artifact (was a 16.2-SD duplicate under the v4 label),
  newest-capture/both-asks quote matching, artifact gates (FCS, 21.5 spread cap, 33.3% dog floor) on
  Main, total gates moved to `config/model.yaml`, venue-based neutral-site weather, postseason week,
  QB channel from the injuries snapshot, 200k sims, source-hash `code_revision`. `DEBUG.md` entry.
- Open decisions: supersede or keep the 243 Main `cfb-structural-v4` rows priced on the 16.2
  arithmetic (identifiable from snapshot `parameters.margin_sd`); whether ESPN-consensus rows
  (229 of 238 Main NCAAF) may reach Main at all — they are now labelled `espn_consensus_indicative`.
- Replay 2026-11-12 (`scripts/replay_cfb_v4_all_settled.py`, `outputs/ncaaf/cfb_v4_replay.md`): on all
  7,960 completed games the fixed model's moneyline Brier (0.1893) equals its own Elo channel (0.1894);
  realized margin SD 16.8 vs served 14.8 on established FBS matchups; on 1,610 settled rows the recorded
  ask beats both old and fixed model, fixed-model fires 98 Flat (51.0%, −6.14u), totals 40.9%.
  Decisions owed: pause Main NCAAF totals; v5 margin_sd/HFA retune as a walk-forward challenger;
  re-evidence the `predictively_qualified` label.
- v5 challenger built 2026-11-12 (`d86876e7`, `models/cfb_structural_v5.py`, `config/models/cfb-structural-v5.json`,
  research status): fitted margin/total mean calibration + residual SD table; holdout Brier 0.18933 vs 0.19056
  (CI includes zero), margin/total log scores significantly better, slope 0.98 vs 1.19. Owed: register it as the
  NCAAF challenger in `config/production.yaml` so it produces shadow rows; promotion only via the ROADMAP rule.
- **Promoted 2026-11-13** (operator directive, `model_promotion` records for moneyline/spread/total; code `02f45736`):
  `champions.NCAAF` = `cfb-structural-v5`, v4 = rollback; `cfb_model()` follows the champion map. Owed now: watch
  the first v5 Main/Flat rows and their settlement replay; decide Main NCAAF totals (both models lose at consensus
  asks); the buyer whitelist carries v5 (edge band 2%-12%).
- Owed: refresh the `calibration` block in `config/models/cfb-structural-v4.json` (still holds
  pre-`margin_sd`-retune numbers); replay v4's own point-in-time predictions against the 363-game
  no-vig closing moneyline set (Kaggle `eddieglush/closing-odds-vs-results-14-sports-leagues`,
  `ncaaf_2026_closing_prices_vs_results.csv`) to get the first real Brier vs market.
- Blocked on data: market-anchor blend (zero quote-verified v4 picks), spread/total closing lines.
- Do not use `scripts/cfb_research_pipeline.py` output as evidence (hardcoded ablation numbers,
  constant 50% market default).
- Cleanup when `elo_ratings.py` is next touched: `ELO_CONFIG["ncaaf"]` is dead code.

### Done when

Sections 0 through 6 are each resolved or explicitly dropped, and this file has been deleted.

### 7. NFL fix batch 2026-11-11 (done; follow-ups open)

Committed `03faf30d`..`78c83389` + evidence commit: fail-closed efficiency inputs (every v7 pick
10-27 -> 11-12 was priced on league-average defaults -- rows left in place, window annotated in
`docs/DEBUG.md`), Main gate on `record_type`, latest-two-sided total quote, ordering/age input
freshness, effective-sd replay inputs, consensus labelling, artifact hygiene, Open-Meteo NFL
weather, guarded serving calibrator, and `scripts/replay_nfl_v7_all_settled.py`.

- **Next action**: watch the first daily run after this lands. Expect `step1k_nfl_weather` in the
  report, a fresh efficiency snapshot with 16 team columns, and NFL forecasts to resume (they
  decline, NO_CALL, while the only snapshot on disk is four-column).
- **Evidence** (`outputs/nfl/nfl_v7_replay.md`): point-in-time over 1,489 games 2021->2026 the
  fixed engines sit at the closing line (spread Brier 0.2496, total 0.2506 vs 0.2500); totals are
  over-confident (slope 0.43). Calibration tournament -> identity for both markets.
- **Open decision**: `config/production.yaml` still marks both v7 engines `predictively_qualified`;
  the replay contradicts that. Either demote the evidence label (keeps them serving, blocks Main as
  qualified) or accept that the 5% Main hurdle will rarely fire (16 of 1,451 games, hit 37.5%).
- **Open**: NFL moneyline (`nfl-elo-trend-lr-v4`) was not re-priced; 85 Flat picks 55W/29L +5.55u.

### 8. NFL fix batch 2026-11-15 (`quick-261115-21e`; done, follow-ups open)

Loss review: at executable quotes moneyline is 29-22 -9.91u (edge >= 8%: 0-8), spread edge >= 5% is
6-14, totals 21-10 +15.82u. Model Brier loses to the ask in all three markets.

- **Done.** Paper buyer rejects picks whose forecast flagged `input_qualified` false or (totals)
  `weather_inputs_verified` false, read from the canonical ledger (`rejected_input_unqualified`;
  first live cycle 06:40Z rejected the NE at DET and SEA at LV totals). `(nfl, spread)` is disabled in
  the buyer, code constant and live state; `nfl-elo-trend-lr-v4` left the live whitelist (it was on
  both lists; state backup `auto_buyer_state.json.pre-nfl-spread-off-lrv4-whitelist-20261115-063137.bak`).
  Registry `evidence_status` is `historical_only` for v7 spread, v7 total and lr-v4. Dashboard
  `performance()` no longer scores `model_opinion_no_executable_quote` rows and reports them under
  `placeholder_priced_excluded`; this applies to every sport (210 settled Main rows, +47u: MLB 144,
  WNBA 25, NFL 25, NBA 16).
- **Open decision: NFL Main still follows the artifact, not the registry.** NFL spread/total gating
  reads `qualification.qualified` in the v7 artifacts, so the evidence demotion is a label only. Wiring
  NFL to the registry (as NBA is) would turn every NFL Main row into a paper call.
- **Open decision: NFL totals in the buyer.** A weather-only total model selected on validation scores
  0.2502 on the locked holdout (from 2025-09-28) against 0.2511 for served v7, whose edge >= 5% fires
  are 13-17 -5.18u there. Recorded wind >= 12 mph went 105 unders / 60 overs through 2025-09 and
  20 / 21 since. No total edge is validated; the +15.82u ledger record is 31 picks.
- **Not changed, on evidence:** spread sd. Flat 12.8 vs 13.8 with the 1.25x heavy-line factor is
  Brier-neutral on validation (0.24946 vs 0.24949) and slightly worse on holdout (0.25064 vs 0.25044).
- **New shadow tool:** `scripts/nfl_price_edge_report.py [date]` lists Polymarket asks below the
  no-vig DraftKings consensus for the same line. Unvalidated; one week of capture history.
- **Blocked on data, with dates:** closing-line value (3 settled rows have a closing consensus; about
  36 more after 2026-11-16 settlement, readable from roughly 2026-12-07 with four slates); forecast
  weather validation (Open-Meteo captures began 2026-11-11); opener and line-movement tests (captures
  began 2026-11-12; four weeks by 2026-12-10).
- **Reversed the same day (operator directive: nothing comes off production, paper needs picks).**
  `(nfl, spread)` and `(nfl, total)` are live in the buyer again (code and state, `f451f802`,
  `b6d3177a`) and `nfl-elo-trend-lr-v4` is back on the live whitelist. It is still on the blacklist
  from before 2026-11-15, so the buyer buys no NFL moneyline. The input gate stays. Do not propose
  disabling a market as a fix; the two open decisions above are closed as "leave serving".
- **Moneyline challenger `nfl-moneyline-margin-v1` (`3c6c6aca`).** The home-win probability
  `nfl-spread-structural-v7` already derives from its margin, logged to Flat under its own model id
  from the spread slate (`cli/forecast_nfl_moneyline_shadow.py`). 594 out-of-sample games
  (2024-09-22..2026-10-08): Brier 0.2071 vs 0.2198 for lr-v4, paired gain +0.0127 (95% +0.0038..
  +0.0213), 67.3% vs 66.0% winners. The gain is the sportsbook line inside the margin (pure
  structural margin 0.2277, line alone 0.2068), so it is accuracy, not edge. Not promoted; promotion
  needs Vincent's yes and `model_promotion promote`. One row (`b112db1191754f20`, PIT at CIN) was
  written to the live Flat ledger at 21:27Z by a dry run that resolved to the runtime root.
  **Next:** confirm the 23:30Z run logged the row beside lr-v4's and compare the two on results.
- **Evaluated 2026-11-15:** `nfl_market_anchor_v1` walk-forward on live quote-backed calls: moneyline
  0.2330 -> 0.2138 (41 rows) and spread 0.2653 -> 0.2408 (26) at weight 1.0, i.e. the market; total
  gets worse, 0.2328 -> 0.2482 (25). Loss mechanism: moneyline picks 5%+ above the ask are 1-11.
- **Not evaluated:** `nfl_joint_market_v3`, `nfl_joint_score_v1`. The anchor
  needs timestamp-valid two-sided quotes per settled game; 121 executable-quote picks exist.

### 9. NFL spread/total improvement search (2026-11-15 evening: nothing beats the line)

Season-forward (fit on seasons < S, score S, 2023..2026), pooled, pushes dropped; closing line = 0.2500 / 50.0%.

- 10 fixed candidates on existing inputs (Elo/form vs line, engine terms refit, line/pace/EPA, boosted
  trees): best spread 0.2500 vs v7 0.2504 (895 games), best total 0.2497 vs 0.2505 (912). All inside noise.
- **The replay's quarterbacks were wrong.** `QBResolver` has depth charts for 65 of 1,469 games; the other
  1,404 fell back to the team's all-time attempts leader. Re-dumped with each game's actual starter (most
  attempts in that game): the starter differs in 598 home / 618 away team-games, `c_qb` changes in 859.
- With real starters v7 is unchanged: spread 0.2501, 52.7% (896); total 0.2506, 51.7% (913). The QB term
  alone has corr 0.027 with the spread residual (0.72 with the line itself) and its fitted sign flips by
  fold. 6 more candidates, none beats v7. The line already prices the quarterback.
- Only stable signal: the v7 total deviation refits to a slope of 0.13-0.23 in every fold (right direction,
  roughly 4x overconfident); gain +0.0010 Brier, 95% -0.0012..+0.0033, same side picked.
- Live path checked: anchor line and priced Polymarket rung are the same contract and snapshot.
- Not replayable on history: `nfl_joint_market_v3`, `nfl_joint_score_v1` (need frozen pregame packets).
- Scripts are in the session scratchpad only (`st_search.py`, `dump_starters.py`, `qb_test.py`); nothing in
  the repo changed. Untested and still open: opener/line-movement inputs (data from 2026-11-11 only).

### 10. NFL opener / line-movement data (fetched 2026-11-15 night)

- `data/research/nfl_openers/espn_odds_raw.jsonl` (1,489 events, ESPN core odds endpoint, all providers,
  raw items) and `openers_parsed.json` (median non-live open/close per event). Openers exist for 826 games:
  2023-10 onward (2021-2022 and early 2023 have none). Parsed close vs replay line: corr 0.996.
- No fit, 826 games. At the CLOSE nothing works: betting with the move 299-315 (spread), 317-338 (total).
- v7 re-anchored on the opener (swap the 0.8 market term): its deviation matches the direction the spread
  later moved 344-279 (55.2%), 202-143 (58.6%) when the deviation is >= 0.5 pt; side vs the opener covers
  427-382 (52.8%, not separable from 50%). With real starters: 58.4% / 62.0% / 53.7%.
- **Not clean yet:** opener timestamps are unknown and the replay inputs are as of 6h before kickoff, so
  the model may know a week of results (and, in the real-starter run, the starter) the opener did not.
  Next step is prospective: price each NFL game when its line opens, log to Flat under its own id, and
  measure CLV against the close. Not built.

### 11. NFL replay extended to 2016 (2026-11-16)

- nflverse weekly player stats 2015-2020 cached in `data/research/nfl_history/`; replay now 2,826 games
  (was 1,489), real starters in 2,818. Dump is scratchpad-only (`dump_2016.py`, `big_test.py`).
- v7 as served on 2016-2020, which it was never tuned on: spread 666-639 (51.0%), Brier 0.2503; total
  664-663 (50.0%), Brier 0.2517. All 2,826: spread 52.0% +/-1.9%, total 50.8% +/-1.9%.
- corr(model lean, result vs closing line): spread +0.013, total -0.0001. Season-forward refits
  (2018-2026, 2,235 / 2,270 games) do not beat v7. The total model has no signal against the close.

### 12. NFL early-line log (started 2026-11-16)

- `scripts/nfl_early_line_log.py <date> [<date> ...]` prices upcoming NFL spread and total slates with the
  serving v7 engines days before kickoff and appends first-sight rows to
  `data/research/nfl_early/early_lines.jsonl`. It builds no ledger object and writes nothing else.
- First run 2026-11-16 ~05:00Z: 13 games x 2 markets (11-16 MNF, 11-19 TNF, 11-22 Sunday), all with
  Polymarket asks. The daily job now runs it once a day for the next seven days (`_nfl_early_task`).
- Purpose: clean prospective test of section 10 (model vs early line predicts the move). Score each row
  against the closing line and the result once the games settle.

### 13. NBA v10 overhaul (started 2026-11-20)

- **Next action:** none blocking; let the forward clocks run. Weekly, run
  `python scripts/nba_v10/g2_report.py` (gate G2, v10 vs Main) and
  `python scripts/nba_v10/forward_report.py` (test A line movement, test B injury latency); both
  stay PENDING until their pre-registered n (G2 150 picks; A 400 games per market; B 100 events).
- **Running (scheduled, research only):** `ModelPrediction-NbaPretipCapture` (registered 2026-11-21,
  every 10 min 11:00-02:00 ET) captures ESPN odds (12 h slate) and Polymarket near tip, then in a
  budgeted thread: official injury reports, ESPN box-score catch-up, nba-player-v10 shadow log,
  T-C2 events (`<runtime>/research/nba_v10_forward/`, `<runtime>/injury_reports/nba/`). Skipped
  00:15-01:20 ET. First full run 16:50 ET 11-21: exit 0, 8 reports, 5 player records, 2 T-C2 events.
- **Results so far:** G1 FAIL for v9, v9.1 and the Phase 3 player layer (all lambda 0); v10 prices at
  the DraftKings devig (verified on the 16:00 run) and its only edge is T-B (>= 3.0 points). G2 rules
  pinned and amended before any pick scored (plan section 9). v9 keeps Main with edge sizing; Flat
  calls everything.
- **Checked 11-21 evening:** in-window pre-tip runs 19:10-19:50 ET (1-3 games inside 25 min of tip)
  took 15-40 s, all exit 0. Their 2-12 HTTP 429s are Polymarket `/book` calls that the capture
  retried through: 0 failures, 0 missing asks, no missing-quote events, no rate-limit pauses.
- **Open:** `forward_report.py` does not yet compute the
  test-A paired player-minus-v9 slope that PREREG_FORWARD.md section 5 requires (needed before
  400 games). v10 spread rows carry `model_artifact_hash` null for the v9 spread source (totals have
  it); upcoming games are treated as regular season in the forward log (fix before April); 2026
  playoff games are absent from `nba_games_all.jsonl`; a pre-existing 311 s capture run (19:50Z
  11-21) brushed the 300 s supervisor timeout.

## 2026-11-22 — Non-binary esports void re-grade and exit-75 skip escalation (quick-261122-skp)

Operator: "regrade" and "escalation for repeated exit-75 skips". Follow-up to the 2026-11-22 review fixes below.

- **Re-grade (`scripts/regrade_nonbinary_esports_voids.py`, tests in `tests/test_regrade_nonbinary_esports_voids.py`).** Candidates are the rows still carrying the stored void reason `esports market settled to a non-binary price` (identity-scoped, no time window): 206 esports moneyline rows (CS2 198, Dota2 7, LoL 1, across Main/Flat/research/gated). The one darts row (`market resolved terminal with non-binary prices [0.7, 0.3]`) is not esports and was left alone. Each row is matched to a BO3 series only when both team names resolve, exactly one finished series with a winner starts within 12 h of the event start, and it has a winner; otherwise it stays a push. Result: **1 of 206 confirmed and re-graded** (`671c5cf4932049de`, Flat CS2 EAC Rising vs Eternal Fire Academy, pick on home, push 0U to loss -1.5U, correction reason on the event chain). The other 205 stay void: 159 CS2 have no BO3 series near the start (BO3 covers Aug-Nov, 4,967 finished CS2 matches, but not these lower-tier fixtures), 38 CS2 team names do not resolve to a BO3 team, 7 Dota2 and 1 LoL have no series. They are not guessed from last-trade prices. Applied with `--apply --expect-count 1` under the daily lock; backup `E:\model-prediction-runtimeackups\ledgers.pre-esports-regrade.20261122T082343Z.db`; event chain intact. The 569 rows voided with `esports market terminal (...)` were already BO3-checked at void time and are out of scope. The xlsx `model_ledgers` mirror was not touched (SQLite is the authority).
- **Exit-75 escalation (`system_health.py`, `43b3f796`, `93f8cf9c`).** A worker whose latest run is `skipped` goes DOWN only when the streak (every skipped row newer than the worker's last non-skipped row) has at least N rows spanning at least T hours: daily 3/3 h, production 3/6 h, auto-buyer 12/3 h (15-min cadence), pre-tip captures 6/1 h, default 3/6 h (`_SKIP_ESCALATION`, sized from `runs.db`: longest daily streak 2 rows, longest auto-buyer 7 rows / 3.0 h on 2026-10-31, the 12-skip rule at 3 h would have tripped it). Shorter streaks degrade as before; the recording of exit 75 is unchanged. `scripts/health_and_data_gap_monitor.py` is unchanged and still reports any non-completed latest daily row as WARNING.

## 2026-11-22 — Project-review fixes: non-binary void, supervisor exit 75, NBA v7/v9 blend sign, v9 key drift (quick-261121-xaw, quick-261121-xbn)

From the 2026-11-21 whole-project review. Operator: "fix this". Served numbers are unchanged by the NBA items; the settlement and supervisor items change recorded outcomes going forward only.

- **Settlement (a97d5048)**: `_settle_by_resolution` voided (a 0U push) as soon as a terminal market showed non-binary prices, but Polymarket US returns pre-close trading prices on decided markets (the esports path mis-voided about 260 picks on this). It now logs and returns a `reason` dict and leaves the pick open; the no-ESPN branch routes it to `failures` so the 7-day "unrecorded" rule cannot void it either. Ledger evidence (read-only, 2026-11-22): 207 settled-push rows carry the non-binary reason: cs2 198 (Flat 113, Main 83, gated 1, research 1), dota2 7, lol 1, darts 1; none in the six in-scope sports. Past voids were NOT re-graded. One existing test pinned the old void and now asserts no-void/no-settle with a reason (`tests/test_resolution_settlement.py`); the fix reverted fails 4 tests.
- **Supervisor (8a682d04)**: a supervised worker exiting 75 with no timeout kill is recorded `skipped` (note kept, exit_code 75) and `run_worker` still returns 75; `system_health` prints the row note, not a hard-coded "(lease held)". Exit 1 stays `failed`, a timeout kill stays `failed`. `health_and_data_gap_monitor` still flags any non-`completed` daily row. Repeated 75s now degrade instead of reading DOWN; no consecutive-skip escalation exists.
- **NBA blend sign (1659f753)**: `nba_v7.py` and `nba_v9.py` used `market_home_margin = -spread_away_line` while the cover formula treats `spread_away_line` as the home-margin line (review item B4; v9.1 already had the corrected sign). Dormant: `market_blend_weight` is 0.0 in every artifact under `config/`, `data/calibration` and the runtime models root, and `nba_replay_harness` raises on a non-zero weight. A 48-prediction fixed-slate SHA-256 across the v7/v9 spread, total and moneyline wrappers was identical before and after. `tests/test_nba_v91.py::test_b4_blend_uses_spread_away_line_as_home_margin_line` asserted v9's old -6.5 and now asserts +6.5. Research scripts that run v7 with a blend weight above 0 give different numbers if re-run: `scripts/ablate_nba_v7.py`, `scripts/market_blend_kaggle_revalidation.py`, `scripts/test_combo_features_v7.py` (not edited).
- **v9 artifact key drift (b7bfff2a, 75706abc)**: containment only. `NBA{Spread,Total,Moneyline}ModelV9.from_artifact` swallow unknown keys (`**_extra_kwargs`) and take code defaults for omitted ones. Each load now logs one INFO line listing both sets (`artifact_key_drift`), and `tests/test_nba_v9_artifact_keys.py` pins the shipped sets and default values. Counts (unread / defaulted): spread-v9 18 / 4, total-v9 18 / 15, moneyline-v9 7 / 27; the unread counts include metadata such as `artifact_hash` and `qualification`. Confirmed unread: spread `possession_model`, `dynamic_matchup_pace`, `coherent_moneyline`, `use_logistic_moneyline`; total `possession_model`, `dynamic_matchup_pace`, `team_total_weight`, `trend_total_weight`. Wrapper and engine default tables do not differ. Nothing was honoured, edited or made strict (each changes served numbers or the artifact hash).
- **Verification**: 222 targeted tests pass on the merged tip; `ruff check src/ tests/` clean. In a worktree without `.venv`, `test_worker_registry_commands_exist_on_disk` fails; it passes in the live tree (`tests/test_run_supervisor.py` 16 of 16). `tests/test_dashboard_server.py` was not run to completion by the implementer (it hung for several minutes) and is unverified. `critical-reviewer`: no blockers.

## 2026-11-22 — NCAAF/NCAAB bug hunt fixes and bad-price archive (quick-261122-nbf)

- **Selectors (7af9d47d)**: spread/total rungs last seen >30 min before the game's newest capture no longer win on balance (11-21: 33 rows, 4 Main, priced off hours-old rungs after the day-ahead capture b87d6452); exact balance ties go to the newer quote; HALTED/untimed books skipped; v6.2 no longer logs 1e-12 "0.0000-edge" picks.
- **Settlement (b1d48666)**: NCAAF spread/moneyline closes resolve the side by team identity and title position (NM State, UMass, San José, App State, Georgia Southern "Eagles", "Fightin' Blue Hens" were blank); resolution settle applies `ncaaf_settlement_slug` and declines ambiguous outcomes; a losing whole-number spread was graded a push in resolution scores; NCAAF name fallback never pairs namesakes; NCAAB fallback dropped the bare-mascot rule and fails closed on two different matching games (NCAAB 53c03c58cbaa4a05 Samford @ USC Upstate 11-18 was settled 78-72 W on UNCG @ Gardner-Webb; real 64-74 L; 0U row, left in place).
- **Archive (operator "Archive bad old rows", 2026-11-22)**: 33 settled NCAAF rows via `archive_settled_rows`: 12 priced on another game's contract (pre-32528f57 one-word team join, 10-24..11-07; 2 Main +2.40U, 10 Flat +11.54U, all wins) and 21 opening-weekend legacy rows with a fabricated 50c decision price (Flat, 11-10, -2.97U). Archive `data/archive/2026-11-22-ncaaf-bad-price-rows/` (MANIFEST.json, settled_rows.jsonl); backups `ledgers.db.pre-ncaaf-bad-price-2026-11-22-*.bak`, `E:\model-prediction-runtime\backups\ncaaf-bad-price-2026-11-22-*`. Not archived (not in the approved list): 881b2c77229146ee (NM State 10-21 v2 Flat, priced at the other side's ask, graded W, contract bought lost).
- Evidence: `outputs/ncaaf/bug_hunt/{settlement,pricing,models}.md`, NCAAB replay `outputs/ncaab/ncaab_v7_vs_v6_daily.md`.

## 2026-11-21 — NCAAF Polymarket quote side guessed from an unresolvable label (quick-261121-qsd)

- **Bug**: `_match_cfb_spread` / `_match_cfb_moneyline` decided which team a quote's line belongs to by resolving its `team` label and fell back to home. Polymarket labels Arizona "University of Arizona" (also "NM State", "U. of Idaho", bare mascots early season). On 11-21 Arizona -10.5 at Kansas State (both "Wildcats") was read as Kansas State -10.5, so v5, v6.2 and v7 each priced "Arizona +10.5" at the Kansas State +10.5 ask (52%): phantom edges of 31-42% on Flat (v5 Main's 8% max-edge gate kept it out of Main and the buyer).
- **Evidence**: the quote's `team` is the title's first team on 22,256 of 22,256 resolvable spread labels and 1,165 of 1,165 moneyline labels; the spread long description equals the line on 94,072 of 94,072 rows. Ledger rows with a recoverable slug: 828 consistent, 13 flipped (3 open Flat Arizona rows 11-21, 8 superseded, 2 NM State v2 Flat 10-21 incl. one settled +0.83U). 1,856 older rows carry no slug.
- **Fix**: side from the title position already matched to ESPN away/home; the label only cross-checks (`_quote_label_side`) and a contradiction fails closed. Tests in `tests/test_cfb_serving_parity.py`, mutation-proven. Operator: "void the Arizona pick" (2026-11-22 ~01:25Z): the 3 open Flat spread rows (v5 `a73eac39a721493a`, v6.2 `fb2d86e1e11f4877`, v7 `ac24d71045194a76`) voided through `PickLedger.void` (push, 0U, audit event), backup `ledgers.db.pre-arizona-void-20261122.bak`; that game's moneyline/total rows were priced correctly and stay.

## 2026-11-21 — NCAAF overhaul execution: v5 identity/FCS/sign fixes, CLV close bounds, capture queue, ledger integrity, stale-price test, ESPN play crawl (quick-261121-spt/lgc/plb/idf/pbp)

Operator: "go ahead execute all" on the ROADMAP NCAAF overhaul plan (2026-11-20). v5 stays champion and keeps picking; nothing was disabled.

- **v5 team identity (c116bd75 -> cherry-picked, `features/cfb_team_identity.py`)**: history matched teams by exact display name, so App State (7/3 games in 2026), San José State (7/4) and Hawaii (5/5) were split across two names and rated on half a season (also Hawaii 2016-19/2021-26, SJSU 2021-24, UConn 2016; Southern Miss, UL Monroe, UTSA, Sam Houston, FIU across seasons). Now one canonical key (ESPN id, else normalised name joined from id-bearing rows before kickoff), reusing v6's `TeamResolver`.
- **FBS/FCS class (a0e30316, b9c79c0c, 8f3be391)**: `resolve_team` had no season and left 351 of 497 names unresolved; the old rule labelled 1,703 of 1,782 FCS team-seasons as FBS, so `is_fbs_vs_fcs` and the Main `block_fcs` gate failed open. Class now comes per season from ESPN feed groups (80/81), unknown fails closed to not-FBS with a warning; one class-driven conference fallback for team, Elo and efficiency opponents; a slate team's known ESPN id beats its display name. Point-in-time on 732 FBS-involved 2026 games: new rule wrong on 2 (week-0 FCS-to-FBS movers), old rule wrong on 124.
- **Efficiency sign bug (549b5ef2)**: `adj_scored = s_pts - opp_offset*0.35` ran opposite to its comment, adding +6.3 points for scoring against FCS (offset -18) and shrinking points against the SEC. Flipped. Impact on 427 settled 2026 games (10-01..11-19): Brier HEAD 0.2174, identity/class fixes 0.2148, plus sign flip 0.2149; margin MAE 13.39 -> 13.06; FBS-vs-FBS flat. The 11-21 slate's FBS-vs-FCS games move to the FBS favourite (Tennessee Tech @ Mississippi State home win 36.0% -> 64.5%; HEAD projected the SEC home team to lose by 5.1). No calibration constant changed; v5's margin slope looks mildly under-stretched (actual on raw projection slope 1.53 vs artifact 1.3189; 21+ favourites projected 28.3 vs actual 32.8) but 427 games is not enough to refit. Per-game extraction 0.11 -> 0.30 s. `tests/test_cfb_team_identity.py` (11 tests, each mutation-proven).
- **CLV closes (0630faa2, acd665f9, b3043df7 -> ffcee729, a29305c5, 8016e594)**: `closing_snapshot` only bounded the quote by the ledger row's kickoff, so six 10-24 rows priced on another game's contract (New Haven vs CCSU, 16:00Z) took its 17:03Z in-play 99c quote as their close. A close must now be observed before the earlier of the row's and the contract's own kickoff, in an OPEN market, at the row's own line; Over/Under matched by whole word ("Thundering Herd" read as Under); the long-side default for unmatched selections is removed; every blank CLV logs a reason. Cross-sport recheck of 3,497 stored closes: six 99c -> 56c, two wrong-side stored closes (NBA `cbf96945def24f2a`, NCAAB `bc6b84b5dcc4423e`), 146 soccer double-chance/draw closes now blank (stored values were wrong), 2 MLB blank (HALTED book). Stored values were not rewritten.
- **Polymarket capture (6f6101e1)**: the /book queue kept each game's markets together, so a 600 s budget cut left late kickoffs with nothing; now moneylines first, then each game's main lines, then alternates. 10-17 moneyline-only was Polymarket's league feed carrying winner markets only (fixed earlier by event-detail hydration a5e7d1a9); 11-07 was the first 429 abandoning the queue (fixed earlier by 79487b2c); every full-game 11-07 row has `timestamp_valid` false. Capture runs only inside the daily job and only for today's ET slate (`cli/daily.py:298-306`), so there is no day-ahead quote and stored closes can be ~3 h stale. Known limit: a row matched to the wrong game at decision time still gets that game's pre-kickoff close (title check rejected clean abbreviated titles, dropped).
- **Ledger integrity (cde2c4e7 + operator-approved archive)**: `archive_settled_rows` hard-failed `cfb_replay_engine_identity_mismatch` on v1-v4 rows because the engine source changed after capture (settle/void/update only warned); archive now warns too (`tests/test_esports_replay.py::test_archive_settled_rows_survives_stale_code_hash_under_sqlite_authority`, mutation-proven). The failed first attempt left one orphan `settled_pick_archived` audit event (39ad8fec..., pick 26e45acf41d047be) before the real one. Archived 34 settled NCAAF rows: 23 priced on another game's quote (incl. Main 5f785b3ec8f3454d ODU @ Louisiana Tech and Flat 2b9c7715d9304cf5 South Carolina vs Tennessee on the Charleston Southern contract; the two 10-31 quote records are no longer in the snapshot file, so at most one row per record could have been genuine), 3 with a `void_reason` still settled, 8 duplicate Flat copies at a line 0.5-1 point off a Main pick. Main settled -80.34U -> -80.24U; Flat -65.56U -> -78.58U (the collided rows had netted about +17U). Archive `data/archive/2026-11-21-ncaaf-integrity-rows/` (MANIFEST.json, MANIFEST_phase2.json, MANIFEST_phase3.json); backups `E:\model-prediction-runtime\ledgers\ledgers.db.pre-ncaaf-integrity-2026-11-21-*.bak`. Ole Miss @ Oklahoma (Sunday 11-15 18:00Z) verified real and correctly graded.
- **Stale-price test (ee49c5d6, 336de2b3; `outputs/ncaaf/cfb_stale_price_test.md`)**: pinned model-free rule (buy the Polymarket rung at the anchor line when the ask is cheap). ESPN-close arm (UPPER BOUND, look-ahead): 222 picks 118-104, ROI +13.3% [-3.9%, +27.8%]; spreads +7.3%, totals +18.1%, both CIs span zero; clean DraftKings as-of arm n 4. Verdict INCONCLUSIVE, so the blend stays in v6.2. 569 of 731 ESPN-lined 2026 events have a Polymarket match. About 42% of Polymarket "spread"/"total" rows are half/quarter/team-total markets; live pricing already excludes them (`models/college_football.py:411,489`).
- **ESPN play-by-play (efc434a5, cb09e962)**: stratified sample of 394 games: 93.1% of FBS-involved and 81.1% of FCS-FCS usable (drives, >= 80 plays, fields present, final score reconciles); weak cells 2022 FBS-involved and FCS-FCS 2017/2018/2022. Full resumable crawl of 16,399 games running (`python scripts/cfb_espn_pbp_crawl.py status`, log `data/research/ncaaf_v7/crawl.log`). Features (`scripts/cfb_pbp_features.py`): success rate 0.421, yards/play 5.78, garbage-time flag on pre-play margin; ESPN scores are post-play and goal-to-go distance is stored as 0.

## 2026-11-20 — Best-pick CONFIRMED_BEST was a winner's-curse label; hardened with 30-decision, Wilson-vs-price and closing-line gates (quick-261120-w6i)

- **Defect and evidence.** In forward use `CONFIRMED_BEST` was anti-predictive: tracker picks labelled confirmed went 19W-27L, -14.07U; picks confirmed from buckets under 10 graded claimed a 76% win rate and hit 34% (12-23, -18.18U), while buckets with 10-29 graded went 7-4; closes on confirmed picks moved against the logged price 27 times and for it 12. Cause: `MIN_GRADED=4` plus a 60% WR / +15% ROI hurdle picks the luckiest of hundreds of small buckets.
- **Fix (9ec2eb63, 0d937297).** CONFIRMED_BEST now needs n >= 30 verified decisions, a one-sided 95% Wilson lower bound above the bucket's stake-weighted mean first-sight price, ROI >= 15%, non-negative bucket CLV (>= 8 moved keys, negative mean means UNPROVEN) and no model-family veto (>= 20 moved keys, mean CLV <= 0 means under review), plus the unchanged verified candidate quote. The flat 60% win-rate gate and its constant were deleted on purpose: it is the even-price vig heuristic, blocks profitable underdog buckets and is looser than the price comparator for favourites. A statistically confirmed bucket is never a trap. Tests 17 to 28; revert proofs observed for the n>=30/Wilson gate (3 tests fail under the old 60%/15% rule) and the bucket CLV gate.
- **First-sight basis bugs (same commits).** D1 open picks are bucketed on their first-sight edge via `first_sight_edge` and keyword-only `audit_candidate(bucket_edge=)` (4 of 122 open rows were their own first sighting). D2 a key is verified evidence only when its first row is verified (202 of 2,388 verified keys took a later rerun's row, 104 landed in another bucket). D3 nrfi rows are never an event score source. D4 rows created at/after their own start, or with an unparseable start, never become first sight. D5 keys with any archived/removed row are excluded whole (16 of 22 still had live rows).
- **Live before/after, `--all-open`, 122 open rows, back to back, read-only.** HEAD: 13 CONFIRMED_BEST / 64 QUALIFIED_MODERATE / 45 DOWNGRADE_TRAP, engine build 1.46 s. New: 0 / 88 / 34, engine build 1.42 s. 65 picks changed bucket on first-sight edge; 6 carry UNPROVEN, 30 carry under review. Widget contract check (8-positional call, evidence fields, `_connect_ro`, `DB_PATH`, `ET_TZ`) passes on both.
- **What passes.** No bucket on the ledger clears the full bar today. Nearest: MLB totals over 2.0-4.9% (59W-45L, n=104, ROI +15.6%, bound 48.7% vs 49.0 cent mean price, 6 moved keys), out of season. Families under review: NBA moneyline v4 and v5, NBA spread v9, NBA total-under v9, NFL moneyline v4.
- **Open items (not done).** Soccer draw grading; zero-unit rows; SQL performance rewrite; the external Rainmeter logger. The logger already passes `bucket_edge` to `audit_candidate` and calls `first_sight_edge` when the engine has it, so no logger change is needed (corrected from the plan's original open item).

## 2026-11-20 — Best-pick audit bucketed on the last pre-game rerun, not the first sighting (quick-261120-gaq)

- **Defect: population selection, not arithmetic.** `scripts/audit_best_picks.py` bucketed each decision on the edge of its settled row, which is the last of up to four daily reruns. That conditions on line movement after the pick was offered, so winners whose edge grew and losers whose edge shrank migrated out of the band. Results and `pnl_units` were verified correct.
- **Evidence.** NBA total v9 under 5-8% read 8W-1L, +77.4% ROI; a manual tracker's 8 first-sight unders in that band went 3-5 and all 5 losers had left the band by the final run (first-in-band 11W-10L, +3.5% ROI). NBA moneyline pooled 5-8% read 8W-1L verified, +47.6% ROI against about +11% at first sighting.
- **Fix (2a48135f).** Buckets now use each decision's earliest open/superseded/settled row (verified: earliest verified row), graded at that row's own line and price (own result, else final scores, else same-line settled sibling, else skipped). Archived/removed rows are never read. The old basis survives as `last_run_by_*` maps, `last_run_evidence` and one "Context, last pre-game run (old basis)" flag; thresholds, basis strings and return keys are unchanged.
- **Measured, live ledger.** NBA total v9 under 5-8%: first sight 1W-5L, -67.3% ROI vs last run 8W-1L, +77.4% (pooled unders: 4W-11L, -47.1% vs 13W-3L, +61.1%). NBA moneyline pooled 5-8% verified: 9W-5L, +5.9% vs 8W-1L, +47.6%. NBA spread v9 5-8%: 6W-3L, +31.7% vs 8W-3L, +41.3%. The verdict for an NBA total v9 under at +6.5% edge moves from CONFIRMED_BEST to DOWNGRADE_TRAP. 494 first-sight keys could not be graded and are skipped. The tracker's 11W-10L is a different population, so it is reported, not reconciled.
- **Old basis preserved.** The new `last_run_by_*` maps equal HEAD's `stats_by_*` maps on the live ledger: identical key sets, identical wins/losses/pushes, pnl and staked within 1e-6. The score-grading rule agrees with the ledger's own settled results on every in-scope NBA/NCAAF/NFL/MLB/WNBA/NCAAB row checked (soccer moneyline draws and tennis sets differ, both out of scope).

## 2026-11-18 — health audit (`quick-261118-hlt`)

- **Settlement name variance.** `_settle_by_resolution` matched sides by substring only, so clubs spelled differently by the ledger and Polymarket (Oerebro Hockey/HK, Anadolu Efes Istanbul/SK, Besiktas Istanbul/JK) never resolved and would have been voided by the 7-day rule although the markets had resolved as wins. Fixed with a fail-closed distinctive-token second pass (`16f92f3b`). Euroleague and hockey results histories only refresh inside their forecast block, so they stop while switched off; settlement must not depend on them.
- **`system_health` economics check** now exempts the writer's `[UNQUOTED_SHADOW:]` marker (placeholder-priced NCAAB calls stake nothing by design, quick 261115-1m3); unmarked silent-zero rows (Flat MLB class) still report.
- **`market_quotes.db` corruption.** Single-bit flips (bit 0x08) left 8 rows where the table and the two indexes disagree (`soccer`/`sgccer`, `mlb`/`elb`, event_id `mlb-`/`mdb-`); the health monitor reported them as stale sports. Repaired under the daily lock with a backup. `PRAGMA quick_check` does NOT compare indexes with the table; use `integrity_check`. Suspect memory or storage hardware (python.exe crashes, 00:30 memory exhaustion, bugcheck 0x139 on 11-07): run a RAM test.
- **Dashboard** died 11-16 (python.exe crash) and its logon-only task never restarted it; restarted 11-18.

## 2026-11-13 — CLAUDE.md session history moved here (operator directive: trim CLAUDE.md to working rules)

The dated sections below lived in the root `CLAUDE.md` from 2026-08-13 to 2026-11-13 and were moved
here verbatim (headings demoted one level) when CLAUDE.md was cut back to durable working rules.
Their still-binding contracts (RuntimePaths, registry fail-closed, supervisor-only scheduling,
ProductionPredictionStore as sole production.db writer, sqlite ledger authority, train/serve parity
test, seed pins) are restated in CLAUDE.md's "Durable contracts" list; everything else here is history.

### 2026-08-15 — consolidation K (runtime singularity)

- **Rolling vs frozen model artifacts.** `config/models/*.json` are frozen
  promoted artifacts (git, never rewritten by the schedule). The daily
  cycle retrains esports/KBO/NPB ratings into the runtime root's
  `models/` (`RuntimePaths.models_root`); research forecasts read
  rolling-first with frozen fallback (`cli._research_models_dir()`).
  If you add a new retrainable artifact, route its writes through that
  contract — do not add new checked-in files the schedule rewrites.
- **The data/ trees are untracked operational output** (`git rm --cached`
  2026-08-15, files remain on disk): availability, player_priors,
  production, esports, international_baseball, historical, odds,
  point_in_time, main/flat/research/gated_research xlsx, model_ledgers
  xlsx, logs, features, dashboard scratch. The SQLite ledger (runtime
  root) is the canonical evidence store; `data/archive/` and `snapshots/`
  remain tracked evidence. Do not re-add files under the ignored trees;
  if a genuinely new evidence file needs tracking, put it outside them
  or adjust the ignore list deliberately.
- **The daily worker's lock lives at the runtime root**
  (`${MODEL_PREDICTION_RUNTIME_ROOT:-data}/locks/daily.lock`), not in the
  repo checkout.

### 2026-08-14 — Infrastructure consolidation, Phase A (control plane)

The operator's 20-item consolidation directive (registry / supervisor /
health / data plane / dashboard) is being executed in phases; **Phase A
is done and pushed** (`cleanup/final-debug-2026-08-14`). These are the
new working contracts — don't regress them:

- **`config/production.yaml` is schema v3** with explicit `models:`
  (13 entries: 11 json_artifact + 2 code_backed soccer/tennis) and a
  `champions:` map. `src/model_prediction/production_registry.py` is the
  single source of truth: loading validates EVERY enabled entry and fails
  that model closed (`load_error`, `resolve()`/`champion()` refuse it);
  the primary's failure is a hard ValueError. Legacy v1/v2 configs still
  load (entries derived from allowed_models + artifact_map, identity from
  each artifact's own fields). Keep `production_canary.py`'s public API
  (it delegates to the registry; tests pin its messages).
- **Champion ≠ primary.** The champion is what SERVES a sport/market
  (`champions:` map); the primary is what the canary predict cycle runs.
  Promotion is one command, never hand-edited yaml:
  `python -m model_prediction.model_promotion promote --new … --sport …
  --market … --approved-by … [--evidence …]`, rollback likewise. Records
  live in the `promotions` table of `data/runs.db`.
- **Scheduled runs go through the supervisor**:
  `python -m model_prediction.run_supervisor run <worker>` (workers:
  daily/production/rebuild-shadow, command map in `WORKERS`). Run rows
  (run_id, lease, heartbeat, exit, log) land in `data/runs.db` (SQLite
  WAL, gitignored). Health reads them — `python -m model_prediction.system_health`
  reports evidence-based status with reasons (DOWN/DEGRADED/HEALTHY).
- Historical JSONL files are **ingest-ordered, not event-ordered** —
  "newest event" is a max-scan over the file, never the last line
  (system_health learned this the hard way).
- All three launchd plists (daily / production / rebuild-shadow) are
  rewired to the supervisor and loaded; the dashboard's "daily" action
  routes through the supervisor too (2026-08-15).

### 2026-08-14 (noon) — data-plane contracts (consolidation B)

- **All canary/control-plane mutable state resolves through
  `RuntimePaths`** (`runtime_paths.py`) — `MODEL_PREDICTION_RUNTIME_ROOT`
  when set (launchd jobs), repo `data/` otherwise.
  `migrate_legacy_state()` carries legacy repo-local files into the
  runtime root once (copy-tmp+rename, never deletes). New code that
  touches runs.db / production.db / production_state.json / supervisor
  logs must go through RuntimePaths, not hand-built paths — the
  2026-08-13 split-brain was exactly this disagreement.
- **Operational entry points FAIL CLOSED on a missing runtime root**
  (2026-08-15): `RuntimePaths.resolve(require_external_runtime=True)` —
  supervisor, canary, promotion, system_health, dashboard,
  cli_production all raise instead of falling back to repo `data/`,
  because the fallback silently created a second runtime next to the
  canonical one. Local dev uses the default `resolve()`; tests use
  `for_test()` or inject pre-resolved `paths`. Don't add a new
  operational entry point without the flag.
- **Exactly one daily scheduler** (2026-08-15): every launchd job
  (daily/production/rebuild-shadow) and the dashboard's "daily" action
  route through `run_supervisor`; the dashboard never executes
  `scripts/run_daily.sh` itself. A busy lease returns exit 75
  (`daily_lock` convention) and is recorded as skipped — lock refusal is
  a coalesced skip, never a failed job. The dashboard service
  (`com.vc.model-dashboard`) is the only owner of port 8765 and carries
  `MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite` like the other jobs.
  "One scheduler" means no competing schedulers, not run-once-per-day:
  **`com.modelprediction.daily` runs 4x/day since 2026-09-04** (00:30,
  06:00, 12:00, 18:00 — operator directive, to catch markets/games that
  don't open until later in the day). This is safe because
  `run_daily.sh`'s forecast step explicitly clears and replaces today's
  picks on every run, settlement/ingestion are idempotent, and the
  auto-buyer's bought-index cross-references the audit log to refuse a
  double-buy on an already-bought pick_id/market_side. Checked-in copy:
  `ops/launchd/com.modelprediction.daily.plist` — keep it byte-synced
  with the live `~/Library/LaunchAgents` copy when changing the schedule
  again.
- **ProductionPredictionStore** (`production_store.py`) is the ONLY
  writer to `production/production.db` (narrow API, identity key
  event_id+model_id+market_type+horizon+decision_time_utc, decisions +
  market_snapshots tables, keyset pagination, `cli_production export`
  for xlsx). `cli_production` no longer uses `ProductionLedger` (kept
  for legacy compat + tests). Schema migrations for legacy DBs happen in
  `_migrate_columns` — new columns/indexes must be added there, NOT
  only in `_SCHEMA` (CREATE IF NOT EXISTS won't touch a migrated table).
- **Superseded 2026-08-16 (verified live)**: the SQLite cutover this
  note warned against has already happened and is the current state —
  all four scheduled jobs (daily/production/rebuild-shadow/dashboard)
  run with `MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite`; `PickLedger`
  (`ledger.py`) treats the runtime-root SQLite store
  (`ledgers/ledgers.db`) as canonical, and the main/flat/research xlsx
  are now a best-effort EXPORT whose failure only logs a warning
  (`ledger.py::_write_rows`, ~line 1641) — never blocks the commit.
  Verified 2026-08-16: sqlite `ledger_records` has real rows across all
  four tiers (main/flat/research/gated_research); `data/main/mlb.xlsx`'s
  **Picks** sheet (not the Summary tab) row count matches sqlite's
  open+settled count exactly (97 = 31+66); archived/removed rows live
  under `data/archive/`, not deleted. Don't re-flip authority back to
  `xlsx` without an explicit decision — that's the real remaining
  governance question, not whether the cutover happened.

### 2026-08-13 — Champion/challenger + settled-picks + cleanup (this session)

**New module `src/model_prediction/champion_challenger.py`** — production
freeze + paired comparison harness. CLI: `freeze-production`,
`compare-champion`. Docs: `docs/CHAMPION_CHALLENGER.md`.

**MLB v9 Phase 1 features wired** — `starter_kbb_gap`, `residual_trend_gap`,
`bullpen_fatigue_gap` added to `validation.py` / `learned_forward.py` /
`features/starter_history.py`. Runner: `scripts/mlb_v9_ablation.py`.
Ablation result: residual-trend variant wins (+56.4u vs +43.1u raw trend).

**MLB v9 Phase 2** — `park_factor_at()` PIT-correct park factors added
(`features/park_factors.py`), wired into `validation.py` walk-forward.

**WNBA spread fix** — `wnba-spread-baseline-v1` was predicting moneyline not
spread (never used the line). Replaced with `wnba-spread-margin-v1`
(margin_normal, `P(away_cover)=Φ(line; margin, 10.5)`). Broken artifact
archived. `config/model.yaml` spread/total refs corrected.

**Cleanup** — 38 files removed (12 `*.previous.json`, 22 obsolete configs, 4
dead rebuild models). Config root 63 → 27 files.

**Known gap** — canonical `data/model_ledgers/` froze 2026-08-03 (retired main
ledger was its only writer). Dashboard + settled-picks loader read stale data.
See `docs/archive/SETTLEMENT_GAP.md` (fixed 08-13; sqlite canonical since J).
Do NOT silently re-route production data paths
without an explicit decision.

**Settlement routing fix** — model-ledger mirror now writes to canonical
`data/model_ledgers/` (threaded `model_ledgers_dir` through `PickLedger`,
`main_ledgers.py`, `research_ledgers.py`). Previously each tier mirrored to
its own subdir (`data/flat/model_ledgers/`, …) while the dashboard + loader
read only canonical — that froze on 2026-08-03. See `docs/archive/SETTLEMENT_GAP.md`.

**MLB distribution methods** — `simulate_game` now takes a `method` argument
(`gamma_poisson` default / `negative_binomial` / `independent_poisson`);
`compare_distribution_methods()` prices moneyline/spread/total from one
coherent joint draw per method. Wired through `MeasuredEdgeMarginModel` /
`MeasuredEdgeTotalsModel.predict(..., method=...)`. NB is the first serious
challenger to the incumbent gamma-Poisson; it is runnable but not yet
promoted. Tests: `tests/test_mlb_distribution_methods.py`.

### 2026-08-13 (later) — deep-audit fix pass (F-72 → F-84)

Full audit run against this tree; every finding fixed with regression tests.
Details in `MASTER.md`'s 2026-08-13 session entry and
`docs/PROJECT_STATUS.md`'s "2026-08-13 deep-audit fix pass". Things that
change how you should work here:

- **Main ledger is un-retired** (operator directive): `main_ledger_enabled:
  true`, workbooks restored to `data/main/`. Phase B's `retired` mechanism
  still exists; don't re-flip without a directive.
- **Train/serve parity is now a testable invariant** —
  `tests/test_validation.py::test_train_serve_parity_for_v9_features` proves
  `validation.py` (training) and `learned_forward.py` (serving) compute
  identical values per feature. Any new feature must hold both sides in
  literal sync — the 2026-08-13 audit found three v9 features where they
  silently diverged (F-79). v9 variants use the new `park_factor_pit`
  feature name; `park_factor` stays the static table for v8's trained
  contract. **Prior v9 ablation numbers are void — re-run
  `scripts/mlb_v9_ablation.py` before trusting them.**
- **Stable seeds are load-bearing**: `simulate_game`'s default
  `gamma_poisson` stream must stay bit-for-bit across refactors; the seed
  pin test in `test_mlb_distribution_methods.py` must never be updated to
  "match new output" — it pins history (F-80).
- **ProductionLedger is live**: every `cli_production predict` writes
  `data/production/predictions.db` fail-soft; lifecycle transitions are
  guarded open→terminal only (settle/void/supersede/error). Operator
  commands are fail-LOUD, scheduler path fail-soft.
- **`pytest` runs WITHOUT the MODEL_PREDICTION_* env vars** (documented safe
  form) — several tests pin the no-env repo-colocated default; setting the
  launchd env vars makes ~12 of them red by retargeting them at the live
  runtime root.
- **Standing open items (2026-08-15)**: the v8 park-factor static-table
  leak is verified and documented (`docs/V8_REPRODUCTION.md`) — v8 is never
  modified; v9 uses `park_factor_pit`. `outputs/rebuild/verification.json`
  is CI-generated evidence (CONSOLIDATION.md policy) — a local 404 is
  expected. Soccer's Odds API credential remains a known-DEGRADED external
  dependency. The two launchd research agents are loaded and producing.

## 2026-11-15 — NCAAF totals: information-source tests, cfb-structural-v6.1 and the 11-14 retrospective replay (quick-261115-183)

**Bottom line.** The operator asked for information the NCAAF closing total cannot price quickly (level drift, line
movement, weather, QB status), a better totals model, and a forward-only test of the lopsided / FBS-vs-FCS tilt. Under a
rule written down before any validation or holdout number existed, **no source beyond the level tracker passed**. v6.1 is
therefore frozen v6 plus the walk-forward level tracker and nothing else (the tracker entered by operator instruction, not
by a passing test). It removes part of v6's over lean, adds no error gain, and **does not beat the closing line**. The
tilt is pre-registered for a forward test that needs 704 games. No promotion decision. v6.1 is not routed.

**Rule and pre-registration.** The pinned rule (`outputs/ncaaf/cfb_totals_sources_rule.json`, canonical-JSON sha256
`1481273f1fbd860a5e35ecc25a864818e74818f564aa0f6d5710ea42f524145f`) and the lopsided pre-registration were committed in
`8ebdc514`, before any workstream output; `57520a29` made the evaluator fail closed on any failed fetch. Every output
carries the same rule hash and each workstream read the holdout exactly once (`holdout_reads` 1). Windows by kickoff date
on lined games: select to 2022-11-11, validation 2022-11-12 to 2024-11-14, holdout 2024-11-15 to 2026-11-13 (already seen
in earlier v6 checks, so any read is a one-time look). Primary statistic per source: dMSE = MSE(close-only OLS) minus
MSE(close plus source OLS) on validation, coefficients fit on select; PASS needs the validation date-clustered 95% CI
lower bound above 0 and a positive holdout point estimate. Bootstrap B 1,000, seed 20261115.

| source | market | n select / validation / holdout | validation dMSE [95% CI] | holdout dMSE (point) | verdict |
|---|---|---|---|---|---|
| W1 level tracker (days 90, chosen on select only) | total | 4,590 / 1,790 / 1,861 (MAE and bias, not dMSE) | MAE v6.1 minus v6 +0.004 [-0.049, +0.057] | +0.048 [-0.045, +0.151] (MAE diff) | enters by operator instruction; no taxonomy verdict |
| W2 ESPN open-to-close movement | total | 4 / 1,064 / 1,861 | not interpretable (select n 4) | not interpretable | INSUFFICIENT_DATA |
| W2 ESPN open-to-close movement | spread | 4 / 1,063 / 1,853 | not interpretable (select n 4) | not interpretable | INSUFFICIENT_DATA |
| W2 intraday Polymarket ladders (2026) | total, spread | no select or validation data | none | none | DESCRIPTIVE_ONLY |
| W3 observed weather (wind, precipitation, cold) | total | 4,135 / 1,617 / 1,677 | +0.142 [-0.351, +0.587] | +0.057 | NOT_WORTH_BUILDING |
| W3 observed weather (wind times expected margin) | spread | 4,232 / 1,616 / 1,668 | +0.022 [-0.051, +0.100] | +0.030 | NOT_WORTH_BUILDING |
| W4 QB (backup start, QB changed; post hoc) | total | 542 / 1,566 / 1,607 | +0.703 [-0.449, +1.900] | -0.789 | NOT_WORTH_BUILDING |
| W4 QB (home-minus-away flag differences; post hoc) | spread | 542 / 1,565 / 1,608 | +1.986 [-0.229, +4.082] | +2.569 | NOT_WORTH_BUILDING |

Caveats that matter for reading the table. **W2:** ESPN carries open values only for 4 games in 2017 and from 2023-10-17
(select window 4 rows, validation 59.4%, holdout 100%), so the pinned test cannot run; the labelled exploratory refit
(coefficients fit on validation rows with open values) gives totals dMSE +0.140 [-0.140, +0.439] and spreads -0.647
[-1.499, +0.320] on the holdout, and neither is a verdict input. ESPN open and close are untimestamped, so even a pass could
only have been an upper bound. The intraday part uses Polymarket-lineage ladder quotes, not ESPN lines; "main line" is the
ladder rung whose over (or home-side) midpoint is nearest 50% at each snapshot, and the last pre-kickoff rung failed its
line-only agreement gate against the ESPN close on 7 of the 8 definitions (totals correlation 0.853, median absolute
difference 1.0 point; one spread sensitivity cut passed at correlation 0.976), so its exploratory outcome numbers (for
example totals: outcome beyond the close in the movement direction by +2.03 points [+0.22, +4.07] on 376 holdout games)
are not usable evidence. **W3:** Open-Meteo archive weather, 131 venues, 1,266 of 1,266 venue-seasons cached; observed
weather is already an upper bound (the line knows the forecast), and it still failed, so the forecast stage did not run
and weather is not worth building. **W4:** starters derived from box-score pass attempts (post hoc, leaky), 2022 onward,
so the select window is only 2022 up to 11-11; the spread holdout point is positive but the validation interval includes
zero, so no PASS. No weather or QB source reached UPPER_BOUND_PASS, so no point-in-time retest is owed.

**Level tracker.** Residual = actual total minus the v6 calibrated total, mean over a trailing window of games that finished
(start plus 5 h, inclusive) before kickoff; grid 14, 30, 60, 90 days then 150, 300, 600 games, day windows under 50 games
fall back to the last 150. Chosen on select only: days 90 (select RMSE 17.021 against 16.981 untracked, so the tracker does
not even improve the select fit).

| window (lined games) | v6 bias | v6.1 bias | v6 over lean | v6.1 over lean | corr(model - line, actual - line), v6 to v6.1 |
|---|---|---|---|---|---|
| select, n 4,590 | -0.41 | -0.09 | 47.5% | 50.6% | -0.003 to -0.004 |
| validation, n 1,790 | +0.38 | -0.37 | 66.0% | 58.7% | 0.059 to 0.055 |
| holdout, n 1,861 | +0.50 | -0.36 | 63.9% | 57.7% | -0.030 to -0.026 |

Expected result partly confirmed: the tracker removes the over lean on select (47.5% to 50.6%) but only part of it on
validation (66.0% to 58.7%) and holdout (63.9% to 57.7%). It adds
no edge (correlation with the closing-line residual unchanged and not distinguishable from zero in holdout) and no MAE gain.

**v6.1 contents and comparison.** `outputs/ncaaf/cfb_v61_spec.json` (spec_sha256
`8a3e5c6612aa72d0d9fc40b94d1ade4e40acb2ea649201750cbd74747db4573c`): frozen v6 calibrated total plus the days-90 tracker;
components empty; spreads v6 unchanged; frozen v6 params sha256 `6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70`
unchanged. Nothing was tuned after the freeze.

| holdout, 1,861 lined games (2024-11-15 to 2026-11-13) | MAE | bias | over lean |
|---|---|---|---|
| closing line | 12.39 | -0.79 | n/a |
| v6 | 12.99 | +0.50 | 63.9% |
| v6.1 | 13.04 | -0.36 | 57.7% |

On the common-v5 holdout cohort (1,511 games) v5 is 13.13, v6 13.02, v6.1 13.00 against a line of 12.52. Paired MAE
differences on the 1,861 holdout games (date-clustered 95% CI): v6.1 minus v6 +0.048 [-0.045, +0.151]; v6.1 minus line
+0.647 [+0.442, +0.854]. **v6.1 does not beat the closing line, and the closing line is significantly better.** Select
(v6.1 minus line +0.535 [+0.383, +0.689]) and validation (+0.280 [+0.105, +0.474]) say the same. Full tables:
`outputs/ncaaf/cfb_v61_comparison.md`.

**11-14 RETROSPECTIVE REPLAY** (`outputs/ncaaf/cfb_v61_replay_1114.md`; 11-14 is inside the data the tilt was found on, so
none of this tests the tilt). Settled at run time (2026-11-15 about 07:38Z): 117 games listed in the window (kickoff
2026-11-14T10:00Z to 2026-11-15T10:00Z, groups 80 and 81), all 117 completed, 0 forfeits, 0 unfinished, 0 v6 no-calls, 116
with an ESPN closing total (1 game has no line and is excluded from the line comparison). Closing lines fetched into the new
file `data/research/ncaaf_v6/espn_lines_20261114.jsonl` (the pinned `espn_lines.jsonl` untouched); the last kickoffs
(03:30Z to 04:00Z on 11-15) were fetched 3.65 to 4.15 hours after kickoff, shortly after those games ended, which is earlier
than the 6-hour rule the forward evaluator uses. Fresh v6 projections matched the frozen research rows on the 92 overlapping
games with max absolute difference 0.0. Tracker offsets applied: mean -0.84 points (range -1.10 to -0.77, 1,270 to 1,363
games averaged).

| cohort A, lined games (n 116) | MAE | bias | over lean |
|---|---|---|---|
| v6 | 13.32 | +5.69 | 69.0% |
| v6.1 | 13.02 | +4.85 | 60.3% |
| closing line | 12.53 | +3.97 | n/a |

The day was high-scoring: the closing line itself was 3.97 points low on average. v6.1 minus v6 -0.31 [-0.46, -0.16],
v6.1 minus line +0.49 [-0.19, +1.18], v6 minus line +0.80 [+0.07, +1.52] (game-level bootstrap, one date). On the 55
FBS-involved games that also carry a v5 ledger projection: v5 12.62 (bias +5.13, over lean 56.4%), v6 13.10 (+6.39, 76.4%),
v6.1 12.65 (+5.51, 63.6%), line 11.39 (+4.45). **Lopsided / FBS-vs-FCS subset: n = 1** (Boston College at Notre Dame,
closing home spread -29.5; v6 MAE 5.01, v6.1 4.24, line 3.50) and **FBS-vs-FCS n = 0**, so the subset says nothing.

Cohort B (52 events with a settled flat-tier `cfb-structural-v5` total row; 3 more flat total rows for completed games were
still open in the ledger at run time and are not in cohort B; prices are the ledger's ESPN-consensus price on the ledger side
and 1.0476 minus it on the other side, a 4.76% vig, flat 1 unit, percentages only):

| cohort B | picks | W-L-P | PnL | ROI |
|---|---|---|---|---|
| v5 (ledger rows' own results) | 52 | 18-34-0 | -16.77 U | -32.2% |
| v6 | 52 | 19-33-0 | -15.27 U | -29.4% |
| v6.1 | 52 | 20-32-0 | -13.43 U | -25.8% |
| v5, 2-8% edge view | 20 | 5-15-0 | -10.11 U | -50.5% |
| v6, 2-8% edge view | 20 | 8-12-0 | -4.66 U | -23.3% |
| v6.1, 2-8% edge view | 19 | 7-12-0 | -5.44 U | -28.7% |

v6 and v6.1 chose different sides on 1 of the 52 rows. Cohort B MAE on its rows: v5 12.29, v6 12.82, v6.1 12.32, line 11.21;
v6.1 minus line +1.11 [+0.10, +2.21]. Every variant lost money on 11-14 at these prices; one Saturday validates nothing.
The seam check passed: a fresh v6.1 instance with offset 0 and a fresh v6 instance gave exactly equal over, under and push
probabilities on all 52 cohort-B rows and all 117 cohort-A rows.

**Lopsided / FBS-vs-FCS pre-registration.** `outputs/ncaaf/cfb_lopsided_prereg.json`, sha256
`45dbd546543e345559d39422ef77d17b7d8721302c5bc314882f89f2deff4d22`, registered_at_utc 2026-11-15T06:28:59Z, committed in
`8ebdc514`. Tilt = closing home spread of 28 points or more, or exactly one side FBS; predicted direction actual minus
closing total above 0, expected size +1.5 points; primary metric mean(actual - closing total) with a one-sided date-clustered
bootstrap lower bound above 0; minimum n 704 qualifying games with kickoff strictly after the registration time (alpha
0.05, power 0.8, residual SD 16.0). About 100 to 180 qualifying lined games a season, so the decision is no earlier than
about 2030 and more likely later. Evaluate with `python scripts/cfb_lopsided_prereg.py evaluate` (prints INSUFFICIENT N
below 704 and never reads a game at or before the registration time); `verify` re-checks the hash. The evaluator was not
run. The 55.2% over on 972 games before registration is cited only as the origin of the hypothesis.

**Task 2 (src port and forward capture), gate OPEN.** The runs.db gate (no daily row in status `started`; the newest daily
row is not the gate) was open before each edit batch and before the commit. `src/model_prediction/models/cfb_structural_v61.py`
subclasses the v6 model overriding `calibrate_means` only; the tracker window, buffer and fallback come from the hash-verified
`config/models/cfb-structural-v6.1.json` (built from the frozen spec; `base_artifact_hash` ties it to the v6 artifact). It is
bit-exact: 200 of 200 fixture rows and all 14,639 research rows match the research tracker with maximum absolute difference
0.0 (offsets, games averaged, and the floored served total). It is inert (no reference from `learned_forward.py`,
`config/production.yaml` or `config/model.yaml`; a clean-subprocess probe confirms serving never loads it). Three mutations
each made tests fail (buffer 5 h to 0, window 90 to 89 days, fallback minimum 50 to 40); the fallback mutation also failed the
fixture parity test, and a synthetic 49/50 boundary test pins it independently.
`src/model_prediction/data_sources/espn_cfb_pregame_capture.py` is the fail-soft forward capture (append-only JSONL under the
runtime root via `RuntimePaths`, `observed_at_utc` read after each response, records at or after kickoff dropped, prices
stored only as implied probabilities, hard request, time and event budgets). The depth-chart probe found no QB slot (core
`depthcharts` answers 400 "not supported for football|college-football", site `depthchart` returns `{}`), so the record says
`qb_depth: endpoint_unavailable`; the league injury feed holds 3 stale entries. A live run into a scratch root wrote one real
record. `tests/test_cfb_structural_v6.py`'s v6-inertness scan lists the v6.1 module as the one allowed v6 subclass (a one-hunk
edit). **The capture is not wired into the daily run:** the call-site rule required the sibling's `espn.py` commit, and its
actual commits (`403df5f3`, `fbe7cb6c`, `3b9728fc`) did not touch `espn.py` or `daily.py`, so `daily.py` was left alone (it
has no uncommitted hunks) and the call site is a documented follow-up in `docs/ROADMAP.md`.

**What remains.** (1) Wire `capture_cfb_pregame_snapshots` into the daily run behind the NCAAF switch (try/except, a new
step key) and let it accumulate a season of timestamped pregame lines, injuries and, once a source exists, QB depth; only then
can W2, QB and injury sources be retested point in time. (2) The ESPN open feed starts 2023-10-17, so the pinned W2 test is
untestable on the select window; a different window split is a new decision, not made here. (3) The lopsided tilt waits for 704
forward games. (4) The four pre-existing non-verifying artifacts in `tests/test_artifact_hash_verification.py`
(`nba-moneyline-v8`, `nba-spread-margin-v8`, `nba-total-margin-v8`, `tennis-games-markov-v3`) are untouched and still fail that
test. No promotion decision. v6.1 is not routed.

## 2026-11-15 — NFL: the paper buyer bought from Flat what Main had declined; placeholder prices inflated the record (quick-261115-21e)

- **Symptom.** Pre-kickoff review of the 2026-11-15 slate: three NFL rows were buy candidates although
  Main had rejected them -- SEA at LV spread and total (`input_qualified` false) and NE at DET over
  49.5 (neutral site, no pregame weather).
- **Cause.** `AutoPolymarketBuyer.evaluate_and_execute` merges Main and Flat and, since the
  2026-11-11 "tags name destinations" change, rejects only `decision != CALL` and `PAPER_CALL_*`.
  The dashboard pick readers it consumes do not project `feature_payload_json`, so the input flags the
  forecast writes never reached the buyer.
- **Fix.** `_load_pick_input_flags` reads the flags from the canonical ledger by pick id (the stored
  values are the strings "True"/"False"); `_has_unqualified_inputs` rejects explicit false, weather for
  totals only, and fails closed for NFL spread/total when the ledger cannot be read. Regression test
  `tests/test_auto_buyer_input_gate.py`, shown to fail with the gate removed.
- **Second finding.** 210 settled Main rows carry sportsbook `model_opinion_no_executable_quote`: no
  quote matched, the row was priced at the 52.38% lay, and favourites were paid as coin flips (+47u
  in total). `dashboard.picks.performance` now excludes them from the record and reports the count.
- **Reproduction.** Row-level NFL loss tables: ledger `ledger_records`, `sport='nfl'`,
  `status='settled'`, de-duplicated on (event, market, selection, line, model) preferring Main, split
  on `market_quote_provenance`. v8 and total-refit numbers: `docs/handoffs/HANDOFF.md` sections 4, 8.

## 2026-11-15 — NCAAF: v6 open items: ingest groups 80/81, measured v5 history backfill, known-team gate, checkout-proof pin (quick-261115-0pq)

**Bottom line.** The operator approved fixing the four open items from the v6 entry below. Two shipped (the known-team
gate and the checkout-proof lib pin). The live-history backfill and the NCAAF `groups=80/81` ingest were **measured first
and rejected by a rule written down before the numbers existed**, so neither shipped and the live history files are
byte-identical to their start state.

### B: measured impact of completing the live history on v5 (verdict REJECT)
`scripts/cfb_v5_history_impact.py` carries its acceptance rule as the constant `RULE` (id `cfb-v5-history-impact-rule-1`,
sha256 `f780ad86a2b3ab378382d873cdbd02455b5b5900c4c46e721726d3c6c2f5a236`). The script was committed (`403df5f3`, blob
`6010d8af8285`) before its first run and never changed afterwards; the report records the same blob sha. The rule: replay the
v5 serving artifact point-in-time on the same 1,527 locked-holdout event ids (2024-11-15 to 2026-11-12) with arm (i) the live
history and arm (ii) the live history plus the recorded append set; ACCEPT only if the Brier delta (ii minus i, date-clustered
bootstrap point estimate, 2,000 resamples) is at most +0.001 and the margin and total Gaussian log-score deltas are each at
least -0.005. Arm (i) must first reproduce the recorded v5 numbers.

Arm (i) reproduces: n 1,527, Brier 0.18933, margin log score -4.21265, total log score -4.21155, and the 575/576
reference-line gate passes on every row (corr -0.0328 / +0.0090, side records 218-257 and 264-233, SDs and MAEs within tolerance).

| holdout (1,527 events) | arm (i) live | arm (ii) live + append | delta (ii - i) | 95% CI |
|---|---|---|---|---|
| Brier | 0.18933 | 0.19040 | **+0.001065** | [-0.000815, +0.002998] |
| margin log score | -4.21265 | -4.20970 | +0.002951 | [-0.004547, +0.010515] |
| total log score | -4.21155 | -4.20731 | +0.004240 | [-0.001014, +0.009189] |
| log loss (informational) | 0.557265 | 0.559138 | +0.001873 | |
| accuracy (informational) | 71.19% | 70.14% | -1.05 points | |

The Brier delta is 0.000065 over the pinned limit, so the verdict is **REJECT**, from the rule as written. The log scores
improve and the Brier interval includes zero, so this is a marginal call, not a finding that the extra games are bad: all
1,527 holdout probabilities move (max 24.5 points of home-win probability) and accuracy drops 1.05 points. 575/576
reference lines, informational only, 1,133 of 1,151 projection pairs change: totals corr -0.0328 [-0.119, +0.041] to +0.0005
[-0.075, +0.071] (side 218-257 to 209-242), spreads +0.0090 [-0.090, +0.105] to +0.0113 [-0.081, +0.108] (side 264-233 to
258-247); spread MAE 14.64 to 14.17 points. The champion's fitted calibration and SD table were fit on the gappy history, so
feeding it a complete one moves its inputs away from its fit; shipping that would be an unvalidated change to the serving
champion's inputs.

Append set (recorded, not applied): 1,478 events, sha256 `7d0f9cafff801ed758b7f20d913db28176a1a63466be8aa1adf072583da80cbb`,
FBS-FCS 1,101, FBS-FBS 376, FCS-FCS 1 (the one FCS-FCS event is listed in both feeds), by season 2016 154, 2017 137, 2018 134,
2019 146, 2020 50, 2021 127, 2022 128, 2023 135, 2024 128, 2025 148, 2026 191. The plan expected about 1,513: the live files
grew from 7,965 to 8,014 rows between planning and the run, and 35 of those 49 new rows are FBS-FBS events that were in the
backfill.

Live files: `data/processed/ncaaf/games.jsonl` and `data/historical/ncaaf_games_all.jsonl` are 8,014 rows and 5,510,866 bytes,
sha256 `a934dd88dc035d800eb2d4fba8211e470d1fb92fa24310bf2b9fceb1f3cc230f`, identical at the measurement and at the final
check. Nothing was appended, so no backup was needed and `scripts/cfb_backfill_live_history.py` was not written.

### A: ingest groups 80/81 (not shipped)
Not shipped, by the plan's own coupling rule: the go-forward ingest would shift v5's inputs the same way the rejected
backfill does, leaving a history that is gappy before a date and complete after it. `ESPNClient.scoreboard`, `ingest.py`
and `tests/test_ingest.py` are unchanged. The design is kept for a later decision: a new `scoreboard_ncaaf_divisions`
client method (groups 80 then 81, limit 500, merged by event id) used only by the NCAAF ingest branch, appending only
FBS-involved non-forfeit rows. Re-opening it needs either a v5 refit on the complete history (validation-split, not a
decision on this holdout) or an operator waiver of the rule.

### C: v6 known-team gate (shipped)
`TeamResolver.is_known(name)` is true for a history name or one `resolve_team` can alias; `project()` raises
`NO_CALL_CFB_V6_UNRATED_TEAM` for any other name before `book.identity`, instead of letting an unknown name pool into the
season's shared lower-division identity. The frozen math, `RatingBook._key`, the solve and `scripts/cfb_v6_lib.py` are
untouched. Against the real resolver on the frozen snapshot an invented name is not known and a real or aliased one is.
Parity over every cached row: 14,822 rows, 14,639 projections, max abs difference 0.0, 183 of 183 cached no-calls matching,
**0 newly declined**, frozen params sha256 unchanged. Tests (three, in `tests/test_cfb_structural_v6.py`): an invented name
declines, a real pooled lower-division team from history still projects, an aliased unseen name still projects.

### D: checkout-proof lib pin (shipped)
Deviation from the brief's wording. `canonical_params` hashes every frozen-fit key except `params_sha256` and
`generated_at_utc`, and `code.cfb_v6_lib_sha256` is one of those keys, so re-pinning the lib hash inside
`outputs/ncaaf/cfb_v6_frozen_fit.json` would change the frozen params sha256 (forbidden) and break the test that pins it. The
frozen-fit file stays byte-identical to HEAD; `verify_frozen` instead hashes the lib after normalising line endings to CRLF
(`lib_sha256_crlf`), and CRLF-normalising either the CRLF working copy or the LF blob gives the existing pin `fe42c288...`.
`cmd_parity` now reports rows the src extractor declines but the research cache projected as `newly_declined` and fails on
any. Proof: verify-frozen passes on the CRLF working file and on the LF blob from `git show HEAD:scripts/cfb_v6_lib.py`; a
one-byte edit of that blob fails (negative control); `cfb_v6_market_check.py v6 --check-only`, the residual and pool-audit
callers all return `6e38b14ca486...`.

### Prove-it-fails
Each C test was proven by a mutation of `cfb_features_v6.py`, the file restored and its sha256 compared equal: the gate
deleted (`if False`) fails the invented-name test; an over-broad gate that also declines pooled identities fails the real
pooled team test; dropping the `resolve_team` fallback from `is_known` fails the aliased-name test. The impact script's
arm (i) reproduction gate is its own control: it must match the recorded v5 numbers before any verdict other than STOP. The
C tests were written after the gate rather than RED-first. `ruff check src/ tests/` clean; `tests/test_ingest.py` and
`tests/test_cfb_structural_v6.py` give 32 passed in a detached worktree at `C:/wt0pq` on the C commit.

No promotion. v6 is not routed; v5 parameters and gates unchanged.

## 2026-11-15 — NCAAF: cfb-structural-v6 shadow challenger and market-relative check (quick-261114-srb)

**Bottom line.** v6 is a better-calibrated probability model than v5 on the locked holdout, and it shows **no
market-relative edge**. Nothing was promoted, routed or wired: v5 stays the NCAAF champion, v6 is a research
artifact that no serving module imports. The decision for the operator is at the end of this entry.

### Why (the 2026-11-14 loss review of v5)

Over the 575 lined 2026 games the v5 projections carry no skill against the market line, and are compressed:

- **Totals:** corr(model - line, actual - line) -0.033 (95% CI [-0.119, +0.041]); model side record 218-257
  (it takes the wrong side more often than not); model total SD 4.13 against a line SD 5.71 and an actual SD 16.76.
- **Spreads:** corr +0.009 (CI [-0.090, +0.105]); side 264-233; MAE 14.64 against 11.65 for the line;
  margin SD 11.78 (model) / 16.35 (line) / 22.37 (actual); on 117 lines of 21+ the model's mean favourite margin
  is 16.64 against a line of 34.22 and an actual of 33.84, and it takes the underdog 107 times.
- The v6 harness regenerates these from the ledger DB and the v5 artifact and refuses to continue if it cannot
  reproduce them (gate passed).

### Five projection defects, and what v6 does instead

1. Matchup was an averaged half-sum of an offence number and a defence number; v6 is additive: league average
   + offence deviation + opponent defence deviation + home term (offence +10 vs defence +8 over 28 projects 46,
   not the half-sum 37; pinned by a test shown to fail when the half-sum is substituted).
2. Opponent strength entered through conference-tier offsets and an Elo channel; v6 solves every rated team
   jointly (weighted ridge, one solve per distinct eligible-game set), so each opponent enters through its own
   rating. No conference table or team list exists in v6. Pooled pseudo-team per season for lower-division
   singletons.
3. Pace: no drive or play data exists (see audit below); v6 carries tempo through the offence and defence
   points ratings instead of a constant.
4. Fixed total baselines (49.5 / 54.0) are replaced by the rolling league scoring average `mu` solved with the
   ratings.
5. v5 fitted its mean calibration and per-slice margin SDs, but on the v4 projection, with one total SD and
   hand-set line-dependent widening and conference scale carried over. v6 refits the calibration on its own
   projection, fits per-slice outcome-residual SDs for BOTH margin and total (established / thin sample /
   FBS-vs-FCS) on train only, chooses on validation, and drops the line-dependent widening and the conference scale.

A game feeds the ratings only if kickoff + 5 hours is at or before the decision time (stricter than v5's
`start < observed`, which admits a game in progress). v4/v5's hand-set altitude, travel, bye, weather and QB
adjustments are not carried into v6; their raw inputs (wind, temperature, precipitation, QB starter probability)
are recorded verbatim in the v6 feature payload (None stays None) so the audit is possible going forward.

### The history gap (item 6)

The existing history (7,965 events, sha256 `659e33b21de95e02633251c5614df3b63f9fea62898a140bd7efaa83e525ecb7`)
misses almost every game that involves a lower-division team. ESPN scoreboard with groups=80 and groups=81
(read-only, 440 requests, 0 failures, all 11 seasons 2016-2026) lists 16,378 completed events; all 7,965
existing events are in it with **0 score disagreements**.

- **FBS-vs-FCS:** the history holds 6-12 games a season against 98-126 on ESPN (2020: 2 of 34), so it misses roughly
  90-95% in every season (2016: 10 of 113; 2025: 6 of 126).
- **FCS-vs-FCS:** 0-16 a season against 653-752 (2020: 0 of 24), so it misses 98-100%.
  (`ESPNClient.scoreboard` sends no `groups` parameter for NCAAF, the likely cause.)
- **2026:** 878 of 1,384 completed games are missing (226 of 731 FBS-involved). FBS teams are missing a mean
  2.46 games each (median 2, max 7), which confirms "about 2 per team". Week 6 is missing entirely
  (108 of 108 games, 58 FBS-involved). Week 11 is mostly ingest lag (93 of 104, 35 of 46 FBS-involved).
  The "16 of 65 on 2026-09-26" claim is confirmed by the US-Eastern date (65 FBS-involved games, 16 missing).
- **Backfill:** 16,378 rows, sha256 `cc704059e4684edb9e580dff5033433424ee34e6a5fe96995c7c272ae82d8bf4`, parser
  `cfb-v6-backfill-1`, under `data/research/ncaaf_v6/` (gitignored). The live history files were only read.
  v6 uses the backfill as rating input; scored games are the existing-history events (D-FIT-COHORT), the
  backfill-only FBS-involved games are scored separately.
- Consequence for v5: its live ratings are built from this gap-ridden history. Fixing ingest is an open item
  below, not done here.

### Input audit (weather, QB, pace)

- **Weather is plumbed with real forecasts but fires on 0.4% of rows at most 1.8 points and was never fitted.**
  Across 6,129 NCAAF ledger rows (607 events) the adjustment is non-zero on 25 rows (0.4%; 0.9% of the 2,829 rows
  that carried a real forecast), on 6 events, largest 1.8 total points. Forecast capture began 2026-10-23.
  Training history carries constant placeholders (5 mph / 65 F on 7,542 rows, 0 / 72 on 182), so the wind,
  precipitation and cold coefficients are hand-set literals that were never fitted or validated.
- **QB adjustment is dead:** the -4.5 point backup discount fired on 0 of 2,430 recorded side cells; the
  injury feed holds 387 entries over 129 snapshots with no QB entries; the "starter" is the season passing leader
  (not a point-in-time starter) and a starter missing from the report is scored as available (1.0).
- **No pace data:** no possession or drive fields exist; `possessions` is the constant 12.5 on all 7,724 legacy rows
  (absent on the 241 newest), success rate the constant 0.42, `home_epa_per_play` has no recorded provenance
  (339 distinct values, absent on the 241 newest).

### ESPN historical closing-line dataset

9,364 lined events (FBS-FBS and FBS-FCS completed games 2016-2026, forfeits excluded), sha256
`2b19a2545fb2e5f9df4c55a1b57ee9f88b11eab371828a4a7ed88e8bdfb7fa54`, selection rule `espn-lines-rule-1`
(median across non-live providers, fixed before any outcome was joined; rule sha256
`ee23e34e91544b797a1feaa9c6ab604fa79597671f6bec110dd34df4b806ee97`). These are **closing/final pregame lines
with no per-quote `observed_at`: a closing-line benchmark, not decision-time quotes.** From 2024 each event carries
one book (ESPN BET 2024-25, DraftKings 2025-26).

### v6 frozen fit and holdout

Frozen before the holdout or any market check ran: params sha256
`6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70` (`outputs/ncaaf/cfb_v6_frozen_fit.json`).
Selected ridge 0.1, previous-season weight 0.2, established threshold 2 current-season games, result buffer 5 h.
Margin calibration 0.4044 + 0.8716 x raw margin; total calibration 21.3898 + 0.6097 x raw total. Margin SD
16.35 established / 16.89 thin sample / 16.76 FBS-vs-FCS; total SD 16.86 / 16.72 / 17.43. Split: train 4,102 events
to 2022-11-11, validation 1,591 to 2024-11-14, holdout after that; 2016 is burn-in (734 existing games excluded).
Caveat: the grid was extended downward twice on validation before freezing and the chosen ridge 0.1 is the
smallest value in the final grid (an edge selection).

Locked holdout, common cohort n 1,527 (2024-11-15 to 2026-11-12), scored once; v5 incumbent check: analytic Brier
0.19003 against the recorded engine 0.18933 (n 1,527):

| metric | v6 | v5 | v6 minus v5 (date-clustered 95% CI) |
|---|---|---|---|
| moneyline Brier | 0.17975 | 0.19003 | -0.01029 [-0.01524, -0.00553] |
| log loss | 0.5333 | 0.5589 | |
| ECE | 0.0225 | 0.0448 | |
| calibration slope | 1.121 | 1.034 | |
| margin log score | -4.1630 | -4.2109 | +0.0479 [+0.0306, +0.0640] |
| total log score | -4.1966 | -4.2115 | +0.0150 [+0.0062, +0.0240] |
| margin MAE | 12.286 | 12.885 | -0.599 [-0.847, -0.332] |

v6's Brier is lower in each of 2024 (n 233), 2025 (n 793) and 2026 (n 501) and in every SD slice (established, thin sample, FBS-vs-FCS). Backfill-only FBS-involved
games (n 384, scored separately): Brier 0.1046, ECE 0.0629, margin MAE 14.54.

### Market-relative checks (FIRST LOOK, frozen fit `6e38b14c...`)

Correlation of (model - line) with (actual - line); a positive value is the evidence of edge, and about zero means none.

| set | totals v6 | totals v5 | spreads v6 | spreads v5 |
|---|---|---|---|---|
| 575-game reference (design-informed, inside the holdout window) | -0.011 [-0.091, +0.054] | -0.033 [-0.119, +0.041] | +0.042 [-0.023, +0.110] | +0.009 [-0.090, +0.105] |
| ESPN lines, holdout window, common set | -0.008 [-0.057, +0.041] (n 1,511) | -0.040 [-0.097, +0.016] | +0.019 [-0.027, +0.066] (n 1,512) | +0.019 [-0.032, +0.068] |
| ESPN lines, fit window, common set | +0.028 [-0.001, +0.058] (n 5,567) | +0.023 [-0.006, +0.051] | -0.009 [-0.041, +0.022] (n 5,574) | +0.004 [-0.024, +0.033] |

Every interval includes zero. Over all 8,279 lined totals v6 is +0.010 [-0.015, +0.034]; over all 8,376 lined spreads
-0.007 [-0.032, +0.018]. On the 575 reference v6 fixes much of the spread compression (margin SD 14.82 against v5's
11.78 and a line of 16.35; MAE 12.56 against 14.64; the 21+ favourite margin 28.74 against 16.64) and does not fix
the total (model SD 5.04 against a line of 5.71), yet side records are 232-256 on totals and 237-246 on spreads.
Fixing compression was necessary, not sufficient, and it did not buy edge.

Caveats, all binding: (1) the ESPN lines are closing lines, so a model with no edge against the close can still
differ from a decision-time quote; (2) single book from 2024 (the fit window mixes providers); (3) fit-window games
fitted both models' calibration, so the holdout window is the clean test; (4) the 575 reference and its 2026 season
sit inside the holdout window and were used to motivate v6, so they are not independent evidence; (5) against the
575 reference the pinned ESPN spreads show 13 sign flips of 576 (inferred to be reference-side line-pair repeats,
not verified); (6) per-season cuts (totals 2024 +0.095 [+0.005, +0.176], 2026 -0.089 [-0.170, -0.013]) are looks
among many and not findings; (7) the moneyline Brier gain is against v5's own calibration, not against market
prices, and no moneyline market comparison was made.

### Item 7 (market-anchored residual): not done, now feasible

It was blocked locally (the only lined games were the 575). The ESPN lines now make it feasible (9,364 events, 8,279
totals and 8,376 spreads covered by v6). Recorded as a follow-up; nothing was fitted on any line.

### Port into src, parity and inertness

`features/cfb_features_v6.py` and `models/cfb_structural_v6.py` carry the frozen math (copied from the research
copy `scripts/cfb_v6_lib.py`, kept as the historical copy that the frozen-fit pin covers). `cfb_v6_research.py
parity` recomputes the cache through the src extractor: **14,639 projections, max abs difference 0.0**, 183 no-call
rows matching (the simulated moneyline probability differs from the analytic Normal one by at most 0.035 on 50
holdout rows, mean 0.017; informational). `config/models/cfb-structural-v6.json` (qualification `research`, not
qualified, no gates block) verifies its own artifact hash. `tests/test_cfb_structural_v6.py` pins the additive form,
point-in-time, opponent rating, rolling baseline, raw weather/QB, fail-closed, loader, artifact and, in a clean
subprocess, that learned_forward, college_football and cli.daily never load either v6 module and that
production.yaml and model.yaml do not name it. Design call and RISK to know before anyone routes v6: a team name never seen in the history resolves to the season's
pooled lower-division pseudo-team and is projected, not declined (checked on the real snapshot: an invented home team
against Ohio State projects a raw margin of -90.9 with 9 sample games, no NO_CALL). `NO_CALL_CFB_V6_UNRATED_TEAM`
fires for a rated name with no game in the window and for any name when no pooled team exists. The frozen math is
unchanged (parity is bit-exact); any serving use would need a known-team gate first.

### Decision for the operator

No promotion decision was made and v6 is not routed. Options: (a) leave v6 shadow-only (nothing here shows edge,
so there is no case to route it); (b) run item 7 on the ESPN lines (a market-anchored residual, with the closing-line
caveat), the only route left to a market-relative signal from these inputs; (c) if better-calibrated probabilities
are wanted on their own, a separate governed promotion with a market-relative test on decision-time quotes.

Open items: the daily ingest still lacks `groups=80/81` (v5's live ratings stay on the gap-ridden history); the
ESPN lines and backfill live under `data/research/ncaaf_v6/` (untracked); QB point-in-time starter data does not
exist; there is no drive or play data for pace.

## 2026-11-14 — Project audit: the forecast step's time was ledger re-reads and replayed simulations, not pricing; pytest wrote into the live runtime root (quick-261114-prf)

**Symptom.** The 14:00 daily spent 1,589 s of 1,886 s in the forecast step: NCAAF 1,567 s,
NCAAB 702 s (`timing.forecast_sport_done_seconds`). Separately, the live
`logs/auto_buyer_cycle_history.jsonl` held 64 rows marked `LIVE_EXECUTION` with
`forecast_date` 2026-08-30 (every one evaluated 5 picks and placed no order), written 2026-10-24
to 2026-11-13. Other `LIVE_EXECUTION` rows with no forecast date are real runs (12,593 picks
evaluated, 147.78 USD placed on 2026-11-10 and 11-11) and are not test output.

**Root causes.**

1. `PickLedger._append_record` read the ledger's whole tier/sport slice for one duplicate check,
   on every append. Timed read-only against the live `ledgers.db` (1.25 GB, 50,949 records):
   flat/ncaaf 4,216 rows 1.9 s, flat/ncaab 8,442 rows 2.5 s, main/ncaab 4,624 rows 1.4 s. A
   contract is appended to two tiers, a slate is hundreds of contracts, and the slice grows every
   day (82% of flat/ncaaf is retired rows that are read, parsed and then dropped). The XLSX
   rebuild was already deferred (`defer_sqlite_xlsx_exports`); the read that fed it was not.
2. `record_from_pick_request` read the model's whole workbook twice (dedupe, then append) and
   rewrote it once per appended pick, and read it again when the same pick arrived from the
   second tier: 0.4 s per read at 1,000 rows.
3. `cfb_replay.replay_inputs` restores the recorded RNG state, so its simulation is a pure
   function of its inputs, but it was re-run every time: once per listed ladder rung in the
   slate build (about 3,600 replays, 93% of a 128 s slate build by stack sample), twice in
   `build_snapshot` (build, then validate), once in `PickRequest.validate`, once per ledger
   write. One simulation is 0.033 s; nothing was slow, it was only repeated.
4. `auto_executor`, `auto_buyer_ledger` and `polymarket_execute` call `RuntimePaths.resolve()` at
   import. Under pytest that reached the live runtime root by three routes: the variable is set
   machine-wide and `tests/conftest.py` cleared it only in an autouse fixture, after collection
   had imported those modules; `RuntimePaths.resolve()` falls back to the repo `.env`, which
   names the live root; and `polymarket_execute`, `auto_executor` and `dashboard/common.py` each
   carried a copy of a loader that puts every `.env` line, live Polymarket credentials
   included, into `os.environ` at import. Their path constants therefore pointed at the live
   runtime root for the whole session, and `test_auto_buyer_cycle_tracking` (live mode, mocked
   executor) patched `DATA` and the state file but not `AUTO_BUYER_CYCLE_HISTORY_PATH`.
   `test_buyer_fails_closed_when_stale_quote_refresh_has_network_error` exposed the second
   route: it builds a default buyer, which read the live state file's one-hour lookahead and
   failed or passed depending on the checkout it ran in. The live buyer ledger, audit log, state file and the
   paper root (`paper_auto_buyer/`: ledger, history, state) were scanned read-only for test
   identities and hold none; the pollution is the main cycle history only.

**Fix.**

- `RuntimeLedgerStore.records/pick_rows` take `event_id` (served by `idx_ledger_records_event`);
  `_append_record` reads only the event's rows while the export is deferred and falls back to
  the full read otherwise, including when the deferral ends between the check and the write.
- `ModelLedger._read_unlocked` serves a copy from a cache keyed on (mtime, size, inode); a write
  drops the entry. Rows are cached from real reads only: the workbook round trip is not an
  identity.
- `cfb_replay._MemoizedReplayEngine` caches the simulated score arrays on (engine parameters, RNG
  state, score means); the lines only threshold them. It subclasses the engine in `cfb_replay.py`
  because `models/cfb_distribution.py` must not change: its sha256 is `engine_source_sha256` in
  every stored snapshot, and editing it would fail replay validation for every open NCAAF pick.
- `tests/conftest.py` drops the env var and sets `MODEL_PREDICTION_SKIP_DOTENV=1` at import,
  before any `model_prediction` import. `runtime_paths.dotenv_disabled()` reads the flag:
  `RuntimePaths.resolve()` skips its `.env` fallback, and the three duplicated loaders are
  replaced by one `runtime_paths.load_dotenv_into_environ()` that honours it. Nothing in
  production sets the flag; with it unset the modules still load the runtime root and the
  credentials from `.env` (checked). The cycle-tracking test patches the history path. A first
  version that only popped the variable passed in a worktree with no `.env` and did nothing in
  the real checkout; the isolation test now asserts the paths are the repo default, not merely
  different from the shell's value.
- Three stale pins the 2026-11-14 triage missed, failing at HEAD: two buyer tests relied on
  `nba-moneyline-v9` being in the default whitelist (removed 2026-11-12); one model-ledger test
  asserted tennis spread is unmapped (it maps to `tennis-games-markov-v4`) and that an unmapped
  market is a no-op (it falls back to the model version).

**Evidence.**

- Targeted read vs full read on a `backup()` copy of the live ledger, through `PickLedger`:
  2,651 events, 7,147 rows, nine tier/sport slices, 0 row-set mismatches, 0 duplicate-verdict
  differences; 0.6-5.6 ms per event against 0.14-1.84 s per slice.
- Replay memo on a 35-rung ladder at 200,000 draws: 1.08 s cold, 0.16 s first pass, 0.04 s
  repeat, probabilities identical.
- Each new test fails with its fix reverted and passes with it restored
  (`test_deferred_append_reads_only_the_events_own_rows`,
  `test_rows_cache_sees_external_writes_and_is_not_mutable_by_callers`,
  `test_memoized_replay_equals_a_cold_simulation`, `tests/test_runtime_root_test_isolation.py`).
- The profiling run of `build_cfb_slate` (no ledgers passed) wrote one candidate-evidence packet,
  `research/college_contract_candidates/ncaaf/2026-11-14/c1669b1e...json` at 15:38 ET. It is a
  real observation by production code, outside the scheduled runs.

**Not done here (see ROADMAP Horizon 3 item 6).** The 52 polluted history rows are still in the
live file: identify them by `mode == "LIVE_EXECUTION"` and `forecast_date == "2026-08-30"`. The
per-model workbook is still rewritten once per appended pick.

## 2026-11-14 — Event-detail quote pass ahead of the paced /book capture (quick-261114-mzz)

**Symptom.** The 21:00Z daily (`daily-20261114T210002Z-126544`, commit 87d41d45) captured 203
contracts and left 955 `skipped_after_time_budget` at its 600 s cap (NCAAF 322, NCAAB 56). The
paced `/book` pass that fixed the abandon-on-429 defect (entry below) is correct but slow: it
sustains about 20 contracts a minute on the IP-wide limit, so a full college slate (about 1,500
NCAAB and 1,900 NCAAF contracts) cannot fit in 600 s.

**Evidence.** `GET /v1/events/slug/{slug}` returned both top-of-book quotes on 525 of 530 slate
markets, matched live `/bbo` exactly on 26 of 27 spot checks, and served 40 requests in 1.1 s with
no 429. Observed while planning (read-only GETs, 21:36Z): the detail market `status` is
`MARKET_STATUS_OPEN` (the readers gate on `MARKET_STATE_OPEN`), with `active true`, `closed false`,
`ep3Status "OPEN"`; the response is CDN-cached (`max-age=30`, an `Age` header); `/bbo` has no
`transactTime` either and is CDN-cached the same way, so a `/bbo` row already has
`transact_time_utc None` and no sizes; the detail `updatedAt` was 07:25Z on a 21:36Z read, so it is
market-metadata time and is never used as a quote time.

**Design** (`data_sources/polymarket_us.py`, `capture_slate_snapshots`).

- Pass 1 (event detail): one request per upcoming in-scope event (priority leagues first, then
  other leagues' events that hold an open-pick slug), spaced 0.25 s
  (`EVENT_DETAIL_MIN_REQUEST_INTERVAL_SECONDS`), inside `max_capture_seconds`. For every slate
  market of the event it writes a row with `reconstruction_status_source`
  `live_gateway_event_detail_quote`, `usage` `prospective_executable_bbo`, `market_state`
  `MARKET_STATE_OPEN`, `bid_size`/`ask_size`/`transact_time_utc` None, `observed_at_utc` stamped
  after the response arrives, and `quote_cache_age_seconds` from the `Age` header. Rows pass through
  the same `_annotate_capture` as `/book` rows, so they carry the identical field set.
- Fail closed per market (`_event_detail_quote_book`): status open, active, not closed,
  `ep3Status` absent or `OPEN`, finite `0 < bid < ask < 1`, and the line equal to the slate's when
  both exist; anything else writes no row and is fetched by pass 2. `market_state` is mapped to
  `MARKET_STATE_OPEN` only from the verified open status, so an event-detail row is exactly as
  executable as a `/bbo` row, never looser.
- Fail soft per event: an HTTP, parse or type error counts in `event_detail_failures` and the pass
  continues; a 429 ends the pass (the limit is IP-wide and pass 2 needs the budget). Ladder pruning
  does not apply to pass 1 (every rung is in the same response); it still applies to pass 2.
- Pass 2 (`/book`) keeps its contract set, pacer and budget. Each tier is now a stable partition,
  contracts with no event-detail quote first, then the quoted ones, so the paced budget reaches
  unquoted markets before it refreshes the ladder shortlist for depth. A later `/book` row wins
  over an earlier event-detail row in `_latest_capture_per_market`, `_fresh_latest_per_slug` and
  `PolymarketSnapshotStore.latest` (pinned by a test).

**Decisions.**

- `captured`, `captured_by_league`, `missing_executable_ask`, `timestamp_invalid_count` and
  `nba_missing_quote_event_ids` count rows written by both passes, because consumers read
  `captured` as snapshots persisted (`ncaab_paper_trial` sums it); `captured_by_source` gives the
  split. New keys only: `event_detail_events`, `event_detail_captured`, `event_detail_failures`,
  `captured_by_source`, `covered_book_failures`; none removed or renamed.
- A `/book` failure or skip on a market pass 1 already quoted is `covered_book_failures`, not
  `failures`: `failures`, `skipped_after_time_budget` and `skipped_after_rate_limit` keep meaning
  "contracts left with no snapshot from this run". `event_detail_failures` is never added to
  `failures`, since those markets fall through to pass 2.
- A client without `event_detail` (test stubs, legacy) captures exactly as before.

**Reader audit.** The only code naming a `reconstruction_status_source` outside this module is
`research_quote_contracts.py:68`, which requires `live_gateway_market_and_book_response` and so
already rejects `/bbo` rows; there is no `/bbo` allow-list to extend and it was not touched. The
executable-quote gates read `market_state == "MARKET_STATE_OPEN"`, `provider`, `reconstructed`,
`timestamp_valid` and `usage`, all of which event-detail rows satisfy. `college_contract_evidence`
also needs a parseable `transact_time_utc`, so it rejects `/bbo` and event-detail rows alike; the
`/book` rows from pass 2 (ladder shortlist) keep serving it. No reader or gate was edited.

**Live proof** (read-only GETs; `cli polymarket-slate --date 2026-11-14`, 21:49Z to 22:04Z, runtime
root unchanged; nothing posted to any order endpoint).

| | events asked | `event_detail_captured` | `captured` | by source (detail / bbo / book) | `elapsed_seconds` | `failures` | rate-limit pauses |
|---|---|---|---|---|---|---|---|
| NCAAB | 34 | 1491 | 1619 | 1491 / 18 / 110 | 367.2 | 0 | 3 |
| NCAAF | 30 | 1910 | 2107 | 1910 / 34 / 163 | 531.6 | 162 | 3 |

`event_detail_failures` 0 and `source_rate_limited` false on both; pass 1 took about 7 s for 34
events. NCAAF `failures` 162 are 160 `capture_time_budget_exhausted` and two `invalid_executable_bbo`;
all 162 are contracts of games already under way (checked against the slate's start times), in
the secondary tier on its 120 s cap, which pass 1 does not cover. Every upcoming-game contract
got a snapshot. Spot checks of
three stored event-detail rows per slate against a live `/bbo` GET: NCAAB 41.0%/42.0%,
79.0%/81.0%, 57.0%/58.0% (bid/ask) equal the live 41.0/42.0, 79.0/81.0, 57.0/58.0; NCAAF
61.0/62.0, 58.0/59.0, 21.0/22.0 equal the live 61.0/62.0, 58.0/59.0, 21.0/22.0. No event-detail
row has `timestamp_valid` false or a missing ask. Compare the 21:00Z daily: 203 captured.
Code: commit e8e2ed47 (8 `test_event_detail_*` tests in `tests/test_polymarket_us.py`).

## 2026-11-14 — Prospective BBO capture abandoned on the first 429 and starved NCAAF; NCAAF priced off ESPN consensus (debug bbo-capture-429-starves-ncaaf)

**Symptom.** Every NCAAF Main row written on 2026-11-14 carried `sportsbook=espn_consensus` and a
blank `market_quote_provenance`. Every daily capture of the day died about a minute in: captured
17 / 2 / 22 / 13 / 7 contracts, `skipped_after_rate_limit` 1754 / 1921 / 1863 / 2052 / 2017,
`source_rate_limited: true` (runs at 05:30Z, 11:00Z, 14:00Z, 17:00Z, 19:00Z). The runtime
archive `odds/ncaaf/2026-11-14` held 143 rows, all from standalone CLI runs, none from a daily.

**Root cause (five defects, compounding).**

1. `capture_slate_snapshots` set a shared `source_rate_limited` flag on the first 429 that survived
   the client's two attempts and skipped the rest of the queue. Four unpaced workers drew that 429
   within about a minute. The 2026-08/10 mitigation (stop the queue) traded 1,700 throttled
   fetches for 2,000 never-attempted ones.
2. The queue was sorted NBA first, then by kickoff across all 15 capture sports, nine of them out
   of production scope since 2026-11-13. Early soccer, tennis and esports games sat ahead of
   every NCAAF game, so the budget was gone before NCAAF was reached.
3. `ladder_keep` was one integer picked first-match in `_polymarket_slate` (`NBA` on the slate
   => 3), so NCAAB (meant 1) and NCAAF (meant 2) priced 3 rungs per event.
4. `daily.py` ran the NFL week-ahead book capture in the same pool as the slate capture (34-73 s
   each, fully overlapping), on the same IP-wide gateway limit.
5. `build_cfb_slate` had no Polymarket fallback between captured books and ESPN consensus, and
   consensus rows carried no quote lineage, so the ledger provenance column was blank.

**Fix.**

- `data_sources/polymarket_us.py`: a shared `_CapturePacer` spaces book requests 0.5 s apart
  across workers. A 429 opens a pause episode (5 s, doubling, Retry-After honored, cap 60 s) that
  every worker waits out, doubles the spacing (cap 4 s, eases back after 20 clean responses) and
  retries the contract (3 attempts). The queue is abandoned only after 5 consecutive episodes.
  Contracts are split into tiers by `_capture_tiers`: upcoming open-pick contracts, then upcoming
  `priority_leagues` contracts, both round-robin across leagues in kickoff order; then a
  secondary tier (out-of-scope leagues and games already under way) on its own
  `secondary_max_seconds`. `max_capture_seconds` caps the whole capture. `ladder_keep` accepts a
  per-league mapping. New result keys: `rate_limit_pauses`, `skipped_after_time_budget`,
  `elapsed_seconds`, `priority_leagues`, `primary_contracts`, `secondary_contracts`,
  `captured_by_league`, `failures_by_league`; existing keys unchanged.
- `cli/commands.py`: `CAPTURE_LADDER_KEEP = {NBA: 3, NCAAF: 2, NCAAB: 1, NFL: 2}` (env
  `POLYMARKET_CAPTURE_LADDER_KEEP` still overrides all). Priority leagues come from config: a
  league with its own `models` entry that `is_sport_daily_enabled` (today NBA, NCAAB, NCAAF, NFL;
  MLB and WNBA rejoin when their season flag flips). Caps: 600 s overall, 120 s secondary (env
  `POLYMARKET_CAPTURE_MAX_SECONDS`, `POLYMARKET_CAPTURE_SECONDARY_SECONDS`).
- `cli/daily.py`: `_serialized_polymarket_captures` runs the NFL week-ahead book capture after
  the slate capture in the same pool slot (also when the slate capture raises); the ESPN
  consensus half stays in the pool.
- `models/college_football.py`: the NCAAF event list is fetched once per slate build (it was
  already fetched for candidate evidence; now hoisted and reused). A market with no captured book
  is matched against the event list with the same `_match_cfb_*` selectors and priced at the
  matched side's `price_probability`: `sportsbook=polymarket_us_indicative`,
  `market_quote_provenance=indicative_slate`, `market_quote_timestamp_valid=False`,
  `market_quote_source=polymarket_us`. A live list fetched more than 10 minutes after the
  decision time is not used (point-in-time). ESPN consensus rows now carry
  `market_quote_provenance=espn_consensus_indicative`, `market_quote_source=espn_consensus`.
- `cli/forecast.py`: indicative NCAAF rows get `Indicative ask 0.xxxx (slug).` in the rationale,
  as executable rows get `Executable ask`.

**Not changed:** artifact gates (`max_edge`), model code, the auto-buyer, `BBO_CAPTURE_SPORTS`
(out-of-scope capture still runs, last and time-boxed).

**Tests (each shown failing on the old code, then passing).** `tests/test_polymarket_us.py`:
`test_slate_capture_resumes_after_a_single_rate_limit` (old: captured 0 of 3),
`test_slate_capture_spaces_requests_by_the_minimum_interval` (old: gaps 0.0 s),
`test_slate_capture_pacing_is_shared_across_workers` (old: gap 0.00001 s),
`test_capture_fetches_scope_leagues_round_robin_before_out_of_scope_and_started`,
`test_capture_time_budget_reaches_every_scope_league_before_any_out_of_scope_one`,
`test_capture_secondary_tier_has_its_own_short_budget`,
`test_ladder_keep_mapping_prices_each_league_its_own_rung_count`,
`test_cli_capture_passes_per_league_ladders_and_scope_priority`; the pinned abandon test became
`test_slate_capture_abandons_queue_only_after_consecutive_rate_limit_budget` and the NBA-first
test became `test_priority_league_shortlists_each_game_before_other_sports`. Mutations (no
round-robin; abandon on the first 429; started games kept in the primary tier) each fail a test.
`tests/test_nfl_week_ahead_capture.py::test_daily_runs_week_ahead_books_only_after_the_slate_capture`.
`tests/test_cfb_indicative_quote.py` (5 tests: indicative pricing, captured book wins,
consensus provenance, the 10-minute point-in-time guard, provenance reaching the Flat ledger row).

**Live proof (read-only GET traffic).** `python -m model_prediction.cli polymarket-slate --league NCAAF --date 2026-11-14`,
19:55:03-20:10:14Z (this process loaded the 900 s cap; the committed default is 600 s). Before
(17:34Z, old code): captured 23, skipped 551 after the first 429. After: 117 events, 539
contracts after ladders (298 upcoming in the primary tier, 241 started in the secondary);
captured 311, `skipped_after_rate_limit` 0, `source_rate_limited` false, 6 rate-limit pauses,
spacing settled at 1.34 s. Every one of the 298 upcoming contracts was captured with a valid
pregame timestamp; 59 of 60 upcoming games have moneyline, spread and total quotes (the 60th,
Nicholls vs. Incarnate Word, lists no spread market). The 225 time-budget skips and 3 invalid
BBOs are all games already under way. Sustained throughput was about 20 contracts a minute
(31 contracts fell back to /bbo after a throttled /book), so the gateway tolerates roughly one
book request every 3 s.

`polymarket-slate --league NCAAB --date 2026-11-14`, 20:13:51-20:21:21Z (600 s cap). Before:
two of the five daily runs wrote NCAAB rows at all (6 and 8); a standalone run at 17:59Z wrote 68.
After: 62 events, 162 contracts after the one-rung ladder (126 upcoming, 36 started); captured
161, `skipped_after_rate_limit` 0, 2 rate-limit pauses, 432 s; all 46 upcoming games have every
listed moneyline/spread/total quoted. The one failure is an invalid BBO on a game under way.

**Cost and limits.** The daily's slate-capture step used to quit after about a minute; it now
runs until the in-scope queue is done or the 600 s cap, plus the NFL week-ahead books after it.
At the measured gateway rate (about 20 contracts a minute) one daily run captures roughly 200
in-scope contracts, earliest kickoffs first, round-robin across NBA/NCAAB/NCAAF/NFL. A full
Saturday queue (NCAAF alone had 298 upcoming contracts at 19:55Z) therefore spans several daily
runs; an NCAAF market not yet captured is priced from the event list (indicative) whenever
Polymarket lists it, and ESPN consensus is only the last fallback. The daily worker's timeout is 60 minutes; today's runs took
17-38 minutes. Open operator decision, not changed here: raise `POLYMARKET_CAPTURE_MAX_SECONDS`,
or move the capture off the daily's critical path into its own scheduled worker.

**Pre-existing, not touched:** `tests/test_polymarket_us.py::test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error`
fails at HEAD (`_open_pick_slugs(root, None)` reads `existing_main_ledgers`, the test patches
`main_ledger`; since 32528f57).

## 2026-11-13 — NCAAF: cfb-structural-v5 promoted to champion (operator directive); serving now follows the champion map

Operator directive 2026-11-13 ("promote v5 fully into main and flat ledger"), executed under the
Direct Model Wiring policy (`docs/ROADMAP.md` 2C) via the sanctioned command, one record per
market in the runtime `promotions` table (`model_promotion history`): NCAAF moneyline, spread,
total `cfb-structural-v4 -> cfb-structural-v5` at 18:51Z, approved_by Vincent Chen, evidence
`outputs/ncaaf/cfb_v5_research.md` (market-relative block: settled rows re-priced at recorded
asks). Quick task `261113-j4w`.

**Evidence stated plainly.** The v5 artifact's `qualification` block says `PRODUCTION_ACTIVE,
qualified: false`: holdout margin and total log scores are significantly better than v4, the
moneyline Brier gain (0.18933 vs 0.19056) is not significant, and totals lose at recorded asks
under both models. The artifact hash is `449bf27c80d6…` (recomputed by code after the status
change; never hand-edited).

**Wiring (every consumer that keyed on the champion id):**

- `college_football.cfb_model()` now resolves the NCAAF champion from `config/production.yaml`
  (all three markets must agree), loads that entry's artifact, verifies `model_version` and
  `artifact_hash`, and dispatches to the serving class (`cfb_model_for_artifact`: v4 →
  `CollegeFootballModel`, v5 → `CFBStructuralV5Model`). `_forecast_cfb_sport` passes the exact
  serving artifact's model into `build_cfb_slate`, so rows are priced by the artifact whose hash
  they carry — the mechanism that would have prevented the 2026-11-11 finding #1.
- Candidate-evidence re-pricing calls `margin_sd_for_features` / `total_sd_for_features`
  (v4: uncertainty widening; v5: slice table), no version-specific knobs in the slate builder.
- `cfb_replay.VERSIONS` accepts v5 (without it every v5 ledger append would have raised
  `cfb_replay_decision_identity_mismatch`); `_forecast_cfb_sport`'s Main version list accepts v5;
  `sports/naming.py` has the v5 descriptor and aliases; the auto-buyer whitelist swaps v4 → v5.
- `config/production.yaml`: v5 entry (`serving_status: production`, `evidence_status:
  historical_only`, `rollback_model: cfb-structural-v4`), v4 → `rollback`, allowed_models and
  artifact_map carry v5, NCAAF challenger incumbents → v5. `config/model.yaml` NCAAF:
  `active_production_version: cfb-structural-v5`, artifact path, protected_versions.

**Live verification.** `cfb_model()` returns `CFBStructuralV5Model`; the 2026-11-14 ESPN slate
(56 games) priced 167 contracts all labelled v5, margin SDs 16.9–20.6 (slice table × blowout
widening). A real `_forecast_cfb_sport` run against live ESPN data into isolated Flat and Main
ledgers wrote 167 Flat rows and 19 Main rows, every snapshot replaying
(`validate_decision_snapshot` passes on append); Main rejections `excessive_edge 56, pregame_weather_unverified 54, total_projection_within_config_gap 14, total_probability_above_config_max 11, total_under_line_above_config_min 10, fcs_blocked 3, spread_line_above_max 1, moneyline_ask_below_floor 1`. The scheduled
daily job will write the production rows at its next run.

**Rollback:** `python -m model_prediction.model_promotion rollback --sport NCAAF --market <m>`
(three markets) restores `cfb-structural-v4`; the serving factory follows the map automatically.

**Shared-tree note.** Another session's commit `ec3210a2` (NBA promotion, 18:47Z) committed
`config/production.yaml` by path four minutes after I had edited the v5 registry entry in the
working tree, so that entry entered history under the NBA commit; this commit carries only the
champions map change. Harmless functionally; recorded for the audit trail.

## 2026-11-13 — NCAAB: Main totals restored behind the sample gate; box-score backfill; possessions tested (quick-261112-nc6, round 3)

- **Main totals restored** (`df37969f`). The 93aa093c pause was a hard `kind != "total"` in
  the Main destination. A total now reaches Main under the spread/moneyline conditions plus
  the current-season sample gate (min_team_games 8) that the pause predated; new policy key
  `models.NCAAB.main_totals_enabled` (default true) is the operator pause lever and a disabled
  total is recorded as `main_totals_disabled_by_policy`. Every one of the 454 settled total
  rows had a sample of 0–3 and would still be excluded. Tests: `tests/test_ncaab_main_totals.py`.
- **Box-score pipeline**: the daily capture (`cli/daily.py` → `capture_completed_boxscores`)
  has been live since 2026-11-03 and complete (file counts match completed games); the gap was
  history. `scripts/backfill_ncaab_boxscores.py` captured the 2025-26 season and 2026-11-02
  (6,483 of 6,484 games in 314 s; one 2025-11-10 game lacks components upstream). Coverage of
  the history file: 99.97%. Backfilled records carry `observed_at_utc` = capture time; they
  are terminal box scores, usable as history only.
- **Possessions feature** (`features/ncaab_possessions.py`, `tempo_source="possessions"` on
  the total model: FGA − OREB + TOV + 0.44 FTA). Walk-forward validation vs the shipped
  attempts proxy at the same sensitivity: MAE 13.399 vs 13.376, over-Brier 0.2342 vs 0.2345,
  PIT 0.501/0.824/0.918 vs 0.507/0.821/0.921 — indistinguishable. **Not shipped**; attempts
  stay. The option remains available and tested for an efficiency-based (points per
  possession) spread feature, which is the natural next step now that the data exists.
- Reproduction gate: the 21:0xZ daily's 132 Flat rows are reproduced exactly by the deployed
  artifact model.

## 2026-11-13 — NBA availability feature charged other teams' injuries to a matchup via abbreviation substrings (quick-261113-jrq)

Found while re-pricing the 2026-11-13 slate after the ledger-store fix. `compute_nba_matchup_availability`
assigned an injury row to a side when the row's team name was a substring of the matchup name (or the
reverse) OR when the row's two/three-letter abbreviation appeared anywhere inside the matchup name.

- **Blast radius tonight**: Grizzlies at Pelicans counted seven Orlando Magic ("orl" in "new orleans")
  and four Philadelphia 76ers ("phi" in "memphis") injuries: home_lost 12.0 / away_lost 10.1 instead of
  5.5 / 4.4, so the Main under 233.5 was priced on 22 phantom points of absence. Blazers at Kings counted
  six Warriors ("gs" in "kings") and four Spurs ("sa" in "sacramento"); Warriors at Spurs counted a
  Nugget ("den" in "golden"). The same code also MISSED real injuries when the feed and scoreboard spell a
  city differently ("LA Clippers" vs "Los Angeles Clippers"): Clippers home_lost 0.0 -> 5.8 after the fix.
- **Scope**: the feature is wired into `nba-spread-margin-v9` / `nba-total-margin-v9` (and v7 before
  them) as `expected_margin += availability_points_gap` and `total -= 0.15 * availability_total_loss`,
  so every settled v7/v9 row for a matchup whose name contains another team's abbreviation carries the
  contamination. The replay snapshot does not persist the availability inputs, so the affected history
  cannot be re-priced from the ledger alone.
- **Fix**: `_team_matches()` with exact normalized-name equality, else nickname (last token) equality
  (NBA nicknames are unique), else abbreviation equality only when the matchup string itself is the
  abbreviation. No substring tests remain.
- **Tests** (`tests/test_nba_nhl_cfb_injuries.py`): decoy Magic/76ers/Warriors/Nuggets rows against a
  Pelicans/Grizzlies and a Kings/Blazers matchup; "LA Clippers" nickname match; abbreviation-only
  matchup strings. Both new tests fail on the old matcher. The two failures in
  `tests/test_five_blind_spot_guards.py` on this tree (`no NCAAF serving class ... 'cfb-structural-v3'`,
  `unit_policy` mock lacking `.rows`) predate this change and come from the NCAAF v5 promotion and
  `forecast.py` edits landing in parallel.

## 2026-11-13 — 05:30Z daily failure: sqlite "disk I/O error" in the ledger store under host memory pressure (quick-261113-jb1)

`daily-20261113T053001Z-8f176c` exited 1. The NCAAB forecast raised
`sqlite3.OperationalError: disk I/O error` inside `RuntimeLedgerStore.records()` while
`PickLedger._append_record` re-read the Flat mirror; the run then failed `ledger_coverage`. The
11:00Z, 14:00Z and 17:00Z runs were green and `PRAGMA quick_check` on `ledgers.db` returns ok.

- **Root cause (host, not disk)**: Windows System log Resource-Exhaustion-Detector Id 2004
  ("low virtual memory condition") at 00:41:49 and 00:42:24 local, inside that run's Step 2
  window (05:33Z-05:49Z); top consumers Overwatch.exe 3.6-4.9 GB and the daily python.exe
  3.4 GB; DWM crashed at 00:41:53. The 2026-11-12 05:30Z failure window carries the same 2004
  events (rust-analyzer + python), the same hour the 11-11/11-12 ingestion `MemoryError`s above
  hit. Ruled out: drive E (healthy NVMe, no disk/NTFS events, 189 GB free), Defender (no scan
  in the window), the 08:00Z offsite backup, and `log_maintenance` (VACUUMs top-level `*.db`
  only, never `ledgers/ledgers.db`).
- **Why it surfaces as I/O**: the store opened one connection per forecast thread (16 workers)
  with `PRAGMA mmap_size=268435456` and a 64 MB page cache each: 4 GB of address space mapped
  over a 1.1 GB file. A memory-mapped page that cannot be committed fails as `SQLITE_IOERR`,
  not `SQLITE_NOMEM`.
- **Fix** (`runtime_ledger_store.py`): `mmap_size=0`, `cache_size=-32000`, and a bounded
  reopen-and-retry in `records()` for `OperationalError` messages containing "disk I/O error"
  or "database is locked" (`_RETRY_DELAYS = (0.5, 1.5, 3.0)`, so four attempts), re-raising
  anything else on the first attempt. New `_reset_connection()` closes and drops the thread's
  handle so the retry does not reuse a poisoned page cache.
- **Tests** (`tests/test_runtime_ledger_store.py`, all four fail on the pre-fix code):
  pragma values; two poisoned connections then success; persistent failure raises after
  `len(_RETRY_DELAYS) + 1` connections; a `no such table` error is not retried.
- **Follow-ups, not done here**: `PickLedger._append_record` re-reads the entire tier/sport
  mirror on every append (that is why the daily process holds so many large result sets at
  once); and the host itself runs at about 1 GB free of 31.7 GB while games or rust-analyzer are
  up, which no code change removes.

## 2026-11-13 — System-usage pass: dashboard snapshot cache unbounded; memory, ledger and backup footprint measured (quick-261113-jpw)

Operator directive "optimize project system usage". Commit `4fb785cd`.

- **Dashboard memory (fixed)**: `dashboard/backtests._SNAPSHOT_FILE_CACHE` kept every parsed
  `polymarket_snapshots.jsonl` for the life of the process: 774 files / 543 MB on disk, x4.4 expansion
  measured (43 MB -> 192 MB resident), ~2.5 GB ceiling. That is the 3.4-3.6 GB python process named in
  the 11-12 16:10 and 20:18 low-virtual-memory events. Now an LRU bounded at 96 MB on-disk bytes.
  Restart the dashboard to apply.
- **Host**: 32 GB RAM / 20 GB pagefile; commit 27 of 52 GB at rest. The 00:30 ET daily run fails on
  nights a game is running (Overwatch/HOI4 15 GB); the daily forecast itself peaks ~3.2 GB.
- **Ledger**: `ledgers.db` 1.12 GB = 660 MB hash-chained `ledger_events` payloads (evidence, keep) +
  340 MB records, 51% of them `superseded` from the 8x/day re-forecast cadence; +16-49 MB/day.
- **Disk**: `backups/` 20.6 GB (14 copies of the 1.1 GB ledger) mirrored 1:1 into OneDrive;
  `market_quotes.db` 685 MB; repo `data/raw` 2.9 GB, `data/availability` 2.1 GB.
- **Not changed** (operator decisions): backup `--keep`, the 00:30 trigger, the forecast cadence.

## 2026-11-13 — Loss review of the 1W-7L paper day; NBA v9 spread/total pulled from the auto-buyer (quick-261113-jf1)

Operator directive "investigate this performance loss, and check pipeline logs". Commit `c648e048`.

- **The loss**: paper auto-buyer ledger, event date 2026-11-12, 8 settled orders, -$41.08. Five
  NCAAB spread paper-trial orders at $0.50 units (1W-4L, -$1.56) and three NBA v9 orders at $10
  units (0W-3L, -$39.53): `nba-spread-margin-v9` Raptors +7.5 (model 61.1% vs ask 51.9%), and
  `nba-total-margin-v9` under 223.5 PHI/TOR (58.9% vs 49.0%; actual 235) and over 214.5 CHA/PHX
  (61.8% vs 51.0%; actual 197). Decision packets intact: executable Polymarket quotes, valid
  timestamps, identity calibrator. Classification: **model_error**.
- **Evidence** (canonical `ledgers.db`, Flat tier, settled rows): `nba-total-margin-v9` 13W-20L
  (-8.8u), 2W-5L at 8-12% edge; its projections are under-dispersed against the market line
  (sd 4.6 vs 6.6 points) and worse than the line at predicting totals (MAE 14.0 vs 12.7); record
  by |model - line|: <2 pts 6W-8L, 2-4 3W-4L, 4-6 2W-7L. `nba-spread-margin-v9` 17W-17L, 2W-7L at
  8-12% edge, MAE 12.0 vs the line's 11.3, 0W-3L at 4-6 points of disagreement. The v7 totals
  model shows the same 0W-5L at 8-12% edge, so this is structural (no market anchoring), not a
  v9 regression. The nightly calibration audit rates both SEVERE_DRIFT (spread slope 0.19,
  ECE 15%; total ECE 12%) but with 33-34 outcomes the >=100-outcome guard keeps the serving
  calibrator at identity, so the buyer sees raw probabilities; edge-scaled sizing then put
  1.25-1.4u on the worst bucket. Both v9 models became champions on 2026-11-08 (`84258909`)
  with no promotions-table record and no evidence doc.
- **Action**: `nba-spread-margin-v9` removed from `DEFAULT_WHITELIST_MODELS` and from the
  persisted whitelist (versioned save 175 -> 176, backup kept); totals were already out
  (quick-261113-r2d). Forecasting unchanged. Owed: a market-relative replay before either is
  made buyable again; a promotion record for the 11-08 activation.
- **Pipeline logs**: 00:30 ET on 11-13 failed again, this time `sqlite3.OperationalError: disk I/O
  error` reading `ledgers.db` (1.17 GB, `quick_check` ok, SSDs healthy). Windows System event
  2004 (low virtual memory) fired at 00:41 naming Overwatch 4.9 GB, the dashboard python 3.4 GB and
  rust-analyzer 2.9 GB; the same event fired at 00:31-00:45 on 11-12 (the MemoryError run) and
  00:36 on 11-11. The three nights' different errors share one host cause. The BLAS fix held
  (ingestion exit 0 at 00:30); every later run on 11-13 was clean. Event 2004 also fired at 16:10
  and 20:18 on 11-12 with the dashboard process as top consumer (3.6 GB); the dashboard was
  restarted at 13:37 today (1 GB now). The calibrate worker's SEVERE_DRIFT flag names about 130
  lanes and is not discriminating.
- **NCAAB forward check** (pinned in the handoff): 1W-4L in the 5-8% bucket with 6 open since
  the 11-12 fix; nothing to conclude yet.

## 2026-11-12 — Full pipeline verification: odds-root split-brain in health/daily/dashboard, frozen artifacts rewritten by the schedule (quick-261112-mpz)

Operator directive "verify and run through ALL pipeline and workflows". Commit `c96f28cf`.

- **Health false alarm (real bug)**: `system_health._market_capture` read the checkout's `data/odds`
  while `cli.commands` has written Polymarket snapshots under the runtime root since 2026-11-04.
  Result: "market snapshots stale (8 days)" for every sport while capture was healthy (the data gap
  monitor, which reads the runtime root, said FRESH). Fixed to read `runtime_root/odds`;
  `tests/test_system_health.py::test_market_capture_reads_the_runtime_root_not_the_checkout`
  fails on the old code, passes on the fix.
- **Frozen artifacts rewritten every run (contract violation)**: `_research_models_dir()` returns the
  rolling `runtime_root/models` only once it exists and is non-empty, else `config/models`. It was
  also used as the WRITE target by `_refresh_esports_ratings`, `_refresh_international_baseball_ratings`
  and the `--write-artifacts` commands, so nothing ever created the rolling dir and every daily run
  rewrote `cs2/dota2/lol/rainbow_six/valorant-tiered-elo-v6.json`, `kbo/npb-tie-aware-elo-v2.json`
  and their `.previous.json` in git (mtimes 16:08-16:10 during the 16:00 run). New
  `_rolling_models_write_dir()` always targets and creates the rolling dir; reads unchanged.
  Runtime `models/` seeded from the 16:08 copies, frozen files `git checkout`ed. Registry still
  validates every model on the frozen copies.
- **Same split-brain elsewhere**: `daily.py` step10 Polymarket edge scanner (`scan_directory(base_dir=
  data_directory/"odds")`) and the `odds_by_sport` summary read the repo tree (scanner saw nothing since
  11-04); `dashboard/routes.py` live capture wrote to `ROOT/"data"`. All rerouted through the new
  `runtime_paths.rerouted_data_root()` (legacy repo `data/` -> runtime root, explicit roots untouched).
  `settle.py`'s closing-snapshot lookup also passes the repo root, but settled rows since 11-04 still
  carry closing prices that differ from decision prices, so it is fed by another path; left as is.
- **00:30 ET daily failures**: 11-11 and 11-12 00:30 runs failed in ingestion with `MemoryError` /
  `OpenBLAS error: Memory allocation still failed after 10 retries` (seven parallel interpreters on a
  12-core box). `run_daily.ps1` ingest jobs now set `OPENBLAS_NUM_THREADS=1` / `OMP_NUM_THREADS=1`.
  The 11-10 all-day `ledger_coverage` integrity exits and the 11-11 16:00 `0xC0000005` crash are the
  earlier, already-documented failures. All five later runs on 11-12 exited 0 on every step.
- **Test suite status (not fixed, triaged)**: full suite 2927 passed / 57 failed; 54 persist with the
  `MODEL_PREDICTION_*` env cleared; 39 fail at clean HEAD, 36 at checkpoint `0af19ecd`. Regressed on the
  branch: `test_auto_polymarket_buyer::test_buyer_rejects_below_win_probability`,
  `::test_buyer_rejects_unrealistic_edge_outliers`,
  `test_qualification_integrity::test_names_cannot_claim_prospective_capture[wnba-moneyline-v5]`.
  Dirty-tree-only (18): `test_dashboard_polymarket` x2 pin `scanner_enabled: false` but the uncommitted
  `config/model.yaml` enables it (operator directive 2026-11-10); `test_dashboard_server::test_pick_quote_*`
  x6, `test_main_ledgers` x4, `test_research_ledger*` x4, `test_auto_polymarket_buyer::..._network_error`,
  `test_research_pair_capture` read local runtime state. The remaining failures pin superseded versions
  (`nfl-*-structural-v5` vs v7, `eurolg-tempo-v1` vs v2, NBA v9 sets, model catalog, CI import count
  78 vs 88, `cfb_structural_v2` dispersion 19.2 vs 16.0) or stub drift (`Ledger.settle()` kwargs,
  `_DuplicateLedger.rows`, `sqlite ledger authority requires a RuntimeLedgerStore`). Two look like
  real code paths worth a look: `research_scoring.outcome_label` does `float(prediction["line"])` on a
  `None` line for moneyline rows, and `settle._settle_by_resolution` indexes `row["event_start_utc"]`
  on rows without it.
- **Also observed, unchanged**: `data/calibration/**` (97 files) is git-tracked yet rewritten by the
  calibrate step every run; health flags off-season kbo/mlb/npb/wnba snapshots as stale (2-day rule,
  no season model); production canary primary is WNBA so `production.db` has no prediction since 10-31
  (NO_EVENTS); auto-buyer had two `0xC0000005` crashes on 11-12 (known python312.dll fault);
  `/api/bets` ~100 s and `/api/export/picks` ~19 s on a threaded server.

## 2026-11-12 — NCAAB feature round: history-derived non-D1 prior, AP-rank prior, attempts-based tempo (quick-261112-nc6, round 2)

Operator directive "improve the model, add more features" after the review fixes. Full
write-up and tables: `docs/NCAAB_V6_REVIEW_FIXES_2026-11-12.md` (Feature round section);
report `outputs/ncaab/ncaab_v6_review_ablation.md`.

- **Diagnosis first**: on the 658 November-2026 games the round-1 model under-predicted
  home margins by 12.4 points on average; 187 of those games had a non-Division-1 visitor
  (bias +29, actual +42 vs modelled +13 — the 1180 non-D1 prior implied a 13-point gap)
  and Tier-1/2 hosts in buy games were under-priced by a further ~13.
- **Data already on disk**: the archived ESPN scoreboards (`data/raw/ncaab/<date>/
  scores_ncaab.json`, 182 days) carry per-team FGA/3PA/FTA/makes, rebounds, assists, the
  AP `curatedRank` carried into each game, neutral-site and conference flags for 99.96%
  of history games. New `features/ncaab_scoreboard_stats.py` serves them by ESPN event id;
  callers pass only the history they may see, so it cannot leak.
- **Spread** (`non_d1_prior_from_history`, `rank_prior_per_slot` 8): both act only in the
  season-opener regime, which the Feb–Mar validation split does not contain, so they were
  selected on the train window (Dec–Feb; nothing else is fitted there) with validation
  required not to degrade. Train: moneyline Brier 0.1844 → 0.1803, margin MAE 10.73 →
  9.83; validation unchanged (0.2020 / 8.85); holdout (reported once) MAE 16.51 → 12.71,
  moneyline Brier 0.1219 → 0.1096 (now better than the pre-fix control's 0.1116). Rank
  slots 12/16 and non-D1 constants 1000/850/700 were tested and not taken.
- **Total** (`tempo_source="attempts"`, `pace_sensitivity` 1.5): validation MAE 13.78 →
  13.38, residual SD 16.93 → 16.56, PIT 0.507/0.821/0.921, over-Brier 0.2363 → 0.2345;
  holdout MAE 14.49 → 14.30. Grid 0.55–2.5; 1.5 is the interior optimum.
- **Deployment check**: the daily at 19:05Z (after round 1) wrote 68 Flat rows; the deployed
  artifact model reproduces all 68 exactly (harness gate `matched_by: deployed_artifact`).
  Artifacts rewritten with the new keys and evidence; consumed-keys / parity tests cover them.
- Rollback: revert the round-2 commit; the round-1 artifacts are in git (`e32335c7`).

## 2026-11-12 — NFL loss investigation: no CLV existed, placeholder rungs priced as main lines, consensus/opener capture added

Follow-up to the 2026-11-11 NFL review. Full evidence in
`docs/NFL_LOSS_INVESTIGATION_2026-11-12.md` (loss attribution, 1,489-game
component regressions, outside research). Code changes, all NFL-only:

- **CLV was zero by construction, not measured.** Every NFL row had an empty
  `closing_line`; `probability_clv` was 0.000 where present because the
  Polymarket "closing" snapshot was the same game-day capture the decision
  used (NFL markets were only snapshotted on game day, 1-2 captures), and no
  sportsbook consensus was stored. New `data_sources/espn_nfl_odds.py`
  captures ESPN's DraftKings opener + current two-sided spread/total/moneyline
  prices for every NFL game in the next 7 days on each run
  (`odds/nfl/<game_date_et>/espn_odds.jsonl`, append-only, sha256 per record,
  readers filter on `observed_at_utc` and drop tampered lines);
  `commands.capture_nfl_polymarket_week_ahead` snapshots Polymarket books for
  the same window (`horizon_hours=None`, filed under each game's own date).
  Both run in the daily capture pool as `step1l_nfl_week_ahead_quotes`.
  NFL rows now carry `decision_consensus_probability/line` (de-vigged side
  price at the book's own line) and the opener in `feature_basis`;
  `cli/settle.py` writes `closing_consensus_probability/line` from the last
  pre-kickoff record when its line equals the pick's line. First live
  capture: 14 games, 2026-11-12T06:52Z (WAS@NYG opener -1.5/47.5, current
  -2.5/40.5).
- **Placeholder rungs were selectable as the main line.** Reproduced on the
  2026-11-12 WAS@NYG snapshot: before the main line was listed, Polymarket
  carried only +17.5..+30.5 spreads (long asks 91-99%) and 17.5-23.5 totals;
  `_select_latest_balanced_market` priced +17.5 at a 91% ask as the spread
  and the 23.5 total at an 8% ask. Fix: long ask must sit in 20-80%, and with
  a consensus reference the nearest rung within 6 (spread) / 7 (total)
  points wins, else the builder declines to the consensus price. The
  reference is the parsed ESPN consensus for the event, home-anchored.
  `tests/test_nfl_market_selection.py` pins the real ladder.
- **Total fallback asks were vig-inclusive one-sided prices** while the
  spread fallback de-vigged; both now use the de-vigged consensus side.
- **"NFL Structural v6" labels on v7 rows** (rationale, model_name, risks)
  now carry the served `model_version`; the risk text states the replay
  finding (structural signal carries no information beyond the close).
- **Addendum 2026-11-13: the consensus capture wrote to the wrong root.** The
  daily job hands capture the checkout's `data/` root while Polymarket capture
  and settlement resolve to the runtime root; eleven runs (2026-11-12T11:01Z
  to 2026-11-13T17:02Z) wrote 153 records under `E:\MP\data\odds
fl` where
  no reader looked. `espn_odds_path` now applies `rerouted_data_root` (one
  choke point for every writer/reader); the 153 records were merged into the
  runtime root by `record_hash` and the misplaced copies removed. Regression:
  `test_repo_local_data_root_is_rerouted_to_the_runtime_root`. Same bug class
  as the 2026-08 capture split-brain and `c96f28cf`.
- Stale fixture `tests/sports/nfl/spread/test_nfl_spread.py` (one-sided asks,
  no `observed_at_utc`) updated to the selector's contract; its two
  end-to-end tests still assert the retired v5 version (pre-existing).

Not changed (operator decisions, see the investigation doc §5): spread sd
12.8 / heavy-line inflation, `predictively_qualified` on the three NFL
champions, retiring `nfl-elo-trend-lr-v4` (−10u at real quotes; its
qualification and 50 of its settled rows are priced at the 52.38% placeholder).

## 2026-11-12 — NCAAF review fixes: serving the v4 class, ask integrity, artifact gates, venue weather, QB channel

Closes every NCAAF item in `docs/MODEL_REVIEW_2026-11-11.md` (#1, #8a/b/c, #9 CFB,
#12, #13, LOW: MC noise, `code_revision`). Quick task `261112-0xd`. Verified
against the live ESPN scoreboard for 2026-11-14 (56 games, 167 contracts) and
the runtime ledger read-only. Every fix carries a regression test in
`tests/test_cfb_serving_parity.py`; the two headline tests were shown red under
mutation (legacy 16.2 model injected; newest-capture matcher flipped to oldest).

**Defect window (#1): 2026-10-26 (v4 promoted) → this fix.** `build_cfb_slate`
instantiated `CollegeFootballModel()` — a second implementation with
`margin_sd=16.2`, no conference scaling and no total-SD widening — and stamped
every pick `cfb-structural-v4` with the v4 artifact hash. `CFBStructuralV4Model`
(14.5 from the 11-05 walk-forward retune) was only ever imported by
`sports/ncaaf/`, which nothing in `cli/` calls. The runtime ledger holds 243
Main rows labelled `cfb-structural-v4` priced on the 16.2 arithmetic (176+3
superseded, 56+6 settled, 2 open; 229 of them against ESPN consensus). They are
left in place and are identifiable: their snapshot `parameters.margin_sd`
divided by the recorded widening is 16.2, not 14.5. Superseding them is an
operator decision, as with the NFL defaulted-input rows.

Fixes:

- **One implementation.** `CollegeFootballModel` is now a subclass of
  `CFBStructuralV4Model` carrying only the legacy constructor (per-market
  version labels). `cfb_model()` builds it with
  `CFBStructuralV4Model.from_artifact(load_cfb_structural_v4_artifact())`,
  which refuses a wrong `model_version`, a bad `artifact_hash`, a declared
  feature the extractor does not produce, or a non-metadata key nothing reads.
  `build_cfb_slate(..., model=None)` defaults to it. The v4 engine now records
  `inference_inputs` (exact pre-draw RNG state, from one advancing stream shared
  across the slate) so settlement replay (`cfb_replay`) keeps working; its
  probabilities are kept at six decimals (four would have failed the 2e-6
  replay tolerance on every row). `cfb_distribution.py` is untouched: its
  source hash is pinned by every open snapshot's replay identity.
- **Feature names.** `CFBMatchupFeatures.to_dict` now emits the eight
  artifact-declared names it omitted (`*_offense_ppp`, `*_defense_ppp`,
  `*_preseason_prior_weight`, `*_qb_value_adjustment`) plus `sample_games`.
- **Ask integrity (#8a/b).** Snapshot rows are reduced to the newest capture
  per `market_slug` before matching; `_match_cfb_moneyline` returns the newest
  matching capture instead of the first file row; spread/total/moneyline
  matchers require both side asks, and the `1 - ask` completion is gone.
- **Provenance (#8c).** `feature_basis.quote_provenance` is
  `polymarket_executable_ask` or `espn_consensus_indicative`, and the rationale
  says so, mirroring NFL `5ffc2b40`. Main eligibility of consensus rows is
  unchanged — open operator decision.
- **Artifact gates enforced (#9).** `main_gates_from_artifact` reads every key
  of the artifact's `gates` block; `_forecast_cfb_sport` rejects from Main with
  `fcs_blocked`, `spread_line_above_max` (|line| > 21.5) and
  `moneyline_ask_below_floor` (ask below 33.3%, the +200 dog limit expressed
  as a probability). `min_edge` defaults to the artifact's 2% (was a hardcoded
  3%). The live 11-14 slate has 3 FCS contracts the gate now blocks.
- **Total gates to config (#12).** The hardcoded p > 62% / under ≥ 56.5 /
  |proj − line| < 1.5 clauses moved to `config/model.yaml`
  `models.NCAAF.main_total_gates` with the same values (documented as
  unvalidated; delete the block to disable); the dead `edge > 0.10` clause and
  the unread `spread_sd: 16.2`, `max_moneyline_american_odds`,
  `blowout_spread_edge_cap` keys are removed.
- **Neutral sites and postseason (#13).** `resolve_cfb_game_weather` takes
  `is_neutral_site` / `venue_name` / `venue_indoor` from ESPN's `competition.venue`
  and never falls back to the home stadium for a neutral game; the daily
  `capture_cfb_pregame_weather` now captures neutral venues by ESPN name
  (NFL venue table + CFB stadium catalog; unknown venue ⇒ unverified). Verified
  on the live 2025-12-31 payload: bowls carry `season.type=3`, `week.number=1`,
  `venue.fullName`, `venue.indoor`. Postseason maps to model week 15 (prior
  weight 1%) instead of week 1 (90%).
- **QB channel (#13).** New `features/cfb_qb_availability.py` joins the daily
  ESPN CFB injuries snapshot (newest with `observed_at_utc <= decision`, ≤ 48 h
  old) to the scoreboard passing leader (ESPN has no college depth chart) and
  sets `qb_starter_prob_*` (Out/IR 0, Doubtful 44.4%, Questionable 77.8%). No
  evidence ⇒ 1.0 with the reason recorded in `feature_basis.qb_availability`.
  Limitation found on live data: ESPN's college injuries feed carried 3 entries
  on 11-12 (one dated 2020), so the channel is wired and point-in-time correct
  but will rarely fire until a denser report source exists.
- **LOW.** Simulation count 15k → 200k on the served engine (±0.8 pp → ±0.22 pp
  95% CI; recorded in `inference_inputs`, replayable, 5.9 s for a 56-game
  slate). `code_revision` is a digest of the five serving source files, not of
  the version string.

Reproduction:

```
.venv/Scripts/python -m pytest tests/test_cfb_serving_parity.py tests/test_cfb_structural_v4.py tests/test_college_football.py tests/test_cfb_replay.py tests/test_cfb_quote_integrity.py -o addopts='' -q
PYTHONPATH=src .venv/Scripts/python -c "from model_prediction.models.college_football import cfb_model; m=cfb_model(); print(m.margin_sd, m.total_sd, m.n_simulations)"   # 14.5 16.0 200000
```

Pre-existing, unrelated failures in the same tree (also red at committed HEAD):
`tests/test_cli.py` tennis/soccer/MLB/WNBA/NFL ledger tests,
`test_model_naming.py::test_catalog_covers_production_registry`,
`test_ci_contracts.py::test_ci_imports_of_checked_in_config_resolve`.

## 2026-11-12 — NBA review follow-up: serving transforms, Main relabel, lr-v4 skew, totals selector, neutral floors

Re-verification of `docs/MODEL_REVIEW_2026-11-11.md` (NBA items) against the live
tree and `E:\model-prediction-runtime\ledgers\ledgers.db` (read-only). The doc's
NBA findings (#3 moneyline path, #6, #8d, #14) all reproduced; three further
defects surfaced that the review could not see from model code alone, because
they live in data files, in a nightly pipeline, or in uncommitted working-tree
code the scheduler ran live. Every fix below carries a regression test shown
red without the fix. Quick task `261112-11j`.

**Defect window (totals): 2026-11-11T01:32Z → 2026-11-12T05:32Z.** Every NBA
total priced in that window was pushed through a Beta calibrator
(`a=1.2827, b=1.2591, c=-0.6334`, `data/calibration/models/nba-total-margin-v9.total_calibrator.json`
at HEAD) that had been fitted on 2026-11-08 from 125 settled picks made by
`nba-total-margin-v2..v7`, labelled v9. The loader in `_forecast_nba_total_slate`
went live from the working tree before it was committed (`0af19ecd`). Served
probabilities on the model's own selected side fell to 35–46% (ledger rows carry
`calibration_method=beta`, hash `7f804465…`), so every total had negative edge
and Main totals went dark from 11-09. At 05:32Z on 11-12 the nightly calibration
pipeline refit on v9's 28 own rows, chose identity, and overwrote the file. No
Main row was priced on the transform (that is the point: it blocked them all);
13 Flat rows were. Flat beta-era rows went 3-10 on the model's raw selections,
which the transform did not change.

Fixes, one commit each:

- **lr-v4 serves on its fit construction** (`62e0118c`). `learned_forward.py`
  injected NBA-only Elo/Trend knobs (HCA 38, offseason gap 60, retention 0.40,
  `isolate_postseason`) added in `a9315de4` and borrowed from the v9
  structural config; `validation.py` fit `nba-elo-trend-lr-v4` on bare
  `build_elo(history, sport)` / `TrendEngine(history)`. Even-team
  `elo_probability` moved 59.9% → 55.5% against a 3.56 coefficient, so the
  artifact's Brier 0.185 described a model that was not being served. Serving
  now passes only what the artifact declares and otherwise uses the library
  defaults training used. Test: `test_nba_learned_slate_rebuilds_elo_and_trends_exactly_as_training_did`.
- **Serving calibrators require own-model fit provenance** (`c5a6a5e9`). One
  shared guard, `calibration_pipeline.serving_guard_reason`, now backs the
  auto-buyer (`calibrate_for_serving`), the NFL slates
  (`_nfl_serving_calibrator`) and the NBA total slate
  (`load_serving_calibrator`). Besides the existing non-identity /
  filename-match / `MIN_SERVING_SAMPLE` / OOF-improvement checks, an artifact
  must declare `fit_model_versions == [model_version]` and
  `fit_source` in {`settled_live_rows`, `point_in_time_replay`}. The pipeline
  writer stamps both; `scripts/calibrate_nba_v9.py` stamps
  `fit_source: retrospective_replay` (v9 replayed on games earlier models
  picked), which the guard refuses. The NBA total slate records the decision
  in its forecast output (`calibration`). The live NFL v7 artifacts are identity
  at HEAD and on disk, so nothing served changes for NFL. Tests:
  `test_calibrate_for_serving_leaves_untrusted_calibrators_unapplied`
  (provenance cases), `test_load_serving_calibrator_applies_the_same_guard_for_slates`,
  `test_pipeline_artifacts_declare_own_model_fit_provenance`.
- **`nba-moneyline-v9` artifact is an unqualified placeholder** (`df48e07d`).
  Rolled back from serving 2026-11-10 (`4d5624ed`, "placeholder coefficients")
  but its artifact still claimed `qualified: true`, `locked_holdout: true`,
  Brier 0.1945. Flat ledger 11-08..11-10: 8 of 11 settled rows priced the model
  below the market ask (54% vs 87%). The qualification block now says
  unqualified/unverified with the rollback note; `artifact_hash` recomputed;
  the auto-buyer `DEFAULT_WHITELIST_MODELS` drops it (`nba-elo-trend-lr-v4` is
  deliberately NOT added — operator decision, HANDOFF item 3). Registry test:
  an `unverified` entry may not carry a qualified artifact claim.
- **NBA forecast path** (`83b76fc7`). (a) MODEL_REVIEW #3, moneyline path: the
  Main lr-v4 row created 2026-11-11T05:51Z carries `reason_code QUALIFIED`,
  `record_type QUALIFIED_SHADOW_CALL` although the champion had been
  `historical_only` since 11-10 17:42Z and `_downgrade_unserved` had marked it
  `PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED`; `ledger.py`'s Main branch
  (`ccc842b6`, 2026-10-23) relabelled every row it received as QUALIFIED. Every
  candidate is still a CALL pick on Main (operator directive 2026-10-11), but a
  non-qualified eligibility now goes through `enforce_universal_paper_call`
  and keeps its reason code and research `record_type`; only a qualified one
  is promoted. (b) #8d: `_select_nba_total_market` shares
  the spread selector's freshness window + latest-per-slug dedupe
  (`_fresh_latest_per_slug`); the slate passes `as_of=observed_at`. (c) #14:
  `UpcomingGame.neutral_site` is set from ESPN `neutralSite` + the
  international-venue heuristic for NBA spread/total slates, and both
  basketball engines drop Elo HCA and trend home-court on neutral floors.
  Tests: `test_main_ledger_keeps_a_downgraded_paper_call_unqualified`,
  `test_nba_learned_downgraded_call_reaches_main_as_a_paper_call_not_qualified`,
  `test_select_nba_total_market_ignores_old_balanced_quote`,
  `test_nba_v9_neutral_site_removes_home_court_from_both_halves`.

Still open from the review (NBA): #10 (v9 logistic s=11.8 vs normal sd 14 — the
v9 moneyline engine is not served, needs one ablation before it becomes the
challenger), LOW items (integer-line push mass, `BasketballModel` constructed
then discarded in the total slate, availability wiring hardcoded to v7/v9).
Operator decisions: whether the nightly calibration pipeline may change a
serving transform without a promotion record (it now can only do so with its
own-model provenance, but it still does so unattended), and whether
`nba-elo-trend-lr-v4` belongs on the buyer whitelist.

Operational note found on the way: supervisor log files under
`E:\model-prediction-runtime\logs\supervisor\` are zero bytes for every recent
daily run on this Windows host, completed or failed (two of the last eight
exited 1 with nothing to explain why). Not fixed here.

## 2026-11-12 — NCAAB model review: total baseline mismatch, artifact drift, moneyline coherence, early-season compression (quick-261112-nc6)

Closes the NCAAB findings of `docs/MODEL_REVIEW_2026-11-11.md` (#7, #9, #10, #11,
LOW push mass, cross-cutting test gaps #1/#4). Full write-up with the validation /
holdout tables: `docs/NCAAB_V6_REVIEW_FIXES_2026-11-12.md`; harness
`scripts/ncaab_v6_review_ablation.py`; report `outputs/ncaab/ncaab_v6_review_ablation.md`.
Both champions are fixed in place under the same version string (NFL v7 precedent);
every fix is a constructor parameter whose default reproduces the old model, and the
control variant reproduced all 68 live Flat rows of the 05:40Z daily (0 deltas)
before any candidate number was read.

- **#7 total**: `hl10` was shrunk toward the league mean and then divided by the
  *conference* baseline, so identical teams projected ~14 points apart by label; the
  advertised `w_league` was `1 - 0.85 - 0.15 = 0`. `TrendEngine` gained per-team
  `offense_baselines`/`defense_baselines` (None = byte-identical); the total model
  shrinks toward the conference baseline and measures pace against the league mean;
  `league_total_weight` is explicit (0.0 — 0.15 was worse on validation).
  Validation MAE 14.30 → 13.78, holdout 15.11 → 14.48. `total_sd` 13.5 → 16.5
  (PIT coverage 0.40/0.68/0.81 → 0.49/0.82/0.92 on validation).
- **#9 artifacts**: `ncaab_spread_v6_model()` / `ncaab_total_v6_model()` now build
  from the hash-verified `config/models/ncaab-*-v6.json` via `from_artifact`; the
  artifacts list every served parameter (retention 0.35 as served, not the 0.2 the
  file claimed) and drop `confidence_threshold`/`min_edge`/`min_sample_games`
  (operator policy lives in `config/model.yaml`; nothing read them). `max_edge` and
  `dynamic_sizing` are consumed by `compute_dynamic_units`; the forward path no
  longer hard-codes 0.08. Consumed-keys and serving-entry-point parity tests added.
- **#10 moneyline**: P(home) is read off the Student-t margin distribution
  (`coherent_moneyline`). Validation Brier 0.2058 → 0.2020, log loss 0.5964 →
  0.5877, intercept −0.28 → +0.04. The review's second suggestion (match the t scale
  to the SD) was tested and **rejected**: scale 10.5 already covers 0.51/0.81/0.91;
  matched gives 0.44/0.74/0.87.
- **#11 compression**: `tiered_trend_priors` (trend half shrinks toward the
  conference-tier strength) and `additive_matchup` (`offense + opp defense − league
  mean`; the old average halved every strength gap even at full sample). Margin MAE
  9.046 → 8.844 with HCA re-selected on validation (home_court_points 7.2 → 5.0,
  Elo HCA 55 → 45; bias −1.92 → −0.87). The sample-scheduled Elo weight is
  implemented but off: inert on validation, and on the holdout it gives back part
  of the priors' gain.
- **Push mass**: integer spread/total lines exclude the (line ± 0.5) band from both
  sides and report `push`; half-point lines are unchanged.
- **Holdout caveat**: 657 of 742 holdout games are the 2026-11 early-season window
  (home win rate 84.6%, realised margin SD 22–24 vs served ~15). All variants
  under-disperse there; the coherent moneyline is worse than the legacy logistic in
  that regime (Brier 0.1217 vs 0.1112) and better everywhere selectable. Main does
  not trade that regime (sample gate ≥ 8); Flat does. Not selectable on validation
  — left open in HANDOFF §1 with the early-season volatility cap and retention.
- Rollback: revert the `quick-261112-nc6` commits; the pre-fix artifacts are in git.
## 2026-11-11 — NFL model review: efficiency inputs silently defaulted since 10-27, forecast-path integrity, weather, calibration replay

Full review of the NFL spread/total champions (`nfl-spread-structural-v7`,
`nfl-total-structural-v7`) plus the forecast path, triggered by a four-sport
model review. Every finding below was reproduced against the live runtime
ledger or live snapshots before it was fixed; every fix carries a regression
test that was shown red without the fix.

**Defect window: 2026-10-27 → 2026-11-12.** Every NFL v7 pick in that window was
priced on league-average efficiency inputs. The 10-27 snapshot cutover
(`6d95a709`) built `team.parquet` with four columns; `_team_efficiency` reads
`sacks_suffered` and a dozen more, hit `ColumnNotFoundError`, and its broad
`except` returned the defaults tuple for every team (reproduced on the
`20261111T220940` snapshot: KC/BUF → `prior_games 0`, all defaults).
`input_qualified` stayed True. Operator decision 2026-11-11: the 52 Main rows
from that window are left in the ledger untouched; performance reviews should
exclude or caveat NFL v7 rows with `event_start_utc` in that window.

Fixes, one commit each (`03faf30d` → `37084668` + calibration wiring):

- **Fail closed on an unusable efficiency frame** (`03faf30d`). `TEAM_COLUMNS`
  is now the engine's full read set and is imported by the engine as its
  required-column contract; `_team_efficiency` raises
  `NFLEfficiencyInputError` (a `RuntimeError`, so no broad handler can turn it
  back into defaults); both NFL slate builders decline the game (NO_CALL).
  `def_sack_rate` now reads `sacks_suffered` on the opponents' rows (this
  team's pass rush) instead of `def_sacks` (the opponents' rush). Existing
  snapshots lack the new columns, so the next daily capture restores forecasts;
  until then NFL declines rather than defaults.
- **Forecast-path integrity** (`9cea0b73`, `e44b7117`): Main gate now requires
  `record_type == QUALIFIED_SHADOW_CALL` (21 Main rows had reached Main as
  research-state rows relabelled QUALIFIED); total market selector takes the
  latest two-sided quote per contract (a stale 05:52Z capture could beat the
  18:10Z one by looking more balanced); `input_qualified` tests ordering/age
  instead of same-calendar-date (the 00:30 ET run could never qualify);
  `inference_inputs.sd` stores the effective sd (heavy-line spreads failed
  scalar replay and vanished into `skipped`). The shared basketball spread
  helper is deliberately unchanged — a pinned test documents that downgraded
  WNBA/NBA spreads do reach Main as research-only paper calls.
- **Consensus labelling** (`5ffc2b40`): ESPN-consensus-priced rows (31 of 52
  Main v7 rows) stay Main-eligible by operator decision but now carry
  `quote_provenance` and say so in the rationale.
- **Artifact hygiene** (`4dd3034e`): dead parameter keys removed from both v7
  artifacts (hashes recomputed); a recording-dict test asserts every artifact
  key is consulted by `from_artifact`.
- **Weather** (`37084668`): ESPN carries no NFL wind/temp, so every outdoor
  total had been priced at a 5 mph / 65 °F placeholder (all 69 outdoor rows).
  Open-Meteo pregame capture for the 32 NFL venues joins the daily capture
  pool; unavailable means no weather term, flagged in `feature_basis`.
- **Serving calibrator**: both slate builders route through the same guarded
  calibrator hook the auto-buyer uses (identity → raw served).

**Replay and calibration** (`scripts/replay_nfl_v7_all_settled.py`, report in
`outputs/nfl/nfl_v7_replay.md`): the fixed engines were run point-in-time over
every completed game with nflverse closing lines from 2021 (1,489 games, 0
failures) and over all 110 settled Flat spread/total picks at their own lines
and asks (0 skipped). Result: **the engines sit at the closing line** — spread
Brier 0.2496 (market 0.2500), total 0.2506; totals are over-confident
(calibration slope 0.43 with recorded weather, 0.19 without) and the >60%
tail is inverted (14 games at mean 61% hit 29%). The walk-forward tournament
(select on first 80%, locked last 20%) picks **identity** for both markets —
no monotone recalibration recovers information that is not there — so the
serving guard leaves the raw probability in place. At the live 5% hurdle the
spread engine would have fired 16 times in 1,451 games (hit 37.5%). On the
Flat picks the fixed model's probabilities sit *below* the recorded asks on
average and fire 8 times (6 wins). The artifacts' `qualification.replay`
blocks now carry these numbers; `config/production.yaml` still says
`predictively_qualified` for both — that label is contradicted by this
evidence and is an operator decision to change.

Inherited test failures not touched (all reproduce at pre-session `5e2212ef`):
`test_cli.py` hard_edge_gate (v5 fixture unregistered), WNBA
`_DuplicateLedger` stub lacks `.rows`, 7 soccer/tennis/MLB ledger-mirror
tests, `test_five_blind_spot_guards.py::test_nba_spread_extreme_disagreement_stays_in_flat`,
and the `tests/sports/nfl/*` end-to-end tests that assert the retired v5
version string.

## 2026-09-09 — Complete learned replay inputs and immutable WNBA priors

Root cause found in the recording path: WNBA probabilities can change after
the fitted artifact via player-availability adjustment, but the row omitted
its points gap, margin sigma, and transform state. `learned_replay.py` now
captures and validates the complete artifact, dynamically named inputs, base
probability, served probability, and transformation. `PickRequest` and the
ledger preserve a JSON snapshot; feature payload v2 retains it through
settlement and export. The incumbent evaluator replays it when present.

`build_and_save_priors` previously overwrote each day's player-prior file.
Retained August 23/24 files are later than the mismatched decisions, so exact
historical recovery remains blocked. Future writes preserve old/new versions
by content hash and atomically update the compatibility view. The as-of loader
reads those snapshots and rejects same-timestamp conflicts. All 22 other
incomplete WNBA contexts lack `defensive_trend_gap` during July 28–August 3.

Also fitted the new incumbent-anchored MLB residual v2; it has no reliable
gain and is not promoted. Verification: 203 focused tests; scoped Ruff/mypy;
repeat training reproduces model bytes and results. See
`docs/REBUILD_SPRINT_2026-09.md`. No historical ledger repair was applied.

## 2026-09-09 — Incumbent replay and quote lineage follow-up

The canonical source is `model-prediction-runtime/ledgers/ledgers.db`, opened
read-only. New `evaluate_research_incumbents.py` collapses mirror decisions,
rejects conflicting contexts, replays exact stored features, and gates paired
comparisons before ranking. Probability zero is valid; null is not 0.5.
Quotes require unique archive linkage, exact participants/start/horizon,
fresh pregame timestamps, actual asks/depth, and supported historical fees.

Found and fixed: learned moneyline quote matching returned a reduced object,
which forecast lineage hashed even though the archive held the full record.
The matcher now carries `_archive_record`; lineage hashes the archived object.
The regression checks the actual archive hash. Old hashes are reconciled only
through the exact known projection and only when one full record matches.

WNBA's two distinct probability mismatches remain unresolved and block its
comparison. MLB replay passes but its candidate offers no reliable predictive
gain; both common-policy simulations lose money. Five incumbent cohorts have
no decisions; fourteen need replay inputs/state and adapters. See
`docs/REBUILD_SPRINT_2026-09.md` and the verified output directory.
Final focused verification: 126 passed; scoped Ruff and mypy passed; existing
production artifacts/config hashes unchanged. No full-suite or profit claim.

## 2026-09-09 — Isolated all-market research generation

Added `research_generation.py` and `train_research_generation.py`, fitting 21
research artifacts with independent chronological train/select/calibrate/test
periods. The first attempt stopped NRFI before fitting because `parse_utc`
correctly rejected a date-only prior cutoff. Adding explicit UTC midnight fixed
the timestamp; the clean rerun completed all 21 with identical predictive
metrics to each first successful attempt. Previous attempts were preserved.

Tests cover two-day result embargo, future-result perturbation, outcome-neutral
tennis orientation, conflicting duplicates, complete-date split isolation,
calibration independence from test labels, artifact corruption, output overwrite
refusal, signed home spreads, and push handling. Final focused verification:
24 passed; scoped Ruff and mypy clean. Existing production config/artifacts
hash unchanged. No full-suite, PIT, incumbent-superiority, or profit claim.

See `docs/REBUILD_SPRINT_2026-09.md` and
`outputs/research/generation_20260909_v1_verified/{manifest,verification}.json`.

**Last audited**: 2026-09-08 (see new section directly below)

## 2026-09-08 — Project-wide optimization and integration

Shared XLSX exports are 4.36x faster in a three-run 1,000-row benchmark
(4.135s median -> 0.949s), with identical values and formatting. Ledger page
queries now have a matching sort index and a complete timestamp/pick/tier
cursor; concurrent same-key dashboard cache misses share one build. Daily
timing includes deferred exports. CFB cannot backdate started games, qualified
WNBA spread logging no longer references a nonexistent enum, and PAPER mode
cannot be overridden into live orders. Frozen artifacts were not retuned.

Verification on the combined checkout: 2,660 passed / 9 socket-restricted
failures / 3 skipped in the full sandbox run (336.36s). All 23 tests in the
HTTP integration file passed with localhost access afterward. The final
execution-boundary changes passed 171 focused tests. Ruff passes; mypy reports
no issues in 344 source files. No outstanding test failures remain after those
reruns. No live daily cycle or trading-service restart was used for validation.

The operator requested committing all existing work and cleaning Git. The
snapshot includes the pre-existing Auto-Buyer/manual-bet/soccer/CFB changes;
a follow-up cleanup removes mutable operational records from tracking while
preserving the files on disk and the snapshot in Git history. A verified local
backup also exists under model-prediction-runtime/backups/git-clean-20260909T000757Z/.

See [the optimization report](PROJECT_OPTIMIZATION_2026-09-08.md) and
outputs/optimization/2026-09-08/ for methodology and practical limits.

## 2026-09-03 — Auto-Buyer IOC partial-fill: rest the remainder, don't chase price

The LODIS order `C8SAMAA24MVP` requested 12.25 shares at $0.51 for a $6.25
target, but the live exchange order expired after filling only 0.88 shares.
The authenticated portfolio reported $0.4488 base cost plus $0.0090 fees,
explaining the roughly $0.46 position visible in Polymarket. The old
Auto-Buyer ledger incorrectly recorded the requested quantity and cost as if
they had filled — confirmed live across the same 04:38 UTC batch: two other
orders (`aec-atp-benbon-ignbus`, `aec-cs2-ntr-ef`) filled 0% while their
ledger rows showed full requested cost, and one (`aec-cs2-vexar-bge`) filled
~52% while its ledger row showed 100%.

An earlier version of this fix chased the ask with a second marketable IOC
up to 3 cents above the original price. That is the wrong design and was
reverted (2026-09-03, same day) — a fallback should never pay more than the
user-confirmed price to force an immediate fill.

**Current design.** Auto-Buyer IOC orders now reconcile actual fill quantity
via the exchange's `cumQuantity`/order state (with a GET refresh if the
synchronous response doesn't carry a fill count). If a buy order partially
or entirely fails to fill, the unfilled remainder is placed as a *second*
order — but that order is a resting **GTC limit at the exact same price**,
never a marketable order or a chased/higher price. It waits on the book for
a seller instead of paying up. This is opt-in per ticket
(`OrderTicket.ioc_fallback_resting`); Auto-Buyer always sets it, a plain
manual `execute` does not unless asked. Ledgers and daily-spend accounting
use the confirmed *filled* quantity/cost from the primary order only — the
resting remainder isn't assumed complete and is tracked separately
(`fallback_order_id`) for later reconciliation once it actually fills.

## 2026-09-02 — Auto-Buyer settlement resilience and performance cohorts

**Settlement audit.** A read-only rehearsal copied the 102-row live ledger to
an isolated temporary directory and ran the real settlement function with
authenticated exchange positions plus ESPN/Polymarket market resolution. The
first pass changed three rows: one newly resolved CS2 position and two
already-settled cost-basis normalizations whose win/loss results did not
change. It ended at 82 settled / 20 pending. An immediate second pass returned
`changed: 0` at the same counts, proving idempotency for that snapshot. The
production ledger was not targeted by either pass.

**Failure found and fixed.** The first sandbox rehearsal exposed an uncaught
`httpx.ConnectError` in the already-settled tennis re-verification branch. A
transient ESPN/DNS outage could therefore abort the entire settlement request.
That branch now treats `httpx.HTTPError` like the other per-row source failures:
the settled record remains unchanged and the pass continues. A regression
pins the fail-closed behavior.

**Performance view.** The Auto-Buyer page now has Ledger and Performance
sub-tabs. Performance uses only terminal win/loss/push rows, groups event dates
in `America/New_York`, and shows day-by-day All Auto-Buys, MLB Models, and
Without MLB cohorts. Empty cohorts render unavailable ROI instead of a false
zero. MLB membership is based on MLB sport or an MLB model ID; pending exposure
never enters record, P&L, stake, win-rate, or ROI denominators.

**Unit control.** Auto-Buyer now has its own persisted `1U = $` control rather
than silently sharing the dashboard's general unit value. This separation is
intentional even though both values are currently $5.00: changing either one
does not silently change the other. A confirmed Auto-Buyer change updates
`auto_buyer_state.json` and future Auto-Buyer cycles read that value. Risk
limits are expressed as 5U per game and 50U per day, so their
dollar equivalents scale with `1U`: at the current $5.00 setting they are
$25/game and $250/day. The confirmation dialog shows both unit and resulting
dollar limits before applying a change. Settlement uses each row's recorded
`unit_value_usd`, so existing orders and historical P&L units are not restated.

**Snapshot review (2026-09-02 15:40 ET).** Across 85 settled rows, All was
47-38, +$4.82 (+9.64U), +10.6% ROI. MLB was 6-8, -$1.00 (-2.01U), -14.4% ROI;
Without MLB was 41-30, +$5.82 (+11.65U), +15.2% ROI. The damaging MLB subset
was moneyline (1-5, -$2.215, -69.4% ROI); MLB non-moneyline was 5-3, +$1.21,
+32.0% ROI. This supports the categorical MLB moneyline Auto-Buyer block while
not supporting a blanket block of all MLB markets. This is a small observational
sample, not promotion-grade evidence.

## 2026-09-02 — Auto-Buyer settlement dialog false `Still pending: 0`

**Symptom.** The settlement dialog reported `No positions changed` and
`Still pending: 0` while the same rendered Auto-Buyer ledger showed 28 pending
positions. The dialog was not using the table's authoritative post-settlement
classification.

**Root causes.** The backend returned a traversal counter that could omit open
records on early-exit branches such as an invalid/missing event start. The
frontend then compounded this by never checking `res.ok` and rendering
`Number(data.pending || 0)`. Any HTTP 500 or response without `pending` was
therefore converted into a confident but false zero.

**Fix.** Settlement now computes `remaining_pending` from the fully rewritten
post-settlement ledger using the same shared terminal-record predicate as the
performance summary. The compatibility `pending` field carries that same
authoritative value. The dialog requires `remaining_pending` to be a valid
non-negative number and surfaces HTTP/backend errors instead of substituting
zero.

**Verification.** Revert-verified regressions cover an ungradeable open row and
the frontend response contract. The three affected test files produced 42
passes in the sandbox; their sole unrelated CFB live-ESPN test passed when
rerun with network access. Ruff and `git diff --check` passed. After restarting
the dashboard, Dia showed the live Auto-Buyer KPI and Pending/Open tab agreeing
at 25 pending positions. No settlement POST was issued during verification.

## 2026-09-02 — MLB moneyline categorically removed from Auto-Buyer

**Risk found.** Removing MLB model IDs from the default whitelist was not a
complete execution control. The persisted live state still contained
`mlb-elo-trend-lr-v8`, `mlb-structural-v10-frozen`, and `mlb-nrfi-v2`, and a
future renamed MLB model could be whitelisted again. A model-ID-only block
therefore did not express the operator's actual instruction: do not auto-buy
the MLB moneyline market.

**Fix.** `portfolio/auto_executor.py` now enforces an operator-level
`("mlb", "moneyline")` block after model-whitelist admission and before quote
lookup or order construction. The same predicate excludes blocked rows from
live snapshot preflight. It normalizes case and the `ml` alias, and infers MLB
from an `mlb-` model ID if a malformed row omits its sport. The status payload
exposes `disabled_sport_markets: ["mlb:moneyline"]`; run summaries count these
rows separately as `rejected_disabled_sport_market`.

**Scope.** This does not remove MLB moneyline forecasts from research or ledger
views, does not cancel existing exchange orders or positions, and does not
block MLB NRFI or another sport's moneyline. No Auto-Buyer cycle was triggered
during deployment.

**Verification.** A regression test proves that an explicitly whitelisted,
previously unseen MLB moneyline model is rejected before quote lookup while a
whitelisted tennis moneyline in the same batch proceeds. The existing MLB NRFI
test remains green. The complete Auto-Buyer test file passed (`16 passed`),
Ruff passed on both touched Python files, the dashboard LaunchAgent was
restarted, `/api/health` returned `ok: true`, and the read-only Auto-Buyer
status endpoint reported the categorical block with Auto-Buyer still enabled.

The broader parallel suite executed 2,538 tests but did not produce a clean
exit: it reported 2,524 passed, 3 skipped, and 11 failed before the remaining
worker stalled and was interrupted. All 11 failures passed on an exact rerun
with sandbox network/socket restrictions lifted. Two CFB tests make live ESPN
requests and nine dashboard tests bind loopback sockets; this is test-isolation
debt, not evidence of a product regression. The 2026-08-31 full-suite baseline
therefore remains the latest clean full-suite verification.

## 2026-09-02 — The Odds API removed; MLB market-odds fallback moved to keyless ESPN pickcenter

**Symptom.** `THE_ODDS_API_KEY` had been 401 for 30+ days (documented since
2026-08-27 in `SYSTEM_DEFECTS_AND_GAPS_AUDIT.md` B), but the client was still
being constructed and called live every day: `_forecast_mlb` (the function
behind the live `measured-edge-margin-v3` MLB spread champion) built a
`TheOddsAPIClient` and called `.odds("MLB")` for the whole slate on every
forecast run. Every game that didn't get an exact Polymarket BBO match fell
through to this dead fallback, appended `the_odds_api:HTTPError` to its load
errors, and (if Polymarket also had no match) surfaced as
`NO_CALL_MARKET_UNAVAILABLE (...,the_odds_api:HTTPError)` — a recurring,
user-visible noise source with no real capability behind it.

**Fix.** `MLBMarketOddsFeed` (`data_sources/mlb_market_odds.py`) no longer
takes an `odds_api` client at all. Its fallback is now
`_from_espn_market()`, which calls `ESPNClient.summary("MLB", event_id)` —
keyless, no quota, and resolvable directly by `event_id` (already threaded
through `build_mlb_slate`), so the old day-batch fetch + team-name-matching
step is gone entirely. ESPN's `pickcenter` odds were already parsed
elsewhere in this codebase (`parse_pregame_and_closing_markets`, used for
historical market ingest in `ingest.py`) — this just reuses that parser live.
Provider tag renamed `draftkings_via_the_odds_api` → `espn_market_consensus`.

**Removed entirely** (not just disconnected): `data_sources/the_odds_api.py`,
`data_sources/odds_soccer_scores.py` (the soccer scores path was already
DORMANT since 2026-08-26, superseded by `api_football.py`, and not called
from the live daily pipeline — confirmed via `grep` before deletion), their
two test files, the `the_odds_api` entry in `source_policy.py`, and the
`THE_ODDS_API_KEY` export from `data_sources/__init__.py`. `config/model.yaml`'s
`odds_fallback` block updated to describe the new ESPN path instead of the
dead one.

**Closes two standing repair-order items**: "rotate The Odds API key" and
"Odds API key-tier check + operator decision on paid historical access" are
now moot — there is no code left that reads `THE_ODDS_API_KEY`.

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_mlb_market_odds.py -q
.venv/bin/ruff check src/ tests/
```

## 2026-08-31 — KBO/NPB stale open rows: not a slate-builder bug, a postponement the source never scores

**Symptom.** 34 open KBO/NPB research rows, the oldest 29 days past its start,
none of which any settle run could ever close. `stale_open_rows` flagged them;
nothing said why.

**The hypothesis that was wrong, and why disproving it mattered.** The standing
theory (carried in the 2026-08-31 handoff) was that
`forecast_international_baseball_slate` was emitting wrong dates or flipped
home/away — the KBO cache had zero games on 2026-08-05 and `Doosan Bears vs NC
Dinos` appeared only on 08-18..20. If that had been true, voiding would have
treated the symptom and the next slate would have regenerated the same rows.
It is false. Checked against the cache, every one of the 34 rows matches a real
scheduled game **in its own orientation** — 0 flipped, 0 date-shifted.

**Root cause.** The official sources list a postponed game forever with no
score: KBO renders a bare `<span>NC</span><em><span>vs</span></em><span>두산</span>`
play cell with an empty relay, NPB a `*` placeholder. `parse_kbo_rows` correctly
drops those (they are not results) — but nothing distinguished them from a
genuine lookup miss, so `find_international_baseball_result` returned `None` on
every run, forever. Live count for August 2026: **44 of 130 KBO rows** are in
this state, including *every* game on 08-05..08-09 and on 08-28, which is
exactly the set of dates the stale rows sit on. Makeup games are replayed under
a new `game_id` on a new date, so the original row is never scored.

**Fix.** Make "no result will ever exist" first-class rather than inferred from
an absence: `parse_kbo_unplayed_rows` / `parse_npb_unplayed_calendar` and
`international_baseball_unplayed_index` report the scheduled-but-scoreless set.
`OfficialInternationalBaseballClient` grew `_kbo_month_payloads` /
`_npb_month_pages` so the played and unplayed parses share one fetch loop
instead of duplicating it.

**Repair.** `scripts/void_postponed_international_baseball.py` — identity-scoped
per `docs/SYSTEM_DEFECTS_AND_GAPS_AUDIT.md` D.1, dry-run by default,
re-verifies every row against the live source at run time, leaves anything
unverified open, and applies a 72h grace window because the source draws no
distinction between "postponed" and "not played yet". Run 2026-08-31: **18
voided** (15 kbo, 3 npb) to `settled/push` at 0.0000 units with a `void_reason`
naming the evidence; 16 held inside the grace window; **0 unverified**. NPB
fully clear. Backup taken and verified first
(`ledgers.bak-postponed-intl-20260831T0230Z.db`, 19,206 records).

**Caveat.** A future scheduled game renders identically to a postponed one —
the source draws no distinction — which is why the grace window is a
correctness requirement and not a convenience.

```bash
# prove the claim without mutating anything
env MODEL_PREDICTION_RUNTIME_ROOT=$HOME/model-prediction-runtime \
    MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite PYTHONPATH=src:. \
    .venv/bin/python scripts/void_postponed_international_baseball.py
# regression tests for the new parsers
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_international_baseball.py -q
```


## 2026-08-27 — Full-System Defect Audit, Serving Landmine Gating & Weather/Travel Reproduction

### 1. Reproduction Commands
- **Full Suite Parallel Run**:
  ```bash
  .venv/bin/pytest -n auto
  ```
  Result: `2414 passed, 3 skipped` in 43s.
- **Ruff Lint Check**:
  ```bash
  .venv/bin/ruff check src/ tests/ scripts/
  ```
  Result: `All checks passed!` (0 findings).
- **Mypy Type Triage**:
  ```bash
  .venv/bin/mypy src/model_prediction
  ```
  Result: 220 errors across 59 files (documented in [`docs/SYSTEM_DEFECTS_AND_GAPS_AUDIT.md`](SYSTEM_DEFECTS_AND_GAPS_AUDIT.md)).
- **MLB Weather & Travel Ablation**:
  ```bash
  env PYTHONPATH=src:. .venv/bin/python scripts/mlb_weather_travel_ablation.py
  ```
  Result: Control MAE `3.584296`, Candidate MAE `3.578813`, Gain `+0.005484`, 95% CI `[-0.000215, +0.011088]`, Verdict: `NO_PROMOTION`.

### 2. Verified Findings & Gated Landmines
- **MLB NRFI Live Serving Landmine — RESOLVED 2026-08-27 (commit `693744b`)**:
  - `config/models/mlb-nrfi-v1.json` reproduces `0.690572` holdout logloss via `MLBFirstInningModel`.
  - At audit time `_forecast_mlb_nrfi_flat` in `src/model_prediction/cli/forecast.py` still invoked the legacy hand-set `MLBNRFIModel()` — whose default `model_version` is the SAME string the validated artifact must use, so a registry-only promotion would have silently served the wrong model to Main.
  - Fixed: live point-in-time feature accumulator `models/mlb_first_inning_live.py` (starter-name-keyed, exact-match verified against the batch ledger on 7 real games across 3 seasons; parity test `tests/test_mlb_first_inning_live.py`, 1e-4 tolerance for one documented floating-point rounding-boundary artifact). Serving now fails closed on a missing/hash-invalid artifact and predicts with `MLBFirstInningModel.from_dict(artifact)`. Champion `MLB.nrfi` set; `blocked_workflows` now empty.
  - Updated 2026-09-01: Polymarket US does list a tradeable first-inning-run Yes/No market. The league summary endpoint omits it; `/v1/events/slug/<event>` exposes `baseball_team_first_inning_run` (Yes=YRFI, No=NRFI). The slate client now hydrates MLB game details and captures exact live quotes.
- **MLB Weather & Travel Research (backfill state)**: Open-Meteo archive raw hourly observations for ~5,800/5,899 venue-days live in `data/weather/mlb_historical.jsonl` (untracked operational output; script `scripts/backfill_mlb_weather.py` is idempotent/resumable). 52 venue-days were still HTTP-429 after two retry passes — one more `--workers 2` resume run clears most. Feature wiring lives behind `total_score.py`'s `real_mlb_context` flag (default OFF = byte-identical incumbent); verdict NO_PROMOTION (see commands above). Venue table: `features/mlb_venue_geocoding.py` (venue-name keyed, relocation-correct; one-off exhibition venues deliberately excluded).
- **The Odds API — historical access audit (superseded 2026-09-02)**: At this audit snapshot, `data_sources/the_odds_api.py` implemented in-season endpoints only and historical-tier access was unresolved. The client and key dependency have since been removed entirely; see the 2026-09-02 entry above. There is no longer an Odds API key or paid-tier decision to make.
- **WNBA Totals Pipeline Absence**:
  - No serving execution path wired in `cli/forecast.py` (`total_research_artifact` read only for dashboard strings). Model underperforms market lines due to limited boxscore history (MAE 13.12 vs 20.92).
- **Soccer Odds API Outage**:
  - Outage $\ge 31$ days (`401 Unauthorized`).
  - `src/model_prediction/data_sources/api_football.py` is implemented and awaiting `API_FOOTBALL_KEY`.

## 2026-08-26 (night) — continuation: documented-bug triage, Bet Better capture, WNBA parser root cause, postponed-game watcher

Every remaining open item from the morning/evening scan was triaged, and the
fixable ones were fixed (uncommitted at handoff of this section; commit
policy unchanged — none pushed):

- **WNBA morning-report parse failures — root-caused and fixed.** All 48
  morning/midday reports on 08-26 failed `parse_report_pdf` with "row is
  missing game/team context" while afternoon/evening reports parsed. Root
  cause: `_parse_coordinate_table` (wnba_injuries.py) runs a two-pass
  coordinate parse; the entry pass propagated date/time/matchup/team
  context ONLY through player rows. The morning layout prints the game date
  on a team-level "NOT YET SUBMITTED" row (no player, so not a player row),
  so the date never reached the first real player row and every morning
  report failed closed. Fix: the pass now walks ALL data lines in document
  order (context propagates from every row; only player rows emit entries),
  preserving the fail-closed raise for genuinely contextless rows and the
  pass-1 `not_yet_submitted` status. Verified against all 81 real 08-26
  reports (0 failures); two real PDFs saved as fixtures
  (`tests/fixtures/wnba/report-morning-not-yet-submitted.pdf`,
  `report-afternoon-submitted.pdf`); regression tests revert-verified
  (`tests/test_wnba_injuries_parser.py`). This closes the handoff's "WNBA
  availability snapshots stale 1.5 days" item: capture itself was healthy —
  every morning report had been failing to parse, so no snapshot existed
  for the feature to read, and the feature correctly degraded to
  NO_CALL_AVAILABILITY_UNAVAILABLE.
- **Soccer capture data_root split-brain — fixed before it fired.** The
  daily's `_collect_soccer` called `collect_soccer_scores(days_from=3)`
  without `data_root`, so it defaulted to `PROJECT_ROOT/data` while every
  sibling capture writes to the caller's `data_root` (the scheduler-
  resolved root under launchd). Harmless locally, divergent under launchd —
  and never manifest because API_FOOTBALL_KEY was never provisioned, so the
  step always exited at the no-key check. Fixed by passing
  `data_root=data_root` explicitly; comment records the bug class.
- **Bet Better model-feed capture wired (new research-only source).**
  `data_sources/bet_better.py`: keyless, no-account, CC BY 4.0 feed of a
  third-party model's win probabilities + real lines across mlb/wnba/nba/
  nfl/soccer (7 leagues)/wta. Live-verified 2026-08-26 (all 12 feed paths;
  1,317 picks captured in a smoke run; NBA/NFL/World Cup empty = offseason,
  captured as available=False so "provider empty" is provably distinct from
  "we never asked"). Day-bucketed snapshots via provider_capture with the
  licence/attribution in an envelope entry. Daily wiring:
  `step1e_bet_better_models`. Reference evidence ONLY — the provider mints
  no stable event ids, so the feed must never become a ledger identity, and
  its picks are model estimates, not bookmaker prices.
- **Statcast aggregates wired into the daily.** The build moved from
  `scripts/ingest_statcast_aggregates.py` (manual-only, 3 days behind) into
  `statcast_aggregates.py` with an explicit `data_root`; the daily runs it
  AFTER the capture pool (it consumes game_snapshots.jsonl the pool writes)
  as `step5e_statcast_aggregates`, fail-soft. Live rebuild: 57,057 pitcher /
  134,765 batter rows through 2026-08-26. Script kept as a thin wrapper.
- **Esports capture/pricing drift pinned.** `CAPTURED_UNPRICED_ESPORTS_
  LEAGUES = {COD, ROCKET_LEAGUE, OVERWATCH}` documents the 8-captured vs
  4-priced split; `test_esports_capture_and_pricing_sets_cannot_drift_
  silently` makes any capture/pricing change land in a named tier or fail.
- **Unknown Polymarket market types no longer vanish silently.**
  `_log_unknown_market_type` logs each unrecognized (league, type-string)
  once per run — the BTTS gap (soccer.py emits `market_type="btts"` but no
  BTTS PM type string has ever been observed; 3-day snapshot sweep shows
  only team_win/spread/total) stays documented until a live string appears.
  NBA/NFL: PM gateway leagues are operational and `BBO_CAPTURE_SPORTS`
  already includes them — capture flows automatically when seasons start;
  the gap is consumer-side. NRFI correction (2026-09-01): the earlier claim
  was based on the incomplete league summary feed. The game-detail endpoint
  exposes `baseball_team_first_inning_run`; exact Yes/No quotes are now
  captured as YRFI/NRFI. First-5-innings contracts remain distinct and must
  never be substituted for them.
- **World Cup settle-stall guard.** A league with no `_LEDGER_LEAGUE_TO_ESPN`
  entry now reports a failure ("no ESPN result path for league X") instead
  of pending forever (the 07-27 WORLD_CUP stall class). Regression tests in
  `tests/test_settle_guard.py`.
- **Registry-free ban enforcement wired.** `bans.py`'s name-based fallback
  existed (fixed 08-04) but `evaluate_esports_eligibility` never consulted
  a ban list (the 07-27 gap's second half). It now checks first when a
  `ban_list` is passed (optional — legacy callers unchanged); the forecast
  call sites still need to thread their `bans` object through to arm it.
- **mlb-v9-candidate-1 identity collision.** `forecast_mlb_v9_benchmark.py`
  recorded rows under `model_version="mlb-v9-candidate-1"` — the name of a
  VOID quarantined artifact. New rows record `mlb-v9-benchmark`
  (`V9_BENCHMARK_MODEL_VERSION`); the workbook keeps the family name for
  history continuity; historical rows untouched.
- **Postponed-game workaround (drain-minimal, operator-chosen).** Research
  confirmed PM's per-market contract: a postponed game's market STAYS OPEN;
  it resolves to the makeup result if played within two weeks of the
  original date, else last-fair-market-price (US boilerplate) or 50-50.
  ESPN issues a new event_id on reschedule. Repo behavior: the daily already
  auto-voids STATUS_POSTPONED/CANCELED rows (`void_postponed=True`), but
  tennis/KBO/NPB/edge-ledger rows and ESPN-dropped events pend forever. Built
  the zero-new-I/O option: `system_health._stale_open_rows` counts open rows
  >24h/72h past their frozen start (pure read-only sqlite query) and
  degrades health when the >72h bucket is non-empty. Live first run: 22 rows
  (kbo 13, cs2 7, soccer 3, npb 3, tennis 2) — now continuously visible.
  Shelved (heaviest): the multi-day ESPN reconciliation sweep; do it only as
  a targeted, identity-scoped, operator-approved re-grade when needed.
- **Daily-run timing instrumentation.** `scripts/run_daily.sh` now logs
  per-step wall-clock; `run_daily`'s report carries a `timing` block
  (total / capture_pool / settlement / post_settlement seconds). Today's
  runs ranged 6.7–22.9 minutes; the instrumentation exists so tomorrow's
  log shows which phase to optimize first.
- **Triaged as already-fixed by later sessions (records closed here):**
  KBO phantom-tie settlements (canonical sqlite has zero phantom 0-0
  pushes; the 3 KBO pushes are real 3-3 ties); tennis surface inference at
  forecast time (`_upcoming_singles_matches` already calls
  `_infer_tennis_surface` and threads `surface=` into UpcomingMatch); the
  stale WORLD_CUP conftest fixture (removed); the DD items from 08-20
  (pre-commit hook, dashboard_server split to 259 lines, TODO tracking) —
  all present. MLB totals absolute-run-environment signal remains the one
  genuinely open research gap (see repair order #2).



Full sweep, not a bug-motivated investigation: `pytest -q` (2,288 passed, 3
skipped, 0 failed), `ruff check .` (clean after fixing 5 pre-existing stray
f-strings in `scripts/audit_mlb_v9_feature_distribution.py`), `verify-chain`
(hash chain intact, `break_count=0`), `validate-totals`, `feature_model_audit.py`,
`check_rebuild_isolation.py`, `check_obsolete_paths.py`, `audit_ledger_pnl.py`,
dashboard `/api/health`, and the installed vs. checked-in launchd plist diff.

**Real findings, none auto-fixed (all need an owner decision):**
- **MLB v9 feature table has 13 dead (zero-variance) features** — see
  `docs/ROADMAP.md`'s Phase 5 "KNOWN ISSUE" callout for the full trace. Source
  data exists and is populated; the bug is in the feature-engine lookup, not
  absent data. This is the highest-priority finding — it means every v9
  ablation result touching starter/offense/bullpen/platoon families to date
  measured noise, not signal.
- **2 model artifacts have SHA-256 hash mismatches against their own embedded
  `artifact_hash`**: `config/models/archive/wnba-spread-baseline-v1.json` and
  `config/models/research/mlb-v9-candidate-1.json`. Both inactive
  (archived/quarantined), so no live-serving impact, but the provenance
  record is currently wrong for both.
- **3 model configs are orphaned** — never wired into `production.yaml`/
  `config/model.yaml` and not registered as challengers:
  `nfl-elo-trend-lr-v4-temperature.json`, `wnba-elo-trend-lr-v4-temperature.json`
  (both committed as intended challengers, per their own commit messages, but
  never actually hooked up), and `mlb-v9-candidate-1.json` (expected — it's
  quarantined).
- **`cs2-tiered-elo.xlsx` model ledger has a 22-row settlement backlog**
  (unambiguous, unapplied since the 2026-08-23 sync) and **6 settlement
  identity conflicts** (4 tennis-surface-elo-v1, 1 cs2-tiered-elo-v6, 1
  lol-tiered-elo-v6) where the same pick has two different recorded P&L
  signatures — `audit_ledger_pnl.py` correctly refuses to guess and leaves
  both classes untouched.
- **Installed launchd plist has drifted from the checked-in one**:
  `~/Library/LaunchAgents/com.modelprediction.daily.plist`'s
  `StartCalendarInterval` is a bare dict; `ops/launchd/com.modelprediction.daily.plist`
  wraps the same value in a 1-element array. Both are valid launchd forms
  (functionally identical), but the live file no longer byte-matches git.
- **`docs/RESEARCH_BACKLOG.md` still exists** despite this file's own
  "2026-08-13" note above (and the top-of-repo `CLAUDE.md`) recording it as
  merged into `docs/ROADMAP.md` and deleted on 2026-08-22; its own
  "Last Updated: 2026-08-23" header postdates the claimed deletion. Someone
  (or some backfill script) recreated it after the merge — worth a decision
  on whether to re-delete it or reconcile the two, since they can now drift
  apart silently.

**Confirmed non-issues (checked, not new):** `scripts/check_obsolete_paths.py`
still flags `data/archive/research-ledger-split-20260726T192729Z/cleanup-report.json`
— this is the exact pre-existing archived-artifact case
`tests/test_obsolete_path_checker.py` already documents as known, deferred
cleanup, not wired into blocking CI. Not a regression.

Uncommitted working-tree changes present at scan time (Polymarket 429
backoff, KBO/NPB timezone-of-"today" fix, ledger PnL sync, launchd
once-daily schedule) were reviewed, tested, and left as-is per the no-commit-
without-request policy — see the session's git status for the full list.

## 2026-08-26 (later) — WNBA total unblocked (artifact identity repair) + model-wide identity/hash sweep

**Bug found and fixed: WNBA total blocked at artifact resolution.**
Both daily runs on 08-26 (12:43Z, 16:35Z) logged the `wnba_total` step as
`status: blocked, reason: "exact serving artifact missing for
wnba-total-margin-v1", scheduled_games: 0`. The slate builder hardcodes
`model_version = "wnba-total-margin-v1"` (`cli/forecast.py:1157`) and loads
artifacts through `_load_exact_artifact_contract`, which requires
`config/models/<model_version>.json` whose embedded `model_version` matches
exactly. The artifact existed only as `wnba-total-score-ridge-v1.json` with
embedded `model_version: wnba-total-score-ridge-v1` — a three-way identity
drift that fail-closed the whole step.

**Fix (operator-directed name: `wnba-total-margin-v1`):**
- `git mv config/models/wnba-total-score-ridge-v1.json → wnba-total-margin-v1.json`;
  embedded `model_version` → `wnba-total-margin-v1`; `artifact_hash` re-signed
  via `production_registry.compute_artifact_hash` (self-verifies).
- `config/model.yaml` `total_research_artifact` → `config/models/wnba-total-margin-v1.json`.
- `config/production.yaml` `blocked_workflows` reason for `wnba-total-margin-v1`
  was stale ("no exact serving artifact exists for this model version") →
  updated to the real remaining gate: "checked-in artifact has no
  qualification.qualified; research-only pending over/under evaluation".
  Reason strings are stored/display-only (`_parse_blocked_workflows`), no logic impact.
- **Untouched on purpose:** `model_ledger.py` `MODEL_ID_BY_LEAGUE_AND_MARKET`
  keeps `("WNBA","total"): "wnba-total-score-ridge"` — ledger identity is a
  family name (NBA/NFL totals are also `*-total-score-ridge`), artifact
  version and ledger id are different axes; renaming it would split history
  into a new empty workbook. Rows already carry
  `model_version: wnba-total-margin-v1` correctly.
- The model remains **zero-unit research-only by design**: the artifact has no
  top-level `qualification.qualified` and `market_qualification` is
  `DATA_READY_PENDING_OVER_UNDER_EVALUATION`. No promotion was performed.
- Tests: the fail-closed test in `tests/test_wnba_totals.py` now mocks the
  loader instead of pinning the old filename; new regression test
  `test_wnba_total_artifact_resolves_under_slate_model_version` pins that the
  checked-in artifact resolves under the slate's exact version. WNBA 9/9,
  registry 12/12, ledger+canary 46/46.

**Model-wide sweep for the same error class (identity drift / hash failure /
unresolvable contract):**
- All 13 production models resolve (11 json_artifact via
  `_load_exact_artifact_contract`, 2 code-backed soccer/tennis); all champions
  resolve; blocked workflows resolve except `mlb-nrfi-v1`, which is
  documented-intentional (code-backed step runs `status: ok`, 15 candidates
  today; the block is Main-tier exclusion only). **No other model is blocked
  the way WNBA total was.**
- `config/models/research/mlb-v9-candidate-1.json` hash was re-hashed during
  the sweep and then **reverted** — `tests/test_artifact_hash_verification.py`
  pins the quarantine-annotation convention (`KNOWN_NON_VERIFIERS` +
  `test_candidate_hash_verifies_without_quarantine_annotation`): its
  `status`/`invalidation_reason`/`replacement` fields are post-signing
  annotations deliberately never re-signed, same pattern as the
  wnba-spread-baseline `_retired*` fields. This corrects the framing in the
  earlier 08-26 scan above: those two "hash mismatches" are test-pinned
  conventions, not broken provenance records. Whether the convention itself
  should change (e.g. re-sign on annotation) is an owner decision.
- Hygiene flags left untouched (challenger/rebuild or by-design):
  `challengers/mlb-two-head-real-features-v1.json` embedded `model_id:
  "mlb-two-head-v1"` ≠ filename (its hash verifies under a `sort_keys=False`
  variant); `market-residual-v1.json` embedded name is the code's dynamic
  `RESIDUAL_VERSION-identity-fallback` (diagnostic-only, fail-soft loader
  never compares names).

**New finding (owner decision, see ROADMAP Phase 21 KNOWN ISSUE):** the live
v9 benchmark (`scripts/forecast_mlb_v9_benchmark.py`) records picks under
`model_version: "mlb-v9-candidate-1"` but never loads
`config/models/research/mlb-v9-candidate-1.json` — it retrains from the frozen
cohort parquet via `mlb_evaluator`. The artifact under that exact name is
`status: VOID_INVALID_FEATURE_PROVENANCE` (`replacement: mlb-v9-candidate-2
TBD`). Functionally safe (voided artifact is not read), but any exact-contract
lookup of "mlb-v9-candidate-1" resolves to a VOID artifact, and the benchmark
rows' identity collides with it. Needs a rename decision on the research side.

**Test suite:** 2,298 passed, 2 failed — both failures were the two
test-pinned hash tests broken by the since-reverted re-hash; the affected
file re-verified 4/4 after revert.

## 2026-08-26 (evening) — resolution of the morning scan findings

Every finding from the morning scan was resolved or superseded:

- **13 dead v9 features — root-caused and fixed.** Four independent lookup
  bugs, all in how snapshot-driven engines build keys vs. how callers query
  them: (1) `features/batter_priors.py`'s loader read flat game fields while
  `game_snapshots.jsonl` stores nested `side.team_name` /
  `side.players[].batting` — every batter registered under an empty team key,
  so `evaluate_projected_team_offense` hit the league-prior fallback for all
  rows (4 Projected-Offense + 2 Platoon columns dead); (2)
  `features/bullpen_state.py` keyed reliever appearances by numeric team_id
  while `evaluate_matchup()` passes full names (3 Bullpen columns dead);
  (3) `features/mlb_v9_features.py` read `starter_k_bb_gap` while the engine
  returns `starter_k_minus_bb_pct_gap` (always the 0.0 default); (4)
  `scripts/mlb_v9_feature_table_v3.py` never supplied starter names, fixed
  via `load_probable_starter_index` (`mlb_v9_features.py:26-80`) reusing
  `validation.py`'s `(start_utc[:16], home, away)` crosswalk. Direct engine
  verification on a 200-game sample: all 19 previously-dead columns now have
  66–185 distinct values (before: exactly 1 each). The builder's per-game
  `platoon_matchup_gaps()` was replaced with the shared-engine equivalent
  (value-identical on 100 real games) — the rebuild drops from hours to
  tens of minutes. Regression tests: `tests/test_mlb_v9_feature_table_lookup.py`
  (9 pass, revert-verified). The v3 parquet rebuild completed (6,678 rows,
  hash 6b079f58cd82, pre-fix parquet backed up) and the distribution audit
  now **PASSES ALL SANITY GATES** (0 nulls, all 24 features non-zero std).
  Three pairs are documented construction-collinear in
  `audit_mlb_v9_feature_distribution.py` (`KNOWN_COLLINEAR_PAIRS`, printed as
  [KNOWN], never silent): `bullpen_freshness_advantage` /
  `bullpen_hl_advantage` (r=1.00000 — boxscore snapshots carry no reliever
  role, so every profile is MIDDLE_RELIEF and hl availability falls back to
  general availability) and `projected_*_gap` / `platoon_*_advantage`
  (r~0.9997 — the vs-hand filter needs per-batter PA by opposing-pitcher
  hand, which boxscore snapshots cannot attribute). Revisit if a pitch-level
  or roster-role source lands. **Ladder re-run on the rebuilt table
  (2026-08-26, 2,000-bootstrap date-cluster paired, locked holdout):**
  v3_baseline LL=0.6865/AUC=0.5589; `v3_starters` dLL=-0.00208 P_better=0.93
  **KEEP**; `v3_offense` dLL=-0.00120 P=0.89 INCONCLUSIVE (borderline);
  `v3_bullpen` +0.00038 (no signal); `v3_platoon` -0.00047 P=0.74;
  `v3_full` dLL=-0.00300 P=0.91 **KEEP** (AUC 0.5743). The pre-fix ladder
  measured all-null on these same families — the starter family is the
  first real v9 signal. Report:
  `outputs/research/mlb_evaluator/report_v3_20260826.json`; the evaluator
  gained `--parquet`/`--manifest` args and `v3_*` feature sets pinned to
  the v3 contract (`manifests/mlb_v9_feature_table_v3_eval.json`, cohort
  files `*_event_ids_v3.json`).**
- **2 artifact hash "mismatches" — confirmed deliberate non-issues.** Both
  re-verify exactly under canonical conventions when their annotation fields
  are excluded (archive/wnba-spread-baseline-v1: `_retired`/`_retired_date`/
  `_retirement_reason`; research/mlb-v9-candidate-1: `status`/
  `invalidation_reason`/`replacement`) — the never-re-signed evidence rule
  (`docs/FEATURE_MODEL_AUDIT.md`) covers both. The morning scan's claim that
  "the provenance record is currently wrong for both" was itself wrong.
  `scripts/feature_model_audit.py` now documents `KNOWN_MISMATCH_ARTIFACTS`
  and reports them as `known_hash_mismatch` instead of gaps;
  `tests/test_artifact_hash_verification.py` pins the conventions.
- **3 orphaned configs** — `nfl-`/`wnba-elo-trend-lr-v4-temperature.json`
  removed (zero references anywhere; inert per their own commit messages).
  `mlb-v9-candidate-1.json` stays: the quarantine is load-bearing for the
  fail-closed paired-shadow gate.
- **Ledger settlement backlog + identity conflicts — resolved.** The 22-row
  cs2-tiered-elo backlog was applied via `audit_ledger_pnl.py
  --apply-model-ledgers`; 27 further model-workbook settlements unlocked and
  applied; all workbooks timestamped-backed-up first. New
  `scripts/resolve_ledger_conflicts.py` resolves the 6 identity conflicts
  from canonical SQLite evidence (latest-settlement authority, lineage-backed
  reference, same-tier stale-survivor archive; result disagreements are never
  auto-resolved). `audit_ledger_pnl.py`'s economic signature is now
  stake-normalized (pnl per unit, 4dp) so tier-specific sizing variance is
  no longer flagged as a settlement conflict. One early resolution-run bug
  replaced four decision payloads instead of extending them; the original
  payloads were restored from the daily backup
  (`model-prediction-runtime/backups/ledgers.20260826T072220Z.db`) as audited
  update events. Final audit: 0 repairs planned, 0 conflicts, 0 anomalies,
  integrity ok. Tests: `tests/test_resolve_ledger_conflicts.py` +
  `tests/test_audit_ledger_pnl.py` (17 pass).
- **launchd plist drift** — installed `com.modelprediction.daily.plist` now
  byte-matches `ops/launchd/`; job reloaded, schedule verified (daily 08:30,
  no RunAtLoad/StartInterval), `tests/test_launchd_schedule.py` passes.
- **RESEARCH_BACKLOG.md resurrection** — deleted for real (git rm, staged);
  unique content (evaluation contracts, v1 control baseline numbers, Phase 23
  gate criteria, secondary-sports status) ported into `docs/ROADMAP.md`;
  repo references retargeted.
- **Production scheduler stall (new finding from the data-gap audit)** —
  `com.modelprediction.production` and `com.modelprediction.rebuild-shadow`
  were DISABLED in launchd (37h stall, undocumented; the canary health reason
  in system_health). Operator approved re-enabling: both jobs loaded, fired,
  and completed exit 0 on 2026-08-26.
- **NRFI model improvement (operator: "calling the same as fair market
  prices")** — two defects found: (1) `features/yrfi_nrfi.py`'s
  `LEAGUE_FIRST_INNING_RUN_RATE` was 0.52 — the per-TEAM half-inning mean
  mistaken for the per-GAME total (a 2x error that inflated every starter
  run-rate multiplier and deflated p_nrfi; corrected to the empirical 1.036
  measured over the 6,683 snapshots, pinned by test); (2) the v1 model's
  logit weights were hand-set, never fitted, and its first-inning component
  was full-game FIP scaled against that wrong constant. New research module
  `models/mlb_first_inning.py` builds a PIT chronological ledger with
  genuinely first-inning features (starter opponent-1st-runs/start, team
  1st-inning scored/allowed split by home/away half, park 1st-inning rate,
  starter rolling FIP/K%/BB%, top-of-order offense composite) and fits a
  walk-forward logistic regression (train-only priors frozen at the train
  window). Locked 1,337-game test window: logloss 0.691033 / Brier 0.248944
  vs incumbent mlb-nrfi-v1 0.694537 / 0.250691 and the fixed-vig market
  proxy 0.694982 / 0.250916; calibration error 0.0163 vs proxy 0.0309.
  Research script: `scripts/mlb_nrfi_first_inning_research.py` (report in
  `tmp/nrfi-first-inning-research.json`); tests:
  `tests/test_mlb_nrfi_improvement.py` (7 new, incl. PIT no-self-leak pins).
  Open follow-up: the repo captures no real Polymarket NRFI quotes (the
  ledger's mlb-nrfi-v1 market_probability is the model's own fair price,
  `sportsbook="model_fair"`) — capturing live NRFI market prices is the
  prerequisite for measuring true CLV/edge, and wiring the new module into
  the daily research path is an operator decision.
- **Data-gap audit (full per-sport/feature/model scan)** — key gaps needing
  follow-up: soccer results capture replaced 2026-08-26 — new
  `data_sources/api_football.py` (API-Football v3, fail-closed without
  `API_FOOTBALL_KEY`, provenance snapshots via `provider_capture`) wired into
  daily step1b + `cmd_collect_scores`; the dormant Odds path is documented
  fallback. Remaining after the key lands: live league-ID/AET/PEN verification.
  Still open: WNBA official availability snapshots stale 1.5 days (today's
  WNBA calls degraded to NO_CALL_AVAILABILITY_UNAVAILABLE); Statcast aggregate
  parquets 3 days behind (manual-only ingester, not wired into daily);
  market-snapshot lineage absent for 7,263 esports/soccer/KBO/NPB rows;
  NBA/NFL have zero odds sources wired (TheRundown shortlisted). Corrected
  false alarms: Polymarket DOES have ATP (and ITF) tennis markets as of
  08-26; the repo-local `data/ledgers/ledgers.db` (not `data/model_ledgers/`)
  is the frozen 08-23 artifact.

## 2026-08-24 — tennis settlement, ledger authority, lineage, and false-green repair

The tennis loss wall was a settlement defect, not measured model performance.
Full-match spread and total rows were graded from a binary match-winner result
(`0-1` or `1-0`) instead of summed per-set games. The exact persisted defect
signature identified 221 settled Main/Flat rows. Every row was resolved by its
exact ESPN event/competition ID before mutation: 205 were regraded from real
game totals and 16 retirement-dependent derivatives were voided. Results are
now 100 wins, 105 losses, and 16 pushes; aggregate P&L changed from
`-269.5966U` to `-82.5410U`. The binary derivative signature is zero and the
runtime hash chain remains intact.

Future tennis pricing is moneyline-only. Unsupported heuristic spread/total
and set/subperiod contracts are no longer emitted. Settlement uses per-set
line scores for derivatives, exact source identity first, and fails closed on
retirement, walkover, missing/misaligned line scores, or subperiod markets.
The logging boundary also caps any future validated tennis derivative slate at
one spread and one total per match, selected by pregame expected return only.
It cannot use the result to choose a survivor.

The canonical duplicate/exposure cleanup classified identity separately from
correlation. It archived 457 settled rows and removed 9 open rows through the
audited ledger APIs: 188 exact-contract refresh groups retained their latest
valid pregame observation, while 58 tennis derivative ladders retained one
pregame-EV-selected line per market family. Alternate lines remain distinct in
the full JSON archive; the live repair planner now reports zero refresh groups
and zero tennis ladders. A partial SQLite unique index prevents a second active
contract/model identity across processes.

SQLite is now the sole ledger authority. Application reads, exposure,
duplicate detection, settlement, and dashboard cache reads use canonical
SQLite rows. XLSX is a disposable projection with an explicit raw reader for
parity. Reconciliation follows authority direction and cannot tombstone a
canonical row because XLSX omitted it. All 22 tier/sport projections were
rebuilt and verify clean.

All 12,335 historical records now carry `ledger-row-features-v1` payloads.
Only stored decision-time fields were copied: 944 rows are `available`, 366
are `partial_with_unavailable_features`, and 11,025 are explicitly
`unavailable_not_recorded`. No feature or market value was invented.

Serving now requires the exact registered sport/market champion and exact
artifact. WNBA total, unqualified WNBA spread, MLB NRFI, and unqualified MLB
derivative workflows are explicit blocked/research-only health entries.
Material daily substep failures now produce a nonzero supervisor result after
fail-soft work completes. Production serving was not expanded.

The Flat MLB moneyline P&L anomaly had a separate cause. The confidence
downgrade wrote `units=0` and later settled `pnl_units=0`, while the dashboard
silently substituted a suggested 0.75--1.75U display size. That mixed a
30-game win-rate denominator with only five genuinely scored rows. The repair
was scoped to the exact canonical signature
`flat/mlb + NO_CALL_BELOW_LEARNED_CONFIDENCE + units=0`: 112 settled rows were
resized from their stored decision-time inputs and regraded, then 649 matching
model-ledger observations were synchronized from SQLite. The reported
08-22--08-23 slice is now 19-11, `+1.8975U`, `6.3% ROI` over 30.0U, with no
zero-P&L wins or losses. Timestamped SQLite/XLSX backups were created before
mutation. The repair script is dry-run by default, requires SQLite authority,
and refuses to run while the daily writer lock is held.

Every persisted forecast is now an explicit paper `CALL` with positive units,
including unqualified, low-edge, thin-history, and market-unavailable model
opinions. Trust and execution authorization remain separate: those rows keep
`record_type=RESEARCH_OBSERVATION`, so the universal paper-call label does not
promote them to executable or qualified status. An unverifiable quote is still
discarded and the flat row uses the explicit `-110` paper benchmark. The
dashboard no longer invents size for a settled zero-unit row, and system health
checks every settled binary row for a positive scoring basis and coherent
win/loss P&L directly in canonical SQLite.

The lock-protected universal migration changed 1,842 canonical active SQLite
records, 1,844 active XLSX projection rows across 13 workbooks, and 11,806
nonfailed model-ledger observations across 16 workbooks. All active stored
predictions now carry `CALL` plus positive paper units; all settled predictions
carry realized P&L computed from their stored entry economics. Open rows retain
a blank P&L field, meaning pending settlement rather than a fabricated zero.
Archived and removed lifecycle tombstones were intentionally excluded. The
post-migration dry run plans zero changes and reports zero unresolved rows.

The same investigation found a scheduler false green: the latest daily run's
MLB v9 forecast exited 1 after calling a removed `PickLedger.list_picks()` API
and then hitting a duplicate, but `run_daily.sh` ignored both v9 child exit
codes in its final gate. The benchmark now reads the public `rows()` API and
handles a concurrent duplicate idempotently; the shell gate includes settle,
v9 settle, ingest, daily, and v9 forecast exits. A live v9 rerun scanned ten
events, skipped all ten existing identities, and exited zero.

A fresh full supervisor run then finished with
`settle=0, v9_settle=0, ingest=0, daily=0, v9_forecast=0`; a separate
production refresh also exited zero and cleared its stale-prediction warning.
System health remains deliberately `DEGRADED` only for the five explicitly
unqualified serving workflows above. It must not be relabeled healthy until
those exact sport/market artifacts earn qualification.

The dedicated MLB v9 view had one remaining denominator leak. Four settled
model outcomes lacked a quote inside the normal 30-minute execution freshness
window, but each had an authenticated Polymarket snapshot known before the
forecast and before first pitch. Because the v9 flat benchmark contract is one
1.0U CALL on every recorded pick, its quote lookup is now explicitly
stale-tolerant while retaining provider, hash, knowledge-time, and pregame
checks. The four rows were restored as priced 1.0U calls and their settled P&L
was recomputed from those stored quotes. The generic dashboard cohort guard
remains in place so a genuinely unscored row cannot leak into betting
performance or render null economics as fake zeros.

The follow-up v9 model-ledger audit also found 71 stale economic observations
across the legacy and candidate workbooks. The lock-protected repair created
timestamped backups and synchronized all 71 observations to authenticated
prices known at forecast time. Sixty-three used quotes inside the normal
30-minute freshness window; eight benchmark observations use older but still
authenticated, pre-decision, pregame snapshots under the explicit all-CALL
flat-benchmark contract. A post-repair dry run plans zero changes in both
workbooks.

Five ambiguous model-ledger conflicts remain deliberately unmodified (three
tennis, one CS2, one LoL): multiple stored observations disagree on settlement
economics, so there is no safe identity-scoped correction without stronger
source evidence. They are evidence gaps, not candidates for automatic P&L
normalization.

Final verification after the universal-call and active-model discovery repairs:
2,287 tests passed, 3 skipped; changed-path Ruff and `git diff --check` passed.
The live API exposes 16 active model ledgers, zero backup models, 2,747 open
model predictions, and zero non-CALL decisions. Dia rendered the same 16-model
evidence table and the corrected v9 settled ledger at 18 scored games and
`+5.92U`. SQLite integrity and the 39,576-event replay chain passed during the
preceding repair verification. Whole-tree Ruff still reports five findings in
the pre-existing untracked `scripts/audit_mlb_v9_feature_distribution.py`,
which this repair preserved.

At the pre-repair audit snapshot, remaining evidence gaps were explicit, not
repaired with fake history: 3,240
active rows predate market-snapshot lineage, 40 active MLB rows lack an
artifact hash, and 11,398 canonical rows have no recorded feature values.
There are 232 open rows, including 24 more than 24 hours past start and 19
more than 72 hours past start. A broad settlement sweep was intentionally not
run without an identity-scoped result manifest.

## 2026-08-19 — prospective lineup capture, overnight wake planning, v9 isolating ladder

### The v9 ladder measured: a null result, at proper isolation

Seven new isolating variants each change EXACTLY ONE term against the v8
control (the pre-existing v9 variants moved two at once, confounded).
2,000-bootstrap date-cluster paired resampling, 1,389-row holdout:

| single change | ΔLogLoss | P(better) |
|---|---|---|
| park_factor → park_factor_pit | +0.00023 | 0.223 |
| trend → residual_trend | −0.00017 | 0.658 |
| starter_era → starter_kbb | +0.00044 | 0.391 |
| starter_era → starter_fip | +0.00120 | **0.025 (worse)** |
| bullpen_weakness → bullpen_fatigue | +0.00081 | 0.128 |
| + bullpen_fatigue alongside | −0.00003 | 0.515 |
| drop weather_factor | +0.00006 | 0.089 |

Scale reference: constant home base rate (0.5309) scores 0.6912 log loss;
v8 scores 0.6866. **All six v8 features are worth 0.0046 nats, AUC 0.567.**
Fixing the known park lookahead leak makes v8 slightly WORSE — the leak
was not buying spurious lift; the feature is simply weak.

Also corrected on operator review: the NB challenger's 203-game OOF win
never included gamma_poisson (only independent_poisson and skellam); the
3,513-game head-to-head that does include it shows NB losing (0.68844 vs
0.68813, P(better)=0.175). **NB is REJECT.** Closed experimental branches
on the measured evidence: `rest_disparity` (90% zeros), `probable_starter_era_gap`
(96% zeros, 100% in 2024-25), `starter_fip` (significantly worse),
`starter_kbb` (no lift). Report:
`outputs/research/mlb_evaluator/v9_isolating_ladder.json`.

Corrected plan: batter PIT priors first (`projected_offense_pit`,
participation weights from games preceding T only — no target-game order
in history; `confirmed_lineup_offense_pit` is a separate feature on the
prospective cohort), then reliever workload × quality. Predeclared
3-component batter family (offense/discipline/power), Beta-Binomial
shrinkage from the existing machinery. New features go into a versioned
`mlb_v9_feature_table_v2.parquet` with its own hashes; v1 stays immutable
as the evidence behind these nulls. Market benchmark reported only on the
identical 293-row timestamp-valid intersection, labelled
"market benchmark cohort — not model-selection cohort".

### Lineup capture — the one input that cannot be backfilled

`features/lineup_strength.py` always returned `unavailable_from_source`;
nothing captured who is actually playing tonight, and a completed boxscore
can never tell you what was ANNOUNCED pregame. Verified live: Stats API
`boxscore.battingOrder` is fully populated (9 per side) at Pre-Game/Warmup,
hours before first pitch. New `data_sources/mlb_lineups.py` captures it
with a hard PIT contract: timestamps stamped AFTER the HTTP response (the
KBO/NPB ordering lesson), `lineup_state` gating (`pregame` rows only are
decision-grade — a started game has a CONFIRMED order, which is exactly
what a pregame decision may not use; unknown statuses resolve to
`unknown`, never optimistically to pregame).

**Live data immediately caught a bug the fixtures missed** (the exact
failure mode CLAUDE.md warns about): the store's dedupe identity included
`observed_at_utc`, which differs on every capture by construction, so
nothing ever deduped — three runs of an unchanged 15-game slate wrote 45
rows (at hourly cadence, ~360 duplicates/day). Dedupe is now on
`content_hash`. But content dedupe alone lost confirmation evidence, so
rows carry `first_observed_at_utc` / `last_observed_at_utc` /
`capture_count`; a re-observation advances the last two on the row it
confirms (a lineup seen at 18:10 and reconfirmed at 20:10 is 20 minutes
old at 20:30, not 2h20m). `last_observed_at_utc` uses max() and never
moves backwards (daily + hourly writers, late retries); `merge()` rewrites
under an exclusive flock because a confirmation MUTATES a row and a torn
read-modify-write would lose lineups that cannot be re-captured. Legacy
rows hash on demand — no migration pass needed to read an old archive.
Archive migrated 16/16, 0 collisions.

### The sleep gap and its fix

launchd COALESCES missed StartInterval firings into one run at wake.
Sleeping through three hourly opportunities is not replayed — for the
lineup archive that is permanent loss, concentrated entirely in
late-starting west-coast games (systematic missingness in exactly the
variable a lineup model conditions on). Two-part answer, neither of which
keeps the machine awake all night:

- `scripts/plan_lineup_wakes.py` follows the slate (no generic repeating
  wake): one-time `pmset wakeorpoweron` events ~35 min before first pitch
  for games whose window falls in the sleep window, coalesced within 20
  minutes. `pmset schedule` needs root; the script is plan-only without
  it and must be installed as a root LaunchDaemon (or run under sudo) to
  self-manage. **Not yet installed — operator step pending.**
- `scripts/lineup_capture_quality.py` stratifies capture by local
  start-time bucket so the bias is measurable before a lineup model is
  trained. First report already shows it: all 5 captured games in the
  7-9pm bucket, ZERO 9pm+ coverage, median first capture 24.4 min before
  start. Note `game_start_utc` is the SCHEDULED start — a row can be
  legitimately `pregame` slightly past it during Warmup.

The collector records `scheduled_games` / `eligible_pregame_games` /
`games_with_lineup_posted` / `boxscore_unavailable` /
`decision_grade_capture_rate` at run time: the denominator cannot be
reconstructed later because the schedule is mutable.

### Verification discipline notes

Two planner tests initially failed; BOTH were test bugs, proven by
computing the timezone arithmetic directly instead of assuming the code
was wrong (05:00Z wakes at 00:25 EDT, outside the window; a next-UTC-date
game IS inside the search horizon). An empty plan is not proof of
correctness — the planner is pinned by 8 fake-client tests at a fixed
instant. The hourly job was verified by its log output (exit 0, clean
stderr, real JSON), not by `launchctl print`.

### Reproduction

```
# lineup collector (also runs hourly via com.vc.mlb-lineup-capture)
.venv/bin/python scripts/capture_mlb_lineups.py

# capture-quality report, stratified by local start time
.venv/bin/python scripts/lineup_capture_quality.py

# wake plan (plan-only without root; --apply under sudo)
.venv/bin/python scripts/plan_lineup_wakes.py

# tests pinning the above
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_mlb_lineups.py tests/test_plan_lineup_wakes.py -q
```

1903 passed, 3 skipped; ruff clean on all touched files.

## 2026-08-18 — model-ledger settlement stranded 63% of its own evidence

**The bug.** `model_ledger.settle_from_pick_row` matched the row to grade
using `_prediction_dedupe_key`, the APPEND-side key — which includes
`observed_at_utc` — and took a single `next(...)` match. The pipeline
re-forecasts an event several times as the line moves, writing one row
per forecast, so only the row whose forecast timestamp exactly equalled
the settled pick's ever graded. Every other row for the same finished
game stayed `open` forever. The function is documented "never raises",
so a miss was a silent no-op.

This is the same silent-evidence-starvation class the hook was added to
fix on 2026-08-02 (`ModelLedger.settle()` existed but nothing called it),
one layer down: the hook fired, but only ever for one row per event.

**Live impact, WNBA spread ledger** (`data/model_ledgers/wnba-spread-margin.xlsx`):
67 rows, 9 settled, 58 open — of which **42 were for games that had already
finished** (oldest 2026-08-13). Every graded event showed the signature
1 settled + N-1 orphaned. `compute_model_evidence` therefore ran hit
rate / Brier / calibration on 9 rows instead of 51. Three events
(401857143/144/151) had all picks `removed` rather than settled, so no
graded pick existed to copy from at all.

**The fix.** New `_event_settlement_key` — event/market/line/model/**selection**,
deliberately without `observed_at_utc` — plus `ModelLedger.settle_event`,
which grades every still-open matching row in ONE lock/read/write cycle
(N calls to `settle()` would rewrite the workbook per row and could leave
an event half-settled). `_prediction_dedupe_key` is unchanged: the append
side still needs the timestamp so a re-forecast is not dropped as a
duplicate.

`selection` is in the settlement key for a real reason: away +6.5 and
home -3.5 are opposite sides of one game and resolve oppositely. Event
401857151 has exactly that shape and grades `away → loss`, `home → win`.
Keying without selection would have written one of them backwards as real
evidence. No live row has flipped sides yet; this keeps a future flip
honestly open instead of silently inverted.

Operator decision 2026-08-18: `pnl_units` is written to every graded row,
not only the row that carried the stake. `compute_model_evidence` SUMS
that field, so a model's reported `pnl_units` now scales with the
re-forecast count — it is a per-prediction economic record, not a
bankroll total.

**Backfill.** `scripts/backfill_model_ledger_settlement.py` grades stranded
rows from the game's FINAL SCORE rather than copying another row's result,
which is the only way to reach rows at a line that was never staked and
events whose picks were all removed. It refuses to write unless it first
reproduces EVERY already-settled row's recorded result from the score —
that self-check is the whole safety argument. WNBA spread: 9/9 reproduced,
0 mismatched; 42 rows settled; ledger now 51 settled / 16 open (the 16 are
genuinely future games). Timestamped backup taken before writing.

**Evidence after the repair** — and the honest reading of it: 51 settled
rows cover only 14 distinct contracts across 12 events, because
re-forecast rows of one game are correlated repeats, not independent
samples. Per-row 27W-24L (52.9%); **per-contract 7W-7L (50.0%)**, Brier
0.2856 against 0.2500 for always-predicting-0.5. The spread model's
pricing math is correct (normal-CDF sign convention verified
independently); it simply has no demonstrated edge and nowhere near the
50-call minimum. Any minimum-sample gate must count distinct contracts,
not rows.

### Second finding: `last_10_total_avg` was a duplicate of `league_total_mean`

`total_score.py` built the feature named "both teams' last 10 game total
averages" as `last_10_avg = baseline` — literally the same value as
`league_total_mean`. Ridge split one weight across two identical columns
(both landed on -1.825776 in the 2026-08-18 WNBA refit), so the level term
pulled twice as hard in the wrong direction. Fixed to the real
point-in-time last-10 signal (per-team `deque(maxlen=10)` of combined
scores, appended only AFTER the row is built). Net effect across the four
sports that share the module: MLB 3.5503→3.5497, NBA 15.4187→15.3341,
NFL 11.2265→10.9961 all improve; WNBA 24.0187→24.5116, already broken by
a wide margin either way.

Still present and NOT fixed: `park_factor`, `weather_factor`,
`bullpen_rest_days`, `travel_distance` are hardcoded constants for every
sport (all four carry 0.0 coefficients) — baseball features in a shared
totals builder.

### Third finding: the totals verdict ignores the CI it computes

`total_score.py:238` sets `improved = model_metrics["mae"] < baseline_metrics["mae"]`
— a bare point estimate — while the same artifact stores a paired
bootstrap `mae_gain_95ci`. Current run: NFL is flagged
`improved_vs_baseline=True` on a gain of +0.6327 whose own CI is
[-0.0427, +1.5336], straddling zero. MLB passes on +0.0310, statistically
clear but practically negligible. Left as-is — the verdict rule is a gate
policy decision, not a bug to fix unilaterally.

### Reproduction

```
# settlement regression (verified to fail against the pre-fix key)
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_model_ledger.py -q -k settle_from_pick_row

# backfill, dry run (self-check must print "0 mismatched")
.venv/bin/python scripts/backfill_model_ledger_settlement.py \
  --ledger data/model_ledgers/wnba-spread-margin.xlsx \
  --scores data/historical/wnba_games_all.jsonl

# totals validation across all four sports
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli validate-totals
```

Full suite 1883 passed / 3 skipped; ruff clean. No promotion decisions;
champions unchanged.

## 2026-08-14 (afternoon) — launchd cutover to the supervisor + health truth source

Operator GO executed: all three launchd plists now invoke
`run_supervisor run <worker>` (old plists preserved as
`*.plist.pre-supervisor`; the legacy scripts remain as manual entry
points). Production + rebuild-shadow agents are LOADED (frozen since
08-11; the gap is left explicit, not backfilled) and the daily agent was
reloaded. Attended-run verification, all recorded in the supervisor:

- production: completed exit 0, counters {events_seen 2, predictions 2},
  production.db latest prediction advanced to 07:36 UTC, rows 646→650
- rebuild-shadow: completed exit 0, runtime shadow.db freshly written
- daily: completed exit 0 (full pipeline through the supervisor)
- system_health under the launchd env: workers all completed — the
  "never run under the supervisor" reasons are gone; the only remaining
  DEGRADED reason is soccer capture stale 11.7 days (provider key,
  deliberately explicit rather than blocking).

Real finding fixed along the way: a SIGKILLed supervisor (launchctl
bootout mid-run) can never write its own failed row — the next run for
the same worker now closes orphaned 'started' rows as failed with a
note (lease proves no live owner). Regression-tested.

Also: `require_probability_normalization` is now ENFORCED (stored
prediction pairs must satisfy |sum-1| <= 1e-6; violations are DOWN), and
freshness reads the canonical production.db (MAX(prediction_time_utc)
via mode=ro), never production_state.json — the state file remains only
for compatibility consumers.

## 2026-08-14 (noon) — data plane: RuntimePaths everywhere + ProductionPredictionStore (consolidation B)

**RuntimePaths extended to the incumbent control plane.** `runs_db`,
`production_db` (`production/production.db`), `production_state_file`,
`supervisor_log_root`, `research_root/research_db` resolve through ONE
resolver — `MODEL_PREDICTION_RUNTIME_ROOT` when set (the launchd jobs),
repo `data/` otherwise. `migrate_legacy_state()` carries the historical
repo-local files (runs.db, predictions.db, production_state.json) into
the runtime root exactly once, via copy-to-tmp + rename, never deleting
the legacy files. Every consumer (run_supervisor, model_promotion,
system_health, cli_production) now resolves through it — the 2026-08-13
split-brain class (writer and reader disagreeing on where state lives)
is structurally impossible.

**ProductionPredictionStore** owns `production/production.db`: narrow
API (start_run/finish_run, append_prediction with the identity key
`(event_id, model_id, market_type, horizon, decision_time_utc)` and
partial unique index, settle/void/supersede/error, record_decision,
record_market_snapshot, keyset-paginated reads + SQL aggregation,
xlsx as an explicit `export`). cli_production is fully cut over.

Two real bugs caught by the tests during the cutover:
1. **Schema-migration trap**: `CREATE TABLE IF NOT EXISTS` leaves a
   migrated legacy table untouched — the canary's historical
   predictions.db has no `market_type`/`horizon`/`decision_time_utc`
   columns, so every store write failed silently (fail-soft) against the
   migrated database. Fixed with `_ensure_column` migrations +
   `decision_time_utc` backfill from `prediction_time_utc` + indexes
   created only after the columns exist.
2. **Test-helper leak**: the cli integration helper monkeypatched only
   `_resolve_data_root`, so a full-suite run wrote fake predictions into
   the LIVE production.db. The helper now monkeypatches `_paths()`
   (RuntimePaths → tmp) too; the live db was verified clean afterward
   (all 2162 rows are real canary cycles) and the state file was
   restored to the committed real record.

The incumbent shadow ledgers (main/flat/research xlsx, written by the
live 3h daily pipeline and verified by the audit chain) are NOT cut over
to SQLite yet — that swap needs the operator's explicit go because the
live writer and `verify-chain` depend on the xlsx audit path; the store
+ research.db schema are ready for the parity/cutover phase.

## 2026-08-14 (deep night) — truthful health + atomic promotion/rollback (consolidation A-3)

**Evidence-based health** (`src/model_prediction/system_health.py`):
status is derived from what the system produced — registry contracts,
supervisor run rows, prediction records, and source capture — never a
file mtime. DOWN: primary contract broken or a worker's latest run
failed. DEGRADED: broken secondary models, skipped/never-run workers,
stale predictions, or a sport's capture going quiet 7-28 days (offseason
stays informational). Live check right now truthfully reports: production
+ rebuild-shadow never ran under the supervisor, canary prediction 122
minutes old, soccer capture stale 11.2 days (The Odds API key). One
real bug caught while wiring it: the historical JSONL files are ordered
by INGEST time, not event time (the 07-19/21 reconciliation batch landed
after the 08-13 games), so "newest event" is a max-scan, not the last
line.

**Atomic promotion/rollback** (`src/model_prediction/model_promotion.py`):
`production.yaml` gained an explicit `champions:` map (sport → market →
model_id) — the champion is what SERVES a sport/market; the `primary` is
what the canary predict cycle runs. `promote --new … --approved-by …
--evidence …` validates the candidate, freezes hashes, points the
champion, preserves the old champion as the new entry's rollback model,
writes the yaml atomically (tmp + os.replace), re-validates through the
registry before marking the record active, and logs a promotions row
(sport, market, old/new model ids + hashes, approved_by, evidence_id,
git_sha) in `data/runs.db`. `rollback --sport --market` restores the
previous champion in one command and closes the prior record.
8 tests each for health and promotion.

## 2026-08-14 (night) — run supervisor: launchd as a trigger, not the architecture (consolidation A-2)

`src/model_prediction/run_supervisor.py` gives every scheduled run one
protocol: unique `run_id`, per-worker fcntl lease (busy runs are recorded
as `skipped`, exit 75 — `daily_lock.py`'s convention), heartbeat thread,
subprocess execution with the output captured to
`data/logs/supervisor/<run_id>.log`, and an outcome row in `data/runs.db`
(SQLite, WAL). Worker→command mapping lives in `WORKERS` (daily /
production / rebuild-shadow); the launchd plists can now just invoke
`python -m model_prediction.run_supervisor run <worker>` instead of each
script re-deciding what "run" means. The workers themselves are
unchanged. `status [worker]` / `runs [LIMIT]` read the history — the
foundation the truthful-health phase reads instead of file mtimes.

7 tests in `tests/test_run_supervisor.py` (completed/failed/skipped rows,
lease contention, heartbeat advancement, per-worker filtering).

## 2026-08-14 (even later) — one production model registry (consolidation A-1)

The production stack had a split personality: `config/production.yaml` listed
13 models in `allowed_models`, but validation, health, and the predict cycle
only ever saw the single WNBA `primary`. Twelve "production" models were
never contract-checked, never resolved, never served.

**`src/model_prediction/production_registry.py`** is now the single source of
truth. Every entry carries `model_id`, `sport`, `market`, implementation
type (`json_artifact` / `code_backed_model` / `rating_engine`), artifact
path, verified hash, feature-schema version, enabled state, and rollback
model. Loading validates **every enabled entry** and fails *that model*
closed (`load_error` recorded, `resolve()` refuses it) — the primary's
failure stays a hard `ValueError`. Legacy v1/v2 configs (`allowed_models` +
`artifact_map`) still derive entries, with sport/market identity coming
from each artifact's own fields.

- `config/production.yaml` migrated to `schema_version: "3"` with explicit
  `models:` (11 json_artifact + 2 code_backed: soccer/tennis, whose
  artifact files never existed — their contracts are resolvable Python
  entry points instead). `allowed_models`/`artifact_map` retained for
  `champion_challenger.freeze()` compatibility.
- `production_canary.py` now delegates validation to the registry and
  `health_check` reports per-model contract status; a broken **secondary**
  model degrades overall health to DEGRADED (truthful, not silently
  "healthy") while the primary's deep checks (hash, finite probabilities,
  data freshness) are unchanged.
- `cli_production.py` predict/status are registry-driven — no hardcoded
  WNBA identity; the primary entry is the served model, single-model
  execution is now an explicit scheduling policy.
- 11 new tests in `tests/test_production_registry.py`, including the CI
  contract test that the **actual checked-in production.yaml resolves all
  13 models**. Full suite: 1777 passed, 3 skipped; ruff at the 120 baseline.

## 2026-08-14 (later) — MLB ingest partial-completion cache bug: 5 games permanently missing, fixed + backfilled

`ingest.py`'s staleness check only re-fetched a past-date raw cache when it
contained **zero** completed events (the 2026-07-26 pregame-snapshot class).
A cache written **mid-slate** — some events `STATUS_FINAL`, some still
`STATUS_IN_PROGRESS` — passed the check forever, so the in-progress games
never reached `data/processed/mlb/games.jsonl` or
`data/historical/mlb_games_all.jsonl` (and the Elo/trend features that read
it). Confirmed live:

- `data/raw/mlb/2026-08-07/` was written 2026-08-08 04:04 UTC with 10 final
  + 5 in-progress events. Events **401816426, 401816435, 401816436,
  401816437, 401816438** were missing from processed/historical while ESPN
  had final scores for all five.
- The same partial-snapshot class also hit 2026-07-30 (2 games), 2026-08-01
  (1), 2026-08-04 (1). Three more dates (2026-07-16, 07-21, 07-25) had full
  slates frozen at `STATUS_SCHEDULED` — caches written by manual bootstraps
  before the daily ingest loop existed (added ~07-25), never revisited.

**Fix** (`src/model_prediction/ingest.py`): a past-date cache is stale when
it holds any unfinished event — competition `status.type.state` in
`{"in", "pre"}` — not just when zero events completed.
Postponed/canceled events are terminal (`state: "post"`) and deliberately
don't trigger refetch (a rainout-heavy date would otherwise re-fetch on
every bootstrap). Skipped for tennis, whose payload events are tournaments,
not per-game events (same sport-aware logic as the completion parser).

**Backfill executed**: re-ingested all 7 affected dates from live ESPN; all
15 events on 2026-08-07 now in processed + historical with real final
scores. Also found 31 processed-only games from 2026-07-19/20/21 (ingested
before the historical append path existed) and appended them to historical
— processed/historical event-id parity is now exact (0/0).

**Tests**: 2 new in `tests/test_ingest.py` — partial-completion refetch and
postponed-is-terminal (verified the first FAILS on the old logic, per the
fix-verification convention). Full suite: 1766 passed, 3 skipped; ruff at
the same 120-finding baseline.

## 2026-08-14 — WNBA spread promoted to live serving (wnba-spread-margin-v1)

The 2026-08-13 audit found `wnba-spread-margin-v1` (the fixed replacement
for the broken moneyline-not-spread `wnba-spread-baseline-v1`) had **zero
serving consumers** — config pointed at it, `models/basketball.py`
implemented it, but nothing built a slate or logged a pick. Wired it up:

- **`cli._forecast_wnba_spread_slate`**: builds `BasketballModel` spread
  predictions from `data/processed/wnba/games.jsonl` history, matched
  against the Polymarket WNBA spread snapshots
  (`data/odds/wnba/{date}/polymarket_snapshots.jsonl`). Alternate lines per
  game are resolved by `_select_wnba_spread_market` — same "most balanced
  line wins" rule `mlb_market_odds._market_balance` uses for MLB, adapted
  to this snapshot format's embedded long/short asks.
- **Sign convention, verified against `PolymarketUSClient.snapshot`'s own
  documented contract**: each spread snapshot row is anchored to one team
  (`team`) with `line` always that team's own spread; empirically confirmed
  live (2026-08-14) that Polymarket anchors WNBA rows to the *away* team,
  so a row's `line` is directly `BasketballModel`'s `spread_away_line`
  input. Selecting "away" logs that line as-is; selecting "home" logs its
  negation (`grade_pick`'s selection-relative-line contract, confirmed
  against the 2026-08-13 grading audit's spread-sign test coverage).
- **Routing**: Main (CALL only) + Flat (every candidate) — same rule MLB
  spread/total uses (operator directive 2026-08-03), trust-boundary-only
  `evaluate_eligibility` (not the curated min-edge gate esports/soccer/
  tennis use), since WNBA is a Main-ledger "show everything" sport like MLB.
- **`model_ledger.py`**: `("WNBA", "spread")` already repointed from the
  archived `wnba-spread-baseline` to `wnba-spread-margin` on 2026-08-13.
- Wired into the `daily` command's concurrent research pool
  (`_wnba_spread_task`, alongside `_mlb_totals_task`/`_soccer_task`/
  `_tennis_task`) — independent of WNBA moneyline, which the learned-sports
  pool already logs separately.
- Live dry-run against 2026-08-13's real 3-game WNBA slate (isolated temp
  ledgers, no writes to real data): 3/3 games matched a spread market,
  3/3 logged CALL to both Main and Flat with real Kelly-scaled units
  (1.5U–2.0U) and correctly-signed lines.
- 5 new tests in `tests/test_cli.py` (line-selection, flat-always-logs,
  main-CALL-only, duplicate-tracking). Full suite: 1764 passed, 3 skipped.
  Ruff: same 120-finding baseline, 0 new.
- **Not changed**: `config/model.yaml`'s WNBA `status`/`active_research_version`
  fields — left as `active_research_version: spread-margin-v1`, matching
  the precedent that MLB spread/total also serve to Main+Flat while still
  configured as `active_research`, not a separate "promoted" status tier.

## 2026-08-13 — KBO phantom 0-0 settlement: every pick settled as a scoreless tie, root-caused and fixed

**Symptom**: all 16 settled KBO picks in `data/research/kbo.xlsx` showed
`away_score=0, home_score=0`, `result=push`, with nonzero pnl — impossible
for real baseball (16 different games, zero runs scored), while NPB (same
settlement code path) had zero 0-0 rows.

**Root cause** (two layers, both in `international_baseball.py`):

1. `parse_kbo_rows` treated a game with an EMPTY relay cell (the official
   KBO page renders unplayed games as "0 vs 0" with no `gameId=` link —
   verified live against `KBO_RESULTS_ENDPOINT`) as a real row, fabricating
   a fallback game_id (`kbo:YYYYMMDD:AWAY:HOME`) and caching it as a 0-0
   `tie=true` row. Once the game actually completed, the source provided
   the real gameId and real scores — a second row with a different id, so
   the merge-by-game_id could never overwrite the phantom.
2. `find_international_baseball_result._match` matched by date+teams and
   returned the first row in sorted (date, game_id) order. The fabricated
   id sorts before the real source id, so the phantom 0-0 row shadowed the
   real-scored row for the same game, and settlement graded a fake tie.

**Fix** (2026-08-13):
- `parse_kbo_rows` now SKIPS rows with no `gameId=` in the relay (same
  spirit as NPB's unplayed-`*` skip). The row reappears with real id and
  scores once the game completes.
- `_match` ignores any row whose game_id matches the fabricated fallback
  pattern (`_FABRICATED_KBO_ID_RE`) — defense in depth for caches written
  before the fix.
- Purged the 30 phantom rows from `data/international_baseball/kbo/games.jsonl`
  (only 2 genuine historical 0-0 rows remain, both with real source ids).
  These phantoms had also contaminated the tie-rate training stats (30
  phantom ties in ~2 weeks vs 2 genuine in 10 years); the daily
  recalibration rebuilds ratings from the cleaned dataset.
- Corrected the 11 ledger rows whose real scores were already in the cache
  via `PickLedger.settle(..., correction_reason=...)`, and mirrored the
  corrections into `data/model_ledgers/kbo-tie-aware-elo.xlsx`.
- Regression tests: `test_parse_kbo_rows_skips_unplayed_games_with_empty_relay`
  and `test_find_result_ignores_fabricated_id_phantom_rows`.

**Remaining follow-up**: the 5 rows from 2026-08-13's own games were still
in progress at fix time. `scripts/correct_phantom_tie_settlements.py`
(idempotent) corrects any rows still carrying the phantom signature once
real scores land — run it after the next daily refresh.

## 2026-08-13 (later) — in-depth bug search: fixes applied

Cross-ledger invariant scan + two parallel audit agents (grading logic;
newest-code PIT/parity) produced these fixes:

- **ProductionLedger idempotency index kept superseded rows in the slot**
  (`production_ledger.py`): the unique index predicate was
  `WHERE supersedes_id IS NULL`, so a superseded row (which keeps
  `supersedes_id` NULL) still collided with a re-fired identical cycle,
  returning the stale row's id instead of the correction's. Predicate is
  now `WHERE status = 'predicted'` (SCHEMA_VERSION bumped to 2), with an
  init-time migration that drops/recreates the index in pre-existing DBs
  (the live `data/production/predictions.db` migrates on its next open).
  Regression tests: `test_rerun_after_supersede_returns_correction_not_stale_row`,
  `test_old_index_predicate_is_migrated`.
- **run_rebuild.sh silently exited 0 with an empty sports list** (config
  read failure → zero rebuilds "succeeding"): now exits non-zero when no
  enabled sports derive from `config/rebuild.yaml`.
- **Tennis ratings frozen since 2026-07-27**: the daily ingest loop covered
  only mlb/nba/wnba/nfl while `models/tennis.py` rebuilds its surface Elo
  from `data/processed/tennis/games.jsonl` every forecast — the file
  stopped advancing when training work stopped and nothing on the schedule
  wrote it. `scripts/run_daily.sh` Step 1b now ingests tennis too (verified
  live: 287 games appended for 2026-08-12). Tennis predictions will shift
  as ratings re-advance — expected, that's the data flow being restored.
- **WNBA spread attribution mapped to the archived model**:
  `model_ledger.py`'s `MODEL_ID_BY_LEAGUE_AND_MARKET` mapped
  `("WNBA","spread")` to `wnba-spread-baseline` (the archived broken
  model) — now `wnba-spread-margin`.
- **frozen_champions.json re-frozen**: the pre-fix snapshot recorded
  kbo/npb as CODE_BACKED even though their artifacts exist; `freeze-production`
  re-run, both now carry real artifact hashes.
- **Test hardening**: `tests/test_pricing.py` now pins all four spread
  sign combinations, away-side integer pushes, and totals push-at-integer
  behavior (the 2026-08-13 audit found only home-side combos were covered).

**Investigated and deliberately left alone** (each needs an explicit
decision, not a silent change): WNBA spread artifact `wnba-spread-margin-v1`
has zero serving consumers (the "fix" is config-wired but nothing loads it —
promotion/wiring decision); tennis walkovers/retirements settle and
Elo-update as full-strength matches (docs/MODEL_IMPROVEMENTS.md demands
walkover → no rating update; implementing it changes model behavior);
soccer score collection is 401-broken (THE_ODDS_API_KEY rejected on all 12
leagues) and the Celta Vigo @ Napoli friendly is outside the 12 configured
leagues anyway; v9 `park_factor_pit` prior-strength drift vs v8's static
table is challenger-only by design.



## 2026-08-02 (latest) — Live-run verification of the per-model ledger architecture: 3 real bugs found and fixed, soccer draw settlement corrected

Operator asked to run a real `daily` end-to-end and confirm the per-model
ledger architecture (previous section) is genuinely working live, not just
migrated. Separately asked about KBO/NPB push settlement -- confirmed
correct as documented below -- which led to checking soccer's analogous
case and finding it was wrong.

**KBO/NPB tie/push settlement: confirmed correct, no change.** A tied KBO/
NPB game really is a 2-outcome market (home wins / away wins) with no
separate "draw" contract; Polymarket settles a tie at $0.50.
`cli.py::_settle_international_baseball_pick` sets
`binary_contract_settlement_value = 0.5` on a tie and `ledger.py::settle()`
computes `pnl = units * (binary_contract_settlement_value /
entry_probability - 1)` using the real pregame decision price as the
buy-in. Verified against real settled Doosan Bears/Samsung Lions/KT Wiz
push rows -- matches the formula exactly. This is the correct, intended
implementation of "if it's a push, market resolves 50/50, use pregame
price to determine buy-in, PnL from that price to 50/50" -- nothing to fix.

**Real bug found: soccer moneyline draws were graded PUSH, should be
LOSS.** Unlike KBO/NPB, Polymarket's soccer win market is not one 2-outcome
market with a tie case -- `data_sources/polymarket_us.py`'s own comment
(verified live 2026-07-27) documents it as three *independent* Yes/No
contracts per game (home wins / draw / away wins). A "home" or "away" pick
is a bet on that specific contract resolving YES; a draw resolves it NO --
a full loss of stake, not a refunded push. `pricing.py::grade_pick` was
reusing the generic moneyline "tied score -> PUSH" branch (correct for a
real 2-outcome tie-refund market, wrong for soccer's independent-contract
structure). Fixed: `grade_pick` now takes an optional `league` parameter;
`league == "SOCCER"` with a tied score grades LOSS. `ledger.py::settle()`
passes `row["league"]` through. 2 new tests in `test_pricing.py`
(soccer-draw-is-a-loss, non-soccer-tie-is-still-a-push -- KBO/NPB's
`binary_contract_settlement_value` special-case still depends on
`grade_pick` returning PUSH for the generic case).

**Real, quantified impact on already-logged data**: exactly 15 already-
settled soccer rows (mirrored identically in `data/flat_picks.xlsx` and
`data/research/soccer.xlsx`, since every soccer decision is logged to
both) were graded push/$0.00 and should have been loss/-1 unit (two of the
15 were sized at 1.25/1.5/2.0 units, not 1.0 -- pnl_units corrected
accordingly). Corrected via the sanctioned mutation path (never raw
edits): `archive_settled_rows` (with a real reason + archive reference,
full row content preserved at
`data/archive/2026-08-02-soccer-draw-push-bug/`), reset to `open`,
`import_rows` back in under the same `pick_id`, then re-`settle()`d
through the now-fixed `grade_pick` -- so every other derived field
(review_status, audit trail) comes from the real settlement code path,
not hand-computed. Verified zero `SOCCER`/`push` rows remain anywhere in
the ledger tree afterward.

**Real bug found: model ledger dedupe key silently dropped genuine
re-forecasts.** Live-verified by actually running `daily` and cross-
checking a real WNBA pick (event `401857107`, Indiana Fever @ Minnesota
Lynx) against its corresponding `data/model_ledgers/wnba-moneyline-elo-
trend-lr.xlsx` row. The Main-ledger pick_id had changed (an earlier open
pick for the same event was replaced by a same-day re-forecast with new
`model_probability`/`decision_price` and a fresh `observed_at_utc`), but
`record_from_pick_request`'s dedupe key -- `(event_id, market_type, line,
model_version)` -- didn't include `observed_at_utc`, so it matched the
*old*, already-migrated row's key and silently skipped writing the new
one. The per-model ledger was stuck showing stale numbers for any event
whose open pick got replaced, exactly the "same real decision routed to
Main+Flat" collapsing the key was designed for, over-applied to a
genuinely different later decision. Fixed: `observed_at_utc` added to
`_prediction_dedupe_key` (now a 5-tuple). Main/Flat/Research/Gated calls
for one real decision still share the exact same `PickRequest` object (and
therefore identical `observed_at_utc`), so they still collapse correctly;
a later re-forecast gets a new `observed_at_utc` and now creates a new row
instead of being silently dropped.

Adding `observed_at_utc` to the key surfaced a second, related bug:
`record_from_pick_request` wrote `request.observed_at_utc` verbatim, which
is `None` for callers that don't explicitly set it (several test fixtures,
possibly some real call sites) -- while `_append_record` (the primary
ledger) falls back to `request.observed_at_utc or iso_utc(created)`. Sole
result: every request with no explicit `observed_at_utc` was writing a
blank value, so every one of *those* re-forecasts collapsed into the same
blank-keyed row again, defeating the fix. `record_from_pick_request` now
takes a `created` parameter and applies the identical fallback; `_append_
record` passes its own `created` through. 1 new regression test
(`test_record_from_pick_request_does_not_dedupe_a_refreshed_forecast`).

Backfilled the 109 real rows this silently dropped by re-running
`scripts/migrate_to_model_ledgers.py` (idempotent by design -- it re-scans
every source ledger and skips anything already present by `prediction_id`,
so re-running after a live code fix is safe): 109 written, 380 skipped as
already migrated, 0 unmapped. Verified event `401857107` now has both the
original (migrated) row and the new, real re-forecast row, and a dry-run
immediately after writes 0.

**Real bug found: `ModelLedger.settle()` existed but was never called.**
The class had a working `settle()` method (used only by the dashboard's
manual operator-decision endpoint's read path, never by the actual
settlement pipeline). Nothing in `cli.py`'s real settlement paths
(`_settle_all_unsettled`, soccer/tennis/esports/KBO/NPB settlement)  ever
called it. Model ledger rows stayed `status: open` forever, even after
`PickLedger.settle()` graded the real, equivalent pick days or weeks
later -- meaning `compute_model_evidence`'s hit-rate/Brier/calibration
numbers (the entire point of "operator decides using real per-model
evidence") never actually populated past whatever the one-time migration
captured. Fixed with the same fail-soft pattern as the append-side hook:
new `model_ledger.settle_from_pick_row(model_ledgers_dir, row)` looks up
the matching open row by the same 5-tuple dedupe identity and calls
`ModelLedger.settle()` on it; wired into `PickLedger.settle()` right after
its own write, wrapped in try/except so a bug here can never turn a real,
successful settlement into a failure. 2 new tests in `test_ledger.py`
(settle-also-settles-the-model-ledger, a-failure-here-never-breaks-the-
primary-settle -- mirrors the existing append-side pair exactly).

Every fix in this section verified via the project's revert-and-confirm
convention (temporarily undo, confirm the new test fails, restore, confirm
it passes) before being considered done. Full suite: 643 passed. Ruff:
118 findings, matching the existing baseline exactly, 0 new.

**Not yet live-verified**: the settle-side wiring above has full unit-test
coverage but no live-run confirmation yet, because the day's real
settlement had already run (via the independently-scheduled
`com.modelprediction.daily.plist` launchd job, `scripts/run_daily.sh` --
found running concurrently under a real, pre-existing OS lock at
`data/locks/daily.lock` while investigating this, PID 32354 at the time)
before this fix landed. The append-side fix *will* be live-exercised by
that same scheduled run's forecast step. A live settle confirmation is the
natural next check once that process finishes.

## 2026-08-02 (later still) — Per-model ledger architecture: new schema, `ModelLedger`, real data migrated

Operator directive: "recompile all models will be production in its own
ledger, the clafiication of benchmarks or shadow should not exist, there
should be no calssification, all models are the same. i decide to pormote
it or not." Followed by explicit confirmation to build the full thing
("all of it in order").

**What shipped:**
- `src/model_prediction/model_ledger.py` — new `ModelLedger` class, one
  `.xlsx` file per model identity (not per sport/routing-destination).
  New common schema (`model_id`, `model_version`, `artifact_hash`,
  `code_revision`, `feature_schema_version`, `model_probability`,
  `model_projection`, `model_uncertainty`, `decision_price`,
  `market_no_vig_probability`, `model_market_difference`,
  `observed_at_utc`, `event_start_utc`, `input_availability`,
  `missing_inputs`, `source_lineage`, `status`, `result`, `closing_price`,
  `probability_clv`, `pnl_units`, `settled_at_utc`) plus a separate
  operator-decision block (`operator_decision`, `operator_selected_model`,
  `operator_selected_market`, `operator_units`, `operator_timestamp`,
  `operator_note`) that `record_operator_decision()` writes without ever
  touching the model's own fields. No `record_type`/`model_state`/
  `qualified_for_betting`-style classification field anywhere in this
  schema — deliberately, per the directive. `append_failure()` only
  accepts one of a fixed `INTEGRITY_FAILURE_REASONS` set (event started,
  identity unresolved, bad artifact hash, stale feature timestamp, wrong
  market, unmapped side, calculation failure, undefined missing-input
  behavior) — the only things allowed to block a numeric prediction now.
  12 new tests in `tests/test_model_ledger.py`.
- `scripts/migrate_to_model_ledgers.py` — read-only against every existing
  source ledger (`data/picks.xlsx`, `data/flat_picks.xlsx`,
  `data/research/*.xlsx`, `data/gated_research/*.xlsx`), maps
  `(league, market_type)` to a model identity (soccer's moneyline+total
  both map to `soccer-poisson-dc` — one model, two market types, not two
  models), and dedupes by the same market-identity key `ledger.py`'s own
  `_market_duplicate_key` uses (event/market/line/sportsbook/model_version),
  preferring a settled copy over an open one when the same real decision
  was logged to more than one old destination. Real numbers: 688 source
  rows scanned across 4 old destination types → 483 genuinely unique
  decisions written across 12 real models. Spot-verified against numbers
  already independently confirmed this session (soccer: 81, matching the
  earlier flat-ledger backfill count exactly; MLB moneyline: 110→55, a
  clean 2x consistent with Main+Flat being the only two destinations that
  sport ever wrote to). Idempotent — re-run against already-migrated data
  writes 0 new rows, skips all 483 by `prediction_id`.

**Explicitly not done in this pass** (real, large, separate remaining
work — see the conversation transcript for the full breakdown given to
the operator): cutting the *live* forecast pipeline (`cli.py`'s ~15
forecast functions, `daily`) over to write through `ModelLedger` instead
of the old `PickLedger`; the dashboard redesign (one row per event, one
column per model, evidence columns, no classification badges); every
challenger/ensemble model named in the plan (total-score Ridge, tennis
point-Markov, roster-aware esports Elo variants, joint Negative Binomial
totals) — none of that modeling code exists yet. `PickLedger`/`ledger.py`
is completely untouched; the live pipeline still writes through it exactly
as before. `data/model_ledgers/*.xlsx` is a real, verified, additive
backfill of historical data into the new shape — not yet the system of
record.

## 2026-08-02 (really final) — MLB position-player availability shipped; a real starter-quality candidate tested honestly and NOT promoted; CLAUDE.md added

### MLB position-player (lineup) availability -- shipped

Extended the pitching-staff availability feature (refactored into a shared
`_team_roster_group_availability` helper) to also cover non-pitchers --
`team_position_player_availability`/`matchup_position_player_availability`.
Real component of section 8 rank-2 ("confirmed vs. projected lineup").
Verified live: Yankees 18.75% unavailable, cross-checked against the real
roster (Aaron Judge, Cody Bellinger, Giancarlo Stanton, all genuinely on
the IL). 12 new tests, wired into `learned_forward.py` shadow-only, same
pattern as everything else this session.

### A real, well-motivated model-improvement candidate tested honestly — did NOT clear the bar, not promoted

Goal was explicit this round: find something that actually moves win rate/
profitability, not just another shadow/availability feature. Found that
the live MLB v7 model's `pitcher_era_gap` feature is a team-level rolling-
runs-allowed proxy, **not** starter-specific, despite its name — the
project's own roadmap ranks "true starting-pitcher quality" as MLB's #1
priority, and it's genuinely never been in the live model. A prior
attempt (`probable_starter_era_gap`, ESPN live probables) was built and
then retired for being train/serve-skewed (2026-07-30).

Found real, already-built, never-used infrastructure for this:
`validation.py::_load_starter_era_map`/`_starter_era_gap` computes a
genuinely point-in-time-safe rolling-5-start ERA gap from real
`mlb_statsapi` box-score snapshots (each starter identified as
`pitcher_order[0]`, history updated strictly *after* computing each game's
feature) -- already wired into `ValidationRow.starter_era_gap`, but never
tested in any ablation (grepped `production_feature_ablation.py`,
`roadmap_challenger.py`, and `outputs/` -- zero hits).

A quick standalone signal check (1055 games, the elasticity-refit feature
cache) showed a real, promising correlation: 0.14 with home-win outcome,
correct direction, real bucket separation (away-starter-much-better:
46.0% home win rate; home-starter-much-better: 55.9%; baseline 51.6%).

**Ran the real thing before trusting that**: `build_walk_forward_rows` +
`chronological_split` (the project's own validated 60/20/20 chronological
framework, reused rather than reimplemented) on the full real history --
6,324 MLB games, 2024-04-06 through 2026-08-01, 5,186 with a real
`starter_era_gap`. Fit the incumbent v7 feature set vs. incumbent +
`starter_era_gap`, both ways, on train only:

| | Validation Brier | Validation accuracy | Locked-holdout Brier | Locked-holdout accuracy |
|---|---|---|---|---|
| Incumbent (v7) | 0.24637 | 55.68% | 0.24683 | 55.65% |
| + starter_era_gap | 0.24696 | 55.13% | 0.24585 | 56.51% |

**Mixed, not a clean win — not promoted.** The candidate is *worse* on the
validation set (the set this project's own promotion rule says decides
whether a feature is worth including at all) and *better* on the locked
holdout, both by small margins. Checking holdout to override a validation-
set regression would itself be exactly the kind of "peek at holdout to
justify inclusion" `docs/AGENTS.md` explicitly forbids. The coefficient
sign is correct (`-0.0193`: a worse home starter ERA lowers home win
probability, as expected) and the standalone correlation is real -- this
isn't nothing -- but `elo_probability` (coefficient ~3.0) already captures
most of what a team's pitching-staff-quality trend proxies for over time,
and the genuinely *additive* value on top of the existing feature set
doesn't clear this project's own bar. Documented honestly rather than
force-promoted or suppressed: a real, validated-as-*not*-sufficient
finding, worth revisiting if a future session tries a different
functional form (e.g. an interaction term, or replacing `pitcher_era_gap`
outright instead of adding alongside it) rather than repeating this exact
test.

### CLAUDE.md added

Auto-loaded project guidelines: doc reading order, the point-in-time
correctness invariant (dominant real-bug source all session), the
shadow-feature pattern, promotion/validation conventions reusing existing
tooling instead of reimplementing chronological splits, testing
conventions, and the worktree-isolated-subagent staleness gotcha from
earlier today.

**Tests**: 571 passed (12 new: 5 position-player availability + the
same-day-transaction and pitching-staff tests already counted above).
`.venv/bin/ruff check src/ tests/` -> 117, at/under baseline.

## 2026-08-02 (final) — three-agent model review results, one real fix applied, one real gap found and NOT yet fixed (needs a scope decision)

Three parallel agents reviewed (1) all MLB code touched today, (2) esports/
KBO-NPB/soccer wiring, (3) the core ledger/eligibility/units invariants.
**Process learning, worth remembering**: two of the three ran with
`isolation: "worktree"`, which checks out a git *commit* into an isolated
copy -- it cannot see uncommitted working-directory changes. At the time
they ran, substantial real, tested, verified work (soccer flat/Main wiring,
the KBO/NPB timestamp fix, the MLB pitching-staff feature) was sitting
uncommitted. One agent correctly self-diagnosed this (used `git archive`
against the right commit and flagged the mismatch explicitly) and its
findings about the state *at that commit* were accurate, just about
already-superseded code. The other did not self-diagnose it and instead
concluded several already-fixed things (`edge_scaled_units` ignoring
`model_uncertainty`, `min_pick_units` still 0.5, `archive_settled_rows`
not existing) must have been "reverted" -- **all three directly
re-verified false** against the actual current code (see below). Lesson:
worktree-isolated review agents need either a fresh commit right before
launch, or explicit instruction to read the shared checkout path directly
(the way the third, reliable agent did) whenever there's known uncommitted
work.

### Real bug, fixed: same-day MLB transactions could leak into a decision

Covered in detail above this section's neighbor entries -- MLB Stats API's
transaction `date` field has no time-of-day component, so a transaction
reported the same calendar day as the decision could have happened before
or after it, with no way to tell. `_starter_status` now excludes same-day
transactions entirely and derives its cutoff from the decision's own
timestamp rather than `game_date`. Verified the regression test actually
catches the bug (reverted the fix, confirmed the test failed, restored
it). Also added a regression test locking in that `ENGINE_VERSION` rejects
the un-promoted v0.3 elasticity refit, and fixed two research scripts'
docstrings that referenced the wrong formula version.

### Real gap, found, NOT fixed: team-ban enforcement doesn't work for esports/soccer/KBO/NPB/tennis

Independently re-verified myself, not just trusted from the review (the
reporting agent's environment was unreliable per above, but this specific
finding checks out against the current live code): `evaluate_esports_
eligibility` (`eligibility.py`) -- the eligibility path used by every
sport routed through the canonical-registry-free "name-based" pattern
(esports, soccer, KBO, NPB, tennis) -- takes no `TeamBanList` parameter
and never checks one. Confirmed no call site (`_forecast_soccer_sport`,
`_log_esports_forecast`, `_forecast_international_sport`,
`_forecast_tennis_sport` in `cli.py`) passes bans through either. Contrast
with `evaluate_eligibility` (the MLB/WNBA/NBA/NFL path), which checks
`ban_list.check(...)` first, before anything else.

It goes deeper than a missing parameter: `TeamBanList.check`/`.add`/
`.remove` (`bans.py`) all call `self.registry.resolve(league, team_input)`
internally, and esports/soccer/KBO/NPB teams are deliberately **not** in
the canonical registry (confirmed live: `registry.resolve(League.LOL,
"T1")` raises `EntityResolutionError`). So `model-prediction ban add
--league LOL --team X` would itself fail today, not just the eligibility
check -- the entire ban mechanism is built around registry resolution, and
these sports were deliberately built registry-free. `config/model.yaml`'s
`team_ban_list.teams` does have (empty) `LOL`/`CS2`/`KBO`/`NPB`/`TENNIS`
sections, suggesting the intent to support this was there; the enforcement
never got built. Soccer/DOTA2/VALORANT/RAINBOW_SIX don't even have a
config section.

**Zero live impact today**: every one of those league sections is
currently empty (no team is banned in any of these sports right now), so
nothing is silently slipping through *right now*. But if the user ever
bans a team in one of these sports expecting it to actually block future
picks, it would silently not work. Properly fixing this means building a
genuinely separate, name-based ban mechanism for these sports (bypassing
`TeamBanList`'s registry coupling entirely, not just adding a parameter),
which is a real, non-trivial, cross-cutting change touching real-money-
adjacent eligibility logic in 4+ sports -- flagged for a scope decision
rather than built unilaterally in the same pass as everything else today.

## 2026-08-02 (still later) — MLB pitching-staff availability (new shadow feature, roadmap item), plus a full model review in progress

New feature: `features/mlb_player_availability.py::team_pitching_staff_availability`/
`matchup_pitching_staff_availability` — the first real pass at
`docs/MODEL_IMPROVEMENTS.md` section 8's rank-3 roadmap item ("bullpen
availability... closer/setup absence"), picked as the concrete next step
after reframing the roadmap's priority order (see the doc's own section 1/
12 updates from earlier today): MLB is the only one of the four major
leagues currently in season, and this reuses the exact live-roster-
snapshot infrastructure already built and validated today for starter
availability, rather than starting a new data source.

Coarser than the roadmap's full description -- MLB Stats API roster data
identifies position *type* (Pitcher vs. everyone else), not bullpen *role*
(closer/setup/long) -- so this reports aggregate pitching-staff health
(share of the current staff on the IL or an admin list), not a specific
reliever's availability. `capture_roster_snapshot` now also stores each
player's `position_type` (a schema addition -- pre-existing captured
snapshots don't have it, so this feature only works going forward from
today, same "clock going forward, no backlog" situation as the KBO/NPB fix).

**A real design bug caught by testing against live data before it
shipped**: the first version's "unavailable" bucket included `"Reassigned
to Minors"`, producing a nonsensical baseline (Yankees 43%, Dodgers 55% of
their pitching staff "unavailable"). Being optioned to AAA is routine
40-man-vs-26-man roster depth management, present for every team on any
given day regardless of health -- not an injury signal, and it swamped the
real signal with structural noise. Fixed with a new, narrower
`INJURY_OR_ADMIN_LIST_STATUSES` constant (`data_sources/mlb_injuries.py`)
that explicitly excludes `Reassigned to Minors` from both the numerator
and denominator, kept deliberately separate from the existing
`STATUS_ACTIVE_PROBABILITIES` (which correctly treats "optioned" as
disqualifying for a *named* player's starter-availability check, but
shouldn't for this *aggregate* health read -- same status, different
question, different answer). Re-verified live after the fix: Yankees
13.3% (2/15 pitchers), Dodgers 43.5% (10/23) -- cross-checked the Dodgers
number against the real roster entries directly (Blake Snell, Tyler
Glasnow, Bobby Miller, Brusdar Graterol, Gavin Stone, Ben Casparius, Blake
Treinen, Brock Stewart, Will Klein, Jake Cousins -- all real, all
genuinely on the IL, matches well-documented real 2026 Dodgers pitching
injuries).

Live-only by construction, and unlike the starter-availability feature,
this one has **no transactions-based historical fallback** -- "how many
pitchers are on the active roster right now" isn't reconstructable from
the IL-transaction log alone (trades/options/call-ups change roster
composition in ways a pure IL filter can't capture), so this feature
genuinely cannot backtest against past games, only forward from today.

Wired into `learned_forward.py`'s generic feature-computation path with
its own dispatch branch and feature-name constant
(`PITCHING_STAFF_FEATURE_NAMES`), same shadow-only, inert-until-requested
design as every other availability feature in this project -- no
production artifact requests these feature names, so this is dead code in
live production today by design, exactly like starter availability was
until an artifact opts in.

**Tests**: 7 new (`test_pitching_staff_excludes_optioned_players_from_numerator_and_denominator`,
`test_matchup_pitching_staff_gap_favors_home_when_away_staff_is_worse`,
`test_pitching_staff_no_pitchers_found_fails_closed`,
`test_pitching_staff_no_roster_snapshot_fails_closed`,
`test_pitching_staff_unrecognized_status_fails_closed`,
`test_matchup_pitching_staff_rejects_postgame_observation`, plus one
existing capture-schema test updated for the new `position_type` field).
Full suite: **563 passed**, 0 failed. `.venv/bin/ruff check src/ tests/` ->
117 findings, still at/under the 118 baseline.

**In progress, not yet reported**: three parallel review agents launched
against (1) all MLB code touched today, (2) esports/KBO-NPB/soccer wiring
(specifically re-checking every other `_forecast_*` function in `cli.py`
for the same timestamp-ordering bug class that broke KBO/NPB), and (3) the
core ledger/eligibility/units invariants. Findings and any resulting fixes
will be appended here once they land.

## 2026-08-02 (later) — soccer wired to flat/Main-gated, esports live-vs-backtest investigation, a real KBO/NPB zero-logging bug found and fixed

### Soccer: wired into flat forecast and Main (gated), same eligibility bar as Gated Research

`_forecast_soccer_sport` (`cli.py`) gained `flat_ledger`/`main_ledger`
parameters. `flat_ledger`: every priced contract logged unconditionally,
same "show everything" semantics every other sport's flat forecast already
uses -- previously soccer's `research_ledger is None` early-return meant a
flat-only call did nothing at all; fixed by falling back to `flat_ledger`
as the exposure-computation source when `research_ledger` isn't provided.
`main_ledger`: mirrors `gated_ledger` exactly, same eligibility result,
same "only when genuinely eligible" gate (operator-confirmed: same bar as
Gated Research, not a separate/stricter one).

**This wiring is correct and live-verified (19 real rows now flowing into
`data/flat_picks.xlsx` via a real `flat-forecast --sport soccer` run), but
produces zero Main rows today.** `config/model.yaml`'s `SOCCER.status` is
still `"research"`, and `lifecycle.py::can_create_qualified_call` requires
`ModelState.SHADOW_QUALIFIED` before any request can become a real CALL --
confirmed this is exactly why 100% of soccer's 62 real research picks carry
`NO_CALL_MODEL_UNVALIDATED`, unrelated to edge or confidence. Promoting
soccer to `shadow_qualified` (the same status LOL/CS2/DOTA2/VALORANT/MLB/
NBA/WNBA/NFL/KBO/NPB already carry) is a real model-promotion decision this
change deliberately does not make on its own -- soccer's real settled
research record (28-19, +6.84u) is a plausible case for it, but promotion
in this project's own stated governance requires the same walk-forward/
locked-holdout process those other sports went through, not a side effect
of wiring two ledgers.

Four new tests in `tests/test_cli.py` (`_soccer_forecast`/`_soccer_config`
fixtures): flat logs everything even when blocked at the model-state gate;
main mirrors gated exactly on both a real CALL and a blocked one.

### Esports: live-vs-backtest divergence investigated, not (yet) acted on

Real settled record since the v4->v5 rebuild: LOL 4-7 (-5.73u research),
1-2 (-1.83u gated); CS2 12-11 (-0.79u), 4-6 (-1.43u gated); DOTA2 8-5
(+3.33u), 3-2 (+1.68u gated); VALORANT 4-3 (+1.36u), 2-1 (+2.02u gated).
Rainbow Six: zero picks, but confirmed as a real data-availability gap
(zero scheduled Polymarket R6 events today), not a code issue --
`matches.jsonl` has 2,969 real historical matches for backtesting, there's
just no live market right now.

Checked calibration (predicted probability vs. actual hit rate) per title.
DOTA2 and VALORANT are fine (predicted ~59%/56%, actual ~62%/57%). LOL and
CS2 both run overconfident on their live samples: LOL predicted 58.9%
research-wide / 62.1% gated, actual 36.4%/33.3% -- roughly 2.4 standard
deviations below what LOL's own locked-test backtest (70.3% accuracy,
n=1,910, a real, large, validated number) would predict for a sample this
size. Investigated the likely explanation rather than guessing: the
backtest's own K/threshold selection is self-labeled
`"profitability": "not_established_no_point_in_time_market_prices"` --
it assumes a flat -110 price for every match, because no real captured
Polymarket price history existed when that methodology was built. That's
now only partially true: 16 days of real captured BBO exist (28,469 lines
across LOL/CS2/DOTA2/VALORANT since 2026-07-17), and live picks already
price against real executable asks (confirmed: `esports.py` sources
`executable_ask` from a live `PolymarketUSClient` call, not a synthetic
assumption) -- so the live PnL numbers above are real, not an artifact of
the backtest's own pricing assumption.

**Deliberately not acted on**: refitting confidence thresholds against the
current 16-day real-price window would trade the ~1,900-2,900-match locked-
test sample that justified each title's current threshold for one two
orders of magnitude smaller -- likely replacing real signal with noise, not
fixing anything. Documented as a real, open gap in
`docs/MODEL_IMPROVEMENTS.md` section 10 with a concrete recommendation: keep
accumulating real captured BBO (free, compounds daily) and revisit a real-
price threshold refit once the sample is large enough to matter.

### Real bug found and fixed: KBO/NPB have logged zero picks, ever, despite real games every day

Found while pulling a full cross-sport performance summary (`research_kbo`/
`research_npb` both showed 0 total rows -- not just 0 settled -- and
`research/kbo.xlsx`'s file mtime was 2026-07-28, untouched by any `daily`
run since). Today's real `daily` log showed `kbo: {"events": 5, "logged":
0}` / `npb: {"events": 6, "logged": 0}` -- real scheduled games, real
priced contracts with real team names/probabilities, zero logged. Traced
directly (wrapped `PickRequest.validate` to print the real exception): every
single contract failed with `"observation timestamp cannot be in the
future"`.

Root cause: `_forecast_international_sport` (`cli.py`) captured
`observed_now = utc_now()` **before** calling
`forecast_international_baseball_slate(...)` -- but that function stamps
each contract's own `observed_at_utc` using **its own internal** `utc_now()`
call, which happens strictly later (real fetch/compute time passes inside
the call). `request.validate(now=observed_now)` then always compared the
contract's own (later) timestamp against an (earlier) `now`, unconditionally
rejecting every contract as "in the future." `_forecast_soccer_sport`/
`_forecast_tennis_sport` already capture `observed_now` in the correct
order (after the slate builder returns); international baseball was the
one holdout. Fixed by moving the capture to after the slate-build call,
matching the correct pattern.

Verified three ways: (1) a direct call against real live KBO data went
from `logged=0` (with the real `ValueError` printed) to `logged=4`
matching `priced=4`; (2) a new regression test
(`test_international_forecast_observed_now_is_captured_after_slate_building_not_before`
in `tests/test_cli.py`) using a mock clock that genuinely advances during
the mocked slate-build call -- confirmed to fail against the pre-fix
ordering (`0 == 1`) and pass against the fix; (3) existing tests never
caught this because they freeze `utc_now()` to one fixed value everywhere,
which trivially satisfies `observed <= now` regardless of call order --
a real gap in the existing test fixture, not just in the production code.

This means every KBO/NPB research/gated pick this project has ever
"forecasted" was silently discarded before reaching any ledger, for as
long as this ordering bug existed. Re-ran the real `daily` command after
the fix to confirm live recovery (see Verification below).

**Tests**: full suite passes; 5 new tests (`test_soccer_flat_ledger_logs_every_contract_even_when_model_state_blocks_a_call`,
`test_soccer_main_ledger_mirrors_gated_ledger_exactly`,
`test_soccer_main_ledger_stays_empty_when_gated_ledger_does`,
`test_daily_forecast_roster_includes_soccer_and_both_international_baseball_leagues`
[pre-existing, unaffected], `test_international_forecast_observed_now_is_captured_after_slate_building_not_before`).
`.venv/bin/ruff check src/ tests/` -> 117 findings, at/under the 118
baseline.

## 2026-08-02 (latest) — MLB player availability (shadow feature) + Measured Edge margin/totals rebuild with fresh data

Two-part session, both parts of a single approved plan.

### Part 1: MLB player availability, shadow feature

New modules: `data_sources/mlb_injuries.py` (captures MLB Stats API's
`/v1/teams/{id}/roster?rosterType=40Man` live status and `/v1/transactions`
dated IL history), `features/mlb_player_availability.py` (cross-references
the ESPN-reported probable starter -- via a new sibling helper,
`data_sources/espn_probables.py::point_in_time_probable_starters`, exposing
the starter *names* the existing ERA-gap helper discarded -- against that
transaction history). Emits `probable_starter_unavailable_{home,away}` and
`availability_report_age_hours`, shadow-only (its own MLB-scoped gate in
`learned_forward.py`'s `_compute_features`, inert until a future artifact
lists these feature names). Wired into `cli.py`'s `daily` as
`step5c_mlb_availability`. 14 tests in `tests/test_mlb_availability.py`,
including a dedicated `date`-vs-`effectiveDate` regression test: MLB Stats
API transactions have both a `date` (when reported) and an `effectiveDate`
(sometimes retroactively backdated, e.g. "placed on the IL retroactive to
last week") -- only `date` (stored as `reported_date`) may ever decide what
was knowable as of a decision time.

A live smoke test against the real API caught a real bug before it shipped:
MLB's `typeDesc` is almost always the generic `"Status Change"` for IL
moves -- the actual detail ("placed RF Aaron Judge on the 10-day injured
list") lives in the free-text `description` field. The matcher was built
against `type_desc` first, which would have silently missed nearly every
real IL move; fixed to match `description`, then re-verified live (found
Aaron Judge on the 60-day IL and correctly flagged him unavailable). Also
simplified: a transaction that doesn't mention an IL/paternity/bereavement/
restricted/suspended list (trades, options, recalls, waiver claims -- most
of a player's real transaction history) is silently ignored rather than
raising an "unrecognized status" error, since only the IL-relevant subset
says anything about availability.

### Part 2: Measured Edge margin/totals rebuild

`scripts/mlb_elasticity_refit.py` (new, saved/rerunnable, with its own
on-disk feature cache at `data/research/mlb_elasticity_feature_cache.jsonl`
so reruns don't re-hit ESPN for already-reconstructed games) refit the
Trend Engine's five run-scaling elasticities via a real
`sklearn.linear_model.PoissonRegressor` GLM against 1,136 real completed
games, 2026-05-04 through 2026-07-31 (vs. the prior fit's 629 games) --
reusing `ESPNMLBClient.reconstructed_features` and
`models.mlb._offense_index`/`_starter_weakness` directly (the same
functions the live formula calls), so the design matrix matches production
feature construction exactly. Same 4-fold chronological expanding-window
CV structure as the original fit, this time with fold-level results
actually saved. One mid-run bug found and fixed: a transient ESPN 500
crashed the first attempt because `collect_rows` only caught
`(KeyError, TypeError, ValueError)` around `reconstructed_features`, not
`httpx.HTTPError` -- fixed to catch it, log, and continue (without
marking the event permanently skipped, so a rerun retries it) rather than
losing a multi-hour collection run to one bad request. The on-disk cache
meant no data was lost from the crash either way.

New elasticities (`mlb-analyst-poisson-trend-v0.3.yaml`): offense
0.035->0.088, starter_weakness 0.211->0.281, park 0.222->0.259, weather
0.021->0.040. Bullpen: previously forced to 0.0 (v0.2's fit found it
consistently negative -- implausible, attributed to selection bias in
which relief innings get recorded). This run's bullpen-included fit shows
a *positive* coefficient in every one of the 4 folds (0.170, 0.050, 0.044,
0.013) -- the documented governance rule ("only overturn 0.0 if every fold
is positive") is technically satisfied, so `bullpen_elasticity=0.069` is
now live in v0.3. Flagged explicitly rather than presented as settled: the
per-fold magnitude is monotonically *declining* by an order of magnitude
across the 4 folds as more data accumulates, which is at least as
consistent with a fading spurious correlation as with a real small
positive effect. Worth specifically re-checking next time this is refit
with more data before trusting it further.

`scripts/mlb_measured_edge_calibrate.py` (new, saved/rerunnable) refit
scale/offset for both margin and totals via OLS against two separate,
non-blended windows: (a) diagnostic -- `reconstruct-mlb-markets` extended
`data/historical/mlb_market_lines_reconstructed.jsonl` from 162 to 340
games (2026-07-01 through 2026-08-01, ESPN-reconstructed, hard-labeled
`timestamp_valid=False`); 290/284 of those games had usable spread/total
lines and cached features. (b) real-market -- genuine captured Polymarket
BBO from `data/odds/mlb/2026-07-17` through `2026-08-01`, joined to the
feature cache by (game_date, away team, home team) rather than event_id,
since Polymarket's own event_id is an unrelated internal id (a real bug
initially: the first calibration run matched 0 real-market rows because it
joined on event_id directly; fixed by building a team-pair+date index and
splitting Polymarket's `event_title` on " vs. "). Also corrects
`calibration_note`'s v1-era mischaracterization of the diagnostic window
as "real historical Polymarket-reconstructed spread lines" -- it has
always been ESPN's postgame reconstruction, diagnostic-only per
`ingest.py::reconstruct_mlb_markets`'s own hard `timestamp_valid: False`
label.

**Margin against the fresh, larger diagnostic window: a real, corroborated
improvement, with either elasticity set.** v1 (162 games, 2026-07-01..
07-12): correlation 0.062, 80 picks, 53.7% hit rate, +2.09 units. Refit
against the new v0.3 elasticities (290 games, 2026-07-01..08-01):
correlation 0.208, 285 picks, 60.0% hit rate, +41.45 units. Refit with
v0.2's *original*, unchanged elasticities on the exact same fresh 290-game
window: correlation 0.206, 289 picks, 59.5% hit rate, +39.36 units --
essentially identical. **Conclusion: margin's whole improvement came from
the fresher/larger calibration window, not from the elasticity refit
itself.**

**Totals: the v0.3 elasticities actively regressed it -- diagnosed and
fixed by keeping v0.2's elasticities.** First attempt (calibrating totals
against the new v0.3 elasticities) regressed badly against v1: correlation
0.166->0.041, hit rate 56.0%->52.9%, units +6.36->+0.73; real-market
corroboration (genuine Polymarket BBO) went further negative -- only 8
qualifying picks, 37.5% hit rate, **-2.27 units**, actively losing money.
Investigated rather than accepted: re-ran the exact same fresh 290-game
diagnostic window and 65-game real-market window with v0.2's *unchanged*
elasticities instead of v0.3's. Result: correlation 0.0585 (vs. 0.0414
under v0.3), hit rate 55.3% (vs. 52.9%), units +6.82 (vs. +0.73); real-
market corroboration jumped from 8 qualifying picks to **37**, hit rate
56.8%, **+3.09 units** -- flips from losing to profitable. Root cause: the
elasticity refit's GLM was fit against each *individual team's* runs
scored, a margin-shaped objective (which team scores more). Reusing that
same fitted elasticity set for totals (which cares about the *sum* of both
teams' runs) is a real objective mismatch -- elasticities that sharpen the
margin signal can inflate the run estimate's variance in ways that hurt
totals calibration specifically, even though both markets share one
`estimate_runs()` formula. **The promoted totals-v2 therefore uses v0.2's
original elasticities, not v0.3's** -- this also means `ENGINE_VERSION`
never needed to change (see below), sidestepping the margin/totals
coupling problem entirely.

Both heads independently re-checked a third way, against the 47 real
already-settled `measured-edge-{margin,totals}-v1` picks in
`data/flat_picks.xlsx` (not fresh calibration data -- recomputed via
`scripts/mlb_measured_edge_compare_settled.py` what v2, using v0.2's
elasticities, would have said for those exact same games/selections,
graded against their real known outcomes): margin (24 picks) Brier 0.2312
-> 0.2183, same 17/24 direction-correct; totals (23 picks) Brier 0.2448 ->
0.2489 (slightly worse) but direction-correct 9/23 -> 11/23 (better) --
mixed and far too small a sample to read much into on its own, but
consistent with "no regression" rather than the alarming totals collapse
seen under v0.3's elasticities.

**`mlb-analyst-poisson-trend-v0.3.yaml` stays on disk as a documented
research artifact, not promoted.** Its real value turned out to be
negative-result knowledge (a single shared elasticity set doesn't serve
both margin and totals; the bullpen finding below), not a better formula.
Also worth flagging on its own terms, independent of the promotion
decision: v0.3's bullpen-included fit shows a *positive* coefficient in
every one of its 4 CV folds (0.170, 0.050, 0.044, 0.013), technically
clearing the "overturn v0.2's forced-0.0 only if every fold is positive"
bar -- but the per-fold magnitude is monotonically *declining* by an order
of magnitude as more data accumulates, at least as consistent with a
fading spurious correlation as a real small positive effect. Not carried
into the promoted totals-v2 (which uses v0.2's elasticities, bullpen still
0.0); worth specifically re-checking if `mlb_elasticity_refit.py` is ever
rerun with more data.

**Promoted.** `MARGIN_MODEL_VERSION`/`TOTALS_MODEL_VERSION` in
`models/mlb.py` -> `measured-edge-{margin,totals}-v2`; `ENGINE_VERSION`
**unchanged** (`mlb-analyst-poisson-trend-v0.2` -- both v2 artifacts'
`base_score_model_version` is v0.2, verified via `_load_artifact`'s own
hash/version checks before promoting, and by actually instantiating
`MeasuredEdgeMarginModel`/`MeasuredEdgeTotalsModel` against the real files).
Six hardcoded `-v1.json`/`"measured-edge-*-v1"` references in `cli.py`
(lines ~661-662, 736, 817-818, 886) updated to `-v2`. v1 artifacts and
every v1 ledger row left on disk untouched -- nothing archived or deleted,
matching this project's versioned-artifact convention (old versions
coexist by filename; only the live pointer moves).

**Verified live in production, not just in scripts.** Ran the real `daily`
command end to end for 2026-08-02 (full multi-sport cycle -- BBO capture
across 9 sports, all forecasting, settlement, MLB availability capture --
not an MLB-only dry run). MLB Measured Edge: 15 scheduled games, 2 skipped
(unresolved probable starter, routine), 26 candidates logged to the flat
ledger (13 spread + 13 total) carrying `model_versions:
["measured-edge-margin-v2", "measured-edge-totals-v2"]`; confirmed
directly in `data/flat_picks.xlsx` (19 new margin-v2 + 19 new totals-v2
rows for 2026-08-02, alongside the untouched v1 history). `step5c_mlb_availability`
also ran cleanly in the same real cycle: 30 teams, 3,572 real transaction
entries captured.

**Tests**: `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` ->
**546 passed**, 0 failed (524 baseline + 14 new `test_mlb_availability.py`
+ 6 new `test_mlb_elasticity_refit.py` + 2 new
`test_espn_probables.py` additions; `test_mlb_measured_edge_calibration.py`
and its production-artifact-loading test updated from v1 to v2 filenames/
version strings to match the promotion). `.venv/bin/ruff check src/ tests/`
-> 117 findings, at/under the established 118 baseline (no new findings
from anything touched this session).

### Same day, later: two real bugs found and fixed in Part 1's shadow feature via ad hoc review of the real captured data

Reviewing today's own real captured `data/availability/mlb/` snapshots
(not synthetic fixtures) for anything the tests might have missed surfaced
two genuine gaps:

1. **Missing "rehab assignment" marker.** Real MLB Stats API transaction
   descriptions include 291 distinct "sent RHP X on a rehab assignment to
   Y" entries -- a player still recovering, not yet activated -- that
   matched neither `AVAILABLE_TRANSACTION_MARKERS` nor
   `UNAVAILABLE_TRANSACTION_MARKERS`, so they were silently skipped. Low-
   impact when the original "placed on the X-day injured list" transaction
   is also within the capture window (the earlier entry still wins as the
   latest *relevant* one), but a real gap when it isn't -- e.g. a 60-Day IL
   player whose placement predates the transactions capture's 60-day
   rolling lookback (`cli.py`'s `_capture_mlb_availability`) but who has a
   recent, in-window rehab-assignment transaction. Fixed by adding
   `"rehab assignment"` to `UNAVAILABLE_TRANSACTION_MARKERS`; verified no
   false-positive risk (zero real transactions in the captured data contain
   both "rehab" and "activated," so a genuine return-from-rehab -- its own
   separate "activated from the X-day injured list" transaction -- still
   correctly overrides it, since `_starter_status` checks the available
   marker first).

2. **Roster snapshots were captured but never read.** `cli.py`'s daily step
   already calls `capture_roster_snapshot` (confirmed real files exist,
   e.g. `data/availability/mlb/snapshots/2026-08-01/roster-*.json`), but
   `features/mlb_player_availability.py` only ever consulted the
   transactions-based reconstruction -- the roster snapshot's direct,
   current-status read was dead weight. This meant the transactions path's
   lookback-window blind spot (above) had no real backstop for a live
   decision. Fixed: `matchup_player_availability` now checks a fresh
   (within `maximum_report_age_hours`, strictly *before* the decision time
   -- a capture from after `observed_at` is never eligible, since a
   live-only signal from the future would leak knowledge the decision
   couldn't have had) roster snapshot first for each side, falling back to
   the transactions-based reconstruction only when nothing sufficiently
   fresh exists (the only option for a genuinely historical/backtest
   decision, since a live roster read can never cover the past). An
   unrecognized roster status now fails closed
   (`NO_CALL_MLB_AVAILABILITY_STATUS_UNKNOWN`) rather than being silently
   ignored, unlike free-text transaction descriptions -- roster status is a
   small, closed, well-known enum (`STATUS_ACTIVE_PROBABILITIES`), so an
   unrecognized value is a real signal something changed upstream, not
   routine noise. Result now also exposes
   `{home,away}_probable_starter_source` (`"roster"` or `"transactions"`)
   so which path resolved each side is always visible. Verified live: found
   a real IL player (Adam Frazier, Injured 10-Day, Los Angeles Angels) on
   today's actual roster snapshot and confirmed the feature correctly
   resolves him via the roster path with `probable_starter_unavailable=1.0`.

**Tests**: 7 new (`test_rehab_assignment_flags_starter_unavailable_even_without_an_in_window_il_placement`,
`test_activation_after_rehab_assignment_still_clears_the_flag`,
`test_fresh_roster_snapshot_is_preferred_over_transactions`,
`test_roster_beyond_an_old_il_placement_still_catches_unavailability`,
`test_stale_roster_snapshot_falls_back_to_transactions`,
`test_roster_snapshot_captured_after_the_decision_is_never_used`,
`test_unrecognized_roster_status_fails_closed`), all in
`tests/test_mlb_availability.py`. Full suite: **553 passed**, 0 failed.
`.venv/bin/ruff check src/ tests/` -> 117 findings, still at/under baseline.

## 2026-08-01 — full reproduction-checklist re-run, end to end, against the state left by everything above

After four consecutive full-project review passes and a large number of
real fixes (side-selection logic in three sports, CLV wiring for three
more, a soccer team-name collision, a KBO/NPB home/away label risk, MLB
calibration governance, an esports methodology rebuilt from v4 to v5, a
four-implementation Elo consolidation, a dead sizing parameter finally
read, the unit range widened, and a real gap in the live-money SELL path)
it was worth re-running this file's own "Reproduction commands" checklist
in full, top to bottom, exactly as written, rather than trusting that the
individual per-fix verification passes add up to a system that's still
coherent as a whole. Every command below was actually executed against
the live repository state in this exact session, not paraphrased from
memory of what earlier per-fix checks showed.

**Tests**: `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` ->
**524 passed**, 0 failed, 0 skipped, 0 errors, 16.33s wall time. This is
the same 524 reported at the end of the fourth pass above — meaning
nothing in the interval between that pass's own verification and this
independent re-run introduced any regression, and no test is flaky at the
"run it twice in a row" level (a real concern after finding a genuine
time-of-day-dependent flaky test in the fourth pass above — this re-run
is itself partial evidence, though not proof, that no *other* latent
timing-flakiness exists elsewhere in the suite).

**Critical imports**: `model_prediction.cli`, `.validation`,
`.learned_forward`, `.eligibility`, `.ledger`, `.forward`, `.audit`,
`.xlsx_ledger` all import cleanly with no circular-import errors — worth
specifically re-checking given this session added new cross-module imports
that didn't exist before (`esports.py`/`international_baseball.py`/
`models/tennis.py` all now import from `features/elo_ratings.py`,
`cli.py` now imports `refresh_recent_international_baseball_matches` from
`international_baseball.py`) — a real, if usually easy-to-catch-at-import-time,
risk any time new inter-module edges get added.

**CLI**: `.venv/bin/model-prediction --help` exits cleanly; Python
3.14.5, matching every other verification pass this session (no
interpreter drift).

**`verify-chain`** (the audit-log tamper-detection / ledger-reconciliation
check): `break_count: 0`, `chain_intact: true`, `rows_missing_creation_event: []`.
`audit_lines` now at 34,748 (up from the low-9000s at the very start of
this session's work — reflecting every archival, restoration, settlement,
and order-related audit event this entire session generated, all still
individually verifiable and none breaking the hash chain).

**Canonical artifact hashes**: swept every JSON file under
`config/models/` (35 files as of this run — up from 33 at session start:
the esports v5 rebuild added 5 new artifacts, and the retired v4 esports
artifacts were deliberately left on disk as historical record rather than
deleted, same convention as MLB's retired v5/v6). 33 of 35 recompute to
their own declared hash correctly. The 2 mismatches
(`nba-spread-baseline-v1.json`, `nfl-spread-baseline-v1.json`) are the
exact same pre-existing, already-investigated pair found and diagnosed
earlier this session — confirmed via `grep` across the entire `src/`
tree that the config keys pointing at them
(`spread_research_artifact`/`total_research_artifact` for NBA/NFL) are
never read by any code path at all, so this remains a dormant,
zero-blast-radius provenance inconsistency (most likely: someone
hand-edited these two specific files after their hash was originally
frozen, adding the "Outcome matching pending" annotation visible in their
JSON, without recomputing the hash) rather than anything newly introduced.
Not re-fixed this pass, for the same reason it wasn't fixed when first
found: touching a hash-verification artifact that nothing consumes is
lower-value than the real, live-code-reachable findings this session
prioritized instead.

**Config artifact resolution**: swept every `production_artifact`/
`research_artifact`/`spread_research_artifact`/`total_research_artifact`/
`artifact` key across every sport block in `config/model.yaml`, checking
each declared path actually exists on disk. One miss:
`market_residual.artifact -> config/models/market-residual-v1.json` — the
same pre-existing, already-diagnosed dormant finding (a manual-only
`train-residual` CLI command's own output path; nothing in `daily` or any
other automatic path ever reads or writes it, so a config entry pointing
at a file that has simply never been generated yet is expected, not
broken).

**Runtime, without writes**: `model-prediction summary` returned real,
current numbers — `model_drift.MLB: {"holdout_hr": 0.5847, "live_hr": 0.8,
"n": 10, "status": "on_track", "z_score": 1.38}` (the v7 model's real
walk-forward holdout hit rate vs. its real recent live hit rate, still
within a statistically unremarkable z-score of each other — no drift
alarm), `open_picks: 18`, `picks_settled_today: 9`,
`qualified_pnl_units_all_time: 2.0411`. Dashboard `/api/health` returned
`{"ok": true}`; `/api/status` reported `models_loaded: 36` (up from 35 at
the session's earlier mid-point check — the esports v5 rebuild's 5 new
artifacts, net of nothing being removed from the loaded set) and real,
currently-advancing per-sport game counts, notably **kbo: 7039** and
**npb: 3925** — both higher than the 7,034/3,925 (kbo)/(already-current
npb) counts seen immediately after this session's own KBO/NPB staleness
fix was first deployed a few hours earlier, live confirmation that the
new `_refresh_international_baseball_ratings` step is continuing to pull
forward on its own during normal operation, not just the one time it was
manually invoked to prove the fix worked.

**Ruff**: `ruff check src/ tests/` -> **118 findings, byte-identical to
the baseline** established at the very start of this session and
re-confirmed after every single fix along the way — meaning across
everything this session touched (a genuine two-digit number of source
files across models, ledger core, dashboard, CLI, and tests), the net new
ruff-visible issue count is exactly zero. (`dashboard_server.py` carries
its own separate, never-tracked-as-baseline 47 findings, confirmed via a
targeted line-range check to be entirely pre-existing and untouched by
this session's specific edits to that file.)

**`daily`** (the actual live production pipeline — settlement, ingestion,
and the full forecast/logging pass across every sport, now including the
newly-wired `_refresh_international_baseball_ratings` step from earlier
this session): exceeded the 120-second foreground timeout as expected
(this command always does against real production data) and completed in
the background — **exit code 0**, and a full scan of its complete JSON
output for any `"failures": [...]` entry, any `error`/`Error`/`Traceback`
string, anywhere across every sport's settlement/forecast block, found
**nothing**. Re-ran `verify-chain` and the full test suite immediately
afterward, specifically because `daily` is the one command in this whole
checklist that actually *mutates* live ledger/audit state rather than
just reading it: `verify-chain` still `break_count: 0`/`chain_intact:
true` (audit log grew from 34,748 to a further-advanced line count,
entirely from this one real run, with zero breaks introduced), and the
full suite still **524/524 passed** — confirming the live pipeline run
itself didn't leave the repository in a state that breaks anything this
checklist already re-verified moments before.

This is, deliberately, the single most boring section in this entire
document — every number in it either matched what was already expected
from the individual fixes' own verification, or was a pre-existing, only
findings, nothing new, everything green. That is exactly the intended
outcome of running a full reproduction checklist after a long session of
changes: not to discover something new, but to confirm that the sum of
many individually-verified small changes is still, in fact, a coherent,
still-passing whole.

## 2026-08-01 — fourth full-project pass: a real gap in the live-money SELL path, a dormant P&L-formula duplication risk closed with a safeguard test instead of a refactor, one rate-limit-diagnostics finding investigated and deliberately left alone, and a self-inflicted test flakiness bug found and fixed along the way

Fourth parallel read-only review pass, this one scoped to
`dashboard_server.py`'s own computation logic (its wiring to the rest of
the project had already been checked in earlier passes; this time the
question was "is the arithmetic and the safety-gate logic *inside* this
file correct") and `data_sources/*.py` (the actual HTTP-fetching/parsing
layer every model depends on — ESPN, Polymarket, The Odds API, official
KBO/NPB pages). Same discipline as the three passes before it: every
finding traced and verified directly against the code, nothing acted on
from the review agent's word alone.

### Fixed: BUY orders check quote freshness and game-start; SELL orders checked neither

This is the most consequential finding of the whole four-pass series
because it sits directly on the real-money order-execution path, so it's
documented here in full rather than summarized.

**How it was found**: the review agent flagged that `_order_readiness`
(`dashboard_server.py`) — which requires a quote to be fresh (observed
within 5 minutes), the market to be `MARKET_STATE_OPEN`, and the game to
not have already started, before a BUY can proceed — is never called at
all for SELL actions. `preview_order`'s and `submit_order`'s sell branches
each independently check only `bid is not None and price <= bid` (a
"don't cross the resting bid" check) and nothing else. There's an
explicit, deliberate-reading comment in the code: *"Buys require the
buy-readiness gate. Sells are exits and only require an executable quote
(you can always try to close a position you hold)."* — meaning this wasn't
an oversight so much as an intentional design choice that turned out to
have a real, unconsidered consequence.

**Why it matters, traced all the way through rather than assumed**:
`_pick_quote` (the function both BUY and SELL rely on for a quote)
explicitly and permanently excludes any Polymarket snapshot observed at or
after `event_start_utc` — by design, since this project has no live
in-game pricing mechanism at all, only pregame BBO capture. That means the
instant a game starts, `_pick_quote` for that row freezes forever at
whatever the last pregame snapshot happened to be — it will never update
again, not in five minutes, not ever, for the rest of that row's life.
For BUY, this is caught immediately: `_order_readiness`'s "game has
already started" check refuses the order outright. For SELL, nothing
catches it — a user (or a script acting on their behalf) could submit a
resting sell limit that gets validated against a bid frozen from hours (or
days) before, with the dashboard reporting it as a normal "doesn't cross
the bid" resting order, when the real, current market could be
*anywhere*. This isn't a "your information might be a few minutes stale"
risk like ordinary quote staleness — it's a structural guarantee that the
comparison being used is definitionally disconnected from the live market
the moment a game goes live.

**Checked whether a deeper layer catches this anyway, rather than assuming
the dashboard is the only gate**: read the entire `PolymarketExecutor.execute()`
gate chain in `data_sources/polymarket_execute.py` line by line — ticket-
to-row identity binding, server-recomputed cost (never trusts the caller's
`estimated_cost_usd`), qualification/artifact-qualified distinction,
open-status check, price/size sanity bounds, whole-cent tick enforcement,
unit-cap enforcement, order-type whitelist, interactive human confirmation,
audit-chain write. Genuinely thorough — and genuinely contains **zero**
staleness or market-state checks anywhere, for either buy or sell. The
dashboard's `_order_readiness` (skipped for sells) really was the only
line of defense in the entire system for this specific failure mode.

**Asked before fixing, because this is a real, deliberate-looking design
choice, not an obvious oversight**: presented three options — block sells
once the game has started (narrowest fix, preserves "always allowed to
try exiting" for genuine pregame staleness, which the removed comment was
plausibly written to protect); make sells fully symmetric with buys
(refuse any sell, pregame or in-game, past 5 minutes of quote age); or
leave sell behavior untouched and treat the human confirmation step as
sufficient protection. Operator chose the first, narrowest option.

**Implementation**: extracted the inline "is this event in the past"
check that `_order_readiness` already did into a standalone
`_event_already_started(row) -> bool` helper (previously it was ~6 lines
duplicated nowhere else, now genuinely shared), and added a call to it at
the top of both `preview_order`'s and `submit_order`'s sell branches — a
sell against an in-progress-or-finished game's frozen quote is now
refused with `"game has already started; quote can no longer update"`,
matching the clarity of BUY's existing refusal reasons. Deliberately
re-checked at *both* preview and submit time, not just preview — state
(specifically, whether the game has started) can change in the window
between a user previewing an order and actually submitting it, and
`submit_order` already independently re-validates several other
conditions for exactly this race-window reason, so this needed the same
treatment rather than trusting preview-time state to still hold.

**Verified the fix doesn't regress the legitimate case it wasn't meant to
touch**: `preview_position_sell`/`submit_position_sell` — a second,
entirely separate sell pathway used for closing a position found via
`live_portfolio_view` rather than a specific ledger row — turned out to
already use `_live_bbo(slug)`, a genuine real-time exchange query, not the
frozen pregame cache `_pick_quote` relies on. Read through both functions
end to end to confirm this before concluding no fix was needed there; this
second pathway was never vulnerable to the same failure mode in the first
place, so it was correctly left untouched.

Three new tests, all against the exact `preview_order`/`submit_order`
functions rather than a simulated subset of their logic: a sell refused
once `event_start_utc` is in the past; a sell still allowed (unchanged
behavior) when `event_start_utc` is in the future; and a preview-then-
submit race test where the game starts in the window between the two
calls, confirming `submit_order`'s independent re-check catches it even
when `preview_order` originally allowed it. All three, plus the full
existing 42-test dashboard suite, passed with zero other changes needed —
this exact code path (the ledger-row-based sell branches specifically, as
opposed to the separate position-sell pathway) had **zero** prior test
coverage at all before this pass, buy or sell, which is very plausibly
why this gap went unnoticed through however many sessions of prior work
touched this file.

### Investigated, deliberately fixed with a test instead of a refactor: `_decorate_pick`'s hand-duplicated P&L fallback

`_decorate_pick` recomputes win/loss profit from raw American odds by hand
whenever both `pnl_units` and `research_pnl_units` are zero/blank — a
second, independently-written copy of `pricing.profit_units`'s math,
living in the dashboard rather than imported from the package. Currently
arithmetically identical, but a second hand-maintained copy of settlement
math is exactly the class of risk this whole four-pass series kept
surfacing elsewhere (the Elo formula duplicated four times, `_team_matches`
almost duplicated a second time in the CLV work before being reused
instead).

**Checked how often this fallback actually fires against real data before
deciding how seriously to treat it**: swept every settled row across all
four live ledgers (Main, Flat, Research, Gated Research) checking whether
both `pnl_units` and `research_pnl_units` were ever simultaneously
zero/blank on a real win/loss row. Real count: **zero**. Every settled row
`ledger.settle()` has ever touched already carries a real, correctly-
computed P&L value from the one true implementation; this fallback exists
purely as a defensive net for malformed or legacy data that has never
actually needed to fire.

**Why the obvious fix (import `pricing.profit_units`) was deliberately not
taken**: `dashboard_server.py` has zero imports from the `model_prediction`
package anywhere in its ~4,700 lines — confirmed by grepping the whole
file for `from model_prediction` / `import model_prediction`, finding
nothing, and checking there's no `sys.path` manipulation making the
package importable either. This is a long-standing, consistent,
file-wide pattern (openpyxl itself is even wrapped in a try/except
optional-import), strongly suggesting a deliberate design goal: keep this
file runnable as a genuinely standalone script with no dependency on the
full modeling package's import graph. Introducing the *first-ever*
cross-import here, to fix a formula divergence risk that currently affects
zero real rows, would be a disproportionate architectural change relative
to the actual risk.

**What was done instead**: added a comment at the fallback site explaining
exactly why it's not an import and pointing at the safeguard; added
`tests/test_dashboard_server.py::test_pnl_fallback_formula_matches_pricing_profit_units`,
which sweeps a representative grid of American odds (-500 through +1000)
and unit sizes (0.5 through 2.0) across both win and loss outcomes and
asserts the dashboard's hand-rolled formula produces bit-for-bit the same
answer as the real `pricing.profit_units` for every combination. If either
formula is ever edited without updating the other, this test fails loudly
the next time the suite runs — the actual risk (silent divergence) is
closed without paying the architectural cost of the more invasive fix.

### Investigated and deliberately left alone: rate-limit errors from The Odds API are indistinguishable from a genuine "no market" outcome

`MLBMarketOddsFeed.load()`/`for_game()` (`data_sources/mlb_market_odds.py`)
catch `httpx.HTTPError` (which includes `HTTPStatusError`, e.g. a real 429
rate-limit response) around both the Polymarket and Odds API fetches, and
both a genuine rate-limit and a genuine "there's really no market for this
game today" collapse into the same `MarketUnavailableError
("NO_CALL_MARKET_UNAVAILABLE (...)")` exception type. The error detail
string embedded in the message *does* already distinguish them
(`type(error).__name__` — "HTTPStatusError" vs. e.g. "ConnectError" or
"TimeoutException" — is recorded verbatim), but nothing downstream parses
that string; every caller just treats the exception as "skip this game."

Checked whether this actually causes a different real-world outcome
between the two cases before deciding whether to fix it: it doesn't.
This project has no in-process retry logic anywhere for this kind of
transient failure — every model's real recovery mechanism is the
already-installed, already-verified launchd job re-running the full daily
pipeline every 3 hours regardless of what happened the run before. A
rate-limited fetch this run and a genuinely-absent market this run produce
the exact same operator-visible outcome (one game skipped, picked back up
automatically next cycle if it was transient) either way. The only real
value of distinguishing the two would be operator debugging convenience
("why didn't this game get priced" — being able to tell "we got
rate-limited" from "there's really no market" faster), not correctness.
Left alone as a legitimate but low-value diagnostics improvement, not a
bug — the lowest-severity of the three findings from this pass, correctly
so.

### A self-inflicted bug, found while re-running the suite after the above: a CLV test computed its own snapshot date from raw UTC instead of Eastern time

While re-running the full suite to confirm the SELL-path fix,
`tests/test_fix_regressions.py::test_esports_settlement_populates_clv_from_captured_closing_snapshot`
(written earlier this session, during the CLV wiring work) failed for the
first time — not because of anything touched in this pass, but because
real wall-clock time had drifted close enough to a UTC/US-Eastern day
boundary to expose a latent bug in the test's own fixture setup: the test
computed the snapshot's storage date via
`datetime.fromisoformat(event_start).date().isoformat()` (raw UTC date),
while the actual production code under test
(`_closing_probability_for_moneyline_pick`) correctly computes
`game_date` via `.astimezone(EASTERN).date().isoformat()`. These two
computations agree for the vast majority of the day and disagree for a
few hours near midnight UTC (which is late afternoon/evening US-Eastern,
depending on DST) — meaning this test was quietly relying on being run at
a time of day where the mismatch didn't matter, and finally wasn't. Fixed
by making the test compute `game_date` identically to the production code
it's testing, which is what it always should have done — a reminder that
"uses `datetime.now()`-relative fixture data" tests need to mirror
timezone-conversion logic exactly, not approximately, or they become
silently time-of-day-dependent. Re-ran the full suite immediately after:
524/524 passed, no other casualties from the same class of issue found.

### Verification

524/524 tests passed (4 net new: 3 for the SELL-path fix, 1 for the P&L
formula safeguard), `ruff check src/ tests/` unchanged at the 118-finding
baseline (confirmed dashboard_server.py's own separate 47 pre-existing
findings, never part of this tracked baseline, were unaffected by this
pass's edits by checking the specific line ranges touched), `verify-chain`
0 breaks, dashboard server process killed and restarted to load the new
code (confirmed via `/api/health` and a clean `server.log` with no
startup errors).

## 2026-07-31 (latest, latest) — the sizing formula's dead parameter, finally fixed; the unit range widened to 1U-2U; full derivation, full blast-radius accounting, full before/after arithmetic

This section is deliberately exhaustive — every number in it was recomputed
by hand against the actual formula in `units.py`, not paraphrased from a
test's pass/fail status, because the previous version of this exact
formula shipped for an unknown but clearly nonzero length of time with a
parameter that was *accepted, threaded through six call sites, and never
read*, and the reason nobody caught it for that long is that every single
one of those six call sites, and every existing test, was content to check
"does this produce *a* plausible-looking unit size," never "does changing
*only* the uncertainty input change the output at all." That is exactly
the kind of bug that a five-word regression test closes forever, so this
section spells out the derivation in full precisely so nobody has to
re-derive it from scratch the next time this formula's correctness is in
question.

### The formula, before

```python
def edge_scaled_units(model_probability, model_uncertainty, american_odds, policy=UnitPolicy()):
    edge = abs(model_probability - 0.5)
    raw = policy.min_pick_units + edge * (policy.max_pick_units - policy.min_pick_units) / 0.15
    units = max(policy.min_pick_units, min(policy.max_pick_units, raw))
    units = round(units / policy.unit_increment) * policy.unit_increment
    return min(units, policy.max_pick_units)
```

Trace every reference to `model_uncertainty` in that body: there are
zero. It is a formal parameter, appears in the function's own signature,
appears in every caller's argument list, and is never read. Six call
sites pass a real, meaningfully-varying value into it for nothing:
`eligibility._call_result` (the real qualified-CALL sizing path, used by
every sport's Main/Flat ledger row), `eligibility._research` and
`eligibility._downgrade_research_call` (the "every pick gets units" paths
fixed earlier this session), `ledger.recompute_research_sizing` (the
backfill helper used three separate times already this session), the
`model_recommended` scoring mode inside `ledger.settle()`, and
`recommend_units`'s own internal override (see below — this one is
subtler and gets its own subsection). Every one of those six believed it
was passing uncertainty-aware information into the sizing calculation.
None of them were.

### Concretely, what this meant in practice

Two picks, same `model_probability = 0.65`, same `american_odds = -110`:

- Pick A: `model_uncertainty = 0.01` (the model is very sure of this 65%)
- Pick B: `model_uncertainty = 0.20` (the model computed 65% but flagged
  enormous uncertainty around that estimate — maybe thin recent data, a
  team with almost no rating history, a feature that had to default to
  neutral because real data was unavailable)

Before this fix: **both got sized identically.** `edge =
abs(0.65-0.5) = 0.15`, which is already at the formula's own saturation
point (`0.15` is the denominator — any edge at or above it saturates to
`max_pick_units`), so both Pick A and Pick B were sized at the maximum,
2.0U, with zero distinction between "the model is confident and right" and
"the model computed a number it doesn't really trust." A system that is
supposed to size bets by conviction had, for its most standard sizing
path, no mechanism whatsoever connecting stated uncertainty to stake size.

### The fix

```python
adjusted_edge = max(0.0, abs(model_probability - 0.5) - max(0.0, model_uncertainty))
raw = policy.min_pick_units + adjusted_edge * (policy.max_pick_units - policy.min_pick_units) / 0.15
```

The uncertainty is subtracted from the raw distance-from-50/50 *before*
that distance gets scaled into a unit size, floored at zero (an
uncertainty larger than the raw edge collapses the adjusted edge to
nothing, never a negative number, never inflating sizing). This is not an
invented convention — it is the exact same "probability minus uncertainty"
conservatism `recommend_units`'s own accept/reject gate already applied a
few lines below (`adjusted_probability = model_probability -
model_uncertainty`, used to decide whether the pick clears `min_edge` at
all) — that gate was already correctly uncertainty-aware; the sizing
formula sitting right next to it, doing conceptually the same kind of
haircut, simply never got the memo. Recomputing the two picks above with
the fix: Pick A (`u=0.01`): `adjusted_edge = 0.15 - 0.01 = 0.14`, sizes to
essentially the max, 2.0U. Pick B (`u=0.20`): `adjusted_edge = max(0,
0.15-0.20) = 0.0`, sizes to the floor, now 1.0U (see the range change
below). Same raw probability, now genuinely different stakes, exactly
tracking how much the model itself trusts the number.

### The `recommend_units` half of this bug, and why fixing `edge_scaled_units` alone also fixed it

`recommend_units` (the fuller sizing function, used wherever exposure caps
and a real Kelly comparison matter, not just the raw edge-scaled override)
calls `edge_units = edge_scaled_units(model_probability, model_uncertainty,
american_odds, policy)` and then `units = max(units, edge_units)` — takes
whichever of Kelly-sizing or edge-scaled-sizing is larger. The
full-project logic review flagged this as a *separate*, second-order
problem worth noting on its own: `recommend_units`'s accept/reject gate
uses the properly uncertainty-adjusted probability
(`model_probability - model_uncertainty`), but the `edge_units` line right
below it was handing that same conservatism-blind, dead-parameter function
its `model_uncertainty` argument for nothing — meaning a pick that barely
scraped past the gate specifically *because* of a large uncertainty
haircut could still get sized as if the model were fully confident, via
the `max(units, edge_units)` comparison picking the (wrongly) larger,
uncertainty-blind `edge_units` value over the correctly-smaller Kelly
value. Once `edge_scaled_units` was fixed to actually read
`model_uncertainty`, this entire second-order problem disappeared for
free — `edge_units` is now itself uncertainty-aware, so `max(units,
edge_units)` is comparing two properly-conservative numbers instead of one
conservative and one not. No separate code change was needed in
`recommend_units` itself; it was never the buggy one, it was just
downstream of the one that was.

### The unit range: 0.5U-2.0U -> 1.0U-2.0U

Separate, operator-directed change, made in the same sitting: the
project's own sizing floor/ceiling (`UnitPolicy.min_pick_units`/
`max_pick_units`, both in the `units.py` dataclass default and in
`config/model.yaml`'s `bankroll:` block, which is the value actually used
in production via `config.unit_policy()`) moved from 0.5-2.0 to 1.0-2.0.
1U is now the floor for the *least* confident logged pick (previously
0.5U); 2U remains the ceiling for the *most* confident, unchanged. Because
`edge_scaled_units`'s formula references `policy.min_pick_units`/
`policy.max_pick_units` symbolically rather than hardcoding 0.5/2.0
anywhere in the arithmetic, this was a pure config/default change with
zero formula-code changes required — the existing scaling math
automatically stretches to fill whatever floor-to-ceiling span the policy
declares. Verified this is genuinely true rather than assumed: at
`adjusted_edge=0`, `raw = min_pick_units` exactly (now 1.0, was 0.5); at
`adjusted_edge=0.15` (the saturation point), `raw = min_pick_units +
0.15*(max-min)/0.15 = max_pick_units` regardless of what min/max actually
are. The 0.15 saturation threshold itself (an edge of 15 percentage points
or more maxes out sizing) was deliberately left untouched — that constant
describes "how much edge is 'a lot,'" a property of the *market*/model
calibration, not of the unit range convention, and changing it wasn't part
of what was asked.

### Blast radius, checked exactly rather than assumed

Ran the full test suite immediately after the `edge_scaled_units` formula
fix, before touching the unit range: **515/515 passed, zero test changes
required.** This was initially surprising enough to double-check rather
than accept at face value — confirmed by hand-computing `edge_scaled_units`
before/after for a probability/uncertainty pair with a large uncertainty
value (0.65 / 0.01 vs. 0.65 / 0.20) outside a Python REPL and observing
real, different outputs (2.0 -> 1.0 -> 0.5 as uncertainty rose from 0.01
to 0.20 at the OLD 0.5-2.0 range) — the fix is real, it's just that no
existing test happened to assert an exact `units` value for a call with a
large-enough `model_uncertainty` to cross an 0.25-unit rounding boundary
under the old 0.5-2.0 range. This is itself worth remembering as a
lesson: 515 green tests is not the same claim as "this code path is
covered" — it only means "nothing that already existed noticed."

Widening the unit range to 1.0-2.0 afterward broke exactly one test
(`test_unvalidated_model_is_capped_at_research_minimum`, asserting the old
0.5U floor) — updated to assert 1.0U, the new, correct floor. Added five
new tests specifically exercising the previously-uncovered behavior:
uncertainty monotonically shrinks sizing for a fixed probability;
uncertainty larger than the raw edge floors at `min_pick_units` rather
than going to zero or negative; a (should-never-happen, but must not
silently misbehave) negative `model_uncertainty` is treated as zero rather
than as a sizing *boost*; an exact-50/50 probability still sizes at the
floor regardless of uncertainty (uncertainty only ever shrinks an existing
edge, it doesn't independently gate the floor); and the new 1.0-2.0 range
is asserted directly against the policy object rather than trusted from
downstream test behavior alone.

Full suite after both changes: **520/520 passed** (5 net new tests). Ruff:
118 findings, identical baseline, nothing new. `verify-chain`: 0 breaks.

### Real-data reconciliation — what actually happens to already-logged rows

Deliberately did **not** retroactively resize already-logged
`QUALIFIED_SHADOW_CALL` rows in Main or Flat — this matches this
project's own pre-existing, deliberate convention
(`recompute_research_sizing`'s own docstring: "QUALIFIED_SHADOW_CALL rows
are never touched: their sizing always came from the real, validated,
exposure-capped path, not this one"). A real qualified call's logged size
is treated as an immutable record of the actual decision made at the time,
not something that gets silently rewritten underneath a human who may
already be acting on it. Confirmed this by running
`recompute_research_sizing` against `data/picks.xlsx` directly and
observing `changed: 0` — exactly the expected outcome, not an oversight.

Did re-run `recompute_research_sizing` (with the now-corrected formula and
policy) against every ledger that legitimately holds
`RESEARCH_OBSERVATION` rows -- `data/flat_picks.xlsx` and every
per-sport Research/Gated-Research workbook -- since those are diagnostic,
zero-real-money sizes by explicit design, and this exact recompute
operation has already been used twice earlier this session for
analogous reasons (once to backfill the "every pick has units" fix,
implicitly again during the esports v4->v5 archival). Real counts: 15
rows changed in `flat_picks.xlsx`, 10 in `research/cs2.xlsx`, 7 in
`research/soccer.xlsx`, 40 in `research/tennis.xlsx` (the largest, since
tennis's `status: research` promotion state means essentially its entire
history runs through the uncertainty-blind `_research()` path), 3 in
`research/valorant.xlsx`, 2 each in `research/dota2.xlsx` and
`research/lol.xlsx` — 79 rows total, all now reflecting the corrected
formula and the widened range. `verify-chain` re-checked clean afterward
(0 breaks), full suite re-confirmed green.

## 2026-07-31 (later) — third full-project pass: 4 more real bugs (2 MLB feature, 2 core-ledger dead/miscalibrated parameters), 1 real gap (KBO/NPB rating staleness) fixed with a self-healing fallback

Third round of "find what else can be improved," split across three more
parallel read-only review passes: the shared ledger/eligibility/sizing/
pricing code every sport depends on (untouched by the two earlier passes,
which focused on per-sport model files), the remaining MLB feature
providers not yet reviewed (park factor, weather, bullpen, WNBA
availability, pitcher ERA gap) plus two flagged-but-never-reviewed modules
(`research_cleanup.py`, `research_io.py`), and a dedicated "gaps" pass
looking specifically for missing/half-wired functionality rather than wrong
math. Verified every finding directly against the code before acting, same
discipline as the prior two passes.

### Fixed

**1. MLB: `features/weather.py`'s ballpark coordinate table still keyed the
Athletics under their old franchise name.** `BALLPARK_COORDS["Oakland
Athletics"]` never matched the team name the rest of the pipeline actually
uses -- `park_factors.py` and `mlb_baseline_refresh.py` were both already
updated to ESPN's live `displayName` ("Athletics"), the latter explicitly
excluding "Oakland Athletics" as a legacy name via `_LEGACY_PARK_NAMES`.
`BALLPARK_COORDS.get("Athletics")` returned `None`, silently falling every
Athletics home game to `status="unknown_park"`/`weather_run_factor=1.0` --
losing real weather signal specifically for the team now playing outdoors
in Sacramento, where weather should matter more than most parks, not less.
Fixed: renamed the key and updated coordinates to Sutter Health Park, West
Sacramento (38.5805, -121.5195).

**2. MLB: the live weather feature provider fetched "weather right now"
instead of "weather at first pitch."** `learned_forward.py`'s
`weather_factor` provider called `live_weather(home_team)` with no
timestamp at all, even though `live_weather` already accepts a
`game_start_utc` argument specifically for this, and the real event start
time was already sitting in scope at the call site the whole time -- just
never threaded through the shared 4-argument provider-callback signature.
A slate built hours before first pitch (e.g. a morning cron run building an
evening slate) got a stale "current moment" reading instead of the
forecast for game time -- a real train/serve skew, since
`validation.py`'s training-time weather lookup correctly keys off
`game.start`, not "now". Fixed by widening the provider callback signature
from `(home, away, event_id, game_date)` to `(home, away, event_id,
game_date, event_start)` -- park_factor and probable_starter_era_gap
ignore the new argument, weather_factor now actually uses it.

**3. Core ledger: `edge_scaled_units` silently ignores its own
`model_uncertainty` parameter.** `units.py`: the function accepts
`model_uncertainty` and every caller (`eligibility._call_result`,
`_research`, `_downgrade_research_call`, `ledger.recompute_research_sizing`,
`settle`'s `model_recommended` research-scoring mode, `recommend_units`'s
override) already threads a real, varying value through -- but the
function body never reads it. Two picks with identical
`model_probability` get identically-sized stakes regardless of whether the
model's own uncertainty on that pick is 0.01 or 0.49. Since this is now the
dominant sizing path project-wide (every real CALL, plus every research
observation after the "every pick has units" fix earlier this session),
model confidence has zero effect on stake size anywhere in the system --
only raw distance from 50/50 matters. Not fixed yet in code as of this
writing -- flagged here with enough detail to act on; genuinely changing
a live sizing formula this central deserves an explicit go-ahead and a
before/after backtest comparison, not a silent mid-audit edit.

**4. KBO/NPB: no mechanism kept Elo ratings current (the exact gap already
found and fixed for esports).** Confirmed live before touching anything:
`kbo-tie-aware-elo-v1.json` was 6 days stale, `npb-tie-aware-elo-v1.json`
14 days stale, and nothing in `dashboard_server.py`'s status/alert logic
surfaces *artifact* staleness at all -- only a much looser 30-day check on
the raw games-manifest age, so an operator would see no alert while
ratings quietly rotted for weeks. `esports.py` already had
`refresh_recent_matches` (fetch a short recent window, merge by stable ID
into existing history, never overwrite) wired into `daily` via
`_refresh_esports_ratings`; `international_baseball.py` had no equivalent
-- only a full-window `backfill_international_baseball` that replaces
`games.jsonl` entirely for whatever range it's given, unsafe to run on a
schedule with a short window (would delete every prior season) and
expensive to run with the full multi-year range (2015-present) every day.

Built `refresh_recent_international_baseball_matches`, mirroring esports'
pattern but adapted to this source's actual shape (KBO/NPB's official
schedule client fetches a whole *year* at a time, not a date-windowed
page, so "recent" here means "re-fetch the current calendar year and merge
by `game_id`" rather than a 14-day lookback -- a KBO/NPB season is a few
hundred games, cheap enough to refetch daily, unlike esports' tens of
thousands of matches per title). Wired into `daily` as
`_refresh_international_baseball_ratings`, matching the esports call
exactly (refresh, re-validate, rewrite the artifact and the dashboard's
evidence-consistency snapshot).

**Self-healing fallback (operator-requested, not part of the original
finding)**: if `games.jsonl` doesn't exist for a league at all -- first run
ever, or every prior daily attempt failed before writing anything -- a
current-year-only refresh would permanently strand every earlier season
with no way to recover automatically. `refresh_recent_international_baseball_matches`
checks for this and falls back to a real full `backfill_international_baseball`
(the league's actual `minimum_year`, 2015, through today) in that case,
self-healing without requiring a manual `backfill-international-baseball`
CLI invocation. Also directly addressed a related operator question ("if I
run offline for a week, does that week's data come back?") -- yes, by
construction: since the normal path always re-fetches the *entire* current
calendar year (not an incremental delta since last success), any
in-season gap of any length is fully caught up on the next successful run,
regardless of how long the gap was or whether the immediately-prior
attempt failed. The one real edge case is a gap spanning an actual
calendar-year boundary (e.g. offline Dec 31, back online Jan 5) -- a
current-year-only fetch would never revisit the tail of the old year. Not
a practical risk for these two leagues specifically since KBO/NPB seasons
run March-October; a year-boundary gap only touches the off-season, when
there are no real games to miss.

Two new tests: the normal-path merge (existing older season preserved,
only the current year re-fetched and merged in, verified via a fake client
that records exactly which years were requested) and the fallback path
(no existing file -> every year from `minimum_year` requested, confirming
a real multi-year backfill actually ran instead of a narrow one).
Live-verified against real production data, not just the test suite:
before this fix, `kbo-tie-aware-elo-v1.json`/`npb-tie-aware-elo-v1.json`
were dated 2026-07-25/2026-07-17; ran the new refresh for real (genuine
network calls to the official KBO/NPB schedule pages) and both artifacts'
`trained_through_date` advanced to 2026-07-31/2026-07-26 immediately
afterward.

### Investigated, reported, deliberately not silently fixed

- `recommend_units`'s edge-scaled override sizes off raw (not
  uncertainty-adjusted) probability while its own accept/reject gate uses
  the uncertainty-adjusted one -- compounds finding #3 above. Flagged, not
  changed, for the same reason: a live sizing-formula change needs an
  explicit go-ahead.
- `PickLedger.exposure()`'s same-bet self-collision exclusion matches only
  `event_id + market_type + selection`, not `line`/`sportsbook` -- two
  genuinely different open positions (a moved line, or the same bet shopped
  across two books) could be treated as one decision's own prior instance
  and excluded from exposure sums, undercounting real risk. Flagged,
  currently low real-world impact since exposure caps no longer gate
  CALL vs. NO_CALL project-wide (per this session's earlier "operator
  directive, 2026-07-26" work) -- exposure is now informational, not a
  live safety gate, so this doesn't currently risk over-betting.
- `_market_duplicate_key` (the ledger's dedupe key) excludes `selection`
  by design (correct for 2-way spread/total markets, where both sides can
  never both be legitimate), but soccer moneyline is a real 3-way market
  (home/draw/away) and the key would treat a "home" pick and a "draw" pick
  on the same event/book/model as duplicates of each other. Checked
  whether this is reachable: soccer's actual moneyline selection logic only
  ever picks `max(("home","away"), ...)` -- "draw" is never constructed as
  a `PickRequest` selection anywhere in the codebase today, so this is a
  real latent risk, not a currently-firing bug. Flagged for whoever adds a
  real draw market in the future rather than fixed now, since the fix
  (excluding `selection` from the key is deliberate for other markets)
  needs a market-type-aware key, not a blanket change.
- `research_cleanup.py` (421 lines, previously assumed by an earlier
  review pass to be an active "supersession detection" system) turned out
  to be dead code in production -- its only caller is a manual one-time
  migration script (`scripts/clean_split_research_ledgers.py`), not
  anything `daily` or any other live path invokes. Its own dedup key
  (`_decision_key`) omits `decision`/`record_type`, which could in
  principle discard a genuine CALL in favor of an earlier NO_CALL row for
  the same market -- worth knowing if this script is ever run again, but
  not an active risk today since nothing calls it automatically.

## 2026-07-31 (later) — esports v4 -> v5 rebuild: proper scoring rules replace a P&L-proxy hyperparameter search, plus a 4-implementation Elo consolidation

Follow-up to the "esports/soccer validation" section below, which flagged
`SPORT_K_OVERRIDE` hardcoding K=96 (the exact top of `K_CANDIDATES`) for 4 of
5 titles but explicitly declined to touch it, citing genuine ambiguity from
only one validation/test split. Re-investigated after a fresh full-project
logic review flagged the same symptom independently and the operator asked
for it to actually be fixed this time.

**Root cause, now nailed down precisely (previous investigation didn't have
this)**: K selection was `max(candidate_scores, key=units_at_minus_110)` — a
raw, unnormalized flat-stake P&L sum — instead of `_metrics`' own `brier`
field, which was already computed on every candidate the whole time and
simply never used for selection (a stale docstring literally said "v4: was
min Brier", i.e. an earlier lineage got this right and v4 regressed it).
K is a pure calibration hyperparameter with no volume/quality tradeoff, so
selecting it by a P&L proxy instead of a proper scoring rule has no
principled justification.

**Fix, in two parts, verified empirically against real historical data
(not assumed) before shipping either one:**

1. K-factor: switched to `min(candidate_scores, key=brier)`. Also widened
   `K_CANDIDATES` from `(..., 96.0)` to `(..., 96.0, 112.0, 128.0)` since a
   grid-edge optimum is itself evidence the search was truncated, regardless
   of which metric picks it. Re-ran the real grid search against current
   data (larger than the last investigation's) with the new criterion:
   chosen K landed at 32/32/40/48/32 for lol/cs2/dota2/valorant/rainbow_six
   — every single one now interior to the grid, none at either edge.
   Removed `SPORT_K_OVERRIDE` entirely rather than reconcile it against the
   new auto-selection; there's no principled reason to keep a P&L-justified
   hand-pick once the selection method it was overriding is fixed.

2. Confidence threshold: **initially made the same change** (min Brier) for
   consistency, then caught it empirically breaking before deploying —
   printed the full `threshold_scores` table for all 5 titles and Brier
   improves *almost monotonically* toward the single most restrictive
   threshold every time (e.g. LOL: brier 0.220 at threshold 0.0 down to
   0.134 at threshold 0.30, the top of the widened grid), because
   restricting to an ever-smaller, ever-more-confident subset mechanically
   improves calibration score with no interior stopping point except
   running out of data (rainbow_six's top threshold had only 34
   observations left). `units_at_minus_110`, by contrast, has a genuine
   volume-vs-quality tradeoff baked in and empirically produces a real
   interior optimum (~0.03-0.05) for every title once the grid was widened
   enough to see it. Reverted threshold selection back to
   `units_at_minus_110` and documented in-code why the two hyperparameters
   need *different* selection criteria despite looking like the same kind
   of "pick the best row from a table" problem — this is the actual
   half-right insight the prior "v4: was min Brier" comment was gesturing
   at without ever articulating why K and threshold aren't interchangeable.

Bumped `ESPORTS_MODEL_LINEAGE` to `v5` (genuine methodology change, not a
data refresh) and re-ran `validate-esports --write-artifacts` for real,
which also regenerated `outputs/latest/esports-baseline-validation.json` --
necessary because `dashboard_server.py`'s `production_evidence()` compares
each configured artifact's hash against that snapshot file, and forgetting
to regenerate it after writing new artifacts made
`test_current_configured_production_artifacts_all_valid` fail with
`model_definition_and_backfill_valid: False` for all 4 titles (caught by
running the full suite before considering this done, not by inspection).

Real locked-test numbers for the new artifacts (out-of-sample, never seen
during K/threshold/Platt fitting): lol 70.1% (1899 selected calls), cs2
65.6% (6595), dota2 68.2% (1822), valorant 63.2% (2453), rainbow_six 66.9%
(511) -- all notably more honest and less inflated than the transient
"all thresholds want 0.15-0.30" numbers seen mid-investigation, which were
an artifact of the (correctly rejected) Brier-based threshold selection
cherry-picking tiny high-confidence subsets.

Updated `config/model.yaml`'s LOL/CS2/DOTA2/VALORANT/RAINBOW_SIX blocks to
point at the new `-v5.json` artifacts and to each artifact's own
newly-validated `research_confidence_gate` (DOTA2 and VALORANT dropped from
0.05 to 0.03 -- the re-run search found the real interior optimum lower for
both once the P&L-proxy K stopped distorting the underlying ratings the
threshold search operates on).

Archived all 288 esports Research/Gated-Research rows still logged under
`-v4` (220 settled via `archive_settled_rows`, 62 open via
`remove_open_rows`) to
`data/archive/2026-07-31-retired-esports-v4-models/`, mirroring the MLB
v5/v6 -> v7 archival pattern exactly (a genuine, discrete model-version
retirement, not the continuous daily recalibration this project's esports
titles otherwise do -- the same distinction that made "leave esports alone"
correct for the *earlier* gate-tightening archival earlier this session).
One wrinkle worth recording: the archival script's own printed running
tally undercounted LOL's gated-research row count by 6 relative to the
pre-archival total; re-checked the live ledger file directly afterward (0
rows remaining, not 6) and cross-referenced the audit log, which showed
those 6 were independently cleared by the system's own automatic
"re-forecast replacement for 2026-07-31" mechanism around the same
wall-clock moment (ordinary daily-pipeline activity, unrelated to and not
conflicting with this archival) -- ground-truth-verified before writing the
final MANIFEST.json rather than trusting the script's own print statements.

**Separately, a real cross-cutting maintenance-risk fix**: four
independently-written from-scratch Elo implementations existed across the
project (`features/elo_ratings.py`'s `EloBook` for MLB/NBA/WNBA/NFL/soccer,
`esports.py`'s `NeutralElo`, `international_baseball.py`'s `HomeElo`, and
inline formulas in `models/tennis.py`) -- flagged by the full-project logic
review as "not a bug today, but a future fix to one won't propagate to the
others." Compared all four line-by-line: each has legitimately different,
sport-specific update mechanics (margin-of-victory scaling in three
different shapes, offseason regression vs. per-team decay, recency/
tournament-tier weighting for esports only, tie-handling for KBO/NPB) that
would be reckless to force into one shared update rule without a much
larger, higher-risk rewrite. But all four independently re-derived the
exact same core Elo logistic formula (`1/(1+10^(-diff/400))`) -- the one
place a sign error would be most catastrophic and hardest to notice, since
it would silently reverse every single prediction for that sport. Extracted
that one formula into a shared `expected_win_probability(rating_a,
rating_b, advantage=0.0)` in `features/elo_ratings.py` and pointed all four
call sites at it, leaving every implementation's own update/decay/margin
logic untouched. New `tests/test_elo_ratings.py` (6 tests: coinflip
symmetry, monotonicity, home-advantage direction, the textbook ~10:1 odds
at a 400-point gap, and an explicit regression check against the exact
pre-consolidation formula). Full suite (513 tests, +6 net from this and the
esports rebuild) confirmed byte-identical prediction behavior before and
after -- no existing test's expected numbers changed, which is exactly what
a pure refactor with zero behavior change should produce.

Verification: 513 tests passed, ruff 118 findings (same baseline, nothing
new), `verify-chain` 0 breaks, a live `daily` run completed with zero
failures across every sport, and directly re-queried the live ledgers
afterward to confirm 0 `-v4` esports rows remain anywhere and only `-v5`
rows are being logged going forward.

## 2026-07-31 (later still) — every logged pick gets real units and P&L, dashboard parity fixes, and a real MLB spread/total wrong-side selection bug

Four related fixes from one long stretch of the same session, driven by the
operator directly reading dashboard screenshots and asking pointed
questions rather than by a proactive audit -- worth recording that the
review-by-suspicious-user-questions method caught a real, live-money-
relevant bug (the last item below) that none of this session's several
prior code-only audits had surfaced.

### "Every pick should have units and pnl, hard code this"

Root cause: `eligibility.py`'s `_research()` (used for every NO_CALL reason
except team-banned: stale data, model-state-not-yet-promoted, missing
provenance) and `evaluate_gated_research_eligibility`'s
`_downgrade_research_call` (used when a row clears the base eligibility
checks but fails Gated Research's own edge/confidence bar) both hard-zeroed
`units` rather than using the same `edge_scaled_units` every real CALL
already uses. This meant two structurally different things were both
displaying as "no size, no P&L" in the dashboard: (a) genuinely-untrusted
rows (banned team -- correctly still zero, kept as the one exception) and
(b) perfectly good model opinions that just hadn't cleared a *curation* bar
(Gated Research) or a *promotion* bar (still `status: research`, e.g. every
single Tennis and Soccer row, since neither has been promoted past
research yet) -- there was no reason those should read as "the model had
no opinion" when it very much did.

Fixed both functions to compute a real `edge_scaled_units` size in every
case except `NO_CALL_TEAM_BANNED` (a banned team is never sized regardless
of model opinion -- matches `ledger.py`'s pre-existing
`call_type="no_call"` special case for that one reason). Extended
`ledger.py`'s pre-existing `recompute_research_sizing` (previously a narrow
allow-list of 3 "sizable" reasons) to size every reason except team-banned,
matching the code fix, and used it to backfill 235 already-settled
historical zero-unit rows across Research/Gated Research so old rows show
real sizes too, not just new ones going forward.

**Found the fix was incomplete on first pass**: reviewing the *Flat*
ledger specifically (not just Research/Gated Research) turned up 20 more
zero-unit rows -- MLB Measured Edge margin/totals rows logged via the
*legacy* `forward.py`/`_forecast_mlb_totals_flat` path, which also routes
through `eligibility.py`'s shared `_research()` and therefore had the exact
same latent zero-unit rows, just in a ledger file this pass hadn't
specifically re-checked yet. Ran `recompute_research_sizing` against
`data/flat_picks.xlsx` and `data/picks.xlsx` directly (not just the
per-sport Research/Gated-Research files) to close the gap -- a reminder
that "every ledger" literally means all four files, not just the two that
happen to be split per-sport.

### Dashboard sort order and KPI parity

Two related display bugs, both from directly reading pasted screenshots:

1. All four ledger tables (Ledger/Main, Flat, Flat Research, Gated
   Research) defaulted to sorting `event_start_utc` **ascending** (oldest
   game first) -- the opposite of what anyone actually wants when opening
   a live trading dashboard. Flipped the Main table's default `sortAsc`
   flag and the other three tables' hardcoded comparators to descending
   (`dashboard.html`'s `renderPicks`/`renderFlatTable`/
   `renderResearchTable`/`renderGatedResearchTable`).

2. Research and Gated Research tabs had no KPI summary card (games/win
   rate/P&L/ROI) at all -- Main and Flat both had one, Research/Gated
   Research never got the equivalent block added when those tabs were
   built. Added `#researchKpis`/`#gatedResearchKpis` divs and the matching
   computation, mirroring Flat's existing pattern exactly (computed from
   the currently-filtered row set, not the unfiltered ledger total, so
   "games" in the KPI card always matches what the visible table actually
   shows).

Also added a plain-English explainer to spread picks (operator: "if its
team +1.5, the dashboard should say no on team winning by 2") and then
reframed it to prediction-market YES/NO phrasing per a follow-up
clarification -- a spread pick now reads e.g. "YES: Los Angeles Dodgers
-1.5 (needs to win by 2+)" instead of a bare "Dodgers -1.5", since
`selection` always literally represents the side being bought (i.e. "YES"
on that specific named outcome), which is the correct, unambiguous framing
for a project built entirely on Polymarket-style binary contracts.

### The real bug: MLB spread/total still selected sides by expected value, not by the model's favorite

Investigating "shouldn't 7/30 have 30 picks (10 ML + 10 spread + 10
total)?" led to "I'm only getting 27 MLB for 7/30 in flat" led to checking
*why* 3 rows were missing from the dashboard's default view -- and all 3
had `model_probability < 0.5` for their own selected side, which is
mathematically impossible under correct probability-argmax selection (the
side that wasn't picked would necessarily have had the higher probability).

Root cause: `forward.py`'s `_paired_event_candidates` (feeds MLB spread/
total into Flat via the Measured Edge margin/totals models) still selected
`max(sides, key=lambda side: probability[side] * decimal_odds[side] - 1)`
-- expected value, not probability -- the exact same anti-pattern the
operator corrected system-wide on 2026-07-30 for MLB moneyline, esports,
and soccer (see that section below). This one file was missed during that
original revert because attention that day was on `learned_forward.py` and
the esports/soccer call sites, not this older, `forecast --model
legacy-measured-edge` code path that still feeds live Flat spread/total
picks today. Fixed to plain `max(sides, key=lambda side: probability[side])`,
matching every other sport. Added a regression test that specifically
constructs a probability-vs-price divergence (a 30% favorite at plus-money
odds vs. a 70% favorite at minus-money odds) so this exact class of
regression can't silently reappear a third time.

**Real, quantified impact on already-logged data**: exactly 7 of 44
Measured-Edge Flat rows (16%) were logged on the wrong side under the old
formula -- an exact test, not a sample, because "selected side's own
probability < 0.5" is only possible under the buggy EV-based selection
(spread/total probabilities for the two sides of a market are
complementary by construction, so the higher-probability side is
mathematically always the one with probability >= 0.5). Verified each of
the 7 against its own `rationale` text (which records both raw and
calibrated probability) before touching anything -- all 7 had raw
selected-side probability below 0.5 too (e.g. 0.3488, 0.3774), confirming
none were false positives from calibration shrinkage alone. 3 were settled
(archived via `archive_settled_rows`), 4 were still open (removed via
`remove_open_rows` and relogged correctly via a fresh `flat-forecast` run
once the code fix landed) -- to
`data/archive/2026-07-31-measured-edge-wrong-side-selection-bug/`.

Retroactive effect on the (already tiny, n<10 per market) settled-row
counts: totals improved 30.0% -> 37.5% (both removed rows were losses);
margin/spread ticked down 80.0% -> 77.8% (the one removed row happened to
have won by luck despite being the wrong side). Neither move reflects the
model getting better or worse -- the underlying math didn't change, only
the bookkeeping got corrected for picks that were never actually the
model's real opinion to begin with.

## 2026-07-31 (still later) — CLV wired for esports, tennis, and soccer moneyline

Operator directive: "implement clv for all ledgers and all picks." Turned
out the "settlement already computes CLV generically, just needs a closing
quote passed in" assumption undersold the real gap -- traced the actual
wiring sport by sport rather than assuming it worked everywhere `settle()`
technically accepts closing-price arguments.

**What was actually true beforehand**: `PickLedger.settle()`'s
`probability_clv` computation is genuinely generic (works for any ledger,
any sport) -- the gap was entirely on the *caller* side. MLB's generic
ESPN-based settlement path (`_settle_all_unsettled`) fetches a real closing
quote via `MarketOddsSnapshotStore.closing_quote()`, which is populated by
MLB's own Measured Edge forecast pass writing a combined moneyline+spread+
total snapshot per event under `data/market_odds_snapshots.jsonl` --
verified this file empirically contains 709 real snapshots, 100% MLB team
names, confirming it has never been written to by any other sport despite
`_settle_all_unsettled` nominally being the shared settlement path for
MLB/NBA/WNBA/NFL/SOCCER alike. Esports and tennis had dedicated settlement
functions that explicitly passed `None, None` for closing price -- no
attempt at all, not just an empty lookup.

The fix didn't require building new snapshot-capture infrastructure:
`data/odds/{sport}/{date}/polymarket_snapshots.jsonl` already exists and is
already populated daily for esports (22,596 real lines), soccer (6,004),
tennis (11,296), and WNBA (9,190) via `capture_slate_snapshots` (part of
every `daily` run, for every `POLYMARKET_SPORT_LEAGUES` entry including
NBA/NFL -- those two simply have zero real lines right now because July is
their offseason and there are no real markets to capture, not a wiring
gap). `PolymarketSnapshotStore.closing_snapshot(slug, event_start_utc)` --
the exact "last snapshot at or before game start" lookup CLV needs -- also
already existed, used only by a manual MLB CLV-backfill CLI command,
never wired into automatic settlement for anything.

Added a shared `_closing_probability_for_moneyline_pick` helper in
`cli.py` (team/player-name matching via `learned_forward.py`'s existing
`_team_matches` -- reused rather than reimplemented, to avoid a second,
possibly-divergent copy of that matching logic) and wired it into
`_settle_esports_pick`, `_settle_tennis_pick`, and (as a fallback when
MLB's own snapshot store comes up empty, which it always does for soccer)
the generic ESPN settlement path's soccer branch. Explicitly scoped
**out**: soccer totals/BTTS (different side-matching shape -- over/under,
yes/no -- than the team-matching helper built here), soccer draw
selections (the home/away helper has no "neither side" case), and KBO/NPB
(literally zero Polymarket markets ever observed for these leagues, so
there is no closing price to ever capture, confirmed, not merely unwired).

4 new tests (esports CLV populated from a captured snapshot, esports CLV
correctly absent when no `data_root` is passed -- proving existing callers
are unaffected, tennis CLV populated by player name across a snapshot
captured under `data/odds/tennis/`). Live-verified via a real
`settle --all-unsettled` run against production data: zero failures across
every sport/step.

## 2026-07-31 (even later) — full-project logic review: 3 more real bugs found and fixed

Operator asked for "logic problems... indepth review of all models" across
the whole project, not just MLB. Split the work across three parallel
read-only review passes (MLB models, soccer/tennis models, esports/KBO-NPB
models) and personally re-derived/verified every finding directly against
the code before acting on any of it -- two flagged issues turned out to be
intentional-but-underdocumented (see below), three were real and got fixed
with regression tests, and several more were investigated and found to
already be correct.

### Fixed

**1. Soccer: a same-city/shared-mascot derby collision could silently
price the wrong team's contract** (`soccer_forward.py`). `_team_matches_title`'s
fuzzy word-matching treated "city" and "united" as generic, strippable
words (grouped with actual corporate suffixes like "FC"/"SC"/"CF"/"Club"),
so both "Manchester United" and "Manchester City" reduced to the identical
distinctive-word set `{"manchester"}` -- a false match. Verified directly:
`_team_matches_title("Manchester United", "Manchester City")` returned
`True` before the fix. The event-level match (which requires both team
names present in one combined title) mostly protects against this, but the
*single-snapshot-present* case -- one side's own team_win market missing a
fresh executable ask, a normal daily occurrence -- has no such protection,
since there's nothing to collide against.

Fixed two ways: (a) tightened `_GENERIC_TEAM_WORDS` to true corporate
suffixes only (removed "city"/"united"); (b) since short prefixes like
"AC" (as in "AC Milan" vs. "Inter Milan") still can't survive the
word-length filter no matter what's in the generic-word set, added a
cross-check at the actual matching call site: a snapshot is only accepted
for the model-favored team if the *opponent's* name does NOT also match
that same snapshot -- refuse rather than guess whenever it's ambiguous.
Proved the fix mattered the hard way: reverted it locally, watched the new
regression test fail with exactly the predicted wrong-team price, then
restored the fix and re-confirmed the test passes.

**2. KBO/NPB: home/away team labels resolved by raw array position instead
of Polymarket's own side tag** (`international_baseball.py` /
`cli.py`). `forecast_international_baseball_slate` already resolves
`home_id`/`away_id` correctly (tag-safe, via each side's own `"selection"`
field) for the actual win-probability math -- but that safe resolution was
then discarded, and the downstream ledger-logging code in `cli.py`
independently guessed `home_team = teams[1]`, `away_team = teams[0]` from
raw side order, which `market["sides"]` has no ordering guarantee for. A
real (if not yet observed) risk: if the gateway ever lists a KBO/NPB
event's sides home-first instead of away-first, the ledger row's home/away
*labels* silently swap -- with no effect on which team gets picked (that
math was always tag-safe) but a real effect on settlement, which matches
`row["home_team"]`/`row["away_team"]` against the official schedule.
Fixed by propagating the already-correct `home_id`/`away_id` resolution
through to explicit `home_team`/`away_team` name fields on the contract
dict, and having `cli.py` use those directly instead of re-guessing from
position (old positional logic kept only as a fallback for pre-existing
contract shapes). New test constructs the same event with sides in both
orders and asserts identical resolved home/away names either way.

**3. MLB: calibration governance only validated the coinflip point, not
the actual output range** (`models/mlb.py`). `MeasuredEdgeMarginModel`'s
`__init__` validates `scale`/`offset` only at `raw_probability=0.5`; its
`calibrate_selected_side` is then called on the *selected* side's
probability, which for a confident pick can be far from 0.5 -- nothing
stopped a scale/offset pair that passes the midpoint check from producing
an out-of-[0,1] "probability" at the extremes (verified algebraically:
scale=1.5/offset=-0.1 passes the midpoint check exactly at its boundary
but yields 1.25 at raw_probability=0.9). Not firing today -- today's real
artifacts stay safely in range across the practical domain -- but a future
recalibration could trip it silently. Worse, `MeasuredEdgeTotalsModel` had
*no* governance check at all, not even the midpoint one; the margin
model's safety net simply didn't exist for its sibling. Added the missing
totals governance check (mirroring margin's) and an explicit output-bounds
guard in both models' `calibrate_selected_side` methods. Verified both
real production artifacts still load and calibrate correctly with the new
checks in place before considering this done.

### Investigated and found to be intentional, not bugs

- `features/trends.py`'s `defensive_momentum` computes `defense[long] -
  defense[short]` -- reversed operand order versus `offensive_momentum`
  and versus the class's own blanket docstring ("Momentum = short minus
  long"). Traced through: `defense` values are runs *allowed* (a "lower is
  better" quantity), so the reversed order is exactly what's needed to
  keep "positive momentum = team improving" true for both fields
  consistently. The code is right; only the shared docstring is imprecise
  about it. Left alone -- a comment-only fix here risked introducing
  confusion of its own without a clear win, and nothing downstream reads
  the docstring instead of the value.
- MLB Dixon-Coles-adjacent checks (Measured Edge simulation, moneyline/
  spread selection, `learned_forward.py`'s feature computation), soccer's
  actual Dixon-Coles matrix (home/away orientation, the rho low-score
  adjustment, BTTS Platt calibration bounds), tennis's surface-blend
  weighting, and the shared/esports/KBO-NPB Elo update formulas (K-factor
  application, expected-score sign, tie handling) were all traced through
  directly and found correct.

### Flagged but deliberately not silently changed at the time (fixed in the very next session-work -- see the newer section above this one)

- Esports K-factor/confidence-threshold selection tuned by a raw P&L proxy
  with K pinned to a grid-edge value for 4 of 5 titles, and three
  duplicate from-scratch Elo implementations plus an unused shared one --
  both called out explicitly as "a legitimate concern, not something to
  silently change" at review time, then fixed for real once the operator
  confirmed. See "esports v4 -> v5 rebuild" above for the actual fix.

## 2026-07-31 (later) — archived retired-model settled picks; new ledger capability

## 2026-07-31 (later) — archived retired-model settled picks; new ledger capability

Operator directive: archive picks logged under fully-retired model versions
so the live ledgers reflect only current models. `remove_open_rows`
deliberately refuses settled rows (permanent record safeguard), so this
required a genuinely new capability, not a workaround: `ledger.py`'s
`archive_settled_rows(pick_ids, reason, archive_reference)` -- same audited
rigor as `remove_open_rows` (lock-held, audit-first, required non-empty
reason), but the opposite filter (settled only), and the audit event
records the row's COMPLETE content so nothing is actually lost even after
live removal. Added tests (mirrors `remove_open_rows`'s own test, plus
idempotency and required-fields checks).

Applied for real: 29 rows (Main, `mlb-elo-trend-lr-v5`/`v6`) and 89 rows
(Flat, same versions) archived to
`data/archive/2026-07-31-retired-mlb-model-picks/{main,flat}_retired_mlb_picks_archive.xlsx`
and removed from the live ledgers. Verified: 118 real `settled_pick_archived`
audit events written, `verify-chain` still clean (0 breaks), full test suite
green. Research/Gated Research had nothing to archive -- checked first:
every esports/soccer/tennis title has only ever used one `model_version`
string, even though calibration was refreshed internally without a version
bump, so there was no "old version" to separate out.

## 2026-07-31 — soccer/esports validation, MLB gate removal, Gated Research tightened

Soccer's Poisson-DC model (`soccer-poisson-dc-v1`) genuinely qualifies against
this project's own bar: 62.5% real locked-holdout hit rate, +90.4u, every
month positive (`validation.py`'s `qualify_soccer_poisson_model`, previously
never run against current data). Real settled Research picks confirm it:
61.5% win rate (8-5, n=13) on real settled games, closely matching the
backtest. No code changes needed -- it was already good, just never checked.

Esports (5 titles) are also individually strong (65-71% locked-test accuracy,
low calibration error) -- but real settled picks were running far below that
(33-55%), and Gated Research was performing *worse* than unfiltered Research
in every single title (e.g. LOL: 46.4% gated vs 54.2% research). Root cause,
confirmed via `confidence_selection_on_validation`: accuracy climbs steadily
with the model's own confidence (LOL 63.9% at zero threshold -> 74.9% at
0.15), and Gated's `research_confidence_gate` was `0.0` for every title --
barely filtering anything, so it wasn't curating for the better-calibrated
range at all. Fixed: raised `research_confidence_gate` per title to match
each artifact's own already-validated `confidence_threshold` (LOL/DOTA2/
VALORANT 0.05, CS2/RAINBOW_SIX 0.03) -- not arbitrary numbers, the same
values each artifact's own grid search already chose. Research (unfiltered)
deliberately untouched.

Operator directive, applied to MLB's learned moneyline path
(`learned_forward.py`, shared by MLB/NBA/WNBA/NFL): the hard confidence
threshold and the min-edge-vs-market gate in `cli.py` both used to silently
skip a candidate from ever reaching the ledger. Both removed -- every
candidate is now a real, sized call (sizing was already driven by the
model's own confidence distance from 50/50, not by either gate, so removing
them doesn't risk over-sizing a bad trade, only stops hiding the row). Both
numbers are still recorded (`reason` field, rationale text) for a human to
review before deciding whether to act. This is a deliberately different
choice from esports' Gated tightening above -- explicit operator direction
was "MLB: show me everything, I decide" vs. "esports: Research shows
everything, Gated should mean something."

**Project**: `/Users/vincentc9002/model prediction`

**Checkout audited**: single `main` branch (git history was consolidated this
session — the old `deepseek-phase5` branch referenced throughout this file's
older sections no longer exists, locally or on `origin`)

## Safety boundary

This file records diagnoses and read-only reproduction commands. It does not
authorize code fixes, artifact regeneration, ledger mutation, order placement,
or real-money execution. Do not use `--log`, `--write-artifacts`, `daily`,
settlement, dashboard POST routes, or execution commands during an audit unless
the operator separately authorizes that state change.

The source tree was changing during this audit. Re-run the checks before acting
on any line number or count.

## 2026-07-30 — MLB moneyline v7: retired v6's contaminated experiment, plus a real live-data bug found along the way

Rebuilt MLB moneyline (`mlb-elo-trend-lr-v7`) via the project's own standard
walk-forward pipeline (`validation.py`), replacing v6 (self-documented
contaminated `probable_starter_era_gap`, 90-day/242-call ad-hoc fit, never
cleared this project's own bar). v7 uses only point-in-time-safe features
(elo, trend, park, weather, real rolling pitcher-runs-allowed, real
credibility-shrunk bullpen weakness) fit on the full real history (3814
train / 1082 validation / 1391 locked-holdout games). Honest result: 58.5%
locked-holdout hit rate, +13.7u — real edge, still below the 60% bar, no
longer resting on a known-broken coefficient. Config's `qualification_override`
updated accordingly; `probable_starter_era_gap` dropped entirely (its honest
replacement, `point_in_time_pitcher_era_gap`, exists in code but its real
archive is only 6 days deep so far).

Two real bugs found and fixed while building this:
- `bullpen_weakness_gap` had no live feature provider at all in
  `learned_forward.py` — every real game today failed to forecast with a
  bullpen-including variant. Added one, reusing the same real
  `team_recent_relief_lines`/`bullpen_profile` functions Measured Edge
  already serves live with.
- `ingest.py`'s raw score cache is documented "immutable," but a cache
  captured mid-day (games still `STATUS_SCHEDULED`) for a date that has
  since passed was being trusted forever — confirmed live: 2026-07-26's
  cache was written at 14:29, before that evening's games, and every ingest
  since silently skipped re-fetching it. `data/processed/mlb/games.jsonl`
  (which every live Elo/trend feature reads) was missing 57 real completed
  games for 4 days. Fixed: a past-dated cache with zero completed events is
  now treated as stale and re-fetched; a genuinely final payload is still
  never touched. Same audit found 12 more stale WNBA dates and 44 stale
  soccer games — recovered.

  Tennis initially showed ~1748 flagged dates using this same check, but
  that turned out to be a false positive in the fix itself, not a real gap:
  tennis events nest matches under `groupings` (one per draw), a completely
  different shape from the flat `competitions` list `ESPNClient.completed_games`
  assumes, so that parser always sees zero completed matches for tennis
  regardless of true state -- confirmed live (statuses were real
  `STATUS_FINAL`/`STATUS_WALKOVER`, `completed_games` still returned 0).
  Fixed by using the same sport-aware parser real ingest already branches on
  (`completed_tennis_singles_matches` for tennis). Re-checked against all
  1878 real cached tennis files with the corrected parser: 0 genuinely
  stale. Added a regression test pinning that a final tennis cache is never
  refetched.

Retrospective check against real, already-settled picks: on the 20 main-ledger
and 72 flat-ledger v6/v5 games that could be matched to real history, v7 would
have selected the winning side 80% and 54.2% of the time respectively, vs.
v6/v5's actual 55% and 47.2% -- small samples, but a consistent improvement in
both. Full test suite: **484 passed** (7 new). Ruff: **117 findings**, same
baseline.

## 2026-07-30 — MLB Measured Edge rebuild: real elasticities replace the assumed-1.0 multiplicative formula

The 2026-07-29 investigation fixed four real data bugs (weather, starter ERA
shrinkage, bullpen shrinkage, park factors) but the resulting model still
showed weak-to-nonexistent real correlation on a 162-game/12-day backtest
(margin 0.045-0.12, totals ~0 or negative) -- concluded at the time as a
likely structural limit of the formula itself: `estimate_runs()` combines
offense/starter-weakness/bullpen/park/weather multiplicatively with an
*implicit, never-fit* exponent of 1.0 on every one of them (`league_avg *
factor1 * factor2 * ...`), assuming each factor should move the run
estimate exactly proportionally. Operator asked to rebuild rather than
accept that ceiling.

**Real refit**: collected point-in-time features (`reconstructed_features`)
and real final scores for 629 real games spanning 2024-02 to 2026-07 (vs.
the previous 162-game/12-day window -- far too narrow to safely fit five
new parameters), sampling every 8th real game-date for season/park/weather
coverage. Fit a Poisson GLM (log link) on `log(runs) ~ elasticity *
log(factor)` per factor, pooling both team-game sides of every game.
Validated via 4 chronological expanding-window folds before trusting any
single number (real risk here: point estimates are noisy at this sample
size and can flip sign fold-to-fold at these correlation magnitudes).

Real fitted elasticities (alpha=0.05 L2, chosen from the middle of the
tested range 0/0.02/0.05/0.1 since fold performance was similar across all
of them): `offense_elasticity=0.035`, `starter_weakness_elasticity=0.211`,
`park_elasticity=0.222`, `weather_elasticity=0.021`. Every one of these is
real but far below the previously-assumed 1.0 -- the formula had been
letting every factor swing the run estimate much harder than the real data
supports. `bullpen_elasticity` fit consistently **negative** across every
alpha and fold (worse opposing bullpen correlating with *fewer* runs
allowed) -- baseball-implausible given the feature's own definition, most
likely selection bias in which relief inning gets recorded (a leading
team's closer vs. a trailing team's mop-up arm), not real signal. Confirmed
dropping the feature entirely gives statistically indistinguishable holdout
performance from including it at its negative fit, so clipped to `0.0`
rather than trusted. Full reasoning lives in
`mlb-analyst-poisson-trend-v0.2.yaml`'s comments.

**Wired in**: `FormulaSpec` gained the five elasticity fields (required in
`load_formula_spec`, no silent defaulting); `estimate_runs()` now applies
`factor ** elasticity` instead of `factor` directly -- elasticity=1.0 is a
strict no-op vs. the pre-rebuild formula, so this is a generalization, not
a rewrite. New tests (`tests/test_mlb_elasticities.py`) verify the
exponentiation mechanism itself (zero elasticity nullifies a factor,
elasticity=1.0 reproduces exact pre-rebuild multiplication, fractional
elasticity dampens the swing) independent of which real values were fit.

**Recalibrated against the same real 162-game/2026-07-01..07-12 backtest**
used on 2026-07-29 (OLS of raw simulated probability vs. real outcome, same
methodology, both margin and totals artifacts rewritten with real new
hashes):

| Market | 2026-07-29 correlation | 2026-07-30 rebuilt correlation | Flat -110 diagnostic |
|---|---|---|---|
| Margin (spread cover) | 0.045-0.12 | **0.062** (within the old range -- rebuild didn't move margin much) | 80 picks, 53.7% hit, +2.09u |
| Totals (over/under) | ~0 or negative | **+0.166** (real, meaningful improvement -- first time this project has seen positive real signal on MLB totals) | 91 picks, 56.0% hit, +6.36u |

Honest caveat, stated the same way as every prior number in this
investigation: still only 162 real games, one 12-day window. Totals'
improvement is the most encouraging result of this whole MLB investigation
so far, but one backtest window is promising, not proven -- `model_state`/
units stay at research/zero (`qualification.insufficient_real_signal` was
removed from the totals artifact since it's no longer accurate, but no
qualification/promotion claim was added either). Live-verified: `build_mlb_slate`
against the real 2026-07-30 slate produces 27 real priced candidates across
9 real games with no new errors.

Full test suite: **481 passed** (3 new). Ruff: **117 findings**, same
baseline. `verify-chain`: 0 breaks, `chain_intact: true`.

## 2026-07-30 — esports/soccer side-selection: investigated, "fixed," then reverted per operator design decision

Found that `_log_esports_forecast` (`cli.py`) and `build_soccer_total_slate`
(`soccer_forward.py`) both select which side of a two-sided contract to log
by **raw model probability** ("whichever side the model thinks is more
likely to win/happen"). Confirmed live (not hypothetical): 23 of 64 real
esports contracts today (36%) had the model's raw favorite priced at
negative edge while the other side was positive (e.g. LOL "Joblife" 56.5%
model prob but ask 0.64, edge -7.5pp, vs. "Skillcamp Esport" 43.5% prob but
ask 0.37, edge +6.5pp); soccer's live slate showed the same pattern
(Newell's Old Boys @ Independiente: model favors Independiente 45.6% to
Newell's 27.8% outright, but Independiente's own team_win market was
-13.4pp edge while Newell's was +11.8pp).

Initially "fixed" this to select by edge instead of raw probability
(`max(sides, key=model_probability - ask)`), reasoning the ledger exists to
find +EV mispricings. **Operator corrected this**: every sport's model is
supposed to call the winner and log that pick -- `learned_forward.py`'s MLB
moneyline already works this way (`selection = "home" if home_probability >=
0.5 else "away"`, confirmed by reading the code), unconditionally, with no
edge comparison between sides. Esports/soccer picking by edge instead was
inconsistent with that established, system-wide policy, not an independent
improvement. Reverted both call sites and their tests back to raw-probability
selection to match MLB's pattern; the min_edge/eligibility gate downstream
(already probability-direction-agnostic: `abs(prob - 0.5)` for confidence,
`prob - implied_probability(odds)` for edge) is what decides whether a
correctly-called winner is *also* good value enough to become a real pick
vs. a zero-unit research observation -- that part was never broken and did
not need to change. Net code diff after the revert: comments only,
documenting the now-confirmed system-wide policy explicitly at both call
sites so this doesn't get "fixed" the same way again.

Also investigated and consciously left alone: `esports.py`'s
`SPORT_K_OVERRIDE` hardcodes K=96 (the top of the grid) for lol/cs2/dota2/
valorant. Re-running the grid search on current (larger) data shows the true
validation-set optimum is now lower for three of those four (lol: 40, cs2:
32, dota2: 48) — but checking held-out locked-test performance with the
auto-selected K vs. the override K gave mixed results (override K=96 did
*better* on LOL's locked test despite scoring worse on validation). With only
one validation/test split this is genuinely ambiguous small-sample noise in
which K wins, not a clear-cut bug like the pricing-side issue above — did not
touch it. Also bumped `mlb-analyst-poisson-trend-v0.2.yaml`'s Monte Carlo
`simulations` from 10000 to 20000 (numpy-vectorized, negligible cost either
way; reduces sampling noise only, does not address the model's real
correlation ceiling documented in the 2026-07-29 section below).

Full test suite: **480 passed**. Ruff: **117 findings**, same as prior
baseline (no new findings). `verify-chain`: 0 breaks, `chain_intact: true`.

## 2026-07-29 — MLB backfill/settlement fixes, dashboard fixes, Measured Edge
## model investigation, baseline auto-refresh

All items below were operator-directed, verified with real data (real ESPN/
Polymarket/Open-Meteo/MLB Stats API calls, real historical games, real
backtests), and committed with tests passing. Full test suite: **478
passed**. `verify-chain`: 0 breaks, `chain_intact: true`, `reconciled: false`
(the reconciliation gap is expected/documented, grows monotonically — see
2026-07-26 section below, not new).

### Ledger/settlement fixes

- **`forecast --force` backfill bug (found and fixed)**: `--force` froze the
  point-in-time decision timestamp at literal midnight UTC of the target
  date, but real Polymarket quote captures for a date don't start until
  hours later (~04:02 UTC observed) — every captured quote was "in the
  future" relative to that frozen instant, so `request.validate()` correctly
  rejected all of them and a real backfill attempt silently returned
  `logged: 0` with zero duplicates flagged. Fixed: each candidate now gets
  its own effective decision time (its own game's start minus one second)
  instead of one shared midnight freeze — `cli.py::_forecast_learned_sport`,
  `effective_now`. Recovered 6 main-ledger + 11 flat-ledger real 7/27 MLB
  moneyline picks this way, using real archived point-in-time odds.
- **`daily`'s automatic settlement never auto-voided postponed/canceled
  games** (`void_postponed=False` hardcoded in the `daily` command handler)
  — a postponed game never becomes "completed" under its original event_id
  (ESPN issues a new event_id for any reschedule), so an affected pick sat
  `open` forever with no automatic resolution path; only a manually-run
  `settle --void-postponed` would clear it. Fixed: `daily`'s automatic
  settlement now passes `void_postponed=True`.
- **`PickLedger.settle()` gained a `correction_reason` parameter** to allow
  re-grading an already-settled/voided row when explicitly reasoned (audit
  event type `pick_resettled_corrected`, distinct from a first-time
  `pick_settled`). Real case: the 7/27 7:00 PM Reds/Guardians game was
  postponed and never played under its own event_id (correctly voided as a
  push), but was actually replayed same-day as game 2 of a doubleheader
  (real final Reds 2, Guardians 0) — re-graded all 4 affected rows (main
  moneyline, flat moneyline/spread/total) to `win` per explicit operator
  directive.

### Dashboard fixes (real bugs found from an actual screenshot, not
### hypothetical)

- **Today tab duplicated the entire Flat Ledger / Flat Research sections.**
  `renderToday()` calls `loadFlatToday()`/`loadResearchToday()` without
  awaiting them, and neither cleared its own previously-appended DOM before
  re-appending; an overlapping call (auto-refresh racing a slow fetch, or a
  second manual refresh) let a stale in-flight call's results land after a
  newer render had already rebuilt the page. Fixed with a render-token guard
  (`todayRenderToken`) — a stale async loader now bails out instead of
  touching the DOM.
- **A pick with no matched executable quote still showed a computed
  market/edge/+EV badge/suggested size** — all derived from the `-110`
  neutral placeholder `cli.py` uses when no real quote is found
  (`sportsbook: "model_opinion_no_executable_quote"`), making a
  model-opinion-only row look like a real actionable edge. The backend
  already blocked the Buy button correctly; only the display was wrong.
  Fixed: rows with that sportsbook value now show `market: no quote`,
  `edge: —`, and a "model opinion only" badge instead.

### MLB Measured Edge (spread/total) model investigation

Four independent, individually-confirmed real bugs in the Trend Engine
feature formula (`models/mlb.py`), found via a real 162-game walk-forward
backtest (real historical Polymarket-reconstructed spread/total lines from
`data/historical/mlb_market_lines_reconstructed.jsonl`, real final scores,
point-in-time-correct reconstructed features):

1. **Weather was silently always neutral.** ESPN's own weather fields
   (`competition.situation.weather`, summary `gameInfo.weather`) are
   empirically always empty for MLB, live or completed — confirmed by
   direct inspection across multiple real games, not just reading docs.
   Fixed: `features/weather.py::resolve_weather` (live Open-Meteo forecast
   for upcoming games, historical archive for backtest/past dates) wired
   into `ESPNMLBClient.reconstructed_features`.
2. **Starter ERA had zero shrinkage for small-innings samples.** A pitcher
   with e.g. 3.3 total innings this season (early callup, return from
   injury) could post a 21.6 ERA off one bad outing and have it trusted at
   full confidence — drove implausible pregame run-total projections
   (observed range 4.32–18.55 runs before the fix). Fixed: credibility-
   weighted shrinkage toward `league_starter_era` for both `season_era` and
   `recent_era` (mirrors the shrinkage `_offense_index` already applies to
   team offense), plus the same treatment for the K%/BB% "discipline"
   multiplier. New spec fields: `starter_season_prior_innings` (40 IP),
   `starter_recent_prior_innings` (20 IP), `starter_rate_prior_batters_faced`
   (60 BF).
3. **Bullpen strength was hardcoded neutral for literally every game** —
   `bullpen_profile(None)`, no data source wired at all. Fixed: built a real
   relief-appearance index from `mlb_statsapi.py`'s boxscore snapshots
   (`features/bullpen.py::team_recent_relief_lines` — last 10 completed
   games' relief lines per team, excluding that game's own starter), with
   the same credibility-weighted shrinkage applied in `bullpen_profile()`
   itself.
4. **Park factors were a static, undated table.** Recomputed empirically
   from this project's own 7,803+ real completed games — confirmed several
   parks were meaningfully stale, most strikingly the Athletics (0.98 static
   vs. 1.153 real, since the team relocated to a different park).

**Honest finding, not swept under the rug**: none of the four fixes
recovered meaningful real-world predictive signal. Margin correlation with
real outcomes stayed weakly positive across every formula revision tested
(0.045–0.12); totals stayed at or below zero throughout (-0.019 to -0.123).
That's a structural finding about the hand-tuned multiplicative-index
formula (every factor's own noise compounds rather than averaging out), not
something further feature-level tuning fixes. Flagged rather than chased
further after four independent rounds all showed the same pattern.

**Calibration refit from the real 162-game backtest**, replacing the prior
identical-for-both-markets `0.85`/`0.075` placeholder:
- `measured-edge-margin-v1.json`: `scale=0.1007`, `offset=0.4667` — real,
  honest, heavy shrinkage; margin showed a real if small positive signal.
- `measured-edge-totals-v1.json`: reuses margin's shrinkage as the most
  conservative available estimate (totals' own fit was noise-level/
  negative), with an explicit `qualification.insufficient_real_signal` flag
  and the full backtest evidence documented in the artifact itself.
- `MeasuredEdgeMarginModel`'s governance bounds check was rewritten: the old
  fixed `-0.25 <= offset <= 0.25` bound implicitly assumed scale stays close
  to 1 (mild shrinkage) and would have rejected this real, correctly-
  centered, heavy-shrinkage fit. Now checks the actual invariant that
  matters — `calibrated(0.5)` stays near 0.5 — regardless of how much
  shrinkage that requires.

Existing picks under the old calibration (52 rows, all already settled)
were left as real historical record rather than destroyed — distinguishable
from new picks by `model_artifact_hash` going forward. Still Flat-only, no
main-ledger promotion.

### MLB season-dependent baseline auto-refresh (new)

Park factors, league-average starter ERA/K%/BB%, and league relief ERA all
drift as a real season progresses (and can jump discontinuously — a team
relocating ballparks, like the Athletics did). These were one-time
snapshots computed manually during the investigation above; a real
regeneration workflow now exists so they don't go stale again silently.

- New module: `mlb_baseline_refresh.py` — `compute_park_factors` (from
  `data/historical/mlb_games_all.jsonl`), `compute_league_rates` (from
  `data/mlb_statsapi/game_snapshots.jsonl`), and writer functions that patch
  `features/park_factors.py` (full regeneration, it's a generated file now)
  and the four `league_*` fields in `mlb-analyst-poisson-trend-v0.2.yaml` /
  `LEAGUE_RELIEF_ERA` in `features/bullpen.py` (targeted regex substitution,
  preserving every hand-written comment in both files).
- Self-throttled via `data/mlb_baseline_refresh_state.json` — default
  minimum 7 days between refreshes (park factors and league rates are
  full-season aggregates; day-to-day noise dominates anything a faster
  cadence would catch, and there's no cost to checking more often since it
  no-ops when recent). `--force` bypasses the throttle.
- Wired into `daily` (`step0_mlb_baseline_refresh` in its output) so it runs
  automatically on the existing cron cadence without needing separate
  infrastructure, and exposed as its own CLI command:
  `model-prediction refresh-mlb-baselines [--force] [--min-days N]`.
- First real run (2026-07-29): 30 real park factors from 7,803 games
  (2024-02-22 to 2026-07-25); league rates from 6,349 real boxscore
  snapshots (2024-03-20 to 2026-09-22) — starter ERA 4.1958, strikeout rate
  0.2193, walk rate 0.0785, relief ERA 4.0593, runs/team/game 4.5493. All
  close to but distinct from the prior hand-typed reasoned defaults.

## 2026-07-27 (evening) — wiring session

Per operator directive, this session's scope was **wiring and features, not
validation**: is a model actually running in the live daily pipeline, and on
what data — not hit rates, Brier scores, or promotion-gate status. Two
background audit agents independently verified the results below against
current source; findings are their direct file:line citations, not
self-reported claims.

**Git**: `deepseek-phase5` and all other branches were merged and deleted; the
repo is now a single `main` branch, synced with `origin/main`.

**Newly wired this session** (previously either broken, unwired, or
nonexistent):
- MLB totals + spread now reach `data/flat_picks.xlsx` via
  `_forecast_mlb_totals_flat` (`cli.py`), pricing `MeasuredEdgeTotalsModel`/
  margin (`models/mlb.py`) against real Polymarket lines. The frozen
  `config/models/mlb-analyst-poisson-trend-v0.2.yaml` spec was missing/
  mismatched `factor_bounds`, `uncertainty`, and `simulation` keys entirely;
  all three blocks were rebuilt with documented reasoned-default values (not
  precision-fit) since the file could not otherwise load.
- Soccer moneyline now prices against Polymarket's real market shape — three
  separate per-team `team_win` Yes/No markets, not one combined moneyline
  market as first assumed. `MARKET_TYPES` in `data_sources/polymarket_us.py`
  didn't recognize `SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME` at all before this,
  so these markets were silently dropped.
- Esports ratings now auto-refresh before every forecast
  (`esports.py::refresh_recent_matches`, called by `cli.py::
  _refresh_esports_ratings` inside `daily`) instead of only updating via a
  full-file-overwrite manual backfill. Verified today (2026-07-27) that
  `manifest.json`'s `matches_sha256` for all four titles matches the actual
  current file hash — the incremental-merge path keeps the manifest/hash
  chain consistent, which a naive implementation would have broken.
- KBO/NPB: a silent-gap bug in `international_baseball.py` — a market with
  the wrong number of sides was silently `continue`d past with no recorded
  reason — now appends `{"reason": "NO_CALL_MARKET_SIDES_INVALID"}` before
  skipping (`international_baseball.py:763-770`, confirmed present by
  audit). Every other `continue` in that function was independently checked
  and does record a reason; one bare `continue` for non-moneyline market
  types is intentional, not a gap.
- Tennis is entirely new this session — see the dedicated section below.
- `League.WORLD_CUP` removed from `config/model.yaml` (`team_ban_list.teams`
  and `models:`) and from `POLYMARKET_SPORT_LEAGUES`/`_LEDGER_LEAGUE_TO_ESPN`
  (live trading and settlement no longer see it) — but see the gap noted
  below; the removal is incomplete elsewhere.
- Dashboard: research-mode CALL/NO_CALL pill removed in favor of plain
  win/loss/push/open across all tabs; "Research" tab renamed "Flat Research"
  (it was always the ungated tier; gated research is the filtered one, so the
  old name was ambiguous); `#researchTable`/`#gatedResearchTable`/
  `#flatTable`/`#btTable` wrapped in `overflow-x:auto` (previously missing,
  causing wide tables to push past the page edge).

### Tennis (new, 2026-07-27)

**Wired: yes**, independently re-verified by a background audit agent against
current source (not just self-reported):

- Invoked from `daily` (`cli.py:2728`, immediately after soccer's call) and
  from the `forecast`/`flat-forecast` sport-dispatch loop (`cli.py:2469`,
  `elif sport == "tennis":`).
- `RESEARCH_LEDGER_SPORTS` (`research_ledgers.py:7-16`) includes `"tennis"`,
  so today-clearing and dashboard discovery pick it up automatically.
- `BBO_CAPTURE_SPORTS` (`data_sources/polymarket_us.py:99`) includes
  `"tennis"`, so real odds get captured daily.
- Settlement: `_find_tennis_result`/`_settle_tennis_pick` (`cli.py:1993-2053`)
  branch on `league == "TENNIS"` inside `_settle_all_unsettled`
  (`cli.py:1768-1776`), matching completed WTA singles matches by player name
  since ESPN's tennis scoreboard shape (`groupings`/`athlete`) doesn't fit the
  generic team-based settlement path at all.
- Ingestion: `data_sources/espn.py::completed_tennis_singles_matches` parses
  ESPN's tennis scoreboard (matches nested under `groupings`, competitors
  carrying `athlete` for singles or `roster` for doubles) and filters to
  singles only via `competition.type.slug` containing `"singles"` — doubles
  are dropped entirely (matches the operator's confirmation that Polymarket
  US never lists doubles markets). Real historical bootstrap run 2026-07-27
  produced 25,787 singles matches (ATP 16,721 / WTA 9,066) into
  `data/processed/tennis/games.jsonl` and `data/historical/
  tennis_games_all.jsonl`, both previously empty despite 1,866 cached raw
  ESPN days sitting unused.
- Live-verified end to end with real data, no mocks, 2026-07-27: a real
  `polymarket-slate --sport tennis` capture stored 420 real BBOs; a real
  `forecast --sport tennis --log` run found 143 scheduled WTA matches, priced
  16 against real Polymarket asks by player name, and logged all 16 to the
  new `data/research/tennis.xlsx` (confirmed 16 rows via `PickLedger`).
- **Structural coverage limit, not a bug**: `POLYMARKET_SPORT_LEAGUES["tennis"]`
  (`polymarket_us.py:73`) is `("WTA", "ITF_MEN", "ITF_WOMEN")` — there is no
  ATP market on Polymarket US at all (comment at line 60 confirms this was
  independently verified live). `LEAGUE_PATHS`/`SPORT_LEAGUES`
  (`espn.py:57-58,92`) only expose `ATP`/`WTA` — no ITF scoreboard path
  exists. WTA is therefore the only tour where both a model prediction and a
  real executable price can ever coexist; `tennis_forward.py` deliberately
  only builds a WTA slate.
- **Gap found by audit, not yet fixed**: `tennis_forward.py`'s
  `_upcoming_singles_matches` (lines 48-78) never extracts the tournament
  name or calls `_infer_tennis_surface`, and `build_tennis_slate` constructs
  `UpcomingMatch(...)` without a `surface=` kwarg (lines 139-147) — live
  predictions therefore always fall back to the dataclass default
  `surface="Hard"` (`models/tennis.py:49`), even though the *historical*
  Elo-build path correctly infers surface per match
  (`espn.py:196,225`). Surface-blending is effectively inert at forecast
  time today. Tracked as an open follow-up.

### Newly found gaps (not yet fixed)

- **Soccer BTTS**: `models/soccer.py:119-121,165-173` already computes a full
  BTTS probability and emits a `GamePrediction(market_type="btts", ...)` —
  the gap is entirely on the classification side.
  `data_sources/polymarket_us.py`'s `MARKET_TYPES` (lines 99-115) has no BTTS
  entry, so no BTTS market is ever recognized even if one exists on the
  gateway. Confirmed still unbuilt, not broken.
- **Esports BBO-capture/forecast mismatch**: `POLYMARKET_SPORT_LEAGUES
  ["esports"]` (`polymarket_us.py:74-82`) lists 8 leagues (LOL, CS2, COD,
  VALORANT, DOTA2, ROCKET_LEAGUE, OVERWATCH, RAINBOW_SIX); because `"esports"`
  is one bucket in `BBO_CAPTURE_SPORTS`, real BBO gets captured for all 8
  whenever Polymarket lists them. Confirmed live: `data/odds/esports/
  2026-07-18/polymarket_snapshots.jsonl` (and 07-19, 07-22) contain real COD
  and RAINBOW_SIX snapshots with live asks/bids. `ESPORTS_TITLES`
  (`cli.py:114`) and `TITLE_SPECS` (`esports.py`) only ever price 4 of the 8
  — COD/ROCKET_LEAGUE/OVERWATCH/RAINBOW_SIX get real market data captured
  and stored with zero consuming model, silently, every day. Not dangerous
  (no money moves), but dead capture work with no test catching the
  mismatch.
- **World Cup removal is incomplete**: dropped from the live-trading league
  tuple (`polymarket_us.py`) and from `_LEDGER_LEAGUE_TO_ESPN`
  (`cli.py:130-141`) — confirmed zero WORLD_CUP rows in `data/picks.xlsx` (20
  rows) or `data/flat_picks.xlsx` (81 rows) as of 2026-07-27, so nothing is
  actively broken. But `League.WORLD_CUP` still exists as an enum member
  (`domain.py:25`), still has a `ModelSpec` (`models/registry.py:70-76`), and
  is still listed in `espn.py`'s `LEAGUE_PATHS`/`SPORT_LEAGUES`
  (lines 44,78) and in `the_odds_api.py`/`football_data.py` sport-key maps.
  If a WORLD_CUP ledger row were ever logged while still open, it would
  settle-stall silently forever (`_LEDGER_LEAGUE_TO_ESPN.get("WORLD_CUP", ())`
  returns nothing, no match, `pending` forever, no error). `tests/
  conftest.py:25` also carries a stale `"WORLD_CUP": []` ban-list fixture key
  that doesn't match the real `config/model.yaml` (which has no `WORLD_CUP`
  key at all). Decision needed: fully retire `League.WORLD_CUP`, or
  explicitly document it as "history/ingest-only, never a live league."
- **Legacy MLB margin model — confirmed intentional, not a gap**:
  `MeasuredEdgeMarginModel` (`models/mlb.py:406-447`) is only reachable via
  the explicit `--model legacy-measured-edge` flag on `forecast`/`log`
  (`cli.py:230,256,2414-2417`); `daily`'s argparse (`cli.py:273-280`) has no
  `--model` flag at all, so this path structurally cannot fire from `daily`.
  `_forecast_mlb`'s own docstring calls it "retained as an explicit
  rollback." Live MLB moneyline in `daily` genuinely goes through
  `learned_forward.py` via `_forecast_learned_sport` for `mlb` in
  `DAILY_LEARNED_SPORTS`. Not documented anywhere in `docs/`, but not
  confusing dead code either — just undocumented.
- **KBO/NPB observation, not confirmed as a bug**: `data/logs/
  daily_2026-07-27.log` shows both leagues returned `"events": 0` across all
  runs today, with no `data/odds/{kbo,npb}/2026-07-27/` capture directories
  (2026-07-26 had 1-5 events for both). Consistent with an off-day/All-Star
  break rather than a wiring bug, but not externally confirmed against either
  league's real schedule.

### Numbers re-verified 2026-07-27 (this session)

| Check | Result |
|---|---|
| Full test suite | **458 passed** |
| Ruff (`src/ tests/`) | **113 findings** |
| `verify-chain` | 0 breaks, 0 hash mismatches, `reconciled: false` (1,230 historical creation events without a matching removal event — grows over time by design, see note below) |

The P0/P1 findings and numbers further down this file (test/ruff/hash/
reconciliation counts under "Verified audit result — 2026-07-26") were **not**
re-run this session — they concern real-money execution safety and ledger/
audit transaction atomicity, which this session's scope did not touch. Treat
them as last-verified-2026-07-26, not current, until independently re-run.

## 2026-07-28 (early hours) — critical esports and tennis correctness fixes

Two bugs found and fixed this pass are more severe than anything in the prior
audit — both were live-verified against real external sources (bo3.gg's API,
the actual dashboard output), not just re-read from code.

### Esports: dota2 and valorant discipline IDs were swapped

Verified live against `GET https://api.bo3.gg/api/v1/disciplines`: the real
mapping is `{1: csgo, 2: valorant, 3: lol, 4: dota2, 5: deadlock, 6: games,
7: r6siege, 8: mlbb}`. `esports.py`'s `TITLE_SPECS` had `dota2:
discipline_id=2` and `valorant: discipline_id=4` — backwards. Confirmed by
cross-referencing real team IDs: the team "Wintermint", stored under the old
`data/esports/dota2/matches.jsonl`, resolves only under `discipline_id=2`
(real Valorant) via `GET /api/v1/teams?filter[teams.discipline_id][eq]=2`;
"Team Sexy", stored under the old `valorant` file, resolves only under
`discipline_id=4` (real Dota 2). The dota2 model had been trained on and
predicting from Valorant match history, and vice versa, for as long as this
project has run esports.

Fixed: swapped `TITLE_SPECS["dota2"]["discipline_id"]` to 4 and
`TITLE_SPECS["valorant"]["discipline_id"]` to 2. Both titles' `matches.jsonl`/
`teams.json`/`manifest.json` were deleted and rebuilt from scratch via
`esports-backfill` (the old data was for the wrong game entirely, not
correctable by a metadata fix) — dota2 now has 10,888 real Dota 2 matches,
valorant 14,575 real Valorant matches. Re-validated: `config/models/
dota2-tiered-elo-v4.json` and `valorant-tiered-elo-v4.json` regenerated.
Live-verified post-fix: `forecast --sport dota2` now resolves real Dota 2
team names ("Nemiga Gaming", "Team Bald", "Level UP") with real computed
probabilities; `forecast --sport valorant` resolves real VCT teams ("Cloud9
GC", "Shopify Rebellion Gold/Black").

### New: Rainbow Six Siege esports model

bo3.gg's `disciplines` endpoint confirmed real coverage for R6 Siege
(`discipline_id: 7, slug: r6siege`) with 2,969 real finished matches
available. Added `TITLE_SPECS["rainbow_six"]`, `League.RAINBOW_SIX`
(`domain.py`), a `ModelSpec` (`models/registry.py`), a `config/model.yaml`
entry (`status: research`, not `qualification_override`-promoted like the
other four — that promotion was a deliberate per-title operator review this
title hasn't had), and wired it into `ESPORTS_TITLES` (`cli.py`), which is
consumed generically by both the `daily` handler and the `forecast`/
`flat-forecast` dispatch loop (no other per-title code needed changing).
Settlement (`_settle_esports_pick`'s league check) and dashboard
(`SPORTS`/`RESEARCH_ONLY_LEAGUES`) updated. Backfilled and validated;
`config/models/rainbow_six-tiered-elo-v4.json` exists with 281 team ratings.

**Confirmed not buildable**: CoD, Rocket League, and Overwatch do **not**
exist as disciplines on bo3.gg at all (verified against the full 8-entry
disciplines list above) — this project has no other esports data source, so
these three cannot get a real model. Polymarket US does list all three as
esports leagues and real BBO gets captured for them daily
(`BBO_CAPTURE_SPORTS` includes the whole `"esports"` bucket) with nothing
ever consuming it — this is real, harmless (no money moves), but genuinely
dead capture work. No fix applied; flagging honestly rather than building a
model with fabricated data.

### Tennis: two additional bugs beyond the surface-inference fix above

Found live from the dashboard itself — every tennis pick showed exactly
50.0% model probability, including for well-known players (Madison Keys,
Sofia Kenin) who should have hundreds of real matches on file. Two distinct
bugs, both now fixed:

**Bug 1 — combined ATP+WTA tournaments mistagged by fetch order, not by
match.** ESPN's `tennis/atp` and `tennis/wta` site-API paths both return the
*entire combined event* (both Men's and Women's Singles groupings) for
shared tournaments — verified live: `scores_atp.json` for Brisbane
International contains a "Women's Singles" grouping, and `scores_wta.json`
for the same date contains its "Men's Singles" grouping too. `ingest.py`
previously tagged every match `league = <the endpoint that fetched it>`,
and since `SPORT_LEAGUES["tennis"] = ("ATP", "WTA")` fetches ATP first, every
WTA player's combined-tournament matches got claimed by the ATP fetch first
and the later WTA fetch's identical `event_id` was deduped away as already
seen. Fixed: `completed_tennis_singles_matches` (`data_sources/espn.py`) now
derives `league` per match from the competition's own `type.slug`
(`"womens-singles"` → WTA, `"mens-singles"` → ATP) instead of accepting a
caller-supplied tag; `ingest.py` no longer overwrites it for tennis.
`tennis_forward.py`'s live discovery was also filtering on generic
`"singles" in slug`, which would have accepted Men's Singles matches
returned redundantly by the WTA endpoint — narrowed to `"womens-singles"`
specifically. Rebuilt from already-cached raw files (no new network calls):
WTA match count went from 9,066 (undercounted) to 15,289; Madison Keys's 129
matches are now all correctly tagged WTA (previously split across both
leagues depending on fetch order).

**Bug 2 (the actual cause of the 50% symptom) — `tennis_forward.py` was
reading history through the wrong abstraction and always got zero rows.**
`build_tennis_slate` called `FeatureStore("data").games_before("tennis",
...)`, but `FeatureStore.load_games` builds a `GameRecord` per row via
direct dict subscript on `raw["away_team"]`, `raw["home_team"]`,
`raw["away_score"]`, `raw["home_score"]` — fields that exist for every
team-vs-team sport but do not exist on tennis's player-vs-player rows
(`winner`/`loser`/`surface`/`match_date`, no scores at all). Every tennis row
therefore raised `KeyError` and was silently caught by `load_games`'s broad
`except (KeyError, TypeError, ValueError): continue`
(`features/base.py`), so `games_before("tennis", ...)` had returned an empty
list for every single call since tennis was wired — completely independent
of bug 1 above, and the reason real players still showed exactly 0.5 even
after bug 1 was fixed. Fixed: added `tennis_forward.py::
_tennis_history_before`, which reads `data/processed/tennis/games.jsonl`
directly as raw dicts (the shape `TennisModel.build_elo` already expects)
and applies the same point-in-time cutoff `games_before` uses, bypassing
`FeatureStore`/`GameRecord` entirely rather than trying to force tennis into
an abstraction built for team sports.

Verified post-fix, directly against real data: Madison Keys's computed Elo is
now 1863 vs. a lower-ranked opponent's 1512, giving a real 84.6% win
probability — not 50%. `_tennis_history_before` returns 25,955 total rows
(15,282 WTA) where it previously returned 0.

**Note on pre-fix ledger rows**: `data/research/tennis.xlsx` contains 16 rows
logged 2026-07-27 before this fix, all computed with the broken
always-cold-start model (every `model_probability` in that batch is exactly
0.5). They were left in place (paper-only, no real exposure) rather than
deleted, but should not be used to judge tennis model performance — treat
anything logged before this fix's commit as void.

### Full-pipeline verification after all of the above

Ran the real `daily --date 2026-07-28` command end to end (not a dry run) to
confirm every sport discussed this session actually logs through the live
pipeline post-fix. All of MLB (main+flat), WNBA (main+flat), soccer, tennis,
KBO, NPB, LOL, CS2, DOTA2, VALORANT, and RAINBOW_SIX appeared in
`step2_3_forecast_and_log`'s output with no exceptions and exit code 0. Real
unit sizing confirmed working end to end by inspecting the actual gated
ledgers afterward: `data/gated_research/cs2.xlsx` (17 rows, 0.75U–2.00U),
`dota2.xlsx` (5 rows, 0.75U–2.00U), `lol.xlsx` (16 rows, 1.00U–2.00U) all show
real, differentiated unit sizes tied to real edge. KBO/NPB/rainbow_six/
soccer/tennis/valorant gated ledgers were empty at this particular run — that
reflects zero candidates clearing that sport's edge/confidence floor *today*,
not a sizing bug (soccer logged 6 candidates to Flat Research the same run,
none gated; KBO logged 5, none gated — both expected, low-edge days).

One transient, non-reproducible discrepancy: a `forecast --sport kbo --log`
run showed `priced_count: 5, logged: 0` at one point. Re-running the
identical logic standalone immediately after succeeded and logged all 5 rows
correctly (now confirmed present in `data/research/kbo.xlsx` with real,
differentiated probabilities). Most likely explanation is lock contention
with the recurring `com.modelprediction.daily.plist` background cron (every
3 hours) rather than a code defect — the exact code path was re-verified
working when run in isolation moments later with no changes. Flagged here in
case it recurs; not treated as a confirmed bug.

## 2026-07-27 remediation note

The historical findings below remain useful audit context, but the approved
forecast scope is now repaired in this working tree:

- MLB historical validation uses only pregame-observed starter archive rows;
  the first real prospective rows were captured 2026-07-26. MLB v6 remains
  unqualified.
- Missing/invalid MLB or WNBA executable quotes retain a zero-unit Today model
  opinion with `NO_CALL_MARKET_UNAVAILABLE`; the 5% valid-quote edge gate is
  unchanged.
- WNBA availability conflicts/errors default affected inputs neutral and are
  surfaced in Today rather than suppressing the model opinion.
- Soccer runs a draw-aware Poisson/Dixon-Coles full-game 2.5-total research
  path. KBO/NPB preview, research/gated routing, daily coverage, settlement
  output, and `$0.50` tie-contract P&L are wired.
- Flat is isolated from soccer/esports/KBO/NPB. The unified runner no longer
  lets a flat phase clear or overwrite research/gated ledgers.
- Research and Gated Research are split into independent per-sport workbooks
  for Soccer, LoL, CS2, Dota 2, Valorant, KBO, and NPB. The dashboard
  aggregates those files without merging their storage.
- A centralized Gated Research eligibility wrapper now requires exact model
  inputs, a timestamp-valid executable quote, and the configured per-sport
  edge/confidence floors. Valid low-edge rows remain zero-unit Research
  `NO_CALL` observations; unresolved or untrained inputs enter neither ledger.
- The legacy mixed ledgers were archived intact under
  `data/archive/research-ledger-split-20260726T192729Z/`. The cleaned live
  ledgers contain 32 Research rows and 22 Gated rows with zero invariant
  violations.
- The full suite passes 436 tests.

## Verified audit result — 2026-07-26

The checkout is **not release-ready**.

| Check | Verified result | Interpretation |
|---|---|---|
| Tests | **410 passed, 4 failed** in 7.93s | Four dashboard order-preview tests are stale against the current `$5.00` unit value/cost cap. |
| Critical focused tests | **84 passed** | `audit`, `cli`, `domain`, `forward`, and `xlsx_ledger` now have focused tests; the old “zero tests” claim was false. |
| Critical imports | Pass | All requested core modules and all feature/data-source modules imported. |
| Python/package | Python 3.14.5; editable install points to this project | Environment and installed console entry point are healthy. |
| Console entry point | Pass | `.venv/bin/model-prediction --help` exits 0. |
| Audit chain | **16,387 events, 0 link breaks, 0 hash mismatches** | Cryptographic chain is intact. |
| Ledger/audit reconciliation | **Not reconciled** | No current ledger row lacks a creation event, but 1,150 historical creation events lack a matching audited removal event. |
| Artifact integrity | **31 of 33 valid; 2 mismatches** | `nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json` fail canonical SHA-256 verification. |
| Config artifact references | One missing; one semantically wrong | `market_residual.artifact` is missing; MLB total research still points to its spread artifact. |
| Ruff | **117 findings** | 79 are `EXE002`; the remaining 38 include six broad exception catches and correctness/style findings. |
| CLI summary | Pass | 4 open picks, 7.75 open units, shadow-accounting note present. |
| Dashboard | Health/status/matrix pass | `/api/summary` no longer exists. `/api/status` reports `promotion_allowed=true` while also warning MLB is below its qualification gate. |
| MLB dry forecast | Pass, no logging | 15 games, 9 confidence calls, `logged=0`; active v6 artifact reports `qualified=false`. |
| Targeted line execution | Mixed | Stdlib tracing measured 65–100% on the core pipeline modules, but only **8.3%** on `cli.py`. |

### Four failing tests

All failures are in `tests/test_dashboard_server.py`:

- `test_resting_order_preview_and_submit_persist_exchange_id`
- `test_submit_parses_success_after_interactive_prompt`
- `test_buy_at_current_ask_submits_marketable_ioc_limit`
- `test_manual_control_can_buy_at_ask_when_positive_edge_gate_is_disabled`

The current config sets `unit_value_usd: 5.00`. The test orders cost `$5.50`,
`$5.50`, `$6.00`, and `$6.40`, while their authorized caps are `$5.00`,
`$5.00`, `$5.00`, and `$6.25`. `preview_order()` therefore refuses them before
creating a nonce. This is test/config drift, not evidence that the cost cap
itself is broken.

## P0 findings

### 1. Real-money execution ticket is not bound to the ledger pick

`PolymarketExecutor.execute()` checks the row's record type and status but does
not prove that the ticket's pick ID, market slug, token side, action, price, or
quantity belongs to that row
(`src/model_prediction/data_sources/polymarket_execute.py:91-148`).
The dollar cap trusts caller-supplied `estimated_cost_usd` instead of
recomputing `price * size_shares`. Submission happens before the audit append
(`polymarket_execute.py:162-187`), so an audit failure can leave a submitted
order unrecorded.

**Operational rule:** do not use the real-money execution surface until the
ticket is cryptographically/structurally bound to the exact ledger row and all
economic fields are recomputed server-side.

### 2. MLB probable-starter validation is not point-in-time

On a historical cache miss, `espn_probables.py` fetches the current ESPN
scoreboard for the historical date and then caches the response indefinitely
(`src/model_prediction/data_sources/espn_probables.py:57-123`).
`validation.py:197-210` consumes this as a historical feature without a
historical `observed_at_utc`.

The active MLB v6 artifact is therefore a live research experiment, not a
promotable historical validation result. Its own artifact correctly says
`qualified=false`; the config override does not cure the provenance problem.

### 3. WNBA availability does not fail closed on source conflicts

`player_availability.py` documents fail-closed behavior but defaults to the
research-only `most_conservative` conflict policy
(`src/model_prediction/features/player_availability.py:151-164`).
The production path does not request `fail_closed` and suppresses common
parsing/conflict exceptions (`player_availability.py:275-301`).
`learned_forward.py:291-301` also logs a skipped availability adjustment at
DEBUG and continues.

### 4. Current eligibility policy bypasses declared risk gates

As of the 2026-07-26 operator directive,
`src/model_prediction/eligibility.py:28-91` accepts but does not use exposure or
market-disagreement inputs. After model-state, staleness, provenance, and ban
checks, `_call_result()` always returns a `QUALIFIED_SHADOW_CALL` and sizes
with `edge_scaled_units`; the exposure-aware `recommend_units()` decision is
not consulted.

The CLI still applies a pre-log executable-ask edge floor for some forecast
paths, but config exposure caps and the maximum disagreement value no longer
gate eligibility. Documentation and dashboard language must not claim that
those gates are enforced.

### 5. Ledger mutation and audit append are not one transaction

Ledger writes commit before the corresponding audit event is appended
(`ledger.py:500-507,635-648,743-770,795-796`). A crash or
`AuditLockTimeout` can therefore leave a created, settled, voided, or removed
row without its audit event. Some retry paths return early once the ledger
already reflects the mutation, so retry does not necessarily repair the audit
gap. Existing tests do not inject an audit failure between these two commits.

### 6. Artifact qualification and quote timestamp validity are informational

`learned_forward.py:304-330` labels a confidence-threshold call
`QUALIFIED_SHADOW_CALL` even when `artifact.qualified` is false. The CLI routes
`calls`, not `qualified_calls` (`cli.py:756-760`), before later config/state
gating. Separately, quote matching returns `timestamp_valid`, but the caller
does not enforce it (`learned_forward.py:431-439`, `cli.py:791-815`).
An invalid pregame snapshot can therefore price a call.

## P1 correctness and integrity findings

### International baseball

- `forecast --sport kbo|npb` without logging passes no research ledger, but
  `_forecast_international_sport()` dereferences it
  (`cli.py:1190-1208,1852-1860`). Read-only preview can crash.
- An early edge check skips KBO/NPB rows before the function can fulfill its
  contract of logging all research rows and only eligible rows to the gated
  ledger (`cli.py:1111-1148`).
- Ties are graded as ordinary moneyline pushes (`cli.py:1466-1493`,
  `pricing.py:33-56`). A contract settling at `$0.50` is not a refund unless
  its entry price was `$0.50`; tie P&L and calibration are economically wrong.
- Secondary-ledger settlement details are computed but hidden from `settle`
  and `daily` output (`cli.py:1882-1900,2116-2139`).

### Market and source semantics

- The Odds API key is placed in a query URL and exception text is returned to
  the caller, which can expose the key
  (`data_sources/the_odds_api.py:85-90`,
  `data_sources/odds_soccer_scores.py:64-69`).
- Polymarket snapshot aggregation hardcodes `timestamp_valid=true` even when
  an individual snapshot is invalid, and can report `status=ok` with missing
  executable asks (`data_sources/polymarket_us.py:404-448`).
- Event discovery requests at most 50 events with no pagination and turns
  per-league HTTP failures into empty slates
  (`data_sources/polymarket_us.py:112-153`).
- `guaranteed_signal.py:40-55` treats future timestamps as fresh because it
  checks only that age is below six hours, not that age is non-negative.
- Soccer head-to-head treats draws as away wins
  (`features/head_to_head.py:20-35`).
- MLB weather extraction passes the wrong payload shape, wind is not applied
  to the run factor, and live weather selects the first forecast hour instead
  of the event hour (`data_sources/espn.py:224-250`,
  `features/weather.py:40-75,115-160`).
- Feature ingestion marks an event ID as seen before validating the row, so a
  malformed first copy can suppress a later valid copy
  (`features/base.py:101-150`).
- The economic bootstrap gate fails only when the ROI confidence interval's
  upper bound is below zero; an interval spanning zero passes even though it
  does not exclude loss (`economic_gate.py:165-168`). The module also states
  that these gates are not wired into live eligibility.

### Concurrency and auditability

Ledger and audit file locks now use non-blocking `flock` with a 30-second
timeout; the old “locks hang forever” finding is fixed. The remaining problem
is transaction scope: exposure is calculated before the CLI's append lock, and
paired research/gated writes are separate transactions
(`cli.py:895-929,1085-1092,1204-1208`). Concurrent writers can approve from the
same stale exposure snapshot or leave paired ledgers inconsistent.

`verify-chain` reports the current chain intact but reconciliation false because
1,150 old creation events do not have audited removal events. Preserve that
historical gap; do not fabricate removal events.

### Artifact and release alignment

- All 33 JSON artifacts carry a hash field, but the NBA and NFL spread
  baselines mismatch their canonical content.
- `config/model.yaml` still points MLB total research to
  `mlb-spread-baseline-v1.json`, not `mlb-total-score-ridge-v1.json`.
- `models.market_residual.artifact` points to missing
  `config/models/market-residual-v1.json`.
- `outputs/latest/learned-model-validation.json` still names an old worktree,
  points MLB at v5, and predates active MLB v6 plus current KBO/NPB artifacts.
  It is not a reproduced release report for this checkout.
- `model-prediction models` still reports Soccer, esports, KBO, and NPB as
  research because it prints static registry specs instead of config-derived
  status (`models/registry.py:136-200`, `cli.py:1739-1740`).

## Current test map

The old zero-test inventory is obsolete. Focused files now exist for:

- `audit.py`: `tests/test_audit.py`
- `cli.py`: `tests/test_cli.py`
- `domain.py`: `tests/test_domain.py`
- `forward.py`: `tests/test_forward.py`
- `xlsx_ledger.py`: `tests/test_xlsx_ledger.py`
- core sport models: `tests/test_sport_models.py`
- execution gate: `tests/test_execution_gate.py`

The five critical focused files (`audit`, `cli`, `domain`, `forward`,
`xlsx_ledger`) pass 84 tests. Remaining high-risk gaps include the exact
execution-ticket binding invariant, KBO/NPB half-settlement economics,
transactional exposure-plus-append behavior, fail-closed WNBA conflict
handling, and secret redaction from provider errors.

Targeted stdlib line tracing during the full run measured:

| Module | Lines executed |
|---|---:|
| `domain.py` | 100% |
| `xlsx_ledger.py` | 96.2% |
| `audit.py` | 93.5% |
| `economic_gate.py` | 90.8% |
| `ledger.py` | 89.2% |
| `learned_forward.py` | 81.2% |
| `eligibility.py` | 77.9% |
| `forward.py` | 65.0% |
| `cli.py` | **8.3%** |

The highest-risk remaining low-execution modules are `cli.py`,
`mlb_statsapi.py`, `odds_soccer_scores.py`, `openligadb.py`, and
`wnba_availability_evaluation.py`. Line execution is not proof of behavioral
coverage; transaction failure, timestamp validity, conflict handling, and
secret-redaction invariants still lack direct tests.

## Reproduction commands

Run from the project root.

### New tooling (2026-08-15/16)

```bash
# MLB ingest completeness (P1-12): ESPN-listed vs ingested per ET day
env PYTHONPATH=src:. .venv/bin/python scripts/check_mlb_ingest_completeness.py --days 7

# v8 feature-parity sample + standardized evaluator (research branch)
# in the isolated worktree /Users/vincentc9002/worktrees/mlb-research:
env PYTHONPATH=src:. .venv/bin/python scripts/mlb_v8_feature_parity_sample.py
env PYTHONPATH=src:. .venv/bin/python scripts/mlb_evaluator.py --variants elo_only
```

### MLB v8 reproduction (aggregate + row-level)

```bash
# Aggregate pin-and-replay (date boundaries + threshold read verbatim
# from the artifact — never re-split, never re-learn the threshold).
env PYTHONPATH=src:. .venv/bin/python scripts/mlb_v8_reproduction.py

# Row-level parity (cohort / coefficient / probability layers).
# Lives on the research branch in the isolated worktree
# (/Users/vincentc9002/worktrees/mlb-research, branch
# research/mlb-v8-reproduction); see docs/V8_REPRODUCTION.md there.
env PYTHONPATH=src:. .venv/bin/python scripts/mlb_v8_row_parity.py
```

2026-08-15 findings (research workspace @ consolidation-2026-08-15):
aggregate replay reproduces closely (calls 150 vs 148, hit delta
0.0052); row-level shows a **31-row net cohort drift** (post-freeze
reconciliation backfill) plus **2 freeze-time rows missing** — v8 never
snapshotted its holdout event-id list, so exact-cohort reproduction is
impossible retroactively; excluding the 31 reproduces 148/148 calls.
**Coefficient parity fails** (refit vs shipped, max delta 0.0107):
history-dependent features (Elo/trend/park/weather) shifted with the
backfills, so v8's exact probabilities are unrecoverable; per-row p
drift bounded at 0.0006. v8 is never modified (its park-factor PIT
defect is part of what v8 was). The frozen v9 feature table exists to
prevent this class for future models.

### WNBA thread-affinity fix (2026-08-15)

Trace: after the sqlite-authority cutover, the daily log showed
`"logged": 0, "status": "error", "reason": "SQLite objects created in a
thread can only be used in that same thread"` — 212 occurrences on
08-14, 0 on 08-13. `RuntimeLedgerStore` shared one sqlite3 connection
across the ThreadPoolExecutor forecast workers. Fixed with per-thread
connections (WAL + busy_timeout serialize writes); regression test
`test_store_usable_from_multiple_threads` (revert-verified). Live
confirmation: the next cycle logged 6 open WNBA rows. The 08-14
removed rows were the remove-then-recreate ordering amplified by the
same bug (`_clear_today_open`'s started-game guard is intact).

### Health

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q

env PYTHONPATH=src:. .venv/bin/python -c "
import model_prediction.cli, model_prediction.validation
import model_prediction.learned_forward, model_prediction.eligibility
import model_prediction.ledger, model_prediction.forward
import model_prediction.audit, model_prediction.xlsx_ledger
print('All critical imports OK')
"

.venv/bin/python --version
.venv/bin/model-prediction --help >/dev/null
```

### Audit and ledger reconciliation

```bash
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain
```

### Canonical artifact hashes

NOTE (2026-08-13): the codebase's real loader convention is
`ensure_ascii=True` — this snippet previously used `ensure_ascii=False` and
"mismatched" every artifact, including two historical "mismatch" reports that
were artifacts of this snippet, never real bugs. If a run reports MISMATCH,
check the artifact with `ensure_ascii=True` before treating it as a finding.

```bash
.venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path

for path in sorted(Path("config/models").glob("*.json")):
    raw = json.loads(path.read_text())
    key = "artifact_hash" if "artifact_hash" in raw else "model_hash"
    canonical = {name: value for name, value in raw.items() if name != key}
    computed = hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    print(path.name, "OK" if computed == raw.get(key) else "MISMATCH")
PY
```

### Config artifact resolution

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import yaml

config = yaml.safe_load(Path("config/model.yaml").read_text())
keys = (
    "production_artifact",
    "research_artifact",
    "spread_research_artifact",
    "total_research_artifact",
    "artifact",
)
for model, item in config.get("models", {}).items():
    if not isinstance(item, dict):
        continue
    for key in keys:
        value = item.get(key)
        if value and not Path(value).exists():
            print(f"MISSING: {model}.{key} -> {value}")
PY
```

### Runtime, without writes

```bash
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary

curl -s http://127.0.0.1:8765/api/health
curl -s http://127.0.0.1:8765/api/status | python3 -m json.tool
curl -s http://127.0.0.1:8765/api/matrix | python3 -m json.tool

env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli forecast \
  --sport mlb --date YYYY-MM-DD --model learned
```

Do not add `--log` to the forecast audit.

### Ruff

```bash
.venv/bin/ruff check src/ tests/
```

## Repair order

1. Bind and recompute every execution ticket field against the exact qualified
   ledger row before any real-money order can be submitted.
2. Make ledger mutation plus audit append recoverable as one transaction, and
   add failure-injection tests before relying on reconciliation.
3. Remove probable-starter data from historical validation unless each record
   has genuine pregame `observed_at_utc` provenance; keep MLB v6 unqualified.
4. Enforce artifact qualification and `timestamp_valid` before a candidate can
   be classified, priced, or logged.
5. Make WNBA availability conflicts fail closed and test malformed/conflicting
   source combinations.
6. Restore green tests by making dashboard tests explicit about unit value and
   intended order cost.
7. Repair the two mismatched spread artifacts, the missing residual reference,
   and the MLB total artifact reference without overwriting rollback artifacts.
8. Make KBO/NPB preview read-only, correct half-settlement P&L, and expose all
   secondary-ledger settlement results.
9. Make exposure-check-plus-append transactional across processes and preserve
   consistency between paired ledgers.
10. Redact provider secrets, enforce non-negative timestamp age, fix soccer draw
   and weather semantics, and make slate truncation/errors explicit.
11. Correct the economic confidence-interval gate so a zero-crossing interval
   does not pass as evidence of positive ROI.
12. Reproduce a new versioned report from one stable green checkout. Keep model
   quality separate from executable-price profitability.

## 2026-11-15 (quick-261114-srb addendum): item 7 and the pooled-team check

Both follow-ups from the v6 entry above are done. Neither changes its verdict. Frozen fit sha256 6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70 is unchanged.

- **Item 7, market-anchored residual (`scripts/cfb_v6_residual.py`, commit 145255b3): NO PICK on both markets.** Rule `cfb-v6-residual-rule-1` (sha256 6b7cc7595a58c69aff5aab636643265fee4d34ffb767cda82c6d58b9ee67bb4f) was hashed before any data loaded. Fit window through 2022-11-11, validation 2022-11-12 to 2024-11-14, holdout window left unscored. Spreads: validation slope -0.045 [-0.207, +0.128], out-of-sample MSE delta +0.017 [-0.061, +0.098]. Totals: validation slope +0.216 [+0.033, +0.398] but the fit-window slope was -0.012 [-0.138, +0.106], so applying the fit coefficients is worse than the line (MSE delta -0.156 [-0.245, -0.068] in the line's favour). Book-only median gives the same verdicts. A line-anchored challenger collapses to the line and would decline every game. The ESPN lines are closing benchmarks without observed_at, so this does not test decision-time quotes.
- **Pooled-team check (`scripts/cfb_v6_pool_audit.py`, commit e83e5f8c): the reported numbers stand.** Zero scored games in the holdout (1,527), the 575/576 reference or the ESPN-lined cohorts had an FBS side rated as the pooled lower-division team. 148 FBS team-events (Florida International, Missouri State, Sacramento State) lack a `resolve_team` alias but are rated under stable ESPN-id keys. A second look excluding them: holdout Brier delta -0.00992 [-0.01495, -0.00505] against -0.01029; every market-relative correlation keeps its sign and its zero-inclusion.
- **Still open for the operator, not changed here:** (1) the live daily ingest sends no `groups` parameter for NCAAF, so v5's ratings sit on history missing most FBS-vs-FCS games and all of 2026 week 6; fixing it shifts v5's inputs and needs a governed decision; (2) v6 needs a known-team gate before any serving use, because an unseen name falls into the pooled team instead of declining; (3) `tests/test_artifact_hash_verification.py` fails on four non-v6 artifacts and failed before this work; (4) the frozen-fit pin on `scripts/cfb_v6_lib.py` matches the CRLF working file, not the LF blob, so `verify-frozen` fails on an LF-only checkout.


## 2026-11-15 NCAAF history backfill, capture wiring, hash-test documentation (quick 261115-w7k)

- Operator waived the pinned history-impact REJECT (holdout Brier delta +0.001065 vs +0.001 limit, CI spans zero). Applied the 1,478-game FBS-involved backfill to data/processed/ncaaf/games.jsonl (append set sha256 7d0f9caf..., loaded history 9,498 rows) and shipped the groups=80/81 NCAAF ingest. No refit, no promotion.
- Forward capture is wired into the daily run as a fail-soft step (step1m_cfb_pregame_capture).
- The four non-verifying artifacts are documented evidence, not drift: three NBA v8 files embed a label, tennis-games-markov-v3 a stale hash. Listed in KNOWN_NON_VERIFIERS.
- Still open: ~6,900 FCS-vs-FCS events not appended; v5 refit on the complete history not done; line-movement retest needs a season of capture.

## 2026-11-15 FCS-vs-FCS append (quick 261115-x4q)

- Pinned cfb-fcs-append-rule-1 ACCEPT: holdout Brier 0.19036 to 0.18974 (delta -0.00062, CI [-0.00231, +0.00076]); neutral within noise. Applied 6,894 groups=[81] rows to the processed history only (16,392 rows loaded). One-time snapshot, not maintained by the daily ingest. No refit, no promotion.
