# NCAAB market-anchored residual test on ESPN closing lines (quick-261115-1m8, Tier 3)

Pinned rule `ncaab-residual-rule-1` sha256 `58c373445cdaef65f96fab3a29f461de249d0bcab1824c0b876d53ab297f96dc` (canonical JSON of the rule block in scripts/ncaab_residual.py, computed before any outcome was joined). Script sha256 `5f2e1a9a21f57bb1382de7d95e748ef394b00ffeb6da0621f1884fcd244be950`. ESPN lines sha256 `3386b8d1f1f8d50fa59bbfb9af0f2c015afff3866ec1a668611147d839e8a4f4` (rule stamp ['e0f6811dc13c08a0cdcdb30656e46737607333a2de20b9715efe84043cf5bc35']), scoreboard sha256 `ee5394da02380ae9ca4780813ba9cfa851f450d87d94206031c7be81a5d8d1a7`.

> CAVEATS. (1) The line is a CLOSING benchmark with no observed_at: this tests whether anything we can compute before tip-off explains what the closing market missed, not whether a model could have beaten a decision-time quote. (2) Opening-to-closing movement is closing-based and cannot serve a live decision; it is reported but can never be an accepted pick feature. (3) POWER: the archive supports detecting roughly a 54% cover rate or better only (table below); a true edge of 53% would need about 43,000 bets to confirm. (4) Executable prices differ from the 52.4% break-even; ROI is shown at 52.4% and 53.5%. (5) PIT v6 projections for 2022-25 use the v6 served parameters on scoreboard-derived history; box-stat tempo and AP ranks come from the scoreboard feed as served at fetch time. (6) The served v6 parameters were selected on 2025-26 data, so the 'locked holdout' is NOT out-of-sample for v6 itself. (7) The line source differs by window: a multi-book median in 2022-24, ESPN BET in 2024-25, DraftKings in 2025-26 on, so a relationship can differ across windows because the benchmark changed. (8) The conference map used by the v6 models is today's, applied to 2022-25 games. No promotion decision follows from this.

## Verdict

- **spread**: NO PICK: no model passes both validation criteria; locked holdout and live check not scored
- **total**: NO PICK: no model passes both validation criteria; locked holdout and live check not scored

## Power

- true cover rate 53%: about 42,936 bets for 80% power against the 52.4% break-even (one-sided 5%)
- true cover rate 54%: about 6,038 bets for 80% power against the 52.4% break-even (one-sided 5%)
- true cover rate 55%: about 2,287 bets for 80% power against the 52.4% break-even (one-sided 5%)
- true cover rate 56%: about 1,193 bets for 80% power against the 52.4% break-even (one-sided 5%)

## Pipeline checks

- Local history overlap: {'local_events': 7116, 'scoreboard_events_2026_on': 7117, 'in_both': 7116, 'score_mismatches': 0, 'only_local': 0, 'only_scoreboard': 1}
- Exact projection reproduction (history restricted to 2025-26 on, non-neutral): {'days': ['2026-01-10', '2026-01-11', '2026-01-12', '2026-01-13', '2026-01-14', '2026-01-15'], 'history': 'scoreboard-derived, season year >= 2026 only', 'spread_mean': {'max_abs_diff': 0.0, 'mean_abs_diff': 0.0, 'n': 331}, 'total_mean': {'max_abs_diff': 0.0, 'mean_abs_diff': 0.0, 'n': 331}}
- Skipped rows: {'spread_no_line_or_projection': 2165, 'total_no_line_or_projection': 2106}; line records 25880 (23715 with a line)

Orientation check (outcome-using, kept outside the fetch script): favourite by closing line wins straight up

| Market cohort | Season year | n | Favourite won | Underdog won | Tie | Favourite win % |
|---|---|---|---|---|---|---|
| spread | 2023 | 5740 | 4193 | 1547 | 0 | 73.05 |
| spread | 2024 | 5785 | 4247 | 1538 | 0 | 73.41 |
| spread | 2025 | 5763 | 4238 | 1525 | 0 | 73.54 |
| spread | 2026 | 5743 | 4178 | 1565 | 0 | 72.75 |
| spread | 2027 | 603 | 470 | 133 | 0 | 77.94 |

Mean residual (actual - line) and RMSE vs the line by season

