# NBA v9.2 total probability shrink fit (quick-261123-h40)

Generated 2026-11-23T17:31:18.810341+00:00 by `scripts/nba_v10/fit_nba_v92_total_shrink.py`.

Engine: the v9.1 total research artifact (B5 + B6 + B7) at slope 1.0, replayed point-in-time by the NBA harness against the archived closing totals. Shrink: `p' = sigmoid(s * logit(p))`, intercept 0, s fitted on train only (slope-only logistic on logit(P(over)), pushes dropped), clipped to [0, 1], rounded to 3 decimals; every number below uses the rounded value.

Reproduction of the cached `v91_total_final` harness rows: 2811 replayed vs 2811 cached pre-holdout rows, max |P(over) diff| 0.00e+00, max |mean diff| 0.00e+00: OK.

## Split (harness pinned boundaries)

| Window | Start | End | Games | Season types |
|---|---|---|---|---|
| train | 2024-01-29 | 2025-11-02 | 2049 | play-in-season 12, post-season 166, regular-season 1871 |
| validation | 2025-11-03 | 2026-02-22 | 762 | regular-season 762 |
| locked_holdout | 2026-02-23 | 2026-06-13 | 466 | play-in-season 6, post-season 85, regular-season 375 |

**Fit:** train MLE s = 0.1051 (SE 0.0916, n = 2049 decided games); stored `probability_logit_slope` = **0.105**.

Served P(over) after the shrink: 35% -> 48.38%, 45% -> 49.47%, 55% -> 50.53%, 65% -> 51.62%, 75% -> 52.88%.

## Outcomes per window

RMSE and bias are on the total mean (the shrink does not move it). Bias = mean(actual - model). Brier, log loss and calibration slope are on over/under at the closing total, pushes dropped (coin flip Brier 0.2500).

| Window | Model | n | RMSE | Bias | Brier | Log loss | Cal. slope | Mean abs dist from 50% |
|---|---|---|---|---|---|---|---|---|
| train | v9 (champion) | 2049 | 18.873 | +0.33 | 0.2687 | 0.7357 | 0.046 | 11.50% |
| train | v9.1 raw | 2049 | 18.323 | +0.58 | 0.2603 | 0.7154 | 0.107 | 9.39% |
| train | v9.2 shrunk | 2049 | 18.323 | +0.58 | 0.2498 | 0.6928 | 1.022 | 1.02% |
| validation | v9 (champion) | 757 | 19.423 | +3.34 | 0.2689 | 0.7357 | -0.079 | 11.94% |
| validation | v9.1 raw | 757 | 19.025 | -0.34 | 0.2656 | 0.7268 | -0.167 | 8.83% |
| validation | v9.2 shrunk | 757 | 19.025 | -0.34 | 0.2506 | 0.6943 | -1.590 | 0.96% |
| locked_holdout | v9.1 raw | 461 | 18.655 | +2.33 | 0.2576 | 0.7093 | 0.194 | 8.37% |
| locked_holdout | v9.2 shrunk | 461 | 18.655 | +2.33 | 0.2497 | 0.6926 | 1.847 | 0.91% |

## Games clearing the NBA Main total edge band

Rule: 5% <= side probability - ask <= 10%; an Over needs 9% when the smaller current-season game count is below 15 (counted from the stored season year). The archive has no Polymarket prices, so the ask is assumed. Edge only: eligibility (CALL, qualified, registration) is not modelled.

| Window | Assumed ask | v9 (champion) | v9.1 raw | v9.2 shrunk |
|---|---|---|---|---|
| train | 52.4% | 435 | 444 | 0 |
| train | 50.0% | 470 | 489 | 0 |
| validation | 52.4% | 169 | 190 | 0 |
| validation | 50.0% | 154 | 212 | 1 |
| locked_holdout | 52.4% | not replayed | 105 | 0 |
| locked_holdout | 50.0% | not replayed | 126 | 1 |

## Disclosures

- Validation was read by the 2026-11-20 v9.1 ablation and selected B5's window (W = 300); B6 and B7 were fitted on train. Train-window probabilities are therefore partly in-sample, which biases s toward too little shrink. Validation is a check, not a clean test.
- The 2026-02-23+ holdout is the NBA registry holdout, touched twice before the harness existed (NBA review 2026-11-20). The harness never replayed it; this script reads it once, after s is frozen. It is design-informed (the same review motivated v9.1), not a clean confirmation. It holds the 2026 playoffs, where B7's train-fitted offset applies.
- Closing totals carry no capture time: a benchmark only. Gate G1 (market) failed for v9.1; the shrink calibrates the probability, it does not create market edge.

## Inputs

| Input | sha256 |
|---|---|
| games_jsonl | `3e77a2cfe3d266d729ce13142f8a0ecb64eafe64f8fcf8b0bfff69128042b5c2` |
| closing_lines_jsonl | `4b72ce5b3f7f42fcebfed04b720219a54d71ddd37dde6dab33912a5fc5a833f1` |
| research_artifact_hash | `7364a664cca253d1666a0b2fb15a64515d015af7f8ecb5d1f01d821f6c22a8cd` |
| cached_v91_total_final_rows | `27d97435ce7410ff9f70a336e1b222c350602603a0ccc4ee30e680666ea30134` |
| cached_v9_rows | `6019a89b64427d51bc3a07f8d208a42822d01c50f65859b4864f67a06cb67141` |
