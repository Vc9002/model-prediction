# A_ml_w0 confirmation read (protocol 261120, addendum v2)

Pin `ncaab-research-protocol-261120-confirmation-A_ml_w0` sha256 `1fbaabb741a44ce37b19c48e65f7256fbc0e529af0cbde6a6c07a4069a12d25c`. Predictions sha256 `d1c0f45a15ad6249300e0d28871a54d3c4814f5aaeb10433a385479e76c2d930`.
Window season years [2015, 2016, 2017, 2018, 2019, 2020, 2022] (2014 burn-in, 2021 history only). Probabilities only.

| statistic | n | days | delta (cand - v7) | one-sided 95% upper | two-sided 95% | log loss cand | log loss v7 |
|---|---|---|---|---|---|---|---|
| primary (pooled) | 38471 | 989 | -0.004627 | -0.003872 | [-0.005521, -0.003731] | 0.524668 | 0.529295 |
| pooled ex 2015 | 32977 | 849 | -0.004298 | -0.003486 | [-0.005272, -0.003312] | 0.525091 | 0.529389 |
| lined secondary | 20826 | 564 | -0.004036 | -0.002993 | [-0.005282, -0.002799] | 0.529112 | 0.533147 |

**Verdict: PASS** (PASS iff coverage met and upper_one_sided_95 < 0; FAIL otherwise; never re-read)

| season | n | coverage | delta |
|---|---|---|---|
| 2015 | 5494 | 1.0 | -0.006604 |
| 2016 | 5514 | 1.0 | -0.006035 |
| 2017 | 5520 | 1.0 | -0.003780 |
| 2018 | 5533 | 1.0 | -0.005929 |
| 2019 | 5597 | 1.0 | -0.004336 |
| 2020 | 5328 | 1.0 | -0.003849 |
| 2022 | 5485 | 1.0 | -0.001824 |

Moneyline Brier delta (cand - v7): -0.001233
