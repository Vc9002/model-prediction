# NCAAF line movement (W2): ESPN open-to-close and 2026 intraday ladders

- generated_at_utc: `2026-11-15T06:51:49Z`; final run: `True`; holdout_reads: `1`
- rule_sha256: `1481273f1fbd860a5e35ecc25a864818e74818f564aa0f6d5710ea42f524145f`; script sha256 (LF-normalised): `046999042e0ad3c73eb7aae4fff758bec2f55cfaa3ec8c6b54f9837adfea58d7`
- verdicts: espn_total=INSUFFICIENT_DATA, espn_spread=INSUFFICIENT_DATA, intraday_polymarket=DESCRIPTIVE_ONLY

## Part A: ESPN open-to-close movement (untimestamped, close-time values)

ESPN open and close blocks carry no `observed_at`: the movement is only known at close, so the best possible verdict is UPPER_BOUND_PASS and nothing here can enter v6.1. Pinned definitions: `move_total = close total - open total`; `move_spread = close home line - open home line`; open = median over eligible non-live providers of the open value (Opening provider 23 contributes its own current line in 2016-2017); Caesars-family providers dropped from open and close in 2017-2018.

Initial-spread sign conflicts vs fav_side (reported, not resolved): 330.

### Coverage of open values by season (lined cohort)

| season | games | total lined | total with open | total non-zero move | spread lined | spread with open | spread non-zero move |
|---|---|---|---|---|---|---|---|
| 2017 | 1541 | 785 | 4 | 4 | 873 | 4 | 4 |
| 2018 | 1550 | 852 | 0 | 0 | 870 | 0 | 0 |
| 2019 | 1611 | 884 | 0 | 0 | 884 | 0 | 0 |
| 2020 | 587 | 567 | 0 | 0 | 567 | 0 | 0 |
| 2021 | 1475 | 848 | 0 | 0 | 848 | 0 | 0 |
| 2022 | 1588 | 894 | 0 | 0 | 894 | 0 | 0 |
| 2023 | 1586 | 910 | 424 | 388 | 910 | 424 | 390 |
| 2024 | 1657 | 877 | 877 | 720 | 875 | 875 | 743 |
| 2025 | 1678 | 931 | 931 | 757 | 924 | 924 | 721 |
| 2026 | 1366 | 731 | 731 | 618 | 731 | 731 | 616 |

### Movement cohort by window

| market | window | lined | with open | non-zero move | open coverage |
|---|---|---|---|---|---|
| total | select | 4590 | 4 | 4 | 0.1% |
| total | validation | 1790 | 1064 | 918 | 59.4% |
| total | holdout | 1861 | 1861 | 1532 | 100.0% |
| total | post_holdout | 38 | 38 | 33 | 100.0% |
| spread | select | 4696 | 4 | 4 | 0.1% |
| spread | validation | 1789 | 1063 | 930 | 59.4% |
| spread | holdout | 1853 | 1853 | 1506 | 100.0% |
| spread | post_holdout | 38 | 38 | 34 | 100.0% |

### Primary statistic (dMSE = MSE(line only) - MSE(line + movement), positive = movement helps)

| market | n select | n validation | validation dMSE [95% CI] | holdout dMSE (point) | verdict |
|---|---|---|---|---|---|
| total | 4 | 1064 | n/a (select n = 4; not interpretable) | n/a (not interpretable) | INSUFFICIENT_DATA |
| spread | 4 | 1063 | n/a (select n = 4; not interpretable) | n/a (not interpretable) | INSUFFICIENT_DATA |

- total: baseline coef [87.67297077187585, -0.24794136277260043], with-source coef [57.26878102592485, 0.23035484464054395, -2.8741986884843804], validation MSE baseline 766.857 vs with movement 645.541; n holdout 1861; reason: select n 4 < 400
- spread: baseline coef [11.33751263902933, 1.9754021509329907], with-source coef [34.50295566502462, 2.2453201970443364, 9.147783251231523], validation MSE baseline 712.479 vs with movement 2751.248; n holdout 1853; reason: select n 4 < 400

NOT INTERPRETABLE: the pinned select window (<= 2022-11-11) carries almost no ESPN open values, so the baseline and with-source coefficients above were fit on a handful of rows and the dMSE is meaningless. The verdict is INSUFFICIENT_DATA as the pinned rule requires; the rule was not altered. The labelled exploratory refit below is the only usable out-of-sample read.

### Exploratory refit (labelled, NOT the pinned primary; coefficients fit on validation rows with open values)

