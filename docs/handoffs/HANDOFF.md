# Handoff — open work (updated 2026-11-22)

**Status: OPEN.** Delete this file when every item below is done or explicitly dropped.
Finished and removed: the `refactor/cleanup-2026-11-11` triage (the branch is the live branch at
`7a234da4`; the 12 failures it listed all pass now: `test_cli`, `test_five_blind_spot_guards`,
`test_forecast_runtime_regressions`, `test_tennis_markov`, 111 passed on 2026-11-22), the 2026-11-22
review fixes and the helper dedupe (merged, worktrees and branches removed). History is in git,
`DEBUG.md` and `ROADMAP.md`. Nothing is pushed.

## Next action

Decide the items in section 0 (operator decisions left by the 2026-11-22 review fixes), then
section 1. The scheduled tasks run the working tree, so do not edit source while a run is in
progress (check `runs.db` for `started` rows first).

## 0. Review-fix follow-ups (2026-11-22; decisions only, no code owed)

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

## 1. NCAAB (updated 2026-11-21 evening, quick 261121-hmc)

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

## 2. Best-picks audit script (offered, not done)

`scripts/audit_best_picks.py` `audit_candidate` hardcodes CONFIRMED_BEST for NBA spreads >=8% edge
on double-digit dogs, NCAAB overs 8-12%, and NCAAB moneyline; none replicate from the ledger
(NBA spread bucket recomputes to 4W-6L, n=10; `ncaab-total-v6` is 4W-10L at >=12%). Remove the
overrides or make the verdict compute from the bucket and require
`market_quote_provenance == decision_time_executable_quote`, then update
`docs/BEST_PICKS_AUDIT_FRAMEWORK.md` (it has uncommitted edits in the tree).

## 3. NBA moneyline whitelist — v4 eligibility decision

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

## 3b. Calibration pipeline vs serving transforms (new 2026-11-12)

The nightly calibration pipeline rewrites `data/calibration/models/*_calibrator.json` and the NBA total
slate / NFL slates / auto-buyer read them at serving time. Since `quick-261112-11j` an artifact is only applied with
own-model fit provenance and >= 100 outcomes, but the pipeline can still change a served transform with no
promotion record. Decide whether that is acceptable or whether serving must pin a hash.

## 4. NFL v8 challenger (replayed 2026-11-15: not promotable; keep unqualified)

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

## 5. Daily forecast speed and reliability

- 2026-11-11 daily log: cycle is bound by throttled network I/O (Polymarket BBO capture 540s with
  4 workers and a 429 guard, auto-buyer cycle 272s, NCAAB settlement 73s). The gateway limits by
  request rate, not concurrency; request volume is the only lever.
- Done: one request per market (`snapshot_from_slate`), capture horizon `POLYMARKET_CAPTURE_HORIZON_HOURS`
  (default 12), DNS retry, per-task timing under `timing.forecast_sport_done_seconds`.
- Open: skip per-sport forecast tasks (research pool, about 1,200s in the 2026-10-22 profile) when a
  sport has no event inside the horizon; batch ledger writes into one transaction; cache source
  fetches per run. Target: zero exit-1 runs over a week. Verify with a single-sport run, not the full suite.

## 6. NCAAF

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

## Done when

Sections 0 through 6 are each resolved or explicitly dropped, and this file has been deleted.

## 7. NFL fix batch 2026-11-11 (done; follow-ups open)

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

## 8. NFL fix batch 2026-11-15 (`quick-261115-21e`; done, follow-ups open)

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

## 9. NFL spread/total improvement search (2026-11-15 evening: nothing beats the line)

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

## 10. NFL opener / line-movement data (fetched 2026-11-15 night)

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

## 11. NFL replay extended to 2016 (2026-11-16)

- nflverse weekly player stats 2015-2020 cached in `data/research/nfl_history/`; replay now 2,826 games
  (was 1,489), real starters in 2,818. Dump is scratchpad-only (`dump_2016.py`, `big_test.py`).
- v7 as served on 2016-2020, which it was never tuned on: spread 666-639 (51.0%), Brier 0.2503; total
  664-663 (50.0%), Brier 0.2517. All 2,826: spread 52.0% +/-1.9%, total 50.8% +/-1.9%.
- corr(model lean, result vs closing line): spread +0.013, total -0.0001. Season-forward refits
  (2018-2026, 2,235 / 2,270 games) do not beat v7. The total model has no signal against the close.

## 12. NFL early-line log (started 2026-11-16)

- `scripts/nfl_early_line_log.py <date> [<date> ...]` prices upcoming NFL spread and total slates with the
  serving v7 engines days before kickoff and appends first-sight rows to
  `data/research/nfl_early/early_lines.jsonl`. It builds no ledger object and writes nothing else.
- First run 2026-11-16 ~05:00Z: 13 games x 2 markets (11-16 MNF, 11-19 TNF, 11-22 Sunday), all with
  Polymarket asks. The daily job now runs it once a day for the next seven days (`_nfl_early_task`).
- Purpose: clean prospective test of section 10 (model vs early line predicts the move). Score each row
  against the closing line and the result once the games settle.

## 13. NBA v10 overhaul (started 2026-11-20)

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
