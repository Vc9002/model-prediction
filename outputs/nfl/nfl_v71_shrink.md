# NFL v7.1 probability shrink fit (quick-261123-2w2)

Generated 2026-11-23T07:43:58.459571+00:00 by `scripts/fit_nfl_v71_shrink.py`.

Cohort: 1489 games priced point-in-time by the v7 engines (0 pricing failures), seasons [2021, 2022, 2023, 2024, 2025, 2026]. Same loop and inputs as `scripts/replay_nfl_v7_all_settled.py`.

Shrink: `p' = sigmoid(s * logit(p))`, intercept 0. s is fitted on train only (slope-only logistic MLE on logit(p)), clipped to [0, 1], rounded to 3 decimals; every number below uses the rounded value.

## Split (chronological_split, 60/20/20 on complete ET dates)

| Window | Start | End | Games |
|---|---|---|---|
| train | 2021-09-09 | 2024-09-26 | 903 |
| validation | 2024-09-29 | 2025-09-25 | 285 |
| locked_holdout | 2025-09-28 | 2026-10-08 | 301 |

**Disclosure:** the locked holdout overlaps the 2025-09-28+ window that the 2026-11-12 replay already read (its 0.24 / 0.13 holdout calibration slopes motivated this change). The holdout numbers are design-informed, not a clean confirmation.

The 11-12 replay split 80/20 by row count, which cut inside 2025-09-28; this split keeps whole dates, so its holdout holds a few more games and its calibration slopes differ from the 11-12 figures. Slopes on ~300 games whose probabilities sit within a few points of 50% are noisy.

## Spread (nfl-spread-structural-v7.1, fitted on `p_home_cover`)

Train MLE s = 1.1098 (SE 0.6678, n = 874); stored `probability_logit_slope` = **1.0**. The engine's [35%, 65%] clamp maps to [35.00%, 65.00%] after the shrink.

**No shrink.** The train-window MLE is above 1, so s clips to 1.0 and nfl-spread-structural-v7.1 prices identically to nfl-spread-structural-v7. The overconfidence shows only after the train window (calibration slope by window below); fitting s on those windows would break the protocol.

| Window | n | Brier raw | Brier shrunk | Log loss raw | Log loss shrunk | Cal. slope raw | Cal. slope shrunk |
|---|---|---|---|---|---|---|---|
| train | 874 | 0.2492 | 0.2492 | 0.6916 | 0.6916 | 1.072 | 1.072 |
| validation | 282 | 0.2502 | 0.2502 | 0.6935 | 0.6935 | 0.478 | 0.478 |
| locked_holdout | 295 | 0.2502 | 0.2502 | 0.6936 | 0.6936 | 0.397 | 0.397 |
| all | 1451 | 0.2496 | 0.2496 | 0.6924 | 0.6924 | 0.802 | 0.802 |

Games clearing the Main edge band at a 52.4% ask (5% to 10% edge, +2 points on |line| >= 8 spreads / overs at 47+; edge only, eligibility not modelled):

| Season | Games | Main raw | Main shrunk |
|---|---|---|---|
| 2021 | 285 | 3 | 3 |
| 2022 | 284 | 2 | 2 |
| 2023 | 285 | 1 | 1 |
| 2024 | 285 | 4 | 4 |
| 2025 | 285 | 3 | 3 |
| 2026 | 65 | 1 | 1 |

## Total (nfl-total-structural-v7.1, fitted on `p_over_b`)

Train MLE s = 0.5216 (SE 0.3642, n = 893); stored `probability_logit_slope` = **0.522**. The engine's [35%, 65%] clamp maps to [41.99%, 58.01%] after the shrink.

| Window | n | Brier raw | Brier shrunk | Log loss raw | Log loss shrunk | Cal. slope raw | Cal. slope shrunk |
|---|---|---|---|---|---|---|---|
| train | 893 | 0.2499 | 0.2494 | 0.6930 | 0.6920 | 0.675 | 1.293 |
| validation | 282 | 0.2525 | 0.2508 | 0.6981 | 0.6948 | -0.529 | -1.013 |
| locked_holdout | 301 | 0.2510 | 0.2501 | 0.6952 | 0.6933 | 0.089 | 0.171 |
| all | 1476 | 0.2506 | 0.2498 | 0.6944 | 0.6928 | 0.425 | 0.814 |
| all_variant_a_no_weather | 1476 | 0.2517 | 0.2504 | 0.6966 | 0.6940 | 0.193 | 0.370 |

Games clearing the Main edge band at a 52.4% ask (5% to 10% edge, +2 points on |line| >= 8 spreads / overs at 47+; edge only, eligibility not modelled):

| Season | Games | Main raw | Main shrunk |
|---|---|---|---|
| 2021 | 285 | 25 | 1 |
| 2022 | 284 | 17 | 1 |
| 2023 | 285 | 20 | 0 |
| 2024 | 285 | 18 | 0 |
| 2025 | 285 | 25 | 0 |
| 2026 | 65 | 6 | 0 |