- spread 2023: n 5787, mean residual +0.458, RMSE 11.29
- spread 2024: n 5818, mean residual -0.046, RMSE 11.33
- spread 2025: n 5763, mean residual +0.141, RMSE 11.19
- spread 2026: n 5744, mean residual -0.184, RMSE 11.27
- spread 2027: n 603, mean residual -0.405, RMSE 11.40
- total 2023: n 5841, mean residual +1.278, RMSE 16.54
- total 2024: n 5819, mean residual +1.019, RMSE 16.56
- total 2025: n 5762, mean residual +0.656, RMSE 16.23
- total 2026: n 5749, mean residual +0.580, RMSE 16.89
- total 2027: n 603, mean residual -1.298, RMSE 16.45

## Rule

```json
{
  "id": "ncaab-residual-rule-1",
  "targets": {
    "spread": "home margin minus implied home margin (implied = -home_spread_line)",
    "total": "actual total minus total line"
  },
  "line": "ESPN pinned median of non-live providers (rule ncaab-espn-lines-rule-1), CLOSING benchmark",
  "windows_by_espn_season_year": {
    "fit": [
      2023,
      2024
    ],
    "validation": [
      2025
    ],
    "locked_holdout": [
      2026
    ],
    "final_live_check": [
      2027
    ],
    "note": "season year 2023 = 2022-23; 2022 (2021-22) is rating-history burn-in only"
  },
  "cohort": "completed lined events with a rebuilt PIT v6 projection and all of the model's features",
  "v6": "ncaab_spread_v6_model()/ncaab_total_v6_model() served parameters, history strictly before the game's ET calendar day, current neutral-site flag from the scoreboard, scoreboard box stats and AP ranks for tempo/rank priors",
  "feature_groups": {
    "v6": "x = v6 projection - line (always included)",
    "tempo": "mean of the two teams' last-10-game shot attempts (FGA + 0.44 FTA, game average) minus the running league mean of all prior games, PIT",
    "rest": "min(rest days home, 7) - min(rest days away, 7); no prior game = 7",
    "neutral": "neutral-site flag",
    "sample": "min(prior current-season games of the two teams, 15) and x * 1[that < 8]",
    "nond1": "either side lacks an ESPN conference id (non-Division-I opponent), and x * flag",
    "movement": "closing minus opening line (spread: implied home margin; total: total points). CLOSING-BASED: not usable at decision time, diagnostic only"
  },
  "model_family_per_market": [
    "m0_v6",
    "m_tempo",
    "m_rest",
    "m_neutral",
    "m_sample",
    "m_nond1",
    "m_joint (v6+tempo+rest+neutral+sample+nond1)"
  ],
  "movement_models": "m_move (v6+movement) and m_joint_move join the family only if rows with an opening line number at least 2000 in the fit window and at least 1000 in the validation window; otherwise movement is reported as a labelled exploratory diagnostic (a movement-only ridge on the rows carrying an opening line, chronological 60/20/20 by date, which spends part of the 2025-26 and 2026-27 windows for movement alone) that cannot affect acceptance. Either way a movement model is never an accepted pick model",
  "model": "ridge on standardised features (alpha 1000 fixed, standardisation and coefficients from the FIT window only) predicting actual - line; the prediction used everywhere is 0.5 x the ridge prediction (shrinkage); baseline = line only = fit-window mean residual",
  "bootstrap": {
    "resamples": 4000,
    "cluster": "game date",
    "seed": 17,
    "interval": "percentile; a model's lower bounds use alpha = 0.025 / K, K = models in the market's family (Bonferroni across the family); the unadjusted 95% bound is reported alongside"
  },
  "accept_if_all_on_VALIDATION_window": [
    "(a) K-adjusted bootstrap lower bound of the validation-window OLS slope of (actual - line) on the model's shrunk prediction is above zero",
    "(b) K-adjusted bootstrap lower bound of the out-of-sample MSE delta (baseline MSE - model MSE, fit-window coefficients applied to validation games) is above zero",
    "(c) the model uses no movement feature (a closing-based feature cannot serve a decision)"
  ],
  "if_not_accepted": "NO PICK; the locked holdout and the live check are NOT scored for that market",
  "if_accepted": "the accepted model with the largest validation MSE delta is scored once on 2025-26; only if it also passes (a) and (b) there with the plain 95% bounds is 2026-27 scored, once; side record follows the sign of the shrunk prediction when |prediction| >= 1 point, pushes excluded. A model whose v6 coefficient is negative is labelled fade-v6, not v6 skill",
  "price_assumptions_pct": {
    "break_even_implied": 52.4,
    "executable_ask": 53.5
  },
  "no_tuning": "no grids, no per-bucket cuts, no other features; alpha, shrinkage, threshold fixed above"
}
```

