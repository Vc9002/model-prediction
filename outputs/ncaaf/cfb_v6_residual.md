# cfb-structural-v6 market-anchored residual test (item 7), ESPN closing lines

Pinned rule `cfb-v6-residual-rule-1` sha256 `6b7cc7595a58c69aff5aab636643265fee4d34ffb767cda82c6d58b9ee67bb4f` (canonical JSON of the rule block in scripts/cfb_v6_residual.py, computed before any validation or holdout number). Frozen v6 fit sha256 `6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70`. ESPN lines sha256 `2b19a2545fb2e5f9df4c55a1b57ee9f88b11eab371828a4a7ed88e8bdfb7fa54`, rule ['ee23e34e91544b797a1feaa9c6ab604fa79597671f6bec110dd34df4b806ee97']. Script sha256 `c463a7015fca209068c1a56d72a7ee05d8ca55c8cf9ee061488bfe23d06825a0`.

> The line is a CLOSING benchmark with no observed_at, so this tests whether v6 explains anything the closing market missed, not whether v6 could have beaten a decision-time quote. The holdout window's v6-vs-line correlations were already seen in the earlier v6-espn check, so any holdout result below is a SECOND LOOK at the same data, not an independent test. Nothing was tuned: one pinned rule, no grids, no extra features. No promotion decision.

## Verdict

- **total, pinned line**: NO PICK: residual shows no skill
- **spread, pinned line**: NO PICK: residual shows no skill
- **total, book_only line**: NO PICK: residual shows no skill
- **spread, book_only line**: NO PICK: residual shows no skill

NO PICK on both markets and both line definitions. For serving: the fitted slope is indistinguishable from zero, so a market-anchored challenger, line + b*(v6 - line), collapses to the line itself and has no side to take: it would decline every game. v6 adds no information beyond the ESPN closing line in this data; this does not make v6 a worse rating model, only a non-edge against a closing benchmark.

## Rule

```json
{
  "id": "cfb-v6-residual-rule-1",
  "target": "actual - line (spread: home margin minus implied home margin; total: actual total minus line)",
  "feature": "frozen v6 calibrated projection minus line",
  "frozen_params_sha256": "6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70",
  "line": "ESPN pinned median of non-live providers (rule espn-lines-rule-1); robustness line: book-only median (providers Opening, accuscore, teamrankings, numberfire, consensus dropped)",
  "cohort": "lined events covered by v6 (cfb_v6_base_rows.jsonl rows with a grid), 2016 burn-in excluded",
  "windows": {
    "fit": "start date <= 2022-11-11",
    "validation": "2022-11-12 through 2024-11-14",
    "holdout": "start date >= 2024-11-15"
  },
  "fit_date_end": "2022-11-11",
  "validation_date_end": "2024-11-14",
  "model": "OLS of (actual - line) on (v6 - line) with intercept, fit on the FIT window only",
  "baseline": "line only = intercept-only fit on the FIT window (line + mean fit-window residual)",
  "accept_if_all_on_VALIDATION_window": [
    "(a) date-clustered bootstrap 95% CI of the validation-window OLS slope of (actual - line) on (v6 - line) has its lower bound above zero",
    "(b) out-of-sample MSE delta = MSE(baseline) - MSE(residual model), both with FIT-window coefficients applied to validation games, has a date-clustered bootstrap 95% CI with its lower bound above zero"
  ],
  "if_not_accepted": "NO PICK: residual shows no skill; the holdout window is not scored for that market/line",
  "if_accepted": "score the holdout window once: slope + CI, OOS MSE delta + CI (fit coefficients), side record following v6 against the line (|v6 - line| >= 1, pushes excluded), date-clustered bootstrap CI of hit rate vs 50%",
  "bootstrap": {
    "resamples": 2000,
    "cluster": "game date",
    "interval": "percentile 2.5 / 97.5",
    "seed": 17
  },
  "side_min_gap_points": 1.0,
  "no_tuning": "no extra features, no grids, no per-bucket cuts"
}
```

## ESPN pinned median line

### Totals

Window n: {"fit": 4590, "validation": 1790, "holdout": 1899}. Fit-window OLS (actual - line = a + b*(v6 - line)): a +0.062, b -0.012; baseline constant +0.066.

Slope and R-squared are refit within the window with a date-clustered bootstrap CI (2,000 resamples). MSE columns apply the FIT-window coefficients (baseline / residual model); the fit-window row is in-sample. Delta = baseline MSE minus residual MSE (positive favours the residual model).

