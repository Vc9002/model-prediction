# cfb-structural-v6 vs v5 on ESPN archived closing lines (FIRST LOOK), as of 2026-11-15T02:10:00Z

Frozen fit sha256 `6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70`. ESPN lines file sha256 `2b19a2545fb2e5f9df4c55a1b57ee9f88b11eab371828a4a7ed88e8bdfb7fa54`; audit sha256 `00672fa6fb47b7687b1ba35724d88a70ea2e49dd0f53279a20e1b19a5e654110`; rule sha256 ['ee23e34e91544b797a1feaa9c6ab604fa79597671f6bec110dd34df4b806ee97']. Lined events in file: 9364. Skipped: {"no_call": 36, "no_call_reasons": {"NO_CALL_CFB_V6_UNRATED_TEAM": 36}, "no_line": 255, "lined_events_not_in_v6_cache_by_reason": {"burn_in_season_2016": 873}}.

> ESPN's odds feed gives archived closing/final pregame lines with no per-quote observed_at: a closing-line benchmark, not decision-time quotes. The line is E's pinned rule (median across non-live providers, fixed before any outcome was joined; sha256 in the header); nothing was fitted on any line. Fit-window games (on or before the frozen validation end) were used to fit v6's calibration and v5's, so the holdout-window seasons are the clean test, and from 2024 on each event carries a single book (ESPN BET 2024-25, DraftKings 2025-26), so the median is that book's close. Against the 575 reference the pinned spreads had 13 sign flips of 576 (inferred to be reference-side line-pair repeats, not verified). The 575/576 check is a design-informed subset of the 2026 holdout-window season and is not independent of it. Spread of projections, over% by line bucket and dog takes are diagnostics, not evidence. No promotion decision.

## Totals: lined v6-covered events 8279, common with v5 7078

Orientation check: {"corr_line_vs_actual_total": 0.402850281355854, "n": 8279}

