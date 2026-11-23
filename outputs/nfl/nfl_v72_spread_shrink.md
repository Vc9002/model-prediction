# nfl-spread-structural-v7.2: cover probability shrink fitted on validation

Operator-approved protocol exception (2026-11-23): the train-window fit clipped at 1.0 (scripts/fit_nfl_v71_shrink.py), so the slope is fitted on the validation window and the locked holdout is reported once. The holdout was read before (2026-11-12 replay, v7.1 fit): design-informed.

Fitted s = 0.378 (MLE 0.3781, SE 1.0780, n 282).

| window | dates | n | Brier raw -> shrunk | log loss raw -> shrunk | calibration slope raw -> shrunk |
|---|---|---|---|---|---|
| train | 2021-09-09 to 2024-09-26 | 874 | 0.2492 -> 0.2496 | 0.6916 -> 0.6922 | 1.072 -> 2.835 |
| validation | 2024-09-29 to 2025-09-25 | 282 | 0.2502 -> 0.2499 | 0.6935 -> 0.6929 | 0.478 -> 1.265 |
| locked_holdout | 2025-09-28 to 2026-10-08 | 295 | 0.2502 -> 0.2499 | 0.6937 -> 0.6930 | 0.379 -> 1.002 |

Main-rule clears per season (raw -> shrunk):

- 2021: 3 -> 0 of 285 games
- 2022: 2 -> 0 of 284 games
- 2023: 1 -> 0 of 285 games
- 2024: 4 -> 0 of 285 games
- 2025: 3 -> 0 of 285 games
- 2026: 1 -> 0 of 65 games
