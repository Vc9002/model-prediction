# cfb-structural-v5 margin-slope recheck

Verdict: **REFIT** (rule `cfb-v5-margin-slope-recheck-rule-1`, pinned in the script before the holdout was scored). Selected refit: `refit_fbs_mean`. Checks: {'market_not_worse': True, 'no_point_regression': True, 'robust_without_trigger_window': True, 'significant_improvement': True}.

As of 2026-11-21T17:57:11Z. Replay snapshot 2026-11-21T17:50:02Z, HEAD `b1e009a8`, 9511 FBS-involved completed games (row cache sha256 `68caa21f0fc0f4e3`).
Artifact: intercept -0.4143, slope 1.3189, SD table {'established': 17.499, 'fbs_vs_fcs': 19.731, 'thin_sample': 17.125}, conference scale 0.9641.

## 1. Replay sanity

- Current raw margin regressed on the 2026-11-12 (pre-fix) replay's raw margin, same events (n=7960): slope 0.9506, intercept 0.201; mean abs change 1.133 points. By season: 2016 0.958, 2017 0.961, 2018 0.966, 2019 0.947, 2020 0.988, 2021 0.944, 2022 0.940, 2023 0.943, 2024 0.940, 2025 0.946, 2026 0.935.
- Trigger window 2026-10-01..11-19, actual on raw: {'intercept': 0.1926, 'n': 427, 'resid_sd': 16.3853, 'slope': 1.2245, 'slope_se': 0.0945}; FBS-only {'intercept': 0.0526, 'n': 419, 'resid_sd': 16.2601, 'slope': 1.153, 'slope_se': 0.0997}.
- Fresh model on physically truncated history, 20 random games: {'max_abs_diff': 0.0, 'pass': True}.

## 2. Actual margin on raw projected margin (OLS)

| cohort | n | slope | se | intercept | resid SD |
|---|---|---|---|---|---|
| all | 9511 | 1.411 | 0.017 | -0.18 | 16.91 |
| FBS vs FBS | 8171 | 1.379 | 0.021 | -0.50 | 16.72 |
| FBS vs FCS | 1340 | 1.178 | 0.049 | 6.63 | 17.68 |
| season 2016 | 888 | 1.376 | 0.065 | 0.31 | 18.51 |
| season 2016 FBS-FBS | 752 | 1.381 | 0.085 | 0.13 | 18.49 |
| season 2017 | 888 | 1.403 | 0.055 | -0.65 | 17.10 |
| season 2017 FBS-FBS | 774 | 1.457 | 0.066 | -0.96 | 16.73 |
| season 2018 | 898 | 1.407 | 0.054 | -0.11 | 17.34 |
| season 2018 FBS-FBS | 767 | 1.341 | 0.066 | -0.42 | 17.21 |
| season 2019 | 899 | 1.470 | 0.052 | 0.25 | 16.73 |
| season 2019 FBS-FBS | 774 | 1.515 | 0.064 | 0.02 | 16.64 |
| season 2020 | 568 | 1.349 | 0.070 | -1.67 | 16.71 |
| season 2020 FBS-FBS | 534 | 1.347 | 0.079 | -1.70 | 16.76 |
| season 2021 | 903 | 1.370 | 0.052 | -1.06 | 16.89 |
| season 2021 FBS-FBS | 770 | 1.391 | 0.066 | -1.24 | 16.61 |
| season 2022 | 906 | 1.365 | 0.054 | -0.65 | 16.79 |
| season 2022 FBS-FBS | 775 | 1.315 | 0.067 | -1.21 | 16.31 |
| season 2023 | 927 | 1.359 | 0.052 | -0.26 | 16.39 |
| season 2023 FBS-FBS | 784 | 1.334 | 0.066 | -0.63 | 16.44 |
| season 2024 | 928 | 1.401 | 0.051 | -0.07 | 16.26 |
| season 2024 FBS-FBS | 795 | 1.325 | 0.064 | -0.28 | 16.18 |
| season 2025 | 941 | 1.456 | 0.051 | 0.76 | 16.19 |
| season 2025 FBS-FBS | 807 | 1.411 | 0.066 | 0.42 | 16.03 |
| season 2026 | 765 | 1.524 | 0.058 | 0.81 | 16.86 |
| season 2026 FBS-FBS | 639 | 1.317 | 0.075 | 0.26 | 16.36 |
| postseason | 406 | 1.151 | 0.171 | 1.38 | 17.26 |
| postseason FBS-FBS | 389 | 1.063 | 0.177 | 1.06 | 17.42 |
| weeks_0_4 | 3154 | 1.445 | 0.027 | 0.26 | 17.50 |
| weeks_0_4 FBS-FBS | 2064 | 1.525 | 0.040 | -0.60 | 17.08 |
| weeks_5_14 | 5951 | 1.356 | 0.024 | -0.51 | 16.54 |
| weeks_5_14 FBS-FBS | 5718 | 1.321 | 0.025 | -0.67 | 16.49 |
| FBS-FBS sample_games ge_6_established | 4268 | 1.318 | 0.029 | -0.47 | 16.58 |
| FBS-FBS sample_games lt_6_thin | 3903 | 1.436 | 0.030 | -0.60 | 16.85 |

