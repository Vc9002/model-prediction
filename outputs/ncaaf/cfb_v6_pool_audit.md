# cfb-structural-v6 pool audit, as of 2026-11-15T02:10:00Z

Frozen fit sha256 `6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70` (verified before and after this run; nothing refitted, no frozen output rewritten). History snapshot sha256 `1fe90e1a11b8e5248003bff92ae62eec5bb17c4bd421a275b2f64ed36f1ced0c`, backfill sha256 `cc704059e4684edb9e580dff5033433424ee34e6a5fe96995c7c272ae82d8bf4`.

## Question

v6 sends a team with fewer than two events in a season, and any unseen name, to one per-season pooled lower-division pseudo-team (`__POOL_OTHER__:<season>`) instead of declining. Among the scored games, how many had an FBS side (ESPN division: the team-season plays an FBS-FBS `game_class` game) whose rating key was the pooled pseudo-team, and how many had a name that did not resolve by `resolve_team` or a shared ESPN id? Keys come from the same `RatingBook.identity` that `project` calls, over the frozen history snapshot; a key depends on (name, season), not on the hour of the decision.

## Verdict

**Pooled: zero. The reported numbers stand with respect to pool contamination.** Across every scored cohort (holdout 1,527 / 1,532 / 384, reference 575 and 576, ESPN-lined total and spread) 0 events had an FBS side rated as the per-season pooled pseudo-team, and an independent recount from raw ESPN ids agrees with the rating book on every snapshot game. Names with neither an ESPN id nor a resolve_team alias (`NAME:` keys) among the flagged team-events: 0; scored names never seen in the history: 0. **Unresolved names: nonzero but harmless.** 10 of the 1,527 holdout events, 25 of 575 and 25 of 576 reference events had an FBS side whose name `resolve_team` does not know. All are 4 programs (Florida International Panthers, Florida Intl Golden Panthers, Missouri State Bears, Sacramento State Hornets) that carry a stable `ID:<espn id>` rating key with 5 to 13 events in every flagged season, never the pooled key. Because the count is nonzero, a SECOND LOOK recomputes the headline numbers without any event they play in; see below. Second-look result: no headline statistic changes its verdict (sign of the correlation or whether its 95% CI excludes zero), the holdout v6-minus-v5 Brier delta stays negative with a CI excluding zero, and every control recompute reproduces the stored first-look value exactly. Rows that do flip on exclusion (season-level ESPN rows only): [{"market": "total", "group": "season:2026", "model": "v5_common", "orig_ci_excludes_zero": true, "sl_ci_excludes_zero": false}].

## Counts per cohort (events)

| cohort | events | FBS side POOLED | FBS-side name UNRESOLVED | FBS sides total | any-division side pooled (context) | any-division side unresolved (context) |
|---|---|---|---|---|---|---|
| holdout_common_1527 | 1527 | 0 | 10 | 3010 | 0 | 14 |
| holdout_v6_scoreable_existing | 1532 | 0 | 10 | 3020 | 0 | 14 |
| holdout_backfill_fbs_involved | 384 | 0 | 48 | 530 | 0 | 281 |
| holdout_all_scoreable | 3430 | 0 | 58 | 3550 | 75 | 1807 |
| espn_total_all | 8279 | 0 | 144 | 15656 | 0 | 974 |
| espn_spread_all | 8376 | 0 | 146 | 15753 | 0 | 1063 |
| reference_total | 575 | 0 | 25 | 1075 | 0 | 95 |
| reference_spread | 576 | 0 | 25 | 1077 | 0 | 95 |

`holdout_common_1527` is the headline locked-holdout cohort; `holdout_v6_scoreable_existing` (1,532) adds the 5 v6-only events; `holdout_backfill_fbs_involved` (384) is the separately scored backfill block; `holdout_all_scoreable` also holds FCS-vs-FCS backfill rows that no headline statistic uses (all of its pooled events are there). `reference_total` / `reference_spread` are the 575 / 576 reference events at their pinned decision times. `espn_*_all` are all ESPN-lined, v6-scored events for the market (fit window plus holdout window).

## Per season and per market

