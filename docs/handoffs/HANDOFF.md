# Handoff — open work (trimmed 2026-11-22)

**Status: OPEN.** Delete this file when every item below is done or dropped. The previous 13-section
version (NCAAB, NFL 7-12, NCAAF, NBA v10 logs) is archived verbatim in `docs/DEBUG.md`, entry
"2026-11-22 — Handoff archive". Nothing is pushed. The scheduled tasks run the working tree, so check
`runs.db` for `started` rows before editing source in `E:\MP`.

## Next action

0. **Auto-buyer (LIVE ON since 11-26 12:41 ET, $50/day loss stop; operator decisions and open items), section 0 below.**
1. **Operator, hardware (blocks trust in everything else):** run a real RAM test, see section 1.
2. Check the first scheduled daily runs after the daily-speed merge, section 2.
3. NCAAB P1 (player availability) confirmation read is DONE (11-26, CONFIRMED, small; behind the market line). Promotion into v7
   needs a walk-forward ablation and an operator decision, section 4.

## 0. Auto-buyer: LIVE ON (11-26 12:41 ET), $50/day loss stop

**LIVE BACK ON 2026-11-26 17:41Z at the operator's request:** `live_enabled: true`, `max_daily_loss_usd` 250 -> 50 for every day
(operator: "for everyday stop loss of 50"); state v1327, backup `auto_buyer_state.pre_loss50_live_on_20261126T174152Z.json`.
First run 17:42Z clean, 0 orders; live cash $95.95. Cohorts unchanged (decision (a) not taken); GTD read-back still unverified.
Earlier history: the operator turned live off
after the first live day (11-25, also the cumulative record: 24 settled buys) went 7-17, -$74.90 on $177 (`ncaab-spread-v7` 5-11 -$45.23, `ncaab-total-v6` 2-6
-$29.67). Live cohorts stay configured and skipped (`LIVE_DISABLED`). Paper cohorts: `ncaab-moneyline-v7m` 12%+; paper twins
(`paper_twin: true`, approved 11-26) of spread v7 and total v6 at saved edge [5%, 12%); `nba-anchor-v10` spread and total
[0, 25%), `nba-spread-margin-v9` spread 12%+ (restored 11-26 06:39Z alongside v10; backup
`auto_buyer_state.pre_v9_paper_back_20261126T063905Z.json`; one buy per game+market, so a v9/v10 collision goes to whichever buys
first, 3 of 34 recent games) and `nba-elo-trend-lr-v5` moneyline [5%, 25%) (NBA totals paper-only in code, 7f0e7517). Review each
NBA paper cohort at ~50 buys: keep if mean close-price movement after fee is positive. $5/unit, $25/game, $500/day spend,
$250/day loss stop, 35-min buy window. Switches: `enabled` is the master (false stops both books); `live_enabled` stops only the live
book and cancels its resting orders by id; the dashboard has a separate "Live (real money)" button. Operator decisions: spread v7
is judged ONLY on `ncaab-spread-v7` rows from 11-21 on (memory `feedback_spread-v7-scope-v7-id-only.md`); the buy window stays on
GROSS edge (fees recorded and shown as `net_edge`, never gating). Telegram (buyer-only alerts, health alerts, edit-in-place
messages, `/open`, `/today`) lives outside the repo in the Rainmeter `@Resources` folder; backups `*.pre-telegram-buyer.bak`,
`*.pre-telegram-v2.bak`.

**Next action first:**
1. **Operator decision waiting:** (a) total v6 loss-limit options if live comes back (cap same-slate overs, narrow to 5-8%, flat 1U).
   Decided and done 11-26: (b) P1 one-shot read run: CONFIRMED (corr delta +0.0048, one-sided 95% lower +0.0021, n 20,826; margin
   RMSE 11.276 vs v7 11.286 vs line 11.132), card `outputs/ncaab/research_261120/P1_player_availability/confirmation_v5.md`;
   (c) removed the `manual-bet-sync` worker and the legacy v6 live bucket (3767c9ff), state key deleted (backup
   `auto_buyer_state.pre_drop_v6_bucket_20261126T065736Z.json`); the whitelist pass STAYS (paper trials incl. the other session's
   NBA ML v6 trial use it; the scheduled global pass is also the ungoverned live path when no `cohorts` key exists, removable only
   as a separate real-money change); tennis/NCAAF gates kept; (d) Telegram 142 unpinned by the operator; (e) refuse, see below.
