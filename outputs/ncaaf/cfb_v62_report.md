# cfb-structural-v6.2 fit and validation (quick-261121-v62)

Rule `cfb-v62-fit-rule-2` sha256 `9cd74d77099d2da05b0d579dbe24e2ebefd4e1278a0596cd0b493d5d2b765127`; spec sha256 `1bb7462ad28e9cd8f3f2bdcd532ff4a0a0c156d88079f84856c859b12115f9cb`; frozen v6 params `6e38b14ca486...`.

> DESIGN-INFORMED. Every ESPN lined window 2017-2025 was read before (v6 residual test, v6-espn check, v6.1 comparison, line movement). The historical anchor is the ESPN pinned CLOSING median; serving uses the timestamped DraftKings line as of decision time, which is less sharp, so these numbers overstate what the served anchor knows. This is a pipeline shakedown challenger, not evidence of edge. No promotion decision.

## Fit (train <= 2022-11-11 only)

- Cohort (both lines, season > 2016): train 4590, validation 1789, holdout rows not read at fit 1889. Residual-report cohort reproduced (8279 totals / 8376 spreads lined).
- Blend weight on the anchor: margin w = 1.000 (raw 1.0335), total w = 1.000 (raw 1.0127). Raw above 1 means v6 adds nothing the close lacks (the v6 residual slope is about 0), so the served mean is the anchor.
- Margin SD: exp(2.7931 + -0.0344*|mu|/10 + 0.0271*(total-55)/10); ridge lambda 1.0 (validation choice: 1.0: -4.00899, 10.0: -4.00975, 100.0: -4.04216).
- Fitted key multipliers g(|margin|) (1 = no key mass; Sides 2022 in brackets): 1: 0.97 [0.9], 2: 0.73 [0.7], 3: 2.76 [2.7], 4: 1.00 [1.1], 5: 0.74 [0.7], 6: 0.99 [0.8], 7: 2.49 [2.1], 8: 0.82 [0.7], 9: 0.40 [0.4], 10: 1.42 [1.3], 11: 0.82 [0.7], 12: 0.43, 13: 0.55, 14: 1.50, 15: 0.53, 16: 0.58, 17: 1.31, 18: 1.02, 19: 0.59, 20: 0.63, 21: 1.68, 22: 0.62, 23: 0.76, 24: 1.30, 25: 0.84, 26: 0.49, 27: 0.92, 28: 1.77.
- Total SD by bucket [[0.0, 45.0], [45.0, 52.0], [52.0, 60.0], [60.0, 999.0]]: 14.89, 15.54, 16.06, 17.79.

## Validation 2022-11-12 to 2024-11-14 (design-informed)

| model | moneyline Brier / log | cover Brier / log (ex push) | over Brier / log (ex push) | margin int log score | total int log score | MAE margin / total | mean abs(mean - line) margin / total |
|---|---|---|---|---|---|---|---|
| v6.2 | 0.15860 / 0.47349 | 0.25000 / 0.69315 | 0.25001 / 0.69316 | -4.0090 | -4.1903 | 12.078 / 12.680 | 0.000 / 0.000 |
| anchor_only_smooth | 0.15866 / 0.47349 | 0.25000 / 0.69315 | 0.25001 / 0.69316 | -4.1280 | -4.1903 | 12.078 / 12.680 | 0.000 / 0.000 |
| anchor_sides2022_weights | 0.15848 / 0.47310 | 0.25000 / 0.69315 | 0.25001 / 0.69316 | -4.0471 | -4.1903 | 12.078 / 12.680 | 0.000 / 0.000 |
| v6.2_model_only_fallback | 0.16717 / 0.49949 | 0.26699 / 0.73178 | 0.25447 / 0.70334 | -4.0669 | -4.2109 | 12.822 / 12.956 | 3.865 / 3.708 |
| v6_engine | 0.16704 / 0.49864 | 0.26875 / 0.73632 | 0.25434 / 0.70304 |  |  | 12.822 / 12.956 | 3.865 / 3.708 |

Line MAE (the anchor itself): margin 12.078, total 12.680.

