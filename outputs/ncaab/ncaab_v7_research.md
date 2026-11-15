# NCAAB v7 shadow challenger: research results (quick 261115-1m7)

SHADOW and INERT. Nothing here is registered in `config/production.yaml` or `config/model.yaml`,
there is no artifact in `config/models/`, no champion route changed, v6 is untouched.
Evidence files: `ncaab_v7_research.json` (everything), `ncaab_v7_parity.json`, `ncaab_v7_serving_leg.json`,
`ncaab_v7_ideas.json`, `ncaab_v7_isolated.json`, `ncaab_v7_dev_calibration.json`,
`ncaab_v7_frozen_config.json`, `ncaab_v7_final.json` (primary, as frozen), `ncaab_v7_final_postfix.json`
(post-freeze rerun, labelled), `ncaab_v7_components{,_postfix}.jsonl` (per-game walk-forward inputs).
Reproduce: `env PYTHONPATH=src:. python scripts/ncaab_v7_research.py components parity serving ideas isolated freeze final postfix report`.

## Item 9 first: v6 retune train/serve parity

**Code, parameters and serving all match. One harness input differs; the retune survives it.**

| check | result |
|---|---|
| ablation `shipped_nond1_hist_rank_8` kwargs vs `ncaab_spread_v6_model()` (artifact) | 0 parameter differences, artifact hash verifies |
| same inputs through both | max abs margin difference 0.0 over 1,069 games |
| **serving leg**: ablation reproduction gate | 132 live Flat rows reproduced, 0 deltas (see `ncaab_v6_review_ablation.md`) |
| **serving leg**: runtime-ledger spread-v6 Flat rows vs this walk-forward's v6 margin | mean abs difference 0.009 / 0.000 / 0.000 on 2026-11-12 / 13 / 14 (23 / 46 / 53 games). 11-08..11-11 differ (1.2 to 3.5) because the artifact changed then |
| **input that differs in the harness** | the ablation builds `UpcomingGame` without `neutral_site`; serving passes it (event flag or ESPN neutral pair) |
| neutral games in validation (ESPN `neutralSite`) | 273 of 1,043 (train 75, Nov-2025 277, holdout 119) |
| `TRNMNT` proxy vs real flag over 6,947 games | both 579, flag-only 165, proxy-only 101, neither 6,102 |

HCA grid on validation (n = 1,043), mean of actual minus predicted home margin (bias) and D1 RMSE.
"home-only" is how the ablation scored; "flag" is how serving behaves:

| HCA / Elo HCA | bias home-only | bias flag | D1 RMSE flag | cover Brier flag |
|---|---|---|---|---|
| 7.2 / 70 | -2.50 | -1.22 | 11.168 | 0.2118 |
| 6.0 / 55 | -1.62 | -0.57 | 11.114 | 0.2114 |
| **5.0 / 45 (shipped)** | -0.93 | **-0.06** | **11.109** | **0.2104** |

Treating conference-tournament games as home inflated the apparent over-prediction at every HCA (by
0.9 to 1.3 points), so the retune evidence was partly an input artefact, but 5.0 / 45 is still the
selected cell with the real flag, and also wins on D1 non-neutral games only (bias +0.22 vs -1.36 at
7.2/70). **No parity break to fix in v6.** Two further notes: Elo history updates and
`_effective_priors` apply home advantage to neutral history games (identical in train and serve, so a
v7 candidate rather than a break), and the dynamic-volatility `sample` logic is not part of this check.

**What the post-retune positive live bias is.** Not HCA. Regressing actual minus v6 on v6 for D1
non-neutral games (holdout row: all 688 D1 games):

| window | n | intercept | slope of actual on v6 |
|---|---|---|---|
| Nov-2025 | 864 | -2.0 +- 0.8 | 1.62 +- 0.07 |
| Dec-Feb | 3,286 | -1.9 +- 0.2 | 1.43 +- 0.03 |
| Mar | 766 | -0.4 +- 0.5 | 1.17 +- 0.07 |
| holdout (2026-11 mostly) | 688 | -0.9 +- 0.7 | 1.61 +- 0.07 |