EXPLORATORY and NOT the pinned primary statistic: the pinned select window (<= 2022-11-11) has no open values, so the pinned test is untestable; coefficients here are fit on validation rows that carry open values. Never a verdict input.

- total / fit_validation_through_2023_eval_validation_2024: n fit 418, n eval 646, dMSE -3.385 [-6.122, -0.054] (positive = movement helps)
- total / fit_all_validation_eval_holdout: n fit 1064, n eval 1861, dMSE 0.140 [-0.140, 0.439] (positive = movement helps)
- spread / fit_validation_through_2023_eval_validation_2024: n fit 418, n eval 645, dMSE -0.093 [-1.768, 1.403] (positive = movement helps)
- spread / fit_all_validation_eval_holdout: n fit 1063, n eval 1853, dMSE -0.647 [-1.499, 0.320] (positive = movement helps)

### Exploratory (labelled, not verdict inputs): movement vs outcome minus close

- select / total: n 4, non-zero movement 4, corr n/a, slope n/a, mean outcome in movement direction n/a, continuation share n/a
- select / spread_expected_home_margin_move: n 4, non-zero movement 4, corr n/a, slope n/a, mean outcome in movement direction n/a, continuation share n/a
- validation / total: n 1064, non-zero movement 918, corr -0.012 [-0.069, 0.051], slope -0.079 [-0.448, 0.334], mean outcome in movement direction -0.014 [-0.949, 0.863], continuation share 49.3%
    - |move| (0,1]: n 339, mean outcome in movement direction 1.516 [0.174, 2.869], continuation 50.6%
    - |move| (1,3]: n 402, mean outcome in movement direction -0.430 [-1.766, 0.832], continuation 49.4%
    - |move| >3: n 177, mean outcome in movement direction -1.997 [-4.563, 0.795], continuation 46.6%
- validation / spread_expected_home_margin_move: n 1063, non-zero movement 930, corr 0.051 [-0.015, 0.114], slope 0.317 [-0.096, 0.632], mean outcome in movement direction 0.215 [-0.577, 1.056], continuation share 51.8%
    - |move| (0,1]: n 392, mean outcome in movement direction -0.705 [-2.148, 0.897], continuation 49.9%
    - |move| (1,3]: n 402, mean outcome in movement direction 0.971 [-0.496, 2.267], continuation 54.0%
    - |move| >3: n 136, mean outcome in movement direction 0.629 [-1.628, 3.038], continuation 51.1%
- holdout / total: n 1861, non-zero movement 1532, corr 0.020 [-0.022, 0.062], slope 0.123 [-0.143, 0.366], mean outcome in movement direction 0.216 [-0.531, 0.960], continuation share 51.6%
    - |move| (0,1]: n 618, mean outcome in movement direction 0.324 [-0.727, 1.419], continuation 50.8%
    - |move| (1,3]: n 625, mean outcome in movement direction 0.140 [-1.090, 1.333], continuation 52.2%
    - |move| >3: n 289, mean outcome in movement direction 0.149 [-1.712, 2.087], continuation 51.9%
- holdout / spread_expected_home_margin_move: n 1853, non-zero movement 1506, corr -0.002 [-0.044, 0.043], slope -0.015 [-0.285, 0.260], mean outcome in movement direction -0.144 [-0.888, 0.618], continuation share 51.1%
    - |move| (0,1]: n 653, mean outcome in movement direction 0.205 [-0.661, 1.064], continuation 50.9%
    - |move| (1,3]: n 591, mean outcome in movement direction -0.617 [-1.910, 0.676], continuation 51.0%
    - |move| >3: n 262, mean outcome in movement direction 0.051 [-1.644, 1.770], continuation 51.7%

Continuation = the realised outcome landed beyond the close in the direction the line moved; reversal = the opposite. Both are exploratory labels.

## Part B: 2026 intraday movement (DESCRIPTIVE_ONLY)

main-line rung = ladder rung whose over (totals) or home-side (spreads) midpoint is nearest 50 percent at each snapshot. These are Polymarket-lineage ladder quotes, NOT ESPN lines. Movement = last pre-kickoff main rung minus first observed main rung.

Verdict: **DESCRIPTIVE_ONLY**. No select or validation data exist (every 2026 game is holdout or post-holdout), so no number here decides anything.

