# raw tables (exploratory)

Split on 572 ET dates: train 2024-01-29..2025-11-02, val 2025-11-03..<2026-02-23, holdout from 2026-02-23 (692 rows DROPPED, not scored).
- train: n=2049, with closing line n=2049
- val: n=762, with closing line n=762
## 1. Totals by season (regular season, train+val)

| season | n | actual | model | act-model | n_line | line | act-line | line-model | 2x league_baseline | trend pts sum |
|---|---|---|---|---|---|---|---|---|---|---|
| 2023-24 | 546 | 224.96 | 226.96 | -2.00 | 546 | 225.46 | -0.49 | -1.51 | 229.19 | 228.84 |
| 2024-25 | 1231 | 227.61 | 224.94 | 2.67 | 1231 | 226.87 | 0.74 | 1.93 | 226.74 | 226.68 |
| 2025-26 | 856 | 230.97 | 227.14 | 3.83 | 856 | 231.13 | -0.16 | 3.99 | 228.18 | 228.58 |

## 1b. Totals by month (all game types, train+val)

| month | split | n | actual | act-model | act-line | line-model | league_baseline/team |
|---|---|---|---|---|---|---|---|
| 2024-01 | train | 27 | 229.04 | -0.27 | -1.50 | 1.23 | 115.85 |
| 2024-02 | train | 174 | 227.91 | -1.57 | -3.28 | 1.71 | 115.61 |
| 2024-03 | train | 230 | 222.56 | -3.72 | 0.10 | -3.82 | 114.25 |
| 2024-04 | train | 157 | 220.39 | -4.34 | 1.12 | -5.46 | 113.44 |
| 2024-05 | train | 41 | 214.76 | -12.15 | 3.57 | -15.72 | 113.40 |
| 2024-06 | train | 5 | 200.80 | -28.09 | -12.50 | -15.59 | 113.40 |
| 2024-10 | train | 71 | 226.59 | 1.24 | 1.30 | -0.06 | 113.42 |
| 2024-11 | train | 222 | 225.59 | 1.14 | -0.13 | 1.26 | 113.36 |
| 2024-12 | train | 192 | 225.81 | 0.55 | 0.28 | 0.27 | 113.24 |
| 2025-01 | train | 227 | 226.69 | 2.25 | 0.21 | 2.04 | 113.19 |
| 2025-02 | train | 176 | 229.97 | 5.01 | 0.72 | 4.29 | 113.34 |
| 2025-03 | train | 238 | 229.82 | 4.57 | 1.71 | 2.87 | 113.55 |
| 2025-04 | train | 149 | 224.82 | -1.20 | 1.37 | -2.58 | 113.65 |
| 2025-05 | train | 39 | 221.54 | -5.84 | 4.29 | -10.14 | 113.65 |
| 2025-06 | train | 7 | 215.86 | -18.02 | -8.64 | -9.38 | 113.65 |
| 2025-10 | train | 80 | 234.88 | 7.83 | 3.26 | 4.57 | 113.74 |
| 2025-11 | train | 219 | 233.06 | 6.20 | 0.45 | 5.76 | 113.94 |
| 2025-12 | val | 198 | 233.64 | 5.87 | 0.38 | 5.49 | 114.15 |
| 2026-01 | val | 233 | 226.51 | -0.57 | -3.54 | 2.98 | 114.24 |
| 2026-02 | val | 126 | 228.93 | 2.11 | 2.04 | 0.06 | 114.20 |

## 1c. Engine league_baseline vs PIT season-to-date points/team (regular season rows)

| season | n | season-to-date ppg | engine baseline ppg | gap |
|---|---|---|---|---|
| 2023-24 | 546 | 114.73 | 114.59 | 0.14 |
| 2024-25 | 1195 | 113.19 | 113.37 | -0.18 |
| 2025-26 | 823 | 116.56 | 114.11 | 2.46 |

## 2. Home margin by season (regular season, train+val)

| season | n | actual home margin | model margin | act-model | line margin | act-line | home win% |
|---|---|---|---|---|---|---|---|
| 2023-24 | 546 | 1.63 | 1.46 | 0.17 | 1.80 | -0.17 | 0.51 |
| 2024-25 | 1231 | 1.62 | 1.56 | 0.06 | 1.89 | -0.27 | 0.54 |
| 2025-26 | 856 | 1.31 | 1.46 | -0.15 | 1.91 | -0.59 | 0.54 |

## 2b. Implied HCA by regression (train+val, with lines)

- OLS actual margin = a + b*(Elo diff in pts, no HCA): a(HCA)=1.73, b=1.02, n=2811
- Served model HCA = 0.70*(38 Elo/28) + 0.30*1.75 = 1.48 pts (before rest term)

## 3. Error by current-season games played (min of two teams), REGULAR SEASON 2024-25+, rows with lines