Events with an FBS side pooled / FBS-side name unresolved (of events), per season:

| cohort | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| holdout_common_1527 | - | - | - | - | - | - | - | 0 / 0 of 233 | 0 / 0 of 793 | 0 / 10 of 501 |
| holdout_v6_scoreable_existing | - | - | - | - | - | - | - | 0 / 0 of 233 | 0 / 0 of 793 | 0 / 10 of 506 |
| holdout_backfill_fbs_involved | - | - | - | - | - | - | - | 0 / 3 of 10 | 0 / 25 of 148 | 0 / 20 of 226 |
| holdout_all_scoreable | - | - | - | - | - | - | - | 0 / 3 of 386 | 0 / 25 of 1678 | 0 / 30 of 1366 |
| espn_total_all | 0 / 12 of 785 | 0 / 12 of 852 | 0 / 13 of 884 | 0 / 5 of 567 | 0 / 11 of 848 | 0 / 12 of 894 | 0 / 12 of 910 | 0 / 12 of 877 | 0 / 25 of 931 | 0 / 30 of 731 |
| espn_spread_all | 0 / 13 of 873 | 0 / 13 of 870 | 0 / 13 of 884 | 0 / 5 of 567 | 0 / 11 of 848 | 0 / 12 of 894 | 0 / 12 of 910 | 0 / 12 of 875 | 0 / 25 of 924 | 0 / 30 of 731 |
| reference_total | - | - | - | - | - | - | - | - | - | 0 / 25 of 575 |
| reference_spread | - | - | - | - | - | - | - | - | - | 0 / 25 of 576 |

Per market (total vs spread) the same counts, from the lined cohorts above:

| market | cohort | events | FBS pooled | FBS unresolved |
|---|---|---|---|---|
| total | reference_total | 575 | 0 | 25 |
| total | espn_total_all | 8279 | 0 | 144 |
| total | espn_total_all window:fit | 6380 | 0 | 86 |
| total | espn_total_all window:holdout | 1899 | 0 | 58 |
| spread | reference_spread | 576 | 0 | 25 |
| spread | espn_spread_all | 8376 | 0 | 146 |
| spread | espn_spread_all window:fit | 6485 | 0 | 88 |
| spread | espn_spread_all window:holdout | 1891 | 0 | 58 |

## Independent checks

- Pooled status recomputed from raw ESPN ids and `resolve_team` (no RatingBook) over all 16378 snapshot games: 0 mismatches against `RatingBook.identity`. Whole snapshot: 426 pooled sides over 426 pooled team-seasons; FBS pooled sides: 0.
- Scored names the history name table has never seen (would hit the resolve_team / `NAME:` fallback): {"holdout_common_1527": 0, "holdout_v6_scoreable_existing": 0, "holdout_backfill_fbs_involved": 0, "holdout_all_scoreable": 0, "espn_total_all": 0, "espn_spread_all": 0, "reference_total": 0, "reference_spread": 0}.
- Key collisions: rating keys standing for several ESPN ids: 0; ESPN ids spread over several keys: 0 (446 distinct keys, 142 resolved).
- The fail-closed path does fire: no-call rows in the cache {"NO_CALL_CFB_V6_UNRATED_TEAM": 183}, of which holdout {"NO_CALL_CFB_V6_UNRATED_TEAM": 26}; those events are not scored anywhere.

## The unresolved FBS names (rated under a stable ESPN-id key, not pooled)

- Florida Intl Golden Panthers: rating key `ID:2229`; seasons [2017]; events per season {'2017': 13}; flagged team-events 13.
- Florida International Panthers: rating key `ID:2229`; seasons [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]; events per season {'2018': 13, '2019': 13, '2020': 5, '2021': 12, '2022': 12, '2023': 12, '2024': 12, '2025': 13, '2026': 10}; flagged team-events 101.
- Missouri State Bears: rating key `ID:2623`; seasons [2025, 2026]; events per season {'2025': 13, '2026': 10}; flagged team-events 23.
- Sacramento State Hornets: rating key `ID:16`; seasons [2026]; events per season {'2026': 11}; flagged team-events 11.

