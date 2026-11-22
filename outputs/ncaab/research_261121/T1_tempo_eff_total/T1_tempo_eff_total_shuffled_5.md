# T1_tempo_eff_total (addendum v3, development, totals)

- candidate: T1 C1 ppp x tempo per 40 + overtime expectation, variant by total RMSE v1
- addendum v3 sha256 `d098238e340f192eb61339a9f936a96e84f49fc00b5e944b9c312dc323b1b337`; K (totals) = 26
- read count for grid name: None; shuffled: True
- coverage by season: {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags: ['rmse_below_line season 2023: 20.9841 < 21.4126', 'rmse_below_line season 2024: 20.4304 < 20.8419', 'rmse_below_line season 2025: 20.1713 < 20.595', 'rmse_below_line season 2026: 21.238 < 21.876', 'market_corr season 2023: 0.19061', 'market_corr season 2024: 0.18971', 'market_corr season 2025: 0.19386', 'market_corr season 2026: 0.237']
- **verdict: LEAK_SUSPECT** ""

| model | RMSE / bias (n) | slope [95%] | corr [95%] {K-adj} | rule-1 MSE delta {K-adj} | over Brier / LL |
|---|---|---|---|---|---|
| cand | 20.71 / 0.45 (22946) | 1.223 [+1.047, +1.403] | 0.2030 [+0.184, +0.223] {[+0.172, +0.232]} | 13.2400 {[+10.779, +15.829]} | 0.2423 / 0.6776 |
| v6_served | 20.61 / -0.30 (22946) | 1.063 [+1.000, +1.130] | 0.2216 [+0.209, +0.235] {[+0.202, +0.242]} | 15.8625 {[+13.848, +17.972]} | 0.2410 / 0.6750 |
| closing_total | 21.19 / 1.55 (22946) | N/A | N/A | N/A | N/A |

## Paired vs served v6 total (identical games)

- total_corr_delta_vs_v6: {"delta": -0.01861, "ci95": [-0.03576, -0.00164], "ci_adj": [-0.0466, 0.00588], "k": 26, "one_sided_95_lower": -0.03287, "late_folds_point": 0.01029}
- total_rmse_delta_vs_v6: {"delta": 0.1009, "ci95": [0.04022, 0.16777], "ci_adj": [0.006, 0.20948], "k": 26}
- over_logloss_delta_vs_v6: {"delta": 0.002619, "n": 22869, "ci95": [-1e-05, 0.00544], "ci_adj": [-0.00138, 0.00672], "k": 26, "late_folds_point": -0.000581, "one_sided_95_upper": 0.004972}
- over_logloss_vs_ln2: {"delta": -0.015564, "n": 22869, "ci95": [-0.01823, -0.01287], "ci_adj": [-0.01972, -0.01133], "k": 26, "late_folds_point": -0.016515, "one_sided_95_upper": -0.013335}
- over_brier_delta_vs_v6: {"delta": 0.001301, "n": 22869, "ci95": [4e-05, 0.00264], "ci_adj": [-0.00063, 0.00326], "k": 26, "late_folds_point": -0.000265}
- mu_equals_v6: false

## RMSE / bias by season

- cand: {"2023": {"n": 5716, "rmse": 20.9841, "bias": 1.191}, "2024": {"n": 5724, "rmse": 20.4304, "bias": 0.6635}, "2025": {"n": 5762, "rmse": 20.1713, "bias": 0.166}, "2026": {"n": 5744, "rmse": 21.238, "bias": -0.2326}}
- v6_served: {"2023": {"n": 5716, "rmse": 20.6676, "bias": -1.0572}, "2024": {"n": 5724, "rmse": 20.27, "bias": -0.695}, "2025": {"n": 5762, "rmse": 20.1285, "bias": -0.3165}, "2026": {"n": 5744, "rmse": 21.3483, "bias": 0.8556}}
- closing_total: {"2023": {"n": 5716, "rmse": 21.4126, "bias": 2.0057}, "2024": {"n": 5724, "rmse": 20.8419, "bias": 1.596}, "2025": {"n": 5762, "rmse": 20.595, "bias": 1.3589}, "2026": {"n": 5744, "rmse": 21.876, "bias": 1.242}}

## RMSE / bias by games played

- cand: {"0": {"n": 588, "rmse": 20.6839, "bias": 5.7166}, "1-2": {"n": 1359, "rmse": 20.3293, "bias": -1.1822}, "3-7": {"n": 3537, "rmse": 20.0503, "bias": -0.5019}, "8+": {"n": 17462, "rmse": 20.8708, "bias": 0.5868}}
- v6_served: {"0": {"n": 588, "rmse": 20.3113, "bias": 1.1619}, "1-2": {"n": 1359, "rmse": 20.6947, "bias": -1.7557}, "3-7": {"n": 3537, "rmse": 20.1934, "bias": -1.7612}, "8+": {"n": 17462, "rmse": 20.6953, "bias": 0.0574}}
- closing_total: {"0": {"n": 588, "rmse": 20.2399, "bias": 0.6964}, "1-2": {"n": 1359, "rmse": 21.4755, "bias": -0.4097}, "3-7": {"n": 3537, "rmse": 20.9249, "bias": 1.1204}, "8+": {"n": 17462, "rmse": 21.248, "bias": 1.8181}}