| window | n | dates | slope [95% CI] | R-squared | MSE baseline / model | MSE delta [95% CI] | OOS R-squared vs baseline |
|---|---|---|---|---|---|---|---|
| fit (in-sample) | 4590 | 2017-08-26 to 2022-11-11 | -0.012 [-0.138, +0.106] | 0.00001 | 267.82 / 267.82 | +0.003 [-0.051, +0.061] | +0.00001 |
| validation (selection) | 1790 | 2022-11-12 to 2024-11-14 | 0.216 [+0.033, +0.398] | 0.00351 | 256.38 / 256.53 | -0.156 [-0.244, -0.068] | -0.00061 |

Validation criteria: (a) slope CI lower bound above zero: pass; (b) OOS MSE delta CI lower bound above zero: fail. **NO PICK: residual shows no skill.**
Holdout window: NOT SCORED (market not accepted on validation; holdout kept unspent).

### Spreads

Window n: {"fit": 4696, "validation": 1789, "holdout": 1891}. Fit-window OLS (actual - line = a + b*(v6 - line)): a -0.208, b -0.009; baseline constant -0.200.

Slope and R-squared are refit within the window with a date-clustered bootstrap CI (2,000 resamples). MSE columns apply the FIT-window coefficients (baseline / residual model); the fit-window row is in-sample. Delta = baseline MSE minus residual MSE (positive favours the residual model).

| window | n | dates | slope [95% CI] | R-squared | MSE baseline / model | MSE delta [95% CI] | OOS R-squared vs baseline |
|---|---|---|---|---|---|---|---|
| fit (in-sample) | 4696 | 2017-08-26 to 2022-11-11 | -0.009 [-0.094, +0.082] | 0.00001 | 244.05 / 244.04 | +0.003 [-0.052, +0.054] | +0.00001 |
| validation (selection) | 1789 | 2022-11-12 to 2024-11-14 | -0.045 [-0.207, +0.128] | 0.00023 | 233.71 / 233.69 | +0.017 [-0.061, +0.098] | +0.00007 |

Validation criteria: (a) slope CI lower bound above zero: fail; (b) OOS MSE delta CI lower bound above zero: fail. **NO PICK: residual shows no skill.**
Holdout window: NOT SCORED (market not accepted on validation; holdout kept unspent).

## Robustness: book-only median (Opening, accuscore, teamrankings, numberfire, consensus dropped)

### Totals

Window n: {"fit": 4475, "validation": 1790, "holdout": 1899}. Fit-window OLS (actual - line = a + b*(v6 - line)): a -0.031, b -0.025; baseline constant -0.023.

Slope and R-squared are refit within the window with a date-clustered bootstrap CI (2,000 resamples). MSE columns apply the FIT-window coefficients (baseline / residual model); the fit-window row is in-sample. Delta = baseline MSE minus residual MSE (positive favours the residual model).

| window | n | dates | slope [95% CI] | R-squared | MSE baseline / model | MSE delta [95% CI] | OOS R-squared vs baseline |
|---|---|---|---|---|---|---|---|
| fit (in-sample) | 4475 | 2017-08-26 to 2022-11-11 | -0.025 [-0.152, +0.109] | 0.00005 | 267.00 / 266.98 | +0.012 [-0.120, +0.136] | +0.00005 |
| validation (selection) | 1790 | 2022-11-12 to 2024-11-14 | 0.215 [+0.030, +0.398] | 0.00346 | 256.57 / 256.92 | -0.348 [-0.539, -0.161] | -0.00136 |

Validation criteria: (a) slope CI lower bound above zero: pass; (b) OOS MSE delta CI lower bound above zero: fail. **NO PICK: residual shows no skill.**
Holdout window: NOT SCORED (market not accepted on validation; holdout kept unspent).

### Spreads

Window n: {"fit": 4614, "validation": 1789, "holdout": 1891}. Fit-window OLS (actual - line = a + b*(v6 - line)): a -0.180, b -0.013; baseline constant -0.169.

Slope and R-squared are refit within the window with a date-clustered bootstrap CI (2,000 resamples). MSE columns apply the FIT-window coefficients (baseline / residual model); the fit-window row is in-sample. Delta = baseline MSE minus residual MSE (positive favours the residual model).

| window | n | dates | slope [95% CI] | R-squared | MSE baseline / model | MSE delta [95% CI] | OOS R-squared vs baseline |
|---|---|---|---|---|---|---|---|
| fit (in-sample) | 4614 | 2017-08-26 to 2022-11-11 | -0.013 [-0.096, +0.072] | 0.00002 | 243.59 / 243.59 | +0.006 [-0.072, +0.081] | +0.00002 |
| validation (selection) | 1789 | 2022-11-12 to 2024-11-14 | -0.045 [-0.207, +0.128] | 0.00023 | 233.70 / 233.68 | +0.024 [-0.091, +0.143] | +0.00010 |

Validation criteria: (a) slope CI lower bound above zero: fail; (b) OOS MSE delta CI lower bound above zero: fail. **NO PICK: residual shows no skill.**
Holdout window: NOT SCORED (market not accepted on validation; holdout kept unspent).

