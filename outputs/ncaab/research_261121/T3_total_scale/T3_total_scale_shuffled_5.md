# T3_total_scale (addendum v3, development, totals)

- candidate: T3 v6 + training median residual, normal sd s0 sqrt(poss/68)(1 + c1 early) v1
- addendum v3 sha256 `d098238e340f192eb61339a9f936a96e84f49fc00b5e944b9c312dc323b1b337`; K (totals) = 26
- read count for grid name: None; shuffled: True
- coverage by season: {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags: ['rmse_below_line season 2023: 20.6437 < 21.4126', 'rmse_below_line season 2024: 20.2774 < 20.8419', 'rmse_below_line season 2025: 20.1681 < 20.595', 'rmse_below_line season 2026: 21.5195 < 21.876', 'market_corr season 2023: 0.25254', 'market_corr season 2024: 0.22417', 'market_corr season 2025: 0.20221', 'market_corr season 2026: 0.2155']
- **verdict: LEAK_SUSPECT** ""

| model | RMSE / bias (n) | slope [95%] | corr [95%] {K-adj} | rule-1 MSE delta {K-adj} | over Brier / LL |
|---|---|---|---|---|---|
| cand | 20.66 / 1.35 (22946) | 1.042 [+0.979, +1.108] | 0.2197 [+0.207, +0.233] {[+0.200, +0.240]} | 15.5687 {[+13.569, +17.695]} | 0.2415 / 0.6760 |
| v6_served | 20.61 / -0.30 (22946) | 1.063 [+1.000, +1.130] | 0.2216 [+0.209, +0.235] {[+0.202, +0.242]} | 15.8625 {[+13.848, +17.972]} | 0.2410 / 0.6750 |
| closing_total | 21.19 / 1.55 (22946) | N/A | N/A | N/A | N/A |

## Paired vs served v6 total (identical games)

- total_corr_delta_vs_v6: {"delta": -0.00193, "ci95": [-0.0026, -0.00122], "ci_adj": [-0.00298, -0.00088], "k": 26, "one_sided_95_lower": -0.00249, "late_folds_point": -0.00142}
- total_rmse_delta_vs_v6: {"delta": 0.0501, "ci95": [0.02589, 0.07541], "ci_adj": [0.01468, 0.08842], "k": 26}
- over_logloss_delta_vs_v6: {"delta": 0.000986, "n": 22869, "ci95": [-0.00015, 0.00215], "ci_adj": [-0.0009, 0.00296], "k": 26, "late_folds_point": 0.003436, "one_sided_95_upper": 0.001975}
- over_logloss_vs_ln2: {"delta": -0.017196, "n": 22869, "ci95": [-0.0202, -0.0143], "ci_adj": [-0.02198, -0.01274], "k": 26, "late_folds_point": -0.012499, "one_sided_95_upper": -0.014727}
- over_brier_delta_vs_v6: {"delta": 0.00049, "n": 22869, "ci95": [-6e-05, 0.00105], "ci_adj": [-0.00041, 0.00144], "k": 26, "late_folds_point": 0.001691}
- mu_equals_v6: false

## RMSE / bias by season

- cand: {"2023": {"n": 5716, "rmse": 20.6437, "bias": 0.3628}, "2024": {"n": 5724, "rmse": 20.2774, "bias": 0.885}, "2025": {"n": 5762, "rmse": 20.1681, "bias": 1.3035}, "2026": {"n": 5744, "rmse": 21.5195, "bias": 2.8406}}
- v6_served: {"2023": {"n": 5716, "rmse": 20.6676, "bias": -1.0572}, "2024": {"n": 5724, "rmse": 20.27, "bias": -0.695}, "2025": {"n": 5762, "rmse": 20.1285, "bias": -0.3165}, "2026": {"n": 5744, "rmse": 21.3483, "bias": 0.8556}}
- closing_total: {"2023": {"n": 5716, "rmse": 21.4126, "bias": 2.0057}, "2024": {"n": 5724, "rmse": 20.8419, "bias": 1.596}, "2025": {"n": 5762, "rmse": 20.595, "bias": 1.3589}, "2026": {"n": 5744, "rmse": 21.876, "bias": 1.242}}

## RMSE / bias by games played

- cand: {"0": {"n": 588, "rmse": 20.4969, "bias": 2.8113}, "1-2": {"n": 1359, "rmse": 20.6357, "bias": -0.1046}, "3-7": {"n": 3537, "rmse": 20.127, "bias": -0.1098}, "8+": {"n": 17462, "rmse": 20.7723, "bias": 1.7091}}
- v6_served: {"0": {"n": 588, "rmse": 20.3113, "bias": 1.1619}, "1-2": {"n": 1359, "rmse": 20.6947, "bias": -1.7557}, "3-7": {"n": 3537, "rmse": 20.1934, "bias": -1.7612}, "8+": {"n": 17462, "rmse": 20.6953, "bias": 0.0574}}
- closing_total: {"0": {"n": 588, "rmse": 20.2399, "bias": 0.6964}, "1-2": {"n": 1359, "rmse": 21.4755, "bias": -0.4097}, "3-7": {"n": 3537, "rmse": 20.9249, "bias": 1.1204}, "8+": {"n": 17462, "rmse": 21.248, "bias": 1.8181}}
