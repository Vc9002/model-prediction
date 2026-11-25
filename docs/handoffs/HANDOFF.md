# Handoff — open work (trimmed 2026-11-22)

**Status: OPEN.** Delete this file when every item below is done or dropped. The previous 13-section
version (NCAAB, NFL 7-12, NCAAF, NBA v10 logs) is archived verbatim in `docs/DEBUG.md`, entry
"2026-11-22 — Handoff archive". Nothing is pushed. The scheduled tasks run the working tree, so check
`runs.db` for `started` rows before editing source in `E:\MP`.

## Next action

0. **LIVE auto-buyer, real money (top priority), section 0 below.**
1. **Operator, hardware (blocks trust in everything else):** run a real RAM test, see section 1.
2. Check the first scheduled daily runs after the daily-speed merge, section 2.
3. Decide whether to fetch 2014-2021 player boxes to confirm NCAAB P1 (player availability), section 4.

## 0. LIVE auto-buyer (REAL MONEY), state at 2026-11-25 19:00 ET

Cohorts unchanged: LIVE `ncaab-spread-v7` and `ncaab-total-v6` (saved and live-ask edge 5-12%); PAPER `ncaab-moneyline-v7m`
and `nba-spread-margin-v9` (>=12%). $5/unit, $25/game, $500/day spend, $250/day loss stop, 35-min buy window. Operator
decisions: spread v7 stays live at current stakes, judged ONLY on `ncaab-spread-v7` rows from 11-21 on (memory
`feedback_spread-v7-scope-v7-id-only.md`); buy window stays on GROSS edge (fees recorded, not gated). Kill switch: use the
dashboard toggle (a hand edit of `enabled` can be overwritten by a running cycle until the fix below lands).

**Landed 11-25 (refactor/cleanup-2026-11-11, not pushed):** 894f9542 + 3c1dfd4a (wrong-team spread mapping refused, both
copies); quick-261125-hih Task 1 14bc409f + fixes c2af49a8, 0db5d783 (idempotent POSTs, intent before send, submit_unknown,
GTD fallback, strict ledger write); Task 2 327b8ccc (total dedup key, same-contract top-up, robust reconcile, kill-switch
cancels, 3 h live age, calibration gate); 7f77fb44 (overnight age extension for late tips); 1e49d24c (B7 reads the pick's own
calibration); 8baae50b + d6152144 (settle ESPN reverse-listed games, date-checked). Every one critical-reviewed. Telegram is
buyer-alerts-only (outside the repo, Rainmeter `@Resources`, backups `*.pre-telegram-buyer.bak`).

**In flight when this was written (agents in worktrees under `E:/MP-wt/`; check `git log` and `git worktree list`):**
hih Task 3 (`hih-t3`: cancel worker every 2 min, dashboard refused-toggle + LIVE label, drawdown warning, docs); daily
re-price fix (`reprice`: `cli/commands.py::_clear_today_open` compares the ET run date with raw UTC string prefixes, so
evening/late picks are never re-priced; also update `auto_executor._daily_clear_retires` and its parity test); Telegram v2
(review fixes, health alerts, edit-in-place results, `/open`, `/today`; backups `*.pre-telegram-v2.bak`).

**Queued, in this order (operator said go ahead on all of it):**
1. **hih Task 4:** register ONLY the new cancel-worker task (never re-run the whole `register_windows_tasks.ps1`: it flips
   S4U tasks to Interactive), add the drawdown state keys with a backup, restart the dashboard, read back the first live GTD
   fallback (if the exchange does not echo tif/goodTillTime, every fallback is cancelled and the buyer runs IOC-only).