| group | n lined | model | n | corr | 95% CI | slope | side record | MAE model / line | SD model / line / actual | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| window:fit | 6380 | v6 all | 6380 | 0.020 | [-0.009, 0.048] | 0.073 | 2628-2546 | 13.28 / 12.85 | 6.39 / 8.09 / 17.81 | positive correlation: yes (point +0.020); 95% CI [-0.009, +0.048] includes zero |
| window:fit | 6380 | v6 common | 5567 | 0.028 | [-0.001, 0.058] | 0.102 | 2325-2190 | 13.22 / 12.85 | 6.33 / 8.16 / 17.82 | positive correlation: yes (point +0.028); 95% CI [-0.001, +0.058] includes zero |
| window:fit | 6380 | v5 common | 5567 | 0.023 | [-0.006, 0.051] | 0.069 | 2341-2291 | 13.47 / 12.85 | 5.70 / 8.16 / 17.82 | positive correlation: yes (point +0.023); 95% CI [-0.006, +0.051] includes zero |
| window:holdout | 1899 | v6 all | 1899 | -0.034 | [-0.078, 0.008] | -0.126 | 743-790 | 12.99 / 12.37 | 5.35 / 6.04 / 16.45 | positive correlation: no (point -0.034); 95% CI [-0.078, +0.008] includes zero |
| window:holdout | 1899 | v6 common | 1511 | -0.008 | [-0.057, 0.041] | -0.034 | 595-608 | 13.02 / 12.52 | 5.32 / 6.08 / 16.63 | positive correlation: no (point -0.008); 95% CI [-0.057, +0.041] includes zero |
| window:holdout | 1899 | v5 common | 1511 | -0.040 | [-0.097, 0.016] | -0.150 | 596-616 | 13.13 / 12.52 | 5.24 / 6.08 / 16.63 | positive correlation: no (point -0.040); 95% CI [-0.097, +0.016] includes zero |
| season:2017 | 785 | v6 all | 785 | 0.005 | [-0.084, 0.091] | 0.018 | 312-310 | 14.15 / 13.71 | 6.74 / 8.10 / 18.97 | positive correlation: yes (point +0.005); 95% CI [-0.084, +0.091] includes zero |
| season:2017 | 785 | v6 common | 729 | 0.007 | [-0.087, 0.095] | 0.025 | 293-286 | 13.76 / 13.34 | 6.77 / 8.19 / 18.43 | positive correlation: yes (point +0.007); 95% CI [-0.087, +0.095] includes zero |
| season:2017 | 785 | v5 common | 729 | -0.010 | [-0.084, 0.056] | -0.029 | 307-302 | 14.07 / 13.34 | 5.95 / 8.19 / 18.43 | positive correlation: no (point -0.010); 95% CI [-0.084, +0.056] includes zero |
| season:2018 | 852 | v6 all | 852 | -0.072 | [-0.138, 0.004] | -0.290 | 309-362 | 13.40 / 12.80 | 6.77 / 8.37 / 18.75 | positive correlation: no (point -0.072); 95% CI [-0.138, +0.004] includes zero |
| season:2018 | 852 | v6 common | 749 | -0.062 | [-0.137, 0.019] | -0.261 | 276-310 | 13.54 / 13.07 | 6.70 / 8.36 / 19.01 | positive correlation: no (point -0.062); 95% CI [-0.137, +0.019] includes zero |
| season:2018 | 852 | v5 common | 749 | -0.057 | [-0.143, 0.029] | -0.171 | 302-319 | 13.98 / 13.07 | 5.74 / 8.36 / 19.01 | positive correlation: no (point -0.057); 95% CI [-0.143, +0.029] includes zero |
| season:2019 | 884 | v6 all | 884 | 0.048 | [-0.026, 0.122] | 0.182 | 352-364 | 13.31 / 12.94 | 6.29 / 8.19 / 17.49 | positive correlation: yes (point +0.048); 95% CI [-0.026, +0.122] includes zero |
| season:2019 | 884 | v6 common | 739 | 0.037 | [-0.035, 0.110] | 0.140 | 296-302 | 13.26 / 12.88 | 6.25 / 8.13 / 17.70 | positive correlation: yes (point +0.037); 95% CI [-0.035, +0.110] includes zero |
| season:2019 | 884 | v5 common | 739 | 0.051 | [-0.028, 0.132] | 0.146 | 312-322 | 13.59 / 12.88 | 5.26 / 8.13 / 17.70 | positive correlation: yes (point +0.051); 95% CI [-0.028, +0.132] includes zero |
| season:2020 | 567 | v6 all | 567 | 0.060 | [-0.029, 0.143] | 0.224 | 254-212 | 13.86 / 13.73 | 6.42 / 8.05 / 18.48 | positive correlation: yes (point +0.060); 95% CI [-0.029, +0.143] includes zero |
| season:2020 | 567 | v6 common | 518 | 0.077 | [-0.011, 0.167] | 0.289 | 236-187 | 13.60 / 13.56 | 6.39 / 7.96 / 18.40 | positive correlation: yes (point +0.077); 95% CI [-0.011, +0.167] includes zero |
| season:2020 | 567 | v5 common | 518 | 0.038 | [-0.043, 0.120] | 0.114 | 226-205 | 14.01 / 13.56 | 5.69 / 7.96 / 18.40 | positive correlation: yes (point +0.038); 95% CI [-0.043, +0.120] includes zero |
| season:2021 | 848 | v6 all | 848 | -0.010 | [-0.076, 0.059] | -0.033 | 342-363 | 13.11 / 12.47 | 6.45 / 7.62 / 17.11 | positive correlation: no (point -0.010); 95% CI [-0.076, +0.059] includes zero |
| season:2021 | 848 | v6 common | 751 | 0.014 | [-0.048, 0.077] | 0.049 | 312-312 | 12.95 / 12.50 | 6.10 / 7.67 / 17.08 | positive correlation: yes (point +0.014); 95% CI [-0.048, +0.077] includes zero |
| season:2021 | 848 | v5 common | 751 | 0.039 | [-0.028, 0.101] | 0.122 | 313-300 | 12.93 / 12.50 | 5.41 / 7.67 / 17.08 | positive correlation: yes (point +0.039); 95% CI [-0.028, +0.101] includes zero |
| season:2022 | 894 | v6 all | 894 | -0.011 | [-0.097, 0.069] | -0.035 | 377-360 | 12.86 / 12.16 | 6.13 / 8.19 / 17.54 | positive correlation: no (point -0.011); 95% CI [-0.097, +0.069] includes zero |
| season:2022 | 894 | v6 common | 767 | -0.011 | [-0.110, 0.076] | -0.038 | 329-309 | 12.83 / 12.13 | 6.12 / 8.32 / 17.53 | positive correlation: no (point -0.011); 95% CI [-0.110, +0.076] includes zero |
| season:2022 | 894 | v5 common | 767 | -0.016 | [-0.093, 0.058] | -0.049 | 296-330 | 13.10 / 12.13 | 6.17 / 8.32 / 17.53 | positive correlation: no (point -0.016); 95% CI [-0.093, +0.058] includes zero |
| season:2023 | 910 | v6 all | 910 | 0.020 | [-0.047, 0.089] | 0.071 | 400-348 | 13.00 / 12.62 | 6.00 / 7.81 / 17.03 | positive correlation: yes (point +0.020); 95% CI [-0.047, +0.089] includes zero |
| season:2023 | 910 | v6 common | 776 | 0.035 | [-0.039, 0.120] | 0.127 | 341-293 | 13.09 / 12.77 | 5.94 / 7.93 / 17.15 | positive correlation: yes (point +0.035); 95% CI [-0.039, +0.120] includes zero |
| season:2023 | 910 | v5 common | 776 | 0.045 | [-0.047, 0.137] | 0.143 | 349-304 | 13.18 / 12.77 | 5.60 / 7.93 / 17.15 | positive correlation: yes (point +0.045); 95% CI [-0.047, +0.137] includes zero |
| season:2024 | 877 | v6 all | 877 | 0.094 | [0.015, 0.164] | 0.387 | 371-314 | 12.76 / 12.69 | 5.73 / 6.65 / 16.65 | positive correlation: yes (point +0.094); 95% CI [+0.015, +0.164] excludes zero |
| season:2024 | 877 | v6 common | 765 | 0.095 | [0.005, 0.176] | 0.411 | 328-272 | 12.83 / 12.78 | 5.70 / 6.75 / 16.63 | positive correlation: yes (point +0.095); 95% CI [+0.005, +0.176] excludes zero |
| season:2024 | 877 | v5 common | 765 | 0.051 | [-0.028, 0.133] | 0.194 | 325-306 | 13.03 / 12.78 | 5.18 / 6.75 / 16.63 | positive correlation: yes (point +0.051); 95% CI [-0.028, +0.133] includes zero |
| season:2025 | 931 | v6 all | 931 | 0.015 | [-0.040, 0.068] | 0.059 | 379-356 | 12.86 / 12.44 | 5.46 / 6.14 / 16.29 | positive correlation: yes (point +0.015); 95% CI [-0.040, +0.068] includes zero |
| season:2025 | 931 | v6 common | 784 | 0.032 | [-0.038, 0.098] | 0.135 | 321-295 | 12.86 / 12.47 | 5.41 / 6.22 / 16.41 | positive correlation: yes (point +0.032); 95% CI [-0.038, +0.098] includes zero |
| season:2025 | 931 | v5 common | 784 | -0.011 | [-0.103, 0.071] | -0.042 | 318-298 | 12.98 / 12.47 | 5.48 / 6.22 / 16.41 | positive correlation: no (point -0.011); 95% CI [-0.103, +0.071] includes zero |
| season:2026 | 731 | v6 all | 731 | -0.105 | [-0.193, -0.037] | -0.351 | 275-347 | 13.22 / 12.22 | 5.19 / 5.72 / 16.69 | positive correlation: no (point -0.105); 95% CI [-0.193, -0.037] excludes zero |
| season:2026 | 731 | v6 common | 500 | -0.089 | [-0.170, -0.013] | -0.378 | 188-232 | 13.29 / 12.51 | 5.15 / 5.55 / 16.96 | positive correlation: no (point -0.089); 95% CI [-0.170, -0.013] excludes zero |
| season:2026 | 731 | v5 common | 500 | -0.094 | [-0.182, -0.003] | -0.349 | 189-221 | 13.36 / 12.51 | 4.35 / 5.55 / 16.96 | positive correlation: no (point -0.094); 95% CI [-0.182, -0.003] excludes zero |
| all | 8279 | v6 all | 8279 | 0.010 | [-0.015, 0.034] | 0.036 | 3371-3336 | 13.22 / 12.74 | 6.20 / 7.75 / 17.54 | positive correlation: yes (point +0.010); 95% CI [-0.015, +0.034] includes zero |
| all | 8279 | v6 common | 7078 | 0.023 | [-0.003, 0.049] | 0.085 | 2920-2798 | 13.18 / 12.78 | 6.16 / 7.85 / 17.60 | positive correlation: yes (point +0.023); 95% CI [-0.003, +0.049] includes zero |
| all | 8279 | v5 common | 7078 | 0.013 | [-0.013, 0.039] | 0.041 | 2937-2907 | 13.40 / 12.78 | 5.66 / 7.85 / 17.60 | positive correlation: yes (point +0.013); 95% CI [-0.013, +0.039] includes zero |

