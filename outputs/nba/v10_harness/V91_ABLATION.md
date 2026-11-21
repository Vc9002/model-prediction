# NBA v9.1 ablation (2026-11-20, exploratory)

Harness: `scripts/nba_v10/replay_harness.py` (PIT day-by-day replay, scored against the 2024-26
closing lines). Split: train < 2025-11-03 <= validation < 2026-02-23; the 2026-02-23+ holdout is
not replayed. Validation is 762 games, all regular season. Engine: `models/nba_v91.py`.
Configs: `v91_configs.json`. Bias = mean(actual - model). Brier is cover/over at the closing
line (coin flip 0.2500). corr = corr(model - line, actual - line).

## Rule and decisions

Keep a fix only if it does not worsen validation RMSE or Brier in the market it serves.

| Fix | Validation effect | Decision |
|---|---|---|
| parity (`v91_off`, all switches off) | identical to golden v9 on all 2,811 rows (0 changed) | pinned |
| B1 current-season sample for OT bump | total RMSE 19.423 -> 19.437, Brier 0.2689 -> 0.2691 alone; on top of B5 19.068 -> 19.074 | dropped (worse) |
| B5 rolling league baseline (W tuned on validation over 75/150/300) | total RMSE 19.423 -> 19.068, bias +3.34 -> +0.64, Brier 0.2689 -> 0.2658; spread unchanged | kept, W = 300 games, prior 30 games |
| B6 fitted symmetric fatigue (fit on train: b2b offense -2.22, defense +0.54; 3-in-4 offense -0.59, defense -0.12 pts per team) | on top of B5: total RMSE 19.068 -> 19.025, Brier 0.2658 -> 0.2656; spread RMSE 14.590 -> 14.613, Brier 0.2638 -> 0.2643 | kept for the total artifact only; spread keeps v9 fatigue |
| B7 playoff total offset (fit on train postseason, n = 166: -12.25) | no validation playoff games (untestable on validation); season-out check inside train: fit on 2024 playoffs, 2025 playoff total RMSE 21.28 -> 17.97 | kept in the total artifact, evidence is train-internal only |
| B4 market-blend sign | inert at weight 0 (no validation effect by construction); unit test pins weight 1 -> mean = line | fixed in engine, weight stays 0 |

Artifacts (research, not registered, not whitelisted): `config/models/research/nba-spread-margin-v9-1.json`
(B5) and `config/models/research/nba-total-margin-v9-1.json` (B5 + B6 + B7).

## Before / after (validation)

| Market | Engine | RMSE model | RMSE line | Bias | Brier | corr |
|---|---|---|---|---|---|---|
| spread | v9 | 14.591 | 13.97 | -0.27 | 0.2638 | -0.015 |
| spread | v9.1 | 14.590 | 13.97 | -0.27 | 0.2638 | -0.015 |
| total | v9 | 19.423 | 18.18 | +3.34 | 0.2689 | -0.018 |
| total | v9.1 | 19.025 | 18.18 | -0.34 | 0.2656 | -0.030 |

Totals bias by season (actual - model): v9 2023-24 -3.87, 2024-25 +1.81, 2025-26 +3.83;
v9.1 -1.49, +1.43, -0.01. Early 2025-26 months: v9 Oct +7.83, Nov +6.20, Dec +5.87; v9.1 +2.66,
+0.72, +1.29. The bias fixes are real; correlation with the line gap stays at zero, so they
remove a systematic loss (the unders) but do not create edge. Both still lose to the line on RMSE.

## All configs (train and validation)

