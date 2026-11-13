# NCAAB v6 review fixes — 2026-11-12

Closes the NCAAB findings of `docs/MODEL_REVIEW_2026-11-11.md` (#7, #9 NCAAB part, #10 NCAAB
part, #11, LOW push mass, cross-cutting test gaps #1 and #4) under operator directive
"focus on NCAAB, fix all bugs and issues, and fix features". Quick task `quick-261112-nc6`.

Both champions (`ncaab-spread-v6`, `ncaab-total-v6`) are fixed **in place under the same
version string**, following the NFL v7 precedent (`03faf30d` → `a2d9a43c`). Every fix is a
constructor parameter whose default reproduces the pre-fix model bit-for-bit; the artifacts
now declare the served values and the factories load them hash-verified. Rollback is
`git revert` of the commits listed in `docs/DEBUG.md` (2026-11-12 NCAAB entry).

## Evidence

`scripts/ncaab_v6_review_ablation.py` → `outputs/ncaab/ncaab_v6_review_ablation.{md,json}`.

- Point-in-time day-by-day walk-forward over `data/historical/ncaab_games_all.jsonl`
  (6,957 completed games, 2025-11-03 → 2026-11-12). Graded days from 2025-12-01, split
  60/20/20 by complete ET dates: train 2025-12-01 → 2026-02-19, validation 2026-02-20 →
  2026-03-18, holdout 2026-03-19 → 2026-11-11 (742 games, 657 of them the 2026-11
  early-season window). Nothing is fitted; "train" only reports residual SDs. Every
  parameter choice below was made on validation; the holdout is reported once.
- **Reproduction gate passed**: the control variant re-derives all 68 Flat rows the live
  daily wrote at 2026-11-12T05:40Z (same history digest, 0 feature deltas, 0 probability
  deltas) before any candidate number is read.
- The file has no market lines, so the evaluation is distributional (moneyline Brier /
  log loss, margin and total error, PIT coverage of the served distribution, cover/over
  Brier at pseudo-lines around the projection), **not market-relative**. CLV / executable
  ROI remain the forward check pinned in `docs/handoffs/HANDOFF.md` §1.

### Shipped vs control, validation (n = 1,043)

| metric | control | shipped |
|---|---|---|
| moneyline Brier | 0.2058 | **0.2020** |
| moneyline log loss | 0.5964 | **0.5877** |
| moneyline intercept (0 = calibrated) | −0.277 | **+0.041** |
| mean P(home) vs home win rate 62.3% | 67.9% | **61.0%** |
| margin MAE | 9.046 | **8.844** |
| margin bias (home over-predicted when negative) | −1.92 | **−0.87** |
| spread cover Brier (pseudo-lines) | 0.2133 | **0.2111** |
| margin PIT coverage 50/80/90 | 0.507/0.807/0.907 | 0.517/0.812/0.915 |
| total MAE | 14.302 | **13.784** |
| total PIT coverage 50/80/90 | 0.402/0.682/0.808 | **0.489/0.816/0.919** |
| over Brier (pseudo-lines) | 0.2364 | 0.2363 |

### Holdout (n = 742, reported once)

| metric | control | shipped |
|---|---|---|
| margin MAE | 17.76 | **16.52** |
| spread cover Brier | 0.2365 | **0.2346** |
| moneyline Brier | **0.1112** | 0.1217 |
| total MAE | 15.11 | **14.48** |
| total bias | +2.26 | **+0.63** |
| over Brier | 0.2355 | **0.2344** |

The holdout is dominated by the 2026-11 early-season window (home teams won 84.6%, mean
absolute margin error 17–19 for every variant, realised margin SD 22–24 against a served
scale of ~15). The coherent moneyline inherits that under-dispersion and is worse there than
the legacy logistic, which stayed sharp by accident. Main NCAAB does not trade that regime
(current-season sample gate ≥ 8, `37c9212d`); Flat does. Widening the thin-sample volatility
cannot be selected on validation (every validation game has a sample ≥ 8) and is left open.

## What changed and why

| # | finding | fix | validation effect |
|---|---|---|---|
| 7 | total: level shrunk to league mean, pace factor divided by conference baseline; `w_league` silently 0 | `conference_trend_priors=True` (TrendEngine per-team shrinkage targets), `pace_baseline="league"`, `league_total_weight` explicit (0.0 — 0.15 was worse) | MAE 14.30 → 13.78 |
| 9 | artifact said retention 0.2, served 0.35; factory ignored the artifact; unread policy keys | `from_artifact` + hash-verified factory; artifact lists every served parameter; `confidence_threshold`/`min_edge`/`min_sample_games` removed (policy lives in `config/model.yaml`); `max_edge`/`dynamic_sizing` now consumed by `compute_dynamic_units` | — (retention 0.35 kept: validation has no offseason gap, so 0.2 vs 0.35 is not selectable) |
| 10 | logistic s=9.5 moneyline vs Student-t margin | `coherent_moneyline=True`: P(home) = 1 − T((0.5 − μ)/scale) | ML Brier 0.2058 → 0.2020, intercept −0.28 → +0.04 |
| 10 | t(df=8) scale 10.5 has true SD 12.1 | **tested and rejected**: scale 10.5 already gives 0.51/0.81/0.91 coverage; matching the scale to the SD gives 0.44/0.74/0.87 | — |
| 11 | early-season compression of the trend half | `tiered_trend_priors=True` (offense/defense shrink toward the conference-tier strength) and `additive_matchup=True` (`offense + opponent defense − league mean` instead of their average, which halved every strength gap even with full samples) | margin MAE 9.046 → 8.973; holdout 17.76 → 16.02 for this pair alone |
| 11 | sample-scheduled Elo weight | implemented (`elo_weight_ramp_games`), **left off**: inert on validation; on the holdout it undoes part of the priors' gain | — |
| HCA | margin bias −1.9 on validation | grid 7.2 / 6.0 / 5.0 (+ Elo HCA 70 / 55 / 45): 5.0 & 45 selected (bias −0.87, best cover Brier, ML Brier within 0.0001 of 6.0) | see table |
| total SD | PIT 0.40/0.68/0.81 at `total_sd` 13.5 | grid 13.5 / 15.5 / 16.5 / 17.5: 16.5 selected (0.489/0.816/0.919) | — |
| LOW | integer-line push mass credited to one side | spread and total exclude the (line ± 0.5) band on integer lines; `push` reported | — |

## Tests (`tests/test_ncaab_v6_review_fixes.py`, `tests/test_trends.py`, `tests/test_ncaab_v6.py`)

- Coherent moneyline equals the margin-distribution mass; legacy logistic differs by > 2 pp.
- Matched Student-t scale has SD = `margin_sd` (documents the rejected option).
- Integer spread/total lines: `home + away + push = 1`, `push > 0`; half-point lines unchanged.
- Elo ramp: weight 1.0 at sample 0, exactly the control margin at sample ≥ 8.
- Tiered priors + additive matchup: Tier-1 host vs Tier-5 visitor projects the Elo-implied
  gap (HCA + (1680 − 1270)/28) instead of HCA only.
- #7 direction: legacy makes a Northeast pair faster than an SEC pair on identical scoring;
  fixed does not.
- Artifact contracts: every artifact key is read by `from_artifact` (recording dict, NFL
  `4dd3034e` pattern); hashes verify against `compute_artifact_hash`; a tampered artifact is
  refused; the object `college_basketball_forward` actually calls carries the artifact's
  parameters (review cross-cutting gap #1).
- TrendEngine: `offense_baselines`/`defense_baselines=None` is byte-identical; a given
  baseline is the shrinkage target.

Red-without-fix evidence: the artifact tests failed on the pre-fix artifacts
(`confidence_threshold`, `min_edge`, `min_sample_games` unread; `coherent_moneyline`
missing); the old `test_ncaab_total_v6_bayesian_credibility_shrinkage` assertion failed on the
integer-line push fix and was rewritten to assert symmetry + push.

## Settled-ledger replay (operator request, 2026-11-12)

`scripts/replay_ncaab_v6_all_settled.py` → `outputs/ncaab/ncaab_v6_settled_replay.{md,json}`.
Every settled NCAAB Flat row (1,167 rows, all model versions, events 2026-11-02 → 2026-11-12,
0 skipped) re-priced at its own line and recorded ask with history before the row's decision
date, by the fixed champions and by the pre-fix control, and compared with the logged
probability. Neutral flags from the archived ESPN scoreboards (144 rows unresolved → home
court). These dates sit inside the ablation's locked holdout, so nothing here was selected on.

Two facts frame every number: **every row has a current-season sample of 0–3 games** (577 at
zero), so none of them would pass today's Main gate (≥ 8), and 1,157 of 1,167 asks are indicative
slate prices, not executable quotes. Units are paper, 1u per row. "Forward rule" = the live
selection logic: the model must favour the recorded side (p ≥ 0.5) and clear the edge hurdle;
the raw "any edge" count is misleading here because 224 of the 305 moneyline rows are away dogs
at a 13.6% ask that the forward would never pick (it only takes the projected winner).

| forward rule, hurdle 3.5% | as logged | pre-fix control | fixed |
|---|---|---|---|
| moneyline (n=305) | 76 bets, 34 W, −1.27u | 71 bets, 30 W, −0.90u | **46 bets, 19 W, +1.60u** |
| spread (n=408) | 367 bets, 180 W, −11.66u | 329 bets, 158 W, −18.59u | **303 bets, 147 W, −13.91u** |
| spread, hurdle 8% | 315 bets, −16.85u | 282 bets, −22.23u | **252 bets, 127 W, −2.24u** |
| total (n=454) | 347 bets, 181 W, +7.48u | 142 bets, 60 W, −16.92u | **79 bets, 32 W, −9.36u** |

| Brier on the recorded side | as logged | control | fixed |
|---|---|---|---|
| moneyline | 0.1862 | **0.1529** | 0.1616 |
| spread | 0.3556 | 0.3008 | **0.2911** |
| total | 0.2502 | 0.2535 | **0.2512** |

Reading:

- **Spread and total**: the fixed model fires less often and loses less on every hurdle, and is
  the best-calibrated of the three (total ECE 0.019). It still loses on these contracts: mean
  spread probability 0.644 against a 50.0% hit rate. That is the season-opener regime (samples
  0–3) where the ablation holdout already showed every variant under-predicts favourites by
  ~12 points; the fixes narrowed it (holdout margin MAE 17.8 → 16.5), they did not close it.
- **Moneyline**: the fixed model's Brier is worse than the control's because the coherent
  moneyline gives early-season away dogs ~29% where the control gave ~21% and they won 12.5%.
  The forward never takes those sides, so under the live rule the fixed model made 46 calls
  (+1.60u) against the control's 71 (−0.90u); on the 81 home-side rows it is the best of the
  three (27 bets, 16 W, +10.2u). The dog over-pricing is the same under-dispersion/bias issue
  and is the first thing to re-test once a non-opener window exists.
- **On the v6-era rows only** (what the champion itself called 11-08 → 11-12): spread-v6 rows
  86 → 68 bets, +6.4u → +4.8u; total-v6 rows 91 → 40 bets, −13.7u → −6.1u; home moneylines
  30 → 11 bets, +3.9u → +1.6u. Fewer calls, smaller losses on totals, slightly fewer units
  on spreads.
- The tempo-v2 total rows (cutoff 2026-11-02, sample 0) price at exactly 0.5 under both v6
  variants: credibility shrinkage puts a zero-sample total on the line, so v6 never fires there.

Verdict: on everything the system has ever called, the fixed model is an improvement over the
model it replaces and over what was logged, but the settled cohort is entirely season-opener
games and the fixed model still would have lost paper units on spreads and totals there. It
does not validate Main NCAAB; it validates that the change moved in the right direction. The
forward check in HANDOFF §1 (quoted picks, sample ≥ 8) remains the test that matters.

## Feature round (operator request "improve the model, add more features", 2026-11-12)

### Where the model was weakest

Residuals of the round-1 model on the 658 November-2026 games (holdout), by matchup:

| subset | n | bias (actual − predicted home margin) | MAE |
|---|---|---|---|
| all | 658 | +12.4 | 17.3 |
| away team not Division 1 | 187 | **+29.3** | 30.0 |
| Tier-1/2 host vs Tier-4/5 or non-D1 visitor | 196 | +13.4 | 15.7 |
| Tier-1/2 vs Tier-1/2 | 55 | −1.1 | 10.3 |
| mid-major vs mid-major | 121 | −2.2 | 8.7 |

Two defects, not one: the non-D1 prior (1180 Elo, a 13-point gap) when D1 hosts actually win
by 42, and conference tiers that understate the top of the sport in buy games.

### Data that was already on disk

The daily ingest archives `data/raw/ncaab/<date>/scores_ncaab.json` (182 days, 6,958
completed games). Beyond scores it carries, per team and game: field-goal / three-point /
free-throw attempts and makes, rebounds, assists, the AP `curatedRank` carried into the game,
plus neutral-site and conference-game flags. Coverage 99.96% of the history file. No
offensive rebounds or turnovers, so possessions cannot be reconstructed; `FGA + 0.44·FTA`
is the tempo proxy (correlation 0.65 with the game total). New module
`features/ncaab_scoreboard_stats.py` serves these keyed by ESPN event id; callers pass the
history they are allowed to see, so a stats row is never newer than the history it rides on.

### Features (all constructor parameters, default off; artifacts turn them on)

| feature | model | what it does | selected on |
|---|---|---|---|
| `non_d1_prior_from_history` | spread | the non-D1 Elo prior is read off the venue-adjusted margins D1 teams posted against non-D1 opponents in the history (≥ 20 games, else the constant) — ~450 Elo at present vs the old 1180 | train window (Dec–Feb; nothing else is fitted there), validation must not degrade |
| `rank_prior_per_slot` = 8 | spread | a team's last AP rank in the history lifts its Elo prior by `8 × (26 − rank)`; feeds the Elo start/regression target and the tiered trend priors | same |
| `tempo_source="attempts"`, `pace_sensitivity` = 1.5 | total | pace factors from each team's shot-attempt tempo (EWM, shrunk toward the league mean) instead of points | validation (grid 0.55–2.5; 1.5 is the interior optimum on residual SD, coverage and over-Brier, within 0.04 MAE of 2.0) |

Early-season features cannot be selected on the Feb–Mar validation split because it contains
no season openers; the train window is the only non-holdout window that does. The Elo ramp and
retention remain off/unchanged for the same reason as round 1.

### Results (round-1 shipped → round-2 shipped)

| spread | train (n=3,601) | validation (n=1,043) | holdout (n=743) |
|---|---|---|---|
| moneyline Brier | 0.1844 → **0.1803** | 0.2020 → 0.2020 | 0.1219 → **0.1096** (control 0.1116) |
| margin MAE | 10.73 → **9.83** | 8.844 → 8.850 | 16.51 → **12.71** (control 17.76) |
| margin MAE, D1-only | 9.36 → **9.25** | — | 11.86 → **11.46** |
| cover Brier (pseudo-lines) | 0.2185 → **0.2170** | 0.2111 → 0.2110 | 0.2346 → **0.2281** |
| PIT coverage 80% | 0.766 → 0.789 | 0.812 → 0.810 | 0.681 → **0.791** |

| total | train | validation | holdout |
|---|---|---|---|
| MAE | 14.34 → **14.08** | 13.78 → **13.38** | 14.49 → **14.30** |
| residual SD | 17.92 → 17.67 | 16.93 → **16.56** | 18.44 → 18.23 |
| PIT 50/80/90 | 0.504/0.801/0.909 → 0.517/0.811/0.913 | 0.489/0.816/0.919 → **0.507/0.821/0.921** | 0.579/0.864/0.949 → 0.572/0.869/0.950 |
| over Brier | 0.2351 → 0.2347 | 0.2363 → **0.2345** | 0.2344 → **0.2335** |

The spread holdout moneyline Brier is now better than the pre-fix control's (0.1096 vs
0.1116) — the round-1 regression there was the under-priced buy games, and the two priors
address exactly those. Rank slots 12 and 16 improve train marginally but start to cost
validation, so 8 was kept. Non-D1 constants 1000/850/700 all lose to the history-derived
prior on train and holdout.

Deployment check: the live daily at 2026-11-12T19:05Z (after round 1 landed) wrote 68 Flat
rows; the deployed artifact model reproduces all 68 with zero feature or probability deltas
(harness gate, `matched_by: deployed_artifact`).

## Main totals restored (2026-11-13)

The pause (`93aa093c`, 2026-11-10) was a hard `kind != "total"` in the Main destination of
`college_basketball_forward.py`, added after the v6 totals lost 25.8u in Main on
season-opener samples (mean current-season sample 1.7 games, mean absolute total error 16).
Restored by operator directive, but **not unconditionally**: a total now reaches Main under
exactly the spread/moneyline conditions — matched fresh executable two-sided quote, edge ≥
`min_edge`, divergence gate, and the current-season sample gate (`min_team_games` = 8) that
the pause predated. The settled-ledger replay above shows why the gate matters: every one of
the 454 settled total rows had a sample of 0–3, and the fixed model still lost 9.4 paper
units on them under the live rule. None of those rows would reach Main today.

A new policy switch, `models.NCAAB.main_totals_enabled` in `config/model.yaml` (default true,
read via the policy dict so no yaml edit was needed), is the operator's pause lever from now
on; a disabled total is recorded as `main_totals_disabled_by_policy` in the run's skipped
list. Tests: `tests/test_ncaab_main_totals.py` (reaches Main with sample 9 and an executable
quote; blocked at sample 3; blocked by the switch).

What this does not claim: that Main totals are profitable. Validation evidence for the total
model is distributional (MAE 13.38, PIT 0.507/0.821/0.921); the first market-relative read
comes from the forward check once gated totals settle.

## Not changed / open

- Early-season dispersion (both markets) and the Elo ramp: need a second cross-season window
  or market lines to select on; see HANDOFF §1.
- `offseason_retention` 0.2 vs 0.35: same reason.
- The live calibrators for both models are identity (`champion_method: identity`,
  n = 50/90/110), so no fitted transform is stale after this change.
- Main NCAAB totals remain paused (`93aa093c`); nothing here restores them.