## 3. Split, reproduction, refit, validation

Split: {'locked_holdout': {'end': '2026-11-21', 'observations': 1949, 'start': '2024-11-15'}, 'method': 'explicit_date_boundaries', 'train': {'end': '2022-11-11', 'observations': 5704, 'start': '2016-08-27'}, 'validation': {'end': '2024-11-14', 'observations': 1858, 'start': '2022-11-12'}}.

Artifact reproduction: {'fit': {'conference_margin_scale': 0.9641, 'margin_calibration': {'intercept': -0.4143, 'slope': 1.3189}, 'margin_sd_table': {'established': 17.499, 'fbs_vs_fcs': 19.731, 'thin_sample': 17.125}}, 'n': 4842, 'on': '2026-11-12 base rows (pre-fix replay), artifact train window', 'pass': True}.

- `refit_fbs_mean`: {'conference_margin_scale': 0.9978, 'margin_calibration': {'intercept': -0.6596, 'slope': 1.3993}, 'margin_sd_table': {'established': 16.919, 'fbs_vs_fcs': 18.238, 'thin_sample': 17.073}}
- `refit_ml_logloss`: {'conference_margin_scale': 0.9979, 'margin_calibration': {'intercept': 0.0974, 'slope': 1.9}, 'margin_sd_table': {'established': 21.992, 'fbs_vs_fcs': 23.709, 'thin_sample': 22.196}}
- `refit_orig`: {'conference_margin_scale': 0.9979, 'margin_calibration': {'intercept': -0.4026, 'slope': 1.3948}, 'margin_sd_table': {'established': 16.917, 'fbs_vs_fcs': 18.238, 'thin_sample': 17.074}}

Train slopes {'all': {'intercept': -0.4026, 'n': 5704, 'resid_sd': 17.1912, 'slope': 1.3948, 'slope_se': 0.0218}, 'fbs_vs_fbs': {'intercept': -0.6596, 'n': 4912, 'resid_sd': 17.0038, 'slope': 1.3993, 'slope_se': 0.0269}}; validation slopes {'all': {'intercept': -0.5265, 'n': 1858, 'resid_sd': 16.5434, 'slope': 1.3951, 'slope_se': 0.037}, 'fbs_vs_fbs': {'intercept': -0.9054, 'n': 1581, 'resid_sd': 16.4576, 'slope': 1.3401, 'slope_se': 0.0463}}.

| validation (Gaussian stand-in) | n | MAE | margin log score | Brier | log loss | z SD |
|---|---|---|---|---|---|---|
| artifact | 1858 | 13.148 | -4.2322 | 0.17016 | 0.50772 | 0.943 |
| refit_fbs_mean | 1858 | 13.153 | -4.2288 | 0.16951 | 0.50407 | 0.958 |
| refit_ml_logloss | 1858 | 14.230 | -4.3399 | 0.16962 | 0.50344 | 0.769 |
| refit_orig | 1858 | 13.156 | -4.2288 | 0.16952 | 0.50402 | 0.958 |

Selected on validation: `refit_fbs_mean`.

## 4. Locked holdout (negative_binomial_200k)