`resolve_team` has no alias for these, so it returns None and the rating key falls to `ID:<espn id>`. Every name variant of the same ESPN id lands on the same key, and the team has at least two events in its season (the counts above), so it is a proper own-team rating, never the pooled pseudo-team.

## SECOND LOOK: recomputation with the flagged games excluded

SECOND LOOK, not a replacement of the first look. No game had an FBS side rated as the pooled pseudo-team, so no pool contamination exists to remove. The flagged team-events are the FBS sides whose name did not resolve (rated under a stable `ID:` key, see above). The recomputation excludes every event in which such a side plays (146 events, all cohorts) as a conservative sensitivity bound, using the frozen v6 values and the frozen v5 calibration unchanged. The unexcluded recompute reproduces the stored first-look numbers: holdout True, reference True, ESPN True.

### Locked holdout, common cohort (original n 1527, second look n 1517)

| statistic | original | second look | delta |
|---|---|---|---|
| v6 Brier | 0.17975 | 0.17984 | +0.00009 |
| v5 Brier | 0.19003 | 0.18975 | -0.00028 |
| v6 log loss | 0.5333 | 0.5335 | +0.0002 |
| v5 log loss | 0.5589 | 0.5583 | -0.0006 |
| v6 ECE | 0.0225 | 0.0229 | +0.0004 |
| v5 ECE | 0.0448 | 0.0448 | -0.0000 |
| v6 margin MAE | 12.286 | 12.262 | -0.024 |
| v5 margin MAE | 12.885 | 12.847 | -0.038 |
| Brier delta v6-v5 (95% CI) | -0.01029 [-0.01524, -0.00553] | -0.00992 [-0.01495, -0.00505] | +0.00037 |
| margin log score delta (95% CI) | +0.04790 [+0.03055, +0.06395] | +0.04725 [+0.03030, +0.06357] | -0.00065 |
| total log score delta (95% CI) | +0.01496 [+0.00623, +0.02399] | +0.01431 [+0.00571, +0.02300] | -0.00065 |
| margin abs error delta (95% CI) | -0.59859 [-0.84682, -0.33170] | -0.58468 [-0.83370, -0.32098] | +0.01391 |

| season | n orig | n second look | v6 Brier orig | v6 Brier SL | v5 Brier orig | v5 Brier SL |
|---|---|---|---|---|---|---|
| 2024 | 233 | 233 | 0.20274 | 0.20274 | 0.20603 | 0.20603 |
| 2025 | 793 | 793 | 0.17387 | 0.17387 | 0.18220 | 0.18220 |
| 2026 | 501 | 491 | 0.17836 | 0.17860 | 0.19499 | 0.19422 |

Backfill-only FBS-involved block: n 384 -> 336, v6 Brier 0.10460 -> 0.09414.

### Reference 575 / 576 (frozen v6 vs v5, date-clustered 95% CI)

| market | set | original corr [CI] (n) | second look corr [CI] (n) | side orig | side SL |
|---|---|---|---|---|---|
| total | v6_common_set | -0.011 [-0.091, +0.054] (n 575) | -0.023 [-0.103, +0.039] (n 550) | 232-256 | 222-247 |
| total | v5_common_set | -0.033 [-0.119, +0.041] (n 575) | -0.032 [-0.115, +0.037] (n 550) | 218-257 | 206-246 |
| total | v5_full_set | -0.033 [-0.119, +0.041] (n 575) | -0.032 [-0.115, +0.037] (n 550) | 218-257 | 206-246 |
| spread | v6_common_set | +0.042 [-0.023, +0.110] (n 576) | +0.039 [-0.026, +0.110] (n 551) | 237-246 | 226-236 |
| spread | v5_common_set | +0.009 [-0.090, +0.105] (n 576) | +0.002 [-0.099, +0.098] (n 551) | 264-233 | 253-224 |
| spread | v5_full_set | +0.009 [-0.090, +0.105] (n 576) | +0.002 [-0.099, +0.098] (n 551) | 264-233 | 253-224 |

Model SD / line SD / actual SD and MAE (model / line), original vs second look:

