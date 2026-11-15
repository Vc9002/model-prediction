# NCAAB v7 multi-season re-fit and evaluation (quick 261115-m5r)

SHADOW and INERT: nothing is registered, no artifact, no champion route, v6 untouched, no ledger write. Pin `ncaab-v7-multiseason-pin-2` sha256 `76f8a71a7e511b0c44cb4219c4b3ff5f1114eadbc51a0f77fe28329e3b6f3fa4` was committed before any outcome was joined to the multi-season components; the windows below follow it. Pin 1 (commit b4a61cf0) was amended once, to extend the ridge grid, after its fit-window selection landed on the grid corner and before any 2024-25 outcome was read (see the pin file).

- **Fit** 2022-23 and 2023-24 (hyperparameters, coefficients, scales; leave-one-season-out selection). **Decision** 2024-25 only. **2025-26** shaped v7's design in 1m7 and **2026-27** (partial) was its holdout there: both are CONTAMINATED and reported line-free only; no line x outcome x model number is read on them, so the 1m8 locked windows stay unspent.
- 2024-25 was read for nine models per market in 1m8; the multiplicity in scorecard B counts that.
- Conference map: AS-OF-SEASON, from the ESPN conference id on every scoreboard game. Realigned teams carry their own season's tier. The tier table is today's, so the old Pac-12 (id 21 through 2023-24) carries an explicit era override of 1660 (the table's 1550 describes the 2026 rebuilt league). Map risk is quantified by v6 as-of vs v6 as served.

## Selected configuration (fit window only)

Ridge variant `rg_c010_l02_k14` (carry^age, lambda, kappa from the key), combiner form `F1`, coefficients [0.9593, 0.1611], t-scales by games-played bucket [11.27, 10.87, 10.71, 9.97], non-D1 rule margin = 0.565 * v6 + 19.96 (scale 16.46), total mode `recal`.

| ridge variant | mean fit-season D1 RMSE |
|---|---|
| rg_c010_l02_k14 | 11.3618 |
| rg_c010_l03_k14 | 11.3652 |
| rg_c010_l02_k10 | 11.3765 |
| rg_c010_l01_k14 | 11.3802 |
| rg_c010_l03_k10 | 11.386 |
| rg_c010_l01_k10 | 11.3878 |

Combiner forms, leave-one-season-out RMSE: {'F1': 11.359400003653537, 'F2': 11.365046232176741, 'F3': 11.376346905783791}. Totals: v6 as-of 17.401 vs recalibrated 17.293.

## Scorecard A: forecast quality on UNSELECTED games, decision window 2024-25

D1-vs-D1 as-of games: 5764 (5763 with a closing line). Cells are RMSE / bias (actual minus predicted).

### Margin by games played (all D1 games)

| games played | v7 | v6_asof | v6_served | elo | ridge |
|---|---|---|---|---|---|
| all | 11.50 / +0.31 (n 5764) | 11.81 / +0.81 (n 5764) | 11.84 / +0.81 (n 5764) | 12.22 / +2.29 (n 5764) | 11.50 / +0.20 (n 5764) |
| 0 | 13.95 / +3.29 (n 147) | 16.09 / +7.20 (n 147) | 16.11 / +7.20 (n 147) | 16.05 / +7.09 (n 147) | 13.88 / +3.08 (n 147) |
| 1-2 | 12.22 / +0.33 (n 336) | 12.90 / +2.61 (n 336) | 13.00 / +2.52 (n 336) | 13.54 / +4.30 (n 336) | 12.23 / +0.15 (n 336) |
| 3-7 | 12.18 / +0.52 (n 898) | 12.54 / +1.59 (n 898) | 12.59 / +1.60 (n 898) | 13.30 / +3.06 (n 898) | 12.20 / +0.43 (n 898) |
| 8-15 | 11.45 / +0.35 (n 1318) | 11.84 / +1.04 (n 1318) | 11.83 / +1.05 (n 1318) | 12.49 / +2.77 (n 1318) | 11.44 / +0.25 (n 1318) |
| 16+ | 11.10 / +0.09 (n 3065) | 11.19 / -0.02 (n 3065) | 11.22 / -0.03 (n 3065) | 11.38 / +1.40 (n 3065) | 11.10 / -0.02 (n 3065) |

### Margin by |closing line| (lined D1 games; the market line is the extra column)

| |line| | v7 | v6_asof | v6_served | elo | ridge | market line |
|---|---|---|---|---|---|---|
| <5 | 11.23 / +0.07 (n 2355) | 11.16 / -0.34 (n 2355) | 11.21 / -0.33 (n 2355) | 11.27 / +0.89 (n 2355) | 11.25 / +0.04 (n 2355) | 11.01 / +0.19 (n 2355) |
| 5-10 | 11.54 / -0.03 (n 1787) | 11.67 / -0.04 (n 1787) | 11.70 / -0.05 (n 1787) | 11.88 / +1.31 (n 1787) | 11.54 / -0.17 (n 1787) | 11.30 / +0.01 (n 1787) |
| 10-15 | 11.02 / +0.35 (n 844) | 11.21 / +1.11 (n 844) | 11.21 / +1.10 (n 844) | 11.75 / +2.77 (n 844) | 11.00 / +0.07 (n 844) | 10.63 / -0.01 (n 844) |
| 15-20 | 11.51 / +0.85 (n 370) | 12.27 / +3.22 (n 370) | 12.29 / +3.23 (n 370) | 13.13 / +4.94 (n 370) | 11.45 / +0.56 (n 370) | 11.09 / +0.16 (n 370) |
| >=20 | 13.69 / +2.63 (n 407) | 16.11 / +8.39 (n 407) | 16.11 / +8.31 (n 407) | 17.85 / +11.23 (n 407) | 13.66 / +2.76 (n 407) | 12.83 / +0.73 (n 407) |

### Home-win probability, all D1 games

| model | n | Brier | n p>=0.8 | hit p>=0.8 | mean fav p |
|---|---|---|---|---|---|
| v7 | 5764 | 0.1778 | 1676 | 0.921 | 0.894 |
| v6_asof | 5764 | 0.1816 | 1155 | 0.939 | 0.871 |
| v6_served | 5764 | 0.1814 | 1139 | 0.938 | 0.871 |
| elo | 5764 | 0.1866 | 954 | 0.942 | 0.869 |
| ridge | 5764 | 0.1778 | 1789 | 0.916 | 0.895 |

### Spread cover probability, lined D1 games (favoured side)

| model | n | home-cover Brier | p 0.5-0.6 n/p/hit | 0.6-0.7 | 0.7-0.8 | >=0.8 |
|---|---|---|---|---|---|---|
| v7 | 5763 | 0.2585 | 4410 / 0.543 / 0.487 | 1188 / 0.636 / 0.49 | 146 / 0.733 / 0.527 | 19 / 0.83 / 0.632 |
| v6_asof | 5763 | 0.2619 | 3847 / 0.545 / 0.492 | 1468 / 0.639 / 0.514 | 366 / 0.737 / 0.486 | 82 / 0.833 / 0.5 |
| v6_served | 5763 | 0.2626 | 3843 / 0.546 / 0.493 | 1452 / 0.639 / 0.51 | 377 / 0.737 / 0.493 | 91 / 0.834 / 0.495 |
| elo | 5763 | 0.2714 | 2992 / 0.548 / 0.515 | 1781 / 0.642 / 0.478 | 650 / 0.741 / 0.515 | 340 / 0.862 / 0.538 |
| ridge | 5763 | 0.2587 | 4424 / 0.543 / 0.484 | 1151 / 0.636 / 0.492 | 167 / 0.735 / 0.539 | 21 / 0.839 / 0.524 |

### Totals by games played (RMSE / bias, all games)

| games played | v6_served | v6_asof | v7 |
|---|---|---|---|
| all | 16.90 / -0.35 (n 6292) | 16.90 / -0.34 (n 6292) | 16.82 / +0.42 (n 6292) |
| 0 | 17.63 / +5.37 (n 486) | 17.63 / +5.36 (n 486) | 16.89 / -0.42 (n 486) |
| 1-2 | 17.77 / +2.15 (n 489) | 17.78 / +2.17 (n 489) | 17.79 / +2.41 (n 489) |
| 3-7 | 17.03 / -3.12 (n 929) | 17.04 / -3.11 (n 929) | 16.85 / -0.65 (n 929) |
| 8-15 | 16.62 / -1.66 (n 1323) | 16.62 / -1.65 (n 1323) | 16.55 / -0.56 (n 1323) |
| 16+ | 16.72 / -0.25 (n 3065) | 16.72 / -0.24 (n 3065) | 16.76 / +0.99 (n 3065) |

### Paired deltas (90% day-cluster bootstrap)

| comparison | n | delta (negative favours the first) | 90% CI |
|---|---|---|---|
| v7_minus_v6_asof_d1_rmse | 5764 | -0.31 | [-0.43, -0.214] |
| v7_minus_v6_asof_d1_rmse_games_0_2 | 483 | -1.18 | [-1.635, -0.67] |
| v7_minus_v6_asof_d1_rmse_games_3plus | 5281 | -0.218 | [-0.287, -0.155] |
| v7_minus_ridge_d1_rmse | 5764 | 0.001 | [-0.007, 0.009] |
| v6_asof_minus_v6_served_d1_rmse_map_risk | 5764 | -0.027 | [-0.042, -0.013] |
| v7_minus_v6_asof_win_brier | 5764 | -0.0038 | [-0.0052, -0.0025] |
| v7_minus_v6_served_win_brier | 5764 | -0.0036 | [-0.0051, -0.0022] |

All games including non-D1 (v7 uses the fitted non-D1 rule; ridge is undefined there): {'n': 6292, 'margin': {'v7': {'n': 6292, 'rmse': 12.122, 'bias': 0.36}, 'v6_asof': {'n': 6292, 'rmse': 12.55, 'bias': 1.302}, 'v6_served': {'n': 6292, 'rmse': 12.628, 'bias': 1.376}, 'elo': {'n': 6292, 'rmse': 12.95, 'bias': 2.703}, 'ridge': {'n': 5764, 'rmse': 11.5, 'bias': 0.203}}, 'non_d1_only': {'v7': {'n': 528, 'rmse': 17.524, 'bias': 0.907}, 'v6_asof': {'n': 528, 'rmse': 18.814, 'bias': 6.65}, 'v6_served': {'n': 528, 'rmse': 19.252, 'bias': 7.594}, 'elo': {'n': 528, 'rmse': 19.172, 'bias': 7.238}, 'ridge': {'n': 0, 'rmse': None, 'bias': None}}}

**Pinned pass criteria:** {'i_rmse_upper_bound_lt_0': True, 'ii_win_brier_point_le_0': True, 'iii_rmse_0_2_point_lt_0': True} -> **PASSED**. Not gating: {'v7_p_ge_0.8_cover_hit_within_0.05_n_ge_50': False, 'abs_line_ge_20_bias_v7_lt_v6_asof': True}

## Scorecard B: market-relative (rule `ncaab-v7-market-rule-2`), decision window 2024-25

(model - closing line) on the residual test of `ncaab-residual-rule-1`: ridge alpha 1000 on the single standardised feature, 0.5 shrink, fit-window coefficients (v7 fit rows are leave-one-season-out), K-adjusted bootstrap by game date. Plain slope = OLS slope of (actual - line) on (model - line), 95% CI.

### spread (K = 12)

| model | n val | slope on shrunk pred, K-adj CI | MSE delta, K-adj CI | plain slope vs line, 95% CI | rule |
|---|---|---|---|---|---|
| v7 | 5763 | -0.659 [-2.118, +0.803] | -0.2263 [-0.5451, +0.0690] | -0.085 [-0.215, +0.052] | no |
| ridge | 5763 | -3.071 [-9.652, +3.622] | -0.0338 [-0.1045, +0.0329] | -0.087 [-0.218, +0.048] | no |
| v6_asof | 5763 | -0.443 [-2.268, +1.451] | -0.1484 [-0.5164, +0.1437] | -0.035 [-0.136, +0.071] | no |

Validation RMSE vs outcome on the lined rows: {'market_line': 11.188591630881456, 'v7': 11.501742034912581, 'v6_asof': 11.811660774929845, 'v6_served': 11.838572184854872, 'n': 5763}

- v7 side record (|pred| >= 1 point): {'bets': 52, 'wins': 29, 'losses': 23, 'pushes_excluded': 0, 'hit_rate_pct': 55.769230769230774, 'hit_rate_ci_95_pct': [42.42424242424242, 70.0], 'roi_pct_at_break_even_implied': 6.429829712272461, 'roi_ci_95_pct_at_break_even_implied': [-19.037705297247285, 33.58778625954197], 'roi_pct_at_executable_ask': 4.241552839683682, 'roi_ci_95_pct_at_executable_ask': [-20.702350608892672, 30.841121495327094]}

- ridge side record (|pred| >= 1 point): {'bets': 0, 'wins': 0, 'losses': 0, 'pushes_excluded': 0}

- v6_asof side record (|pred| >= 1 point): {'bets': 34, 'wins': 17, 'losses': 17, 'pushes_excluded': 0, 'hit_rate_pct': 50.0, 'hit_rate_ci_95_pct': [31.372549019607842, 80.95238095238095], 'roi_pct_at_break_even_implied': -4.580152671755733, 'roi_ci_95_pct_at_break_even_implied': [-40.1287232450232, 54.489276626681196], 'roi_pct_at_executable_ask': -6.542056074766355, 'roi_ci_95_pct_at_executable_ask': [-41.35972145867694, 51.312861593235425]}

### total (K = 11)

| model | n val | slope on shrunk pred, K-adj CI | MSE delta, K-adj CI | plain slope vs line, 95% CI | rule |
|---|---|---|---|---|---|
| v6_asof_total | 5762 | -0.250 [-3.403, +3.061] | -0.0194 [-0.2865, +0.2715] | -0.013 [-0.126, +0.100] | no |
| v7_total | 5762 | +0.596 [-2.383, +3.230] | +0.0643 [-0.2973, +0.4142] | +0.035 [-0.086, +0.144] | no |

Validation RMSE vs outcome on the lined rows: {'market_line': 16.23445509116051, 'v6_asof_total': 16.75961650363269, 'v7_total': 16.725369844457845, 'n': 5762}

- v6_asof_total side record (|pred| >= 1 point): {'bets': 1, 'wins': 1, 'losses': 0, 'pushes_excluded': 0, 'hit_rate_pct': 100.0, 'hit_rate_ci_95_pct': [100.0, 100.0], 'roi_pct_at_break_even_implied': 90.83969465648853, 'roi_ci_95_pct_at_break_even_implied': [90.83969465648853, 90.83969465648853], 'roi_pct_at_executable_ask': 86.91588785046729, 'roi_ci_95_pct_at_executable_ask': [86.91588785046729, 86.91588785046729]}

- v7_total side record (|pred| >= 1 point): {'bets': 5, 'wins': 3, 'losses': 2, 'pushes_excluded': 0, 'hit_rate_pct': 60.0, 'hit_rate_ci_95_pct': [0.0, 100.0], 'roi_pct_at_break_even_implied': 14.50381679389312, 'roi_ci_95_pct_at_break_even_implied': [-100.0, 90.83969465648853], 'roi_pct_at_executable_ask': 12.149532710280365, 'roi_ci_95_pct_at_executable_ask': [-100.0, 86.91588785046729]}

## Mismatch fix (item 4)

Cohort: both teams D1 as-of, non-neutral, home tier prior >= 1550 and away tier prior <= 1400 (power or upper mid-major host vs a low-major visitor).

### season year 2025 (n 479; lined 479)

| model | margin RMSE / bias |
|---|---|
| v7 | 12.92 / +0.68 (n 479) |
| v6_asof | 14.39 / +4.97 (n 479) |
| v6_served | 14.38 / +4.97 (n 479) |
| elo | 15.50 / +6.84 (n 479) |
| ridge | 12.93 / +0.64 (n 479) |

| model (lined) | n | home-cover Brier | p>=0.8 n/mean p/hit |
|---|---|---|---|
| v7 | 479 | 0.2595 | {'n': 7, 'mean_p': 0.83, 'hit': 0.429} |
| v6_asof | 479 | 0.2787 | {'n': 42, 'mean_p': 0.834, 'hit': 0.524} |
| v6_served | 479 | 0.2796 | {'n': 44, 'mean_p': 0.834, 'hit': 0.545} |
| elo | 479 | 0.3057 | {'n': 164, 'mean_p': 0.869, 'hit': 0.494} |
| ridge | 479 | 0.2608 | {'n': 7, 'mean_p': 0.84, 'hit': 0.286} |

Market line in cohort: 12.44 / -0.15 (n 479)

### season year 2026 (n 501)  CONTAMINATED, line-free

| model | margin RMSE / bias |
|---|---|
| v7 | 12.85 / +1.10 (n 501) |
| v6_asof | 14.73 / +5.97 (n 501) |
| v6_served | 14.72 / +5.99 (n 501) |
| elo | 15.96 / +8.00 (n 501) |
| ridge | 12.86 / +1.07 (n 501) |

### season year 2027 (n 198)  CONTAMINATED, line-free

| model | margin RMSE / bias |
|---|---|
| v7 | 13.16 / +3.02 (n 198) |
| v6_asof | 15.95 / +8.75 (n 198) |
| v6_served | 15.95 / +8.75 (n 198) |
| elo | 16.76 / +10.17 (n 198) |
| ridge | 13.11 / +2.83 (n 198) |

**Pinned verdict:** {'bias_and_rmse_smaller_than_v6_asof_2024_25': True, 'p_ge_0.8_cover_hit_not_below_v6_asof': False, 'fix_claimed': False}

## Settled-ledger checks (read-only runtime ledger)

Non-D1 no-pick rule (fc7ff8b8 predicate: either team absent from v6 team_priors) on settled Flat v6 rows: 634 rows ({'total': 241, 'spread': 224, 'moneyline': 169}; the plan said 610, the ledger holds this many now), declined 3 ({'moneyline': 3}), P&L of declined rows 0.138u of -11.675u; losing rows 288 of which declined 0; rows with model p >= 0.8: 59, losing 19, of which declined 0.

First 610 settled rows (the plan's snapshot): {'note': "the plan's 610 was the settled count at about 2026-11-15T05:34Z; rows settled since are excluded here", 'rows': 610, 'declined': 3, 'declined_markets': {'moneyline': 3}, 'losing_rows_declined': 0, 'p_ge_0.8_losing_rows': 19, 'p_ge_0.8_losing_rows_declined': 0}

Replay of the settled spread rows (contaminated, ledger lines): {'rows_joined': 357, 'rows_total_spread': 408, 'v6_served': {'recorded_side_brier': 0.291, 'n_p_ge_0.8': 50, 'hit_p_ge_0.8': 0.46, 'n_p_ge_0.7': 115, 'hit_p_ge_0.7': 0.478}, 'v6_asof': {'recorded_side_brier': 0.291, 'n_p_ge_0.8': 50, 'hit_p_ge_0.8': 0.46, 'n_p_ge_0.7': 115, 'hit_p_ge_0.7': 0.478}, 'v7': {'recorded_side_brier': 0.2776, 'n_p_ge_0.8': 15, 'hit_p_ge_0.8': 0.4, 'n_p_ge_0.7': 77, 'hit_p_ge_0.7': 0.455}, 'v6_served_p_ge_0.8_composition': {'n': 50, 'wins': 23, 'losses': 27, 'non_d1_either_side_as_of': 0, 'mismatch_cohort': 31, 'losses_non_d1': 0, 'losses_mismatch_cohort': 16}}

## Contaminated windows, line-free

### 2025-26 (contaminated: shaped v7 in 1m7): 6300 games, 5752 D1

| games played | v7 | v6_asof | v6_served | elo | ridge |
|---|---|---|---|---|---|
| all | 11.60 / -0.06 (n 5752) | 11.98 / +0.55 (n 5752) | 11.98 / +0.55 (n 5752) | 12.38 / +2.09 (n 5752) | 11.61 / -0.17 (n 5752) |
| 0 | 13.83 / +3.66 (n 149) | 16.13 / +7.66 (n 149) | 16.14 / +7.71 (n 149) | 15.92 / +7.54 (n 149) | 13.73 / +3.47 (n 149) |
| 1-2 | 12.56 / +0.40 (n 339) | 13.73 / +3.16 (n 339) | 13.67 / +3.18 (n 339) | 14.56 / +4.96 (n 339) | 12.55 / +0.20 (n 339) |
| 3-7 | 12.47 / +0.37 (n 885) | 12.89 / +1.70 (n 885) | 12.89 / +1.66 (n 885) | 13.78 / +3.29 (n 885) | 12.50 / +0.27 (n 885) |
| 8-15 | 11.48 / -0.34 (n 1331) | 11.93 / +0.67 (n 1331) | 11.92 / +0.68 (n 1331) | 12.52 / +2.51 (n 1331) | 11.49 / -0.44 (n 1331) |
| 16+ | 11.15 / -0.30 (n 3048) | 11.27 / -0.48 (n 3048) | 11.28 / -0.48 (n 3048) | 11.39 / +0.97 (n 3048) | 11.16 / -0.40 (n 3048) |

| model | n | Brier | n p>=0.8 | hit p>=0.8 | mean fav p |
|---|---|---|---|---|---|
| v7 | 5752 | 0.1792 | 1809 | 0.91 | 0.897 |
| v6_asof | 5752 | 0.1827 | 1255 | 0.942 | 0.872 |
| v6_served | 5752 | 0.1823 | 1261 | 0.933 | 0.872 |
| elo | 5752 | 0.1868 | 992 | 0.952 | 0.872 |
| ridge | 5752 | 0.1792 | 1892 | 0.906 | 0.899 |

v7 minus v6 as-of D1 RMSE {'n': 5752, 'delta_rmse': -0.384, 'ci90': [-0.511, -0.271]}; v7 minus v6 as served {'n': 5752, 'delta_rmse': -0.385, 'ci90': [-0.508, -0.275]}

### 2026-27 partial (contaminated: 1m7 holdout): 817 games, 603 D1

| games played | v7 | v6_asof | v6_served | elo | ridge |
|---|---|---|---|---|---|
| all | 12.70 / +1.16 (n 603) | 14.32 / +4.24 (n 603) | 14.32 / +4.24 (n 603) | 14.96 / +5.76 (n 603) | 12.67 / +0.97 (n 603) |
| 0 | 14.41 / +3.87 (n 149) | 17.22 / +8.06 (n 149) | 17.22 / +8.06 (n 149) | 17.18 / +8.48 (n 149) | 14.29 / +3.58 (n 149) |
| 1-2 | 12.32 / +0.91 (n 343) | 13.61 / +3.77 (n 343) | 13.61 / +3.77 (n 343) | 14.63 / +5.55 (n 343) | 12.31 / +0.73 (n 343) |
| 3-7 | 11.35 / -1.67 (n 111) | 11.99 / +0.58 (n 111) | 11.99 / +0.58 (n 111) | 12.54 / +2.73 (n 111) | 11.38 / -1.81 (n 111) |
| 8-15 | n/a | n/a | n/a | n/a | n/a |
| 16+ | n/a | n/a | n/a | n/a | n/a |

| model | n | Brier | n p>=0.8 | hit p>=0.8 | mean fav p |
|---|---|---|---|---|---|
| v7 | 603 | 0.1396 | 329 | 0.936 | 0.922 |
| v6_asof | 603 | 0.1442 | 214 | 0.972 | 0.873 |
| v6_served | 603 | 0.15 | 172 | 0.971 | 0.862 |
| elo | 603 | 0.1442 | 217 | 0.963 | 0.883 |
| ridge | 603 | 0.1404 | 356 | 0.924 | 0.927 |

v7 minus v6 as-of D1 RMSE {'n': 603, 'delta_rmse': -1.621, 'ci90': [-2.114, -1.017]}; v7 minus v6 as served {'n': 603, 'delta_rmse': -1.621, 'ci90': [-2.114, -1.017]}

## Fit-window reference (v7 leave-one-season-out)

- season year 2023: v7 11.33 / +0.22 (n 5721), v6_asof 11.54 / +0.65 (n 5721), v6_served 11.54 / +0.64 (n 5721), elo 11.82 / +2.05 (n 5721), ridge 11.34 / +0.12 (n 5721)
- season year 2024: v7 11.39 / -0.05 (n 5726), v6_asof 11.65 / +0.47 (n 5726), v6_served 11.65 / +0.47 (n 5726), elo 11.95 / +1.92 (n 5726), ridge 11.39 / -0.20 (n 5726)