Holdout slopes: {'all': {'intercept': 0.7948, 'n': 1949, 'resid_sd': 16.3564, 'slope': 1.4735, 'slope_se': 0.036}, 'fbs_vs_fbs': {'intercept': 0.3726, 'n': 1678, 'resid_sd': 16.0815, 'slope': 1.3534, 'slope_se': 0.0456}}.

| subset | arm | n | MAE | log score | Brier | log loss | ECE | d Brier [CI] | d log loss [CI] | d log score [CI] | d MAE [CI] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all | artifact | 1949 | 13.167 | -4.2282 | 0.17197 | 0.51203 | 0.0274 |  | | |  |
| all | refit_fbs_mean | 1949 | 13.105 | -4.2234 | 0.17173 | 0.51026 | 0.0250 | -0.00024 [-0.0006, +0.0002] | -0.00177 [-0.0030, -0.0004] | +0.00484 [+0.0019, +0.0078] | -0.06236 [-0.1113, -0.0139] |
| all | refit_ml_logloss | 1949 | 13.591 | -4.3188 | 0.17164 | 0.50805 | 0.0316 | -0.00033 [-0.0014, +0.0008] | -0.00398 [-0.0082, +0.0005] | -0.09060 [-0.1073, -0.0728] | +0.42314 [+0.1435, +0.7026] |
| all | refit_orig | 1949 | 13.087 | -4.2222 | 0.17157 | 0.50980 | 0.0270 | -0.00040 [-0.0007, -0.0001] | -0.00223 [-0.0035, -0.0009] | +0.00599 [+0.0029, +0.0091] | -0.08017 [-0.1329, -0.0302] |
| before_2026_10_01 | artifact | 1515 | 13.181 | -4.2306 | 0.16018 | 0.48231 | 0.0337 |  | | |  |
| before_2026_10_01 | refit_fbs_mean | 1515 | 13.087 | -4.2236 | 0.15964 | 0.47925 | 0.0341 | -0.00053 [-0.0009, -0.0002] | -0.00306 [-0.0042, -0.0017] | +0.00697 [+0.0036, +0.0102] | -0.09350 [-0.1514, -0.0306] |
| before_2026_10_01 | refit_ml_logloss | 1515 | 13.460 | -4.3162 | 0.15886 | 0.47403 | 0.0253 | -0.00132 [-0.0024, -0.0002] | -0.00827 [-0.0123, -0.0036] | -0.08558 [-0.1048, -0.0661] | +0.27916 [-0.0494, +0.6174] |
| before_2026_10_01 | refit_orig | 1515 | 13.068 | -4.2222 | 0.15945 | 0.47868 | 0.0310 | -0.00073 [-0.0011, -0.0004] | -0.00363 [-0.0048, -0.0023] | +0.00836 [+0.0047, +0.0119] | -0.11315 [-0.1748, -0.0463] |
| from_2026_10_01 | artifact | 434 | 13.120 | -4.2198 | 0.21313 | 0.61579 | 0.0569 |  | | |  |
| from_2026_10_01 | refit_fbs_mean | 434 | 13.167 | -4.2224 | 0.21393 | 0.61850 | 0.0602 | +0.00081 [+0.0001, +0.0016] | +0.00271 [+0.0004, +0.0049] | -0.00260 [-0.0073, +0.0006] | +0.04634 [+0.0011, +0.1012] |
| from_2026_10_01 | refit_ml_logloss | 434 | 14.046 | -4.3279 | 0.21626 | 0.62678 | 0.0807 | +0.00314 [+0.0009, +0.0054] | +0.01099 [+0.0033, +0.0186] | -0.10810 [-0.1380, -0.0724] | +0.92571 [+0.5637, +1.3056] |
| from_2026_10_01 | refit_orig | 434 | 13.155 | -4.2221 | 0.21387 | 0.61844 | 0.0594 | +0.00074 [+0.0001, +0.0013] | +0.00265 [+0.0005, +0.0046] | -0.00231 [-0.0065, +0.0010] | +0.03496 [-0.0077, +0.0899] |
| fbs_vs_fbs | artifact | 1678 | 12.757 | -4.2022 | 0.19098 | 0.56125 | 0.0201 |  | | |  |
| fbs_vs_fbs | refit_fbs_mean | 1678 | 12.770 | -4.2015 | 0.19107 | 0.56127 | 0.0223 | +0.00009 [-0.0003, +0.0005] | +0.00002 [-0.0011, +0.0012] | +0.00067 [-0.0016, +0.0029] | +0.01256 [-0.0251, +0.0504] |
| fbs_vs_fbs | refit_ml_logloss | 1678 | 13.448 | -4.3047 | 0.19149 | 0.56247 | 0.0364 | +0.00050 [-0.0008, +0.0018] | +0.00121 [-0.0030, +0.0057] | -0.10253 [-0.1190, -0.0851] | +0.69134 [+0.4368, +0.9362] |
| fbs_vs_fbs | refit_orig | 1678 | 12.760 | -4.2008 | 0.19094 | 0.56096 | 0.0251 | -0.00004 [-0.0004, +0.0003] | -0.00030 [-0.0014, +0.0009] | +0.00134 [-0.0008, +0.0037] | +0.00277 [-0.0361, +0.0415] |

