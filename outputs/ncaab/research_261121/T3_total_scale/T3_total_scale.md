# T3_total_scale (addendum v3, development, totals)

- candidate: T3 v6 + training median residual, normal sd s0 sqrt(poss/68)(1 + c1 early) v1
- addendum v3 sha256 `d098238e340f192eb61339a9f936a96e84f49fc00b5e944b9c312dc323b1b337`; K (totals) = 26
- read count for grid name: 1; shuffled: False
- coverage by season: {2023: 1.0, 2024: 1.0, 2025: 1.0, 2026: 1.0}; leak flags: none
- **verdict: NOT_ELIGIBLE** {"over_logloss_delta_adj_upper_lt_0": false, "over_logloss_vs_ln2_adj_upper_lt_0": false, "late_folds_over_logloss_delta_lt_0": false, "over_brier_delta_point_le_0": false}

| model | RMSE / bias (n) | slope [95%] | corr [95%] {K-adj} | rule-1 MSE delta {K-adj} | over Brier / LL |
|---|---|---|---|---|---|
| cand | 17.02 / 0.63 (22946) | 0.068 [+0.019, +0.119] | 0.0184 [+0.005, +0.032] {[-0.005, +0.039]} | 0.0589 {[-0.063, +0.161]} | 0.2588 / 0.7122 |
| v6_served | 17.03 / -1.02 (22946) | 0.068 [+0.017, +0.118] | 0.0181 [+0.005, +0.031] {[-0.005, +0.039]} | 0.0539 {[-0.063, +0.152]} | 0.2584 / 0.7110 |
| closing_total | 16.52 / 0.83 (22946) | N/A | N/A | N/A | N/A |

## Paired vs served v6 total (identical games)

- total_corr_delta_vs_v6: {"delta": 0.00037, "ci95": [-0.00026, 0.00099], "ci_adj": [-0.00068, 0.00136], "k": 26, "one_sided_95_lower": -0.00015, "late_folds_point": 7e-05}
- total_rmse_delta_vs_v6: {"delta": -0.0084, "ci95": [-0.03683, 0.01989], "ci_adj": [-0.04998, 0.03175], "k": 26}
- over_logloss_delta_vs_v6: {"delta": 0.001209, "n": 22868, "ci95": [-4e-05, 0.00248], "ci_adj": [-0.00076, 0.00316], "k": 26, "late_folds_point": 0.002615, "one_sided_95_upper": 0.002281}
- over_logloss_vs_ln2: {"delta": 0.019022, "n": 22868, "ci95": [0.01604, 0.02208], "ci_adj": [0.01445, 0.02367], "k": 26, "late_folds_point": 0.019384, "one_sided_95_upper": 0.02157}
- over_brier_delta_vs_v6: {"delta": 0.000453, "n": 22868, "ci95": [-0.00011, 0.00102], "ci_adj": [-0.00045, 0.00136], "k": 26, "late_folds_point": 0.001026}
- mu_equals_v6: false

## RMSE / bias by season

- cand: {"2023": {"n": 5716, "rmse": 17.0046, "bias": -0.4388}, "2024": {"n": 5724, "rmse": 16.8098, "bias": 0.1789}, "2025": {"n": 5762, "rmse": 16.7393, "bias": 0.6001}, "2026": {"n": 5744, "rmse": 17.5197, "bias": 2.179}}
- v6_served: {"2023": {"n": 5716, "rmse": 17.1002, "bias": -1.8588}, "2024": {"n": 5724, "rmse": 16.8672, "bias": -1.4011}, "2025": {"n": 5762, "rmse": 16.7596, "bias": -1.0199}, "2026": {"n": 5744, "rmse": 17.3848, "bias": 0.194}}
- closing_total: {"2023": {"n": 5716, "rmse": 16.4527, "bias": 1.2041}, "2024": {"n": 5724, "rmse": 16.4898, "bias": 0.8898}, "2025": {"n": 5762, "rmse": 16.2345, "bias": 0.6555}, "2026": {"n": 5744, "rmse": 16.894, "bias": 0.5804}}

## RMSE / bias by games played

- cand: {"0": {"n": 588, "rmse": 18.0957, "bias": 5.1582}, "1-2": {"n": 1359, "rmse": 18.2027, "bias": 0.6453}, "3-7": {"n": 3537, "rmse": 17.1789, "bias": -1.146}, "8+": {"n": 17462, "rmse": 16.8557, "bias": 0.838}}
- v6_served: {"0": {"n": 588, "rmse": 17.6747, "bias": 3.5088}, "1-2": {"n": 1359, "rmse": 18.1841, "bias": -1.0059}, "3-7": {"n": 3537, "rmse": 17.3508, "bias": -2.7974}, "8+": {"n": 17462, "rmse": 16.8478, "bias": -0.8137}}
- closing_total: {"0": {"n": 588, "rmse": 16.9268, "bias": 3.0434}, "1-2": {"n": 1359, "rmse": 17.0281, "bias": 0.3401}, "3-7": {"n": 3537, "rmse": 16.4864, "bias": 0.0842}, "8+": {"n": 17462, "rmse": 16.4718, "bias": 0.947}}
