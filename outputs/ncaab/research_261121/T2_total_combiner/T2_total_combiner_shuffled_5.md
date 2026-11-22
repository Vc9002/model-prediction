# T2_total_combiner (addendum v3, development, totals)

- candidate: T2 ridge [v6, T1, early x v6, early x T1, neutral, PIT league level] v1
- addendum v3 sha256 `d098238e340f192eb61339a9f936a96e84f49fc00b5e944b9c312dc323b1b337`; K (totals) = 26
- read count for grid name: None; shuffled: True
- coverage by season: {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags: ['rmse_below_line season 2023: 20.9494 < 21.4126', 'rmse_below_line season 2024: 20.509 < 20.8419', 'rmse_below_line season 2025: 20.3578 < 20.595', 'rmse_below_line season 2026: 21.5677 < 21.876', 'market_corr season 2023: 0.18916', 'market_corr season 2024: 0.167', 'market_corr season 2026: 0.17419']
- **verdict: LEAK_SUSPECT** ""

| model | RMSE / bias (n) | slope [95%] | corr [95%] {K-adj} | rule-1 MSE delta {K-adj} | over Brier / LL |
|---|---|---|---|---|---|
| cand | 20.85 / 0.75 (22946) | 1.074 [+0.953, +1.193] | 0.1662 [+0.151, +0.182] {[+0.140, +0.191]} | 8.9630 {[+7.109, +10.908]} | 0.2446 / 0.6822 |
| v6_served | 20.61 / -0.30 (22946) | 1.063 [+1.000, +1.130] | 0.2216 [+0.209, +0.235] {[+0.202, +0.242]} | 15.8625 {[+13.848, +17.972]} | 0.2410 / 0.6750 |
| closing_total | 21.19 / 1.55 (22946) | N/A | N/A | N/A | N/A |

## Paired vs served v6 total (identical games)

- total_corr_delta_vs_v6: {"delta": -0.05536, "ci95": [-0.0649, -0.04607], "ci_adj": [-0.07063, -0.04089], "k": 26, "one_sided_95_lower": -0.0634, "late_folds_point": -0.05078}
- total_rmse_delta_vs_v6: {"delta": 0.24217, "ci95": [0.20347, 0.28407], "ci_adj": [0.17993, 0.30668], "k": 26}
- over_logloss_delta_vs_v6: {"delta": 0.007259, "n": 22869, "ci95": [0.00558, 0.00905], "ci_adj": [0.00467, 0.00986], "k": 26, "late_folds_point": 0.007264, "one_sided_95_upper": 0.00873}
- over_logloss_vs_ln2: {"delta": -0.010924, "n": 22869, "ci95": [-0.01339, -0.00863], "ci_adj": [-0.01481, -0.00745], "k": 26, "late_folds_point": -0.008671, "one_sided_95_upper": -0.008982}
- over_brier_delta_vs_v6: {"delta": 0.003542, "n": 22869, "ci95": [0.00274, 0.00439], "ci_adj": [0.00229, 0.00478], "k": 26, "late_folds_point": 0.003563}
- mu_equals_v6: false

## RMSE / bias by season

- cand: {"2023": {"n": 5716, "rmse": 20.9494, "bias": 0.2118}, "2024": {"n": 5724, "rmse": 20.509, "bias": 0.6523}, "2025": {"n": 5762, "rmse": 20.3578, "bias": 0.5314}, "2026": {"n": 5744, "rmse": 21.5677, "bias": 1.6092}}
- v6_served: {"2023": {"n": 5716, "rmse": 20.6676, "bias": -1.0572}, "2024": {"n": 5724, "rmse": 20.27, "bias": -0.695}, "2025": {"n": 5762, "rmse": 20.1285, "bias": -0.3165}, "2026": {"n": 5744, "rmse": 21.3483, "bias": 0.8556}}
- closing_total: {"2023": {"n": 5716, "rmse": 21.4126, "bias": 2.0057}, "2024": {"n": 5724, "rmse": 20.8419, "bias": 1.596}, "2025": {"n": 5762, "rmse": 20.595, "bias": 1.3589}, "2026": {"n": 5744, "rmse": 21.876, "bias": 1.242}}

## RMSE / bias by games played

- cand: {"0": {"n": 588, "rmse": 20.498, "bias": 2.8346}, "1-2": {"n": 1359, "rmse": 20.6481, "bias": -1.8132}, "3-7": {"n": 3537, "rmse": 20.1838, "bias": -0.3643}, "8+": {"n": 17462, "rmse": 21.0109, "bias": 1.1073}}
- v6_served: {"0": {"n": 588, "rmse": 20.3113, "bias": 1.1619}, "1-2": {"n": 1359, "rmse": 20.6947, "bias": -1.7557}, "3-7": {"n": 3537, "rmse": 20.1934, "bias": -1.7612}, "8+": {"n": 17462, "rmse": 20.6953, "bias": 0.0574}}
- closing_total: {"0": {"n": 588, "rmse": 20.2399, "bias": 0.6964}, "1-2": {"n": 1359, "rmse": 21.4755, "bias": -0.4097}, "3-7": {"n": 3537, "rmse": 20.9249, "bias": 1.1204}, "8+": {"n": 17462, "rmse": 21.248, "bias": 1.8181}}