Buckets, window:holdout, v6 all covered:

| line bucket | n | actual over % | model over / under takes | model side win % |
|---|---|---|---|---|
| under 45 | 203 | 44.3 | 164 / 14 | 43.8 |
| 45-52 | 730 | 52.5 | 515 / 86 | 49.1 |
| 52-60 | 757 | 49.3 | 322 / 268 | 48.8 |
| 60 and over | 209 | 52.6 | 30 / 134 | 50.0 |

Buckets, window:holdout, v5 common:

| line bucket | n | actual over % | model over / under takes | model side win % |
|---|---|---|---|---|
| under 45 | 182 | 44.0 | 138 / 26 | 45.7 |
| 45-52 | 604 | 52.6 | 423 / 86 | 50.7 |
| 52-60 | 575 | 49.2 | 198 / 220 | 48.3 |
| 60 and over | 150 | 51.3 | 17 / 104 | 50.4 |

Buckets, window:fit, v6 all covered:

| line bucket | n | actual over % | model over / under takes | model side win % |
|---|---|---|---|---|
| under 45 | 598 | 50.3 | 511 / 27 | 49.8 |
| 45-52 | 1743 | 50.1 | 1151 / 256 | 51.6 |
| 52-60 | 2330 | 50.1 | 887 / 909 | 50.1 |
| 60 and over | 1709 | 45.7 | 201 / 1232 | 51.3 |

