# P1_player_availability (addendum v4, development, spread)

- candidate: P1: v7 + b1*d(avail_top7_prev1) + b2*d(top7_minutes_share_out_3plus), LOSO OLS v1
- addendum v4 sha256 `ccb107fc6f0b5b525337f8e48e7c9717b27afe824b78f7ea6c354f354618490e`; K {'spread_market_relative': 25, 'total_market_relative': 26, 'cover_calibration': 16, 'moneyline': 16}
- read count for grid name: None; shuffled: True
- reproduction gate (v1 K 21): not run (shuffled)
- coverage by season: {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags: ['rmse_below_line season 2023: 19.0429 < 19.1186', 'rmse_below_line season 2024: 19.4671 < 19.4824', 'rmse_below_line season 2025: 19.992 < 20.1794', 'rmse_below_line season 2026: 20.9475 < 21.1861', 'market_corr season 2026: 0.16139']
- **verdict: LEAK_SUSPECT** ""
# NCAAB closing-line scorecard (development, protocol `ncaab-research-protocol-261120`)

Protocol sha256 `72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813`. Seasons [2023, 2024, 2025, 2026] (leave-one-season-out). Cohort: 22947 D1-vs-D1 games with a closing spread line, identical for every row (by season {2023: 5716, 2024: 5724, 2025: 5763, 2026: 5744}; 16 unlined D1 games not scored). Points and probabilities only. Line source `espn_archive` (ncaab-espn-lines-rule-1, sha256 `3386b8d1f1f8d50f...`).

> PLUMBING RUN ON SHUFFLED OUTCOMES. No number below is evidence of anything.

## Market-relative (primary): (actual - closing line) on (model - closing line)

| model | n | slope | 95% CI | corr | 95% CI | rule-1 MSE delta | K-adj CI (K 25) | edge-eligible |
|---|---|---|---|---|---|---|---|---|
| cand | 22947 | +0.956 | [+0.831, +1.081] | +0.1172 | [+0.1004, +0.1336] | +3.6203 | [+2.4410, +4.7546] | yes |
| v7_served | 22947 | +1.111 | [+0.991, +1.232] | +0.1366 | [+0.1205, +0.1526] | +4.9857 | [+3.6859, +6.2727] | yes |
| v6_asof | 22947 | +1.973 | [+1.901, +2.051] | +0.3514 | [+0.3357, +0.3673] | +34.8401 | [+30.1539, +40.1000] | yes |
| closing_line | 22947 | N/A | - | N/A | - | N/A | - | - |

## Margin RMSE / bias (actual - predicted) by games played

| model | all | 0 | 1-2 | 3-7 | 8+ |
|---|---|---|---|---|---|
| cand | 19.88 / +2.93 (n 22947) | 20.35 / -6.79 (n 589) | 20.40 / -1.20 (n 1359) | 20.09 / +1.52 (n 3537) | 19.77 / +3.87 (n 17462) |
| v7_served | 19.83 / +2.94 (n 22947) | 20.35 / -6.79 (n 589) | 20.40 / -1.20 (n 1359) | 20.07 / +1.53 (n 3537) | 19.72 / +3.87 (n 17462) |
| v6_asof | 19.16 / +3.45 (n 22947) | 18.45 / -3.24 (n 589) | 19.28 / +0.85 (n 1359) | 19.15 / +2.55 (n 3537) | 19.18 / +4.06 (n 17462) |
| closing_line | 20.01 / +2.92 (n 22947) | 20.99 / -7.37 (n 589) | 20.94 / -1.91 (n 1359) | 20.32 / +1.41 (n 3537) | 19.84 / +3.94 (n 17462) |

## Margin RMSE / bias by |closing line|

| model | <5 | 5-10 | 10-20 | 20+ |
|---|---|---|---|---|
| cand | 19.33 / +7.05 (n 9267) | 19.07 / +4.62 (n 7263) | 20.14 / -1.70 (n 4904) | 25.37 / -15.32 (n 1513) |
| v7_served | 19.30 / +7.00 (n 9267) | 19.02 / +4.62 (n 7263) | 20.06 / -1.62 (n 4904) | 25.31 / -15.23 (n 1513) |
| v6_asof | 19.11 / +6.58 (n 9267) | 18.69 / +4.63 (n 7263) | 19.20 / -0.22 (n 4904) | 21.42 / -9.50 (n 1513) |
| closing_line | 19.35 / +7.21 (n 9267) | 19.17 / +4.76 (n 7263) | 20.25 / -1.82 (n 4904) | 26.20 / -16.88 (n 1513) |

## Margin RMSE by held-out season

| model | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| cand | 19.04 / +2.93 (n 5716) | 19.47 / +2.62 (n 5724) | 19.99 / +3.14 (n 5763) | 20.95 / +3.05 (n 5744) |
| v7_served | 19.02 / +2.94 (n 5716) | 19.41 / +2.62 (n 5724) | 19.93 / +3.15 (n 5763) | 20.90 / +3.04 (n 5744) |
| v6_asof | 18.36 / +3.37 (n 5716) | 18.78 / +3.13 (n 5724) | 19.34 / +3.65 (n 5763) | 20.11 / +3.65 (n 5744) |
| closing_line | 19.12 / +3.12 (n 5716) | 19.48 / +2.64 (n 5724) | 20.18 / +2.98 (n 5763) | 21.19 / +2.93 (n 5744) |

## Cover probability at the closing line (pushes dropped)

| model | n | Brier | log loss | fav 0.5-0.6 n/p/hit | 0.6-0.7 | 0.7-0.8 | 0.8-1.0 |
|---|---|---|---|---|---|---|---|
| cand | 22846 | 0.2496 | 0.6926 | 17664 / 0.543 / 0.520 | 4541 / 0.635 / 0.562 | 556 / 0.733 / 0.642 | 85 / 0.831 / 0.824 |
| v7_served | 22846 | 0.2481 | 0.6895 | 17627 / 0.544 / 0.524 | 4575 / 0.635 / 0.583 | 561 / 0.734 / 0.661 | 83 / 0.831 / 0.819 |
| v6_asof | 22846 | 0.2313 | 0.6542 | 15186 / 0.546 / 0.559 | 6071 / 0.639 / 0.679 | 1288 / 0.737 / 0.804 | 301 / 0.839 / 0.887 |
| closing_line | N/A | - | - | - | - | - | - |

## Moneyline (home win), against the closing-spread-implied probability

| model | n | Brier | log loss | corr(win - p_line, p - p_line), 95% CI |
|---|---|---|---|---|
| cand | 22947 | 0.2691 | 0.7650 | -0.1671 [-0.1805, -0.1539] |
| v7_served | 22947 | 0.2686 | 0.7636 | -0.1648 [-0.1781, -0.1517] |
| v6_asof | 22947 | 0.2619 | 0.7371 | -0.0878 [-0.1044, -0.0712] |
| closing_line | 22947 | 0.2533 | 0.7097 | N/A |

p_line = 1 - Phi((0.5 - L) / sigma); sigma by held-out season (fit on the other seasons): {2023: 20.0951, 2024: 19.9556, 2025: 19.7404, 2026: 19.3827}.

## Totals (games with a closing total)

| model | RMSE / bias | slope, 95% CI | corr | rule-1 MSE delta, K-adj CI | over Brier / log loss |
|---|---|---|---|---|---|
| v7_served | 20.61 / -0.30 (n 22946) | +1.063 [+1.000, +1.130] | +0.2216 | +15.8625 [+13.8477, +17.9718] | 0.2410 / 0.6750 |
| v6_asof | 20.61 / -0.30 (n 22946) | +1.063 [+1.000, +1.130] | +0.2216 | +15.8625 [+13.8477, +17.9718] | 0.2410 / 0.6750 |
| closing_line | 21.19 / +1.55 (n 22946) | N/A | N/A | N/A | N/A |

v7_served totals are the served v6 total (v7 never serves totals), so they equal v6_asof.

## Candidate minus served v7 (identical games)

| statistic | delta | 95% CI | K-adj CI | K | 2025+2026 folds |
|---|---|---|---|---|---|
| spread_corr_delta_vs_v7 | -0.01940 | [-0.02346, -0.01553] | [-0.02621, -0.01357] | 25 | -0.02167 |
| margin_rmse_delta_vs_v7 | +0.04641 | [+0.03747, +0.05538] | - | - | - |
| cover_logloss_delta_vs_v7 | +0.00316 | [+0.00249, +0.00383] | [+0.00217, +0.00417] | 16 | 0.00376 |
| moneyline_logloss_delta_vs_v7 | +0.00144 | [+0.00111, +0.00178] | [+0.00091, +0.00196] | 16 | 0.00167 |
| moneyline_brier_delta_vs_v7 | +0.00048 | [+0.00035, +0.00062] | - | - | 0.00056 |

Candidate `P1: v7 + b1*d(avail_top7_prev1) + b2*d(top7_minutes_share_out_3plus), LOSO OLS v1` grid `P1_player_availability`; coverage {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags ['rmse_below_line season 2023: 19.0429 < 19.1186', 'rmse_below_line season 2024: 19.4671 < 19.4824', 'rmse_below_line season 2025: 19.992 < 20.1794', 'rmse_below_line season 2026: 20.9475 < 21.1861', 'market_corr season 2026: 0.16139'].
**Verdict: LEAK_SUSPECT**: ['rmse_below_line season 2023: 19.0429 < 19.1186', 'rmse_below_line season 2024: 19.4671 < 19.4824', 'rmse_below_line season 2025: 19.992 < 20.1794', 'rmse_below_line season 2026: 20.9475 < 21.1861', 'market_corr season 2026: 0.16139'] . Edge (rule-1, development): {'spread': True}.

## Adjustment non-zero subset (no verdict)

- n non-zero 10157 (44.3% of the scored cohort); by season {"2023": {"n": 5716, "n_nonzero": 2305, "share": 0.40325}, "2024": {"n": 5724, "n_nonzero": 2444, "share": 0.42697}, "2025": {"n": 5763, "n_nonzero": 2656, "share": 0.46087}, "2026": {"n": 5744, "n_nonzero": 2752, "share": 0.47911}}
- adjustment: {"mean": 0.0092, "sd": 0.9768, "mean_abs": 0.8424, "p99_abs": 2.4384}
- spread_corr_delta_vs_v7: -0.05084 95% [-0.06021, -0.04143] K-adj [-0.06603, -0.03678] 2025+2026 -0.05402
- margin_rmse_delta_vs_v7: +0.10491 95% [+0.08424, +0.12482] K-adj - 2025+2026 -
- cover_logloss_delta_vs_v7: +0.00713 95% [+0.00561, +0.00863] K-adj [+0.00481, +0.00940] 2025+2026 0.00799
