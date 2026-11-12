# cfb-structural-v5 research — 2026-11-12T21:13:19Z

Incumbent reproduction gate: cached v4 Brier 0.189259 vs replay 0.18925924156052448 → PASS. Split on complete kickoff dates: train 4842, validation 1591, holdout 1527 games (`{"method": "complete_date_60_20_20", "train": {"start": "2016-08-27", "end": "2022-11-11", "observations": 4842}, "validation": {"start": "2022-11-12", "end": "2024-11-14", "observations": 1591}, "locked_holdout": {"start": "2024-11-15", "end": "2026-11-12", "observations": 1527}}`).

Selected on validation: `v5_k6_confscale`. Fitted on train only:

- margin mean: `m' = -0.4143 + 1.3189 · m` ; total mean: `t' = -5.6336 + 1.1017 · t`
- margin SD table (residual, after calibration): established 17.499 (sample ≥ 6), thin sample 17.125, FBS-vs-FCS 19.731; conference scale 0.9641 (v4: 0.9135)
- total SD 17.288 with no uncertainty widening (v4: 16.0 × uncertainty widening). Blowout-line and total-deviation widening carried over from v4 (no lines to evaluate them).

## Validation tournament (analytic Normal; v4_engine row = the real engine for reference)

| variant | n | ML Brier | log loss | slope | acc | margin log score | resid SD | mean SD | cov50 | cov80 | cov95 | total log score | total resid SD | mean total SD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v4_analytic | 1591 | 0.19117 | 0.5633 | 1.220 | 0.7077 | -4.2453 | 16.78 | 15.79 | 0.490 | 0.773 | 0.932 | -4.2306 | 16.36 | 18.30 |
| elo_channel | 1591 | 0.19238 | 0.5645 | 0.838 | 0.7002 | -4.2453 | 16.78 | 15.79 | 0.490 | 0.773 | 0.932 | n/a | n/a | n/a |
| v4_engine | 1591 | 0.19076 | 0.5618 | 1.156 | 0.7046 | -4.2453 | 16.78 | 15.79 | 0.490 | 0.773 | 0.932 | -4.2306 | 16.36 | 18.30 |
| mean_cal_only | 1591 | 0.19046 | 0.5610 | 0.905 | 0.7046 | -4.2376 | 16.60 | 15.79 | 0.479 | 0.775 | 0.933 | -4.2314 | 16.39 | 18.30 |
| sd_table_only | 1591 | 0.19174 | 0.5642 | 1.345 | 0.7077 | -4.2377 | 16.78 | 17.13 | 0.529 | 0.806 | 0.951 | -4.2176 | 16.36 | 17.29 |
| v5_k3_confscale | 1591 | 0.18988 | 0.5575 | 1.001 | 0.7046 | -4.2279 | 16.60 | 17.13 | 0.512 | 0.824 | 0.955 | -4.2184 | 16.39 | 17.29 |
| v5_k3_noconf | 1591 | 0.18989 | 0.5575 | 1.014 | 0.7046 | -4.2293 | 16.60 | 17.33 | 0.515 | 0.830 | 0.957 | -4.2184 | 16.39 | 17.29 |
| v5_k4_confscale | 1591 | 0.18988 | 0.5575 | 1.001 | 0.7046 | -4.2280 | 16.60 | 17.12 | 0.513 | 0.822 | 0.955 | -4.2184 | 16.39 | 17.29 |
| v5_k4_noconf | 1591 | 0.18989 | 0.5574 | 1.017 | 0.7046 | -4.2297 | 16.60 | 17.38 | 0.518 | 0.830 | 0.957 | -4.2184 | 16.39 | 17.29 |
| v5_k5_confscale | 1591 | 0.18987 | 0.5574 | 1.001 | 0.7046 | -4.2275 | 16.60 | 17.12 | 0.511 | 0.820 | 0.955 | -4.2184 | 16.39 | 17.29 |
| v5_k5_noconf | 1591 | 0.18988 | 0.5574 | 1.017 | 0.7046 | -4.2296 | 16.60 | 17.38 | 0.517 | 0.829 | 0.957 | -4.2184 | 16.39 | 17.29 |
| v5_k6_confscale | 1591 | 0.18985 | 0.5573 | 1.003 | 0.7046 | -4.2279 | 16.60 | 17.15 | 0.512 | 0.821 | 0.956 | -4.2184 | 16.39 | 17.29 |
| v5_k6_noconf | 1591 | 0.18987 | 0.5573 | 1.016 | 0.7046 | -4.2295 | 16.60 | 17.36 | 0.517 | 0.827 | 0.957 | -4.2184 | 16.39 | 17.29 |

## Locked holdout (real engine, scored once)