Buckets, window:fit, v5 common:

| line bucket | n | actual over % | model over / under takes | model side win % |
|---|---|---|---|---|
| under 45 | 550 | 49.8 | 473 / 22 | 49.7 |
| 45-52 | 1566 | 50.3 | 1211 / 155 | 49.7 |
| 52-60 | 1977 | 49.6 | 763 / 713 | 49.5 |
| 60 and over | 1474 | 44.7 | 147 / 1148 | 53.0 |

### Robustness: book-only median (Opening, accuscore, teamrankings, numberfire, consensus dropped), total

| group | model | n | pinned corr [95% CI] | book-only corr [95% CI] |
|---|---|---|---|---|
| window:fit | v6 all | 6265 | 0.020 [-0.009, 0.048] | 0.018 [-0.012, 0.049] |
| window:fit | v5 common | 5476 | 0.023 [-0.006, 0.051] | 0.022 [-0.007, 0.051] |
| window:holdout | v6 all | 1899 | -0.034 [-0.078, 0.008] | -0.034 [-0.078, 0.008] |
| window:holdout | v5 common | 1511 | -0.040 [-0.097, 0.016] | -0.040 [-0.097, 0.016] |
| all | v6 all | 8164 | 0.010 [-0.015, 0.034] | 0.008 [-0.017, 0.033] |
| all | v5 common | 6987 | 0.013 [-0.013, 0.039] | 0.012 [-0.014, 0.038] |

