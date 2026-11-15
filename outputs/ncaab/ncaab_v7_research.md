# NCAAB v7 shadow challenger: research results (quick 261115-1m7)

SHADOW and INERT. Nothing here is registered in `config/production.yaml` or `config/model.yaml`,
there is no artifact in `config/models/`, no champion route changed, v6 is untouched.
Evidence files: `ncaab_v7_research.json` (everything), `ncaab_v7_parity.json`, `ncaab_v7_ideas.json`,
`ncaab_v7_isolated.json`, `ncaab_v7_dev_calibration.json`, `ncaab_v7_frozen_config.json`,
`ncaab_v7_final.json`, `ncaab_v7_components.jsonl` (the per-game walk-forward inputs).
Reproduce: `env PYTHONPATH=src:. python scripts/ncaab_v7_research.py components parity ideas isolated freeze final report`.

## Item 9 first: v6 retune train/serve parity

**Code and parameters match exactly; one input differs; the retune survives it.**

| check | result |
|---|---|
| ablation `shipped_nond1_hist_rank_8` kwargs vs `ncaab_spread_v6_model()` (artifact) | 0 parameter differences, artifact hash verifies |
| same inputs through both | max abs margin difference 0.0 over 1,069 games |
| **input that differs** | the ablation builds `UpcomingGame` without `neutral_site`; serving passes it (event flag or ESPN neutral pair) |
| neutral games in validation (ESPN `neutralSite`) | 273 of 1,043 (train 75, Nov-2025 277, holdout 119) |
| `TRNMNT` proxy vs real flag over 6,947 games | both 579, flag-only 165, proxy-only 101, neither 6,102 |

HCA grid on validation (n = 1,043), mean of actual minus predicted home margin (bias) and D1 RMSE.
"home-only" is how the ablation scored; "flag" is how serving behaves:

| HCA / Elo HCA | bias home-only | bias flag | D1 RMSE flag | cover Brier flag |
|---|---|---|---|---|
| 7.2 / 70 | -2.50 | -1.22 | 11.168 | 0.2118 |
| 6.0 / 55 | -1.62 | -0.57 | 11.114 | 0.2114 |
| **5.0 / 45 (shipped)** | -0.93 | **-0.06** | **11.109** | **0.2104** |

Treating conference-tournament games as home inflated the apparent over-prediction at every HCA (by 0.9 to 1.3 points), so the retune evidence was partly an input artefact, but 5.0 / 45 is still
the selected cell with the real flag, and also wins on D1 non-neutral games only (bias +0.22 vs -1.36
at 7.2/70). **No parity break to fix in v6.** Two further notes: Elo
history updates and `_effective_priors` apply home advantage to neutral history games (identical in
train and serve, so a v7 candidate rather than a break), and the v6 `sample`-based dynamic
volatility is not part of this check.

**What the post-retune positive live bias is.** Not HCA. Regressing actual minus v6 on v6 for D1
non-neutral games:

| window | n | intercept | slope of actual on v6 |
|---|---|---|---|
| Nov-2025 | 864 | -2.0 +- 0.8 | 1.62 +- 0.07 |
| Dec-Feb | 3,286 | -1.9 +- 0.2 | 1.43 +- 0.03 |
| Mar | 766 | -0.4 +- 0.5 | 1.17 +- 0.07 |
| holdout (2026-11 mostly) | 688 D1 | -0.9 +- 0.7 | 1.61 +- 0.07 |

A negative intercept with a slope well above 1 means the projections are compressed (every gap too
small), not that home court is too high. Daily D1 bias of v6 shrinks as samples grow (descriptive,
read before freezing: 11-09 +6.5, 11-10 +4.9, 11-11 +3.0, 11-12 +1.8, 11-13 +0.7, 11-14 -1.3). I did
not have the exact +4.3 / +1.9 / +0.2 live rows and did not reproduce those three figures.

## Protocol and honesty notes

- Split frozen (same graded-day rule as the v6 ablation): warm 2025-11-03..11-30 (1,402 games),
  train 2025-12-01..2026-02-19 (3,601), validation 2026-02-20..03-18 (1,043), holdout 2026-03-19..
  2026-11-14 (901; 85 are 2025-26, 816 are 2026-27). Walk-forward by ET day; real ESPN neutral flag;
  v6 served through its hash-verified artifact.
