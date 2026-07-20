# Roadmap challenger factorial experiment

Generated: `2026-07-20T11:09:51.593147Z`

## Decision boundary

This is a development experiment, not a promotion audit. The current models were snapshotted before changes. The historical holdouts have already been viewed in prior research, so every result below is evidence for what deserves a fresh future test—not permission to activate a feature. No model config, production artifact, ledger, pick, or order was changed.

Snapshot directory: `snapshots/20260720T073745+0800-pre-roadmap-challenger`
Snapshot archive SHA-256: `7b7227fd456290b9072d1c79b56218db3329fe157556061765d95aa7e86f182d`

Tested additions: `consistency_gap`, `hot_cold_gap`, `rest_disparity`, `back_to_back_gap`, `games_last_7_gap`, and `schedule_available`. These are the only roadmap additions that can be constructed consistently from existing completed-game histories in both validation and forward paths.

## Method

- Each sport starts from the exact feature list in its active artifact.
- The exact active artifact is scored separately. In factorial tables, `incumbent` means a matched logistic refit of that feature specification on the current train cohort so nested challengers have a fair control.
- All 64 combinations of six additions are fit on the existing complete-date 60/20/20 split.
- Coefficients are fit on train only. Gate selection uses validation only.
- All-prediction Brier/log loss/ECE are primary. Selective hit rate and flat `-110` units are diagnostics.
- Brier deltas use a 2,000-resample date-cluster bootstrap.
- Confidence gates from 0.50 to 0.80 are shown for the incumbent and every isolated addition.
- Market prices are absent, so ROI, CLV, and executable EV are not claimed.

## Untestable high-value roadmap additions

| Model | Missing historical point-in-time additions | Decision |
|---|---|---|
| NBA/WNBA | player availability, projected minutes, RAPM/lineup impact, Four Factors/pace snapshots, transactions | Collect first; do not proxy from final box scores. |
| MLB | true pregame starters, confirmed lineups, bullpen availability, archived game-time forecasts, pitch mix | Current retrospective caches cannot support promotion-grade testing. |
| NFL | expected QB, practice/inactive state, EPA/CPOE, line continuity, pressure and drive state | No historical decision-time feature archive exists. |
| LoL/CS2 | effective-dated rosters, patch/map/veto/draft state | Existing baseline remains score/series-only. |
| KBO/NPB | starters, reliever workload, lineups, park/weather, game-specific tie probability | Existing tie-aware Elo remains the control. |

## Executive conclusion

Clean additions passing the full statistical and structural screen: **0**.

The blunt answer is **do not activate any tested addition**. Apparent formal wins that include `schedule_available` are rejected as degenerate: the field is constant or almost constant in validation/holdout and acts like a cohort/intercept marker, not a durable predictive signal. The nondegenerate candidates below are useful hypotheses for fresh prospective collection, but none clears both validation consistency and clustered holdout uncertainty.

| Sport | Most informative nondegenerate challenger | Validation Δ Brier | Holdout Δ Brier | Holdout 95% CI | Interpretation |
|---|---|---:|---:|---:|---|
| MLB | `incumbent+rest_disparity` | +0.000435 | -0.000361 | [-0.000709, -0.000005] | Holdout signal, but validation moved the wrong way. |
| NBA | `incumbent+consistency+hot_cold+schedule_density` | -0.000430 | -0.000569 | [-0.001773, +0.000539] | Both cohorts improve slightly, but the clustered interval crosses zero. |
| WNBA | `incumbent+hot_cold` | +0.000773 | -0.001025 | [-0.002267, +0.000289] | Holdout improves, validation worsens, and sample is small. |
| NFL | `incumbent+consistency+hot_cold+rest_disparity+schedule_density` | -0.002472 | -0.003782 | [-0.007820, +0.000726] | Largest directional gain, but only 110 holdout games and interval crosses zero. |

Confidence gating does not rescue the additions. The validation-selected 60% gate is effectively nonselective for NBA, WNBA, and NFL; MLB's refit gate rises to roughly 0.57 but misses the 60% target on reused holdout. Attractive flat `-110` rows are threshold diagnostics only and cannot establish executable profitability.

# MLB

## Incumbent and data validation

- Artifact: `config/models/mlb-elo-trend-lr-v3.json`
- Version/hash: `mlb-elo-trend-lr-v3` / `ebdb392c90fd9a9414a97c1b39024e1a4f2c894aee118b3b4598ecd7e5e96695`
- Incumbent features: `elo_probability, trend_gap, park_factor, weather_factor, pitcher_era_gap`
- Active artifact threshold: 0.515
- Split: train 3787, validation 1048, holdout 1335
- Processed data: `data/processed/mlb/games.jsonl`
- Data SHA-256: `bfdc29af57b4e191851f885b592a7a64e7e9a88daad8fb0b1ffe1ac01feae3b2`
- Raw rows / loaded modeling games / excluded by loader / duplicate IDs / invalid JSON: 7785 / 6226 / 1559 / 0 / 0
- Walk-forward binary rows / excluded before evaluation: 6170 / 56 (The first 50 history games seed features; tied non-soccer results are excluded.)
- Rows with `observed_at_utc`: 0 (metadata complete: False)
- Schedule coverage train/validation/holdout: 99.9% / 100.0% / 100.0%

> Provenance limitation: Completed score order is enforced by event time, but legacy processed rows do not carry retrieval observed_at_utc; this experiment is predictive development evidence, not promotion-grade source provenance.

### Exact active artifact versus matched refit control

The active row uses the snapshotted coefficients and threshold. The refit row uses the same feature list but re-estimates coefficients on the current train cohort. All factorial deltas use the refit row as control.

| Model | Validation Brier | Holdout Brier | Holdout log loss | Gate | Holdout calls | Holdout hit | -110 units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Exact active artifact | 0.245326 | 0.249176 | 0.691603 | 0.515 | 1132 | 0.555 | 66.91 |
| Matched refit control (`incumbent`) | 0.245161 | 0.249058 | 0.691352 | 0.569 | 499 | 0.563 | 37.45 |

Active-to-refit coefficient deltas: `elo_probability=-0.060984, trend_gap=+0.005387, park_factor=+0.027707, weather_factor=-0.001765, pitcher_era_gap=-0.001756, intercept=+0.006284`

### Feature distributions

| Cohort | Feature | Mean | Std | Min | Max | Unique | Zero rate |
|---|---|---:|---:|---:|---:|---:|---:|
| train | `consistency_gap` | -0.0023 | 0.0846 | -0.3348 | 0.7156 | 3237 | 0.3% |
| train | `hot_cold_gap` | -0.0039 | 0.4173 | -1.4776 | 1.5467 | 3301 | 0.6% |
| train | `rest_disparity` | 0.0349 | 0.4011 | -2.0000 | 5.0000 | 8 | 88.1% |
| train | `back_to_back_gap` | -0.0166 | 0.2907 | -1.0000 | 1.0000 | 3 | 91.5% |
| train | `games_last_7_gap` | 0.0222 | 0.8841 | -6.0000 | 3.0000 | 9 | 48.5% |
| train | `schedule_available` | 0.9992 | 0.0281 | 0.0000 | 1.0000 | 2 | 0.1% |
| validation | `consistency_gap` | -0.0003 | 0.0841 | -0.3245 | 0.3653 | 919 | 0.2% |
| validation | `hot_cold_gap` | -0.0039 | 0.5050 | -1.9109 | 1.7632 | 930 | 0.0% |
| validation | `rest_disparity` | 0.0439 | 0.4540 | -3.0000 | 5.0000 | 9 | 90.2% |
| validation | `back_to_back_gap` | -0.0248 | 0.2573 | -1.0000 | 1.0000 | 3 | 93.3% |
| validation | `games_last_7_gap` | 0.0496 | 0.7920 | -3.0000 | 2.0000 | 6 | 57.0% |
| validation | `schedule_available` | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 1 | 0.0% |
| holdout | `consistency_gap` | -0.0000 | 0.0791 | -0.3246 | 0.2526 | 1177 | 0.0% |
| holdout | `hot_cold_gap` | -0.0081 | 0.5004 | -1.7664 | 1.6056 | 1192 | 0.1% |
| holdout | `rest_disparity` | 0.0105 | 0.3908 | -2.0000 | 2.0000 | 5 | 88.1% |
| holdout | `back_to_back_gap` | -0.0127 | 0.3033 | -1.0000 | 1.0000 | 3 | 90.8% |
| holdout | `games_last_7_gap` | 0.0442 | 0.8330 | -3.0000 | 2.0000 | 6 | 52.7% |
| holdout | `schedule_available` | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 1 | 0.0% |

## Isolated additions

Lower Brier/log loss is better. A negative delta favors the challenger. `KEEP_FOR_FRESH_TEST` requires validation improvement and a holdout Brier/log-loss improvement whose clustered 95% CI excludes zero. Variants using the near-constant missingness flag are `REJECT_DEGENERATE` even if their numerical screen looks favorable.

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent` | 0.249058 | +0.000000 | 0.691352 | +0.000000 | [+0.000000, +0.000000] | 0.569 | 499 | 0.563 | 37.45 | INCONCLUSIVE |
| `incumbent+consistency` | 0.249143 | +0.000085 | 0.691540 | +0.000188 | [-0.000298, +0.000490] | 0.562 | 557 | 0.542 | 19.55 | INCONCLUSIVE |
| `incumbent+hot_cold` | 0.249158 | +0.000100 | 0.691575 | +0.000223 | [-0.000483, +0.000696] | 0.566 | 529 | 0.554 | 30.36 | INCONCLUSIVE |
| `incumbent+rest_disparity` | 0.248697 | -0.000361 | 0.690614 | -0.000738 | [-0.000709, -0.000005] | 0.569 | 501 | 0.567 | 41.18 | INCONCLUSIVE |
| `incumbent+back_to_back` | 0.249444 | +0.000386 | 0.692142 | +0.000790 | [+0.000112, +0.000689] | 0.569 | 503 | 0.555 | 29.64 | REJECT |
| `incumbent+schedule_density` | 0.249085 | +0.000027 | 0.691408 | +0.000056 | [-0.000043, +0.000098] | 0.570 | 497 | 0.563 | 37.55 | INCONCLUSIVE |
| `incumbent+schedule_missingness` | 0.249031 | -0.000027 | 0.691297 | -0.000055 | [-0.000116, +0.000054] | 0.569 | 500 | 0.566 | 40.27 | REJECT_DEGENERATE |

## Pairwise interactions

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent+consistency+hot_cold` | 0.249221 | +0.000163 | 0.691715 | +0.000363 | [-0.000488, +0.000841] | 0.561 | 574 | 0.542 | 19.73 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity` | 0.248773 | -0.000285 | 0.690784 | -0.000568 | [-0.000776, +0.000215] | 0.580 | 401 | 0.571 | 36.18 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back` | 0.249489 | +0.000431 | 0.692244 | +0.000892 | [-0.000048, +0.000898] | 0.566 | 520 | 0.548 | 24.09 | INCONCLUSIVE |
| `incumbent+consistency+schedule_density` | 0.249174 | +0.000116 | 0.691604 | +0.000252 | [-0.000264, +0.000513] | 0.577 | 441 | 0.553 | 24.82 | INCONCLUSIVE |
| `incumbent+consistency+schedule_missingness` | 0.249125 | +0.000067 | 0.691504 | +0.000152 | [-0.000345, +0.000496] | 0.564 | 540 | 0.550 | 27.00 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity` | 0.248800 | -0.000258 | 0.690844 | -0.000508 | [-0.000954, +0.000413] | 0.572 | 472 | 0.572 | 43.45 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back` | 0.249558 | +0.000500 | 0.692393 | +0.001041 | [-0.000152, +0.001130] | 0.566 | 531 | 0.542 | 18.82 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_density` | 0.249183 | +0.000125 | 0.691629 | +0.000277 | [-0.000470, +0.000712] | 0.567 | 519 | 0.555 | 30.82 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_missingness` | 0.249138 | +0.000080 | 0.691536 | +0.000184 | [-0.000524, +0.000662] | 0.565 | 535 | 0.559 | 35.82 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back` | 0.249521 | +0.000463 | 0.692321 | +0.000969 | [-0.000452, +0.001377] | 0.572 | 479 | 0.547 | 21.18 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_density` | 0.248704 | -0.000354 | 0.690627 | -0.000725 | [-0.000684, -0.000029] | 0.569 | 496 | 0.562 | 36.64 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_missingness` | 0.248666 | -0.000392 | 0.690552 | -0.000800 | [-0.000741, -0.000043] | 0.569 | 498 | 0.566 | 40.36 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density` | 0.249510 | +0.000452 | 0.692279 | +0.000927 | [+0.000171, +0.000782] | 0.569 | 500 | 0.558 | 32.64 | REJECT |
| `incumbent+back_to_back+schedule_missingness` | 0.249429 | +0.000371 | 0.692112 | +0.000760 | [+0.000075, +0.000686] | 0.569 | 498 | 0.560 | 34.64 | REJECT_DEGENERATE |
| `incumbent+schedule_density+schedule_missingness` | 0.249065 | +0.000007 | 0.691369 | +0.000017 | [-0.000113, +0.000128] | 0.570 | 494 | 0.567 | 40.55 | REJECT_DEGENERATE |