| market | set | SD model orig | SD model SL | MAE model orig | MAE model SL | MAE line orig | MAE line SL |
|---|---|---|---|---|---|---|---|
| total | v6_common_set | 5.04 | 5.03 | 13.12 | 13.16 | 12.43 | 12.44 |
| total | v5_common_set | 4.13 | 4.17 | 13.15 | 13.15 | 12.43 | 12.44 |
| spread | v6_common_set | 14.82 | 14.92 | 12.56 | 12.52 | 11.65 | 11.59 |
| spread | v5_common_set | 11.78 | 11.94 | 14.64 | 14.59 | 11.65 | 11.59 |

### ESPN closing lines (pinned median), corr(model - line, actual - line) with date-clustered 95% CI

#### total

| group | model | original corr [CI] (n) | second look corr [CI] (n) | side orig | side SL |
|---|---|---|---|---|---|
| window:fit | v6_all_covered | +0.020 [-0.009, +0.048] (n 6380) | +0.020 [-0.011, +0.047] (n 6294) | 2628-2546 | 2594-2512 |
| window:fit | v6_common | +0.028 [-0.001, +0.058] (n 5567) | +0.028 [-0.001, +0.058] (n 5567) | 2325-2190 | 2325-2190 |
| window:fit | v5_common | +0.023 [-0.006, +0.051] (n 5567) | +0.023 [-0.006, +0.051] (n 5567) | 2341-2291 | 2341-2291 |
| window:holdout | v6_all_covered | -0.034 [-0.078, +0.008] (n 1899) | -0.035 [-0.082, +0.007] (n 1841) | 743-790 | 722-768 |
| window:holdout | v6_common | -0.008 [-0.057, +0.041] (n 1511) | -0.006 [-0.056, +0.043] (n 1501) | 595-608 | 593-602 |
| window:holdout | v5_common | -0.040 [-0.097, +0.016] (n 1511) | -0.037 [-0.094, +0.019] (n 1501) | 596-616 | 593-610 |
| season:2017 | v6_all_covered | +0.005 [-0.084, +0.091] (n 785) | +0.004 [-0.087, +0.091] (n 773) | 312-310 | 308-305 |
| season:2017 | v6_common | +0.007 [-0.087, +0.095] (n 729) | +0.007 [-0.087, +0.095] (n 729) | 293-286 | 293-286 |
| season:2017 | v5_common | -0.010 [-0.084, +0.056] (n 729) | -0.010 [-0.084, +0.056] (n 729) | 307-302 | 307-302 |
| season:2018 | v6_all_covered | -0.072 [-0.138, +0.004] (n 852) | -0.076 [-0.146, +0.001] (n 840) | 309-362 | 305-356 |
| season:2018 | v6_common | -0.062 [-0.137, +0.019] (n 749) | -0.062 [-0.137, +0.019] (n 749) | 276-310 | 276-310 |
| season:2018 | v5_common | -0.057 [-0.143, +0.029] (n 749) | -0.057 [-0.143, +0.029] (n 749) | 302-319 | 302-319 |
| season:2019 | v6_all_covered | +0.048 [-0.026, +0.122] (n 884) | +0.047 [-0.026, +0.121] (n 871) | 352-364 | 347-361 |
| season:2019 | v6_common | +0.037 [-0.035, +0.110] (n 739) | +0.037 [-0.035, +0.110] (n 739) | 296-302 | 296-302 |
| season:2019 | v5_common | +0.051 [-0.028, +0.132] (n 739) | +0.051 [-0.028, +0.132] (n 739) | 312-322 | 312-322 |
| season:2020 | v6_all_covered | +0.060 [-0.029, +0.143] (n 567) | +0.059 [-0.031, +0.143] (n 562) | 254-212 | 252-210 |
| season:2020 | v6_common | +0.077 [-0.011, +0.167] (n 518) | +0.077 [-0.011, +0.167] (n 518) | 236-187 | 236-187 |
| season:2020 | v5_common | +0.038 [-0.043, +0.120] (n 518) | +0.038 [-0.043, +0.120] (n 518) | 226-205 | 226-205 |
| season:2021 | v6_all_covered | -0.010 [-0.076, +0.059] (n 848) | -0.012 [-0.078, +0.055] (n 837) | 342-363 | 337-359 |
| season:2021 | v6_common | +0.014 [-0.048, +0.077] (n 751) | +0.014 [-0.048, +0.077] (n 751) | 312-312 | 312-312 |
| season:2021 | v5_common | +0.039 [-0.028, +0.101] (n 751) | +0.039 [-0.028, +0.101] (n 751) | 313-300 | 313-300 |
| season:2022 | v6_all_covered | -0.011 [-0.097, +0.069] (n 894) | -0.010 [-0.099, +0.070] (n 882) | 377-360 | 372-355 |
| season:2022 | v6_common | -0.011 [-0.110, +0.076] (n 767) | -0.011 [-0.110, +0.076] (n 767) | 329-309 | 329-309 |
| season:2022 | v5_common | -0.016 [-0.093, +0.058] (n 767) | -0.016 [-0.093, +0.058] (n 767) | 296-330 | 296-330 |
| season:2023 | v6_all_covered | +0.020 [-0.047, +0.089] (n 910) | +0.020 [-0.047, +0.085] (n 898) | 400-348 | 395-343 |
| season:2023 | v6_common | +0.035 [-0.039, +0.120] (n 776) | +0.035 [-0.039, +0.120] (n 776) | 341-293 | 341-293 |
| season:2023 | v5_common | +0.045 [-0.047, +0.137] (n 776) | +0.045 [-0.047, +0.137] (n 776) | 349-304 | 349-304 |
| season:2024 | v6_all_covered | +0.094 [+0.015, +0.164] (n 877) | +0.098 [+0.014, +0.161] (n 865) | 371-314 | 365-310 |
| season:2024 | v6_common | +0.095 [+0.005, +0.176] (n 765) | +0.095 [+0.005, +0.176] (n 765) | 328-272 | 328-272 |
| season:2024 | v5_common | +0.051 [-0.028, +0.133] (n 765) | +0.051 [-0.028, +0.133] (n 765) | 325-306 | 325-306 |
| season:2025 | v6_all_covered | +0.015 [-0.040, +0.068] (n 931) | +0.020 [-0.033, +0.074] (n 906) | 379-356 | 370-346 |
| season:2025 | v6_common | +0.032 [-0.038, +0.098] (n 784) | +0.032 [-0.038, +0.098] (n 784) | 321-295 | 321-295 |
| season:2025 | v5_common | -0.011 [-0.103, +0.071] (n 784) | -0.011 [-0.103, +0.071] (n 784) | 318-298 | 318-298 |
| season:2026 | v6_all_covered | -0.105 [-0.193, -0.037] (n 731) | -0.114 [-0.207, -0.043] (n 701) | 275-347 | 265-335 |
| season:2026 | v6_common | -0.089 [-0.170, -0.013] (n 500) | -0.086 [-0.170, -0.010] (n 490) | 188-232 | 186-226 |
| season:2026 | v5_common | -0.094 [-0.182, -0.003] (n 500) | -0.085 [-0.173, +0.008] (n 490) | 189-221 | 186-215 |
| all | v6_all_covered | +0.010 [-0.015, +0.034] (n 8279) | +0.010 [-0.015, +0.034] (n 8135) | 3371-3336 | 3316-3280 |
| all | v6_common | +0.023 [-0.003, +0.049] (n 7078) | +0.023 [-0.002, +0.049] (n 7068) | 2920-2798 | 2918-2792 |
| all | v5_common | +0.013 [-0.013, +0.039] (n 7078) | +0.014 [-0.012, +0.040] (n 7068) | 2937-2907 | 2934-2901 |

