# Structural residual on the Polymarket ladder, every settled NBA game

## Validation (history)

```
{
 "spread": {
  "train_n": 19115,
  "val_n": 3690,
  "val_corr": 0.0359,
  "val_ci": [
   0.0038,
   0.0697
  ],
  "val_pred_sd": 0.641,
  "pass": true,
  "coef_std": {
   "rest_h": -0.139,
   "rest_a": -0.0,
   "b2b_h": -0.063,
   "b2b_a": 0.088,
   "three_in_four_h": -0.066,
   "three_in_four_a": 0.121,
   "gp_h": 0.121,
   "gp_a": -0.61,
   "early": -0.572,
   "spread": -0.56,
   "big_spread": -0.042,
   "home_dog": 0.956
  }
 },
 "total": {
  "train_n": 19115,
  "val_n": 3690,
  "val_corr": -0.0002,
  "val_ci": [
   -0.0327,
   0.0323
  ],
  "val_pred_sd": 0.68,
  "pass": false,
  "coef_std": {
   "rest_h": 0.2,
   "rest_a": 0.422,
   "b2b_h": 0.136,
   "b2b_a": 0.524,
   "three_in_four_h": -0.169,
   "three_in_four_a": -0.002,
   "gp_h": -1.954,
   "gp_a": 2.163,
   "early": -0.227,
   "spread": 0.112,
   "big_spread": 0.115,
   "home_dog": -0.088,
   "total_line": -0.13
  }
 }
}
```

```
{
 "games": 219,
 "no_polymarket_or_schedule": 23,
 "evaluated": 151,
 "no_ladder_mean": 11,
 "no_quote_in_window": 45
}
```

## Picks (gap >= 3.0 points, validated markets only)

| slice | picks | W-L | PnL gross (ROI) | PnL net (ROI) | 95% CI ROI (games) |
|---|---|---|---|---|---|
| ALL | 40 | 18-22 | -4.37 (-10.9%) | -5.40 (-13.5%) | -40.8% .. +19.2% |
| spread | 40 | 18-22 | -4.37 (-10.9%) | -5.40 (-13.5%) | -40.8% .. +19.2% |
| total | 0 | - | - | - | - |

### Ignoring the validation gate (context)

| slice | picks | W-L | PnL gross (ROI) | PnL net (ROI) | 95% CI ROI (games) |
|---|---|---|---|---|---|
| spread | 40 | 18-22 | -4.37 (-10.9%) | -5.40 (-13.5%) | -40.8% .. +19.2% |
| total | 13 | 7-6 | +1.13 (+8.7%) | +0.71 (+5.5%) | -39.4% .. +57.4% |

### By week, picks ignoring the validation gate

| week | picks | PnL gross | PnL net |
|---|---|---|---|
| 2026-10-26 | 13 | -1.07 | -1.42 |
| 2026-11-02 | 10 | -4.11 | -4.28 |
| 2026-11-09 | 14 | +1.97 | +1.51 |
| 2026-11-16 | 16 | -0.03 | -0.50 |