Favourite-size buckets (served favourite; model vs realised win %):

| cohort | arm | bucket | n | proj fav margin | actual fav margin | model win % | realised win % |
|---|---|---|---|---|---|---|---|
| all | artifact | 0-3 | 354 | 1.45 | 2.57 | 53.7% | 54.2% |
| all | artifact | 3-7 | 446 | 4.93 | 5.76 | 62.4% | 63.0% |
| all | artifact | 7-14 | 538 | 10.29 | 10.33 | 74.1% | 74.5% |
| all | artifact | 14-21 | 289 | 16.95 | 18.72 | 84.7% | 88.2% |
| all | artifact | 21+ | 322 | 29.54 | 35.21 | 93.3% | 96.3% |
| all | refit_fbs_mean | 0-3 | 341 | 1.49 | 2.46 | 53.8% | 54.3% |
| all | refit_fbs_mean | 3-7 | 419 | 4.94 | 5.79 | 62.5% | 63.0% |
| all | refit_fbs_mean | 7-14 | 526 | 10.23 | 9.62 | 74.1% | 72.8% |
| all | refit_fbs_mean | 14-21 | 317 | 17.02 | 17.91 | 85.1% | 86.8% |
| all | refit_fbs_mean | 21+ | 346 | 30.47 | 34.17 | 94.2% | 96.2% |
| FBS-FBS | artifact | 0-3 | 343 | 1.45 | 2.59 | 53.7% | 54.2% |
| FBS-FBS | artifact | 3-7 | 433 | 4.93 | 5.42 | 62.4% | 62.8% |
| FBS-FBS | artifact | 7-14 | 511 | 10.28 | 9.76 | 74.2% | 73.8% |
| FBS-FBS | artifact | 14-21 | 248 | 16.81 | 17.18 | 84.9% | 87.5% |
| FBS-FBS | artifact | 21+ | 143 | 26.61 | 29.22 | 93.0% | 93.0% |
| FBS-FBS | refit_fbs_mean | 0-3 | 330 | 1.49 | 2.67 | 53.8% | 54.5% |
| FBS-FBS | refit_fbs_mean | 3-7 | 408 | 4.95 | 5.49 | 62.5% | 62.7% |
| FBS-FBS | refit_fbs_mean | 7-14 | 498 | 10.22 | 9.05 | 74.1% | 72.1% |
| FBS-FBS | refit_fbs_mean | 14-21 | 280 | 16.87 | 16.6 | 85.1% | 86.1% |
| FBS-FBS | refit_fbs_mean | 21+ | 162 | 27.29 | 27.74 | 93.5% | 93.2% |

## 5. Market check (ESPN archived closing spreads)