Groups where the book-only conclusion differs (sign of corr or CI excluding zero): 2.
- season:2018 / v6_all_covered: pinned: positive correlation: no (point -0.072); 95% CI [-0.138, +0.004] includes zero; book-only: positive correlation: no (point -0.086); 95% CI [-0.159, -0.005] excludes zero
- season:2020 / v6_common: pinned: positive correlation: yes (point +0.077); 95% CI [-0.011, +0.167] includes zero; book-only: positive correlation: yes (point +0.086); 95% CI [+0.000, +0.175] excludes zero

## Spreads: lined v6-covered events 8376, common with v5 7086

Orientation check: {"corr_implied_home_margin_vs_actual": 0.7229396245014452, "favourite_wins_straight_up_pct": 76.16313838057648, "n": 8361}

| group | n lined | model | n | corr | 95% CI | slope | side record | MAE model / line | SD model / line / actual | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| window:fit | 6485 | v6 all | 6485 | -0.006 | [-0.034, 0.024] | -0.017 | 2591-2633 | 13.08 / 12.27 | 14.39 / 16.01 / 22.31 | positive correlation: no (point -0.006); 95% CI [-0.034, +0.024] includes zero |
| window:fit | 6485 | v6 common | 5574 | -0.009 | [-0.041, 0.022] | -0.028 | 2151-2267 | 12.94 / 12.29 | 13.13 / 13.99 / 21.00 | positive correlation: no (point -0.009); 95% CI [-0.041, +0.022] includes zero |
| window:fit | 6485 | v5 common | 5574 | 0.004 | [-0.024, 0.033] | 0.010 | 2294-2424 | 13.37 / 12.29 | 12.67 / 13.99 / 21.00 | positive correlation: yes (point +0.004); 95% CI [-0.024, +0.033] includes zero |
| window:holdout | 1891 | v6 all | 1891 | -0.016 | [-0.060, 0.027] | -0.046 | 750-773 | 12.73 / 11.84 | 14.29 / 15.91 / 22.21 | positive correlation: no (point -0.016); 95% CI [-0.060, +0.027] includes zero |
| window:holdout | 1891 | v6 common | 1512 | 0.019 | [-0.027, 0.066] | 0.060 | 607-589 | 12.30 / 11.73 | 12.47 / 13.27 / 20.08 | positive correlation: yes (point +0.019); 95% CI [-0.027, +0.066] includes zero |
| window:holdout | 1891 | v5 common | 1512 | 0.019 | [-0.032, 0.068] | 0.043 | 689-619 | 12.87 / 11.73 | 12.11 / 13.27 / 20.08 | positive correlation: yes (point +0.019); 95% CI [-0.032, +0.068] includes zero |
| season:2017 | 873 | v6 all | 873 | 0.034 | [-0.058, 0.121] | 0.093 | 382-331 | 13.11 / 12.35 | 14.28 / 15.96 / 22.53 | positive correlation: yes (point +0.034); 95% CI [-0.058, +0.121] includes zero |
| season:2017 | 873 | v6 common | 737 | -0.021 | [-0.117, 0.069] | -0.071 | 303-289 | 12.93 / 12.15 | 13.18 / 14.15 / 21.64 | positive correlation: no (point -0.021); 95% CI [-0.117, +0.069] includes zero |
| season:2017 | 873 | v5 common | 737 | -0.015 | [-0.109, 0.090] | -0.038 | 297-314 | 13.45 / 12.15 | 12.65 / 14.15 / 21.64 | positive correlation: no (point -0.015); 95% CI [-0.109, +0.090] includes zero |
| season:2018 | 870 | v6 all | 870 | 0.001 | [-0.057, 0.070] | 0.004 | 349-340 | 13.13 / 12.50 | 14.75 / 16.35 / 22.75 | positive correlation: yes (point +0.001); 95% CI [-0.057, +0.070] includes zero |
| season:2018 | 870 | v6 common | 749 | 0.012 | [-0.066, 0.094] | 0.046 | 288-295 | 13.12 / 12.69 | 13.56 / 14.17 / 21.53 | positive correlation: yes (point +0.012); 95% CI [-0.066, +0.094] includes zero |
| season:2018 | 870 | v5 common | 749 | -0.006 | [-0.071, 0.051] | -0.016 | 319-324 | 13.77 / 12.69 | 12.83 / 14.17 / 21.53 | positive correlation: no (point -0.006); 95% CI [-0.071, +0.051] includes zero |
| season:2019 | 884 | v6 all | 884 | -0.018 | [-0.081, 0.046] | -0.052 | 350-365 | 12.82 / 11.97 | 14.99 / 16.70 / 22.94 | positive correlation: no (point -0.018); 95% CI [-0.081, +0.046] includes zero |
| season:2019 | 884 | v6 common | 739 | -0.025 | [-0.081, 0.039] | -0.080 | 284-303 | 12.86 / 12.16 | 13.87 / 15.00 / 22.10 | positive correlation: no (point -0.025); 95% CI [-0.081, +0.039] includes zero |
| season:2019 | 884 | v5 common | 739 | -0.031 | [-0.096, 0.031] | -0.074 | 285-348 | 13.43 / 12.16 | 13.16 / 15.00 / 22.10 | positive correlation: no (point -0.031); 95% CI [-0.096, +0.031] includes zero |
| season:2020 | 567 | v6 all | 567 | 0.028 | [-0.067, 0.118] | 0.078 | 239-240 | 13.27 / 12.70 | 13.62 / 14.98 / 21.45 | positive correlation: yes (point +0.028); 95% CI [-0.067, +0.118] includes zero |
| season:2020 | 567 | v6 common | 518 | 0.037 | [-0.061, 0.128] | 0.107 | 218-219 | 13.30 / 12.84 | 12.98 / 13.90 / 20.87 | positive correlation: yes (point +0.037); 95% CI [-0.061, +0.128] includes zero |
| season:2020 | 567 | v5 common | 518 | 0.074 | [-0.019, 0.159] | 0.172 | 226-224 | 13.60 / 12.84 | 12.20 / 13.90 / 20.87 | positive correlation: yes (point +0.074); 95% CI [-0.019, +0.159] includes zero |
| season:2021 | 848 | v6 all | 848 | -0.039 | [-0.120, 0.044] | -0.094 | 321-365 | 13.75 / 12.54 | 14.86 / 15.90 / 22.13 | positive correlation: no (point -0.039); 95% CI [-0.120, +0.044] includes zero |
| season:2021 | 848 | v6 common | 751 | -0.017 | [-0.110, 0.063] | -0.052 | 274-322 | 13.37 / 12.56 | 13.56 / 14.07 / 20.76 | positive correlation: no (point -0.017); 95% CI [-0.110, +0.063] includes zero |
| season:2021 | 848 | v5 common | 751 | 0.044 | [-0.031, 0.117] | 0.105 | 311-329 | 13.46 / 12.56 | 12.61 / 14.07 / 20.76 | positive correlation: yes (point +0.044); 95% CI [-0.031, +0.117] includes zero |
| season:2022 | 894 | v6 all | 894 | -0.028 | [-0.092, 0.037] | -0.084 | 345-366 | 12.95 / 12.18 | 14.01 / 15.88 / 21.87 | positive correlation: no (point -0.028); 95% CI [-0.092, +0.037] includes zero |
| season:2022 | 894 | v6 common | 767 | -0.014 | [-0.110, 0.083] | -0.047 | 291-308 | 12.42 / 11.88 | 12.42 / 13.56 / 19.80 | positive correlation: no (point -0.014); 95% CI [-0.110, +0.083] includes zero |
| season:2022 | 894 | v5 common | 767 | -0.004 | [-0.074, 0.063] | -0.010 | 307-333 | 12.81 / 11.88 | 12.30 / 13.56 / 19.80 | positive correlation: no (point -0.004); 95% CI [-0.074, +0.063] includes zero |
| season:2023 | 910 | v6 all | 910 | -0.021 | [-0.108, 0.067] | -0.060 | 362-356 | 12.73 / 12.06 | 13.83 / 15.71 / 21.77 | positive correlation: no (point -0.021); 95% CI [-0.108, +0.067] includes zero |
| season:2023 | 910 | v6 common | 776 | -0.056 | [-0.134, 0.020] | -0.185 | 292-306 | 12.82 / 12.13 | 12.25 / 13.28 / 20.41 | positive correlation: no (point -0.056); 95% CI [-0.134, +0.020] includes zero |
| season:2023 | 910 | v5 common | 776 | -0.024 | [-0.104, 0.055] | -0.060 | 318-322 | 13.22 / 12.13 | 12.50 / 13.28 / 20.41 | positive correlation: no (point -0.024); 95% CI [-0.104, +0.055] includes zero |
| season:2024 | 875 | v6 all | 875 | 0.011 | [-0.051, 0.078] | 0.031 | 335-350 | 12.72 / 11.91 | 13.77 / 15.30 / 21.72 | positive correlation: yes (point +0.011); 95% CI [-0.051, +0.078] includes zero |
| season:2024 | 875 | v6 common | 763 | 0.029 | [-0.040, 0.097] | 0.098 | 288-303 | 12.65 / 12.05 | 12.31 / 12.94 / 20.07 | positive correlation: yes (point +0.029); 95% CI [-0.040, +0.097] includes zero |
| season:2024 | 875 | v5 common | 763 | 0.030 | [-0.030, 0.092] | 0.074 | 345-312 | 13.11 / 12.05 | 12.62 / 12.94 / 20.07 | positive correlation: yes (point +0.030); 95% CI [-0.030, +0.092] includes zero |
| season:2025 | 924 | v6 all | 924 | 0.024 | [-0.037, 0.087] | 0.071 | 384-358 | 12.50 / 11.90 | 14.04 / 15.57 / 21.87 | positive correlation: yes (point +0.024); 95% CI [-0.037, +0.087] includes zero |
| season:2025 | 924 | v6 common | 786 | 0.058 | [-0.004, 0.118] | 0.188 | 329-294 | 12.09 / 11.75 | 12.49 / 13.35 / 20.20 | positive correlation: yes (point +0.058); 95% CI [-0.004, +0.118] includes zero |
| season:2025 | 924 | v5 common | 786 | 0.032 | [-0.039, 0.100] | 0.074 | 345-332 | 12.64 / 11.75 | 12.08 / 13.35 / 20.20 | positive correlation: yes (point +0.032); 95% CI [-0.039, +0.100] includes zero |
| season:2026 | 731 | v6 all | 731 | -0.078 | [-0.145, -0.013] | -0.195 | 274-335 | 13.21 / 11.78 | 15.10 / 16.91 / 23.27 | positive correlation: no (point -0.078); 95% CI [-0.145, -0.013] excludes zero |
| season:2026 | 731 | v6 common | 500 | -0.047 | [-0.124, 0.022] | -0.134 | 191-217 | 12.60 / 11.59 | 13.19 / 13.87 / 20.50 | positive correlation: no (point -0.047); 95% CI [-0.124, +0.022] includes zero |
| season:2026 | 731 | v5 common | 500 | -0.026 | [-0.106, 0.061] | -0.051 | 230-205 | 13.31 / 11.59 | 12.34 / 13.87 / 20.50 | positive correlation: no (point -0.026); 95% CI [-0.106, +0.061] includes zero |
| all | 8376 | v6 all | 8376 | -0.007 | [-0.032, 0.018] | -0.020 | 3341-3406 | 13.00 / 12.17 | 14.38 / 15.99 / 22.30 | positive correlation: no (point -0.007); 95% CI [-0.032, +0.018] includes zero |
| all | 8376 | v6 common | 7086 | -0.002 | [-0.029, 0.024] | -0.006 | 2758-2856 | 12.80 / 12.17 | 13.00 / 13.84 / 20.82 | positive correlation: no (point -0.002); 95% CI [-0.029, +0.024] includes zero |
| all | 8376 | v5 common | 7086 | 0.007 | [-0.018, 0.031] | 0.016 | 2983-3043 | 13.26 / 12.17 | 12.55 / 13.84 / 20.82 | positive correlation: yes (point +0.007); 95% CI [-0.018, +0.031] includes zero |