2. **Before any live re-enable:** the first live GTD fallback (11-26 00:01 UTC, total v6 over, 20 shares) was cancelled
   `cancelled_unverified_gtd` on a 404 read-back, before the retry fix. No fallback has been read back through the new path
   (`fallback_verified_via` is empty), so whether the exchange echoes `tif`/`goodTillTime` is unproven. The dashboard is on the current code (see item 4).
3. **Telegram control (operator: "full + cancel orders").** Repo CLI `python -m model_prediction.telegram_control` (dd7b56d7,
   f2c429bb, 40f20d0b) + the Rainmeter `TelegramBot.py` (backups `*.pre-telegram-control*.bak`): `/status`, `/orders`, `/cancel N`
   (exact full id from the last `/orders` list <= 10 min, `--exact`, never cancel-all), `/liveoff`, `/pause` (live off then master
   off), `/resume` (paper only, live forced off), `/liveon` (6-digit code, 60 s, single use). Owner chat + user id only, forwarded
   messages refused. Audit `E:\model-prediction-runtime\auto_buyer_telegram_audit.jsonl`. Only read-only `status`/`orders` have run
   against the real runtime; the account-wide open-orders read is unproven with real resting orders (union with ledger markets and a
   `partial` flag cover it). PASS WITH NITS after 3 review cycles (DEBUG 2026-11-26); open nit: `Telegram.py` temp file
   is named by PID only (heartbeat thread and main thread share it).
4. **Pinned-market refuse (operator (e)), ce7565f8 + 9c245f0f + 57701e0d, PASS WITH NITS after 3 cycles.** A pick with a recorded
   market is never name-matched: refused (with a reason in the dashboard and the buyer skip log) when its slug was never captured
   (day-1/day+1 files searched first, exchange start within 300 s), or no snapshot is pregame against min(row start, exchange start),
   or the pinned side has no ask. Naive/unreadable times fail closed. Dashboard restarted onto it (listener PID 21136, 11-26 ~02:30 ET;
   note `stop_dashboard.ps1` disables the task, re-enable with `Enable-ScheduledTask ModelPredictionDashboard`). Follow-ups:
   `polymarket_execute._verify_live_side_and_timing` should also use the exchange start; `_event_already_started` should fail closed
   on an unparseable start; `_pick_quote_refusal` is test-only; dead-code nits (manual_bet_ledger `_run_scheduled_sync` docstring
   still points at the removed worker; test_auto_buyer_cohorts.py ~786 docstring overstates `whitelist_models` being ignored).
5. **Cleanup batch landed** (ac9a5b82, critical-reviewed PASS WITH NITS). Two low-risk nits open: compute the P1
   `_p1_in_progress` live set before `PlayerBoxIndex.load` (college_basketball_forward.py ~466-469, a raise reloads the
   season index per game); catch `AttributeError` for a non-object `.parse_failed` marker (ncaab_boxscores.py ~365-370).
6. **Early December:** re-test spread v7 once teams have 8+ games (same v7-id-only rule); read the pre-registered afternoon test
   (`docs/research/ncaab_spread_v7_afternoon_test.md`, one cut 17:00 ET) forward only; review cohorts on the paper twins and Flat rows,
   since live is paused.

**Landed 11-25/26 (refactor/cleanup-2026-11-11, not pushed; every change critical-reviewed; full traces in `docs/DEBUG.md` "2026-11-25/26"):**
- hih Tasks 1-3 (14bc409f, c2af49a8, 0db5d783, 327b8ccc, 34199a47, d0390627, 8d7bfd0d, eb82edd5) and Task 4 (coordinator: task
  `ModelPrediction-AutoBuyerCancel` registered 11-26 00:18 ET via UAC, S4U, every 2 min from 08:00 for 18 h; first run clean).
- Fixes: wrong-team spread refused in both copies (894f9542, 3c1dfd4a); late-tip age extension (7f77fb44) and the ET-date daily
  re-price clear (cb8493b3); B7 on the pick's own calibration (1e49d24c); ESPN reverse-listed settlement (8baae50b, d6152144).