A negative intercept with a slope well above 1 means the projections are compressed (every gap too
small), not that home court is too high. Live Flat bias (actual minus the live `expected_margin`) by
day: 11-08 +0.3, 11-09 +5.4, 11-10 -0.7, 11-11 +5.1, 11-12 +1.3, 11-13 +1.4, 11-14 -1.0. These do not
match the quoted +4.3 / +1.9 / +0.2 (a different cut that I did not have), so those three figures were
not reproduced; the direction (large early, shrinking as team samples grow) is the same.

## Protocol, deviations and honesty notes

- Split frozen (same graded-day rule as the v6 ablation): warm 2025-11-03..11-30 (1,402 games),
  train 2025-12-01..2026-02-19 (3,601), validation 2026-02-20..03-18 (1,043), holdout 2026-03-19..
  2026-11-14 (901: 85 games from 15 dates of 2025-26, 816 from 2026-27). **Deviation:** the plan's
  holdout is "2026-27 so far + the last chronological 20% of 2025-26"; the ablation boundary I reused
  gives a 2025-26 tail of 15 of 119 graded dates (12.6%, not 20%) because its 60/20/20 fractions were
  computed over all graded dates including 2026-27. The holdout had already been read, so I disclosed
  rather than re-split.
- **Deviation:** `validation.py::build_walk_forward_rows` / `chronological_split` were not used. They
  operate on `ValidationRow` objects carrying the learned-model feature vector; the ablation's
  `load_games` / `graded_days` / `summarise_spread` / `summarise_total` are the loader and scorers for
  this NCAAB model and are reused instead.
- **Deviation:** candidate coefficients were fitted on warm+train and validation was used as a
  non-degradation check (as in v6 ablation round 2), not "fit on train only": warm (Nov-2025) is the only
  early-season data before the holdout, and validation is mature-season only (sample ~29).
- Carryover cannot be fitted anywhere but the holdout (no prior season in dev); 0.5 was declared ex ante.
- The config was frozen and committed (`b59b58e0`) before the holdout was read. **The holdout was not
  pristine for v6**: the plan's evidence and an earlier read-only agent covered all games, and I printed
  v6 holdout bias/slope and daily bias while exploring. Nothing about v7 was selected on holdout numbers.
- **A bug found after the single look.** The carryover fallback counted only current-season games, which
  switched carryover off on 2026-11-01/02 (121 of the 150 sample-0 D1 holdout games). Fixed in the module;
  dev rows and the frozen coefficients were verified unchanged by a rebuild. The as-frozen run stays the
  primary result; the rerun is labelled post-freeze. The replay block was also corrected after review:
  `line` is the selected side's own spread (outcome consistency is now 100%), and the replay's
  `p_fixed` comes from an older artifact, so the like-for-like v6 column is recomputed.
- The `ideas`, `isolated` and `freeze` stages never read holdout rows.

## Verdict table (every feature idea)

