# NCAAB closing-line scorecard (development, protocol `ncaab-research-protocol-261120`)

Protocol sha256 `72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813`. Seasons [2023, 2024, 2025, 2026] (leave-one-season-out). Cohort: 22947 D1-vs-D1 games with a closing spread line, identical for every row (by season {2023: 5716, 2024: 5724, 2025: 5763, 2026: 5744}; 16 unlined D1 games not scored). Points and probabilities only. Line source `espn_archive` (ncaab-espn-lines-rule-1, sha256 `3386b8d1f1f8d50f...`).

> PLUMBING RUN ON SHUFFLED OUTCOMES. No number below is evidence of anything.

## Market-relative (primary): (actual - closing line) on (model - closing line)

| model | n | slope | 95% CI | corr | 95% CI | rule-1 MSE delta | K-adj CI (K 21) | edge-eligible |
|---|---|---|---|---|---|---|---|---|
| cand | 22947 | +1.392 | [+1.270, +1.507] | +0.1813 | [+0.1616, +0.2008] | +9.0318 | [+6.7752, +11.6203] | yes |
| v7_served | 22947 | +1.111 | [+0.991, +1.232] | +0.1366 | [+0.1205, +0.1526] | +4.9857 | [+3.6907, +6.2472] | yes |
| v6_asof | 22947 | +1.973 | [+1.901, +2.051] | +0.3514 | [+0.3357, +0.3673] | +34.8401 | [+30.1651, +39.9900] | yes |
| closing_line | 22947 | N/A | - | N/A | - | N/A | - | - |

## Margin RMSE / bias (actual - predicted) by games played

| model | all | 0 | 1-2 | 3-7 | 8+ |
|---|---|---|---|---|---|
| cand | 19.71 / +2.92 (n 22947) | 19.27 / -5.56 (n 589) | 20.19 / -1.73 (n 1359) | 19.90 / +1.43 (n 3537) | 19.65 / +3.87 (n 17462) |
| v7_served | 19.83 / +2.94 (n 22947) | 20.35 / -6.79 (n 589) | 20.40 / -1.20 (n 1359) | 20.07 / +1.53 (n 3537) | 19.72 / +3.87 (n 17462) |
| v6_asof | 19.16 / +3.45 (n 22947) | 18.45 / -3.24 (n 589) | 19.28 / +0.85 (n 1359) | 19.15 / +2.55 (n 3537) | 19.18 / +4.06 (n 17462) |
| closing_line | 20.01 / +2.92 (n 22947) | 20.99 / -7.37 (n 589) | 20.94 / -1.91 (n 1359) | 20.32 / +1.41 (n 3537) | 19.84 / +3.94 (n 17462) |

## Margin RMSE / bias by |closing line|

| model | <5 | 5-10 | 10-20 | 20+ |
|---|---|---|---|---|
| cand | 19.25 / +6.87 (n 9267) | 19.02 / +4.44 (n 7263) | 20.06 / -1.64 (n 4904) | 24.12 / -13.74 (n 1513) |
| v7_served | 19.30 / +7.00 (n 9267) | 19.02 / +4.62 (n 7263) | 20.06 / -1.62 (n 4904) | 25.31 / -15.23 (n 1513) |
| v6_asof | 19.11 / +6.58 (n 9267) | 18.69 / +4.63 (n 7263) | 19.20 / -0.22 (n 4904) | 21.42 / -9.50 (n 1513) |
| closing_line | 19.35 / +7.21 (n 9267) | 19.17 / +4.76 (n 7263) | 20.25 / -1.82 (n 4904) | 26.20 / -16.88 (n 1513) |

## Margin RMSE by held-out season

| model | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| cand | 18.83 / +2.88 (n 5716) | 19.34 / +2.55 (n 5724) | 19.84 / +3.17 (n 5763) | 20.77 / +3.08 (n 5744) |
| v7_served | 19.02 / +2.94 (n 5716) | 19.41 / +2.62 (n 5724) | 19.93 / +3.15 (n 5763) | 20.90 / +3.04 (n 5744) |
| v6_asof | 18.36 / +3.37 (n 5716) | 18.78 / +3.13 (n 5724) | 19.34 / +3.65 (n 5763) | 20.11 / +3.65 (n 5744) |
| closing_line | 19.12 / +3.12 (n 5716) | 19.48 / +2.64 (n 5724) | 20.18 / +2.98 (n 5763) | 21.19 / +2.93 (n 5744) |

## Cover probability at the closing line (pushes dropped)