2. **Buyer fixes:**
   - kill-switch hand edit overwritten by a running cycle (save only owned keys onto a fresh read; refuse to save over an
     unparseable file);
   - the 1U value scales the live caps (cap live in dollars);
   - the loss stop counts manual rows, nets wins against losses, and keys by settle day;
   - resting-fallback cancel uses the global 3.5% floor, not the cohort window;
   - fee accounting (record fee and average fill price; fee-inclusive cost, caps and P&L);
   - settle route and `cli settle` without daily.lock;
   - same-orientation ESPN rematch has no date check (yesterday's final can settle today's game);
   - two older top-up merge issues (an unknown top-up IOC reconciled as a fallback; merge picks the first same-pick row);
   - a started fallback with a permanently missing id gets a failing cancel every reconcile.
3. **Operations:**
   - the buyer shares daily.lock, so it misses windows and settles late (11-25: totals waited 40 min);
   - failure alerts and a balance check before buying;
   - nightly backup of `auto_buyer_ledger.jsonl` and `auto_buyer_state.json`;
   - 3-4 `dashboard_server` processes found on every restart;
   - IOC fill trusted in a non-terminal state;
   - tip time not cross-checked against Polymarket's start;
   - live pass can fall back to xlsx picks.
4. **Tracking:** live fill log, CLV at the real pre-tip close (the buyer's own close equals entry), skip reasons, line-move
   log, daily dashboard, paper copy of the live cohorts, tip time for the pre-registered afternoon test (one cut, 5 PM ET).
5. **Improvements:**
   - save the market slug at forecast time;
   - merge the duplicate spread-side resolvers (`dashboard/backtests.py` and `polymarket_execute.py`);
   - compute edge on the limit price, not the raw ask;
   - add team-name aliases (UCA, NAU, EIU, UML, Tenn. State, Ore. State).
6. **Cleanup** (~15 items from the 11-25 review): unused `force` param, unread `p_info` and index keys, duplicate assert,
   dead start-time check, dead audit dedup, always-true hasattr, legacy v6 live bucket and the empty whitelist pass, unused
   `manual-bet-sync` worker, dead state keys, stale "30-minute"/"GTC"/"paper only" comments, American-odds comments and
   xlsx column, `not_authorized` fallback label, runtime-root clutter. Tennis/NCAAF branches only with operator approval.
7. **Research:**
   - v7 shrink-to-market challenger;
   - total v6 market-skill check;
   - re-test spread v7 once teams have 8+ games;
   - NCAAB injury (P1) shadow feature, whose one-shot confirmation needs an operator yes; command in the 11-25 transcript:
     `scripts/ncaab_research/confirmation/confirm_p1_261122.py read`.
   - Dropped on evidence: rest/back-to-back, more Elo.

**Operator:** unpin Telegram message 142 (old best-pick card).


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

- Serving: moneyline `ncaab-moneyline-v7m`, spread `ncaab-spread-v7`, total `ncaab-total-v6` (buyer state blacklists v7 and v7m; NCAAB buyer stays disabled by operator state).
- **Addendum v4 scorecards read 2026-11-22** (22,947 D1 games with a closing spread, 2023-2026, leave-one-season-out; `outputs/ncaab/research_261120/`):
  **P1 player availability is DEV_ELIGIBLE** vs served v7: spread corr delta +0.0065 (K-adj CI +0.0013..+0.0121), margin RMSE -0.017 pt,
  moneyline log loss -0.00065 (K-adj CI -0.0011..-0.0002), cover log loss -0.0008 (K-adj CI includes zero). Still **no edge**: its
  corr with (result minus closing line) is +0.003 (CI -0.011..+0.018), closing-line RMSE 11.19 vs 11.44; the shuffled-outcome control
  degrades it (corr delta -0.019), as a clean control should. **P2 returning production is NOT_ELIGIBLE** (late-fold corr delta
  -0.0014; the RMSE and log-loss gains do not move corr vs the line). Next, only if wanted: fetch 2014-2021 player boxes and run the
  sealed confirmation (2018-20 + 2022) for P1; expect a small accuracy gain on a market that already beats us, not an edge.
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
