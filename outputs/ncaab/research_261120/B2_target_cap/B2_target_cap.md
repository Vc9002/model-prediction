# NCAAB closing-line scorecard (development, protocol `ncaab-research-protocol-261120`)

Protocol sha256 `72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813`. Seasons [2023, 2024, 2025, 2026] (leave-one-season-out). Cohort: 22947 D1-vs-D1 games with a closing spread line, identical for every row (by season {2023: 5716, 2024: 5724, 2025: 5763, 2026: 5744}; 16 unlined D1 games not scored). Points and probabilities only. Line source `espn_archive` (ncaab-espn-lines-rule-1, sha256 `3386b8d1f1f8d50f...`).

## Market-relative (primary): (actual - closing line) on (model - closing line)

| model | n | slope | 95% CI | corr | 95% CI | rule-1 MSE delta | K-adj CI (K 21) | edge-eligible |
|---|---|---|---|---|---|---|---|---|
| cand | 22947 | -0.025 | [-0.080, +0.030] | -0.0064 | [-0.0207, +0.0079] | -0.0050 | [-0.0258, +0.0137] | no |
| v7_served | 22947 | -0.015 | [-0.078, +0.049] | -0.0033 | [-0.0170, +0.0107] | -0.0108 | [-0.0273, +0.0050] | no |
| v6_asof | 22947 | -0.002 | [-0.051, +0.047] | -0.0008 | [-0.0163, +0.0146] | -0.0080 | [-0.0204, +0.0037] | no |
| closing_line | 22947 | N/A | - | N/A | - | N/A | - | - |

## Margin RMSE / bias (actual - predicted) by games played

| model | all | 0 | 1-2 | 3-7 | 8+ |
|---|---|---|---|---|---|
| cand | 11.60 / +0.71 (n 22947) | 14.08 / +4.63 (n 589) | 12.56 / +1.96 (n 1359) | 12.37 / +1.27 (n 3537) | 11.26 / +0.37 (n 17462) |
| v7_served | 11.46 / +0.10 (n 22947) | 13.34 / +2.45 (n 589) | 12.25 / +0.60 (n 1359) | 12.14 / +0.41 (n 3537) | 11.18 / -0.07 (n 17462) |
| v6_asof | 11.75 / +0.62 (n 22947) | 15.11 / +6.01 (n 589) | 12.93 / +2.64 (n 1359) | 12.45 / +1.43 (n 3537) | 11.37 / +0.12 (n 17462) |
| closing_line | 11.19 / +0.08 (n 22947) | 12.22 / +1.87 (n 589) | 11.68 / -0.11 (n 1359) | 11.75 / +0.29 (n 3537) | 10.99 / -0.00 (n 17462) |

## Margin RMSE / bias by |closing line|

| model | <5 | 5-10 | 10-20 | 20+ |
|---|---|---|---|---|
| cand | 11.21 / -0.21 (n 9267) | 11.38 / +0.20 (n 7263) | 11.62 / +1.33 (n 4904) | 14.51 / +6.87 (n 1513) |
| v7_served | 11.24 / -0.26 (n 9267) | 11.37 / +0.01 (n 7263) | 11.54 / +0.37 (n 4904) | 12.87 / +1.98 (n 1513) |
| v6_asof | 11.23 / -0.68 (n 9267) | 11.48 / +0.02 (n 7263) | 11.93 / +1.77 (n 4904) | 15.10 / +7.72 (n 1513) |
| closing_line | 11.03 / -0.05 (n 9267) | 11.14 / +0.15 (n 7263) | 11.20 / +0.16 (n 4904) | 12.28 / +0.34 (n 1513) |

## Margin RMSE by held-out season

| model | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| cand | 11.41 / +0.76 (n 5716) | 11.51 / +0.52 (n 5724) | 11.67 / +0.97 (n 5763) | 11.80 / +0.60 (n 5744) |
| v7_served | 11.34 / +0.22 (n 5716) | 11.39 / -0.05 (n 5724) | 11.50 / +0.31 (n 5763) | 11.60 / -0.07 (n 5744) |
| v6_asof | 11.54 / +0.65 (n 5716) | 11.65 / +0.47 (n 5724) | 11.81 / +0.81 (n 5763) | 11.99 / +0.54 (n 5744) |
| closing_line | 11.12 / +0.41 (n 5716) | 11.17 / -0.02 (n 5724) | 11.19 / +0.14 (n 5763) | 11.27 / -0.18 (n 5744) |

