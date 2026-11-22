# P1_player_availability (addendum v4, development, spread)

- candidate: P1: v7 + b1*d(avail_top7_prev1) + b2*d(top7_minutes_share_out_3plus), LOSO OLS v1
- addendum v4 sha256 `ccb107fc6f0b5b525337f8e48e7c9717b27afe824b78f7ea6c354f354618490e`; K {'spread_market_relative': 25, 'total_market_relative': 26, 'cover_calibration': 16, 'moneyline': 16}
- read count for grid name: 1; shuffled: False
- reproduction gate (v1 K 21): True
- coverage by season: {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags: none
- **verdict: DEV_ELIGIBLE** {"corr_delta_adj_lower_gt_0": true, "rmse_delta_point_le_0": true, "late_folds_corr_delta_gt_0": true}
# NCAAB closing-line scorecard (development, protocol `ncaab-research-protocol-261120`)

Protocol sha256 `72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813`. Seasons [2023, 2024, 2025, 2026] (leave-one-season-out). Cohort: 22947 D1-vs-D1 games with a closing spread line, identical for every row (by season {2023: 5716, 2024: 5724, 2025: 5763, 2026: 5744}; 16 unlined D1 games not scored). Points and probabilities only. Line source `espn_archive` (ncaab-espn-lines-rule-1, sha256 `3386b8d1f1f8d50f...`).

## Market-relative (primary): (actual - closing line) on (model - closing line)

| model | n | slope | 95% CI | corr | 95% CI | rule-1 MSE delta | K-adj CI (K 25) | edge-eligible |
|---|---|---|---|---|---|---|---|---|
| cand | 22947 | +0.015 | [-0.049, +0.081] | +0.0032 | [-0.0106, +0.0176] | -0.0107 | [-0.0278, +0.0050] | no |
| v7_served | 22947 | -0.015 | [-0.078, +0.049] | -0.0033 | [-0.0170, +0.0107] | -0.0108 | [-0.0278, +0.0052] | no |
| v6_asof | 22947 | -0.002 | [-0.051, +0.047] | -0.0008 | [-0.0163, +0.0146] | -0.0080 | [-0.0208, +0.0040] | no |
| closing_line | 22947 | N/A | - | N/A | - | N/A | - | - |

## Margin RMSE / bias (actual - predicted) by games played

| model | all | 0 | 1-2 | 3-7 | 8+ |
|---|---|---|---|---|---|
| cand | 11.44 / +0.10 (n 22947) | 13.34 / +2.45 (n 589) | 12.25 / +0.60 (n 1359) | 12.15 / +0.39 (n 3537) | 11.15 / -0.08 (n 17462) |
| v7_served | 11.46 / +0.10 (n 22947) | 13.34 / +2.45 (n 589) | 12.25 / +0.60 (n 1359) | 12.14 / +0.41 (n 3537) | 11.18 / -0.07 (n 17462) |
| v6_asof | 11.75 / +0.62 (n 22947) | 15.11 / +6.01 (n 589) | 12.93 / +2.64 (n 1359) | 12.45 / +1.43 (n 3537) | 11.37 / +0.12 (n 17462) |
| closing_line | 11.19 / +0.08 (n 22947) | 12.22 / +1.87 (n 589) | 11.68 / -0.11 (n 1359) | 11.75 / +0.29 (n 3537) | 10.99 / -0.00 (n 17462) |

## Margin RMSE / bias by |closing line|

| model | <5 | 5-10 | 10-20 | 20+ |
|---|---|---|---|---|
| cand | 11.22 / -0.21 (n 9267) | 11.35 / +0.01 (n 7263) | 11.52 / +0.29 (n 4904) | 12.85 / +1.89 (n 1513) |
| v7_served | 11.24 / -0.26 (n 9267) | 11.37 / +0.01 (n 7263) | 11.54 / +0.37 (n 4904) | 12.87 / +1.98 (n 1513) |
| v6_asof | 11.23 / -0.68 (n 9267) | 11.48 / +0.02 (n 7263) | 11.93 / +1.77 (n 4904) | 15.10 / +7.72 (n 1513) |
| closing_line | 11.03 / -0.05 (n 9267) | 11.14 / +0.15 (n 7263) | 11.20 / +0.16 (n 4904) | 12.28 / +0.34 (n 1513) |

## Margin RMSE by held-out season

| model | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| cand | 11.32 / +0.21 (n 5716) | 11.37 / -0.05 (n 5724) | 11.48 / +0.30 (n 5763) | 11.58 / -0.07 (n 5744) |
| v7_served | 11.34 / +0.22 (n 5716) | 11.39 / -0.05 (n 5724) | 11.50 / +0.31 (n 5763) | 11.60 / -0.07 (n 5744) |
| v6_asof | 11.54 / +0.65 (n 5716) | 11.65 / +0.47 (n 5724) | 11.81 / +0.81 (n 5763) | 11.99 / +0.54 (n 5744) |
| closing_line | 11.12 / +0.41 (n 5716) | 11.17 / -0.02 (n 5724) | 11.19 / +0.14 (n 5763) | 11.27 / -0.18 (n 5744) |

## Cover probability at the closing line (pushes dropped)

| model | n | Brier | log loss | fav 0.5-0.6 n/p/hit | 0.6-0.7 | 0.7-0.8 | 0.8-1.0 |
|---|---|---|---|---|---|---|---|
| cand | 22787 | 0.2574 | 0.7091 | 17608 / 0.543 / 0.503 | 4540 / 0.635 / 0.499 | 556 / 0.733 / 0.498 | 83 / 0.831 / 0.470 |
| v7_served | 22787 | 0.2578 | 0.7099 | 17569 / 0.544 / 0.501 | 4576 / 0.635 / 0.499 | 561 / 0.734 / 0.490 | 81 / 0.832 / 0.469 |
| v6_asof | 22787 | 0.2616 | 0.7189 | 15140 / 0.546 / 0.503 | 6065 / 0.639 / 0.496 | 1281 / 0.737 / 0.523 | 301 / 0.839 / 0.468 |
| closing_line | N/A | - | - | - | - | - | - |

## Moneyline (home win), against the closing-spread-implied probability

| model | n | Brier | log loss | corr(win - p_line, p - p_line), 95% CI |
|---|---|---|---|---|
| cand | 22947 | 0.1820 | 0.5380 | +0.0057 [-0.0083, +0.0198] |
| v7_served | 22947 | 0.1822 | 0.5386 | +0.0022 [-0.0114, +0.0164] |
| v6_asof | 22947 | 0.1849 | 0.5466 | +0.0060 [-0.0078, +0.0200] |
| closing_line | 22947 | 0.1769 | 0.5238 | N/A |

p_line = 1 - Phi((0.5 - L) / sigma); sigma by held-out season (fit on the other seasons): {2023: 11.2108, 2024: 11.1934, 2025: 11.1884, 2026: 11.1598}.

## Totals (games with a closing total)

| model | RMSE / bias | slope, 95% CI | corr | rule-1 MSE delta, K-adj CI | over Brier / log loss |
|---|---|---|---|---|---|
| v7_served | 17.03 / -1.02 (n 22946) | +0.068 [+0.017, +0.118] | +0.0181 | +0.0539 [-0.0625, +0.1521] | 0.2584 / 0.7110 |
| v6_asof | 17.03 / -1.02 (n 22946) | +0.068 [+0.017, +0.118] | +0.0181 | +0.0539 [-0.0625, +0.1521] | 0.2584 / 0.7110 |
| closing_line | 16.52 / +0.83 (n 22946) | N/A | N/A | N/A | N/A |

v7_served totals are the served v6 total (v7 never serves totals), so they equal v6_asof.

## Candidate minus served v7 (identical games)

| statistic | delta | 95% CI | K-adj CI | K | 2025+2026 folds |
|---|---|---|---|---|---|
| spread_corr_delta_vs_v7 | +0.00654 | [+0.00320, +0.01005] | [+0.00133, +0.01208] | 25 | 0.00654 |
| margin_rmse_delta_vs_v7 | -0.01719 | [-0.02556, -0.00903] | - | - | - |
| cover_logloss_delta_vs_v7 | -0.00080 | [-0.00149, -0.00011] | [-0.00183, +0.00025] | 16 | -0.00079 |
| moneyline_logloss_delta_vs_v7 | -0.00065 | [-0.00095, -0.00036] | [-0.00107, -0.00023] | 16 | -0.00066 |
| moneyline_brier_delta_vs_v7 | -0.00026 | [-0.00039, -0.00013] | - | - | -0.00026 |

Candidate `P1: v7 + b1*d(avail_top7_prev1) + b2*d(top7_minutes_share_out_3plus), LOSO OLS v1` grid `P1_player_availability`; coverage {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags none.
**Verdict: DEV_ELIGIBLE**: eligible for the sealed confirmation window; NOT 'beats v7' until confirmation passes {'corr_delta_adj_lower_gt_0': True, 'rmse_delta_point_le_0': True, 'late_folds_corr_delta_gt_0': True}. Edge (rule-1, development): {'spread': False}.

## Adjustment non-zero subset (no verdict)

- n non-zero 10157 (44.3% of the scored cohort); by season {"2023": {"n": 5716, "n_nonzero": 2305, "share": 0.40325}, "2024": {"n": 5724, "n_nonzero": 2444, "share": 0.42697}, "2025": {"n": 5763, "n_nonzero": 2656, "share": 0.46087}, "2026": {"n": 5744, "n_nonzero": 2752, "share": 0.47911}}
- adjustment: {"mean": 0.0092, "sd": 0.9768, "mean_abs": 0.8424, "p99_abs": 2.4384}
- spread_corr_delta_vs_v7: +0.01705 95% [+0.00807, +0.02604] K-adj [+0.00306, +0.03202] 2025+2026 0.01630
- margin_rmse_delta_vs_v7: -0.03935 95% [-0.05862, -0.02020] K-adj - 2025+2026 -
- cover_logloss_delta_vs_v7: -0.00181 95% [-0.00331, -0.00023] K-adj [-0.00410, +0.00062] 2025+2026 -0.00167