## Every feature combination

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent` | 0.249058 | +0.000000 | 0.691352 | +0.000000 | [+0.000000, +0.000000] | 0.569 | 499 | 0.563 | 37.45 | INCONCLUSIVE |
| `incumbent+consistency` | 0.249143 | +0.000085 | 0.691540 | +0.000188 | [-0.000298, +0.000490] | 0.562 | 557 | 0.542 | 19.55 | INCONCLUSIVE |
| `incumbent+hot_cold` | 0.249158 | +0.000100 | 0.691575 | +0.000223 | [-0.000483, +0.000696] | 0.566 | 529 | 0.554 | 30.36 | INCONCLUSIVE |
| `incumbent+rest_disparity` | 0.248697 | -0.000361 | 0.690614 | -0.000738 | [-0.000709, -0.000005] | 0.569 | 501 | 0.567 | 41.18 | INCONCLUSIVE |
| `incumbent+back_to_back` | 0.249444 | +0.000386 | 0.692142 | +0.000790 | [+0.000112, +0.000689] | 0.569 | 503 | 0.555 | 29.64 | REJECT |
| `incumbent+schedule_density` | 0.249085 | +0.000027 | 0.691408 | +0.000056 | [-0.000043, +0.000098] | 0.570 | 497 | 0.563 | 37.55 | INCONCLUSIVE |
| `incumbent+schedule_missingness` | 0.249031 | -0.000027 | 0.691297 | -0.000055 | [-0.000116, +0.000054] | 0.569 | 500 | 0.566 | 40.27 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold` | 0.249221 | +0.000163 | 0.691715 | +0.000363 | [-0.000488, +0.000841] | 0.561 | 574 | 0.542 | 19.73 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity` | 0.248773 | -0.000285 | 0.690784 | -0.000568 | [-0.000776, +0.000215] | 0.580 | 401 | 0.571 | 36.18 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back` | 0.249489 | +0.000431 | 0.692244 | +0.000892 | [-0.000048, +0.000898] | 0.566 | 520 | 0.548 | 24.09 | INCONCLUSIVE |
| `incumbent+consistency+schedule_density` | 0.249174 | +0.000116 | 0.691604 | +0.000252 | [-0.000264, +0.000513] | 0.577 | 441 | 0.553 | 24.82 | INCONCLUSIVE |
| `incumbent+consistency+schedule_missingness` | 0.249125 | +0.000067 | 0.691504 | +0.000152 | [-0.000345, +0.000496] | 0.564 | 540 | 0.550 | 27.00 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity` | 0.248800 | -0.000258 | 0.690844 | -0.000508 | [-0.000954, +0.000413] | 0.572 | 472 | 0.572 | 43.45 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back` | 0.249558 | +0.000500 | 0.692393 | +0.001041 | [-0.000152, +0.001130] | 0.566 | 531 | 0.542 | 18.82 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_density` | 0.249183 | +0.000125 | 0.691629 | +0.000277 | [-0.000470, +0.000712] | 0.567 | 519 | 0.555 | 30.82 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_missingness` | 0.249138 | +0.000080 | 0.691536 | +0.000184 | [-0.000524, +0.000662] | 0.565 | 535 | 0.559 | 35.82 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back` | 0.249521 | +0.000463 | 0.692321 | +0.000969 | [-0.000452, +0.001377] | 0.572 | 479 | 0.547 | 21.18 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_density` | 0.248704 | -0.000354 | 0.690627 | -0.000725 | [-0.000684, -0.000029] | 0.569 | 496 | 0.562 | 36.64 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_missingness` | 0.248666 | -0.000392 | 0.690552 | -0.000800 | [-0.000741, -0.000043] | 0.569 | 498 | 0.566 | 40.36 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density` | 0.249510 | +0.000452 | 0.692279 | +0.000927 | [+0.000171, +0.000782] | 0.569 | 500 | 0.558 | 32.64 | REJECT |
| `incumbent+back_to_back+schedule_missingness` | 0.249429 | +0.000371 | 0.692112 | +0.000760 | [+0.000075, +0.000686] | 0.569 | 498 | 0.560 | 34.64 | REJECT_DEGENERATE |
| `incumbent+schedule_density+schedule_missingness` | 0.249065 | +0.000007 | 0.691369 | +0.000017 | [-0.000113, +0.000128] | 0.570 | 494 | 0.567 | 40.55 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity` | 0.248855 | -0.000203 | 0.690969 | -0.000383 | [-0.000934, +0.000516] | 0.561 | 575 | 0.548 | 26.36 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+back_to_back` | 0.249602 | +0.000544 | 0.692494 | +0.001142 | [-0.000181, +0.001228] | 0.575 | 429 | 0.555 | 25.36 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+schedule_density` | 0.249244 | +0.000186 | 0.691765 | +0.000413 | [-0.000473, +0.000848] | 0.574 | 437 | 0.568 | 36.45 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+schedule_missingness` | 0.249203 | +0.000145 | 0.691679 | +0.000327 | [-0.000517, +0.000833] | 0.561 | 586 | 0.544 | 23.00 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back` | 0.249605 | +0.000547 | 0.692506 | +0.001154 | [-0.000389, +0.001514] | 0.583 | 370 | 0.562 | 27.09 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+schedule_density` | 0.248781 | -0.000277 | 0.690801 | -0.000551 | [-0.000779, +0.000190] | 0.580 | 398 | 0.570 | 35.36 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+schedule_missingness` | 0.248755 | -0.000303 | 0.690749 | -0.000603 | [-0.000851, +0.000205] | 0.580 | 397 | 0.577 | 40.18 | REJECT_DEGENERATE |
| `incumbent+consistency+back_to_back+schedule_density` | 0.249588 | +0.000530 | 0.692453 | +0.001101 | [+0.000077, +0.001012] | 0.577 | 428 | 0.547 | 18.73 | REJECT |
| `incumbent+consistency+back_to_back+schedule_missingness` | 0.249500 | +0.000442 | 0.692271 | +0.000919 | [-0.000036, +0.000925] | 0.577 | 426 | 0.554 | 24.55 | REJECT_DEGENERATE |
| `incumbent+consistency+schedule_density+schedule_missingness` | 0.249149 | +0.000091 | 0.691556 | +0.000204 | [-0.000333, +0.000510] | 0.577 | 431 | 0.557 | 27.18 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back` | 0.249639 | +0.000581 | 0.692580 | +0.001228 | [-0.000449, +0.001647] | 0.584 | 369 | 0.561 | 26.18 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+schedule_density` | 0.248805 | -0.000253 | 0.690857 | -0.000495 | [-0.000931, +0.000411] | 0.572 | 468 | 0.571 | 41.73 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+schedule_missingness` | 0.248777 | -0.000281 | 0.690799 | -0.000553 | [-0.000938, +0.000363] | 0.581 | 380 | 0.579 | 40.00 | REJECT_DEGENERATE |
| `incumbent+hot_cold+back_to_back+schedule_density` | 0.249571 | +0.000513 | 0.692421 | +0.001069 | [-0.000103, +0.001131] | 0.567 | 514 | 0.547 | 22.45 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back+schedule_missingness` | 0.249518 | +0.000460 | 0.692313 | +0.000961 | [-0.000218, +0.001066] | 0.569 | 495 | 0.556 | 30.00 | REJECT_DEGENERATE |
| `incumbent+hot_cold+schedule_density+schedule_missingness` | 0.249169 | +0.000111 | 0.691603 | +0.000251 | [-0.000485, +0.000699] | 0.568 | 501 | 0.563 | 37.36 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back+schedule_density` | 0.249539 | +0.000481 | 0.692357 | +0.001005 | [-0.000395, +0.001381] | 0.586 | 352 | 0.545 | 14.55 | INCONCLUSIVE |
| `incumbent+rest_disparity+back_to_back+schedule_missingness` | 0.249491 | +0.000433 | 0.692261 | +0.000909 | [-0.000433, +0.001395] | 0.585 | 360 | 0.547 | 16.09 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+schedule_density+schedule_missingness` | 0.248678 | -0.000380 | 0.690576 | -0.000776 | [-0.000729, -0.000023] | 0.569 | 498 | 0.566 | 40.36 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density+schedule_missingness` | 0.249487 | +0.000429 | 0.692233 | +0.000881 | [+0.000121, +0.000737] | 0.570 | 496 | 0.562 | 36.64 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back` | 0.249705 | +0.000647 | 0.692727 | +0.001375 | [-0.000408, +0.001719] | 0.585 | 355 | 0.566 | 28.73 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_density` | 0.248892 | -0.000166 | 0.691046 | -0.000306 | [-0.000897, +0.000550] | 0.560 | 578 | 0.548 | 27.18 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_missingness` | 0.248839 | -0.000219 | 0.690938 | -0.000414 | [-0.000948, +0.000478] | 0.559 | 596 | 0.554 | 34.00 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_density` | 0.249686 | +0.000628 | 0.692671 | +0.001319 | [-0.000062, +0.001364] | 0.577 | 405 | 0.565 | 32.18 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_missingness` | 0.249589 | +0.000531 | 0.692468 | +0.001116 | [-0.000149, +0.001282] | 0.577 | 417 | 0.561 | 29.73 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+schedule_density+schedule_missingness` | 0.249208 | +0.000150 | 0.691690 | +0.000338 | [-0.000481, +0.000805] | 0.559 | 596 | 0.542 | 20.64 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_density` | 0.249613 | +0.000555 | 0.692523 | +0.001171 | [-0.000340, +0.001585] | 0.582 | 378 | 0.556 | 22.91 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_missingness` | 0.249588 | +0.000530 | 0.692472 | +0.001120 | [-0.000420, +0.001433] | 0.583 | 369 | 0.561 | 26.18 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+schedule_density+schedule_missingness` | 0.248773 | -0.000285 | 0.690786 | -0.000566 | [-0.000801, +0.000249] | 0.580 | 397 | 0.577 | 40.18 | REJECT_DEGENERATE |
| `incumbent+consistency+back_to_back+schedule_density+schedule_missingness` | 0.249560 | +0.000502 | 0.692398 | +0.001046 | [+0.000036, +0.000997] | 0.577 | 426 | 0.554 | 24.55 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.249650 | +0.000592 | 0.692602 | +0.001250 | [-0.000425, +0.001665] | 0.584 | 370 | 0.562 | 27.09 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.249616 | +0.000558 | 0.692535 | +0.001183 | [-0.000520, +0.001674] | 0.582 | 378 | 0.556 | 22.91 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.248801 | -0.000257 | 0.690850 | -0.000502 | [-0.000967, +0.000401] | 0.569 | 500 | 0.568 | 42.18 | REJECT_DEGENERATE |
| `incumbent+hot_cold+back_to_back+schedule_density+schedule_missingness` | 0.249588 | +0.000530 | 0.692463 | +0.001111 | [-0.000116, +0.001157] | 0.570 | 487 | 0.552 | 26.55 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.249535 | +0.000477 | 0.692352 | +0.001000 | [-0.000450, +0.001386] | 0.585 | 361 | 0.546 | 15.09 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.249694 | +0.000636 | 0.692701 | +0.001349 | [-0.000392, +0.001693] | 0.584 | 355 | 0.563 | 26.82 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.249658 | +0.000600 | 0.692631 | +0.001279 | [-0.000491, +0.001619] | 0.586 | 346 | 0.561 | 24.36 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.248829 | -0.000229 | 0.690915 | -0.000437 | [-0.000918, +0.000490] | 0.559 | 592 | 0.551 | 30.36 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_density+schedule_missingness` | 0.249651 | +0.000593 | 0.692600 | +0.001248 | [-0.000133, +0.001289] | 0.573 | 446 | 0.556 | 27.45 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.249594 | +0.000536 | 0.692486 | +0.001134 | [-0.000388, +0.001484] | 0.581 | 390 | 0.559 | 26.18 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.249629 | +0.000571 | 0.692562 | +0.001210 | [-0.000522, +0.001621] | 0.583 | 376 | 0.556 | 23.00 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.249698 | +0.000640 | 0.692711 | +0.001359 | [-0.000382, +0.001722] | 0.584 | 359 | 0.557 | 22.82 | REJECT_DEGENERATE |

## Confidence-gate sweeps for incumbent and isolated additions

### `incumbent`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 1048 | 0.559 | 70.73 | 1335 | 0.548 | 60.55 |
| 0.525 | 788 | 0.562 | 57.73 | 1000 | 0.550 | 50.00 |
| 0.550 | 519 | 0.580 | 55.64 | 689 | 0.550 | 34.55 |
| 0.575 | 320 | 0.613 | 54.18 | 451 | 0.561 | 32.00 |
| 0.600 | 175 | 0.663 | 46.45 | 235 | 0.574 | 22.73 |
| 0.625 | 111 | 0.658 | 28.36 | 133 | 0.564 | 10.18 |
| 0.650 | 58 | 0.690 | 18.36 | 66 | 0.591 | 8.45 |
| 0.675 | 23 | 0.783 | 11.36 | 33 | 0.727 | 12.82 |
| 0.700 | 5 | 0.800 | 2.64 | 14 | 0.786 | 7.00 |
| 0.725 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.750 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.775 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+consistency`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 1048 | 0.550 | 51.64 | 1335 | 0.546 | 56.73 |
| 0.525 | 768 | 0.564 | 58.64 | 1011 | 0.554 | 58.09 |
| 0.550 | 530 | 0.581 | 58.00 | 708 | 0.548 | 32.73 |
| 0.575 | 340 | 0.597 | 47.55 | 448 | 0.554 | 25.45 |
| 0.600 | 178 | 0.652 | 43.45 | 244 | 0.566 | 19.45 |
| 0.625 | 107 | 0.654 | 26.64 | 121 | 0.562 | 8.82 |
| 0.650 | 55 | 0.673 | 15.64 | 67 | 0.552 | 3.64 |
| 0.675 | 23 | 0.826 | 13.27 | 32 | 0.688 | 10.00 |
| 0.700 | 7 | 0.714 | 2.55 | 14 | 0.786 | 7.00 |
| 0.725 | 0 | — | 0.00 | 1 | 1.000 | 0.91 |
| 0.750 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.775 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+hot_cold`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 1048 | 0.551 | 53.55 | 1335 | 0.543 | 49.09 |
| 0.525 | 767 | 0.563 | 57.73 | 1008 | 0.551 | 51.55 |
| 0.550 | 520 | 0.577 | 52.73 | 683 | 0.549 | 32.91 |
| 0.575 | 319 | 0.596 | 43.73 | 432 | 0.565 | 33.82 |
| 0.600 | 199 | 0.663 | 53.00 | 233 | 0.575 | 22.82 |
| 0.625 | 107 | 0.673 | 30.45 | 116 | 0.517 | -1.45 |
| 0.650 | 61 | 0.689 | 19.18 | 68 | 0.544 | 2.64 |
| 0.675 | 23 | 0.783 | 11.36 | 37 | 0.649 | 8.82 |
| 0.700 | 7 | 0.714 | 2.55 | 13 | 0.769 | 6.09 |
| 0.725 | 1 | 1.000 | 0.91 | 2 | 1.000 | 1.82 |
| 0.750 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.775 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+rest_disparity`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 1048 | 0.554 | 61.18 | 1335 | 0.546 | 56.73 |
| 0.525 | 782 | 0.563 | 58.00 | 994 | 0.551 | 52.18 |
| 0.550 | 528 | 0.581 | 58.09 | 688 | 0.558 | 45.09 |
| 0.575 | 320 | 0.603 | 48.45 | 453 | 0.565 | 35.73 |
| 0.600 | 179 | 0.659 | 46.27 | 243 | 0.560 | 16.64 |
| 0.625 | 113 | 0.664 | 30.18 | 131 | 0.573 | 12.18 |
| 0.650 | 60 | 0.650 | 14.45 | 66 | 0.606 | 10.36 |
| 0.675 | 23 | 0.783 | 11.36 | 30 | 0.733 | 12.00 |
| 0.700 | 7 | 0.857 | 4.45 | 15 | 0.800 | 7.91 |
| 0.725 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.750 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.775 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+back_to_back`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 1048 | 0.558 | 68.82 | 1335 | 0.542 | 47.18 |
| 0.525 | 785 | 0.566 | 62.64 | 1010 | 0.549 | 47.64 |
| 0.550 | 530 | 0.585 | 61.82 | 695 | 0.547 | 30.45 |
| 0.575 | 325 | 0.606 | 51.09 | 447 | 0.557 | 28.36 |
| 0.600 | 179 | 0.659 | 46.27 | 240 | 0.567 | 19.64 |
| 0.625 | 110 | 0.682 | 33.18 | 135 | 0.556 | 8.18 |
| 0.650 | 59 | 0.678 | 17.36 | 68 | 0.559 | 4.55 |
| 0.675 | 21 | 0.762 | 9.55 | 33 | 0.727 | 12.82 |
| 0.700 | 5 | 0.800 | 2.64 | 15 | 0.800 | 7.91 |
| 0.725 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.750 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.775 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+schedule_density`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 1048 | 0.554 | 61.18 | 1335 | 0.549 | 64.36 |
| 0.525 | 792 | 0.562 | 57.55 | 1002 | 0.552 | 53.73 |
| 0.550 | 517 | 0.584 | 59.55 | 691 | 0.550 | 34.45 |
| 0.575 | 324 | 0.611 | 54.00 | 445 | 0.555 | 26.55 |
| 0.600 | 176 | 0.665 | 47.36 | 232 | 0.573 | 21.91 |
| 0.625 | 111 | 0.658 | 28.36 | 134 | 0.552 | 7.27 |
| 0.650 | 55 | 0.691 | 17.55 | 68 | 0.574 | 6.45 |
| 0.675 | 23 | 0.783 | 11.36 | 34 | 0.735 | 13.73 |
| 0.700 | 5 | 0.800 | 2.64 | 14 | 0.786 | 7.00 |
| 0.725 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.750 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.775 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+schedule_missingness`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 1048 | 0.562 | 76.45 | 1335 | 0.546 | 56.73 |
| 0.525 | 782 | 0.564 | 59.91 | 1000 | 0.550 | 50.00 |
| 0.550 | 521 | 0.583 | 59.36 | 688 | 0.554 | 39.36 |
| 0.575 | 321 | 0.601 | 47.45 | 446 | 0.561 | 31.27 |
| 0.600 | 179 | 0.676 | 52.00 | 240 | 0.562 | 17.73 |
| 0.625 | 112 | 0.661 | 29.27 | 136 | 0.559 | 9.09 |
| 0.650 | 59 | 0.678 | 17.36 | 62 | 0.597 | 8.64 |
| 0.675 | 25 | 0.760 | 11.27 | 33 | 0.667 | 9.00 |
| 0.700 | 5 | 0.800 | 2.64 | 14 | 0.786 | 7.00 |
| 0.725 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.750 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.775 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

