# Production feature ablation — 2026-07-22

## Bottom line

Across 15 testable active features in 5 reproduced models (9 configured production artifacts): **2 KEEP**, **2 REMOVE CANDIDATE**, and **11 INCONCLUSIVE**.

This is development evidence, not a promotion or profit study. The locked cohorts have been reused, legacy score rows lack `observed_at_utc`, and no point-in-time executable prices, fees, or CLV were used. Flat `-110` units below are non-executable diagnostics only.

## Predeclared decision rule

The primary comparison is leave-one-out minus the matched refit on all locked-holdout predictions. KEEP requires removal to worsen validation Brier, worsen holdout Brier by at least 0.001, worsen holdout log loss, produce a date-cluster 95% Brier interval above zero, and survive Holm adjustment at 0.05. REMOVE CANDIDATE is symmetric, or follows directly from a point-in-time provenance blocker. Everything else is INCONCLUSIVE.

## Untestable production models

These models were excluded from inference and multiplicity adjustment because the source or required reproduction evidence was not uniquely established.
A model is excluded when the full-feature refit does not reproduce the artifact or the artifact does not pin the holdout evidence required to test reproduction.

| Model | Status | Explicit source | SHA-256 | Raw / loaded / walk-forward rows | Reason |
|---|---|---|---|---:|---|
| LOL | `UNTESTABLE_REPRODUCTION_EVIDENCE_MISSING` | `data/esports/lol/matches.jsonl` | `c4f16d0c8b9420ace0d33ef315ddabfd83b6c91aa752a204d637a02a5e4e96e6` | 11916 / 11916 / 11916 | active artifact does not pin locked-test metrics and calls |
| CS2 | `UNTESTABLE_REPRODUCTION_EVIDENCE_MISSING` | `data/esports/cs2/matches.jsonl` | `383a6c10ed3f9c6ec7ddf099ebbb0c19d224fe690dcdfa9bf62a07bb9f399109` | 37887 / 37887 / 37887 | active artifact does not pin locked-test metrics and calls |
| DOTA2 | `UNTESTABLE_REPRODUCTION_EVIDENCE_MISSING` | `data/esports/dota2/matches.jsonl` | `969da1b7eeaa62a4c64dfe35383ea2484de36080690cccc3fbc924ac816e5bbd` | 14509 / 14509 / 14509 | active artifact does not pin locked-test metrics and calls |
| VALORANT | `UNTESTABLE_REPRODUCTION_EVIDENCE_MISSING` | `data/esports/valorant/matches.jsonl` | `ee7b8bfa77bd8e3fd7b267405864838253cdbeba5c8d32f51c521db487394d9f` | 10842 / 10842 / 10842 | active artifact does not pin locked-test metrics and calls |

## Feature decisions

| Model | Active feature omitted | Decision | Val Δ Brier | Holdout Δ Brier | Δ log loss | 95% CI Δ Brier | Raw p | Holm p |
|---|---|---|---:|---:|---:|---:|---:|---:|
| MLB | `elo_probability` | **INCONCLUSIVE** | +0.002609 | -0.000286 | -0.000694 | [-0.003803, +0.003051] | 0.8746 | 1.0000 |
| MLB | `trend_gap` | **INCONCLUSIVE** | -0.000583 | -0.000074 | -0.000167 | [-0.000520, +0.000369] | 0.7381 | 1.0000 |
| MLB | `park_factor` | **REMOVE CANDIDATE** | -0.000074 | +0.000034 | +0.000054 | [-0.000349, +0.000422] | 0.8720 | 1.0000 |
| MLB | `weather_factor` | **REMOVE CANDIDATE** | +0.000034 | +0.000006 | +0.000016 | [-0.000067, +0.000076] | 0.8664 | 1.0000 |
| MLB | `pitcher_era_gap` | **INCONCLUSIVE** | -0.000338 | -0.000246 | -0.000500 | [-0.000778, +0.000262] | 0.3815 | 1.0000 |
| NBA | `elo_probability` | **KEEP** | +0.019779 | +0.048279 | +0.103954 | [+0.038656, +0.057658] | 0.0002 | 0.0030 |
| NBA | `trend_gap` | **INCONCLUSIVE** | -0.000039 | +0.000120 | +0.000279 | [-0.000134, +0.000363] | 0.3645 | 1.0000 |
| NBA | `defensive_trend_gap` | **INCONCLUSIVE** | -0.000627 | -0.000634 | -0.001646 | [-0.001482, +0.000161] | 0.1516 | 1.0000 |
| WNBA | `elo_probability` | **INCONCLUSIVE** | +0.030371 | +0.030904 | +0.064451 | [+0.011621, +0.049084] | 0.0052 | 0.0676 |
| WNBA | `trend_gap` | **INCONCLUSIVE** | +0.000038 | +0.000323 | +0.000600 | [-0.000440, +0.001110] | 0.4207 | 1.0000 |
| WNBA | `defensive_trend_gap` | **INCONCLUSIVE** | -0.000036 | -0.000017 | -0.000032 | [-0.000378, +0.000341] | 0.9280 | 1.0000 |
| SOCCER | `elo_probability` | **KEEP** | +0.024524 | +0.022127 | +0.044194 | [+0.014276, +0.029976] | 0.0002 | 0.0030 |
| SOCCER | `trend_gap` | **INCONCLUSIVE** | -0.000377 | -0.000018 | -0.000227 | [-0.000655, +0.000583] | 0.9490 | 1.0000 |
| NFL | `elo_probability` | **INCONCLUSIVE** | +0.021561 | +0.019585 | +0.042328 | [+0.000178, +0.036623] | 0.0734 | 0.8806 |
| NFL | `trend_gap` | **INCONCLUSIVE** | -0.002535 | +0.003342 | +0.007759 | [-0.003944, +0.010289] | 0.3587 | 1.0000 |

