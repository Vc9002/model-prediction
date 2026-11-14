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

## 4. NFL v8 challenger

`nfl-spread-structural-v8` (discrete-margin PMF) is an unqualified challenger. The replay over every
settled spread ledger game is in `scripts/replay_nfl_v8_settled.py`; its result is not recorded in
any doc. Run it, record Brier/ROI vs v7 with the 60/20/20 contract, and only then discuss promotion.

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