## Development ranking (not a promotion ranking)

| Rank | Variant | Holdout Brier | Δ Brier | Verdict |
|---:|---|---:|---:|---|
| 1 | `incumbent+rest_disparity+schedule_missingness` | 0.248666 | -0.000392 | REJECT_DEGENERATE |
| 2 | `incumbent+rest_disparity+schedule_density+schedule_missingness` | 0.248678 | -0.000380 | REJECT_DEGENERATE |
| 3 | `incumbent+rest_disparity` | 0.248697 | -0.000361 | INCONCLUSIVE |
| 4 | `incumbent+rest_disparity+schedule_density` | 0.248704 | -0.000354 | INCONCLUSIVE |
| 5 | `incumbent+consistency+rest_disparity+schedule_missingness` | 0.248755 | -0.000303 | REJECT_DEGENERATE |
| 6 | `incumbent+consistency+rest_disparity` | 0.248773 | -0.000285 | INCONCLUSIVE |
| 7 | `incumbent+consistency+rest_disparity+schedule_density+schedule_missingness` | 0.248773 | -0.000285 | REJECT_DEGENERATE |
| 8 | `incumbent+hot_cold+rest_disparity+schedule_missingness` | 0.248777 | -0.000281 | REJECT_DEGENERATE |
| 9 | `incumbent+consistency+rest_disparity+schedule_density` | 0.248781 | -0.000277 | INCONCLUSIVE |
| 10 | `incumbent+hot_cold+rest_disparity` | 0.248800 | -0.000258 | INCONCLUSIVE |

# NBA

## Incumbent and data validation

- Artifact: `config/models/nba-elo-trend-lr-v3.json`
- Version/hash: `nba-elo-trend-lr-v3` / `75dc49ff4fdd33721e56362fbead751cd705245983dd3790dde4449f02dd1aec`
- Incumbent features: `elo_probability, trend_gap, defensive_trend_gap`
- Active artifact threshold: 0.565
- Split: train 2176, validation 745, holdout 662
- Processed data: `data/processed/nba/games.jsonl`
- Data SHA-256: `198b418ec7da2ff81c3f4dc5a8f799f1e03133556fd31e24c3ff063f3ef92b2b`
- Raw rows / loaded modeling games / excluded by loader / duplicate IDs / invalid JSON: 3633 / 3633 / 0 / 0 / 0
- Walk-forward binary rows / excluded before evaluation: 3583 / 50 (The first 50 history games seed features; tied non-soccer results are excluded.)
- Rows with `observed_at_utc`: 0 (metadata complete: False)
- Schedule coverage train/validation/holdout: 99.7% / 99.5% / 99.5%

> Provenance limitation: Completed score order is enforced by event time, but legacy processed rows do not carry retrieval observed_at_utc; this experiment is predictive development evidence, not promotion-grade source provenance.

### Exact active artifact versus matched refit control

The active row uses the snapshotted coefficients and threshold. The refit row uses the same feature list but re-estimates coefficients on the current train cohort. All factorial deltas use the refit row as control.

| Model | Validation Brier | Holdout Brier | Holdout log loss | Gate | Holdout calls | Holdout hit | -110 units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Exact active artifact | 0.226034 | 0.194854 | 0.575545 | 0.565 | 540 | 0.757 | 240.82 |
| Matched refit control (`incumbent`) | 0.226034 | 0.194854 | 0.575545 | 0.500 | 662 | 0.702 | 225.73 |

Active-to-refit coefficient deltas: `elo_probability=+0.000000, trend_gap=+0.000000, defensive_trend_gap=+0.000000, intercept=+0.000000`

### Feature distributions

| Cohort | Feature | Mean | Std | Min | Max | Unique | Zero rate |
|---|---|---:|---:|---:|---:|---:|---:|
| train | `consistency_gap` | -0.0019 | 0.0356 | -0.9153 | 0.1379 | 2155 | 0.2% |
| train | `hot_cold_gap` | -0.0078 | 0.4301 | -1.9209 | 1.7679 | 2153 | 1.1% |
| train | `rest_disparity` | 0.1553 | 1.1463 | -7.0000 | 7.0000 | 15 | 51.0% |
| train | `back_to_back_gap` | -0.0588 | 0.5802 | -1.0000 | 1.0000 | 3 | 66.0% |
| train | `games_last_7_gap` | 0.0253 | 0.8257 | -4.0000 | 3.0000 | 8 | 52.8% |
| train | `schedule_available` | 0.9972 | 0.0524 | 0.0000 | 1.0000 | 2 | 0.3% |
| validation | `consistency_gap` | -0.0019 | 0.0433 | -0.9179 | 0.1379 | 743 | 0.0% |
| validation | `hot_cold_gap` | -0.0145 | 0.5149 | -1.5378 | 1.4442 | 745 | 0.0% |
| validation | `rest_disparity` | 0.1047 | 1.2669 | -6.0000 | 6.0000 | 13 | 44.7% |
| validation | `back_to_back_gap` | -0.0268 | 0.5947 | -1.0000 | 1.0000 | 3 | 64.6% |
| validation | `games_last_7_gap` | 0.0577 | 0.8031 | -3.0000 | 2.0000 | 6 | 51.8% |
| validation | `schedule_available` | 0.9946 | 0.0731 | 0.0000 | 1.0000 | 2 | 0.5% |
| holdout | `consistency_gap` | -0.0001 | 0.0264 | -0.1296 | 0.0973 | 658 | 0.5% |
| holdout | `hot_cold_gap` | -0.0183 | 0.5168 | -1.4058 | 1.6779 | 658 | 0.6% |
| holdout | `rest_disparity` | 0.1450 | 1.1144 | -6.0000 | 7.0000 | 12 | 52.4% |
| holdout | `back_to_back_gap` | -0.0438 | 0.5916 | -1.0000 | 1.0000 | 3 | 64.8% |
| holdout | `games_last_7_gap` | -0.0257 | 0.7817 | -3.0000 | 2.0000 | 6 | 56.3% |
| holdout | `schedule_available` | 0.9955 | 0.0672 | 0.0000 | 1.0000 | 2 | 0.5% |

## Isolated additions

Lower Brier/log loss is better. A negative delta favors the challenger. `KEEP_FOR_FRESH_TEST` requires validation improvement and a holdout Brier/log-loss improvement whose clustered 95% CI excludes zero. Variants using the near-constant missingness flag are `REJECT_DEGENERATE` even if their numerical screen looks favorable.

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent` | 0.194854 | +0.000000 | 0.575545 | +0.000000 | [+0.000000, +0.000000] | 0.500 | 662 | 0.702 | 225.73 | INCONCLUSIVE |
| `incumbent+consistency` | 0.194715 | -0.000139 | 0.575232 | -0.000313 | [-0.000281, -0.000002] | 0.500 | 662 | 0.701 | 223.82 | INCONCLUSIVE |
| `incumbent+hot_cold` | 0.194593 | -0.000261 | 0.574967 | -0.000578 | [-0.000547, +0.000003] | 0.500 | 662 | 0.698 | 220.00 | INCONCLUSIVE |
| `incumbent+rest_disparity` | 0.194651 | -0.000203 | 0.574983 | -0.000562 | [-0.001282, +0.000852] | 0.500 | 661 | 0.699 | 221.00 | INCONCLUSIVE |
| `incumbent+back_to_back` | 0.194671 | -0.000183 | 0.574937 | -0.000608 | [-0.002319, +0.001990] | 0.501 | 661 | 0.697 | 219.09 | INCONCLUSIVE |
| `incumbent+schedule_density` | 0.194635 | -0.000219 | 0.574857 | -0.000688 | [-0.001260, +0.000868] | 0.500 | 662 | 0.704 | 227.64 | INCONCLUSIVE |
| `incumbent+schedule_missingness` | 0.194290 | -0.000564 | 0.574299 | -0.001246 | [-0.001902, +0.000054] | 0.500 | 662 | 0.701 | 223.82 | REJECT_DEGENERATE |

## Pairwise interactions

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent+consistency+hot_cold` | 0.194479 | -0.000375 | 0.574710 | -0.000835 | [-0.000694, -0.000077] | 0.500 | 662 | 0.699 | 221.91 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity` | 0.194530 | -0.000324 | 0.574704 | -0.000841 | [-0.001355, +0.000701] | 0.500 | 662 | 0.701 | 223.82 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back` | 0.194539 | -0.000315 | 0.574626 | -0.000919 | [-0.002313, +0.001672] | 0.501 | 660 | 0.700 | 222.00 | INCONCLUSIVE |
| `incumbent+consistency+schedule_density` | 0.194494 | -0.000360 | 0.574531 | -0.001014 | [-0.001501, +0.000655] | 0.500 | 662 | 0.701 | 223.82 | INCONCLUSIVE |
| `incumbent+consistency+schedule_missingness` | 0.194111 | -0.000743 | 0.573886 | -0.001659 | [-0.002214, -0.000016] | 0.501 | 661 | 0.700 | 222.91 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity` | 0.194438 | -0.000416 | 0.574503 | -0.001042 | [-0.001517, +0.000689] | 0.500 | 662 | 0.699 | 221.91 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back` | 0.194510 | -0.000344 | 0.574580 | -0.000965 | [-0.002409, +0.001719] | 0.500 | 660 | 0.698 | 220.09 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_density` | 0.194415 | -0.000439 | 0.574358 | -0.001187 | [-0.001692, +0.000785] | 0.500 | 661 | 0.703 | 226.73 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_missingness` | 0.193990 | -0.000864 | 0.573609 | -0.001936 | [-0.002466, +0.000020] | 0.500 | 662 | 0.698 | 220.00 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back` | 0.194703 | -0.000151 | 0.575011 | -0.000534 | [-0.002352, +0.002076] | 0.500 | 662 | 0.695 | 216.18 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_density` | 0.194545 | -0.000309 | 0.574650 | -0.000895 | [-0.001631, +0.001033] | 0.501 | 657 | 0.702 | 223.09 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_missingness` | 0.194040 | -0.000814 | 0.573610 | -0.001935 | [-0.002769, +0.000696] | 0.501 | 660 | 0.698 | 220.09 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density` | 0.194537 | -0.000317 | 0.574555 | -0.000990 | [-0.002627, +0.002019] | 0.500 | 662 | 0.699 | 221.91 | INCONCLUSIVE |
| `incumbent+back_to_back+schedule_missingness` | 0.194090 | -0.000764 | 0.573645 | -0.001900 | [-0.003251, +0.001639] | 0.500 | 662 | 0.698 | 220.00 | REJECT_DEGENERATE |
| `incumbent+schedule_density+schedule_missingness` | 0.194029 | -0.000825 | 0.573492 | -0.002053 | [-0.002902, +0.000730] | 0.500 | 661 | 0.703 | 226.73 | REJECT_DEGENERATE |

