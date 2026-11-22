# P2_returning_production_early (addendum v4, development, spread)

- candidate: P2: v7 + max(0, 1 - s/10)*(c1*d(ret gmsc same+transfer) + c2*d(prior gmsc40)), LOSO OLS v1
- addendum v4 sha256 `ccb107fc6f0b5b525337f8e48e7c9717b27afe824b78f7ea6c354f354618490e`; K {'spread_market_relative': 25, 'total_market_relative': 26, 'cover_calibration': 16, 'moneyline': 16}
- read count for grid name: 1; shuffled: False
- reproduction gate (v1 K 21): True
- coverage by season: {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags: none
- **verdict: NOT_ELIGIBLE** {"corr_delta_adj_lower_gt_0": false, "rmse_delta_point_le_0": true, "late_folds_corr_delta_gt_0": false}
# NCAAB closing-line scorecard (development, protocol `ncaab-research-protocol-261120`)

Protocol sha256 `72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813`. Seasons [2023, 2024, 2025, 2026] (leave-one-season-out). Cohort: 22947 D1-vs-D1 games with a closing spread line, identical for every row (by season {2023: 5716, 2024: 5724, 2025: 5763, 2026: 5744}; 16 unlined D1 games not scored). Points and probabilities only. Line source `espn_archive` (ncaab-espn-lines-rule-1, sha256 `3386b8d1f1f8d50f...`).

## Market-relative (primary): (actual - closing line) on (model - closing line)

| model | n | slope | 95% CI | corr | 95% CI | rule-1 MSE delta | K-adj CI (K 25) | edge-eligible |
|---|---|---|---|---|---|---|---|---|
| cand | 22947 | -0.015 | [-0.082, +0.053] | -0.0032 | [-0.0170, +0.0109] | -0.0132 | [-0.0312, +0.0041] | no |
| v7_served | 22947 | -0.015 | [-0.078, +0.049] | -0.0033 | [-0.0170, +0.0107] | -0.0108 | [-0.0278, +0.0052] | no |
| v6_asof | 22947 | -0.002 | [-0.051, +0.047] | -0.0008 | [-0.0163, +0.0146] | -0.0080 | [-0.0208, +0.0040] | no |
| closing_line | 22947 | N/A | - | N/A | - | N/A | - | - |

## Margin RMSE / bias (actual - predicted) by games played

| model | all | 0 | 1-2 | 3-7 | 8+ |
|---|---|---|---|---|---|
| cand | 11.43 / -0.00 (n 22947) | 13.34 / +2.45 (n 589) | 12.12 / -0.35 (n 1359) | 12.05 / +0.11 (n 3537) | 11.18 / -0.08 (n 17462) |
| v7_served | 11.46 / +0.10 (n 22947) | 13.34 / +2.45 (n 589) | 12.25 / +0.60 (n 1359) | 12.14 / +0.41 (n 3537) | 11.18 / -0.07 (n 17462) |
| v6_asof | 11.75 / +0.62 (n 22947) | 15.11 / +6.01 (n 589) | 12.93 / +2.64 (n 1359) | 12.45 / +1.43 (n 3537) | 11.37 / +0.12 (n 17462) |
| closing_line | 11.19 / +0.08 (n 22947) | 12.22 / +1.87 (n 589) | 11.68 / -0.11 (n 1359) | 11.75 / +0.29 (n 3537) | 10.99 / -0.00 (n 17462) |

## Margin RMSE / bias by |closing line|

| model | <5 | 5-10 | 10-20 | 20+ |
|---|---|---|---|---|
| cand | 11.22 / -0.24 (n 9267) | 11.35 / -0.00 (n 7263) | 11.51 / +0.14 (n 4904) | 12.78 / +0.94 (n 1513) |
| v7_served | 11.24 / -0.26 (n 9267) | 11.37 / +0.01 (n 7263) | 11.54 / +0.37 (n 4904) | 12.87 / +1.98 (n 1513) |
| v6_asof | 11.23 / -0.68 (n 9267) | 11.48 / +0.02 (n 7263) | 11.93 / +1.77 (n 4904) | 15.10 / +7.72 (n 1513) |
| closing_line | 11.03 / -0.05 (n 9267) | 11.14 / +0.15 (n 7263) | 11.20 / +0.16 (n 4904) | 12.28 / +0.34 (n 1513) |

## Margin RMSE by held-out season

| model | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| cand | 11.30 / +0.17 (n 5716) | 11.37 / -0.12 (n 5724) | 11.48 / +0.15 (n 5763) | 11.57 / -0.21 (n 5744) |
| v7_served | 11.34 / +0.22 (n 5716) | 11.39 / -0.05 (n 5724) | 11.50 / +0.31 (n 5763) | 11.60 / -0.07 (n 5744) |
| v6_asof | 11.54 / +0.65 (n 5716) | 11.65 / +0.47 (n 5724) | 11.81 / +0.81 (n 5763) | 11.99 / +0.54 (n 5744) |
| closing_line | 11.12 / +0.41 (n 5716) | 11.17 / -0.02 (n 5724) | 11.19 / +0.14 (n 5763) | 11.27 / -0.18 (n 5744) |

## Cover probability at the closing line (pushes dropped)

| model | n | Brier | log loss | fav 0.5-0.6 n/p/hit | 0.6-0.7 | 0.7-0.8 | 0.8-1.0 |
|---|---|---|---|---|---|---|---|
| cand | 22787 | 0.2572 | 0.7084 | 17853 / 0.543 / 0.499 | 4418 / 0.634 / 0.504 | 464 / 0.731 / 0.487 | 52 / 0.833 / 0.462 |
| v7_served | 22787 | 0.2578 | 0.7099 | 17569 / 0.544 / 0.501 | 4576 / 0.635 / 0.499 | 561 / 0.734 / 0.490 | 81 / 0.832 / 0.469 |
| v6_asof | 22787 | 0.2616 | 0.7189 | 15140 / 0.546 / 0.503 | 6065 / 0.639 / 0.496 | 1281 / 0.737 / 0.523 | 301 / 0.839 / 0.468 |
| closing_line | N/A | - | - | - | - | - | - |

## Moneyline (home win), against the closing-spread-implied probability

| model | n | Brier | log loss | corr(win - p_line, p - p_line), 95% CI |
|---|---|---|---|---|
| cand | 22947 | 0.1819 | 0.5376 | +0.0036 [-0.0101, +0.0176] |
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
| spread_corr_delta_vs_v7 | +0.00013 | [-0.00422, +0.00444] | [-0.00749, +0.00756] | 25 | -0.00140 |
| margin_rmse_delta_vs_v7 | -0.02522 | [-0.03620, -0.01480] | - | - | - |
| cover_logloss_delta_vs_v7 | -0.00152 | [-0.00227, -0.00084] | [-0.00272, -0.00045] | 16 | -0.00109 |
| moneyline_logloss_delta_vs_v7 | -0.00104 | [-0.00134, -0.00076] | [-0.00152, -0.00064] | 16 | -0.00103 |
| moneyline_brier_delta_vs_v7 | -0.00041 | [-0.00053, -0.00029] | - | - | -0.00038 |

Candidate `P2: v7 + max(0, 1 - s/10)*(c1*d(ret gmsc same+transfer) + c2*d(prior gmsc40)), LOSO OLS v1` grid `P2_returning_production_early`; coverage {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags none.
**Verdict: NOT_ELIGIBLE**: does not beat v7 under the pinned development rule {'corr_delta_adj_lower_gt_0': False, 'rmse_delta_point_le_0': True, 'late_folds_corr_delta_gt_0': False}. Edge (rule-1, development): {'spread': False}.

## Adjustment non-zero subset (no verdict)

- n non-zero 5528 (24.1% of the scored cohort); by season {"2023": {"n": 5716, "n_nonzero": 1220, "share": 0.21344}, "2024": {"n": 5724, "n_nonzero": 1311, "share": 0.22904}, "2025": {"n": 5763, "n_nonzero": 1457, "share": 0.25282}, "2026": {"n": 5744, "n_nonzero": 1540, "share": 0.26811}}
- adjustment: {"mean": 0.4492, "sd": 1.5712, "mean_abs": 1.1229, "p99_abs": 5.4873}
- spread_corr_delta_vs_v7: -0.00037 95% [-0.01374, +0.01319] K-adj [-0.02026, +0.02049] 2025+2026 -0.00350
- margin_rmse_delta_vs_v7: -0.10100 95% [-0.14018, -0.06268] K-adj - 2025+2026 -
- cover_logloss_delta_vs_v7: -0.00632 95% [-0.00904, -0.00351] K-adj [-0.01057, -0.00221] 2025+2026 -0.00417
