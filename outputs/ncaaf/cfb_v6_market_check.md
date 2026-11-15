# cfb-structural-v6 market-relative check, as of 2026-11-15T02:10:00Z

Frozen fit sha256: `6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70` (with backfill). Selected: {"k": 2, "lambda": 0.1, "w_prev": 0.2}.

v5 reproduction gate: PASS (reference file sha256 `31f2a9b83bec29d8`). Reference sets: 575 totals, 576 spreads.

> The 575-game check is design-informed (the v5 loss review that motivated v6 used these same games) and its window sits inside the locked holdout, so the two results are not independent. At about 575 games the correlation CI is roughly +/-0.08, so a small positive point estimate is no evidence of edge; the spread of projections, over% by line bucket and dog counts below are diagnostics, not evidence. No promotion decision.

## Verdicts

- **total, v6 (common covered set, n 575)**: positive correlation: no (point -0.011); 95% CI [-0.091, +0.054] includes zero
- total, v5 on the same events: positive correlation: no (point -0.033); 95% CI [-0.119, +0.041] includes zero
- total, v5 full set (n 575): positive correlation: no (point -0.033); 95% CI [-0.119, +0.041] includes zero
- **spread, v6 (common covered set, n 576)**: positive correlation: yes (point +0.042); 95% CI [-0.023, +0.110] includes zero
- spread, v5 on the same events: positive correlation: yes (point +0.009); 95% CI [-0.090, +0.105] includes zero
- spread, v5 full set (n 576): positive correlation: yes (point +0.009); 95% CI [-0.090, +0.105] includes zero

## Tables

### Totals

v6 covered 575 of 575 events; uncovered reasons: {}.

| statistic | v5 full set | v5 common set | v6 common set |
|---|---|---|---|
| n | 575 | 575 | 575 |
| corr(model - line, actual - line) | -0.033 | -0.033 | -0.011 |
| corr 95% CI (date-clustered) | [-0.119, 0.041] | [-0.119, 0.041] | [-0.091, 0.054] |
| OLS slope | -0.112 | -0.112 | -0.039 |
| model side record (|gap| >= 1, no push) | 218-257 | 218-257 | 232-256 |
| MAE model / MAE line | 13.15 / 12.43 | 13.15 / 12.43 | 13.12 / 12.43 |
| SD model / line / actual | 4.13 / 5.71 / 16.76 | 4.13 / 5.71 / 16.76 | 5.04 / 5.71 / 16.76 |

Buckets, v5 full set:

| line bucket | n | actual over % | model over / under takes | model side win % |
|---|---|---|---|---|
| under 45 | 63 | 44.4 | 59 / 1 | 45.0 |
| 45-52 | 230 | 48.3 | 177 / 25 | 47.5 |
| 52-60 | 236 | 50.8 | 82 / 90 | 44.8 |
| 60 and over | 46 | 58.7 | 2 / 39 | 43.9 |

Buckets, v5 common set:

| line bucket | n | actual over % | model over / under takes | model side win % |
|---|---|---|---|---|
| under 45 | 63 | 44.4 | 59 / 1 | 45.0 |
| 45-52 | 230 | 48.3 | 177 / 25 | 47.5 |
| 52-60 | 236 | 50.8 | 82 / 90 | 44.8 |
| 60 and over | 46 | 58.7 | 2 / 39 | 43.9 |

Buckets, v6 common set:

| line bucket | n | actual over % | model over / under takes | model side win % |
|---|---|---|---|---|
| under 45 | 63 | 44.4 | 52 / 6 | 41.4 |
| 45-52 | 230 | 48.3 | 177 / 23 | 43.5 |
| 52-60 | 236 | 50.8 | 111 / 83 | 52.6 |
| 60 and over | 46 | 58.7 | 9 / 27 | 52.8 |

### Spreads

v6 covered 576 of 576 events; uncovered reasons: {}.

| statistic | v5 full set | v5 common set | v6 common set |
|---|---|---|---|
| n | 576 | 576 | 576 |
| corr(model - line, actual - line) | 0.009 | 0.009 | 0.042 |
| corr 95% CI (date-clustered) | [-0.090, 0.105] | [-0.090, 0.105] | [-0.023, 0.110] |
| OLS slope | 0.012 | 0.012 | 0.105 |
| model side record (|gap| >= 1, no push) | 264-233 | 264-233 | 237-246 |
| MAE model / MAE line | 14.64 / 11.65 | 14.64 / 11.65 | 12.56 / 11.65 |
| SD model / line / actual | 11.78 / 16.35 / 22.37 | 11.78 / 16.35 / 22.37 | 14.82 / 16.35 / 22.37 |

Buckets, v5 full set:

| line bucket | n | side takes | model side win % |
|---|---|---|---|
| |line| under 7 | 227 | 182 | 53.3 |
| 7-14 | 150 | 126 | 54.0 |
| 14-21 | 82 | 76 | 55.3 |
| 21 and over | 117 | 113 | 50.4 |

Lines of 21+: n 117, mean favourite margin model 16.64 vs line 34.22 vs actual 33.84; dog takes 107.

Buckets, v5 common set:

| line bucket | n | side takes | model side win % |
|---|---|---|---|
| |line| under 7 | 227 | 182 | 53.3 |
| 7-14 | 150 | 126 | 54.0 |
| 14-21 | 82 | 76 | 55.3 |
| 21 and over | 117 | 113 | 50.4 |

Lines of 21+: n 117, mean favourite margin model 16.64 vs line 34.22 vs actual 33.84; dog takes 107.

Buckets, v6 common set:

| line bucket | n | side takes | model side win % |
|---|---|---|---|
| |line| under 7 | 227 | 182 | 50.0 |
| 7-14 | 150 | 128 | 46.9 |
| 14-21 | 82 | 68 | 45.6 |
| 21 and over | 117 | 105 | 52.4 |

Lines of 21+: n 117, mean favourite margin model 28.74 vs line 34.22 vs actual 33.84; dog takes 88.

