# T2_total_combiner (addendum v3, development, totals)

- candidate: T2 ridge [v6, T1, early x v6, early x T1, neutral, PIT league level] v1
- addendum v3 sha256 `d098238e340f192eb61339a9f936a96e84f49fc00b5e944b9c312dc323b1b337`; K (totals) = 26
- read count for grid name: 1; shuffled: False
- coverage by season: {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags: none
- **verdict: NOT_ELIGIBLE** {"corr_delta_adj_lower_gt_0": false, "rmse_delta_point_le_0": true, "late_folds_corr_delta_gt_0": false}

| model | RMSE / bias (n) | slope [95%] | corr [95%] {K-adj} | rule-1 MSE delta {K-adj} | over Brier / LL |
|---|---|---|---|---|---|
| cand | 16.82 / 0.03 (22946) | -0.006 [-0.080, +0.071] | -0.0011 [-0.016, +0.014] {[-0.027, +0.023]} | -0.0173 {[-0.045, +0.009]} | 0.2558 / 0.7055 |
| v6_served | 17.03 / -1.02 (22946) | 0.068 [+0.017, +0.118] | 0.0181 [+0.005, +0.031] {[-0.005, +0.039]} | 0.0539 {[-0.063, +0.152]} | 0.2584 / 0.7110 |
| closing_total | 16.52 / 0.83 (22946) | N/A | N/A | N/A | N/A |

## Paired vs served v6 total (identical games)

- total_corr_delta_vs_v6: {"delta": -0.01922, "ci95": [-0.02842, -0.01029], "ci_adj": [-0.03418, -0.00592], "k": 26, "one_sided_95_lower": -0.02684, "late_folds_point": -0.01705}
- total_rmse_delta_vs_v6: {"delta": -0.20619, "ci95": [-0.25406, -0.14968], "ci_adj": [-0.27777, -0.10895], "k": 26}
- over_logloss_delta_vs_v6: {"delta": -0.005481, "n": 22868, "ci95": [-0.00756, -0.00309], "ci_adj": [-0.00857, -0.00155], "k": 26, "late_folds_point": -0.002315, "one_sided_95_upper": -0.003516}
- over_logloss_vs_ln2: {"delta": 0.012332, "n": 22868, "ci95": [0.00967, 0.01513], "ci_adj": [0.00834, 0.01696], "k": 26, "late_folds_point": 0.014454, "one_sided_95_upper": 0.014762}
- over_brier_delta_vs_v6: {"delta": -0.002554, "n": 22868, "ci95": [-0.00352, -0.00148], "ci_adj": [-0.00397, -0.00086], "k": 26, "late_folds_point": -0.001185}
- mu_equals_v6: false

## RMSE / bias by season

- cand: {"2023": {"n": 5716, "rmse": 16.7357, "bias": -0.5899}, "2024": {"n": 5724, "rmse": 16.6753, "bias": -0.0538}, "2025": {"n": 5762, "rmse": 16.5615, "bias": -0.172}, "2026": {"n": 5744, "rmse": 17.3106, "bias": 0.9476}}
- v6_served: {"2023": {"n": 5716, "rmse": 17.1002, "bias": -1.8588}, "2024": {"n": 5724, "rmse": 16.8672, "bias": -1.4011}, "2025": {"n": 5762, "rmse": 16.7596, "bias": -1.0199}, "2026": {"n": 5744, "rmse": 17.3848, "bias": 0.194}}
- closing_total: {"2023": {"n": 5716, "rmse": 16.4527, "bias": 1.2041}, "2024": {"n": 5724, "rmse": 16.4898, "bias": 0.8898}, "2025": {"n": 5762, "rmse": 16.2345, "bias": 0.6555}, "2026": {"n": 5744, "rmse": 16.894, "bias": 0.5804}}

## RMSE / bias by games played

- cand: {"0": {"n": 588, "rmse": 18.4987, "bias": 5.1816}, "1-2": {"n": 1359, "rmse": 17.9633, "bias": -1.0633}, "3-7": {"n": 3537, "rmse": 17.055, "bias": -1.4005}, "8+": {"n": 17462, "rmse": 16.6239, "bias": 0.2362}}
- v6_served: {"0": {"n": 588, "rmse": 17.6747, "bias": 3.5088}, "1-2": {"n": 1359, "rmse": 18.1841, "bias": -1.0059}, "3-7": {"n": 3537, "rmse": 17.3508, "bias": -2.7974}, "8+": {"n": 17462, "rmse": 16.8478, "bias": -0.8137}}
- closing_total: {"0": {"n": 588, "rmse": 16.9268, "bias": 3.0434}, "1-2": {"n": 1359, "rmse": 17.0281, "bias": 0.3401}, "3-7": {"n": 3537, "rmse": 16.4864, "bias": 0.0842}, "8+": {"n": 17462, "rmse": 16.4718, "bias": 0.947}}