| window | arm | n | corr [95% CI] | ATS >=3 W-L (win %, ROI at 52.4%) | ATS >=7 W-L (win %, ROI) | mean abs disagreement | MAE model / line |
|---|---|---|---|---|---|---|---|
| fit_window_through_2024_11_14 | artifact | 7387 | +0.0016 [-0.0221, 0.025] | 2218-2274 (49.4%, -5.8%) | 931-954 (49.4%, -5.7%) | 5.221 | 13.543 / 12.326 |
| fit_window_through_2024_11_14 | refit_fbs_mean | 7387 | +0.0010 [-0.0224, 0.0248] | 2207-2263 (49.4%, -5.8%) | 916-964 (48.7%, -7.0%) | 5.181 | 13.543 / 12.326 |
| fit_window_through_2024_11_14 | refit_orig | 7387 | +0.0010 [-0.0224, 0.025] | 2211-2282 (49.2%, -6.1%) | 921-966 (48.8%, -6.9%) | 5.184 | 13.54 / 12.326 |
| holdout_before_2026_10_01 | artifact | 1491 | +0.0064 [-0.0451, 0.0557] | 468-464 (50.2%, -4.2%) | 207-210 (49.6%, -5.3%) | 5.357 | 13.174 / 11.812 |
| holdout_before_2026_10_01 | refit_fbs_mean | 1491 | +0.0113 [-0.0423, 0.0605] | 481-455 (51.4%, -1.9%) | 212-216 (49.5%, -5.5%) | 5.287 | 13.09 / 11.812 |
| holdout_before_2026_10_01 | refit_orig | 1491 | +0.0110 [-0.0428, 0.0603] | 483-457 (51.4%, -1.9%) | 211-214 (49.6%, -5.2%) | 5.27 | 13.072 / 11.812 |
| holdout_window | artifact | 1891 | -0.0008 [-0.0471, 0.0417] | 571-565 (50.3%, -4.1%) | 234-241 (49.3%, -6.0%) | 5.06 | 13.119 / 11.837 |
| holdout_window | refit_fbs_mean | 1891 | +0.0042 [-0.042, 0.0462] | 590-561 (51.3%, -2.2%) | 244-248 (49.6%, -5.4%) | 5.039 | 13.059 / 11.837 |
| holdout_window | refit_orig | 1891 | +0.0039 [-0.0423, 0.0459] | 587-565 (51.0%, -2.8%) | 239-246 (49.3%, -6.0%) | 5.024 | 13.043 / 11.837 |
| holdout_window_fbs_vs_fbs | artifact | 1644 | +0.0181 [-0.0256, 0.062] | 488-455 (51.8%, -1.2%) | 181-175 (50.8%, -3.0%) | 4.576 | 12.705 / 11.774 |
| holdout_window_fbs_vs_fbs | refit_fbs_mean | 1644 | +0.0206 [-0.0232, 0.0665] | 505-461 (52.3%, -0.2%) | 191-182 (51.2%, -2.3%) | 4.643 | 12.714 / 11.774 |
| holdout_window_fbs_vs_fbs | refit_orig | 1644 | +0.0205 [-0.0237, 0.0662] | 501-467 (51.8%, -1.2%) | 186-180 (50.8%, -3.0%) | 4.637 | 12.704 / 11.774 |