#### spread

| group | model | original corr [CI] (n) | second look corr [CI] (n) | side orig | side SL |
|---|---|---|---|---|---|
| window:fit | v6_all_covered | -0.006 [-0.034, +0.024] (n 6485) | -0.010 [-0.037, +0.021] (n 6397) | 2591-2633 | 2537-2613 |
| window:fit | v6_common | -0.009 [-0.041, +0.022] (n 5574) | -0.009 [-0.041, +0.022] (n 5574) | 2151-2267 | 2151-2267 |
| window:fit | v5_common | +0.004 [-0.024, +0.033] (n 5574) | +0.004 [-0.024, +0.033] (n 5574) | 2294-2424 | 2294-2424 |
| window:holdout | v6_all_covered | -0.016 [-0.060, +0.027] (n 1891) | -0.014 [-0.058, +0.031] (n 1833) | 750-773 | 729-747 |
| window:holdout | v6_common | +0.019 [-0.027, +0.066] (n 1512) | +0.019 [-0.027, +0.067] (n 1502) | 607-589 | 604-585 |
| window:holdout | v5_common | +0.019 [-0.032, +0.068] (n 1512) | +0.020 [-0.031, +0.067] (n 1502) | 689-619 | 683-615 |
| season:2017 | v6_all_covered | +0.034 [-0.058, +0.121] (n 873) | +0.030 [-0.063, +0.116] (n 860) | 382-331 | 375-327 |
| season:2017 | v6_common | -0.021 [-0.117, +0.069] (n 737) | -0.021 [-0.117, +0.069] (n 737) | 303-289 | 303-289 |
| season:2017 | v5_common | -0.015 [-0.109, +0.090] (n 737) | -0.015 [-0.109, +0.090] (n 737) | 297-314 | 297-314 |
| season:2018 | v6_all_covered | +0.001 [-0.057, +0.070] (n 870) | +0.002 [-0.056, +0.070] (n 857) | 349-340 | 340-338 |
| season:2018 | v6_common | +0.012 [-0.066, +0.094] (n 749) | +0.012 [-0.066, +0.094] (n 749) | 288-295 | 288-295 |
| season:2018 | v5_common | -0.006 [-0.071, +0.051] (n 749) | -0.006 [-0.071, +0.051] (n 749) | 319-324 | 319-324 |
| season:2019 | v6_all_covered | -0.018 [-0.081, +0.046] (n 884) | -0.023 [-0.086, +0.042] (n 871) | 350-365 | 341-363 |
| season:2019 | v6_common | -0.025 [-0.081, +0.039] (n 739) | -0.025 [-0.081, +0.039] (n 739) | 284-303 | 284-303 |
| season:2019 | v5_common | -0.031 [-0.096, +0.031] (n 739) | -0.031 [-0.096, +0.031] (n 739) | 285-348 | 285-348 |
| season:2020 | v6_all_covered | +0.028 [-0.067, +0.118] (n 567) | +0.029 [-0.067, +0.119] (n 562) | 239-240 | 238-238 |
| season:2020 | v6_common | +0.037 [-0.061, +0.128] (n 518) | +0.037 [-0.061, +0.128] (n 518) | 218-219 | 218-219 |
| season:2020 | v5_common | +0.074 [-0.019, +0.159] (n 518) | +0.074 [-0.019, +0.159] (n 518) | 226-224 | 226-224 |
| season:2021 | v6_all_covered | -0.039 [-0.120, +0.044] (n 848) | -0.045 [-0.123, +0.040] (n 837) | 321-365 | 314-361 |
| season:2021 | v6_common | -0.017 [-0.110, +0.063] (n 751) | -0.017 [-0.110, +0.063] (n 751) | 274-322 | 274-322 |
| season:2021 | v5_common | +0.044 [-0.031, +0.117] (n 751) | +0.044 [-0.031, +0.117] (n 751) | 311-329 | 311-329 |
| season:2022 | v6_all_covered | -0.028 [-0.092, +0.037] (n 894) | -0.036 [-0.099, +0.026] (n 882) | 345-366 | 337-365 |
| season:2022 | v6_common | -0.014 [-0.110, +0.083] (n 767) | -0.014 [-0.110, +0.083] (n 767) | 291-308 | 291-308 |
| season:2022 | v5_common | -0.004 [-0.074, +0.063] (n 767) | -0.004 [-0.074, +0.063] (n 767) | 307-333 | 307-333 |
| season:2023 | v6_all_covered | -0.021 [-0.108, +0.067] (n 910) | -0.026 [-0.115, +0.061] (n 898) | 362-356 | 355-352 |
| season:2023 | v6_common | -0.056 [-0.134, +0.020] (n 776) | -0.056 [-0.134, +0.020] (n 776) | 292-306 | 292-306 |
| season:2023 | v5_common | -0.024 [-0.104, +0.055] (n 776) | -0.024 [-0.104, +0.055] (n 776) | 318-322 | 318-322 |
| season:2024 | v6_all_covered | +0.011 [-0.051, +0.078] (n 875) | +0.010 [-0.054, +0.080] (n 863) | 335-350 | 328-348 |
| season:2024 | v6_common | +0.029 [-0.040, +0.097] (n 763) | +0.029 [-0.040, +0.097] (n 763) | 288-303 | 288-303 |
| season:2024 | v5_common | +0.030 [-0.030, +0.092] (n 763) | +0.030 [-0.030, +0.092] (n 763) | 345-312 | 345-312 |
| season:2025 | v6_all_covered | +0.024 [-0.037, +0.087] (n 924) | +0.028 [-0.031, +0.085] (n 899) | 384-358 | 374-347 |
| season:2025 | v6_common | +0.058 [-0.004, +0.118] (n 786) | +0.058 [-0.004, +0.118] (n 786) | 329-294 | 329-294 |
| season:2025 | v5_common | +0.032 [-0.039, +0.100] (n 786) | +0.032 [-0.039, +0.100] (n 786) | 345-332 | 345-332 |
| season:2026 | v6_all_covered | -0.078 [-0.145, -0.013] (n 731) | -0.079 [-0.147, -0.016] (n 701) | 274-335 | 264-321 |
| season:2026 | v6_common | -0.047 [-0.124, +0.022] (n 500) | -0.048 [-0.128, +0.023] (n 490) | 191-217 | 188-213 |
| season:2026 | v5_common | -0.026 [-0.106, +0.061] (n 500) | -0.027 [-0.104, +0.061] (n 490) | 230-205 | 224-201 |
| all | v6_all_covered | -0.007 [-0.032, +0.018] (n 8376) | -0.010 [-0.034, +0.016] (n 8230) | 3341-3406 | 3266-3360 |
| all | v6_common | -0.002 [-0.029, +0.024] (n 7086) | -0.002 [-0.029, +0.024] (n 7076) | 2758-2856 | 2755-2852 |
| all | v5_common | +0.007 [-0.018, +0.031] (n 7086) | +0.007 [-0.018, +0.031] (n 7076) | 2983-3043 | 2977-3039 |

