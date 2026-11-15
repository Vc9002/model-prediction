# NCAAF totals W1: walk-forward level tracker on frozen v6 (quick-261115-183)

- rule_sha256 `1481273f1fbd860a5e35ecc25a864818e74818f564aa0f6d5710ea42f524145f`; script sha256 `fa63a1ad5166702eabe6eaa31ec6e9b540ac2bc8987fd13decbb1b29f9ff523c`; generated 2026-11-15T06:39:19Z
- final run: True; holdout_reads: 1
- Values are points (bias = mean(model - actual)); brackets are date-clustered 95% CIs (B = 1000, seed 20261115).
- Select-window self check: line MAE 12.92, v6 MAE 13.41, n 4590 (matches the diagnosis).

## Selected window (select window only)

Chosen window: **days 90**. v6 RMSE with no tracker on the select rows: 16.981.

| window | select RMSE of v6 + offset | mean games used |
|---|---|---|
| days 14 | 17.055 | 170.0 |
| days 30 | 17.039 | 346.3 |
| days 60 | 17.022 | 556.8 |
| days 90 (chosen) | 17.021 | 652.3 |
| games 150 | 17.057 | 148.7 |
| games 300 | 17.031 | 295.1 |
| games 600 | 17.029 | 581.3 |

## select window (n = 4590 lined games, 432 dates)

| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |
|---|---|---|---|---|---|---|
| v6 | 4590 | -0.41 [-0.87, 0.05] | 13.41 [13.12, 13.74] | 16.98 [16.60, 17.36] | 47.5% [45.5, 49.4] | -0.003 [-0.037, 0.034] |
| v6 + tracker | 4590 | -0.09 [-0.58, 0.41] | 13.45 [13.16, 13.77] | 17.02 [16.66, 17.39] | 50.6% [48.2, 53.1] | -0.004 [-0.036, 0.030] |
| closing line | 4590 | -0.07 [-0.48, 0.35] | 12.92 [12.62, 13.22] | 16.37 [16.00, 16.75] | n/a | n/a |

| pair (a - b) | MAE diff | RMSE diff |
|---|---|---|
| v6-line | 0.493 [0.347, 0.640] | 0.616 [0.457, 0.778] |
| v6_tracker-line | 0.535 [0.383, 0.689] | 0.656 [0.497, 0.819] |
| v6_tracker-v6 | 0.042 [-0.001, 0.087] | 0.040 [-0.003, 0.088] |

Tracker offset: mean 0.32, sd 1.45, range [-13.06, 6.17], mean games used 652, rows with no eligible game 0.0%.

## validation window (n = 1790 lined games, 172 dates)

| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |
|---|---|---|---|---|---|---|
| v6 | 1790 | 0.38 [-0.45, 1.20] | 12.96 [12.50, 13.41] | 16.31 [15.85, 16.78] | 66.0% [63.5, 68.3] | 0.059 [0.006, 0.111] |
| v6 + tracker | 1790 | -0.37 [-1.18, 0.46] | 12.96 [12.51, 13.44] | 16.33 [15.85, 16.83] | 58.7% [55.5, 61.9] | 0.055 [0.004, 0.106] |
| closing line | 1790 | -1.28 [-2.09, -0.48] | 12.68 [12.22, 13.10] | 16.02 [15.47, 16.54] | n/a | n/a |

| pair (a - b) | MAE diff | RMSE diff |
|---|---|---|
| v6-line | 0.276 [0.084, 0.473] | 0.290 [0.050, 0.539] |
| v6_tracker-line | 0.280 [0.105, 0.474] | 0.312 [0.091, 0.541] |
| v6_tracker-v6 | 0.004 [-0.049, 0.057] | 0.022 [-0.036, 0.083] |

Tracker offset: mean -0.75, sd 0.85, range [-3.40, 2.67], mean games used 762, rows with no eligible game 0.0%.

## holdout window (n = 1861 lined games, 178 dates)

| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |
|---|---|---|---|---|---|---|
| v6 | 1861 | 0.50 [-0.33, 1.40] | 12.99 [12.60, 13.40] | 16.07 [15.63, 16.49] | 63.9% [60.7, 67.2] | -0.030 [-0.074, 0.011] |
| v6 + tracker | 1861 | -0.36 [-1.30, 0.71] | 13.04 [12.63, 13.48] | 16.14 [15.69, 16.60] | 57.7% [53.0, 62.4] | -0.026 [-0.073, 0.017] |
| closing line | 1861 | -0.79 [-1.56, 0.05] | 12.39 [11.98, 12.81] | 15.42 [14.95, 15.88] | n/a | n/a |

| pair (a - b) | MAE diff | RMSE diff |
|---|---|---|
| v6-line | 0.599 [0.421, 0.800] | 0.649 [0.445, 0.874] |
| v6_tracker-line | 0.647 [0.442, 0.854] | 0.716 [0.481, 0.955] |
| v6_tracker-v6 | 0.048 [-0.045, 0.151] | 0.067 [-0.037, 0.185] |

Tracker offset: mean -0.85, sd 1.43, range [-9.12, 0.48], mean games used 773, rows with no eligible game 0.0%.

## Line tracker diagnostic (diagnostic only)