Buckets, window:holdout, v6 all covered:

| line bucket | n | side takes | model side win % |
|---|---|---|---|
| |line| under 7 | 766 | 598 | 50.0 |
| 7-14 | 473 | 367 | 47.4 |
| 14-21 | 255 | 210 | 50.5 |
| 21 and over | 397 | 348 | 49.1 |

Lines of 21+: n 397, mean favourite margin model 27.99 vs line 32.84 vs actual 34.65; dog takes 289.

Buckets, window:holdout, v5 common:

| line bucket | n | side takes | model side win % |
|---|---|---|---|
| |line| under 7 | 688 | 578 | 52.9 |
| 7-14 | 417 | 363 | 51.0 |
| 14-21 | 212 | 182 | 50.5 |
| 21 and over | 195 | 185 | 57.3 |

Lines of 21+: n 195, mean favourite margin model 22.70 vs line 29.52 vs actual 30.48; dog takes 155.

Buckets, window:fit, v6 all covered:

| line bucket | n | side takes | model side win % |
|---|---|---|---|
| |line| under 7 | 2336 | 1841 | 48.9 |
| 7-14 | 1654 | 1271 | 50.2 |
| 14-21 | 1054 | 861 | 49.2 |
| 21 and over | 1441 | 1251 | 50.2 |

