# cfb-structural-v6 locked-holdout result, as of 2026-11-15T02:10:00Z

Frozen fit sha256 `6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70` (with backfill); selected {"k": 2, "lambda": 0.1, "w_prev": 0.2}. Holdout scored once. v5 incumbent check: analytic Brier 0.19003 vs recorded engine 0.18933 (n 1527).

Common event cohort (in both v5 and v6 holdout sets): n 1527. v5 events without a v6 projection: 0 ({}).

| model | n | Brier | log loss | ECE | calibration slope | margin log score | total log score | margin resid SD | total resid SD |
|---|---|---|---|---|---|---|---|---|---|
| v6 | 1527 | 0.17975 | 0.5333 | 0.0225 | 1.121 | -4.1630 | -4.1966 | 15.51 | 16.00 |
| v5 | 1527 | 0.19003 | 0.5589 | 0.0448 | 1.034 | -4.2109 | -4.2115 | 16.22 | 16.25 |

Margin MAE: v6 12.286, v5 12.885.

Date-clustered bootstrap, v6 minus v5 (95% CI):

- ML Brier: -0.01029 [-0.01524, -0.00553] (negative = v6 better)
- margin log score: +0.04790 [+0.03055, +0.06395] (positive = v6 better)
- total log score: +0.01496 [+0.00623, +0.02399] (positive = v6 better)
- margin absolute error: -0.59859 [-0.84682, -0.33170] (negative = v6 better)

| season | n | v6 Brier | v5 Brier | v6 margin LS | v5 margin LS | v6 bias | v5 bias |
|---|---|---|---|---|---|---|---|
| 2024 | 233 | 0.20274 | 0.20603 | -4.1625 | -4.1936 | 0.39 | 0.95 |
| 2025 | 793 | 0.17387 | 0.18220 | -4.1526 | -4.2033 | 0.34 | 1.39 |
| 2026 | 501 | 0.17836 | 0.19499 | -4.1797 | -4.2310 | 0.02 | 1.15 |

| slice | n | v6 Brier | v5 Brier | v6 margin LS | v5 margin LS |
|---|---|---|---|---|---|
| established | 1307 | 0.18569 | 0.19508 | -4.1539 | -4.1970 |
| thin_sample | 190 | 0.15046 | 0.16115 | -4.2393 | -4.2791 |
| fbs_vs_fcs | 30 | 0.10637 | 0.15314 | -4.0790 | -4.3850 |

## v6-only existing events (after the v5 cache ends, or v5 has no row)

n 5 (too few to score)

## Backfill-only FBS-involved games (rating inputs in the fit, scored separately)

n 384: Brier 0.10460, log loss 0.3268, ECE 0.0629, slope 1.310, margin log score -4.3028, total log score -4.1924, margin MAE 14.544.

Counts: {"holdout_rows_in_cache": 3456, "v6_scoreable_existing": 1532, "common_cohort": 1527, "v5_events_without_v6": 0, "v5_events_without_v6_reasons": {}, "v6_only_existing_events": 5, "backfill_only_fbs_involved": 384}
