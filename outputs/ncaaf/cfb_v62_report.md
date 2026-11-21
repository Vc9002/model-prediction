# cfb-structural-v6.2 fit and validation (quick-261121-v62)

Rule `cfb-v62-fit-rule-1` sha256 `08aa947170c1a0273b77e1bb246d4980201b9d66236afbb736255e60082ae368`; spec sha256 `cf74057820d71ac0865f828f37f7ccda2321f3bc68d11bd4a92f2108754c99a0`; frozen v6 params `6e38b14ca486...`.

> DESIGN-INFORMED. Every ESPN lined window 2017-2025 was read before (v6 residual test, v6-espn check, v6.1 comparison, line movement). The historical anchor is the ESPN pinned CLOSING median; serving uses the timestamped DraftKings line as of decision time, which is less sharp, so these numbers overstate what the served anchor knows. This is a pipeline shakedown challenger, not evidence of edge. No promotion decision.

## Fit (train <= 2022-11-11 only)

- Cohort (both lines, season > 2016): train 4590, validation 1789, holdout rows not read at fit 1889. Residual-report cohort reproduced (8279 totals / 8376 spreads lined).
- Blend weight on the anchor: margin w = 1.000 (raw 1.0335), total w = 1.000 (raw 1.0127). Raw above 1 means v6 adds nothing the close lacks (the v6 residual slope is about 0), so the served mean is the anchor.
- Margin SD: exp(2.7968 + -0.0358*|mu|/10 + 0.0263*(total-55)/10); ridge lambda 1.0 (validation choice: 1.0: -4.00909, 10.0: -4.00993, 100.0: -4.04294).
- Fitted key multipliers g(|margin|) (1 = no key mass; Sides 2022 in brackets): 1: 1.00 [0.9], 10: 1.41 [1.3], 11: 0.81 [0.7], 12: 0.43, 13: 0.54, 14: 1.49, 15: 0.53, 16: 0.58, 17: 1.31, 18: 1.02, 19: 0.59, 2: 0.76 [0.7], 20: 0.64, 21: 1.70, 22: 0.62, 23: 0.77, 24: 1.32, 25: 0.85, 26: 0.49, 27: 0.93, 28: 1.78, 3: 2.84 [2.7], 4: 1.03 [1.1], 5: 0.75 [0.7], 6: 1.00 [0.8], 7: 2.48 [2.1], 8: 0.82 [0.7], 9: 0.39 [0.4].
- Total SD by bucket [[0.0, 45.0], [45.0, 52.0], [52.0, 60.0], [60.0, 999.0]]: 14.89, 15.54, 16.06, 17.79.

## Validation 2022-11-12 to 2024-11-14 (design-informed)

| model | moneyline Brier / log | cover Brier / log (ex push) | over Brier / log (ex push) | margin int log score | total int log score | MAE margin / total | mean abs(mean - line) margin / total |
|---|---|---|---|---|---|---|---|
| anchor_only_smooth | 0.15868 / 0.47350 | 0.25026 / 0.69366 | 0.25001 / 0.69316 | -4.1280 | -4.1903 | 12.078 / 12.680 | 0.000 / 0.000 |
| anchor_sides2022_weights | 0.15872 / 0.47360 | 0.25008 / 0.69330 | 0.25001 / 0.69316 | -4.0479 | -4.1903 | 12.078 / 12.680 | 0.000 / 0.000 |
| v6.2 | 0.15877 / 0.47386 | 0.25009 / 0.69333 | 0.25001 / 0.69316 | -4.0091 | -4.1903 | 12.078 / 12.680 | 0.000 / 0.000 |
| v6.2_model_only_fallback | 0.16718 / 0.49952 | 0.26705 / 0.73193 | 0.25447 / 0.70334 | -4.0667 | -4.2109 | 12.822 / 12.956 | 3.865 / 3.708 |
| v6_engine | 0.16704 / 0.49864 | 0.26875 / 0.73632 | 0.25434 / 0.70304 |  |  | 12.822 / 12.956 | 3.865 / 3.708 |

Line MAE (the anchor itself): margin 12.078, total 12.680.

Key mass, predicted vs actual (% of games with |margin| = k):

| k | v6.2 pred | smooth pred | Sides 2022 pred | actual |
|---|---|---|---|---|
| 1 | 3.38 | 3.68 | 3.05 | 2.96 |
| 10 | 4.32 | 3.29 | 3.99 | 4.53 |
| 14 | 4.17 | 2.96 | 2.80 | 4.02 |
| 17 | 3.34 | 2.67 | 2.55 | 3.69 |
| 2 | 2.55 | 3.67 | 2.37 | 3.24 |
| 21 | 3.77 | 2.27 | 2.20 | 3.69 |
| 3 | 9.51 | 3.65 | 9.08 | 10.23 |
| 4 | 3.44 | 3.62 | 3.67 | 3.52 |
| 6 | 3.26 | 3.54 | 2.62 | 2.79 |
| 7 | 8.01 | 3.49 | 6.79 | 8.27 |
| 8 | 2.61 | 3.43 | 2.23 | 2.85 |