| model | n | ML Brier | log loss | slope | acc | margin log score | resid SD | mean SD | cov50 | cov80 | cov95 | margin bias | total log score | total resid SD | mean total SD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v4_engine | 1527 | 0.19056 | 0.5618 | 1.189 | 0.7099 | -4.2311 | 16.43 | 15.79 | 0.488 | 0.774 | 0.936 | 1.92 | -4.2244 | 16.22 | 18.37 |
| v5_engine | 1527 | 0.18933 | 0.5573 | 0.981 | 0.7119 | -4.2127 | 16.22 | 17.19 | 0.535 | 0.817 | 0.965 | 1.25 | -4.2115 | 16.25 | 17.29 |
| elo_channel | 1527 | 0.18837 | 0.5555 | 0.894 | 0.7066 | -4.2311 | 16.43 | 15.79 | 0.488 | 0.774 | 0.936 | 1.92 | n/a | n/a | n/a |

Date-clustered bootstrap (95% CI), v5 − v4: ML Brier -0.00122 [-0.00262, +0.00024] (negative = better); margin log score +0.0184 [+0.0043, +0.0330] (positive = better); total log score +0.0129 [+0.0063, +0.0196].

| holdout season | n | v4 Brier | v5 Brier | v4 margin bias | v5 margin bias | v5 resid SD | v5 mean SD |
|---|---|---|---|---|---|---|---|
| 2024 | 233 | 0.20559 | 0.20577 | 1.30 | 0.95 | 15.87 | 17.15 |
| 2025 | 793 | 0.18310 | 0.18103 | 2.04 | 1.39 | 16.08 | 17.17 |
| 2026 | 501 | 0.19536 | 0.19483 | 2.02 | 1.15 | 16.59 | 17.25 |

| holdout slice | n | v4 Brier | v5 Brier | v4 resid/served SD | v5 resid/served SD | v4 cov80 | v5 cov80 |
|---|---|---|---|---|---|---|---|
| established | 651 | 0.19406 | 0.19348 | 16.20/14.27 | 16.03/17.03 | 0.737 | 0.820 |
| thin_sample | 844 | 0.18833 | 0.18630 | 16.50/16.76 | 16.25/17.18 | 0.799 | 0.814 |
| fbs_vs_fcs | 32 | 0.17789 | 0.18492 | 16.43/21.35 | 17.23/20.76 | 0.875 | 0.844 |
| conference | 1002 | 0.20361 | 0.20401 | 16.02/14.82 | 16.00/17.01 | 0.760 | 0.819 |
| neutral | 109 | 0.23187 | 0.23249 | 14.68/15.21 | 14.84/17.36 | 0.780 | 0.835 |

## Settled ledger rows re-priced (recorded line and ask, history as of decision time)

| tier / market | n | v4 Brier | v5 Brier | ask Brier | v4 fires / hit / units | v5 fires / hit / units |
|---|---|---|---|---|---|---|
| flat moneyline | 209 | 0.2147 | 0.2175 | 0.1952 | 20 / 0.7500 / 4.90 | 32 / 0.6875 / 1.11 |
| flat spread | 173 | 0.2552 | 0.2599 | 0.2474 | 34 / 0.5000 / -1.53 | 29 / 0.5862 / 3.79 |
| flat total | 177 | 0.2705 | 0.2715 | 0.2506 | 44 / 0.4091 / -9.52 | 48 / 0.4375 / -7.99 |
| main moneyline | 288 | 0.1961 | 0.1929 | 0.1408 | 39 / 0.5385 / -4.86 | 35 / 0.6857 / 2.13 |
| main spread | 381 | 0.3005 | 0.2971 | 0.2505 | 76 / 0.4868 / -5.13 | 64 / 0.5625 / 4.94 |
| main total | 382 | 0.2570 | 0.2591 | 0.2491 | 126 / 0.4444 / -18.45 | 108 / 0.4537 / -13.91 |

Artifact: `config/models/cfb-structural-v5.json` (hash `82daddd417b1…`, status research, not registered).

## Caveats

- No historical market lines: spread/total layers are judged by variance calibration and Gaussian log score, not cover probabilities. Settled rows (2026 only, mostly consensus asks) are the only market-relative check and are small.
- Weather and QB channels inert in this data (placeholder weather fields; no PIT injury source before 2026-11).
- The mean calibration is a fixed train-period fit; holdout-season bias shows whether home-advantage drift persists.
- The bare Elo channel's moneyline Brier beats v5 on the holdout but loses on validation (and on the replay's 2016-2022 seasons), so swapping the structural mean for Elo is not supported; a two-regressor mean (structural + Elo-implied margin) changed validation Brier by 0.0001 and was not adopted.