- fit_window_through_2024_11_14: orientation {'corr_line_margin_vs_actual': 0.7163, 'line_favourite_wins_pct': 76.1}; refit-only games {'only_refit_ge_3': {'losses': 122, 'pushes': 5, 'roi_pct_at_52_4': -1.92, 'win_pct': 51.39, 'win_pct_ci95': [45.2, 57.5], 'wins': 129}, 'only_refit_ge_7': {'losses': 81, 'pushes': 2, 'roi_pct_at_52_4': -9.54, 'win_pct': 47.4, 'win_pct_ci95': [39.7, 55.3], 'wins': 73}, 'side_flips_refit_side': {'losses': 166, 'pushes': 6, 'roi_pct_at_52_4': -8.78, 'win_pct': 47.8, 'win_pct_ci95': [42.4, 53.3], 'wins': 152}}; actual ~ raw + line {'coef_line': 0.9977, 'coef_line_se': 0.0266, 'coef_raw': -0.0047, 'coef_raw_se': 0.0411, 'intercept': -0.11, 'n': 7387}
- holdout_before_2026_10_01: orientation {'corr_line_margin_vs_actual': 0.7542, 'line_favourite_wins_pct': 77.8}; refit-only games {'only_refit_ge_3': {'losses': 20, 'pushes': 1, 'roi_pct_at_52_4': 23.88, 'win_pct': 64.91, 'win_pct_ci95': [51.9, 76.0], 'wins': 37}, 'only_refit_ge_7': {'losses': 13, 'pushes': 0, 'roi_pct_at_52_4': 25.55, 'win_pct': 65.79, 'win_pct_ci95': [49.9, 78.8], 'wins': 25}, 'side_flips_refit_side': {'losses': 31, 'pushes': 0, 'roi_pct_at_52_4': -16.72, 'win_pct': 43.64, 'win_pct_ci95': [31.4, 56.7], 'wins': 24}}; actual ~ raw + line {'coef_line': 0.9703, 'coef_line_se': 0.057, 'coef_raw': 0.096, 'coef_raw_se': 0.0895, 'intercept': 0.787, 'n': 1491}
- holdout_window: orientation {'corr_line_margin_vs_actual': 0.7397, 'line_favourite_wins_pct': 76.4}; refit-only games {'only_refit_ge_3': {'losses': 28, 'pushes': 1, 'roi_pct_at_52_4': 19.59, 'win_pct': 62.67, 'win_pct_ci95': [51.4, 72.7], 'wins': 47}, 'only_refit_ge_7': {'losses': 15, 'pushes': 0, 'roi_pct_at_52_4': 28.61, 'win_pct': 67.39, 'win_pct_ci95': [53.0, 79.1], 'wins': 31}, 'side_flips_refit_side': {'losses': 37, 'pushes': 0, 'roi_pct_at_52_4': -4.58, 'win_pct': 50.0, 'win_pct_ci95': [38.9, 61.1], 'wins': 37}}; actual ~ raw + line {'coef_line': 0.9886, 'coef_line_se': 0.0525, 'coef_raw': 0.074, 'coef_raw_se': 0.0814, 'intercept': 0.583, 'n': 1891}
- holdout_window_fbs_vs_fbs: orientation {'corr_line_margin_vs_actual': 0.6579, 'line_favourite_wins_pct': 73.3}; refit-only games {'only_refit_ge_3': {'losses': 25, 'pushes': 1, 'roi_pct_at_52_4': 18.55, 'win_pct': 62.12, 'win_pct_ci95': [50.1, 72.9], 'wins': 41}, 'only_refit_ge_7': {'losses': 11, 'pushes': 0, 'roi_pct_at_52_4': 30.86, 'win_pct': 68.57, 'win_pct_ci95': [52.0, 81.4], 'wins': 24}, 'side_flips_refit_side': {'losses': 31, 'pushes': 0, 'roi_pct_at_52_4': -9.43, 'win_pct': 47.46, 'win_pct_ci95': [35.3, 60.0], 'wins': 28}}; actual ~ raw + line {'coef_line': 0.9541, 'coef_line_se': 0.0618, 'coef_raw': 0.0993, 'coef_raw_se': 0.0924, 'intercept': 0.458, 'n': 1644}

Corr by season (artifact vs selected refit): {'2016': {'artifact': -0.0092, 'refit_fbs_mean': -0.0145}, '2017': {'artifact': 0.0169, 'refit_fbs_mean': 0.0187}, '2018': {'artifact': -0.0073, 'refit_fbs_mean': -0.0088}, '2019': {'artifact': -0.0348, 'refit_fbs_mean': -0.0333}, '2020': {'artifact': 0.0836, 'refit_fbs_mean': 0.0838}, '2021': {'artifact': 0.0056, 'refit_fbs_mean': 0.004}, '2022': {'artifact': -0.0073, 'refit_fbs_mean': -0.0107}, '2023': {'artifact': -0.0103, 'refit_fbs_mean': -0.0116}, '2024': {'artifact': 0.034, 'refit_fbs_mean': 0.0382}, '2025': {'artifact': 0.0195, 'refit_fbs_mean': 0.0242}, '2026': {'artifact': -0.0585, 'refit_fbs_mean': -0.0529}}

## 6. Decomposition (informational, run after the verdict; does not change it)

Which layer carries the holdout change? Each arm differs from the artifact in one layer only; same engine, same common random numbers.

