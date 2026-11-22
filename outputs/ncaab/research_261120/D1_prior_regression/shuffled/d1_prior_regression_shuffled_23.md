# NCAAB closing-line scorecard (development, protocol `ncaab-research-protocol-261120`)

Protocol sha256 `72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813`. Seasons [2023, 2024, 2025, 2026] (leave-one-season-out). Cohort: 22947 D1-vs-D1 games with a closing spread line, identical for every row (by season {2023: 5716, 2024: 5724, 2025: 5763, 2026: 5744}; 16 unlined D1 games not scored). Points and probabilities only. Line source `espn_archive` (ncaab-espn-lines-rule-1, sha256 `3386b8d1f1f8d50f...`).

> PLUMBING RUN ON SHUFFLED OUTCOMES. No number below is evidence of anything.

## Market-relative (primary): (actual - closing line) on (model - closing line)

| model | n | slope | 95% CI | corr | 95% CI | rule-1 MSE delta | K-adj CI (K 21) | edge-eligible |
|---|---|---|---|---|---|---|---|---|
| cand | 22947 | +0.885 | [+0.761, +1.010] | +0.1073 | [+0.0931, +0.1216] | +3.1205 | [+2.2268, +4.0328] | yes |
| v7_served | 22947 | +1.155 | [+1.042, +1.267] | +0.1421 | [+0.1268, +0.1574] | +5.4931 | [+4.2027, +6.9099] | yes |
| v6_asof | 22947 | +1.945 | [+1.873, +2.018] | +0.3469 | [+0.3313, +0.3622] | +33.9552 | [+29.1221, +39.3455] | yes |
| closing_line | 22947 | N/A | - | N/A | - | N/A | - | - |

## Margin RMSE / bias (actual - predicted) by games played

| model | all | 0 | 1-2 | 3-7 | 8+ |
|---|---|---|---|---|---|
| cand | 19.88 / +2.94 (n 22947) | 22.48 / -8.10 (n 589) | 20.43 / -2.42 (n 1359) | 20.30 / +1.81 (n 3537) | 19.65 / +3.96 (n 17462) |
| v7_served | 19.79 / +2.92 (n 22947) | 21.75 / -7.24 (n 589) | 20.12 / -2.16 (n 1359) | 20.19 / +1.81 (n 3537) | 19.61 / +3.88 (n 17462) |
| v6_asof | 19.15 / +3.43 (n 22947) | 19.94 / -3.69 (n 589) | 18.83 / -0.12 (n 1359) | 19.33 / +2.83 (n 3537) | 19.11 / +4.07 (n 17462) |
| closing_line | 19.98 / +2.90 (n 22947) | 22.37 / -7.82 (n 589) | 20.85 / -2.87 (n 1359) | 20.42 / +1.68 (n 3537) | 19.73 / +3.95 (n 17462) |

## Margin RMSE / bias by |closing line|

| model | <5 | 5-10 | 10-20 | 20+ |
|---|---|---|---|---|
| cand | 19.04 / +7.00 (n 9267) | 19.24 / +4.95 (n 7263) | 20.20 / -1.76 (n 4904) | 25.91 / -16.33 (n 1513) |
| v7_served | 19.00 / +6.88 (n 9267) | 19.21 / +4.83 (n 7263) | 20.13 / -1.71 (n 4904) | 25.34 / -15.56 (n 1513) |
| v6_asof | 18.82 / +6.47 (n 9267) | 18.91 / +4.84 (n 7263) | 19.33 / -0.31 (n 4904) | 21.54 / -9.82 (n 1513) |
| closing_line | 19.08 / +7.10 (n 9267) | 19.30 / +4.97 (n 7263) | 20.32 / -1.92 (n 4904) | 26.46 / -17.20 (n 1513) |

## Margin RMSE by held-out season

| model | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| cand | 19.18 / +2.96 (n 5716) | 19.38 / +2.59 (n 5724) | 20.00 / +3.22 (n 5763) | 20.89 / +2.98 (n 5744) |
| v7_served | 19.11 / +2.93 (n 5716) | 19.31 / +2.61 (n 5724) | 19.95 / +3.15 (n 5763) | 20.74 / +2.97 (n 5744) |
| v6_asof | 18.45 / +3.36 (n 5716) | 18.72 / +3.12 (n 5724) | 19.34 / +3.65 (n 5763) | 20.04 / +3.57 (n 5744) |
| closing_line | 19.27 / +3.12 (n 5716) | 19.41 / +2.63 (n 5724) | 20.13 / +2.98 (n 5763) | 21.06 / +2.85 (n 5744) |