- Every candidate coefficient was fitted on warm+train and checked on validation. Validation is
  mature-season only (sample ~29), so it can only act as a "no degradation" guard for early-season
  ideas, as in ablation round 2. Carryover cannot be fitted anywhere but the holdout.
- The config was frozen to disk and committed (`b59b58e0`) before the holdout was read once.
  **The holdout was not pristine for v6**: the plan's evidence and an earlier read-only agent
  covered all games, and I printed v6 holdout bias/slope and daily bias while exploring. Nothing
  about v7 was selected on holdout numbers.
- The `ideas`, `isolated` and `freeze` stages never read holdout rows.

## Verdict table (every feature idea)

| # | idea | verdict | key numbers |
|---|---|---|---|
| 1 | mismatch rescale / heavier tail | supported for early season, not a mature-season gain | v6 slope 1.62 / 1.43 / 1.17 / 1.61 (Nov, Dec-Feb, Mar, holdout). Single fitted slope 1.35 worsens validation (11.192 vs 11.109). Sample-scheduled + tail ties v6 on validation (11.107), fit window 12.369 -> 11.983. Holdout slope v6 1.61, v7 1.07 |
| 2 | ridge (Massey) + conference-tier prior | **supported, core of v7** | ridge alone, no fitted parameter: warm+train D1 RMSE 12.026 vs v6 12.369; validation 11.090 vs 11.109 (tie); Nov-2025 13.38 vs 13.93; holdout 13.82 vs 14.20. Replacing the trend half via refit hurts validation (+0.141), so v7 uses the ridge as the strength model |
| 3 | last-season carryover 0.5-1 | weakly supported, holdout-only | v7 form D1 RMSE carry 0 / 0.5 / 1.0 = 13.425 / 13.283 / 13.355; bias -0.06 / +1.94 / +1.77. 0.5 declared ex ante; same direction an earlier agent saw (not blind) |
| 4 | non-D1 rule or exclusion | decline as policy supported; rule fixes bias only | v6 non-D1 RMSE 24.2 / 22.2 (bias +14.2 / +12.4). Rule 0.608*v6 + 23.5 (fit Nov) scores 17.9 on Dec-Feb; holdout 20.15 -> 18.97, bias +8.2 -> -3.0 |
| 5 | team / venue HCA, neutral handling | not supported | team HCA delta -0.03 [-0.06,+0.00]; listed-home edge on neutral games worse (+0.038); v6 neutral bias +1.0 / -0.7 / +0.7 |
| 6 | rest / schedule density | not supported (untestable) | rest coefficient 0.17+-0.75, 0.35+-1.31, 2.07+-1.07; b2b games 13-29 per window; fitted rest terms worsen validation (+0.115) |
| 7 | efficiency x pace totals | not supported out of sample | validation blend 16.42 vs 16.57 (-0.15) did not replicate: holdout 18.11 vs 18.13 (-0.02 [-0.29,+0.23]), bias +1.0 |
| 8 | home-vs-away favourite asymmetry | a v6 symptom, no term | v6 away-fav vs home-fav bias -2.9 vs +0.8, -0.5 vs +0.5, -1.8 vs +3.8, holdout -1.1 vs +5.1; ridge has none; v7 holdout +2.4 / -0.1 |
| 9 | retune parity | no break (see top) | 0 parameter diffs; neutral flag is the only input gap; 5.0/45 still selected |
| 10 | v7 vs v6 on locked holdout | see below | D1 RMSE 13.28 vs 14.20 |

## Item 10: locked holdout (n = 901, 688 D1)

| model | D1 RMSE | D1 bias | all-games RMSE | ML Brier | cover Brier | PIT 50/80/90 |
|---|---|---|---|---|---|---|
| v6 served | 14.195 | +4.14 | 15.805 | 0.1156 | 0.2279 | 0.509/0.799/0.912 |
| v6, scale relearned (control) | 14.195 | +4.14 | 15.805 | 0.1179 | 0.2275 | 0.479/0.786/0.901 |
| Elo only (same scale procedure) | 14.823 | +5.52 | 15.941 | 0.1219 | 0.2304 | 0.484/0.789/0.906 |
| ridge only (D1 games only meaningful) | 13.824 | -0.09 | n/a | 0.1574 | 0.2351 | n/a |
| **v7** | **13.283** | +1.94 | **14.825** | 0.1159 | **0.2266** | 0.482/0.800/0.906 |