| engine | split | n | spread RMSE | spread bias | spread Brier | spread corr | total RMSE | total bias | total Brier | total corr |
|---|---|---|---|---|---|---|---|---|---|---|
| v9 | train | 2049 | 14.488 | +0.40 | 0.2656 | -0.015 | 18.873 | +0.33 | 0.2687 | +0.011 |
| v9 | val | 762 | 14.591 | -0.27 | 0.2638 | -0.015 | 19.423 | +3.34 | 0.2689 | -0.018 |
| v91_off | train | 2049 | 14.488 | +0.40 | 0.2656 | -0.015 | 18.873 | +0.33 | 0.2687 | +0.011 |
| v91_off | val | 762 | 14.591 | -0.27 | 0.2638 | -0.015 | 19.423 | +3.34 | 0.2689 | -0.018 |
| v91_b1 | train | 2049 | 14.488 | +0.40 | 0.2656 | -0.015 | 18.891 | +0.39 | 0.2690 | +0.009 |
| v91_b1 | val | 762 | 14.591 | -0.27 | 0.2638 | -0.015 | 19.437 | +3.38 | 0.2691 | -0.019 |
| v91_b5_w75 | train | 2049 | 14.488 | +0.40 | 0.2656 | -0.015 | 18.755 | +0.47 | 0.2667 | +0.015 |
| v91_b5_w75 | val | 762 | 14.590 | -0.27 | 0.2638 | -0.015 | 19.188 | +1.82 | 0.2670 | -0.038 |
| v91_b5_w150 | train | 2049 | 14.488 | +0.40 | 0.2656 | -0.015 | 18.712 | +0.75 | 0.2664 | +0.014 |
| v91_b5_w150 | val | 762 | 14.590 | -0.27 | 0.2638 | -0.015 | 19.100 | +1.28 | 0.2654 | -0.035 |
| v91_b5_w300 | train | 2049 | 14.488 | +0.40 | 0.2656 | -0.015 | 18.712 | +0.57 | 0.2669 | +0.014 |
| v91_b5_w300 | val | 762 | 14.590 | -0.27 | 0.2638 | -0.015 | 19.068 | +0.64 | 0.2658 | -0.032 |
| v91_b6zero | train | 2049 | 14.504 | +0.44 | 0.2659 | -0.017 | 18.880 | -1.94 | 0.2684 | +0.008 |
| v91_b6zero | val | 762 | 14.601 | -0.23 | 0.2642 | -0.016 | 19.117 | +1.02 | 0.2654 | -0.012 |
| v91_b6fit | train | 2049 | 14.455 | +0.42 | 0.2648 | -0.010 | 18.822 | -0.71 | 0.2675 | +0.011 |
| v91_b6fit | val | 762 | 14.614 | -0.26 | 0.2643 | -0.028 | 19.233 | +2.29 | 0.2665 | -0.015 |
| v91_b7 | train | 2049 | 14.488 | +0.40 | 0.2656 | -0.015 | 18.508 | +1.38 | 0.2631 | +0.016 |
| v91_b7 | val | 762 | 14.591 | -0.27 | 0.2638 | -0.015 | 19.423 | +3.34 | 0.2689 | -0.018 |
| v91_c_b5b1 | train | 2049 | 14.488 | +0.40 | 0.2656 | -0.015 | 18.725 | +0.63 | 0.2671 | +0.012 |
| v91_c_b5b1 | val | 762 | 14.590 | -0.27 | 0.2638 | -0.015 | 19.074 | +0.68 | 0.2659 | -0.033 |
| v91_c_b5b6zero | train | 2049 | 14.504 | +0.44 | 0.2659 | -0.017 | 18.700 | -1.71 | 0.2663 | +0.010 |
| v91_c_b5b6zero | val | 762 | 14.600 | -0.23 | 0.2642 | -0.016 | 19.086 | -1.67 | 0.2675 | -0.026 |
| v91_c_b5b6fit | train | 2049 | 14.455 | +0.42 | 0.2648 | -0.010 | 18.652 | -0.41 | 0.2655 | +0.014 |
| v91_c_b5b6fit | val | 762 | 14.613 | -0.26 | 0.2643 | -0.028 | 19.025 | -0.34 | 0.2656 | -0.030 |
| v91_spread_final | train | 2049 | 14.488 | +0.40 | 0.2656 | -0.015 | 18.712 | +0.57 | 0.2669 | +0.014 |
| v91_spread_final | val | 762 | 14.590 | -0.27 | 0.2638 | -0.015 | 19.068 | +0.64 | 0.2658 | -0.032 |
| v91_total_final | train | 2049 | 14.455 | +0.42 | 0.2648 | -0.010 | 18.323 | +0.58 | 0.2603 | +0.020 |
| v91_total_final | val | 762 | 14.613 | -0.26 | 0.2643 | -0.028 | 19.025 | -0.34 | 0.2656 | -0.030 |
