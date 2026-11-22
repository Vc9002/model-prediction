# NCAAF W3: observed-weather upper bound (quick-261115-183)

Rule sha256 `1481273f1fbd860a5e35ecc25a864818e74818f564aa0f6d5710ea42f524145f`; script sha256 `b53eb169dd5819f48a15d4b38cb98dd888b9044a422f3aecf0a07776955a32fa`; generated 2026-11-15T07:32:25Z; final run: True; holdout_reads: 1.

**OBSERVED WEATHER UPPER BOUND (the line already knows the forecast).** Observed weather can at most reach UPPER_BOUND_PASS and never enters v6.1; if even perfect weather fails, weather is not worth building.

## Coverage

Venues 131; venue-seasons cached 1266 of 1266. Forecast venue-seasons cached 0 of 643.

| season | FBS-involved | neutral | dome home | unresolved or no coords | eligible | with weather | lined total | lined spread | forecast |
|---|---|---|---|---|---|---|---|---|---|
| 2017 | 873 | 61 | 19 | 22 | 771 | 771 | 691 | 771 | 0 |
| 2018 | 884 | 61 | 18 | 14 | 791 | 791 | 761 | 778 | 0 |
| 2019 | 888 | 58 | 18 | 18 | 794 | 794 | 790 | 790 | 0 |
| 2020 | 568 | 38 | 15 | 10 | 505 | 505 | 504 | 504 | 0 |
| 2021 | 852 | 61 | 18 | 5 | 768 | 768 | 764 | 764 | 0 |
| 2022 | 896 | 61 | 20 | 6 | 809 | 809 | 807 | 807 | 0 |
| 2023 | 910 | 62 | 19 | 6 | 823 | 823 | 823 | 823 | 0 |
| 2024 | 919 | 61 | 19 | 6 | 833 | 833 | 793 | 791 | 0 |
| 2025 | 934 | 55 | 18 | 12 | 849 | 849 | 847 | 839 | 0 |
| 2026 | 731 | 10 | 19 | 16 | 686 | 682 | 682 | 682 | 0 |

## Primary statistic: dMSE = MSE(line-only) - MSE(line + weather), validation, select-fit coefficients

| market | stage | n select | n validation | n holdout | dMSE validation | 95% CI | dMSE holdout (point) | verdict |
|---|---|---|---|---|---|---|---|---|
| total | observed | 4135 | 1617 | 1677 | 0.1419 | [-0.3512, 0.5866] | 0.0568 | NOT_WORTH_BUILDING |
| spread | observed | 4232 | 1616 | 1668 | 0.0220 | [-0.0512, 0.1000] | 0.0303 | NOT_WORTH_BUILDING |

Positive dMSE means weather helps. PASS needs the validation CI lower bound above 0 AND a positive holdout point estimate.

## Verdicts

- **total**: NOT_WORTH_BUILDING (stage observed): fails the pass rule (validation CI lower bound > 0 AND holdout point estimate > 0)
- **spread**: NOT_WORTH_BUILDING (stage observed): fails the pass rule (validation CI lower bound > 0 AND holdout point estimate > 0)

## Forecast stage

- total: not run (observed test did not pass; the rule only runs the retest on a pass)
- spread: not run (observed test did not pass; the rule only runs the retest on a pass)

## Exploratory (not a verdict input)

- total: select in-sample dMSE 0.1108, validation dMAE 0.0056
- spread: select in-sample dMSE 0.0030, validation dMAE 0.0005