| # | idea | verdict | key numbers |
|---|---|---|---|
| 1 | mismatch rescale / heavier tail | supported for early season, not a mature-season gain | v6 slope 1.62 / 1.43 / 1.17 / 1.61 (Nov, Dec-Feb, Mar, holdout). Single fitted slope 1.35 worsens validation (11.192 vs 11.109). Sample-scheduled + tail ties v6 on validation (11.107), fit window 12.369 -> 11.983. Holdout slope v6 1.61, v7 1.07 (1.04 post-fix) |
| 2 | ridge (Massey) + conference-tier prior | **supported, core of v7** | ridge alone, no fitted parameter: warm+train D1 RMSE 12.026 vs v6 12.369; validation 11.090 vs 11.109 (tie); Nov-2025 13.38 vs 13.93; holdout 13.82 vs 14.20. Replacing the trend half via refit hurts validation (+0.141), so v7 uses the ridge as the strength model |
| 3 | last-season carryover 0.5-1 | supported on holdout only; 0.5 declared ex ante | frozen run (carryover off on opening day): D1 RMSE 13.425 / 13.283 / 13.355 for carry 0 / 0.5 / 1.0. Post-fix: 13.425 / **12.988** / 13.023, bias -0.06 / +1.46 / +1.18; raw ridge 13.824 / 13.035 / 13.032. 0.5 vs 1.0 not separable |
| 4 | non-D1 rule or exclusion | decline as policy supported; rule fixes bias only | v6 non-D1 RMSE 24.2 / 22.2 (bias +14.2 / +12.4). Rule 0.608*v6 + 23.5 (fit Nov) scores 17.9 on Dec-Feb; holdout 20.15 -> 18.97, bias +8.2 -> -3.0 |
| 5 | team / venue HCA, neutral handling | not supported | team HCA delta -0.03 [-0.06,+0.00]; listed-home edge on neutral games worse (+0.038); v6 neutral bias +1.0 / -0.7 / +0.7 |
| 6 | rest / schedule density | not supported (untestable) | rest coefficient 0.17+-0.75, 0.35+-1.31, 2.07+-1.07; home/away b2b games 14/29, 13/16, 6/7 per window; fitted rest terms worsen validation (+0.115) |
| 7 | efficiency x pace totals | not supported out of sample | validation blend 16.42 vs 16.57 (-0.15) did not replicate: holdout 18.11 vs 18.13 (-0.02 [-0.29,+0.23]), bias +1.0 |
| 8 | home-vs-away favourite asymmetry | a v6 symptom, no term | v6 away-fav vs home-fav bias -2.9 vs +0.8, -0.5 vs +0.5, -1.8 vs +3.8, holdout -1.1 vs +5.1; ridge has none; v7 holdout +2.4 / -0.1 |
| 9 | retune parity | no break (see top) | 0 parameter diffs; serving margin identical to 0.01; neutral flag the only harness input gap; 5.0/45 still selected |
| 10 | v7 vs v6 on locked holdout | D1 margin clearly better; calibration a tie | D1 RMSE 13.28 as frozen, 12.99 post-fix, v6 14.20 |

## Item 10: locked holdout (n = 901, 688 D1)

As frozen (primary) and post-fix (rerun, labelled):

| model | D1 RMSE | D1 bias | all-games RMSE | ML Brier | cover Brier | PIT 50/80/90 |
|---|---|---|---|---|---|---|
| v6 served | 14.195 | +4.14 | 15.805 | 0.1156 | 0.2279 | 0.509/0.799/0.912 |
| v6, scale relearned (control) | 14.195 | +4.14 | 15.805 | 0.1179 | 0.2275 | 0.479/0.786/0.901 |
| Elo only (same scale procedure) | 14.823 | +5.52 | 15.941 | 0.1219 | 0.2304 | 0.484/0.789/0.906 |
| ridge only (carry 0; D1 columns only meaningful) | 13.824 | -0.09 | n/a | n/a | n/a | n/a |
| **v7, as frozen** | **13.283** | +1.94 | **14.825** | 0.1159 | **0.2266** | 0.482/0.800/0.906 |
| v7, post-fix (carryover on opening day) | 12.988 | +1.46 | 14.624 | 0.1141 | 0.2251 | 0.483/0.808/0.910 |

- v7 vs v6, D1: -0.912 RMSE, 90% day-cluster bootstrap [-1.326, -0.596] (post-fix -1.207 [-1.575, -0.786]); all games -0.980 [-1.535, -0.478].
- v7 vs ridge-only, D1: -0.541 [-1.170, +0.032] frozen, -0.836 [-1.470, -0.064] post-fix.
- By current-season sample (D1, frozen): 0 games 16.92 -> 16.05, 1-2 games 13.71 -> 12.71, 3-7 games 12.05 -> 11.82,
  8+ games 13.42 -> 11.88; post-fix sample 0 is 14.90. Bias remains positive early (sample 0: +6.2 frozen, +4.0 post-fix).