## MLB — `mlb-elo-trend-lr-v5`

Split: 3739 train / 1062 validation / 1366 locked holdout. Active features: `elo_probability`, `trend_gap`, `park_factor`, `weather_factor`, `pitcher_era_gap`.
Source: `/Users/vincentc9002/Documents/Poly & Kalshi/model prediction/data/processed/mlb/games.jsonl`; SHA-256 `154ce14648773c03934807517d46d634c647e4dbb66149ee76c20718aff50261`; raw / loaded / walk-forward rows: 7816 / 6257 / 6198.
Reproduction gate: **PASS**. Maximum absolute coefficient delta `0`; intercept delta `+0`; calls / hits deltas `0` / `0`; Brier / log-loss deltas `+0` / `-3.8817e-08`. Tolerances: coefficients/intercept `1e-08`, metrics `1e-06`.

| Baseline | Observations | Accuracy | Brier | Log loss | Calls | Hit rate | -110 units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Matched refit | 1366 | 0.5425 | 0.249407 | 0.692076 | 254 | 0.562992 | 19.00 |

- Omit `elo_probability`: 1366 observations, 52.05% accuracy, Brier 0.249121, log loss 0.691382; 0 calls, — hit rate, +0.00 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared
- Omit `trend_gap`: 1366 observations, 54.54% accuracy, Brier 0.249333, log loss 0.691908; 249 calls, 0.550201 hit rate, +12.55 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared
- Omit `park_factor`: 1366 observations, 54.17% accuracy, Brier 0.249441, log loss 0.692129; 245 calls, 0.567347 hit rate, +20.36 diagnostic units. **REMOVE CANDIDATE** — 2025-three-year static table is applied retroactively across seasons
- Omit `weather_factor`: 1366 observations, 54.10% accuracy, Brier 0.249413, log loss 0.692092; 255 calls, 0.560784 hit rate, +18.00 diagnostic units. **REMOVE CANDIDATE** — historical weather cache has no forecast issue or observed_at timestamp
- Omit `pitcher_era_gap`: 1366 observations, 54.69% accuracy, Brier 0.249162, log loss 0.691576; 265 calls, 0.554717 hit rate, +15.64 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared

## NBA — `nba-elo-trend-lr-v4`

Split: 2171 train / 753 validation / 654 locked holdout. Active features: `elo_probability`, `trend_gap`, `defensive_trend_gap`.
Source: `/Users/vincentc9002/Documents/Poly & Kalshi/model prediction/data/processed/nba/games.jsonl`; SHA-256 `198b418ec7da2ff81c3f4dc5a8f799f1e03133556fd31e24c3ff063f3ef92b2b`; raw / loaded / walk-forward rows: 3633 / 3633 / 3578.
Reproduction gate: **PASS**. Maximum absolute coefficient delta `0`; intercept delta `+0`; calls / hits deltas `0` / `0`; Brier / log-loss deltas `+0` / `-8.1504e-08`. Tolerances: coefficients/intercept `1e-08`, metrics `1e-06`.

| Baseline | Observations | Accuracy | Brier | Log loss | Calls | Hit rate | -110 units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Matched refit | 654 | 0.7034 | 0.193223 | 0.571952 | 577 | 0.736568 | 234.36 |

- Omit `elo_probability`: 654 observations, 57.19% accuracy, Brier 0.241502, log loss 0.675906; 347 calls, 0.62536 hit rate, +67.27 diagnostic units. **KEEP** — removal worsened validation and holdout proper scores with multiplicity-adjusted paired evidence
- Omit `trend_gap`: 654 observations, 70.18% accuracy, Brier 0.193343, log loss 0.572231; 578 calls, 0.738754 hit rate, +237.18 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared
- Omit `defensive_trend_gap`: 654 observations, 70.34% accuracy, Brier 0.192589, log loss 0.570306; 575 calls, 0.73913 hit rate, +236.36 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared

## WNBA — `wnba-elo-trend-lr-v4`