| model | n | Brier | log loss | fav 0.5-0.6 n/p/hit | 0.6-0.7 | 0.7-0.8 | 0.8-1.0 |
|---|---|---|---|---|---|---|---|
| cand | 22846 | 0.2452 | 0.6835 | 17338 / 0.543 / 0.531 | 4675 / 0.635 / 0.607 | 737 / 0.735 / 0.704 | 96 / 0.835 / 0.802 |
| v7_served | 22846 | 0.2481 | 0.6895 | 17627 / 0.544 / 0.524 | 4575 / 0.635 / 0.583 | 561 / 0.734 / 0.661 | 83 / 0.831 / 0.819 |
| v6_asof | 22846 | 0.2313 | 0.6542 | 15186 / 0.546 / 0.559 | 6071 / 0.639 / 0.679 | 1288 / 0.737 / 0.804 | 301 / 0.839 / 0.887 |
| closing_line | N/A | - | - | - | - | - | - |

## Moneyline (home win), against the closing-spread-implied probability

| model | n | Brier | log loss | corr(win - p_line, p - p_line), 95% CI |
|---|---|---|---|---|
| cand | 22947 | 0.2681 | 0.7606 | -0.1575 [-0.1711, -0.1437] |
| v7_served | 22947 | 0.2686 | 0.7636 | -0.1648 [-0.1781, -0.1517] |
| v6_asof | 22947 | 0.2619 | 0.7371 | -0.0878 [-0.1044, -0.0712] |
| closing_line | 22947 | 0.2533 | 0.7097 | N/A |

p_line = 1 - Phi((0.5 - L) / sigma); sigma by held-out season (fit on the other seasons): {2023: 20.0951, 2024: 19.9556, 2025: 19.7404, 2026: 19.3827}.

## Totals (games with a closing total)

| model | RMSE / bias | slope, 95% CI | corr | rule-1 MSE delta, K-adj CI | over Brier / log loss |
|---|---|---|---|---|---|
| cand | 21.43 / +0.58 (n 22946) | +0.068 [-0.023, +0.157] | +0.0124 | -0.2756 [-0.4726, -0.0545] | 0.2556 / 0.7051 |
| v7_served | 20.61 / -0.30 (n 22946) | +1.063 [+1.000, +1.130] | +0.2216 | +15.8625 [+13.8496, +17.9113] | 0.2410 / 0.6750 |
| v6_asof | 20.61 / -0.30 (n 22946) | +1.063 [+1.000, +1.130] | +0.2216 | +15.8625 [+13.8496, +17.9113] | 0.2410 / 0.6750 |
| closing_line | 21.19 / +1.55 (n 22946) | N/A | N/A | N/A | N/A |

v7_served totals are the served v6 total (v7 never serves totals), so they equal v6_asof.

## Candidate minus served v7 (identical games)

| statistic | delta | 95% CI | K-adj CI | K | 2025+2026 folds |
|---|---|---|---|---|---|
| spread_corr_delta_vs_v7 | +0.04473 | [+0.03136, +0.05928] | [+0.02497, +0.06794] | 21 | 0.03858 |
| margin_rmse_delta_vs_v7 | -0.11934 | [-0.15628, -0.08756] | - | - | - |
| cover_logloss_delta_vs_v7 | -0.00598 | [-0.00821, -0.00408] | [-0.00948, -0.00333] | 14 | -0.00392 |
| moneyline_logloss_delta_vs_v7 | -0.00295 | [-0.00452, -0.00170] | [-0.00542, -0.00123] | 14 | -0.00171 |
| moneyline_brier_delta_vs_v7 | -0.00048 | [-0.00074, -0.00023] | - | - | -0.00005 |

Candidate `C1 opponent-adjusted PPP ridge x adjusted tempo v1` grid `C1_ppp_adjusted`; coverage {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags ['rmse_below_line season 2023: 18.8343 < 19.1186', 'rmse_below_line season 2024: 19.3365 < 19.4824', 'rmse_below_line season 2025: 19.8444 < 20.1794', 'rmse_below_line season 2026: 20.7703 < 21.1861', 'market_corr season 2023: 0.16613', 'market_corr season 2025: 0.20581', 'market_corr season 2026: 0.22431'].
**Verdict: LEAK_SUSPECT**: ['rmse_below_line season 2023: 18.8343 < 19.1186', 'rmse_below_line season 2024: 19.3365 < 19.4824', 'rmse_below_line season 2025: 19.8444 < 20.1794', 'rmse_below_line season 2026: 20.7703 < 21.1861', 'market_corr season 2023: 0.16613', 'market_corr season 2025: 0.20581', 'market_corr season 2026: 0.22431'] . Edge (rule-1, development): {'spread': True, 'total': False}.