| arm | subset | d Brier [CI] | d log loss [CI] | d log score [CI] | d MAE [CI] |
|---|---|---|---|---|---|
| fcs_sd_slice_only | all | -0.00020 [-0.0003, -0.0001] | -0.00096 [-0.0014, -0.0005] | +0.00078 [-0.0006, +0.0023] | +0.00000 [+0.0000, +0.0000] |
| fcs_sd_slice_only | fbs_vs_fbs | +0.00000 [+0.0000, +0.0000] | +0.00000 [+0.0000, +0.0000] | +0.00000 [+0.0000, +0.0000] | +0.00000 [+0.0000, +0.0000] |
| fcs_sd_slice_only | fbs_vs_fcs | -0.00143 [-0.0021, -0.0008] | -0.00693 [-0.0092, -0.0047] | +0.00561 [-0.0049, +0.0148] | +0.00000 [+0.0000, +0.0000] |
| fcs_sd_slice_only | from_2026_10_01 | +0.00002 [-0.0001, +0.0001] | +0.00015 [-0.0002, +0.0006] | -0.00001 [-0.0007, +0.0007] | +0.00000 [+0.0000, +0.0000] |
| sd_table_only | all | -0.00024 [-0.0004, -0.0001] | -0.00111 [-0.0016, -0.0007] | +0.00153 [+0.0001, +0.0031] | +0.00000 [+0.0000, +0.0000] |
| sd_table_only | fbs_vs_fbs | -0.00005 [-0.0001, +0.0000] | -0.00017 [-0.0003, -0.0000] | +0.00088 [+0.0002, +0.0016] | +0.00000 [+0.0000, +0.0000] |
| sd_table_only | fbs_vs_fcs | -0.00143 [-0.0021, -0.0008] | -0.00693 [-0.0092, -0.0047] | +0.00561 [-0.0049, +0.0148] | +0.00000 [+0.0000, +0.0000] |
| sd_table_only | from_2026_10_01 | -0.00011 [-0.0003, +0.0001] | -0.00030 [-0.0009, +0.0004] | +0.00028 [-0.0018, +0.0019] | +0.00000 [+0.0000, +0.0000] |
| selected_refit_by_class | fbs_vs_fcs | -0.00224 [-0.0034, -0.0012] | -0.01286 [-0.0166, -0.0089] | +0.03064 [+0.0206, +0.0402] | -0.52628 [-0.6779, -0.3886] |
| slope_intercept_only | all | -0.00005 [-0.0004, +0.0003] | -0.00084 [-0.0019, +0.0003] | +0.00279 [+0.0003, +0.0053] | -0.06236 [-0.1113, -0.0139] |
| slope_intercept_only | fbs_vs_fbs | +0.00011 [-0.0003, +0.0005] | +0.00013 [-0.0009, +0.0013] | -0.00021 [-0.0023, +0.0020] | +0.01256 [-0.0251, +0.0504] |
| slope_intercept_only | fbs_vs_fcs | -0.00103 [-0.0015, -0.0006] | -0.00679 [-0.0083, -0.0050] | +0.02138 [+0.0162, +0.0270] | -0.52628 [-0.6779, -0.3886] |
| slope_intercept_only | from_2026_10_01 | +0.00084 [+0.0001, +0.0017] | +0.00279 [+0.0006, +0.0050] | -0.00298 [-0.0064, -0.0000] | +0.04634 [+0.0011, +0.1012] |

Arms: {"fcs_sd_slice_only": {"conference_margin_scale": 0.9641, "margin_calibration": {"intercept": -0.4143, "slope": 1.3189}, "margin_sd_table": {"established": 17.499, "fbs_vs_fcs": 18.238, "thin_sample": 17.125}}, "sd_table_only": {"conference_margin_scale": 0.9978, "margin_calibration": {"intercept": -0.4143, "slope": 1.3189}, "margin_sd_table": {"established": 16.919, "fbs_vs_fcs": 18.238, "thin_sample": 17.073}}, "selected_refit_by_class": {"conference_margin_scale": 0.9978, "margin_calibration": {"intercept": -0.6596, "slope": 1.3993}, "margin_sd_table": {"established": 16.919, "fbs_vs_fcs": 18.238, "thin_sample": 17.073}}, "slope_intercept_only": {"conference_margin_scale": 0.9641, "margin_calibration": {"intercept": -0.6596, "slope": 1.3993}, "margin_sd_table": {"established": 17.499, "fbs_vs_fcs": 19.731, "thin_sample": 17.125}}}