| cs games | n | RMSE margin model | RMSE margin line | |model-line| | corr(m-l, a-l) | RMSE total model | RMSE total line | total bias act-model |
|---|---|---|---|---|---|---|---|---|
| 0-5 | 162 | 12.98 | 12.95 | 3.29 | 0.15 | 19.87 | 19.08 | 4.81 |
| 6-10 | 151 | 13.13 | 12.14 | 2.95 | -0.13 | 18.58 | 17.10 | 3.38 |
| 11-20 | 306 | 13.14 | 12.91 | 2.91 | 0.07 | 19.99 | 18.85 | 3.59 |
| 21-40 | 606 | 14.63 | 14.05 | 3.37 | 0.01 | 18.74 | 17.51 | 2.08 |
| 41-99 | 862 | 14.94 | 14.20 | 3.43 | -0.03 | 18.89 | 17.76 | 3.38 |

## 4. Rest / back-to-back (REGULAR SEASON rows with lines)

| rest diff | n | act-line (market err) | model-line | act-model | SE |
|---|---|---|---|---|---|
| a: home rest <= away-1.5d | 112 | -0.46 | 0.08 | -0.54 | 1.41 |
| b: home -1d | 438 | -1.07 | 0.42 | -1.48 | 0.66 |
| c: equal | 1457 | -0.04 | -0.29 | 0.25 | 0.36 |
| d: home +1d | 493 | -0.64 | -1.26 | 0.62 | 0.60 |
| e: home >= away+1.5d | 133 | -0.35 | -0.83 | 0.48 | 1.14 |

| b2b | n | margin act-line | margin model-line | margin act-model | total act-line | total model-line | SE margin | SE total | line total | actual total | model total |
|---|---|---|---|---|---|---|---|---|---|---|---|
| neither | 1846 | -0.19 | -0.28 | 0.08 | 0.23 | -1.53 | 0.32 | 0.41 | 228.19 | 228.42 | 226.67 |
| home b2b | 307 | -1.29 | 1.06 | -2.35 | 1.21 | -2.93 | 0.79 | 1.05 | 227.63 | 228.84 | 224.71 |
| away b2b | 330 | -1.07 | -2.06 | 0.99 | 0.20 | -1.66 | 0.77 | 0.97 | 226.71 | 226.90 | 225.04 |
| both b2b | 150 | 1.11 | -0.69 | 1.80 | -2.34 | -4.65 | 1.18 | 1.50 | 228.53 | 226.19 | 223.88 |

## 5. Residual SD vs served sigma (rows with lines)

| split | n | SD margin resid (model) | SD margin resid (line) | SD total resid (model) | SD total resid (line) | SD(model-line) margin | SD(model-line) total |
|---|---|---|---|---|---|---|---|
| train | 2049 | 14.48 | 13.79 | 18.87 | 17.61 | 4.22 | 6.97 |
| val | 762 | 14.59 | 13.95 | 19.13 | 18.17 | 4.07 | 5.67 |

Served: margin_sd=14.0, total_sd=18.0

### 5b. Spread cover calibration (train) - picked side prob vs hit%

| model side prob | n | mean model % | hit % |
|---|---|---|---|
| 0.500-0.525 | 310 | 51.19 | 54.19 |
| 0.525-0.550 | 315 | 53.81 | 50.79 |
| 0.550-0.600 | 552 | 57.50 | 52.54 |
| 0.600-0.650 | 477 | 62.41 | 50.94 |
| 0.650-1.000 | 394 | 69.85 | 44.92 |

### 5c. Total over/under calibration (train)

| model side prob | n | mean model % | hit % |
|---|---|---|---|
| 0.500-0.525 | 269 | 51.27 | 51.67 |
| 0.525-0.550 | 269 | 53.76 | 52.04 |
| 0.550-0.600 | 503 | 57.38 | 49.90 |
| 0.600-0.650 | 399 | 62.30 | 47.12 |
| 0.650-1.000 | 609 | 72.32 | 51.89 |

- train p_away_cover: n=2048, Brier model=0.2656 vs coin-flip 0.2500; hit% at model side=50.7
- train p_over: n=2049, Brier model=0.2687 vs coin-flip 0.2500; hit% at model side=50.5

### 5b. Spread cover calibration (val) - picked side prob vs hit%

| model side prob | n | mean model % | hit % |
|---|---|---|---|
| 0.500-0.525 | 122 | 51.24 | 50.00 |
| 0.525-0.550 | 116 | 53.76 | 46.55 |
| 0.550-0.600 | 213 | 57.39 | 46.48 |
| 0.600-0.650 | 181 | 62.59 | 50.83 |
| 0.650-1.000 | 123 | 70.02 | 49.59 |

### 5c. Total over/under calibration (val)

| model side prob | n | mean model % | hit % |
|---|---|---|---|
| 0.500-0.525 | 84 | 51.21 | 40.48 |
| 0.525-0.550 | 101 | 53.79 | 39.60 |
| 0.550-0.600 | 160 | 57.78 | 46.88 |
| 0.600-0.650 | 154 | 62.34 | 51.30 |
| 0.650-1.000 | 258 | 70.97 | 51.94 |

- val p_away_cover: n=755, Brier model=0.2638 vs coin-flip 0.2500; hit% at model side=48.6
- val p_over: n=757, Brier model=0.2689 vs coin-flip 0.2500; hit% at model side=47.8