## Every feature combination

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent` | 0.194854 | +0.000000 | 0.575545 | +0.000000 | [+0.000000, +0.000000] | 0.500 | 662 | 0.702 | 225.73 | INCONCLUSIVE |
| `incumbent+consistency` | 0.194715 | -0.000139 | 0.575232 | -0.000313 | [-0.000281, -0.000002] | 0.500 | 662 | 0.701 | 223.82 | INCONCLUSIVE |
| `incumbent+hot_cold` | 0.194593 | -0.000261 | 0.574967 | -0.000578 | [-0.000547, +0.000003] | 0.500 | 662 | 0.698 | 220.00 | INCONCLUSIVE |
| `incumbent+rest_disparity` | 0.194651 | -0.000203 | 0.574983 | -0.000562 | [-0.001282, +0.000852] | 0.500 | 661 | 0.699 | 221.00 | INCONCLUSIVE |
| `incumbent+back_to_back` | 0.194671 | -0.000183 | 0.574937 | -0.000608 | [-0.002319, +0.001990] | 0.501 | 661 | 0.697 | 219.09 | INCONCLUSIVE |
| `incumbent+schedule_density` | 0.194635 | -0.000219 | 0.574857 | -0.000688 | [-0.001260, +0.000868] | 0.500 | 662 | 0.704 | 227.64 | INCONCLUSIVE |
| `incumbent+schedule_missingness` | 0.194290 | -0.000564 | 0.574299 | -0.001246 | [-0.001902, +0.000054] | 0.500 | 662 | 0.701 | 223.82 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold` | 0.194479 | -0.000375 | 0.574710 | -0.000835 | [-0.000694, -0.000077] | 0.500 | 662 | 0.699 | 221.91 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity` | 0.194530 | -0.000324 | 0.574704 | -0.000841 | [-0.001355, +0.000701] | 0.500 | 662 | 0.701 | 223.82 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back` | 0.194539 | -0.000315 | 0.574626 | -0.000919 | [-0.002313, +0.001672] | 0.501 | 660 | 0.700 | 222.00 | INCONCLUSIVE |
| `incumbent+consistency+schedule_density` | 0.194494 | -0.000360 | 0.574531 | -0.001014 | [-0.001501, +0.000655] | 0.500 | 662 | 0.701 | 223.82 | INCONCLUSIVE |
| `incumbent+consistency+schedule_missingness` | 0.194111 | -0.000743 | 0.573886 | -0.001659 | [-0.002214, -0.000016] | 0.501 | 661 | 0.700 | 222.91 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity` | 0.194438 | -0.000416 | 0.574503 | -0.001042 | [-0.001517, +0.000689] | 0.500 | 662 | 0.699 | 221.91 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back` | 0.194510 | -0.000344 | 0.574580 | -0.000965 | [-0.002409, +0.001719] | 0.500 | 660 | 0.698 | 220.09 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_density` | 0.194415 | -0.000439 | 0.574358 | -0.001187 | [-0.001692, +0.000785] | 0.500 | 661 | 0.703 | 226.73 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_missingness` | 0.193990 | -0.000864 | 0.573609 | -0.001936 | [-0.002466, +0.000020] | 0.500 | 662 | 0.698 | 220.00 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back` | 0.194703 | -0.000151 | 0.575011 | -0.000534 | [-0.002352, +0.002076] | 0.500 | 662 | 0.695 | 216.18 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_density` | 0.194545 | -0.000309 | 0.574650 | -0.000895 | [-0.001631, +0.001033] | 0.501 | 657 | 0.702 | 223.09 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_missingness` | 0.194040 | -0.000814 | 0.573610 | -0.001935 | [-0.002769, +0.000696] | 0.501 | 660 | 0.698 | 220.09 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density` | 0.194537 | -0.000317 | 0.574555 | -0.000990 | [-0.002627, +0.002019] | 0.500 | 662 | 0.699 | 221.91 | INCONCLUSIVE |
| `incumbent+back_to_back+schedule_missingness` | 0.194090 | -0.000764 | 0.573645 | -0.001900 | [-0.003251, +0.001639] | 0.500 | 662 | 0.698 | 220.00 | REJECT_DEGENERATE |
| `incumbent+schedule_density+schedule_missingness` | 0.194029 | -0.000825 | 0.573492 | -0.002053 | [-0.002902, +0.000730] | 0.500 | 661 | 0.703 | 226.73 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity` | 0.194332 | -0.000522 | 0.574257 | -0.001288 | [-0.001636, +0.000603] | 0.501 | 661 | 0.700 | 222.91 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+back_to_back` | 0.194393 | -0.000461 | 0.574303 | -0.001242 | [-0.002591, +0.001566] | 0.500 | 661 | 0.702 | 224.82 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+schedule_density` | 0.194285 | -0.000569 | 0.574058 | -0.001487 | [-0.001773, +0.000539] | 0.500 | 662 | 0.702 | 225.73 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+schedule_missingness` | 0.193874 | -0.000980 | 0.573359 | -0.002186 | [-0.002456, -0.000135] | 0.500 | 661 | 0.700 | 222.91 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back` | 0.194577 | -0.000277 | 0.574721 | -0.000824 | [-0.002372, +0.001878] | 0.500 | 661 | 0.700 | 222.91 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+schedule_density` | 0.194422 | -0.000432 | 0.574362 | -0.001183 | [-0.001776, +0.000935] | 0.500 | 661 | 0.700 | 222.91 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+schedule_missingness` | 0.193902 | -0.000952 | 0.573300 | -0.002245 | [-0.002728, +0.000480] | 0.500 | 662 | 0.701 | 223.82 | REJECT_DEGENERATE |
| `incumbent+consistency+back_to_back+schedule_density` | 0.194382 | -0.000472 | 0.574180 | -0.001365 | [-0.002679, +0.001839] | 0.501 | 661 | 0.700 | 222.91 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back+schedule_missingness` | 0.193923 | -0.000931 | 0.573253 | -0.002292 | [-0.003387, +0.001451] | 0.501 | 659 | 0.701 | 223.00 | REJECT_DEGENERATE |
| `incumbent+consistency+schedule_density+schedule_missingness` | 0.193891 | -0.000963 | 0.573183 | -0.002362 | [-0.002761, +0.000497] | 0.500 | 662 | 0.701 | 223.82 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back` | 0.194543 | -0.000311 | 0.574661 | -0.000884 | [-0.002458, +0.001824] | 0.500 | 662 | 0.696 | 218.09 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+schedule_density` | 0.194351 | -0.000503 | 0.574207 | -0.001338 | [-0.001944, +0.000828] | 0.502 | 659 | 0.701 | 223.00 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+schedule_missingness` | 0.193828 | -0.001026 | 0.573134 | -0.002411 | [-0.002952, +0.000545] | 0.501 | 660 | 0.700 | 222.00 | REJECT_DEGENERATE |
| `incumbent+hot_cold+back_to_back+schedule_density` | 0.194388 | -0.000466 | 0.574219 | -0.001326 | [-0.002729, +0.002013] | 0.500 | 662 | 0.701 | 223.82 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back+schedule_missingness` | 0.193936 | -0.000918 | 0.573307 | -0.002238 | [-0.003413, +0.001411] | 0.500 | 661 | 0.699 | 221.00 | REJECT_DEGENERATE |
| `incumbent+hot_cold+schedule_density+schedule_missingness` | 0.193817 | -0.001037 | 0.573016 | -0.002529 | [-0.002986, +0.000578] | 0.500 | 660 | 0.703 | 225.82 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back+schedule_density` | 0.194571 | -0.000283 | 0.574611 | -0.000934 | [-0.002770, +0.002082] | 0.501 | 658 | 0.698 | 218.27 | INCONCLUSIVE |
| `incumbent+rest_disparity+back_to_back+schedule_missingness` | 0.194128 | -0.000726 | 0.573739 | -0.001806 | [-0.003165, +0.001702] | 0.500 | 662 | 0.695 | 216.18 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+schedule_density+schedule_missingness` | 0.193935 | -0.000919 | 0.573275 | -0.002270 | [-0.003062, +0.000799] | 0.501 | 660 | 0.698 | 220.09 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density+schedule_missingness` | 0.193924 | -0.000930 | 0.573171 | -0.002374 | [-0.003730, +0.001580] | 0.501 | 661 | 0.699 | 221.00 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back` | 0.194417 | -0.000437 | 0.574361 | -0.001184 | [-0.002555, +0.001733] | 0.500 | 662 | 0.702 | 225.73 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_density` | 0.194240 | -0.000614 | 0.573944 | -0.001601 | [-0.002087, +0.000685] | 0.500 | 662 | 0.701 | 223.82 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_missingness` | 0.193724 | -0.001130 | 0.572904 | -0.002641 | [-0.002941, +0.000341] | 0.500 | 662 | 0.699 | 221.91 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_density` | 0.194282 | -0.000572 | 0.573970 | -0.001575 | [-0.002880, +0.001781] | 0.501 | 662 | 0.701 | 223.82 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_missingness` | 0.193786 | -0.001068 | 0.572949 | -0.002596 | [-0.003643, +0.001181] | 0.500 | 660 | 0.702 | 223.91 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+schedule_density+schedule_missingness` | 0.193642 | -0.001212 | 0.572616 | -0.002929 | [-0.003035, +0.000321] | 0.500 | 662 | 0.702 | 225.73 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_density` | 0.194419 | -0.000435 | 0.574247 | -0.001298 | [-0.002728, +0.001803] | 0.500 | 662 | 0.695 | 216.18 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_missingness` | 0.193956 | -0.000898 | 0.573330 | -0.002215 | [-0.003361, +0.001405] | 0.501 | 660 | 0.698 | 220.09 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+schedule_density+schedule_missingness` | 0.193803 | -0.001051 | 0.572978 | -0.002567 | [-0.002975, +0.000552] | 0.500 | 662 | 0.699 | 221.91 | REJECT_DEGENERATE |
| `incumbent+consistency+back_to_back+schedule_density+schedule_missingness` | 0.193787 | -0.001067 | 0.572862 | -0.002683 | [-0.003611, +0.001468] | 0.500 | 662 | 0.699 | 221.91 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.194420 | -0.000434 | 0.574272 | -0.001273 | [-0.002806, +0.002004] | 0.500 | 661 | 0.697 | 219.09 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.193965 | -0.000889 | 0.573376 | -0.002169 | [-0.003510, +0.001640] | 0.501 | 662 | 0.696 | 218.09 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.193739 | -0.001115 | 0.572831 | -0.002714 | [-0.003126, +0.000692] | 0.501 | 660 | 0.700 | 222.00 | REJECT_DEGENERATE |
| `incumbent+hot_cold+back_to_back+schedule_density+schedule_missingness` | 0.193813 | -0.001041 | 0.572946 | -0.002599 | [-0.003440, +0.001628] | 0.500 | 661 | 0.699 | 221.00 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.193994 | -0.000860 | 0.573330 | -0.002215 | [-0.003528, +0.001775] | 0.500 | 662 | 0.693 | 214.27 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.194293 | -0.000561 | 0.573969 | -0.001576 | [-0.003017, +0.001893] | 0.501 | 658 | 0.699 | 220.18 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.193757 | -0.001097 | 0.572875 | -0.002670 | [-0.003581, +0.001406] | 0.500 | 661 | 0.702 | 224.82 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.193631 | -0.001223 | 0.572587 | -0.002958 | [-0.003187, +0.000439] | 0.500 | 661 | 0.700 | 222.91 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_density+schedule_missingness` | 0.193653 | -0.001201 | 0.572559 | -0.002986 | [-0.003842, +0.001413] | 0.500 | 662 | 0.701 | 223.82 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.193817 | -0.001037 | 0.572906 | -0.002639 | [-0.003734, +0.001679] | 0.501 | 661 | 0.696 | 217.18 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.193809 | -0.001045 | 0.572895 | -0.002650 | [-0.003958, +0.001713] | 0.501 | 659 | 0.700 | 221.09 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.193694 | -0.001160 | 0.572636 | -0.002909 | [-0.003904, +0.001512] | 0.500 | 662 | 0.698 | 220.00 | REJECT_DEGENERATE |

## Confidence-gate sweeps for incumbent and isolated additions

### `incumbent`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 745 | 0.627 | 146.55 | 662 | 0.702 | 225.73 |
| 0.525 | 688 | 0.637 | 148.18 | 609 | 0.722 | 231.00 |
| 0.550 | 620 | 0.656 | 157.00 | 573 | 0.742 | 238.36 |
| 0.575 | 560 | 0.655 | 140.64 | 510 | 0.761 | 230.73 |
| 0.600 | 496 | 0.673 | 141.64 | 456 | 0.765 | 210.27 |
| 0.625 | 424 | 0.691 | 135.36 | 394 | 0.792 | 201.64 |
| 0.650 | 352 | 0.702 | 119.55 | 341 | 0.818 | 191.64 |
| 0.675 | 290 | 0.724 | 110.91 | 301 | 0.831 | 176.27 |
| 0.700 | 244 | 0.746 | 103.45 | 238 | 0.845 | 145.73 |
| 0.725 | 186 | 0.780 | 90.82 | 170 | 0.882 | 116.36 |
| 0.750 | 117 | 0.803 | 62.45 | 126 | 0.865 | 82.09 |
| 0.775 | 55 | 0.782 | 27.09 | 83 | 0.880 | 56.36 |
| 0.800 | 12 | 1.000 | 10.91 | 33 | 0.879 | 22.36 |

### `incumbent+consistency`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 745 | 0.628 | 148.45 | 662 | 0.701 | 223.82 |
| 0.525 | 684 | 0.637 | 148.36 | 608 | 0.724 | 232.00 |
| 0.550 | 622 | 0.656 | 156.91 | 574 | 0.740 | 237.36 |
| 0.575 | 562 | 0.655 | 140.55 | 510 | 0.761 | 230.73 |
| 0.600 | 496 | 0.673 | 141.64 | 456 | 0.768 | 212.18 |
| 0.625 | 425 | 0.689 | 134.36 | 397 | 0.788 | 200.55 |
| 0.650 | 353 | 0.700 | 118.55 | 341 | 0.821 | 193.55 |
| 0.675 | 290 | 0.728 | 112.82 | 298 | 0.839 | 179.27 |
| 0.700 | 244 | 0.746 | 103.45 | 238 | 0.845 | 145.73 |
| 0.725 | 189 | 0.778 | 91.64 | 170 | 0.876 | 114.45 |
| 0.750 | 118 | 0.805 | 63.36 | 131 | 0.870 | 86.64 |
| 0.775 | 58 | 0.776 | 27.91 | 84 | 0.869 | 55.36 |
| 0.800 | 12 | 1.000 | 10.91 | 35 | 0.886 | 24.18 |

### `incumbent+hot_cold`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 745 | 0.627 | 146.55 | 662 | 0.698 | 220.00 |
| 0.525 | 686 | 0.636 | 146.36 | 617 | 0.718 | 228.73 |
| 0.550 | 618 | 0.655 | 155.18 | 574 | 0.739 | 235.45 |
| 0.575 | 562 | 0.657 | 142.45 | 514 | 0.761 | 232.45 |
| 0.600 | 500 | 0.672 | 141.45 | 456 | 0.770 | 214.09 |
| 0.625 | 417 | 0.688 | 130.91 | 395 | 0.795 | 204.45 |
| 0.650 | 355 | 0.699 | 118.45 | 346 | 0.818 | 194.27 |
| 0.675 | 295 | 0.722 | 111.64 | 298 | 0.842 | 181.18 |
| 0.700 | 248 | 0.742 | 103.27 | 243 | 0.844 | 148.36 |
| 0.725 | 188 | 0.777 | 90.73 | 172 | 0.872 | 114.36 |
| 0.750 | 122 | 0.803 | 65.09 | 129 | 0.868 | 84.82 |
| 0.775 | 60 | 0.783 | 29.73 | 87 | 0.874 | 58.09 |
| 0.800 | 15 | 0.867 | 9.82 | 36 | 0.889 | 25.09 |

### `incumbent+rest_disparity`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 745 | 0.619 | 135.09 | 662 | 0.698 | 220.00 |
| 0.525 | 683 | 0.633 | 141.73 | 610 | 0.721 | 230.00 |
| 0.550 | 618 | 0.652 | 151.36 | 568 | 0.745 | 239.55 |
| 0.575 | 561 | 0.665 | 151.09 | 517 | 0.764 | 237.09 |
| 0.600 | 486 | 0.675 | 140.18 | 454 | 0.767 | 210.36 |
| 0.625 | 423 | 0.693 | 136.36 | 396 | 0.790 | 201.55 |
| 0.650 | 345 | 0.687 | 107.45 | 346 | 0.812 | 190.45 |
| 0.675 | 303 | 0.700 | 101.73 | 302 | 0.841 | 182.91 |
| 0.700 | 244 | 0.742 | 101.55 | 241 | 0.842 | 146.55 |
| 0.725 | 200 | 0.770 | 94.00 | 174 | 0.885 | 120.00 |
| 0.750 | 116 | 0.836 | 69.18 | 128 | 0.875 | 85.82 |
| 0.775 | 59 | 0.763 | 26.91 | 82 | 0.866 | 53.55 |
| 0.800 | 20 | 0.850 | 12.45 | 38 | 0.895 | 26.91 |

### `incumbent+back_to_back`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 745 | 0.624 | 142.73 | 662 | 0.698 | 220.00 |
| 0.525 | 689 | 0.636 | 147.18 | 609 | 0.721 | 229.09 |
| 0.550 | 627 | 0.656 | 157.64 | 560 | 0.743 | 234.18 |
| 0.575 | 570 | 0.661 | 149.73 | 511 | 0.763 | 233.55 |
| 0.600 | 495 | 0.677 | 144.55 | 457 | 0.770 | 215.00 |
| 0.625 | 419 | 0.695 | 136.55 | 395 | 0.800 | 208.27 |
| 0.650 | 363 | 0.691 | 116.18 | 339 | 0.820 | 191.73 |
| 0.675 | 302 | 0.715 | 110.36 | 296 | 0.838 | 177.45 |
| 0.700 | 249 | 0.731 | 98.45 | 243 | 0.840 | 146.45 |
| 0.725 | 189 | 0.772 | 89.73 | 183 | 0.863 | 118.64 |
| 0.750 | 129 | 0.814 | 71.45 | 133 | 0.872 | 88.45 |
| 0.775 | 80 | 0.838 | 47.91 | 87 | 0.897 | 61.91 |
| 0.800 | 31 | 0.806 | 16.73 | 44 | 0.886 | 30.45 |

### `incumbent+schedule_density`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 745 | 0.628 | 148.45 | 662 | 0.704 | 227.64 |
| 0.525 | 688 | 0.635 | 146.27 | 610 | 0.725 | 233.82 |
| 0.550 | 614 | 0.650 | 147.73 | 570 | 0.739 | 233.73 |
| 0.575 | 557 | 0.662 | 147.45 | 510 | 0.761 | 230.73 |
| 0.600 | 491 | 0.676 | 142.82 | 462 | 0.764 | 211.91 |
| 0.625 | 421 | 0.698 | 140.27 | 400 | 0.785 | 199.45 |
| 0.650 | 363 | 0.702 | 123.82 | 344 | 0.826 | 198.18 |
| 0.675 | 293 | 0.724 | 111.73 | 302 | 0.825 | 173.36 |
| 0.700 | 241 | 0.743 | 100.73 | 242 | 0.860 | 155.09 |
| 0.725 | 187 | 0.791 | 95.55 | 180 | 0.867 | 117.82 |
| 0.750 | 116 | 0.793 | 59.64 | 128 | 0.859 | 82.00 |
| 0.775 | 60 | 0.817 | 33.55 | 86 | 0.884 | 59.09 |
| 0.800 | 14 | 1.000 | 12.73 | 39 | 0.923 | 29.73 |

### `incumbent+schedule_missingness`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 745 | 0.632 | 154.18 | 662 | 0.701 | 223.82 |
| 0.525 | 688 | 0.642 | 155.82 | 609 | 0.722 | 231.00 |
| 0.550 | 621 | 0.662 | 163.64 | 574 | 0.742 | 239.27 |
| 0.575 | 560 | 0.662 | 148.27 | 514 | 0.763 | 234.36 |
| 0.600 | 497 | 0.676 | 144.45 | 459 | 0.767 | 213.00 |
| 0.625 | 424 | 0.696 | 139.18 | 396 | 0.795 | 205.36 |
| 0.650 | 353 | 0.700 | 118.55 | 344 | 0.820 | 194.36 |
| 0.675 | 290 | 0.728 | 112.82 | 302 | 0.838 | 181.00 |
| 0.700 | 244 | 0.746 | 103.45 | 242 | 0.847 | 149.36 |
| 0.725 | 185 | 0.778 | 89.91 | 173 | 0.884 | 119.09 |
| 0.750 | 117 | 0.803 | 62.45 | 126 | 0.865 | 82.09 |
| 0.775 | 55 | 0.782 | 27.09 | 83 | 0.880 | 56.36 |
| 0.800 | 11 | 1.000 | 10.00 | 33 | 0.879 | 22.36 |

## Development ranking (not a promotion ranking)

| Rank | Variant | Holdout Brier | Δ Brier | Verdict |
|---:|---|---:|---:|---|
| 1 | `incumbent+consistency+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.193631 | -0.001223 | REJECT_DEGENERATE |
| 2 | `incumbent+consistency+hot_cold+schedule_density+schedule_missingness` | 0.193642 | -0.001212 | REJECT_DEGENERATE |
| 3 | `incumbent+consistency+hot_cold+back_to_back+schedule_density+schedule_missingness` | 0.193653 | -0.001201 | REJECT_DEGENERATE |
| 4 | `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.193694 | -0.001160 | REJECT_DEGENERATE |
| 5 | `incumbent+consistency+hot_cold+rest_disparity+schedule_missingness` | 0.193724 | -0.001130 | REJECT_DEGENERATE |
| 6 | `incumbent+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.193739 | -0.001115 | REJECT_DEGENERATE |
| 7 | `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.193757 | -0.001097 | REJECT_DEGENERATE |
| 8 | `incumbent+consistency+hot_cold+back_to_back+schedule_missingness` | 0.193786 | -0.001068 | REJECT_DEGENERATE |
| 9 | `incumbent+consistency+back_to_back+schedule_density+schedule_missingness` | 0.193787 | -0.001067 | REJECT_DEGENERATE |
| 10 | `incumbent+consistency+rest_disparity+schedule_density+schedule_missingness` | 0.193803 | -0.001051 | REJECT_DEGENERATE |

# WNBA

## Incumbent and data validation

- Artifact: `config/models/wnba-elo-trend-lr-v3.json`
- Version/hash: `wnba-elo-trend-lr-v3` / `d339198b196cb3fcd02950ed5efbdd4cefbeb95b2cd63cd756e77f99dc483719`
- Incumbent features: `elo_probability, trend_gap, defensive_trend_gap`
- Active artifact threshold: 0.500
- Split: train 449, validation 133, holdout 175
- Processed data: `data/processed/wnba/games.jsonl`
- Data SHA-256: `dde0683dde06682638cb885b86f0853d4347a6a04db992b9a1b01722d8ae2b9c`
- Raw rows / loaded modeling games / excluded by loader / duplicate IDs / invalid JSON: 810 / 810 / 0 / 0 / 0
- Walk-forward binary rows / excluded before evaluation: 757 / 53 (The first 50 history games seed features; tied non-soccer results are excluded.)
- Rows with `observed_at_utc`: 0 (metadata complete: False)
- Schedule coverage train/validation/holdout: 98.9% / 97.0% / 100.0%

> Provenance limitation: Completed score order is enforced by event time, but legacy processed rows do not carry retrieval observed_at_utc; this experiment is predictive development evidence, not promotion-grade source provenance.

### Exact active artifact versus matched refit control

The active row uses the snapshotted coefficients and threshold. The refit row uses the same feature list but re-estimates coefficients on the current train cohort. All factorial deltas use the refit row as control.

| Model | Validation Brier | Holdout Brier | Holdout log loss | Gate | Holdout calls | Holdout hit | -110 units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Exact active artifact | 0.216890 | 0.218558 | 0.628512 | 0.500 | 175 | 0.691 | 56.00 |
| Matched refit control (`incumbent`) | 0.216890 | 0.218558 | 0.628512 | 0.505 | 173 | 0.688 | 54.18 |

Active-to-refit coefficient deltas: `elo_probability=+0.000000, trend_gap=+0.000000, defensive_trend_gap=+0.000000, intercept=+0.000000`

### Feature distributions

| Cohort | Feature | Mean | Std | Min | Max | Unique | Zero rate |
|---|---|---:|---:|---:|---:|---:|---:|
| train | `consistency_gap` | -0.0039 | 0.0828 | -0.9303 | 0.3170 | 446 | 0.7% |
| train | `hot_cold_gap` | 0.0024 | 0.3576 | -1.3713 | 1.1817 | 448 | 0.4% |
| train | `rest_disparity` | 0.1403 | 1.5132 | -6.0000 | 6.0000 | 13 | 47.2% |
| train | `back_to_back_gap` | -0.0535 | 0.5099 | -1.0000 | 1.0000 | 3 | 73.7% |
| train | `games_last_7_gap` | -0.0223 | 0.8006 | -2.0000 | 3.0000 | 6 | 52.3% |
| train | `schedule_available` | 0.9889 | 0.1049 | 0.0000 | 1.0000 | 2 | 1.1% |
| validation | `consistency_gap` | -0.0091 | 0.1587 | -0.9090 | 0.8679 | 133 | 0.0% |
| validation | `hot_cold_gap` | 0.0103 | 0.3954 | -0.9259 | 1.0604 | 133 | 0.0% |
| validation | `rest_disparity` | 0.2105 | 1.4356 | -4.0000 | 5.0000 | 10 | 49.6% |
| validation | `back_to_back_gap` | -0.0902 | 0.4151 | -1.0000 | 1.0000 | 3 | 82.0% |
| validation | `games_last_7_gap` | 0.0000 | 0.8671 | -2.0000 | 2.0000 | 5 | 49.6% |
| validation | `schedule_available` | 0.9699 | 0.1708 | 0.0000 | 1.0000 | 2 | 3.0% |
| holdout | `consistency_gap` | -0.0005 | 0.0291 | -0.0893 | 0.0790 | 175 | 0.0% |
| holdout | `hot_cold_gap` | -0.0121 | 0.4449 | -1.2180 | 1.3326 | 175 | 0.0% |
| holdout | `rest_disparity` | 0.0057 | 1.7452 | -6.0000 | 5.0000 | 12 | 30.9% |
| holdout | `back_to_back_gap` | 0.0000 | 0.5237 | -1.0000 | 1.0000 | 3 | 72.6% |
| holdout | `games_last_7_gap` | 0.0457 | 0.9430 | -2.0000 | 3.0000 | 6 | 41.1% |
| holdout | `schedule_available` | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 1 | 0.0% |

## Isolated additions

Lower Brier/log loss is better. A negative delta favors the challenger. `KEEP_FOR_FRESH_TEST` requires validation improvement and a holdout Brier/log-loss improvement whose clustered 95% CI excludes zero. Variants using the near-constant missingness flag are `REJECT_DEGENERATE` even if their numerical screen looks favorable.

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent` | 0.218558 | +0.000000 | 0.628512 | +0.000000 | [+0.000000, +0.000000] | 0.505 | 173 | 0.688 | 54.18 | INCONCLUSIVE |
| `incumbent+consistency` | 0.218539 | -0.000019 | 0.628468 | -0.000044 | [-0.000052, +0.000017] | 0.505 | 174 | 0.690 | 55.09 | INCONCLUSIVE |
| `incumbent+hot_cold` | 0.217533 | -0.001025 | 0.626375 | -0.002137 | [-0.002267, +0.000289] | 0.501 | 174 | 0.690 | 55.09 | INCONCLUSIVE |
| `incumbent+rest_disparity` | 0.218750 | +0.000192 | 0.628981 | +0.000469 | [-0.000915, +0.001301] | 0.503 | 173 | 0.688 | 54.18 | INCONCLUSIVE |
| `incumbent+back_to_back` | 0.218898 | +0.000340 | 0.629270 | +0.000758 | [-0.000604, +0.001295] | 0.504 | 174 | 0.684 | 53.18 | INCONCLUSIVE |
| `incumbent+schedule_density` | 0.220646 | +0.002088 | 0.632891 | +0.004379 | [-0.001752, +0.006096] | 0.502 | 175 | 0.674 | 50.27 | INCONCLUSIVE |
| `incumbent+schedule_missingness` | 0.218537 | -0.000021 | 0.628459 | -0.000053 | [-0.000107, +0.000066] | 0.506 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |

## Pairwise interactions

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent+consistency+hot_cold` | 0.217517 | -0.001041 | 0.626342 | -0.002170 | [-0.002310, +0.000305] | 0.501 | 173 | 0.688 | 54.18 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity` | 0.218729 | +0.000171 | 0.628935 | +0.000423 | [-0.000899, +0.001226] | 0.504 | 173 | 0.688 | 54.18 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back` | 0.218873 | +0.000315 | 0.629215 | +0.000703 | [-0.000630, +0.001225] | 0.504 | 174 | 0.684 | 53.18 | INCONCLUSIVE |
| `incumbent+consistency+schedule_density` | 0.220629 | +0.002071 | 0.632852 | +0.004340 | [-0.001900, +0.006157] | 0.502 | 175 | 0.674 | 50.27 | INCONCLUSIVE |
| `incumbent+consistency+schedule_missingness` | 0.218502 | -0.000056 | 0.628379 | -0.000133 | [-0.000154, +0.000043] | 0.505 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity` | 0.217724 | -0.000834 | 0.626854 | -0.001658 | [-0.002397, +0.000669] | 0.501 | 175 | 0.680 | 52.18 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back` | 0.217865 | -0.000693 | 0.627127 | -0.001385 | [-0.002276, +0.000819] | 0.500 | 175 | 0.680 | 52.18 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_density` | 0.219559 | +0.001001 | 0.630650 | +0.002138 | [-0.003192, +0.005355] | 0.500 | 175 | 0.680 | 52.18 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_missingness` | 0.217527 | -0.001031 | 0.626353 | -0.002159 | [-0.002259, +0.000254] | 0.501 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back` | 0.218867 | +0.000309 | 0.629232 | +0.000720 | [-0.000894, +0.001449] | 0.504 | 174 | 0.684 | 53.18 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_density` | 0.222017 | +0.003459 | 0.636096 | +0.007584 | [-0.001106, +0.007942] | 0.502 | 174 | 0.667 | 47.45 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_missingness` | 0.218741 | +0.000183 | 0.628955 | +0.000443 | [-0.000878, +0.001266] | 0.504 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density` | 0.221648 | +0.003090 | 0.635153 | +0.006641 | [-0.000861, +0.007149] | 0.503 | 172 | 0.669 | 47.55 | INCONCLUSIVE |
| `incumbent+back_to_back+schedule_missingness` | 0.218887 | +0.000329 | 0.629240 | +0.000728 | [-0.000663, +0.001244] | 0.505 | 174 | 0.684 | 53.18 | REJECT_DEGENERATE |
| `incumbent+schedule_density+schedule_missingness` | 0.220634 | +0.002076 | 0.632855 | +0.004343 | [-0.001882, +0.006159] | 0.503 | 173 | 0.671 | 48.45 | REJECT_DEGENERATE |

## Every feature combination

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent` | 0.218558 | +0.000000 | 0.628512 | +0.000000 | [+0.000000, +0.000000] | 0.505 | 173 | 0.688 | 54.18 | INCONCLUSIVE |
| `incumbent+consistency` | 0.218539 | -0.000019 | 0.628468 | -0.000044 | [-0.000052, +0.000017] | 0.505 | 174 | 0.690 | 55.09 | INCONCLUSIVE |
| `incumbent+hot_cold` | 0.217533 | -0.001025 | 0.626375 | -0.002137 | [-0.002267, +0.000289] | 0.501 | 174 | 0.690 | 55.09 | INCONCLUSIVE |
| `incumbent+rest_disparity` | 0.218750 | +0.000192 | 0.628981 | +0.000469 | [-0.000915, +0.001301] | 0.503 | 173 | 0.688 | 54.18 | INCONCLUSIVE |
| `incumbent+back_to_back` | 0.218898 | +0.000340 | 0.629270 | +0.000758 | [-0.000604, +0.001295] | 0.504 | 174 | 0.684 | 53.18 | INCONCLUSIVE |
| `incumbent+schedule_density` | 0.220646 | +0.002088 | 0.632891 | +0.004379 | [-0.001752, +0.006096] | 0.502 | 175 | 0.674 | 50.27 | INCONCLUSIVE |
| `incumbent+schedule_missingness` | 0.218537 | -0.000021 | 0.628459 | -0.000053 | [-0.000107, +0.000066] | 0.506 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold` | 0.217517 | -0.001041 | 0.626342 | -0.002170 | [-0.002310, +0.000305] | 0.501 | 173 | 0.688 | 54.18 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity` | 0.218729 | +0.000171 | 0.628935 | +0.000423 | [-0.000899, +0.001226] | 0.504 | 173 | 0.688 | 54.18 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back` | 0.218873 | +0.000315 | 0.629215 | +0.000703 | [-0.000630, +0.001225] | 0.504 | 174 | 0.684 | 53.18 | INCONCLUSIVE |
| `incumbent+consistency+schedule_density` | 0.220629 | +0.002071 | 0.632852 | +0.004340 | [-0.001900, +0.006157] | 0.502 | 175 | 0.674 | 50.27 | INCONCLUSIVE |
| `incumbent+consistency+schedule_missingness` | 0.218502 | -0.000056 | 0.628379 | -0.000133 | [-0.000154, +0.000043] | 0.505 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity` | 0.217724 | -0.000834 | 0.626854 | -0.001658 | [-0.002397, +0.000669] | 0.501 | 175 | 0.680 | 52.18 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back` | 0.217865 | -0.000693 | 0.627127 | -0.001385 | [-0.002276, +0.000819] | 0.500 | 175 | 0.680 | 52.18 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_density` | 0.219559 | +0.001001 | 0.630650 | +0.002138 | [-0.003192, +0.005355] | 0.500 | 175 | 0.680 | 52.18 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_missingness` | 0.217527 | -0.001031 | 0.626353 | -0.002159 | [-0.002259, +0.000254] | 0.501 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back` | 0.218867 | +0.000309 | 0.629232 | +0.000720 | [-0.000894, +0.001449] | 0.504 | 174 | 0.684 | 53.18 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_density` | 0.222017 | +0.003459 | 0.636096 | +0.007584 | [-0.001106, +0.007942] | 0.502 | 174 | 0.667 | 47.45 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_missingness` | 0.218741 | +0.000183 | 0.628955 | +0.000443 | [-0.000878, +0.001266] | 0.504 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density` | 0.221648 | +0.003090 | 0.635153 | +0.006641 | [-0.000861, +0.007149] | 0.503 | 172 | 0.669 | 47.55 | INCONCLUSIVE |
| `incumbent+back_to_back+schedule_missingness` | 0.218887 | +0.000329 | 0.629240 | +0.000728 | [-0.000663, +0.001244] | 0.505 | 174 | 0.684 | 53.18 | REJECT_DEGENERATE |
| `incumbent+schedule_density+schedule_missingness` | 0.220634 | +0.002076 | 0.632855 | +0.004343 | [-0.001882, +0.006159] | 0.503 | 173 | 0.671 | 48.45 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity` | 0.217705 | -0.000853 | 0.626812 | -0.001700 | [-0.002384, +0.000718] | 0.501 | 175 | 0.680 | 52.18 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+back_to_back` | 0.217849 | -0.000709 | 0.627093 | -0.001419 | [-0.002340, +0.000896] | 0.500 | 175 | 0.680 | 52.18 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+schedule_density` | 0.219546 | +0.000988 | 0.630623 | +0.002111 | [-0.003203, +0.005270] | 0.501 | 175 | 0.680 | 52.18 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+schedule_missingness` | 0.217516 | -0.001042 | 0.626329 | -0.002183 | [-0.002239, +0.000266] | 0.501 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back` | 0.218843 | +0.000285 | 0.629178 | +0.000666 | [-0.000824, +0.001344] | 0.504 | 174 | 0.684 | 53.18 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+schedule_density` | 0.222014 | +0.003456 | 0.636087 | +0.007575 | [-0.000832, +0.007847] | 0.502 | 174 | 0.667 | 47.45 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+schedule_missingness` | 0.218707 | +0.000149 | 0.628879 | +0.000367 | [-0.000917, +0.001210] | 0.505 | 172 | 0.686 | 53.27 | REJECT_DEGENERATE |
| `incumbent+consistency+back_to_back+schedule_density` | 0.221648 | +0.003090 | 0.635148 | +0.006636 | [-0.000820, +0.007477] | 0.503 | 172 | 0.669 | 47.55 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back+schedule_missingness` | 0.218864 | +0.000306 | 0.629187 | +0.000675 | [-0.000672, +0.001231] | 0.505 | 174 | 0.684 | 53.18 | REJECT_DEGENERATE |
| `incumbent+consistency+schedule_density+schedule_missingness` | 0.220606 | +0.002048 | 0.632792 | +0.004280 | [-0.001775, +0.006260] | 0.502 | 173 | 0.671 | 48.45 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back` | 0.217853 | -0.000705 | 0.627130 | -0.001382 | [-0.002341, +0.000883] | 0.500 | 174 | 0.678 | 51.27 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+schedule_density` | 0.220948 | +0.002390 | 0.633954 | +0.005442 | [-0.002073, +0.007225] | 0.502 | 175 | 0.669 | 48.36 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+schedule_missingness` | 0.217714 | -0.000844 | 0.626821 | -0.001691 | [-0.002389, +0.000645] | 0.501 | 175 | 0.680 | 52.18 | REJECT_DEGENERATE |
| `incumbent+hot_cold+back_to_back+schedule_density` | 0.220552 | +0.001994 | 0.632920 | +0.004408 | [-0.002452, +0.006480] | 0.501 | 175 | 0.669 | 48.36 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back+schedule_missingness` | 0.217842 | -0.000716 | 0.627070 | -0.001442 | [-0.002276, +0.000855] | 0.501 | 174 | 0.684 | 53.18 | REJECT_DEGENERATE |
| `incumbent+hot_cold+schedule_density+schedule_missingness` | 0.219549 | +0.000991 | 0.630616 | +0.002104 | [-0.003314, +0.005481] | 0.501 | 175 | 0.680 | 52.18 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back+schedule_density` | 0.222007 | +0.003449 | 0.636074 | +0.007562 | [-0.000792, +0.008071] | 0.502 | 174 | 0.667 | 47.45 | INCONCLUSIVE |
| `incumbent+rest_disparity+back_to_back+schedule_missingness` | 0.218845 | +0.000287 | 0.629176 | +0.000664 | [-0.000880, +0.001405] | 0.504 | 174 | 0.684 | 53.18 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+schedule_density+schedule_missingness` | 0.222009 | +0.003451 | 0.636062 | +0.007550 | [-0.000937, +0.007604] | 0.502 | 174 | 0.667 | 47.45 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density+schedule_missingness` | 0.221628 | +0.003070 | 0.635097 | +0.006585 | [-0.001110, +0.007295] | 0.503 | 173 | 0.665 | 46.55 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back` | 0.217836 | -0.000722 | 0.627094 | -0.001418 | [-0.002382, +0.000949] | 0.500 | 173 | 0.682 | 52.27 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_density` | 0.220935 | +0.002377 | 0.633928 | +0.005416 | [-0.002193, +0.007173] | 0.501 | 175 | 0.669 | 48.36 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_missingness` | 0.217705 | -0.000853 | 0.626801 | -0.001711 | [-0.002356, +0.000643] | 0.501 | 175 | 0.680 | 52.18 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_density` | 0.220533 | +0.001975 | 0.632883 | +0.004371 | [-0.002402, +0.006517] | 0.501 | 174 | 0.672 | 49.36 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_missingness` | 0.217837 | -0.000721 | 0.627056 | -0.001456 | [-0.002318, +0.000896] | 0.501 | 174 | 0.684 | 53.18 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+schedule_density+schedule_missingness` | 0.219544 | +0.000986 | 0.630606 | +0.002094 | [-0.003384, +0.005369] | 0.501 | 175 | 0.680 | 52.18 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_density` | 0.221995 | +0.003437 | 0.636044 | +0.007532 | [-0.000917, +0.007984] | 0.502 | 174 | 0.667 | 47.45 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_missingness` | 0.218818 | +0.000260 | 0.629113 | +0.000601 | [-0.000982, +0.001330] | 0.505 | 174 | 0.684 | 53.18 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+schedule_density+schedule_missingness` | 0.221985 | +0.003427 | 0.636008 | +0.007496 | [-0.001097, +0.008023] | 0.503 | 174 | 0.667 | 47.45 | REJECT_DEGENERATE |
| `incumbent+consistency+back_to_back+schedule_density+schedule_missingness` | 0.221601 | +0.003043 | 0.635035 | +0.006523 | [-0.000876, +0.007201] | 0.503 | 173 | 0.665 | 46.55 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.220937 | +0.002379 | 0.633927 | +0.005415 | [-0.002173, +0.007120] | 0.502 | 174 | 0.672 | 49.36 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.217844 | -0.000714 | 0.627101 | -0.001411 | [-0.002358, +0.000879] | 0.501 | 174 | 0.678 | 51.27 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.220941 | +0.002383 | 0.633926 | +0.005414 | [-0.002156, +0.007080] | 0.501 | 175 | 0.669 | 48.36 | REJECT_DEGENERATE |
| `incumbent+hot_cold+back_to_back+schedule_density+schedule_missingness` | 0.220544 | +0.001986 | 0.632891 | +0.004379 | [-0.002632, +0.006365] | 0.501 | 175 | 0.669 | 48.36 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.221989 | +0.003431 | 0.636020 | +0.007508 | [-0.001147, +0.007935] | 0.503 | 174 | 0.667 | 47.45 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.220931 | +0.002373 | 0.633916 | +0.005404 | [-0.002354, +0.007044] | 0.500 | 175 | 0.669 | 48.36 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.217830 | -0.000728 | 0.627068 | -0.001444 | [-0.002370, +0.000951] | 0.501 | 174 | 0.678 | 51.27 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.220942 | +0.002384 | 0.633925 | +0.005413 | [-0.002041, +0.007121] | 0.501 | 175 | 0.669 | 48.36 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_density+schedule_missingness` | 0.220540 | +0.001982 | 0.632881 | +0.004369 | [-0.002631, +0.006354] | 0.501 | 175 | 0.669 | 48.36 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.221969 | +0.003411 | 0.635976 | +0.007464 | [-0.001022, +0.007963] | 0.503 | 174 | 0.667 | 47.45 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.220932 | +0.002374 | 0.633903 | +0.005391 | [-0.002062, +0.007087] | 0.501 | 175 | 0.669 | 48.36 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.220930 | +0.002372 | 0.633900 | +0.005388 | [-0.002041, +0.007000] | 0.501 | 175 | 0.669 | 48.36 | REJECT_DEGENERATE |