## Spreads

Window n {'fit': 11605, 'validation': 5763, 'holdout': 5744, 'live': 603}; rows with an opening line {'fit': 5818, 'validation': 5763, 'holdout': 5744, 'live': 603}; movement models in the family: True; family size K = 9 (adjusted alpha 0.00278 per tail).

| Model | n fit | slope on prediction (val) | adj CI | MSE baseline / model | MSE delta | adj CI | (a) | (b) | Accepted |
|---|---|---|---|---|---|---|---|---|---|
| m0_v6 | 11605 | -0.579 | [-2.286, +1.125] | 125.17 / 125.35 | -0.1809 | [-0.5582, +0.1083] | fail | fail | False |
| m_tempo | 11569 | -0.551 | [-2.310, +1.206] | 125.20 / 125.36 | -0.1637 | [-0.5053, +0.1123] | fail | fail | False |
| m_rest | 11605 | -0.611 | [-2.282, +1.059] | 125.17 / 125.36 | -0.1921 | [-0.5727, +0.0990] | fail | fail | False |
| m_neutral | 11605 | -0.544 | [-2.247, +1.150] | 125.17 / 125.34 | -0.1755 | [-0.5521, +0.1136] | fail | fail | False |
| m_sample | 11605 | -0.242 | [-1.378, +1.264] | 125.17 / 125.37 | -0.2010 | [-0.7131, +0.1884] | fail | fail | False |
| m_nond1 | 11605 | -1.738 | [-6.856, +3.375] | 125.17 / 125.21 | -0.0407 | [-0.1651, +0.0571] | fail | fail | False |
| m_joint | 11569 | -0.365 | [-2.396, +2.364] | 125.20 / 125.26 | -0.0618 | [-0.2588, +0.1216] | fail | fail | False |
| m_move | 5818 | -0.200 | [-2.129, +1.762] | 125.20 / 125.28 | -0.0811 | [-0.3483, +0.1414] | fail | fail | False |
| m_joint_move | 5808 | +0.482 | [-0.724, +1.718] | 125.22 / 125.23 | -0.0103 | [-0.2440, +0.2222] | fail | fail | False |

Standardised ridge coefficients (fit window, before the 0.5 shrink):

- m0_v6: {'x': 0.6147745851862338}
- m_tempo: {'x': 0.5635526946273881, 'tempo': 0.12847244295416516}
- m_rest: {'x': 0.6147199887194617, 'rest': -0.07665273931867213}
- m_neutral: {'x': 0.615486549583175, 'neutral': 0.02846313879418671}
- m_sample: {'x': 0.10611124378586634, 'n_min': -0.4242712308241329, 'x_early': 0.8206799727965189}
- m_nond1: {'x': 0.20500539279498453, 'nond1': 0.5187864343610544, 'x_nond1': 1.051530551796597}
- m_joint: {'x': 0.033537770645218554, 'tempo': 0.1006407245894853, 'rest': -0.04157324820925648, 'neutral': -0.018995582402845785, 'n_min': -0.27755533215513695, 'x_early': 0.36033900968181826, 'nond1': 0.35300798681284473, 'x_nond1': 0.8928601520341019}
- m_move: {'x': 0.3676982857078052, 'move': -0.1637623172806856}
- m_joint_move: {'x': -0.19532625239423082, 'tempo': 0.19603601305820328, 'rest': -0.074943974699508, 'neutral': -0.0024022691995579317, 'n_min': -0.39611389500020894, 'x_early': 0.3140135450129062, 'nond1': 0.1525609870800958, 'x_nond1': 0.798122514434753, 'move': -0.28022541010557656}

Descriptive side records on the VALIDATION window (|0.5 x ridge prediction| >= 1, pushes excluded; not an acceptance input):