## Cover probability at the closing line (pushes dropped)

| model | n | Brier | log loss | fav 0.5-0.6 n/p/hit | 0.6-0.7 | 0.7-0.8 | 0.8-1.0 |
|---|---|---|---|---|---|---|---|
| cand | 22787 | 0.2625 | 0.7223 | 15724 / 0.545 / 0.496 | 5313 / 0.637 / 0.502 | 1227 / 0.740 / 0.507 | 523 / 0.855 / 0.505 |
| v7_served | 22787 | 0.2578 | 0.7099 | 17569 / 0.544 / 0.501 | 4576 / 0.635 / 0.499 | 561 / 0.734 / 0.490 | 81 / 0.832 / 0.469 |
| v6_asof | 22787 | 0.2616 | 0.7189 | 15140 / 0.546 / 0.503 | 6065 / 0.639 / 0.496 | 1281 / 0.737 / 0.523 | 301 / 0.839 / 0.468 |
| closing_line | N/A | - | - | - | - | - | - |

## Moneyline (home win), against the closing-spread-implied probability

| model | n | Brier | log loss | corr(win - p_line, p - p_line), 95% CI |
|---|---|---|---|---|
| cand | 22947 | 0.1823 | 0.5389 | +0.0022 [-0.0116, +0.0164] |
| v7_served | 22947 | 0.1822 | 0.5386 | +0.0022 [-0.0114, +0.0164] |
| v6_asof | 22947 | 0.1849 | 0.5466 | +0.0060 [-0.0078, +0.0200] |
| closing_line | 22947 | 0.1769 | 0.5238 | N/A |

p_line = 1 - Phi((0.5 - L) / sigma); sigma by held-out season (fit on the other seasons): {2023: 11.2108, 2024: 11.1934, 2025: 11.1884, 2026: 11.1598}.

## Totals (games with a closing total)

| model | RMSE / bias | slope, 95% CI | corr | rule-1 MSE delta, K-adj CI | over Brier / log loss |
|---|---|---|---|---|---|
| v7_served | 17.03 / -1.02 (n 22946) | +0.068 [+0.017, +0.118] | +0.0181 | +0.0539 [-0.0556, +0.1500] | 0.2584 / 0.7110 |
| v6_asof | 17.03 / -1.02 (n 22946) | +0.068 [+0.017, +0.118] | +0.0181 | +0.0539 [-0.0556, +0.1500] | 0.2584 / 0.7110 |
| closing_line | 16.52 / +0.83 (n 22946) | N/A | N/A | N/A | N/A |

v7_served totals are the served v6 total (v7 never serves totals), so they equal v6_asof.

## Candidate minus served v7 (identical games)

| statistic | delta | 95% CI | K-adj CI | K | 2025+2026 folds |
|---|---|---|---|---|---|
| spread_corr_delta_vs_v7 | -0.00303 | [-0.01080, +0.00456] | [-0.01593, +0.00892] | 21 | 0.00072 |
| margin_rmse_delta_vs_v7 | +0.14046 | [+0.11197, +0.17143] | - | - | - |
| cover_logloss_delta_vs_v7 | +0.01242 | [+0.00961, +0.01552] | [+0.00825, +0.01697] | 14 | 0.01481 |
| moneyline_logloss_delta_vs_v7 | +0.00023 | [+0.00007, +0.00040] | [-0.00002, +0.00046] | 14 | 0.00039 |
| moneyline_brier_delta_vs_v7 | +0.00000 | [-0.00004, +0.00004] | - | - | 0.00002 |

Candidate `B2: v7 F1 combiner + bucket t8 scales refit LOSO on margin clipped to +-25` grid `B2_target_cap`; coverage {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags none.
**Verdict: NOT_ELIGIBLE**: does not beat v7 under the pinned development rule {'corr_delta_adj_lower_gt_0': False, 'rmse_delta_point_le_0': False, 'late_folds_corr_delta_gt_0': True}. Edge (rule-1, development): {'spread': False, 'total': None}.