Key mass, predicted vs actual (% of games with |margin| = k):

| k | v6.2 pred | smooth pred | Sides 2022 pred | actual |
|---|---|---|---|---|
| 1 | 3.26 | 3.70 | 3.04 | 2.96 |
| 2 | 2.46 | 3.69 | 2.35 | 3.24 |
| 3 | 9.21 | 3.66 | 9.04 | 10.23 |
| 4 | 3.33 | 3.64 | 3.66 | 3.52 |
| 6 | 3.21 | 3.55 | 2.61 | 2.79 |
| 7 | 8.00 | 3.50 | 6.76 | 8.27 |
| 8 | 2.61 | 3.44 | 2.22 | 2.85 |
| 10 | 4.38 | 3.30 | 3.98 | 4.53 |
| 14 | 4.23 | 2.96 | 2.79 | 4.02 |
| 17 | 3.40 | 2.67 | 2.56 | 3.69 |
| 21 | 3.78 | 2.27 | 2.21 | 3.69 |

Near 3 (|spread| in [2.5, 3.5], n 292), predicted vs actual %:

| event | v6.2 pred | smooth pred | actual |
|---|---|---|---|
| fav_by_ge_3 | 53.07 | 51.20 | 51.03 |
| fav_by_ge_4 | 46.38 | 48.58 | 42.47 |
| fav_by_eq_3 | 6.69 | 2.62 | 8.56 |
| home cover Brier (v6.2 / smooth) | 0.25000 | 0.25000 | rate 49.3% |

Near 7 (|spread| in [6.5, 7.5], n 203), predicted vs actual %:

| event | v6.2 pred | smooth pred | actual |
|---|---|---|---|
| fav_by_ge_7 | 53.09 | 51.34 | 58.62 |
| fav_by_ge_8 | 46.97 | 48.70 | 51.23 |
| fav_by_eq_7 | 6.12 | 2.64 | 7.39 |
| home cover Brier (v6.2 / smooth) | 0.25000 | 0.25000 | rate 46.5% |

Favourite strict cover and push by exact line, predicted vs actual % (v6.2 / smooth):

| line | n | fav cover v6.2 | smooth | actual | push v6.2 | actual |
|---|---|---|---|---|---|---|
| 2.5 | 120 | 50.0 | 50.0 | 51.7 | 0.0 | 0.0 |
| 3.0 | 73 | 46.7 | 48.7 | 34.2 | 6.7 | 13.7 |
| 3.5 | 96 | 50.0 | 50.0 | 41.7 | 0.0 | 0.0 |
| 6.5 | 78 | 50.0 | 50.0 | 56.4 | 0.0 | 0.0 |
| 7.0 | 44 | 46.9 | 48.7 | 54.5 | 6.2 | 6.8 |
| 7.5 | 81 | 50.0 | 50.0 | 53.1 | 0.0 | 0.0 |
| 9.5 | 50 | 50.0 | 50.0 | 46.0 | 0.0 | 0.0 |
| 10.0 | 31 | 48.2 | 48.7 | 35.5 | 3.6 | 9.7 |
| 10.5 | 46 | 50.0 | 50.0 | 37.0 | 0.0 | 0.0 |
| 13.5 | 45 | 50.0 | 50.0 | 51.1 | 0.0 | 0.0 |
| 14.0 | 28 | 48.1 | 48.7 | 60.7 | 3.8 | 0.0 |
| 14.5 | 41 | 50.0 | 50.0 | 53.7 | 0.0 | 0.0 |

Holdout: NOT RE-READ under rule-2. Rule-1 (mean-centred) was scored once on the holdout after hashing; that single read is kept in outputs/ncaaf/cfb_v62_report_rule1.{json,md}.

Models: v6.2 = blended mean, fitted SD model and key multipliers; anchor_only_smooth = close as mean, same SD form refit with no key mass; anchor_sides2022_weights = close as mean with the Sides 2022 multipliers; v6.2_model_only_fallback = v6 means with v6 slice SDs and the fitted multipliers (served when no timestamped anchor exists); v6_engine = v6 as built (NB joint engine, 50k draws, own RNG).
