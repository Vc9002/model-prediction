# T1_tempo_eff_total (addendum v3, development, totals)

- candidate: T1 C1 ppp x tempo per 40 + overtime expectation, variant by total RMSE v1
- addendum v3 sha256 `d098238e340f192eb61339a9f936a96e84f49fc00b5e944b9c312dc323b1b337`; K (totals) = 26
- read count for grid name: 1; shuffled: False
- coverage by season: {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags: none
- **verdict: NOT_ELIGIBLE** {"corr_delta_adj_lower_gt_0": false, "rmse_delta_point_le_0": true, "late_folds_corr_delta_gt_0": false}

| model | RMSE / bias (n) | slope [95%] | corr [95%] {K-adj} | rule-1 MSE delta {K-adj} | over Brier / LL |
|---|---|---|---|---|---|
| cand | 16.94 / -0.27 (22946) | -0.103 [-0.174, -0.028] | -0.0220 [-0.038, -0.006] {[-0.047, +0.004]} | 0.0924 {[-0.057, +0.260]} | 0.2582 / 0.7106 |
| v6_served | 17.03 / -1.02 (22946) | 0.068 [+0.017, +0.118] | 0.0181 [+0.005, +0.031] {[-0.005, +0.039]} | 0.0539 {[-0.063, +0.152]} | 0.2584 / 0.7110 |
| closing_total | 16.52 / 0.83 (22946) | N/A | N/A | N/A | N/A |

## Paired vs served v6 total (identical games)

- total_corr_delta_vs_v6: {"delta": -0.04007, "ci95": [-0.05607, -0.02425], "ci_adj": [-0.06453, -0.0165], "k": 26, "one_sided_95_lower": -0.05298, "late_folds_point": -0.02821}
- total_rmse_delta_vs_v6: {"delta": -0.08507, "ci95": [-0.174, 0.0218], "ci_adj": [-0.22533, 0.09288], "k": 26}
- over_logloss_delta_vs_v6: {"delta": -0.000398, "n": 22868, "ci95": [-0.00392, 0.0036], "ci_adj": [-0.00596, 0.00627], "k": 26, "late_folds_point": 0.006265, "one_sided_95_upper": 0.002895}
- over_logloss_vs_ln2: {"delta": 0.017415, "n": 22868, "ci95": [0.0142, 0.02108], "ci_adj": [0.01277, 0.02389], "k": 26, "late_folds_point": 0.023034, "one_sided_95_upper": 0.020505}
- over_brier_delta_vs_v6: {"delta": -0.000129, "n": 22868, "ci95": [-0.00176, 0.00167], "ci_adj": [-0.0027, 0.00292], "k": 26, "late_folds_point": 0.002806}
- mu_equals_v6: false

## RMSE / bias by season

- cand: {"2023": {"n": 5716, "rmse": 16.7351, "bias": 0.3894}, "2024": {"n": 5724, "rmse": 16.8334, "bias": -0.0427}, "2025": {"n": 5762, "rmse": 16.6873, "bias": -0.5374}, "2026": {"n": 5744, "rmse": 17.5082, "bias": -0.8941}}
- v6_served: {"2023": {"n": 5716, "rmse": 17.1002, "bias": -1.8588}, "2024": {"n": 5724, "rmse": 16.8672, "bias": -1.4011}, "2025": {"n": 5762, "rmse": 16.7596, "bias": -1.0199}, "2026": {"n": 5744, "rmse": 17.3848, "bias": 0.194}}
- closing_total: {"2023": {"n": 5716, "rmse": 16.4527, "bias": 1.2041}, "2024": {"n": 5724, "rmse": 16.4898, "bias": 0.8898}, "2025": {"n": 5762, "rmse": 16.2345, "bias": 0.6555}, "2026": {"n": 5744, "rmse": 16.894, "bias": 0.5804}}

## RMSE / bias by games played

- cand: {"0": {"n": 588, "rmse": 20.062, "bias": 8.0635}, "1-2": {"n": 1359, "rmse": 17.7091, "bias": -0.4324}, "3-7": {"n": 3537, "rmse": 17.1794, "bias": -1.5381}, "8+": {"n": 17462, "rmse": 16.7195, "bias": -0.2843}}
- v6_served: {"0": {"n": 588, "rmse": 17.6747, "bias": 3.5088}, "1-2": {"n": 1359, "rmse": 18.1841, "bias": -1.0059}, "3-7": {"n": 3537, "rmse": 17.3508, "bias": -2.7974}, "8+": {"n": 17462, "rmse": 16.8478, "bias": -0.8137}}
- closing_total: {"0": {"n": 588, "rmse": 16.9268, "bias": 3.0434}, "1-2": {"n": 1359, "rmse": 17.0281, "bias": 0.3401}, "3-7": {"n": 3537, "rmse": 16.4864, "bias": 0.0842}, "8+": {"n": 17462, "rmse": 16.4718, "bias": 0.947}}