- v7 vs v6, D1: -0.912 RMSE, 90% day-cluster bootstrap [-1.326, -0.596]; all games -0.980 [-1.535, -0.478].
- v7 vs ridge-only, D1: -0.541 [-1.170, +0.032]: the ridge is most of the gain; the v6 tail term and
  carryover add little that is separable.
- By current-season sample (D1): 0 games 16.92 -> 16.05, 1-2 games 13.71 -> 12.71, 3-7 games 12.05 -> 11.82,
  8+ games 13.42 -> 11.88. Bias remains positive early (sample 0: +6.2).
- Moneyline Brier is a tie (0.1159 vs 0.1156); in the 2026-27 early window v7 is slightly worse (0.1112 vs 0.1091).
  The gain is in the margin and in cover calibration, not in the win probability.
- Validation (non-degradation guard): D1 RMSE 11.093 vs 11.109, cover Brier 0.2104 vs 0.2104.
- Totals (frozen blend 0.55 v6 + 0.44 effpace, sd 17.65): holdout 18.11 vs 18.13, no gain; narrower
  than v6's sample-widened sd (PIT 0.498/0.790/0.890 vs 0.569/0.869/0.952). Not recommended.

### Replay on the settled v6 rows (`ncaab_v6_settled_replay.json`)

357 of the 408 spread rows join to a v7 forecast (same home, away, ET date); every one has a
current-season sample of 0-3 games.

| | v6 as replayed | v7 |
|---|---|---|
| recorded-side Brier | 0.2939 | 0.3117 |
| mean probability / hit rate | 0.639 / 0.482 | 0.618 / 0.482 |
| rows with p >= 0.8, hit rate | 60, 50.0% (mean p 0.853) | 54, 42.6% (mean p 0.876) |
| rows with p >= 0.7, hit rate | 121, 48.8% | 117, 43.6% |

**v7 does not fix the p >= 0.8 over-confidence on these rows; it is slightly worse.** The cause is not
a margin that is too small: for lines >= 25 (n = 76, mean line 31.5, mean result 32.6) v6 projected 17.4,
v7 23.6, so both remain compressed against large mismatches.

Market-relative comparison, thin by construction (the spread line is the logged decision line, home
margin convention; 1,157 of 1,167 asks are indicative slate prices, not executable quotes; the rows are
the picks the old models chose to log, so deviations from the line are selected): RMSE of the line 15.34,
v7 14.56, v6 15.49; correlation of (model - line) with (result - line) 0.458 (v7), 0.440 (v6); result minus
line averages +2.6 against v7 -2.6 and v6 -4.3. That the indicative line is a worse forecast than v7 on
season-opener games says more about the quote than about v7. **No claim of market edge.**
Totals rows (n = 395): line RMSE 17.15 vs v7 17.32.

## Frozen v7 configuration (fitted on warm+train)

D1 margin = 0.8853 * ridge(carry 0.5) + 0.9336 * tail(v6 margin beyond 12 points); non-D1 margin =
0.6079 * v6 + 23.531; Student-t (df 8) scale by current-season sample 0 / 1-2 / 3-7 / 8+ =
13.313 / 12.168 / 11.371 / 10.171, non-D1 16.817; total = 0.5511 * v6 + 0.4393 * effpace, sd 17.652.

## Caveats and recommended next step

- The only early-season windows are Nov-2025 (fit side) and Nov-2026 (holdout). Carryover, the non-D1
  rule and the sample-scheduled scales are each supported by one or two Novembers.
- No quoted closing lines exist for the dev windows, so every number is distributional. The evidence on
  market-relative skill is the thin replay block above.
- v7 still under-predicts early-season home margins (bias +1.9, +6.2 at sample 0).
- Recommended: do not promote. Cheapest useful production change that the evidence supports is a
  no-pick rule for games with a non-D1 team (and optionally < 3 current-season games), which removes the
  worst error cohort (RMSE 20+, 24% of holdout games) with no model change. Forward-test v7 in shadow on
  the next season opener, and use Tier 3's ESPN closing-line archive for the first real market-relative
  read before any promotion decision.