## Reading

- The 1.53 trigger does not reproduce with real weeks. On the identical 427 events (2026-10-02..11-19, weeks 5-12) the replay gives actual-on-raw slope 1.22 (SE 0.09) and FBS-vs-FBS 1.15, with Brier 0.2128 (FBS-vs-FBS 0.2146). Forcing week=1 (UpcomingCFBGame's default) gives Brier 0.2145, FBS-vs-FBS 0.2168, calibrated MAE 13.02 and slope 1.67. The idf summary published 0.2149 / 0.2170 / 13.06 / 1.53. The idf check therefore almost certainly priced these mid-season games as week 1. That leans on preseason priors, which compress projections. The served slate passes the real ESPN week, so 1.53 is a harness artifact. Lookahead checks: passing the full history to the extractor returns identical raw margins, because the extractor filters on kickoff itself; a fresh model on truncated history matches exactly.
- Validation window, FBS-vs-FBS only (Gaussian stand-in): every refit delta's CI spans 0. For refit_fbs_mean: Brier -0.0001 [-0.0005, +0.0003], log loss -0.0005 [-0.0018, +0.0008], margin log score -0.0011 [-0.0032, +0.0010]. The holdout neutrality on FBS-vs-FBS predates the holdout. refit_orig and refit_fbs_mean tied on pooled validation (Brier 0.16952 vs 0.16951); the choice between them is noise.
- The fixes rescaled raw margins by about 0.95 (current raw on 2026-11-12 raw, same 7,960 events, every season 0.935-0.988). With the artifact's own estimator, which reproduces 1.3189 / -0.4143 exactly on the old rows, the train refit is 1.3948 / -0.4026 (FBS mean fit 1.3993 / -0.6596). That is a mechanical 6% steepening, not a jump to 1.5.
- By-season FBS-vs-FBS slopes run 1.32-1.52 with no trend; 2026 FBS-vs-FBS is 1.32. Compression is week-dependent: weeks 0-4 FBS-vs-FBS 1.53, weeks 5-14 1.32, postseason 1.06; thin samples 1.44, established 1.32. A single slope is the wrong tool for that. A week- or sample-dependent slope would be its own challenger.
- Holdout: the pinned rule returns REFIT on the pooled cohort. Decomposition shows where the gain comes from. The FBS-vs-FCS games, which the Main gate blocks, gain from both the new SD slice (19.73 to 18.24, which had been fit under the old fail-open labels) and the mean. On FBS-vs-FBS games the slope/intercept change is neutral (Brier +0.0001, CI -0.0003 to +0.0005), and the SD table gives a small log-loss gain (-0.00017, CI excludes 0). In the 2026-10-01+ window the slope change is worse (Brier +0.0008, CI excludes 0).
- Market: the correlation of (served minus line) with (actual minus line) is about 0 under both arms, in every window and season. Against the spread at a 52.4% break-even, neither arm clears it at disagreements of 3 or more or 7 or more points. Holding the line fixed, the raw projection adds nothing (fit window: coefficient -0.005, SE 0.041). The steeper slope widens no edge; there is none to widen.

## Caveats

- History rows are not vintaged (scores only). Team identity and FBS/FCS class are rebuilt from rows before kickoff, but a row's feed membership is as fetched later.
- Weather: history files carry placeholders, so no weather term (as production with no verified forecast).
- QB availability has no point-in-time source before 2026-11 (channel inert).
- ESPN lines are archived closing / final pregame lines with no per-quote observed_at: a closing benchmark, not decision-time quotes. From 2024 each event carries one book.
- Moneyline probabilities use the projected (raw-margin) spread for blowout widening in every arm, as the served path does when no market spread is supplied.
- The 427-game trigger sample lies inside the locked holdout; the holdout is reported with and without it.
- Prices are percentages; no American odds. Nothing promoted; artifact unchanged.