Same tracker on (actual - close total) over lined games; window picked on the select window by lowest RMSE of (line + offset). Own window: **games 600**.

### select, own_selected_window (games 600), n = 4590

| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |
|---|---|---|---|---|---|---|
| closing line | 4590 | -0.07 [-0.48, 0.35] | 12.92 [12.62, 13.22] | 16.37 [16.00, 16.75] | n/a | n/a |
| line + line tracker | 4590 | -0.02 [-0.45, 0.42] | 12.94 [12.65, 13.24] | 16.39 [16.03, 16.77] | 58.4% [50.3, 66.5] | -0.019 [-0.039, 0.003] |

| pair (a - b) | MAE diff | RMSE diff |
|---|---|---|
| line_tracker-line | 0.019 [0.001, 0.040] | 0.028 [0.009, 0.049] |

### select, at_v6_tracker_window (days 90), n = 4590

| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |
|---|---|---|---|---|---|---|
| closing line | 4590 | -0.07 [-0.48, 0.35] | 12.92 [12.62, 13.22] | 16.37 [16.00, 16.75] | n/a | n/a |
| line + line tracker | 4590 | -0.21 [-0.68, 0.25] | 12.97 [12.68, 13.26] | 16.42 [16.07, 16.80] | 47.1% [38.9, 55.7] | -0.020 [-0.041, 0.002] |

| pair (a - b) | MAE diff | RMSE diff |
|---|---|---|
| line_tracker-line | 0.049 [0.017, 0.079] | 0.059 [0.030, 0.088] |

### validation, own_selected_window (games 600), n = 1790

| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |
|---|---|---|---|---|---|---|
| closing line | 1790 | -1.28 [-2.09, -0.48] | 12.68 [12.22, 13.10] | 16.02 [15.47, 16.54] | n/a | n/a |
| line + line tracker | 1790 | -0.40 [-1.21, 0.44] | 12.70 [12.26, 13.11] | 15.99 [15.46, 16.50] | 73.5% [60.6, 85.2] | 0.005 [-0.041, 0.047] |

| pair (a - b) | MAE diff | RMSE diff |
|---|---|---|
| line_tracker-line | 0.023 [-0.033, 0.079] | -0.029 [-0.085, 0.031] |

### validation, at_v6_tracker_window (days 90), n = 1790

| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |
|---|---|---|---|---|---|---|
| closing line | 1790 | -1.28 [-2.09, -0.48] | 12.68 [12.22, 13.10] | 16.02 [15.47, 16.54] | n/a | n/a |
| line + line tracker | 1790 | -0.34 [-1.20, 0.52] | 12.72 [12.28, 13.13] | 16.01 [15.48, 16.53] | 81.7% [70.9, 90.3] | -0.024 [-0.076, 0.023] |

| pair (a - b) | MAE diff | RMSE diff |
|---|---|---|
| line_tracker-line | 0.043 [-0.009, 0.101] | -0.008 [-0.059, 0.046] |

### holdout, own_selected_window (games 600), n = 1861

| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |
|---|---|---|---|---|---|---|
| closing line | 1861 | -0.79 [-1.56, 0.05] | 12.39 [11.98, 12.81] | 15.42 [14.95, 15.88] | n/a | n/a |
| line + line tracker | 1861 | 0.08 [-0.71, 0.94] | 12.41 [12.02, 12.82] | 15.42 [14.97, 15.86] | 98.1% [96.7, 99.1] | -0.014 [-0.063, 0.033] |

| pair (a - b) | MAE diff | RMSE diff |
|---|---|---|
| line_tracker-line | 0.018 [-0.022, 0.062] | -0.005 [-0.054, 0.050] |

### holdout, at_v6_tracker_window (days 90), n = 1861

| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |
|---|---|---|---|---|---|---|
| closing line | 1861 | -0.79 [-1.56, 0.05] | 12.39 [11.98, 12.81] | 15.42 [14.95, 15.88] | n/a | n/a |
| line + line tracker | 1861 | -0.29 [-1.13, 0.66] | 12.46 [12.04, 12.89] | 15.48 [15.00, 15.95] | 82.6% [71.1, 93.0] | -0.022 [-0.066, 0.034] |

| pair (a - b) | MAE diff | RMSE diff |
|---|---|---|
| line_tracker-line | 0.068 [0.001, 0.145] | 0.051 [-0.034, 0.148] |

## Reading against the expectation

Rule: removes the over lean = over lean within 5 points of 50% in every window; adds no edge = no window where the tracker improves paired MAE over v6 with a CI entirely below 0 or where v6 + tracker beats the closing line with a CI entirely below 0.

| window | over lean v6 | over lean v6 + tracker | within 5 pts of 50% | corr v6 | corr v6 + tracker | tracker MAE vs v6 CI < 0 | v6 + tracker MAE vs line CI < 0 |
|---|---|---|---|---|---|---|---|
| select | 47.5% | 50.6% | True | -0.003 | -0.004 | False | False |
| validation | 66.0% | 58.7% | False | 0.059 | 0.055 | False | False |
| holdout | 63.9% | 57.7% | False | -0.030 | -0.026 | False | False |

Removes the over lean: **False**. Adds edge: **False**. Expected result (removes the over lean and adds no edge) confirmed: **False**.