- Source: runtime root odds/ncaaf/<date>/polymarket_snapshots.jsonl (Polymarket-lineage ladder quotes); files 42, rows 166477 (raw 166477, duplicates dropped 0).
- market_quotes.db (read-only, reconciliation only): 141448 distinct keys, 141435 present in the JSONL, 13 absent ({'2026-10-23 total': 5, '2026-10-23 spread': 5, '2026-10-23 moneyline': 3}).
- Event match: 1111 of 1145 Polymarket events matched 1:1 to base rows; unmatched: {'no_base_row': 8, 'name_mismatch_one_side_found': 1, 'after_last_base_row': 25}.
- Quote filtering: {'quotes_open_matched': 161063, 'quotes_pre_kickoff': 137772, 'quotes_full_game_spread': 48894, 'quotes_full_game_total': 24907, 'spread_rows_side_unresolved_dropped': 864, 'spread_rows_resolved_side': {'away': 48012}, 'multi_rung_snapshots': 4371, 'single_rung_snapshots_excluded': 445}.
- Coverage: 2026 base rows 1366 (total lined 731, spread lined 731), by window {'holdout': 1270, 'post_holdout': 96}; movement defined by market and window {'total|select': 0, 'total|validation': 0, 'total|holdout': 564, 'total|post_holdout': 8, 'spread|select': 0, 'spread|validation': 0, 'spread|holdout': 662, 'spread|post_holdout': 8}; with an ESPN close in the holdout window {'total': 376, 'spread': 409}; post-holdout (2026-11-14) games excluded from outcome comparison: 16.

### Movement distribution (points)

- total: {'event_market_rows': 843, 'with_ge2_snapshots': 577, 'move_defined': 572, 'mean_move_points': 0.291958041958042, 'mean_abs_move_points': 2.3653846153846154, 'median_abs_move_points': 1.0, 'zero_move_share': 0.3583916083916084, 'abs_move_quantiles_p50_p90_p99': [1.0, 6.0, 17.289999999999964], 'median_span_hours': 11.49773991164234, 'median_first_lead_hours': 14.919291987485355, 'last_lead_hours_p10_p50_p90': [0.6606863236162398, 2.7570504093170167, 5.950085054112805], 'share_last_snapshot_within_6h': 0.9108391608391608, 'width_first_median': 0.010000000000000009, 'width_last_median': 0.010000000000000009, 'share_first_bracketed': 0.8601398601398601, 'share_last_bracketed': 0.8444055944055944, 'share_first_at_ladder_edge': 0.2465034965034965, 'share_last_at_ladder_edge': 0.24825174825174826}
- spread: {'event_market_rows': 866, 'with_ge2_snapshots': 675, 'move_defined': 670, 'mean_move_points': -0.13880597014925372, 'mean_abs_move_points': 5.956716417910448, 'median_abs_move_points': 2.0, 'zero_move_share': 0.32985074626865674, 'abs_move_quantiles_p50_p90_p99': [2.0, 18.0, 42.309999999999945], 'median_span_hours': 11.49761675029993, 'median_first_lead_hours': 14.6570545336273, 'last_lead_hours_p10_p50_p90': [0.6578160307407379, 2.418407361672984, 5.918969106389417], 'share_last_snapshot_within_6h': 0.9641791044776119, 'width_first_median': 0.010000000000000009, 'width_last_median': 0.010000000000000009, 'share_first_bracketed': 0.7805970149253731, 'share_last_bracketed': 0.7910447761194029, 'share_first_at_ladder_edge': 0.29402985074626864, 'share_last_at_ladder_edge': 0.291044776119403}

### Agreement of the last pre-kickoff main rung with the ESPN close (line-only gate, no outcomes)

Gate thresholds (fixed before any outcome was read): {'corr_min': 0.95, 'median_abs_diff_max': 2.0}.