- m0_v6: 19-19 (0 pushes), hit 50.0% CI [33.3%, 73.9%], ROI at 52.4% -4.6% [-36.4, +41.1], at 53.5% -6.5% [-37.7, +38.2]
- m_tempo: 12-18 (0 pushes), hit 40.0% CI [24.4%, 68.2%], ROI at 52.4% -23.7% [-53.5, +30.1], at 53.5% -25.2% [-54.4, +27.4]
- m_rest: 22-23 (0 pushes), hit 48.9% CI [35.1%, 67.6%], ROI at 52.4% -6.7% [-33.0, +29.1], at 53.5% -8.6% [-34.4, +26.4]
- m_neutral: 19-20 (0 pushes), hit 48.7% CI [34.0%, 72.4%], ROI at 52.4% -7.0% [-35.2, +38.2], at 53.5% -8.9% [-36.5, +35.4]
- m_sample: 127-112 (0 pushes), hit 53.1% CI [48.1%, 58.1%], ROI at 52.4% +1.4% [-8.2, +10.8], at 53.5% -0.7% [-10.1, +8.6]
- m_nond1: no bets (|prediction| never reached 1 point), 0 pushes
- m_joint: 2-4 (0 pushes), hit 33.3% CI [0.0%, 100.0%], ROI at 52.4% -36.4% [-100.0, +90.8], at 53.5% -37.7% [-100.0, +86.9]
- m_move: 6-11 (0 pushes), hit 35.3% CI [11.1%, 57.1%], ROI at 52.4% -32.6% [-78.8, +9.1], at 53.5% -34.0% [-79.2, +6.8]
- m_joint_move: 22-21 (0 pushes), hit 51.2% CI [35.7%, 63.3%], ROI at 52.4% -2.4% [-31.8, +20.7], at 53.5% -4.4% [-33.2, +18.3]

Descriptive (NCAAF-style): validation-window OLS slope of (actual - line) on the raw v6-minus-line gap -0.0466, date-clustered 95% CI [-0.1467, +0.0563], n 5763.

Descriptive: following the RAW v6-minus-line gap (|gap| >= 1) on the non-locked windows:

- fit: 4164-4180 (114 pushes), hit 49.9% CI [48.6%, 51.1%], ROI at 52.4% -4.8% [-7.2, -2.4], at 53.5% -6.7% [-9.1, -4.5]
- validation: 2082-2108 (0 pushes), hit 49.7% CI [48.2%, 51.1%], ROI at 52.4% -5.2% [-7.9, -2.5], at 53.5% -7.1% [-9.8, -4.5]

Models whose v6 coefficient is negative (a fade-v6 model, not v6 skill): ['m_joint_move'].

Locked windows: NOT SCORED (holdout 2025-26 and live check 2026-27 kept unspent).

## Totals

Window n {'fit': 11660, 'validation': 5762, 'holdout': 5749, 'live': 603}; rows with an opening line {'fit': 5819, 'validation': 5762, 'holdout': 5749, 'live': 603}; movement models in the family: True; family size K = 9 (adjusted alpha 0.00278 per tail).

| Model | n fit | slope on prediction (val) | adj CI | MSE baseline / model | MSE delta | adj CI | (a) | (b) | Accepted |
|---|---|---|---|---|---|---|---|---|---|
| m0_v6 | 11660 | -0.219 | [-3.488, +3.291] | 263.37 / 263.38 | -0.0115 | [-0.2568, +0.2551] | fail | fail | False |
| m_tempo | 11611 | -0.599 | [-2.995, +1.773] | 263.43 / 263.53 | -0.0993 | [-0.3729, +0.1813] | fail | fail | False |
| m_rest | 11660 | -0.035 | [-2.767, +2.954] | 263.37 / 263.38 | -0.0064 | [-0.2820, +0.2882] | fail | fail | False |
| m_neutral | 11660 | +0.152 | [-2.346, +2.441] | 263.37 / 263.38 | -0.0096 | [-0.4038, +0.3564] | fail | fail | False |
| m_sample | 11660 | -0.355 | [-3.350, +2.929] | 263.37 / 263.40 | -0.0256 | [-0.2705, +0.2415] | fail | fail | False |
| m_nond1 | 11660 | -0.234 | [-3.733, +3.522] | 263.37 / 263.34 | +0.0340 | [-0.2228, +0.2959] | fail | fail | False |
| m_joint | 11611 | +0.095 | [-2.116, +2.229] | 263.43 / 263.43 | +0.0030 | [-0.4321, +0.4377] | fail | fail | False |
| m_move | 5819 | -0.382 | [-3.048, +2.360] | 263.26 / 263.33 | -0.0725 | [-0.3722, +0.2342] | fail | fail | False |
| m_joint_move | 5808 | -0.522 | [-2.460, +1.246] | 263.31 / 263.49 | -0.1798 | [-0.6530, +0.2608] | fail | fail | False |