## 6. Does the model add information beyond the closing line?

| market | split | n | corr(model-line, actual-line) | RMSE model | RMSE line |
|---|---|---|---|---|---|
| margin | train | 2049 | -0.02 | 14.49 | 13.79 |
| margin | val | 762 | -0.02 | 14.59 | 13.97 |
| total | train | 2049 | 0.01 | 18.87 | 17.62 |
| total | val | 762 | -0.02 | 19.42 | 18.18 |

### 6b. Shrink-to-market: pred = line + lam*(model - line); lam fit on train (OLS), applied to val

| market | lam (train, no const) | const (train) | lam w/ const (train) | lam argmin on VAL | val RMSE line | val RMSE blend(train lam) | val RMSE model |
|---|---|---|---|---|---|---|---|
| margin | -0.05 | -0.07 | -0.05 | -0.03 | 13.97 | 13.97 | 14.59 |
| total | 0.03 | 0.48 | 0.03 | 0.01 | 18.18 | 18.18 | 19.42 |

### 6c. Moneyline: model win prob vs line-implied win prob (Phi(line/sigma), sigma = train SD of actual-line)

| split | n | Brier line-implied | Brier ML v5 (served) | Brier v9 coherent ML | Brier 50/50 line+v5 | corr(v5-line, y-line) |
|---|---|---|---|---|---|---|
| train | 2049 | 0.20 | 0.22 | 0.22 | 0.21 | -0.05 |
| val | 762 | 0.21 | 0.22 | 0.22 | 0.21 | 0.00 |

(sigma from train = 13.79)

- logit-blend weight on v5 minimizing Brier: train w=0.00, val w=0.00

## 7. Leak / integrity signals

- train: straight-up 'spread at line 0' hit 64.5%, v9 ML Brier 0.2173 (n=2049)
- val: straight-up 'spread at line 0' hit 63.6%, v9 ML Brier 0.2242 (n=762)
- artifact claim: spread holdout_brier_score 0.1948; total 0.2235 (see 5b/5c for real closing-line Brier)
- rows where engine current_season_games_min (lifetime games_played) exceeds true current-season min by >5: 2163/2811

## 8a. By season type (train+val, rows with lines)

| type | n | total act-model | total act-line | total line-model | RMSE margin model | RMSE margin line |
|---|---|---|---|---|---|---|
| regular-season | 2633 | 2.08 | 0.19 | 1.89 | 14.38 | 13.70 |
| play-in-season | 12 | -8.50 | 0.67 | -9.16 | 14.98 | 14.64 |
| post-season | 166 | -12.98 | 0.19 | -13.16 | 16.54 | 15.86 |

## 8b. Margin decomposition by b2b (mean pts; fatigue+rest = exp_margin - 0.7*elo - 0.3*(trend_raw+1.75))

| b2b | n | 0.7*elo | 0.3*trend | fatigue+rest | line | actual |
|---|---|---|---|---|---|---|
| neither | 1846 | 0.99 | 0.52 | 0.08 | 1.87 | 1.68 |
| home b2b | 307 | 0.84 | 0.50 | -0.55 | -0.26 | -1.55 |
| away b2b | 330 | 0.79 | 0.46 | 0.50 | 3.81 | 2.73 |
| both b2b | 150 | 1.01 | 0.54 | -0.15 | 2.09 | 3.20 |

## 8c. Totals with a PIT season-to-date drift correction (exp_total + 2*(stdppg - baseline)), regular season

| split | n | bias raw | bias corrected | RMSE raw | RMSE corrected | RMSE line | corr(corr-line, act-line) |
|---|---|---|---|---|---|---|---|
| train | 1802 | 1.44 | 1.28 | 18.68 | 18.69 | 17.67 | -0.00 |
| val | 762 | 3.34 | -1.25 | 19.42 | 19.10 | 18.18 | -0.02 |

## 8d. Brier detail (4dp)

- train line-implied: Brier 0.2007 (n=2049)
- train ML v5: Brier 0.2168 (n=2049)
- train v9 coherent: Brier 0.2173 (n=2049)
- val line-implied: Brier 0.2122 (n=762)
- val ML v5: Brier 0.2232 (n=762)
- val v9 coherent: Brier 0.2242 (n=762)
- train cover Brier with sigma=14: 0.2656
- train cover Brier with sigma=20: 0.2585
- train cover Brier with sigma=30: 0.2542
- train cover Brier with sigma=60: 0.2513
- val cover Brier with sigma=14: 0.2638
- val cover Brier with sigma=20: 0.2573
- val cover Brier with sigma=30: 0.2535
- val cover Brier with sigma=60: 0.2510

## 8e. Parity check: exp_margin - [0.7*elo + 0.3*(trend_raw+1.75) + 0.15*rest_diff], rows with no b2b

residual (0.1 pt) counts; expected only {0, -0.24 (home 3in4), +0.36 (away 3in4), +0.12 (both)} +/- rounding:
[(-0.2, 294), (0.0, 480), (0.1, 955), (0.4, 295)]
