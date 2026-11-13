# NCAAF fixed-model replay — 2026-11-12T07:16:52Z

Model: `cfb-structural-v4` built from the artifact (margin_sd 14.5, total_sd 16.0, HFA 2.5, 200,000 sims), serving code hash `d49358c16459b8ec`, artifact hash `5d1c1b3bf32a…`. Point-in-time: each game sees only games that kicked off before it (cohort 1) or before the pick's decision time (cohort 2). No artifact, calibrator or ledger was written.

## Cohort 1 — every completed game, 2016 → today (no market lines exist for this cohort)

Games scored: 7960 (ties excluded: 0). Weather treated as unavailable (the history file's weather fields are placeholders).

| cohort | n | Brier | log loss | ECE | slope | acc. | Elo-only Brier | home-rate Brier | margin resid SD | eff. margin SD | total resid SD | eff. total SD | margin bias | total bias |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 7960 | 0.1893 | 0.5583 | 0.0239 | 1.199 | 0.7084 | 0.1894 | 0.2424 | 17.15 | 15.94 | 16.91 | 18.42 | 0.84 | -0.24 |
| 2016 | 734 | 0.2023 | 0.5879 | 0.0207 | 1.095 | 0.6730 | 0.2011 | 0.2429 | 18.76 | 15.97 | 17.91 | 18.40 | 1.12 | 0.64 |
| 2017 | 751 | 0.1826 | 0.5424 | 0.0432 | 1.344 | 0.7204 | 0.1848 | 0.2449 | 17.31 | 15.99 | 17.61 | 18.37 | 0.59 | -0.33 |
| 2018 | 764 | 0.1804 | 0.5363 | 0.0483 | 1.313 | 0.7107 | 0.1798 | 0.2404 | 17.65 | 15.94 | 17.99 | 18.33 | 1.16 | 0.21 |
| 2019 | 753 | 0.1767 | 0.5293 | 0.0539 | 1.340 | 0.7424 | 0.1768 | 0.2410 | 17.46 | 15.97 | 16.83 | 18.35 | 1.45 | -0.02 |
| 2020 | 518 | 0.1915 | 0.5654 | 0.0457 | 1.192 | 0.7162 | 0.1919 | 0.2454 | 17.15 | 16.07 | 17.68 | 18.84 | -0.66 | 1.16 |
| 2021 | 776 | 0.1922 | 0.5668 | 0.0383 | 1.122 | 0.7113 | 0.1914 | 0.2436 | 17.09 | 15.95 | 16.50 | 18.34 | 0.07 | -0.38 |
| 2022 | 778 | 0.1977 | 0.5785 | 0.0321 | 1.077 | 0.6902 | 0.1999 | 0.2430 | 16.45 | 15.91 | 16.27 | 18.30 | 0.01 | -0.72 |
| 2023 | 792 | 0.1876 | 0.5551 | 0.0295 | 1.206 | 0.7096 | 0.1879 | 0.2428 | 16.66 | 15.81 | 16.27 | 18.32 | 0.58 | -0.63 |
| 2024 | 800 | 0.1951 | 0.5696 | 0.0316 | 1.089 | 0.6863 | 0.1961 | 0.2436 | 16.67 | 15.71 | 16.43 | 18.28 | 0.84 | -0.29 |
| 2025 | 793 | 0.1831 | 0.5437 | 0.0544 | 1.360 | 0.7327 | 0.1800 | 0.2397 | 16.37 | 15.68 | 16.04 | 18.28 | 2.04 | -1.07 |
| 2026 | 501 | 0.1954 | 0.5743 | 0.0397 | 1.067 | 0.6986 | 0.1973 | 0.2364 | 16.80 | 16.66 | 16.51 | 19.24 | 2.02 | -0.98 |

### Variance check (is the served SD the realized SD?)

| slice | n | margin resid SD | eff. margin SD | total resid SD | eff. total SD | Brier |
|---|---|---|---|---|---|---|
| all | 7960 | 17.15 | 15.94 | 16.91 | 18.42 | 0.1893 |
| established_fbs_vs_fbs_sample_ge_4 | 4670 | 16.84 | 14.76 | 16.96 | 17.34 | 0.1931 |
| conference_matchups | 3365 | 16.61 | 14.43 | 16.77 | 17.41 | 0.1928 |
| non_conference | 1305 | 17.34 | 15.60 | 17.43 | 17.17 | 0.1940 |
| fbs_vs_fcs | 191 | 18.98 | 21.12 | 16.73 | 22.30 | 0.1903 |
| neutral_site | 580 | 17.08 | 15.43 | 16.93 | 17.39 | 0.2264 |

| uncertainty bucket | n | margin resid SD | eff. margin SD | Brier |
|---|---|---|---|---|
| 0.04-0.07 | 1994 | 17.24 | 14.27 | 0.1910 |
| 0.07-0.10 | 1278 | 16.69 | 14.73 | 0.1904 |
| 0.10-0.14 | 2118 | 16.55 | 15.73 | 0.1984 |
| 0.14-0.19 | 2392 | 17.64 | 17.77 | 0.1792 |
| 0.19-0.31 | 178 | 18.79 | 21.34 | 0.1877 |

### Moneyline reliability (all games)

| bucket | n | mean p(home) | observed |
|---|---|---|---|
| 0.0-0.1 | 42 | 0.0807 | 0.0000 |
| 0.1-0.2 | 267 | 0.1585 | 0.1348 |
| 0.2-0.3 | 580 | 0.2535 | 0.2207 |
| 0.3-0.4 | 885 | 0.3524 | 0.3367 |
| 0.4-0.5 | 1165 | 0.4528 | 0.4506 |
| 0.5-0.6 | 1270 | 0.5495 | 0.5669 |
| 0.6-0.7 | 1227 | 0.6500 | 0.6650 |
| 0.7-0.8 | 1178 | 0.7494 | 0.7810 |
| 0.8-0.9 | 1017 | 0.8465 | 0.8987 |
| 0.9-1.0 | 329 | 0.9191 | 0.9635 |

## Cohort 2 — every settled ledger row re-priced by the fixed model

Rows scored: 1610 (skipped 9: pushes / no ask). Each row is re-priced at its recorded line and executable ask with history as of its decision time. 'Fires' = the fixed model would have produced a Main-eligible call under the artifact gates (2% ≤ edge ≤ 8%, FCS block, 21.5 spread cap, 33.3% moneyline ask floor). Flat units = 1u per fire at the recorded ask.

### Flat ledger — all rows

| group | n | recorded hit | recorded units | old Brier | new Brier | ask Brier | mean old p | mean new p | mean ask | fires | fire hit | fire units | mean fire edge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 559 | 0.5564 | -25.84 | 0.2448 | 0.2449 | 0.2289 | 0.6192 | 0.6089 | 0.5864 | 98 | 0.5102 | -6.14 | 0.0470 |

### Flat ledger — by market

| group | n | recorded hit | recorded units | old Brier | new Brier | ask Brier | mean old p | mean new p | mean ask | fires | fire hit | fire units | mean fire edge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| moneyline | 209 | 0.6603 | -12.84 | 0.2085 | 0.2147 | 0.1952 | 0.6556 | 0.6577 | 0.6935 | 20 | 0.7500 | 4.90 | 0.0461 |
| spread | 173 | 0.5376 | 16.97 | 0.2490 | 0.2552 | 0.2474 | 0.6032 | 0.5963 | 0.5217 | 34 | 0.5000 | -1.53 | 0.0480 |
| total | 177 | 0.4520 | -29.97 | 0.2835 | 0.2705 | 0.2506 | 0.5919 | 0.5636 | 0.5233 | 44 | 0.4091 | -9.52 | 0.0466 |

### Flat ledger — by model version that produced the row

| group | n | recorded hit | recorded units | old Brier | new Brier | ask Brier | mean old p | mean new p | mean ask | fires | fire hit | fire units | mean fire edge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cfb-spread-v1 | 10 | 0.5000 | 1.95 | 0.2490 | 0.2762 | 0.2482 | 0.7360 | 0.6042 | 0.5215 | 1 | 0.0000 | -1.00 | 0.0204 |
| cfb-structural-v2 | 126 | 0.5873 | -5.80 | 0.2367 | 0.2441 | 0.2377 | 0.6190 | 0.6095 | 0.6254 | 24 | 0.5000 | -2.14 | 0.0454 |
| cfb-structural-v3 | 117 | 0.5812 | 6.47 | 0.2415 | 0.2398 | 0.2309 | 0.6335 | 0.6198 | 0.5819 | 18 | 0.5556 | -0.54 | 0.0499 |
| cfb-structural-v4 | 287 | 0.5401 | -20.06 | 0.2455 | 0.2436 | 0.2247 | 0.6028 | 0.6054 | 0.5710 | 51 | 0.5294 | -0.27 | 0.0471 |
| cfb-total-v1 | 10 | 0.2000 | -8.81 | 0.4216 | 0.3233 | 0.2643 | 0.6942 | 0.5884 | 0.5254 | 2 | 0.0000 | -2.00 | 0.0515 |
| college-football-v1 | 9 | 0.7778 | 0.41 | 0.1759 | 0.2431 | 0.1516 | 0.7475 | 0.5982 | 0.7332 | 2 | 0.5000 | -0.20 | 0.0473 |

### Flat ledger — by quote source

| group | n | recorded hit | recorded units | old Brier | new Brier | ask Brier | mean old p | mean new p | mean ask | fires | fire hit | fire units | mean fire edge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| espn_consensus | 416 | 0.5457 | -38.22 | 0.2484 | 0.2513 | 0.2261 | 0.6152 | 0.6029 | 0.5882 | 77 | 0.5065 | -5.32 | 0.0469 |
| polymarket_us | 143 | 0.5874 | 12.38 | 0.2342 | 0.2264 | 0.2370 | 0.6310 | 0.6262 | 0.5812 | 21 | 0.5238 | -0.82 | 0.0473 |

Gate reasons (flat): `{"edge_outside_band": 459, "fcs_blocked": 11, "moneyline_ask_below_floor": 10, "spread_line_above_max": 10}`

### Main ledger — all rows

| group | n | recorded hit | recorded units | old Brier | new Brier | ask Brier | mean old p | mean new p | mean ask | fires | fire hit | fire units | mean fire edge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 1051 | 0.5633 | -56.54 | 0.2684 | 0.2561 | 0.2199 | 0.6451 | 0.6147 | 0.5665 | 241 | 0.4730 | -28.44 | 0.0474 |

### Main ledger — by market

| group | n | recorded hit | recorded units | old Brier | new Brier | ask Brier | mean old p | mean new p | mean ask | fires | fire hit | fire units | mean fire edge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| moneyline | 288 | 0.6806 | -40.17 | 0.2121 | 0.1961 | 0.1408 | 0.6687 | 0.6348 | 0.6831 | 39 | 0.5385 | -4.86 | 0.0452 |
| spread | 381 | 0.5197 | -14.67 | 0.3203 | 0.3005 | 0.2505 | 0.6964 | 0.6566 | 0.5222 | 76 | 0.4868 | -5.13 | 0.0504 |
| total | 382 | 0.5183 | -1.71 | 0.2590 | 0.2570 | 0.2491 | 0.5761 | 0.5579 | 0.5228 | 126 | 0.4444 | -18.45 | 0.0462 |

### Main ledger — by model version that produced the row

| group | n | recorded hit | recorded units | old Brier | new Brier | ask Brier | mean old p | mean new p | mean ask | fires | fire hit | fire units | mean fire edge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cfb-spread-v1 | 288 | 0.5000 | -30.14 | 0.3464 | 0.3214 | 0.2515 | 0.7246 | 0.6710 | 0.5239 | 47 | 0.5319 | 0.73 | 0.0496 |
| cfb-structural-v2 | 107 | 0.5701 | 9.22 | 0.2491 | 0.2438 | 0.2446 | 0.6414 | 0.6249 | 0.5261 | 24 | 0.4167 | -4.84 | 0.0554 |
| cfb-structural-v3 | 57 | 0.5614 | 4.20 | 0.2353 | 0.2362 | 0.2361 | 0.5940 | 0.5932 | 0.5394 | 26 | 0.4615 | -5.53 | 0.0450 |
| cfb-structural-v4 | 61 | 0.5246 | 0.89 | 0.2476 | 0.2531 | 0.2439 | 0.5752 | 0.5725 | 0.5271 | 38 | 0.4474 | -6.29 | 0.0464 |
| cfb-total-v1 | 293 | 0.5188 | -3.25 | 0.2589 | 0.2557 | 0.2493 | 0.5723 | 0.5565 | 0.5240 | 86 | 0.4651 | -9.49 | 0.0463 |
| college-football-v1 | 245 | 0.6980 | -37.46 | 0.2092 | 0.1906 | 0.1272 | 0.6694 | 0.6293 | 0.7012 | 20 | 0.5000 | -3.02 | 0.0419 |

### Main ledger — by quote source

| group | n | recorded hit | recorded units | old Brier | new Brier | ask Brier | mean old p | mean new p | mean ask | fires | fire hit | fire units | mean fire edge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| espn_consensus | 994 | 0.5674 | -50.89 | 0.2695 | 0.2567 | 0.2186 | 0.6490 | 0.6166 | 0.5692 | 216 | 0.4861 | -20.14 | 0.0470 |
| polymarket_us | 57 | 0.4912 | -5.65 | 0.2478 | 0.2454 | 0.2432 | 0.5766 | 0.5822 | 0.5189 | 25 | 0.3600 | -8.30 | 0.0503 |

Gate reasons (main): `{"edge_outside_band": 808, "fcs_blocked": 18, "moneyline_ask_below_floor": 27, "spread_line_above_max": 97}`

## Caveats

- Cohort 1 has no market lines: moneyline is scored against outcomes only; spread/total are assessed through residual variance, not cover probabilities.
- History rows are scores only and not vintaged; the production snapshots carry the same `historical_source_observation_times_verified=False`.
- Weather is unavailable in both cohorts (placeholder fields / no PIT forecast in old snapshots), so the weather term is zero — a total that would have been weather-adjusted live is unadjusted here.
- QB availability has no point-in-time source before 2026-11; the channel is inert in this replay.
- Cohort 2 asks are the recorded decision-time asks (mostly ESPN consensus, labelled per row); 'fire units' assume that price was executable, which for consensus rows it was not.