- total / primary_all_defined: {'n': 382, 'corr_last_rung_vs_espn_close': 0.8549855550768128, 'median_abs_diff_points': 1.0, 'mean_diff_points': -0.020942408376963352, 'share_within_1_point': 0.7146596858638743, 'passed': False}
- total / both_ends_bracket_50pct: {'n': 299, 'corr_last_rung_vs_espn_close': 0.9149761159661467, 'median_abs_diff_points': 1.0, 'mean_diff_points': -0.06354515050167224, 'share_within_1_point': 0.7892976588628763, 'passed': False}
- total / both_ends_width_le_10pts: {'n': 380, 'corr_last_rung_vs_espn_close': 0.8549658373639142, 'median_abs_diff_points': 1.0, 'mean_diff_points': -0.021052631578947368, 'share_within_1_point': 0.7131578947368421, 'passed': False}
- total / last_snapshot_within_6h: {'n': 352, 'corr_last_rung_vs_espn_close': 0.8621109070938577, 'median_abs_diff_points': 1.0, 'mean_diff_points': -0.13920454545454544, 'share_within_1_point': 0.7329545454545454, 'passed': False}
- spread / primary_all_defined: {'n': 415, 'corr_last_rung_vs_espn_close': 0.9202705680038268, 'median_abs_diff_points': 1.0, 'mean_diff_points': 1.0433734939759036, 'share_within_1_point': 0.6216867469879518, 'passed': False}
- spread / both_ends_bracket_50pct: {'n': 276, 'corr_last_rung_vs_espn_close': 0.9764133602295464, 'median_abs_diff_points': 0.5, 'mean_diff_points': -0.041666666666666664, 'share_within_1_point': 0.7608695652173914, 'passed': True}
- spread / both_ends_width_le_10pts: {'n': 397, 'corr_last_rung_vs_espn_close': 0.9254151063095394, 'median_abs_diff_points': 1.0, 'mean_diff_points': 0.8866498740554156, 'share_within_1_point': 0.6171284634760705, 'passed': False}
- spread / last_snapshot_within_6h: {'n': 401, 'corr_last_rung_vs_espn_close': 0.9194835835025267, 'median_abs_diff_points': 1.0, 'mean_diff_points': 1.1246882793017456, 'share_within_1_point': 0.6334164588528678, 'passed': False}

### Movement vs actual minus ESPN close (holdout-window games only; read in the --final run)

- total / primary_all_defined (primary definition; GATE FAILED: corr 0.853, median abs diff 1.00 points): n 376, non-zero 242, corr 0.010 [-0.090, 0.099], slope 0.031 [-0.241, 0.352], mean outcome in movement direction 2.033 [0.221, 4.074], continuation share 54.1%
- total / both_ends_bracket_50pct (SENSITIVITY (labelled); GATE FAILED: corr 0.914, median abs diff 1.00 points): n 295, non-zero 163, corr 0.034 [-0.040, 0.153], slope 0.199 [-0.202, 1.359], mean outcome in movement direction 2.635 [0.457, 5.096], continuation share 55.8%
- total / both_ends_width_le_10pts (SENSITIVITY (labelled); GATE FAILED: corr 0.853, median abs diff 1.00 points): n 374, non-zero 240, corr 0.002 [-0.099, 0.092], slope 0.007 [-0.279, 0.333], mean outcome in movement direction 1.883 [-0.016, 3.974], continuation share 53.8%
- total / last_snapshot_within_6h (SENSITIVITY (labelled); GATE FAILED: corr 0.860, median abs diff 1.00 points): n 346, non-zero 216, corr 0.014 [-0.120, 0.120], slope 0.040 [-0.353, 0.423], mean outcome in movement direction 1.880 [-0.685, 4.161], continuation share 55.1%
- spread / primary_all_defined (primary definition; GATE FAILED: corr 0.920, median abs diff 1.00 points): n 409, non-zero 264, corr 0.048 [-0.078, 0.178], slope 0.056 [-0.089, 0.229], mean outcome in movement direction 1.879 [0.060, 3.846], continuation share 55.8%
- spread / both_ends_bracket_50pct (SENSITIVITY (labelled); gate passed: corr 0.976, median abs diff 0.50 points): n 273, non-zero 141, corr 0.177 [0.080, 0.274], slope 0.352 [0.173, 0.621], mean outcome in movement direction 3.574 [1.000, 6.088], continuation share 58.6%
- spread / both_ends_width_le_10pts (SENSITIVITY (labelled); GATE FAILED: corr 0.925, median abs diff 1.00 points): n 391, non-zero 246, corr -0.024 [-0.115, 0.090], slope -0.032 [-0.162, 0.151], mean outcome in movement direction 1.325 [-0.237, 3.088], continuation share 55.0%
- spread / last_snapshot_within_6h (SENSITIVITY (labelled); GATE FAILED: corr 0.919, median abs diff 1.00 points): n 395, non-zero 252, corr 0.055 [-0.068, 0.195], slope 0.063 [-0.075, 0.249], mean outcome in movement direction 2.103 [0.302, 4.235], continuation share 56.5%

## Notes

- ESPN movement is a close-time, untimestamped value: at most UPPER_BOUND_PASS, never enters v6.1.
- Intraday ladders are Polymarket-lineage quotes, not ESPN lines; DESCRIPTIVE_ONLY.
- 2026-11-14 games are post-holdout and are never scored in any outcome statistic here.
- A non-final run reads no holdout row: verdicts that depend on the holdout are PROVISIONAL_PENDING_FINAL.