## Confidence-gate sweeps for incumbent and isolated additions

### `incumbent`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 133 | 0.669 | 36.91 | 175 | 0.691 | 56.00 |
| 0.525 | 118 | 0.686 | 36.64 | 152 | 0.691 | 48.45 |
| 0.550 | 102 | 0.686 | 31.64 | 141 | 0.681 | 42.27 |
| 0.575 | 89 | 0.708 | 31.27 | 125 | 0.720 | 46.82 |
| 0.600 | 81 | 0.716 | 29.73 | 114 | 0.711 | 40.64 |
| 0.625 | 73 | 0.712 | 26.27 | 93 | 0.720 | 34.91 |
| 0.650 | 62 | 0.758 | 27.73 | 69 | 0.739 | 28.36 |
| 0.675 | 49 | 0.735 | 19.73 | 51 | 0.725 | 19.64 |
| 0.700 | 33 | 0.758 | 14.73 | 34 | 0.706 | 11.82 |
| 0.725 | 22 | 0.773 | 10.45 | 19 | 0.842 | 11.55 |
| 0.750 | 12 | 0.917 | 9.00 | 7 | 0.714 | 2.55 |
| 0.775 | 8 | 0.875 | 5.36 | 3 | 0.667 | 0.82 |
| 0.800 | 1 | 1.000 | 0.91 | 0 | — | 0.00 |

### `incumbent+consistency`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 133 | 0.669 | 36.91 | 175 | 0.691 | 56.00 |
| 0.525 | 118 | 0.686 | 36.64 | 154 | 0.688 | 48.36 |
| 0.550 | 102 | 0.686 | 31.64 | 141 | 0.681 | 42.27 |
| 0.575 | 89 | 0.708 | 31.27 | 126 | 0.722 | 47.73 |
| 0.600 | 80 | 0.713 | 28.82 | 114 | 0.711 | 40.64 |
| 0.625 | 73 | 0.712 | 26.27 | 93 | 0.720 | 34.91 |
| 0.650 | 62 | 0.758 | 27.73 | 69 | 0.739 | 28.36 |
| 0.675 | 49 | 0.735 | 19.73 | 51 | 0.725 | 19.64 |
| 0.700 | 33 | 0.758 | 14.73 | 34 | 0.706 | 11.82 |
| 0.725 | 22 | 0.773 | 10.45 | 19 | 0.842 | 11.55 |
| 0.750 | 12 | 0.917 | 9.00 | 7 | 0.714 | 2.55 |
| 0.775 | 8 | 0.875 | 5.36 | 3 | 0.667 | 0.82 |
| 0.800 | 1 | 1.000 | 0.91 | 0 | — | 0.00 |

### `incumbent+hot_cold`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 133 | 0.677 | 38.82 | 175 | 0.686 | 54.09 |
| 0.525 | 118 | 0.678 | 34.73 | 154 | 0.695 | 50.27 |
| 0.550 | 104 | 0.683 | 31.55 | 142 | 0.683 | 43.18 |
| 0.575 | 93 | 0.699 | 31.09 | 127 | 0.724 | 48.64 |
| 0.600 | 83 | 0.699 | 27.73 | 113 | 0.717 | 41.64 |
| 0.625 | 73 | 0.726 | 28.18 | 98 | 0.724 | 37.55 |
| 0.650 | 64 | 0.734 | 25.73 | 71 | 0.732 | 28.27 |
| 0.675 | 51 | 0.745 | 21.55 | 54 | 0.759 | 24.27 |
| 0.700 | 36 | 0.778 | 17.45 | 36 | 0.694 | 11.73 |
| 0.725 | 25 | 0.720 | 9.36 | 21 | 0.762 | 9.55 |
| 0.750 | 15 | 0.800 | 7.91 | 9 | 0.667 | 2.45 |
| 0.775 | 8 | 0.875 | 5.36 | 4 | 0.750 | 1.73 |
| 0.800 | 1 | 1.000 | 0.91 | 0 | — | 0.00 |

### `incumbent+rest_disparity`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 133 | 0.669 | 36.91 | 175 | 0.686 | 54.09 |
| 0.525 | 120 | 0.683 | 36.55 | 156 | 0.686 | 48.27 |
| 0.550 | 100 | 0.700 | 33.64 | 140 | 0.686 | 43.27 |
| 0.575 | 89 | 0.708 | 31.27 | 126 | 0.730 | 49.64 |
| 0.600 | 82 | 0.720 | 30.64 | 115 | 0.722 | 43.45 |
| 0.625 | 74 | 0.716 | 27.18 | 96 | 0.719 | 35.73 |
| 0.650 | 63 | 0.762 | 28.64 | 68 | 0.735 | 27.45 |
| 0.675 | 49 | 0.735 | 19.73 | 50 | 0.720 | 18.73 |
| 0.700 | 31 | 0.742 | 12.91 | 34 | 0.676 | 9.91 |
| 0.725 | 23 | 0.739 | 9.45 | 21 | 0.810 | 11.45 |
| 0.750 | 13 | 0.846 | 8.00 | 7 | 0.714 | 2.55 |
| 0.775 | 6 | 1.000 | 5.45 | 3 | 0.667 | 0.82 |
| 0.800 | 1 | 1.000 | 0.91 | 0 | — | 0.00 |