- Ops batch 35eaade9..f252f5af and 6ffea002: own `auto_buyer.lock`, failure alarms and balance check, backups, dashboard stop script,
  IOC finality, tip cross-check, SQLite-only live picks, GTD read-back retry.
- Buyer fixes (d7cc13b7, 2f47ef30, 6c598b19, 40273c7d, 0f088043, e202406a) and the live/paper switch (76f0b63c, 3847e750, a7b4f9a5).
- Tracking (62848c71, 31200d50, db1a29e5, 415266fe, f34943e5): live fill log, skip log, close-CLV daily report, paper twins.
- Improvements: one spread-side resolver (7de8fdfe), edge on the sent price (f10bf007), recorded market pin and 13 aliases
  (9398ea7b), pin guards and the mascot-rule fix (3385dbd7, f7eaaa9a, a389afef, 0ad1e461).
- NCAAB P1 shadow feature, inert (9efb85af, 50ad838f, d445f435); the coordinator ran the backfill: 1,419 boxes, 0 missing.
- Research (nothing served changed): spread v7 no market-relative skill (model-minus-market coefficient about -0.2); v7 shrink-to-market
  lambda 0, no challenger built; total v6 no proven edge, about 78% overs; rest/back-to-back and Elo blend dropped; injury P1 a small real
  signal, not tradeable; afternoon gap probably noise.


## 1. Suspected bad RAM (open, operator action)

- Three unexplained bugchecks with no driver frame: `0x139` 11-07, `0x133` 11-19, and `0x1a` MEMORY_MANAGEMENT
  (param `0x404`) 11-21 12:44. Plus single-bit flips in `market_quotes.db` (11-18) and python.exe crashes.
- Windows Memory Diagnostic ran after the 11-21 reboot and was **canceled** (event 1103 at 12:44:45), so there is
  still no completed RAM test. `bcdedit` needs an elevated shell; re-arm with `bcdedit /bootsequence {memdiag}` or run
  `mdsched.exe`, or use MemTest86 overnight (multiple passes).
- DIMMs: G.Skill F4-3200C20-16GVK (16 GB, CL20) mixed with 2 x Corsair CMK16GX4M2B3200C16 (8 GB, CL16); Windows
  reports 3200 MT/s at a configured voltage of 1.2 V, which is below the 1.35 V these XMP kits normally need. Cheap
  first test: disable XMP/DOCP (JEDEC speed) for a week, then test DIMMs one at a time.
- Checked 2026-11-22 after the 11-21 crash: full `PRAGMA integrity_check` on `ledgers.db` (1.5 GB), `runs.db`,
  `production.db`, `market_quotes.db` and `dashboard_cache.db` all return `ok`. Re-run after every crash; `quick_check` does
  not compare indexes.

## 2. Daily speed and exit-1 runs (landed 2026-11-22, quick-261122-spd; watch the first runs)

- **Exit-1 causes (runs.db 11-12 to 11-22, 3 of 91 daily runs): none is a code defect.** Low host memory (11-12, 11-13),
  native heap corruption in the ingest step (11-20), a host bugcheck (11-19): all section 1. "Zero exit-1 in a week" is not
  reachable in code while the RAM fault persists. No ingest retry was added: it would only turn a hardware fault green.
- **Timing (53 reports 11-15..11-22):** mean 1,086 s; capture pool 731 s (request-rate bound, not fixable here); forecast
  phase 284 s. The old 1,200 s figure was mostly sports now switched off.