- Moneyline Brier is a tie as frozen (0.1159 vs 0.1156; in the 2026-27 early window 0.1112 vs 0.1091 slightly worse,
  post-fix 0.1092 vs 0.1091). The gain is in the margin and in cover calibration.
- Validation (non-degradation guard): D1 RMSE 11.093 vs 11.109, cover Brier 0.2104 vs 0.2104.
- Totals (frozen blend 0.55 v6 + 0.44 effpace, sd 17.65): holdout 18.11 vs 18.13, no gain; narrower than
  v6's sample-widened sd (PIT 0.498/0.790/0.890 vs 0.569/0.869/0.952). Not recommended.

### Replay on the settled v6 rows (`ncaab_v6_settled_replay.json`)

357 of the 408 spread rows join to a v7 forecast (same home, away, ET date; 1,016 of 1,167 rows across
markets join, 81 more only with home/away swapped, 70 unmatched); every spread row has a current-season
sample of 0-3 games. `line` is the selected side's own spread (market home margin is `line` for an away
pick, `-line` for a home pick); with that convention 357 of 357 outcomes are consistent with margin and
line. The replay's `p_fixed` comes from an older artifact (spread `7704c580`, current `7c6d6d1c`), so
the like-for-like v6 column is the current v6.

| | v6 as replayed (older artifact) | v6 current | v7 (frozen) |
|---|---|---|---|
| recorded-side Brier | 0.2939 | 0.2913 | 0.2971 |
| mean probability / hit rate | 0.639 / 0.482 | 0.626 / 0.482 | 0.607 / 0.482 |
| rows with p >= 0.8, hit rate | 60, 50.0% | 42, 42.9% | 34, 41.2% |
| rows with p >= 0.7, hit rate | 121, 48.8% | 112, 50.0% | 102, 46.1% |

**v7 does not fix the p >= 0.8 over-confidence on these rows and is slightly worse on Brier.** For lines
>= 25 (n = 81, mean market home margin 31.7, mean result 32.3) current v6 projected 16.5 and v7 22.5, so both
remain compressed against large mismatches.

Market-relative comparison, thin by construction (indicative slate prices, 1,157 of 1,167 asks are not
executable quotes; the rows are the picks the old models chose to log): RMSE of the market home margin
**12.06** vs v7 14.56 vs v6 15.49; correlation of (model - line) with (result - line) is 0.02 (v7) and
0.05 (v6). **The market line beats both models on these rows and the models add no incremental information
beyond it.** Totals rows (n = 395): line RMSE 17.15, v7 17.32, v6 17.04.

## Frozen v7 configuration (fitted on warm+train)

D1 margin = 0.8853 * ridge(carry 0.5) + 0.9336 * tail(v6 margin beyond 12 points); non-D1 margin =
0.6079 * v6 + 23.531; Student-t (df 8) scale by current-season sample 0 / 1-2 / 3-7 / 8+ =
13.313 / 12.168 / 11.371 / 10.171, non-D1 16.817; total = 0.5511 * v6 + 0.4393 * effpace, sd 17.652.

## Caveats and recommended next step

- The only early-season windows are Nov-2025 (fit side) and Nov-2026 (holdout). Carryover, the non-D1
  rule and the sample-scheduled scales are each supported by one or two Novembers.
- No quoted closing lines exist for the dev windows, so every number is distributional. The only
  market-relative evidence is the thin replay block above, and it says the market is better than both models.
- v7 still under-predicts early-season home margins (bias +1.5 to +1.9; sample 0 +4 to +6).
- Recommended: do not promote. The change the evidence supports most cheaply is a no-pick rule for games
  with a non-D1 team (and optionally < 3 current-season games), which removes the worst error cohort (RMSE
  20+, 24% of holdout games) with no model change. Forward-test v7 in shadow on the next season opener, and
  use Tier 3's ESPN closing-line archive for the first real market-relative read before any promotion decision.