## Every flagged team-event

148 distinct team-events with an FBS side whose name did not resolve (reason `fbs_name_unresolved`); 0 pooled. Cohorts are the audited cohorts the event belongs to. A further 33 team-events are listed in the JSON only (the team is FBS in another season and FCS in this one; broad definition, same stable key, not pooled).

| team | season | date | side | rating key | events that season | reason | cohorts |
|---|---|---|---|---|---|---|---|
| Florida Intl Golden Panthers | 2017 | 2017-08-31 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-09-08 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-09-23 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-09-30 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-10-07 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-10-14 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-10-28 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-11-04 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-11-12 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-11-19 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-11-25 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-12-02 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida Intl Golden Panthers | 2017 | 2017-12-22 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-09-01 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-09-08 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-09-15 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-09-22 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-09-29 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_spread_all |
| Florida International Panthers | 2018 | 2018-10-13 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-10-20 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-10-27 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-11-03 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-11-11 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-11-17 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-11-24 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2018 | 2018-12-21 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-08-30 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-09-07 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-09-14 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-09-21 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-10-05 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-10-12 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-10-19 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-10-26 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-11-02 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-11-09 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-11-24 | home | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-11-30 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2019 | 2019-12-21 | away | `ID:2229` | 13 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2020 | 2020-09-26 | away | `ID:2229` | 5 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2020 | 2020-10-10 | home | `ID:2229` | 5 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2020 | 2020-10-23 | home | `ID:2229` | 5 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2020 | 2020-11-14 | home | `ID:2229` | 5 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2020 | 2020-11-21 | away | `ID:2229` | 5 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-09-11 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-09-18 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-09-25 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-10-02 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-10-08 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-10-23 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-10-30 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-11-06 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-11-13 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-11-21 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2021 | 2021-11-27 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-09-01 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-09-10 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-09-24 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-10-02 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-10-08 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-10-15 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-10-22 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-10-29 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-11-05 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-11-13 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-11-19 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2022 | 2022-11-26 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-08-27 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-09-02 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-09-09 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-09-16 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-09-23 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-10-05 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-10-11 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-10-18 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-10-25 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-11-11 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-11-19 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2023 | 2023-11-25 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-08-31 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-09-07 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-09-14 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-09-21 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-09-28 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-10-08 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-10-17 | away | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-10-22 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-10-29 | home | `ID:2229` | 12 | fbs_name_unresolved | espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-11-16 | away | `ID:2229` | 12 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-11-23 | away | `ID:2229` | 12 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2024 | 2024-11-30 | home | `ID:2229` | 12 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-08-30 | away | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-08-30 | home | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-09-06 | away | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-09-06 | away | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-09-13 | home | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-09-14 | home | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-09-20 | home | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-09-20 | home | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-09-27 | home | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-10-04 | away | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-10-08 | away | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-10-15 | away | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-10-21 | home | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-10-23 | away | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-10-30 | away | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-10-30 | home | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-11-08 | away | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-11-08 | away | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-11-15 | home | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-11-15 | home | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-11-22 | home | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-11-22 | away | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-11-29 | away | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-11-29 | home | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Missouri State Bears | 2025 | 2025-12-19 | away | `ID:2623` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2025 | 2025-12-27 | away | `ID:2229` | 13 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Sacramento State Hornets | 2026 | 2026-08-29 | away | `ID:16` | 11 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-09-05 | away | `ID:2623` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2026 | 2026-09-05 | away | `ID:2229` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Sacramento State Hornets | 2026 | 2026-09-06 | home | `ID:16` | 11 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2026 | 2026-09-12 | home | `ID:2229` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-09-12 | home | `ID:2623` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Sacramento State Hornets | 2026 | 2026-09-13 | away | `ID:16` | 11 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Florida International Panthers | 2026 | 2026-09-19 | away | `ID:2229` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-09-19 | home | `ID:2623` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Sacramento State Hornets | 2026 | 2026-09-20 | home | `ID:16` | 11 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Florida International Panthers | 2026 | 2026-09-26 | home | `ID:2229` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-09-27 | away | `ID:2623` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Sacramento State Hornets | 2026 | 2026-09-27 | home | `ID:16` | 11 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-10-08 | away | `ID:2623` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2026 | 2026-10-08 | home | `ID:2229` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Sacramento State Hornets | 2026 | 2026-10-10 | away | `ID:16` | 11 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all |
| Florida International Panthers | 2026 | 2026-10-14 | away | `ID:2229` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-10-14 | home | `ID:2623` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Sacramento State Hornets | 2026 | 2026-10-18 | home | `ID:16` | 11 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Florida International Panthers | 2026 | 2026-10-20 | home | `ID:2229` | 10 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-10-20 | away | `ID:2623` | 10 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Sacramento State Hornets | 2026 | 2026-10-24 | away | `ID:16` | 11 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-10-28 | home | `ID:2623` | 10 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Sacramento State Hornets | 2026 | 2026-10-31 | home | `ID:16` | 11 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Florida International Panthers | 2026 | 2026-10-31 | away | `ID:2229` | 10 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Sacramento State Hornets | 2026 | 2026-11-05 | home | `ID:16` | 11 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-11-07 | away | `ID:2623` | 10 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Florida International Panthers | 2026 | 2026-11-07 | home | `ID:2229` | 10 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Sacramento State Hornets | 2026 | 2026-11-12 | away | `ID:16` | 11 | fbs_name_unresolved | holdout_common_1527, holdout_v6_scoreable_existing, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Missouri State Bears | 2026 | 2026-11-14 | home | `ID:2623` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
| Florida International Panthers | 2026 | 2026-11-14 | away | `ID:2229` | 10 | fbs_name_unresolved | holdout_backfill_fbs_involved, holdout_all_scoreable, espn_total_all, espn_spread_all, reference_total, reference_spread |