Split: 457 train / 143 validation / 163 locked holdout. Active features: `elo_probability`, `trend_gap`, `defensive_trend_gap`.
Source: `/Users/vincentc9002/Documents/Poly & Kalshi/model prediction/data/processed/wnba/games.jsonl`; SHA-256 `660385e75a42acc13ca33455e9d9c6bfaaaa9831c46078ce63ba5862ae8c020f`; raw / loaded / walk-forward rows: 813 / 813 / 763.
Reproduction gate: **PASS**. Maximum absolute coefficient delta `0`; intercept delta `+0`; calls / hits deltas `0` / `0`; Brier / log-loss deltas `+0` / `+8.0821e-08`. Tolerances: coefficients/intercept `1e-08`, metrics `1e-06`.

| Baseline | Observations | Accuracy | Brier | Log loss | Calls | Hit rate | -110 units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Matched refit | 163 | 0.6748 | 0.214138 | 0.618737 | 163 | 0.674847 | 47.00 |

- Omit `elo_probability`: 163 observations, 56.44% accuracy, Brier 0.245041, log loss 0.683188; 163 calls, 0.564417 hit rate, +12.64 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared
- Omit `trend_gap`: 163 observations, 65.64% accuracy, Brier 0.214461, log loss 0.619337; 163 calls, 0.656442 hit rate, +41.27 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared
- Omit `defensive_trend_gap`: 163 observations, 67.48% accuracy, Brier 0.214120, log loss 0.618705; 163 calls, 0.674847 hit rate, +47.00 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared

## SOCCER — `soccer-elo-trend-lr-v2`

Split: 5476 train / 2009 validation / 1601 locked holdout. Active features: `elo_probability`, `trend_gap`.
Source: `/Users/vincentc9002/Documents/Poly & Kalshi/model prediction/data/processed/soccer/games.jsonl`; SHA-256 `957be12bef72b572f506116af49b2926e7d2a14721fc445de4e2a6ca2ec015ad`; raw / loaded / walk-forward rows: 9137 / 9137 / 9087.
Reproduction gate: **PASS**. Maximum absolute coefficient delta `0`; intercept delta `+0`; calls / hits deltas `0` / `0`; Brier / log-loss deltas `+0` / `+3.8286e-08`. Tolerances: coefficients/intercept `1e-08`, metrics `1e-06`.

| Baseline | Observations | Accuracy | Brier | Log loss | Calls | Hit rate | -110 units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Matched refit | 1601 | 0.6246 | 0.224417 | 0.642026 | 1381 | 0.648805 | 329.55 |

- Omit `elo_probability`: 1601 observations, 55.90% accuracy, Brier 0.246545, log loss 0.686221; 1601 calls, 0.559026 hit rate, +107.64 diagnostic units. **KEEP** — removal worsened validation and holdout proper scores with multiplicity-adjusted paired evidence
- Omit `trend_gap`: 1601 observations, 62.71% accuracy, Brier 0.224399, log loss 0.641799; 1374 calls, 0.652111 hit rate, +336.55 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared

## NFL — `nfl-elo-trend-lr-v4`

Split: 366 train / 146 validation / 122 locked holdout. Active features: `elo_probability`, `trend_gap`.
Source: `/Users/vincentc9002/Documents/Poly & Kalshi/model prediction/data/processed/nfl/games.jsonl`; SHA-256 `17f7961cb8172be8abe3b0ca74ef2f7ddae55231f83adbee3d51c772b65f2cb2`; raw / loaded / walk-forward rows: 700 / 700 / 634.
Reproduction gate: **PASS**. Maximum absolute coefficient delta `0`; intercept delta `+0`; calls / hits deltas `0` / `0`; Brier / log-loss deltas `+0` / `-6.4281e-08`. Tolerances: coefficients/intercept `1e-08`, metrics `1e-06`.

| Baseline | Observations | Accuracy | Brier | Log loss | Calls | Hit rate | -110 units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Matched refit | 122 | 0.6393 | 0.217894 | 0.625581 | 87 | 0.712644 | 31.36 |

- Omit `elo_probability`: 122 observations, 59.84% accuracy, Brier 0.237480, log loss 0.667908; 78 calls, 0.653846 hit rate, +19.36 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared
- Omit `trend_gap`: 122 observations, 65.57% accuracy, Brier 0.221237, log loss 0.633340; 86 calls, 0.697674 hit rate, +28.55 diagnostic units. **INCONCLUSIVE** — predeclared removal or retention gate not cleared

## Multiplicity and economic boundary

Holm correction covers all 15 feature omissions. These reused holdouts can rank removal hypotheses, but they cannot certify a promoted model. No ROI, EV, profitability, or tradability claim is made; that requires point-in-time executable asks on both sides, fees/friction, and CLV on a fresh prospective cohort.

Reproducibility hash: `df39ceaa58b0cc09166f36f0bc3d03a236e40f3e052c83fa42f808c5bf204d3b`.