Lines of 21+: n 1441, mean favourite margin model 25.82 vs line 30.87 vs actual 30.11; dog takes 1087.

Buckets, window:fit, v5 common:

| line bucket | n | side takes | model side win % |
|---|---|---|---|
| |line| under 7 | 2222 | 1858 | 49.3 |
| 7-14 | 1519 | 1268 | 48.4 |
| 14-21 | 958 | 815 | 47.4 |
| 21 and over | 875 | 777 | 48.6 |

Lines of 21+: n 875, mean favourite margin model 22.58 vs line 28.11 vs actual 27.36; dog takes 671.

### Robustness: book-only median (Opening, accuscore, teamrankings, numberfire, consensus dropped), spread

| group | model | n | pinned corr [95% CI] | book-only corr [95% CI] |
|---|---|---|---|---|
| window:fit | v6 all | 6403 | -0.006 [-0.034, 0.024] | -0.007 [-0.035, 0.021] |
| window:fit | v5 common | 5492 | 0.004 [-0.024, 0.033] | 0.005 [-0.023, 0.032] |
| window:holdout | v6 all | 1891 | -0.016 [-0.060, 0.027] | -0.016 [-0.060, 0.027] |
| window:holdout | v5 common | 1512 | 0.019 [-0.032, 0.068] | 0.019 [-0.032, 0.068] |
| all | v6 all | 8294 | -0.007 [-0.032, 0.018] | -0.008 [-0.032, 0.015] |
| all | v5 common | 7004 | 0.007 [-0.018, 0.031] | 0.007 [-0.017, 0.031] |

Groups where the book-only conclusion differs (sign of corr or CI excluding zero): 2.
- season:2018 / v6_all_covered: pinned: positive correlation: yes (point +0.001); 95% CI [-0.057, +0.070] includes zero; book-only: positive correlation: no (point -0.008); 95% CI [-0.059, +0.053] includes zero
- season:2018 / v6_common: pinned: positive correlation: yes (point +0.012); 95% CI [-0.066, +0.094] includes zero; book-only: positive correlation: no (point -0.000); 95% CI [-0.073, +0.080] includes zero