- **Changed:** per-model xlsx appends spool to `<name>.xlsx.pending.jsonl` (fsync'd) and the workbook is rebuilt once when the
  daily deferral exits (estimated saving 100-150 s/run, not yet measured); `cli/event_horizon.py` skips NBA/NFL/WNBA/MLB-moneyline
  scoreboard tasks (and NBA/NFL/WNBA spread and total) when a well-formed scoreboard has no game left (saves about 15-20 s on
  non-game days). Fail-open: any fetch error or odd date means the task runs. NCAAF, MLB totals and NRFI stay ungated.
- **Verify after the first scheduled runs:** report keys `timing.event_horizon_probe_seconds` and `forecast_tasks_skipped_no_event`;
  `forecast_seconds` should drop versus the 284 s mean; no `*.pending.jsonl` left under `model_ledgers/` once a run ends.
- **Known limits:** the dashboard's direct xlsx read lags model-ledger rows until the run's flush; the startup fold covers
  `<data_root>/model_ledgers` and the ledger path's `model_ledgers` only; one empty-but-200 scoreboard would skip a league's
  tasks for that run (the 6-hourly cadence limits it). Batching `supersede_open_rows`/`restore_unreplaced_picks` was declined
  (about 7 ms/row; audit-first ordering).

## 3. Decisions recorded 2026-11-22 (operator said "go ahead"; every one is "leave as is", reversible)

| Item | Decision | Why |
|---|---|---|
| Non-binary terminal markets stay open; no age-out void | Leave as is. A hand-void handles a real non-binary settle. `portfolio/auto_buyer_ledger.py` (~2173) still books a push; leave. | A silent age-out void is how 207 rows got a wrong outcome. Revisit only if open-row count grows. |
| v9 unread/defaulted artifact keys | Leave logged and pinned (`tests/test_nba_v9_artifact_keys.py`). | v9 is not whitelisted; a new version needs a `model_promotion` run with evidence. |
| Paper quote age unbounded (`max_quote_age_seconds`) | Leave. | Paper picks use decision-time executable quotes; real-money path has its own freshness gate. |
| Auto-buyer live-order path gated only by saved `mode` (`paper`) | Leave. | Real-money execution is a separate gated path by operator rule; do not add a second switch silently. |
| NBA moneyline evidence label (`nba-elo-trend-lr-v5` is `historical_only`) | Leave `historical_only`; the whitelist entry stays inert. | Flipping to `predictively_qualified` is a governed config change and the replays show no NBA edge. Needs an explicit yes. |
| Calibration pipeline can change a served transform without a promotion record | Leave. | Serving already requires own-model fit provenance and >= 100 outcomes (quick-261112-11j). |
| NFL v7 evidence label / NFL Main follows artifact not registry; NFL totals in buyer | Closed: leave serving (operator directive 11-15). | Nothing comes off production. |
| NCAAF: 243 Main v4 rows on 16.2 arithmetic; ESPN-consensus rows in Main; Main totals | Leave. Rows are labelled `espn_consensus_indicative`. | Same directive. |
| NFL `nfl-moneyline-margin-v1` promotion | Not promoted. | Accuracy gain is the line inside the margin, not edge; needs Vincent's yes and `model_promotion promote`. |
| 205 voided esports rows (CS2 197, Dota2 7, LoL 1) | **Closed: the voids are correct, do not re-grade.** No alias, window or script change. | Investigated 2026-11-22: 162 of the 205 are BO3 `defwin` (forfeit) or `canceled` at the event time; Polymarket settles those at the last fair price, not to the walkover winner. 0 of 205 have exactly one unambiguous finished series (12h/24h windows). All 117 markets are `RESOLVED` with non-binary `settlementPx`; 8 control win/loss rows settled binary. |
| Push books 0U on a market that really settles at the last price (e.g. 0.52) | Open, small: grading those at `settlementPx` would be more faithful than 0U, but it adds a new result type to the ledger. Not done. | Only matters if paper PnL is ever reconciled against real fills. |

## 4. NCAAB

- Serving: moneyline `ncaab-moneyline-v7m`, spread `ncaab-spread-v7`, total `ncaab-total-v6` (buyer state blacklists v7 and v7m; the legacy global NCAAB buyer stays disabled by operator state; the cohorts in section 0 override it).
- **Addendum v4 scorecards read 2026-11-22** (22,947 D1 games with a closing spread, 2023-2026, leave-one-season-out; `outputs/ncaab/research_261120/`):
  **P1 player availability is DEV_ELIGIBLE** vs served v7: spread corr delta +0.0065 (K-adj CI +0.0013..+0.0121), margin RMSE -0.017 pt,
  moneyline log loss -0.00065 (K-adj CI -0.0011..-0.0002), cover log loss -0.0008 (K-adj CI includes zero). Still **no edge**: its
  corr with (result minus closing line) is +0.003 (CI -0.011..+0.018), closing-line RMSE 11.19 vs 11.44; the shuffled-outcome control
  degrades it (corr delta -0.019), as a clean control should. **P2 returning production is NOT_ELIGIBLE** (late-fold corr delta
  -0.0014; the RMSE and log-loss gains do not move corr vs the line). **P1 sealed confirmation read 2026-11-26: CONFIRMED**
  (2018-20 + 2022, pooled corr delta +0.0048, one-sided 95% lower +0.0021; 2022 alone -0.0008). Small accuracy gain, no edge: margin
  RMSE 11.276 vs v7 11.286 vs closing line 11.132. Next, only if wanted: walk-forward ablation of v7 + P1 and an operator promotion decision.
- Rollback: moneyline first (`model_promotion rollback --sport NCAAB --market moneyline`), then spread.
- Forward check: split on `COALESCE(json_extract(json_extract(decision_payload_json,'$.model_input_snapshot_json'),'$.features.margin_engine'),'v6')`; the v7 cohort includes `ncaab-moneyline-v7m` rows.
- Open: the ledger `model_probability` column is raw while the payload is served (readers fixed); check settled NCAAB rows carry close != decision price.

## 5. NBA v10 forward clocks (research only, nothing blocking)

- Weekly: `python scripts/nba_v10/g2_report.py` (G2: n=150 picks) and `python scripts/nba_v10/forward_report.py` (test A: 400 games per market; test B: 100 events). Both stay PENDING until their pre-registered n.
- Task `ModelPrediction-NbaPretipCapture` runs every 10 min, 11:00-02:00 ET.
- Open before 400 games: `forward_report.py` lacks the test-A paired player-minus-v9 slope (PREREG_FORWARD.md section 5); v10 spread rows carry a null `model_artifact_hash`; upcoming games are treated as regular season (fix before April); 2026 playoff games are missing from `nba_games_all.jsonl`.

## 6. NFL / NCAAF watch items

- NFL early-line log (`scripts/nfl_early_line_log.py`, run daily): score `data/research/nfl_early/early_lines.jsonl` against closing line and result once games settle (clean prospective test of the opener/line-move signal). CLV and opener tests become readable roughly 2026-12-07 to 2026-12-10.
- NCAAF: `cfb-structural-v5.2` champion since 2026-11-23 (v5 with totals shrunk to ~50%), v5 rollback. Owed (low): refresh the `calibration` block in `config/models/cfb-structural-v4.json`; `ELO_CONFIG["ncaaf"]` is dead code to remove when `elo_ratings.py` is next touched.
- **NEXT (operator-approved 2026-11-23): champion serving path for `cfb-structural-v7.1`** (NCAAF spread and moneyline; totals keep a shrink). The v7 family only prices Flat/research through `cfb_structural_v7_challenger.price_challenger_slate`, and `college_football.cfb_model` requires one artifact for all three markets, so this needs a v7.1-based champion artifact with a total shrink, Main gates, replay snapshots, `_SERVING_CLASSES`, the Main version list (`cli/forecast.py` ~5933), the buyer whitelist (both layers) and `model.yaml active_production_version`. Evidence: `outputs/ncaaf/cfb_v71_report.md` (vs v5 on the holdout: margin MAE -0.667, Brier -0.0079). Caveat: its CFBD inputs are current-revised, not as-published.
- NFL spread: `nfl-spread-structural-v7.2` champion since 2026-11-23 (slope 0.378 fitted on validation by operator exception, quick-261123-6q6); NFL spread Main picks effectively stop.
- **Watch (2026-11-23 promotions, quick-261123-2w2):** NCAAF total picks and NFL total Main picks should drop to near zero. A v5.2 total can still reach Main if the leaning side's ask is <= ~48c (lopsided book): 0 of 906 past v5 total decisions had such an ask. A nightly calibrator fitted on v5.2's ~50% totals could stretch them back out; check `data/calibration/models/cfb-structural-v5.2.total_calibrator.json` stays identity.

## Done when

Sections 1, 2 and 4 are resolved and sections 5 and 6 have no open item, then delete this file.