Standardised ridge coefficients (fit window, before the 0.5 shrink):

- m0_v6: {'x': 0.4378540202999901}
- m_tempo: {'x': 0.34889882005337575, 'tempo': 0.3551780207139721}
- m_rest: {'x': 0.43808911029469183, 'rest': -0.19603066551404796}
- m_neutral: {'x': 0.4591754705149033, 'neutral': -0.38366272346829483}
- m_sample: {'x': 0.49308367301497, 'n_min': -0.06405343476309495, 'x_early': -0.10848464770314208}
- m_nond1: {'x': 0.4091682771757965, 'nond1': 0.851707527805497, 'x_nond1': 0.5757978255567493}
- m_joint: {'x': 0.5028590864619272, 'tempo': 0.28936652729026013, 'rest': -0.06025819217273354, 'neutral': -0.32631151207543446, 'n_min': 0.11984731736595669, 'x_early': -0.10329659100724442, 'nond1': 0.8487921687907617, 'x_nond1': 0.1244444120591962}
- m_move: {'x': 0.44644773592715264, 'move': -0.09937610877393675}
- m_joint_move: {'x': 0.4955709414648388, 'tempo': 0.14794926197281716, 'rest': 0.01124700862061347, 'neutral': -0.2359932389635168, 'n_min': -0.24080735812000917, 'x_early': 0.11770477690198557, 'nond1': 0.7853286209286833, 'x_nond1': -0.28117831910943863, 'move': -0.09390241339016077}

Descriptive side records on the VALIDATION window (|0.5 x ridge prediction| >= 1, pushes excluded; not an acceptance input):

- m0_v6: 1-0 (0 pushes), hit 100.0% CI [100.0%, 100.0%], ROI at 52.4% +90.8% [+90.8, +90.8], at 53.5% +86.9% [+86.9, +86.9]
- m_tempo: 1-0 (0 pushes), hit 100.0% CI [100.0%, 100.0%], ROI at 52.4% +90.8% [+90.8, +90.8], at 53.5% +86.9% [+86.9, +86.9]
- m_rest: 1-1 (0 pushes), hit 50.0% CI [0.0%, 100.0%], ROI at 52.4% -4.6% [-100.0, +90.8], at 53.5% -6.5% [-100.0, +86.9]
- m_neutral: 8-9 (0 pushes), hit 47.1% CI [23.5%, 70.0%], ROI at 52.4% -10.2% [-55.1, +33.6], at 53.5% -12.0% [-56.0, +30.8]
- m_sample: no bets (|prediction| never reached 1 point), 0 pushes
- m_nond1: 1-0 (0 pushes), hit 100.0% CI [100.0%, 100.0%], ROI at 52.4% +90.8% [+90.8, +90.8], at 53.5% +86.9% [+86.9, +86.9]
- m_joint: 17-14 (0 pushes), hit 54.8% CI [38.3%, 70.6%], ROI at 52.4% +4.7% [-26.9, +34.7], at 53.5% +2.5% [-28.4, +31.9]
- m_move: 1-2 (0 pushes), hit 33.3% CI [0.0%, 100.0%], ROI at 52.4% -36.4% [-100.0, +90.8], at 53.5% -37.7% [-100.0, +86.9]
- m_joint_move: 29-39 (0 pushes), hit 42.6% CI [32.8%, 53.7%], ROI at 52.4% -18.6% [-37.4, +2.5], at 53.5% -20.3% [-38.7, +0.4]

Descriptive (NCAAF-style): validation-window OLS slope of (actual - line) on the raw v6-minus-line gap -0.0105, date-clustered 95% CI [-0.1219, +0.1017], n 5762.

Descriptive: following the RAW v6-minus-line gap (|gap| >= 1) on the non-locked windows:

- fit: 5003-4830 (60 pushes), hit 50.9% CI [49.8%, 51.9%], ROI at 52.4% -2.9% [-4.9, -0.9], at 53.5% -4.9% [-6.8, -3.0]
- validation: 2350-2379 (0 pushes), hit 49.7% CI [48.2%, 51.1%], ROI at 52.4% -5.2% [-8.0, -2.4], at 53.5% -7.1% [-9.9, -4.5]

Models whose v6 coefficient is negative (a fade-v6 model, not v6 skill): none.

Locked windows: NOT SCORED (holdout 2025-26 and live check 2026-27 kept unspent).