## Cover probability at the closing line (pushes dropped)

| model | n | Brier | log loss | fav 0.5-0.6 n/p/hit | 0.6-0.7 | 0.7-0.8 | 0.8-1.0 |
|---|---|---|---|---|---|---|---|
| cand | 22839 | 0.2495 | 0.6924 | 17709 / 0.543 / 0.522 | 4516 / 0.634 / 0.569 | 564 / 0.734 / 0.628 | 50 / 0.831 / 0.740 |
| v7_served | 22839 | 0.2473 | 0.6877 | 17618 / 0.544 / 0.525 | 4579 / 0.635 / 0.590 | 559 / 0.734 / 0.674 | 83 / 0.831 / 0.807 |
| v6_asof | 22839 | 0.2315 | 0.6546 | 15175 / 0.546 / 0.562 | 6078 / 0.639 / 0.678 | 1285 / 0.737 / 0.809 | 301 / 0.839 / 0.894 |
| closing_line | N/A | - | - | - | - | - | - |

## Moneyline (home win), against the closing-spread-implied probability

| model | n | Brier | log loss | corr(win - p_line, p - p_line), 95% CI |
|---|---|---|---|---|
| cand | 22947 | 0.2692 | 0.7647 | -0.1662 [-0.1800, -0.1508] |
| v7_served | 22947 | 0.2688 | 0.7637 | -0.1633 [-0.1772, -0.1477] |
| v6_asof | 22947 | 0.2621 | 0.7375 | -0.0857 [-0.1018, -0.0678] |
| closing_line | 22947 | 0.2536 | 0.7102 | N/A |

p_line = 1 - Phi((0.5 - L) / sigma); sigma by held-out season (fit on the other seasons): {2023: 20.0142, 2024: 19.9468, 2025: 19.7228, 2026: 19.3913}.

## Totals (games with a closing total)

| model | RMSE / bias | slope, 95% CI | corr | rule-1 MSE delta, K-adj CI | over Brier / log loss |
|---|---|---|---|---|---|
| v7_served | 20.37 / -0.36 (n 22946) | +1.095 [+1.020, +1.169] | +0.2305 | +16.6352 [+14.1873, +19.0836] | 0.2411 / 0.6751 |
| v6_asof | 20.37 / -0.36 (n 22946) | +1.095 [+1.020, +1.169] | +0.2305 | +16.6352 [+14.1873, +19.0836] | 0.2411 / 0.6751 |
| closing_line | 20.98 / +1.49 (n 22946) | N/A | N/A | N/A | N/A |

v7_served totals are the served v6 total (v7 never serves totals), so they equal v6_asof.

## Candidate minus served v7 (identical games)

| statistic | delta | 95% CI | K-adj CI | K | 2025+2026 folds |
|---|---|---|---|---|---|
| spread_corr_delta_vs_v7 | -0.03474 | [-0.04272, -0.02736] | [-0.04739, -0.02330] | 21 | -0.03737 |
| margin_rmse_delta_vs_v7 | +0.08719 | [+0.07026, +0.10715] | - | - | - |
| cover_logloss_delta_vs_v7 | +0.00464 | [+0.00367, +0.00572] | [+0.00314, +0.00641] | 14 | 0.00523 |
| moneyline_logloss_delta_vs_v7 | +0.00096 | [+0.00069, +0.00125] | [+0.00055, +0.00139] | 14 | 0.00126 |
| moneyline_brier_delta_vs_v7 | +0.00031 | [+0.00022, +0.00040] | - | - | 0.00040 |

Candidate `D1 prior-season ridge regressed to conference mean (fitted r) as v7 ridge prior, F1 refit v1` grid `D1_prior_regression`; coverage {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags ['rmse_below_line season 2023: 19.1805 < 19.2686', 'rmse_below_line season 2024: 19.3832 < 19.407', 'rmse_below_line season 2025: 20.005 < 20.1332', 'rmse_below_line season 2026: 20.8862 < 21.0581'].
**Verdict: LEAK_SUSPECT**: ['rmse_below_line season 2023: 19.1805 < 19.2686', 'rmse_below_line season 2024: 19.3832 < 19.407', 'rmse_below_line season 2025: 20.005 < 20.1332', 'rmse_below_line season 2026: 20.8862 < 21.0581'] . Edge (rule-1, development): {'spread': True, 'total': None}.