Near 3 (|spread| in [2.5, 3.5], n 292), predicted vs actual %:

| event | v6.2 pred | smooth pred | actual |
|---|---|---|---|
| fav_by_eq_3 | 6.81 | 2.63 | 8.56 |
| fav_by_ge_3 | 53.17 | 52.30 | 51.03 |
| fav_by_ge_4 | 46.36 | 49.67 | 42.47 |
| home cover Brier (v6.2 / smooth) | 0.24878 | 0.25105 | rate 49.3% |

Near 7 (|spread| in [6.5, 7.5], n 203), predicted vs actual %:

| event | v6.2 pred | smooth pred | actual |
|---|---|---|---|
| fav_by_eq_7 | 6.08 | 2.64 | 7.39 |
| fav_by_ge_7 | 49.76 | 52.10 | 58.62 |
| fav_by_ge_8 | 43.68 | 49.46 | 51.23 |
| home cover Brier (v6.2 / smooth) | 0.25463 | 0.24920 | rate 46.5% |

## Holdout >= 2024-11-15, single read after hashing (second look at this window)

| model | moneyline Brier / log | cover Brier / log (ex push) | over Brier / log (ex push) | margin int log score | total int log score | MAE margin / total | mean abs(mean - line) margin / total |
|---|---|---|---|---|---|---|---|
| v6.2 | 0.15816 / 0.47063 | 0.25074 / 0.69464 | 0.25000 / 0.69315 | -4.0010 | -4.1537 | 11.843 / 12.371 | 0.000 / 0.000 |
| anchor_only_smooth | 0.15805 / 0.47021 | 0.24991 / 0.69297 | 0.25000 / 0.69315 | -4.1082 | -4.1537 | 11.843 / 12.371 | 0.000 / 0.000 |
| anchor_sides2022_weights | 0.15811 / 0.47035 | 0.25031 / 0.69376 | 0.25000 / 0.69315 | -4.0395 | -4.1537 | 11.843 / 12.371 | 0.000 / 0.000 |
| v6.2_model_only_fallback | 0.16518 / 0.49301 | 0.26856 / 0.73561 | 0.26269 / 0.72032 | -4.0650 | -4.1980 | 12.721 / 12.994 | 3.969 / 3.361 |
| v6_engine | 0.16486 / 0.49180 | 0.27093 / 0.74143 | 0.26215 / 0.71876 |  |  | 12.721 / 12.994 | 3.969 / 3.361 |

Line MAE (the anchor itself): margin 11.843, total 12.371.

Key mass, predicted vs actual (% of games with |margin| = k):

| k | v6.2 pred | smooth pred | Sides 2022 pred | actual |
|---|---|---|---|---|
| 1 | 3.39 | 3.69 | 3.06 | 3.44 |
| 2 | 2.55 | 3.68 | 2.37 | 2.81 |
| 3 | 9.53 | 3.65 | 9.10 | 9.79 |
| 4 | 3.44 | 3.62 | 3.68 | 3.18 |
| 6 | 3.26 | 3.54 | 2.62 | 3.02 |
| 7 | 8.00 | 3.48 | 6.78 | 7.36 |
| 8 | 2.61 | 3.42 | 2.23 | 2.91 |
| 10 | 4.30 | 3.28 | 3.98 | 3.81 |
| 14 | 4.13 | 2.94 | 2.77 | 4.02 |
| 17 | 3.30 | 2.64 | 2.52 | 3.97 |
| 21 | 3.70 | 2.23 | 2.17 | 3.39 |

Near 3 (|spread| in [2.5, 3.5], n 339), predicted vs actual %:

| event | v6.2 pred | smooth pred | actual |
|---|---|---|---|
| fav_by_ge_3 | 53.07 | 52.20 | 56.64 |
| fav_by_ge_4 | 46.26 | 49.58 | 50.15 |
| fav_by_eq_3 | 6.81 | 2.62 | 6.49 |
| home cover Brier (v6.2 / smooth) | 0.25185 | 0.24949 | rate 48.6% |

Near 7 (|spread| in [6.5, 7.5], n 235), predicted vs actual %:

| event | v6.2 pred | smooth pred | actual |
|---|---|---|---|
| fav_by_ge_7 | 49.76 | 52.10 | 54.89 |
| fav_by_ge_8 | 43.68 | 49.46 | 47.23 |
| fav_by_eq_7 | 6.08 | 2.64 | 7.66 |
| home cover Brier (v6.2 / smooth) | 0.25239 | 0.24994 | rate 53.4% |

Models: v6.2 = blended mean, fitted SD model and key multipliers; anchor_only_smooth = close as mean, same SD form refit with no key mass; anchor_sides2022_weights = close as mean with the Sides 2022 multipliers; v6.2_model_only_fallback = v6 means with v6 slice SDs and the fitted multipliers (served when no timestamped anchor exists); v6_engine = v6 as built (NB joint engine, 50k draws, own RNG).