### `incumbent+back_to_back`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 133 | 0.669 | 36.91 | 175 | 0.686 | 54.09 |
| 0.525 | 118 | 0.686 | 36.64 | 156 | 0.686 | 48.27 |
| 0.550 | 102 | 0.696 | 33.55 | 141 | 0.681 | 42.27 |
| 0.575 | 89 | 0.708 | 31.27 | 125 | 0.728 | 48.73 |
| 0.600 | 80 | 0.713 | 28.82 | 116 | 0.716 | 42.45 |
| 0.625 | 74 | 0.716 | 27.18 | 94 | 0.713 | 33.91 |
| 0.650 | 63 | 0.762 | 28.64 | 69 | 0.725 | 26.45 |
| 0.675 | 50 | 0.740 | 20.64 | 53 | 0.698 | 17.64 |
| 0.700 | 31 | 0.742 | 12.91 | 31 | 0.677 | 9.09 |
| 0.725 | 23 | 0.739 | 9.45 | 19 | 0.842 | 11.55 |
| 0.750 | 11 | 0.909 | 8.09 | 8 | 0.750 | 3.45 |
| 0.775 | 7 | 1.000 | 6.36 | 3 | 0.667 | 0.82 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+schedule_density`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 133 | 0.692 | 42.64 | 175 | 0.674 | 50.27 |
| 0.525 | 115 | 0.722 | 43.45 | 157 | 0.682 | 47.27 |
| 0.550 | 105 | 0.714 | 38.18 | 144 | 0.701 | 48.82 |
| 0.575 | 89 | 0.674 | 25.55 | 126 | 0.690 | 40.09 |
| 0.600 | 79 | 0.696 | 26.00 | 111 | 0.676 | 32.18 |
| 0.625 | 75 | 0.693 | 24.27 | 92 | 0.707 | 32.09 |
| 0.650 | 62 | 0.726 | 23.91 | 70 | 0.757 | 31.18 |
| 0.675 | 43 | 0.767 | 20.00 | 50 | 0.720 | 18.73 |
| 0.700 | 34 | 0.765 | 15.64 | 39 | 0.744 | 16.36 |
| 0.725 | 22 | 0.773 | 10.45 | 24 | 0.708 | 8.45 |
| 0.750 | 14 | 0.786 | 7.00 | 6 | 0.833 | 3.55 |
| 0.775 | 6 | 0.833 | 3.55 | 3 | 0.667 | 0.82 |
| 0.800 | 3 | 1.000 | 2.73 | 0 | — | 0.00 |

### `incumbent+schedule_missingness`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 133 | 0.669 | 36.91 | 175 | 0.691 | 56.00 |
| 0.525 | 117 | 0.692 | 37.64 | 152 | 0.691 | 48.45 |
| 0.550 | 102 | 0.686 | 31.64 | 141 | 0.681 | 42.27 |
| 0.575 | 91 | 0.714 | 33.09 | 125 | 0.720 | 46.82 |
| 0.600 | 83 | 0.723 | 31.55 | 114 | 0.711 | 40.64 |
| 0.625 | 73 | 0.712 | 26.27 | 93 | 0.720 | 34.91 |
| 0.650 | 63 | 0.746 | 26.73 | 70 | 0.743 | 29.27 |
| 0.675 | 50 | 0.720 | 18.73 | 51 | 0.725 | 19.64 |
| 0.700 | 34 | 0.765 | 15.64 | 34 | 0.706 | 11.82 |
| 0.725 | 24 | 0.792 | 12.27 | 19 | 0.842 | 11.55 |
| 0.750 | 12 | 0.917 | 9.00 | 7 | 0.714 | 2.55 |
| 0.775 | 8 | 0.875 | 5.36 | 3 | 0.667 | 0.82 |
| 0.800 | 1 | 1.000 | 0.91 | 0 | — | 0.00 |

## Development ranking (not a promotion ranking)

| Rank | Variant | Holdout Brier | Δ Brier | Verdict |
|---:|---|---:|---:|---|
| 1 | `incumbent+consistency+hot_cold+schedule_missingness` | 0.217516 | -0.001042 | REJECT_DEGENERATE |
| 2 | `incumbent+consistency+hot_cold` | 0.217517 | -0.001041 | INCONCLUSIVE |
| 3 | `incumbent+hot_cold+schedule_missingness` | 0.217527 | -0.001031 | REJECT_DEGENERATE |
| 4 | `incumbent+hot_cold` | 0.217533 | -0.001025 | INCONCLUSIVE |
| 5 | `incumbent+consistency+hot_cold+rest_disparity` | 0.217705 | -0.000853 | INCONCLUSIVE |
| 6 | `incumbent+consistency+hot_cold+rest_disparity+schedule_missingness` | 0.217705 | -0.000853 | REJECT_DEGENERATE |
| 7 | `incumbent+hot_cold+rest_disparity+schedule_missingness` | 0.217714 | -0.000844 | REJECT_DEGENERATE |
| 8 | `incumbent+hot_cold+rest_disparity` | 0.217724 | -0.000834 | INCONCLUSIVE |
| 9 | `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.217830 | -0.000728 | REJECT_DEGENERATE |
| 10 | `incumbent+consistency+hot_cold+rest_disparity+back_to_back` | 0.217836 | -0.000722 | INCONCLUSIVE |

# NFL

## Incumbent and data validation

- Artifact: `config/models/nfl-elo-trend-lr-v3.json`
- Version/hash: `nfl-elo-trend-lr-v3` / `75466d2935fafde3e00566ac14b7535b4fd36aceaa2760b54ea9a37322e7b967`
- Incumbent features: `elo_probability, trend_gap`
- Active artifact threshold: 0.535
- Split: train 382, validation 143, holdout 110
- Processed data: `data/processed/nfl/games.jsonl`
- Data SHA-256: `17f7961cb8172be8abe3b0ca74ef2f7ddae55231f83adbee3d51c772b65f2cb2`
- Raw rows / loaded modeling games / excluded by loader / duplicate IDs / invalid JSON: 700 / 700 / 0 / 0 / 0
- Walk-forward binary rows / excluded before evaluation: 635 / 65 (The first 50 history games seed features; tied non-soccer results are excluded.)
- Rows with `observed_at_utc`: 0 (metadata complete: False)
- Schedule coverage train/validation/holdout: 100.0% / 100.0% / 100.0%

> Provenance limitation: Completed score order is enforced by event time, but legacy processed rows do not carry retrieval observed_at_utc; this experiment is predictive development evidence, not promotion-grade source provenance.

### Exact active artifact versus matched refit control

The active row uses the snapshotted coefficients and threshold. The refit row uses the same feature list but re-estimates coefficients on the current train cohort. All factorial deltas use the refit row as control.

| Model | Validation Brier | Holdout Brier | Holdout log loss | Gate | Holdout calls | Holdout hit | -110 units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Exact active artifact | 0.223808 | 0.221420 | 0.633065 | 0.535 | 82 | 0.707 | 28.73 |
| Matched refit control (`incumbent`) | 0.223808 | 0.221420 | 0.633065 | 0.500 | 110 | 0.627 | 21.73 |

Active-to-refit coefficient deltas: `elo_probability=+0.000000, trend_gap=+0.000000, intercept=+0.000000`

### Feature distributions

| Cohort | Feature | Mean | Std | Min | Max | Unique | Zero rate |
|---|---|---:|---:|---:|---:|---:|---:|
| train | `consistency_gap` | -0.0002 | 0.0465 | -0.1552 | 0.1928 | 379 | 0.3% |
| train | `hot_cold_gap` | -0.0050 | 0.2176 | -0.8118 | 0.6809 | 366 | 4.5% |
| train | `rest_disparity` | 0.0759 | 0.7773 | -3.0000 | 2.0000 | 6 | 64.7% |
| train | `back_to_back_gap` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1 | 100.0% |
| train | `games_last_7_gap` | -0.0445 | 0.4860 | -1.0000 | 1.0000 | 3 | 76.2% |
| train | `schedule_available` | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 1 | 0.0% |
| validation | `consistency_gap` | -0.0005 | 0.0391 | -0.0955 | 0.1069 | 143 | 0.0% |
| validation | `hot_cold_gap` | -0.0244 | 0.3291 | -0.7197 | 0.8643 | 143 | 0.0% |
| validation | `rest_disparity` | -0.0070 | 0.7977 | -2.0000 | 2.0000 | 5 | 63.6% |
| validation | `back_to_back_gap` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1 | 100.0% |
| validation | `games_last_7_gap` | -0.0140 | 0.5016 | -1.0000 | 1.0000 | 3 | 74.8% |
| validation | `schedule_available` | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 1 | 0.0% |
| holdout | `consistency_gap` | -0.0003 | 0.0425 | -0.1602 | 0.1558 | 110 | 0.0% |
| holdout | `hot_cold_gap` | 0.0156 | 0.4223 | -0.8719 | 1.0799 | 110 | 0.9% |
| holdout | `rest_disparity` | -0.0273 | 0.6669 | -2.0000 | 2.0000 | 5 | 66.4% |
| holdout | `back_to_back_gap` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1 | 100.0% |
| holdout | `games_last_7_gap` | 0.0273 | 0.4565 | -1.0000 | 1.0000 | 3 | 79.1% |
| holdout | `schedule_available` | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 1 | 0.0% |

## Isolated additions

Lower Brier/log loss is better. A negative delta favors the challenger. `KEEP_FOR_FRESH_TEST` requires validation improvement and a holdout Brier/log-loss improvement whose clustered 95% CI excludes zero. Variants using the near-constant missingness flag are `REJECT_DEGENERATE` even if their numerical screen looks favorable.

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent` | 0.221420 | +0.000000 | 0.633065 | +0.000000 | [+0.000000, +0.000000] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+consistency` | 0.221360 | -0.000060 | 0.632951 | -0.000114 | [-0.000161, +0.000039] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+hot_cold` | 0.218952 | -0.002468 | 0.627525 | -0.005540 | [-0.005364, +0.000381] | 0.501 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+rest_disparity` | 0.221182 | -0.000238 | 0.632492 | -0.000573 | [-0.000704, +0.000429] | 0.501 | 108 | 0.620 | 19.91 | INCONCLUSIVE |
| `incumbent+back_to_back` | 0.221420 | +0.000000 | 0.633065 | +0.000000 | [-0.000000, +0.000000] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+schedule_density` | 0.221379 | -0.000041 | 0.632868 | -0.000197 | [-0.002939, +0.002851] | 0.502 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+schedule_missingness` | 0.221440 | +0.000020 | 0.633111 | +0.000046 | [-0.000014, +0.000053] | 0.500 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |

## Pairwise interactions

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent+consistency+hot_cold` | 0.218808 | -0.002612 | 0.627222 | -0.005843 | [-0.005527, +0.000186] | 0.501 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity` | 0.221116 | -0.000304 | 0.632367 | -0.000698 | [-0.000787, +0.000383] | 0.500 | 109 | 0.615 | 18.91 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back` | 0.221360 | -0.000060 | 0.632951 | -0.000114 | [-0.000166, +0.000039] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+consistency+schedule_density` | 0.221290 | -0.000130 | 0.632695 | -0.000370 | [-0.003051, +0.002914] | 0.504 | 107 | 0.636 | 22.82 | INCONCLUSIVE |
| `incumbent+consistency+schedule_missingness` | 0.221343 | -0.000077 | 0.632913 | -0.000152 | [-0.000183, +0.000024] | 0.500 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity` | 0.218582 | -0.002838 | 0.626623 | -0.006442 | [-0.005521, +0.000012] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back` | 0.218952 | -0.002468 | 0.627525 | -0.005540 | [-0.005480, +0.000368] | 0.501 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_density` | 0.218819 | -0.002601 | 0.627184 | -0.005881 | [-0.006721, +0.001303] | 0.504 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_missingness` | 0.218908 | -0.002512 | 0.627424 | -0.005641 | [-0.005477, +0.000404] | 0.501 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back` | 0.221182 | -0.000238 | 0.632492 | -0.000573 | [-0.000694, +0.000430] | 0.501 | 108 | 0.620 | 19.91 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_density` | 0.220437 | -0.000983 | 0.630594 | -0.002471 | [-0.004344, +0.002550] | 0.502 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_missingness` | 0.221195 | -0.000225 | 0.632523 | -0.000542 | [-0.000705, +0.000448] | 0.501 | 107 | 0.617 | 19.00 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density` | 0.221379 | -0.000041 | 0.632868 | -0.000197 | [-0.002861, +0.002815] | 0.502 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+back_to_back+schedule_missingness` | 0.221440 | +0.000020 | 0.633111 | +0.000046 | [-0.000014, +0.000052] | 0.500 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+schedule_density+schedule_missingness` | 0.221383 | -0.000037 | 0.632876 | -0.000189 | [-0.002878, +0.002932] | 0.502 | 109 | 0.633 | 22.73 | REJECT_DEGENERATE |

## Every feature combination

| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `incumbent` | 0.221420 | +0.000000 | 0.633065 | +0.000000 | [+0.000000, +0.000000] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+consistency` | 0.221360 | -0.000060 | 0.632951 | -0.000114 | [-0.000161, +0.000039] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+hot_cold` | 0.218952 | -0.002468 | 0.627525 | -0.005540 | [-0.005364, +0.000381] | 0.501 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+rest_disparity` | 0.221182 | -0.000238 | 0.632492 | -0.000573 | [-0.000704, +0.000429] | 0.501 | 108 | 0.620 | 19.91 | INCONCLUSIVE |
| `incumbent+back_to_back` | 0.221420 | +0.000000 | 0.633065 | +0.000000 | [-0.000000, +0.000000] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+schedule_density` | 0.221379 | -0.000041 | 0.632868 | -0.000197 | [-0.002939, +0.002851] | 0.502 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+schedule_missingness` | 0.221440 | +0.000020 | 0.633111 | +0.000046 | [-0.000014, +0.000053] | 0.500 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold` | 0.218808 | -0.002612 | 0.627222 | -0.005843 | [-0.005527, +0.000186] | 0.501 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity` | 0.221116 | -0.000304 | 0.632367 | -0.000698 | [-0.000787, +0.000383] | 0.500 | 109 | 0.615 | 18.91 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back` | 0.221360 | -0.000060 | 0.632951 | -0.000114 | [-0.000166, +0.000039] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+consistency+schedule_density` | 0.221290 | -0.000130 | 0.632695 | -0.000370 | [-0.003051, +0.002914] | 0.504 | 107 | 0.636 | 22.82 | INCONCLUSIVE |
| `incumbent+consistency+schedule_missingness` | 0.221343 | -0.000077 | 0.632913 | -0.000152 | [-0.000183, +0.000024] | 0.500 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity` | 0.218582 | -0.002838 | 0.626623 | -0.006442 | [-0.005521, +0.000012] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back` | 0.218952 | -0.002468 | 0.627525 | -0.005540 | [-0.005480, +0.000368] | 0.501 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_density` | 0.218819 | -0.002601 | 0.627184 | -0.005881 | [-0.006721, +0.001303] | 0.504 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+hot_cold+schedule_missingness` | 0.218908 | -0.002512 | 0.627424 | -0.005641 | [-0.005477, +0.000404] | 0.501 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back` | 0.221182 | -0.000238 | 0.632492 | -0.000573 | [-0.000694, +0.000430] | 0.501 | 108 | 0.620 | 19.91 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_density` | 0.220437 | -0.000983 | 0.630594 | -0.002471 | [-0.004344, +0.002550] | 0.502 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+rest_disparity+schedule_missingness` | 0.221195 | -0.000225 | 0.632523 | -0.000542 | [-0.000705, +0.000448] | 0.501 | 107 | 0.617 | 19.00 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density` | 0.221379 | -0.000041 | 0.632868 | -0.000197 | [-0.002861, +0.002815] | 0.502 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+back_to_back+schedule_missingness` | 0.221440 | +0.000020 | 0.633111 | +0.000046 | [-0.000014, +0.000052] | 0.500 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+schedule_density+schedule_missingness` | 0.221383 | -0.000037 | 0.632876 | -0.000189 | [-0.002878, +0.002932] | 0.502 | 109 | 0.633 | 22.73 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity` | 0.218483 | -0.002937 | 0.626421 | -0.006644 | [-0.005632, +0.000102] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+back_to_back` | 0.218808 | -0.002612 | 0.627222 | -0.005843 | [-0.005687, +0.000382] | 0.501 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+schedule_density` | 0.218697 | -0.002723 | 0.626935 | -0.006130 | [-0.006938, +0.001324] | 0.505 | 107 | 0.636 | 22.82 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+schedule_missingness` | 0.218821 | -0.002599 | 0.627247 | -0.005818 | [-0.005606, +0.000243] | 0.502 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back` | 0.221116 | -0.000304 | 0.632367 | -0.000698 | [-0.000762, +0.000411] | 0.500 | 109 | 0.615 | 18.91 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+schedule_density` | 0.220345 | -0.001075 | 0.630406 | -0.002659 | [-0.004387, +0.002555] | 0.501 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+schedule_missingness` | 0.221161 | -0.000259 | 0.632454 | -0.000611 | [-0.000725, +0.000407] | 0.501 | 107 | 0.617 | 19.00 | REJECT_DEGENERATE |
| `incumbent+consistency+back_to_back+schedule_density` | 0.221290 | -0.000130 | 0.632695 | -0.000370 | [-0.002984, +0.002723] | 0.504 | 107 | 0.636 | 22.82 | INCONCLUSIVE |
| `incumbent+consistency+back_to_back+schedule_missingness` | 0.221343 | -0.000077 | 0.632913 | -0.000152 | [-0.000178, +0.000024] | 0.500 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+consistency+schedule_density+schedule_missingness` | 0.221314 | -0.000106 | 0.632736 | -0.000329 | [-0.002946, +0.002784] | 0.503 | 108 | 0.630 | 21.82 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back` | 0.218582 | -0.002838 | 0.626623 | -0.006442 | [-0.005623, +0.000105] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+schedule_density` | 0.217759 | -0.003661 | 0.624614 | -0.008451 | [-0.007895, +0.000813] | 0.500 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+schedule_missingness` | 0.218548 | -0.002872 | 0.626539 | -0.006526 | [-0.005709, +0.000003] | 0.501 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+hot_cold+back_to_back+schedule_density` | 0.218819 | -0.002601 | 0.627184 | -0.005881 | [-0.006860, +0.001189] | 0.504 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+hot_cold+back_to_back+schedule_missingness` | 0.218908 | -0.002512 | 0.627424 | -0.005641 | [-0.005693, +0.000510] | 0.501 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+hot_cold+schedule_density+schedule_missingness` | 0.218836 | -0.002584 | 0.627222 | -0.005843 | [-0.006810, +0.001574] | 0.504 | 109 | 0.633 | 22.73 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back+schedule_density` | 0.220437 | -0.000983 | 0.630594 | -0.002471 | [-0.004455, +0.002618] | 0.502 | 109 | 0.633 | 22.73 | INCONCLUSIVE |
| `incumbent+rest_disparity+back_to_back+schedule_missingness` | 0.221195 | -0.000225 | 0.632523 | -0.000542 | [-0.000699, +0.000417] | 0.501 | 107 | 0.617 | 19.00 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+schedule_density+schedule_missingness` | 0.220428 | -0.000992 | 0.630568 | -0.002497 | [-0.004343, +0.002451] | 0.502 | 109 | 0.633 | 22.73 | REJECT_DEGENERATE |
| `incumbent+back_to_back+schedule_density+schedule_missingness` | 0.221383 | -0.000037 | 0.632876 | -0.000189 | [-0.002780, +0.002894] | 0.502 | 109 | 0.633 | 22.73 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back` | 0.218483 | -0.002937 | 0.626421 | -0.006644 | [-0.005707, +0.000086] | 0.500 | 110 | 0.627 | 21.73 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_density` | 0.217638 | -0.003782 | 0.624369 | -0.008696 | [-0.007820, +0.000726] | 0.500 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_missingness` | 0.218460 | -0.002960 | 0.626362 | -0.006703 | [-0.005616, -0.000173] | 0.500 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_density` | 0.218697 | -0.002723 | 0.626935 | -0.006130 | [-0.006864, +0.001613] | 0.505 | 107 | 0.636 | 22.82 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_missingness` | 0.218821 | -0.002599 | 0.627247 | -0.005818 | [-0.005652, +0.000330] | 0.502 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+schedule_density+schedule_missingness` | 0.218720 | -0.002700 | 0.626988 | -0.006077 | [-0.006865, +0.001333] | 0.505 | 107 | 0.636 | 22.82 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_density` | 0.220345 | -0.001075 | 0.630406 | -0.002659 | [-0.004474, +0.002543] | 0.501 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_missingness` | 0.221161 | -0.000259 | 0.632454 | -0.000611 | [-0.000729, +0.000430] | 0.501 | 107 | 0.617 | 19.00 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+schedule_density+schedule_missingness` | 0.220369 | -0.001051 | 0.630449 | -0.002616 | [-0.004460, +0.002591] | 0.501 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+consistency+back_to_back+schedule_density+schedule_missingness` | 0.221314 | -0.000106 | 0.632736 | -0.000329 | [-0.002979, +0.002786] | 0.503 | 108 | 0.630 | 21.82 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.217759 | -0.003661 | 0.624614 | -0.008451 | [-0.007652, +0.000811] | 0.500 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.218548 | -0.002872 | 0.626539 | -0.006526 | [-0.005597, -0.000023] | 0.501 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.217766 | -0.003654 | 0.624637 | -0.008428 | [-0.007738, +0.000797] | 0.500 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+hot_cold+back_to_back+schedule_density+schedule_missingness` | 0.218836 | -0.002584 | 0.627222 | -0.005843 | [-0.006690, +0.001356] | 0.504 | 109 | 0.633 | 22.73 | REJECT_DEGENERATE |
| `incumbent+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.220428 | -0.000992 | 0.630568 | -0.002497 | [-0.004227, +0.002601] | 0.502 | 109 | 0.633 | 22.73 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.217638 | -0.003782 | 0.624369 | -0.008696 | [-0.007846, +0.000774] | 0.500 | 110 | 0.636 | 23.64 | INCONCLUSIVE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.218460 | -0.002960 | 0.626362 | -0.006703 | [-0.005622, -0.000148] | 0.500 | 110 | 0.627 | 21.73 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.217659 | -0.003761 | 0.624419 | -0.008646 | [-0.007891, +0.000526] | 0.500 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+back_to_back+schedule_density+schedule_missingness` | 0.218720 | -0.002700 | 0.626988 | -0.006077 | [-0.006644, +0.001355] | 0.505 | 107 | 0.636 | 22.82 | REJECT_DEGENERATE |
| `incumbent+consistency+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.220369 | -0.001051 | 0.630449 | -0.002616 | [-0.004452, +0.002829] | 0.501 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.217766 | -0.003654 | 0.624637 | -0.008428 | [-0.007529, +0.000965] | 0.500 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |
| `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.217659 | -0.003761 | 0.624419 | -0.008646 | [-0.007896, +0.000631] | 0.500 | 110 | 0.636 | 23.64 | REJECT_DEGENERATE |

## Confidence-gate sweeps for incumbent and isolated additions

### `incumbent`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 143 | 0.650 | 34.55 | 110 | 0.627 | 21.73 |
| 0.525 | 118 | 0.669 | 32.82 | 90 | 0.678 | 26.45 |
| 0.550 | 96 | 0.667 | 26.18 | 76 | 0.697 | 25.18 |
| 0.575 | 78 | 0.679 | 23.18 | 60 | 0.683 | 18.27 |
| 0.600 | 52 | 0.788 | 26.27 | 55 | 0.673 | 15.64 |
| 0.625 | 37 | 0.784 | 18.36 | 46 | 0.739 | 18.91 |
| 0.650 | 22 | 0.909 | 16.18 | 36 | 0.833 | 21.27 |
| 0.675 | 15 | 0.933 | 11.73 | 30 | 0.833 | 17.73 |
| 0.700 | 9 | 0.889 | 6.27 | 19 | 0.842 | 11.55 |
| 0.725 | 4 | 1.000 | 3.64 | 7 | 0.857 | 4.45 |
| 0.750 | 1 | 1.000 | 0.91 | 2 | 1.000 | 1.82 |
| 0.775 | 0 | — | 0.00 | 1 | 1.000 | 0.91 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+consistency`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 143 | 0.650 | 34.55 | 110 | 0.627 | 21.73 |
| 0.525 | 119 | 0.672 | 33.73 | 90 | 0.678 | 26.45 |
| 0.550 | 96 | 0.667 | 26.18 | 76 | 0.697 | 25.18 |
| 0.575 | 78 | 0.679 | 23.18 | 60 | 0.683 | 18.27 |
| 0.600 | 53 | 0.774 | 25.27 | 55 | 0.673 | 15.64 |
| 0.625 | 36 | 0.778 | 17.45 | 47 | 0.723 | 17.91 |
| 0.650 | 22 | 0.909 | 16.18 | 36 | 0.833 | 21.27 |
| 0.675 | 15 | 0.933 | 11.73 | 29 | 0.828 | 16.82 |
| 0.700 | 9 | 0.889 | 6.27 | 19 | 0.842 | 11.55 |
| 0.725 | 4 | 1.000 | 3.64 | 7 | 0.857 | 4.45 |
| 0.750 | 1 | 1.000 | 0.91 | 2 | 1.000 | 1.82 |
| 0.775 | 0 | — | 0.00 | 1 | 1.000 | 0.91 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+hot_cold`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 143 | 0.650 | 34.55 | 110 | 0.636 | 23.64 |
| 0.525 | 123 | 0.659 | 31.64 | 93 | 0.656 | 23.45 |
| 0.550 | 101 | 0.683 | 30.73 | 80 | 0.700 | 26.91 |
| 0.575 | 79 | 0.684 | 24.09 | 64 | 0.703 | 21.91 |
| 0.600 | 59 | 0.746 | 25.00 | 56 | 0.696 | 18.45 |
| 0.625 | 40 | 0.800 | 21.09 | 43 | 0.767 | 20.00 |
| 0.650 | 28 | 0.821 | 15.91 | 38 | 0.842 | 23.09 |
| 0.675 | 19 | 0.895 | 13.45 | 32 | 0.844 | 19.55 |
| 0.700 | 10 | 0.900 | 7.18 | 22 | 0.864 | 14.27 |
| 0.725 | 5 | 1.000 | 4.55 | 13 | 0.846 | 8.00 |
| 0.750 | 3 | 1.000 | 2.73 | 6 | 0.833 | 3.55 |
| 0.775 | 1 | 1.000 | 0.91 | 2 | 1.000 | 1.82 |
| 0.800 | 0 | — | 0.00 | 1 | 1.000 | 0.91 |

### `incumbent+rest_disparity`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 143 | 0.657 | 36.45 | 110 | 0.627 | 21.73 |
| 0.525 | 120 | 0.667 | 32.73 | 90 | 0.678 | 26.45 |
| 0.550 | 96 | 0.667 | 26.18 | 76 | 0.697 | 25.18 |
| 0.575 | 77 | 0.675 | 22.27 | 63 | 0.683 | 19.09 |
| 0.600 | 58 | 0.741 | 24.09 | 53 | 0.679 | 15.73 |
| 0.625 | 36 | 0.806 | 19.36 | 44 | 0.750 | 19.00 |
| 0.650 | 23 | 0.913 | 17.09 | 36 | 0.833 | 21.27 |
| 0.675 | 15 | 0.933 | 11.73 | 29 | 0.828 | 16.82 |
| 0.700 | 9 | 0.889 | 6.27 | 19 | 0.842 | 11.55 |
| 0.725 | 4 | 1.000 | 3.64 | 8 | 0.875 | 5.36 |
| 0.750 | 1 | 1.000 | 0.91 | 3 | 1.000 | 2.73 |
| 0.775 | 0 | — | 0.00 | 2 | 1.000 | 1.82 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+back_to_back`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 143 | 0.650 | 34.55 | 110 | 0.627 | 21.73 |
| 0.525 | 118 | 0.669 | 32.82 | 90 | 0.678 | 26.45 |
| 0.550 | 96 | 0.667 | 26.18 | 76 | 0.697 | 25.18 |
| 0.575 | 78 | 0.679 | 23.18 | 60 | 0.683 | 18.27 |
| 0.600 | 52 | 0.788 | 26.27 | 55 | 0.673 | 15.64 |
| 0.625 | 37 | 0.784 | 18.36 | 46 | 0.739 | 18.91 |
| 0.650 | 22 | 0.909 | 16.18 | 36 | 0.833 | 21.27 |
| 0.675 | 15 | 0.933 | 11.73 | 30 | 0.833 | 17.73 |
| 0.700 | 9 | 0.889 | 6.27 | 19 | 0.842 | 11.55 |
| 0.725 | 4 | 1.000 | 3.64 | 7 | 0.857 | 4.45 |
| 0.750 | 1 | 1.000 | 0.91 | 2 | 1.000 | 1.82 |
| 0.775 | 0 | — | 0.00 | 1 | 1.000 | 0.91 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+schedule_density`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 143 | 0.657 | 36.45 | 110 | 0.636 | 23.64 |
| 0.525 | 126 | 0.667 | 34.36 | 89 | 0.674 | 25.55 |
| 0.550 | 100 | 0.660 | 26.00 | 79 | 0.671 | 22.18 |
| 0.575 | 76 | 0.697 | 25.18 | 65 | 0.677 | 19.00 |
| 0.600 | 56 | 0.750 | 24.18 | 57 | 0.702 | 19.36 |
| 0.625 | 40 | 0.775 | 19.18 | 44 | 0.750 | 19.00 |
| 0.650 | 23 | 0.870 | 15.18 | 35 | 0.829 | 20.36 |
| 0.675 | 14 | 1.000 | 12.73 | 27 | 0.852 | 16.91 |
| 0.700 | 10 | 1.000 | 9.09 | 20 | 0.900 | 14.36 |
| 0.725 | 5 | 1.000 | 4.55 | 8 | 0.875 | 5.36 |
| 0.750 | 1 | 1.000 | 0.91 | 1 | 1.000 | 0.91 |
| 0.775 | 0 | — | 0.00 | 0 | — | 0.00 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

### `incumbent+schedule_missingness`

| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |
|---:|---:|---:|---:|---:|---:|---:|
| 0.500 | 143 | 0.650 | 34.55 | 110 | 0.627 | 21.73 |
| 0.525 | 118 | 0.669 | 32.82 | 90 | 0.678 | 26.45 |
| 0.550 | 96 | 0.667 | 26.18 | 76 | 0.697 | 25.18 |
| 0.575 | 78 | 0.679 | 23.18 | 60 | 0.683 | 18.27 |
| 0.600 | 52 | 0.788 | 26.27 | 55 | 0.673 | 15.64 |
| 0.625 | 37 | 0.784 | 18.36 | 46 | 0.739 | 18.91 |
| 0.650 | 22 | 0.909 | 16.18 | 36 | 0.833 | 21.27 |
| 0.675 | 15 | 0.933 | 11.73 | 30 | 0.833 | 17.73 |
| 0.700 | 9 | 0.889 | 6.27 | 19 | 0.842 | 11.55 |
| 0.725 | 4 | 1.000 | 3.64 | 7 | 0.857 | 4.45 |
| 0.750 | 1 | 1.000 | 0.91 | 2 | 1.000 | 1.82 |
| 0.775 | 0 | — | 0.00 | 1 | 1.000 | 0.91 |
| 0.800 | 0 | — | 0.00 | 0 | — | 0.00 |

## Development ranking (not a promotion ranking)

| Rank | Variant | Holdout Brier | Δ Brier | Verdict |
|---:|---|---:|---:|---|
| 1 | `incumbent+consistency+hot_cold+rest_disparity+schedule_density` | 0.217638 | -0.003782 | INCONCLUSIVE |
| 2 | `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.217638 | -0.003782 | INCONCLUSIVE |
| 3 | `incumbent+consistency+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.217659 | -0.003761 | REJECT_DEGENERATE |
| 4 | `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.217659 | -0.003761 | REJECT_DEGENERATE |
| 5 | `incumbent+hot_cold+rest_disparity+schedule_density` | 0.217759 | -0.003661 | INCONCLUSIVE |
| 6 | `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density` | 0.217759 | -0.003661 | INCONCLUSIVE |
| 7 | `incumbent+hot_cold+rest_disparity+schedule_density+schedule_missingness` | 0.217766 | -0.003654 | REJECT_DEGENERATE |
| 8 | `incumbent+hot_cold+rest_disparity+back_to_back+schedule_density+schedule_missingness` | 0.217766 | -0.003654 | REJECT_DEGENERATE |
| 9 | `incumbent+consistency+hot_cold+rest_disparity+schedule_missingness` | 0.218460 | -0.002960 | REJECT_DEGENERATE |
| 10 | `incumbent+consistency+hot_cold+rest_disparity+back_to_back+schedule_missingness` | 0.218460 | -0.002960 | REJECT_DEGENERATE |

# Final decision rules

1. Reject additions that worsen both Brier and log loss with a clustered CI above zero.
2. Do not keep an addition merely because one confidence gate creates attractive flat units; that is threshold mining.
3. Keep only robust additions as candidates for a newly reserved prospective cohort.
4. Treat mixed or CI-over-zero results as inconclusive, not wins.
5. Do not activate any challenger until Vincent selects a named feature set and it passes a fresh forward test with timestamp-valid inputs and prices.

