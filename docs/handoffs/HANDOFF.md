# Handoff — open work (updated 2026-11-11)

**Status: OPEN.** Delete this file when every item below is done or explicitly dropped.
Items from the 2026-11-05 version that finished (project folder rename to `E:\MP`, NCAAF
duplicate dedup, NCAAF champion v3->v4, `cfb-total-v1` backtest correction, research-ledger
merge into Flat/Main) were removed; history is in git and `ROADMAP.md`.

## Next action

Decide what to do with branch `refactor/cleanup-2026-11-11` (head `3a776f29`): the pass is
finished but not merged, and 12 tests fail on it (the same 12 fail at the pre-refactor
checkpoint `0af19ecd`: 9 in `tests/test_cli.py`, 1 each in `test_five_blind_spot_guards.py`,
`test_forecast_runtime_regressions.py`, `test_tennis_markov.py`). Triage those 12 with
targeted runs (`-o addopts='' -q`, never the full suite), then merge or hold. The scheduled
tasks run the working tree, so do not edit source while a daily run is in progress.

## 1. NCAAB (open, most expensive loss source)

- **Evidence:** `docs/NCAAB_LOSS_AUDIT_2026-11-10.md`. v6 Main spread 20/40 (+0.95u), total 11/36
  (-25.78u); v3 away moneyline 13 of 151. Gates added: executable-quote requirement,
  current-season sample >= 8, totals paused in Main.
- **Forward check (pinned):** `ncaab-spread-v6`, spread, edge 5%-8%, picks created on or after
  2026-11-12, dedupe on (event, selection, line, model) preferring Main over Flat. Report record,
  ROI, mean model probability vs hit rate, verified-quote count. Baseline 11W-3L is post-hoc;
  call it real only after about 50+ forward picks at roughly 58%+. Do not size up or gate on it.
  Use `ledgers.db`, not the buyer ledger (buyer rows include manual early exits).
- **Falsifiable next check from the audit:** once fresh quoted games settle, compare serving
  probability vs outcome vs decision-time market probability per market and side (Brier, log loss,
  calibration, line error) before restoring Main totals or considering a v7. Do not fit on the
  evaluated dates.
- **Structural finding, still unfixed:** pure Elo underrates home teams across most probability
  buckets (worst at p_home 0.4-0.5, actual about 60%), consistent across ncaab/euroleague/acb/lnb/bbl.
  Needs a real `elo_weight` x `offseason_regression` ablation on multi-season history. The local
  NCAAB file covers one season, so `offseason_regression` cannot be tuned yet.
- **2026-11-12 review fixes landed (`quick-261112-nc6`)**: #7 total baseline mismatch, #9 artifact
  drift, #10 coherent moneyline, #11 early-season compression (tiered trend priors + additive
  matchup), integer-line push mass; HCA 7.2 -> 5.0 / Elo 55 -> 45 and `total_sd` 13.5 -> 16.5
  selected on validation. Evidence: `docs/NCAAB_V6_REVIEW_FIXES_2026-11-12.md`,
  `outputs/ncaab/ncaab_v6_review_ablation.md` (not market-relative). Both champions changed in
  place under the same version string -- the forward check above now measures the fixed model;
  rows created before 2026-11-12T07:00Z were priced by the old one.
- **Settled-ledger replay of the fixed model** (`scripts/replay_ncaab_v6_all_settled.py`,
  `outputs/ncaab/ncaab_v6_settled_replay.md`): all 1,167 settled Flat rows re-priced under the live
  forward rule -- fixed vs pre-fix control: ML 46 bets +1.60u vs 71 bets -0.90u; spread 303 bets
  -13.91u vs 329 bets -18.59u (8% hurdle: -2.24u vs -22.23u); total 79 bets -9.36u vs 142 bets
  -16.92u. Better on every market, still negative on spreads/totals; every row is a sample 0-3
  season-opener and 1,157 of 1,167 asks are indicative. Does not validate Main; the forward check
  above does.
- **Feature round landed (`quick-261112-nc6` round 2)**: history-derived non-D1 Elo prior and
  AP-rank prior (8 Elo per slot) on the spread model; attempts-based tempo (FGA + 0.44 FTA from
  the archived ESPN scoreboards, sensitivity 1.5) on the total model. Holdout margin MAE 16.5 ->
  12.7, holdout moneyline Brier 0.122 -> 0.110 (pre-fix control 0.112); validation total MAE
  13.78 -> 13.38. Early-season features were selected on the train window (validation has no
  openers) -- see `docs/NCAAB_V6_REVIEW_FIXES_2026-11-12.md`. New data module:
  `features/ncaab_scoreboard_stats.py`.
- **2026-11-13**: Main totals restored (`df37969f`) behind the sample gate (>= 8) with a new
  `models.NCAAB.main_totals_enabled` policy switch. Box scores backfilled for 2025-26 (6,483 games,
  `scripts/backfill_ncaab_boxscores.py`, 99.97% history coverage); exact possessions tested as the
  total tempo source and found indistinguishable from the shipped attempts proxy (not shipped).
  Natural next model step: points-per-possession efficiency as the spread trend half.
- **Open after the fixes (needs a second cross-season window or market lines to select on):**
  early-season under-dispersion (served margin scale ~15 vs realised 22-24 in 2026-11; total
  credibility widening x1.4 is now too wide on top of `total_sd` 16.5); `elo_weight_ramp_games`
  (implemented, off); `offseason_retention` 0.2 vs 0.35. Do not pick these on the 2026-11 games --
  that window is the locked holdout of the ablation.
- `aba`, `nbl`, `wncaab` still have no game-result dataset and stay on the generic Elo config.

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

Items 1 through 6 are each resolved or explicitly dropped, and this file has been deleted.

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
