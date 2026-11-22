## 0. Population
NBA ledger rows: 3442 (settled 675, superseded 2732, open 35; voided/archived: 0 in ledger_records).
Settled after dedupe on (event, selection, line, model), Main preferred: 512 picks, 205 games.
 quoted   regime   n
  False     FLAT  15
  False     QUAL  14
  False RESEARCH   3
   True     FLAT 174
   True     QUAL 282
   True RESEARCH  24

## 1. Record / calibration by model (quoted rows; all regimes)
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ML | elo-trend-lr-v4 | 78 | 78 | 52 | 26 | 66.7% | 56%-76% | 65.8% | 64.9% | 64.7% | -2.88 | -3.4% | -0.6% |
| ML | elo-trend-lr-v5 | 49 | 49 | 30 | 19 | 61.2% | 47%-74% | 70.2% | 69.2% | 68.7% | -3.85 | -7.9% | -7.9% |
| ML | moneyline-v9 (unproven) | 11 | 11 | 10 | 1 | 90.9% | 62%-98% | 62.8% | 73.8% | 73.3% | +3.12 | 28.4% | 28.4% |
| spread | spread-margin-v2 | 21 | 21 | 8 | 13 | 38.1% | 21%-59% | 58.8% | 50.9% | 50.2% | -5.21 | -18.1% | -25.4% |
| spread | spread-margin-v4 (unproven) | 8 | 8 | 5 | 3 | 62.5% | 31%-86% | 61.0% | 50.7% | 50.3% | +2.59 | 20.3% | 23.6% |
| spread | spread-margin-v5 | 31 | 31 | 10 | 21 | 32.3% | 19%-50% | 60.9% | 50.6% | 49.9% | -14.39 | -29.9% | -36.5% |
| spread | spread-margin-v7 | 25 | 25 | 15 | 10 | 60.0% | 41%-77% | 62.1% | 52.0% | 51.5% | +5.08 | 13.7% | 16.0% |
| spread | spread-margin-v9 | 85 | 85 | 43 | 42 | 50.6% | 40%-61% | 59.1% | 50.6% | 50.1% | +0.42 | 0.3% | -0.0% |
| total | total-margin-v2 | 22 | 22 | 10 | 12 | 45.5% | 27%-65% | 60.7% | 52.9% | 51.4% | -1.05 | -3.6% | -15.3% |
| total | total-margin-v4 (unproven) | 8 | 8 | 3 | 5 | 37.5% | 14%-69% | 58.2% | 50.5% | 49.9% | -3.04 | -26.8% | -25.5% |
| total | total-margin-v5 | 33 | 33 | 16 | 17 | 48.5% | 33%-65% | 65.7% | 52.0% | 51.0% | -1.90 | -3.6% | -5.4% |
| total | total-margin-v7 | 24 | 24 | 11 | 13 | 45.8% | 28%-65% | 56.5% | 50.5% | 50.0% | -3.66 | -11.2% | -9.3% |
| total | total-margin-v9 | 85 | 85 | 39 | 46 | 45.9% | 36%-56% | 54.4% | 50.7% | 50.1% | -9.10 | -8.3% | -9.4% |

### 1b. Same, split by regime (QUAL = edge-qualified call, FLAT = flat every-game call)
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ML | FLAT | 110 | 110 | 75 | 35 | 68.2% | 59%-76% | 67.9% | 68.8% | 68.7% | +0.63 | 0.6% | 0.2% |
| ML | QUAL (unproven) | 8 | 8 | 3 | 5 | 37.5% | 14%-69% | 62.0% | 57.5% | 57.0% | -2.77 | -34.7% | -34.7% |
| ML | RESEARCH | 20 | 20 | 14 | 6 | 70.0% | 48%-85% | 65.2% | 61.9% | 61.3% | -1.47 | -5.9% | 6.8% |
| spread | FLAT | 23 | 23 | 8 | 15 | 34.8% | 19%-55% | 59.9% | 52.2% | 51.6% | -13.59 | -39.2% | -35.0% |
| spread | QUAL | 145 | 145 | 73 | 72 | 50.3% | 42%-58% | 60.0% | 50.6% | 50.1% | +4.59 | 2.1% | -0.5% |
| spread | RESEARCH (unproven) | 2 | 2 | 0 | 2 | 0.0% | 0%-66% | 56.4% | 53.5% | 51.2% | -2.50 | -100.0% | -100.0% |
| total | FLAT | 41 | 41 | 18 | 23 | 43.9% | 30%-59% | 56.7% | 52.3% | 51.1% | -8.38 | -14.9% | -15.5% |
| total | QUAL | 129 | 129 | 59 | 70 | 45.7% | 37%-54% | 57.8% | 50.5% | 50.0% | -12.38 | -7.0% | -9.4% |
| total | RESEARCH (unproven) | 2 | 2 | 2 | 0 | 100.0% | 34%-100% | 82.6% | 74.5% | 62.3% | +2.01 | 67.0% | 50.5% |

### 1c. Unquoted rows (no executable Polymarket quote at decision; excluded from headline)
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ML | elo-trend-lr-v4 | 17 | 17 | 12 | 5 | 70.6% | 47%-87% | 63.3% | 52.4% |  | +4.68 | 26.4% | 31.3% |
| ML | moneyline-v7 | 15 | 15 | 10 | 5 | 66.7% | 42%-85% | 65.9% | 60.2% |  | +1.24 | 8.3% | 8.3% |

### 1d. Brier / log loss: model vs market on the same picks (quoted)
Market = decision ask (biased high by half-spread) and decision midpoint from snapshot. Conditional on model-selected picks. Diff CI = game-clustered bootstrap of Brier(model)-Brier(mid).
| mkt | n | games | b_model | b_ask | b_mid | ll_model | ll_mid | diff |
|---|---|---|---|---|---|---|---|---|
| ML all | 133 | 133 | 0.2126 | 0.1950 | 0.1950 | 0.6145 | 0.5719 | +0.0176 [+0.0006,+0.0338] |
| ML QUAL (unproven) | 8 | 8 | 0.2807 | 0.2693 | 0.2673 | 0.7531 | 0.7269 | +0.0134 [-0.0190,+0.0463] |
| spread all | 163 | 163 | 0.2630 | 0.2487 | 0.2482 | 0.7207 | 0.6887 | +0.0148 [-0.0019,+0.0335] |
| spread QUAL | 139 | 139 | 0.2581 | 0.2498 | 0.2497 | 0.7113 | 0.6926 | +0.0083 [-0.0106,+0.0284] |
| total all | 165 | 165 | 0.2565 | 0.2509 | 0.2499 | 0.7071 | 0.6928 | +0.0067 [-0.0068,+0.0214] |
| total QUAL | 124 | 124 | 0.2606 | 0.2514 | 0.2508 | 0.7150 | 0.6947 | +0.0098 [-0.0062,+0.0250] |
| ALL all | 461 | 170 | 0.2461 | 0.2340 | 0.2334 | 0.6852 | 0.6564 | +0.0127 [+0.0026,+0.0221] |
| ALL QUAL | 271 | 151 | 0.2599 | 0.2511 | 0.2507 | 0.7142 | 0.6946 | +0.0092 [-0.0032,+0.0212] |

## 2. Edge buckets (quoted)
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ML | <0 | 67 | 67 | 56 | 11 | 83.6% | 73%-91% | 68.4% | 77.2% | 76.8% | +6.09 | 9.1% | 9.1% |
| ML | 0-2 | 16 | 16 | 9 | 7 | 56.2% | 33%-77% | 67.8% | 66.6% | 65.8% | -2.25 | -14.1% | -14.1% |
| ML | 2-5 | 15 | 15 | 7 | 8 | 46.7% | 25%-70% | 66.1% | 62.6% | 62.2% | -3.57 | -23.8% | -23.8% |
| ML | 5-8 (unproven) | 9 | 9 | 8 | 1 | 88.9% | 56%-98% | 66.9% | 60.2% | 59.7% | +4.29 | 47.6% | 47.6% |
| ML | 8-12 (unproven) | 14 | 14 | 4 | 10 | 28.6% | 12%-55% | 65.4% | 55.0% | 54.9% | -7.45 | -50.5% | -47.8% |
| ML | 12+ | 17 | 17 | 8 | 9 | 47.1% | 26%-69% | 64.2% | 45.8% | 45.6% | -0.72 | -3.2% | 5.3% |
| spread | <0 (unproven) | 8 | 8 | 1 | 7 | 12.5% | 2%-47% | 55.8% | 57.9% | 57.7% | -6.94 | -86.7% | -86.7% |
| spread | 0-2 | 22 | 22 | 13 | 9 | 59.1% | 39%-77% | 51.8% | 50.9% | 50.4% | +3.23 | 13.7% | 15.8% |
| spread | 2-5 | 31 | 31 | 13 | 18 | 41.9% | 26%-59% | 53.6% | 49.9% | 49.4% | -5.84 | -13.7% | -16.7% |
| spread | 5-8 | 25 | 25 | 13 | 12 | 52.0% | 33%-70% | 57.4% | 51.0% | 50.5% | +0.49 | 1.6% | 1.6% |
| spread | 8-12 | 26 | 26 | 11 | 15 | 42.3% | 26%-61% | 60.0% | 50.4% | 49.9% | -6.69 | -17.7% | -16.5% |
| spread | 12+ | 58 | 58 | 30 | 28 | 51.7% | 39%-64% | 68.0% | 50.5% | 49.9% | +4.24 | 3.9% | 3.0% |
| total | <0 | 18 | 18 | 7 | 11 | 38.9% | 20%-61% | 46.7% | 53.6% | 51.7% | -5.22 | -29.0% | -29.0% |
| total | 0-2 | 25 | 25 | 11 | 14 | 44.0% | 27%-63% | 52.5% | 51.5% | 50.7% | -3.53 | -13.1% | -15.0% |
| total | 2-5 | 40 | 40 | 15 | 25 | 37.5% | 24%-53% | 54.7% | 51.4% | 50.5% | -12.47 | -23.4% | -26.2% |
| total | 5-8 | 27 | 27 | 17 | 10 | 63.0% | 44%-78% | 56.6% | 50.3% | 49.7% | +8.43 | 25.0% | 25.0% |
| total | 8-12 | 31 | 31 | 13 | 18 | 41.9% | 26%-59% | 60.5% | 50.7% | 50.2% | -7.80 | -17.6% | -17.3% |
| total | 12+ | 31 | 31 | 16 | 15 | 51.6% | 35%-68% | 71.1% | 50.7% | 50.0% | +1.83 | 3.1% | 2.1% |

### 2b. Edge buckets, v9 only (spread+total)
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ML | <0 (unproven) | 8 | 8 | 8 | 0 | 100.0% | 68%-100% | 59.0% | 78.7% | 78.2% | +2.26 | 28.3% | 28.3% |
| ML | 2-5 (unproven) | 1 | 1 | 0 | 1 | 0.0% | 0%-79% | 79.8% | 76.0% | 75.5% | -1.00 | -100.0% | -100.0% |
| ML | 12+ (unproven) | 2 | 2 | 2 | 0 | 100.0% | 34%-100% | 69.4% | 53.0% | 52.5% | +1.86 | 93.0% | 93.0% |
| spread | <0 (unproven) | 4 | 4 | 0 | 4 | 0.0% | 0%-49% | 51.5% | 52.3% | 51.7% | -4.00 | -100.0% | -100.0% |
| spread | 0-2 (unproven) | 14 | 14 | 8 | 6 | 57.1% | 33%-79% | 51.8% | 50.8% | 50.3% | +1.91 | 12.6% | 12.4% |
| spread | 2-5 | 17 | 17 | 9 | 8 | 52.9% | 31%-74% | 53.6% | 49.9% | 49.4% | +1.17 | 4.7% | 5.2% |
| spread | 5-8 (unproven) | 11 | 11 | 8 | 3 | 72.7% | 43%-90% | 58.1% | 51.5% | 51.0% | +5.68 | 41.3% | 41.3% |
| spread | 8-12 | 15 | 15 | 6 | 9 | 40.0% | 20%-64% | 60.0% | 50.5% | 50.0% | -5.02 | -23.1% | -21.3% |
| spread | 12+ | 24 | 24 | 12 | 12 | 50.0% | 31%-69% | 68.4% | 50.4% | 49.9% | +0.69 | 1.5% | -0.1% |
| total | <0 | 15 | 15 | 4 | 11 | 26.7% | 11%-52% | 43.1% | 51.0% | 50.4% | -7.15 | -47.7% | -47.7% |
| total | 0-2 | 15 | 15 | 7 | 8 | 46.7% | 25%-70% | 53.1% | 52.1% | 51.1% | -1.66 | -10.2% | -11.5% |
| total | 2-5 | 16 | 16 | 7 | 9 | 43.8% | 23%-67% | 53.8% | 50.4% | 49.9% | -2.87 | -12.9% | -13.4% |
| total | 5-8 | 16 | 16 | 11 | 5 | 68.8% | 44%-86% | 56.3% | 50.1% | 49.6% | +7.47 | 37.3% | 37.3% |
| total | 8-12 | 16 | 16 | 7 | 9 | 43.8% | 23%-67% | 60.3% | 50.6% | 50.1% | -3.35 | -14.7% | -13.0% |
| total | 12+ (unproven) | 7 | 7 | 3 | 4 | 42.9% | 16%-75% | 64.7% | 49.7% | 49.2% | -1.53 | -12.0% | -12.6% |

- ML: corr(edge, win - ask) over edge>0 picks = +0.164 (n=71)
- spread: corr(edge, win - ask) over edge>0 picks = +0.008 (n=162)
- total: corr(edge, win - ask) over edge>0 picks = +0.094 (n=154)

## 3. Sides (quoted)
### Home/away (spread, ML)
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ML | away | 64 | 64 | 45 | 19 | 70.3% | 58%-80% | 67.0% | 64.5% | 64.2% | +3.32 | 5.0% | 6.9% |
| ML | home | 74 | 74 | 47 | 27 | 63.5% | 52%-74% | 67.2% | 69.4% | 69.3% | -6.93 | -9.0% | -7.6% |
| spread | away | 82 | 82 | 42 | 40 | 51.2% | 41%-62% | 60.1% | 50.7% | 50.2% | +3.97 | 3.2% | 0.8% |
| spread | home | 88 | 88 | 39 | 49 | 44.3% | 34%-55% | 59.8% | 51.0% | 50.4% | -15.48 | -12.0% | -13.0% |

### Favorite/dog (spread by line sign, ML by price)
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ML | dog | 17 | 17 | 6 | 11 | 35.3% | 17%-59% | 57.9% | 41.9% | 41.4% | -4.42 | -20.8% | -11.0% |
| ML | fav | 121 | 121 | 86 | 35 | 71.1% | 62%-78% | 68.4% | 70.7% | 70.4% | +0.81 | 0.7% | 0.5% |
| spread | dog | 105 | 105 | 52 | 53 | 49.5% | 40%-59% | 60.6% | 51.0% | 50.5% | -1.22 | -0.8% | -3.2% |
| spread | fav | 65 | 65 | 29 | 36 | 44.6% | 33%-57% | 58.9% | 50.6% | 50.0% | -10.28 | -10.6% | -11.5% |

### Over/under
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| total | over | 46 | 46 | 18 | 28 | 39.1% | 26%-54% | 54.8% | 50.1% | 49.6% | -10.56 | -17.8% | -21.5% |
| total | under | 126 | 126 | 61 | 65 | 48.4% | 40%-57% | 59.0% | 51.6% | 50.7% | -8.19 | -4.7% | -6.0% |

### Spread |line| bands
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| spread | |L|<=3.5 | 53 | 53 | 29 | 24 | 54.7% | 41%-67% | 60.4% | 50.5% | 49.9% | +11.03 | 13.9% | 8.0% |
| spread | 4-6.5 | 44 | 44 | 19 | 25 | 43.2% | 30%-58% | 58.3% | 50.8% | 50.2% | -9.48 | -15.3% | -14.6% |
| spread | 7-9.5 | 37 | 37 | 15 | 22 | 40.5% | 26%-57% | 60.5% | 50.6% | 50.0% | -10.44 | -18.4% | -19.9% |
| spread | 10+ | 36 | 36 | 18 | 18 | 50.0% | 34%-66% | 60.7% | 51.7% | 51.2% | -2.61 | -4.9% | -3.4% |

### Spread fav/dog x band
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| spread | dog 10+ | 23 | 23 | 13 | 10 | 56.5% | 37%-74% | 62.7% | 52.5% | 52.0% | +1.99 | 5.7% | 7.8% |
| spread | dog 4-6.5 | 25 | 25 | 10 | 15 | 40.0% | 23%-59% | 58.5% | 50.6% | 50.1% | -8.05 | -22.9% | -20.9% |
| spread | dog 7-9.5 | 24 | 24 | 11 | 13 | 45.8% | 28%-65% | 60.2% | 50.5% | 49.9% | -1.15 | -3.2% | -9.7% |
| spread | dog |L|<=3.5 | 33 | 33 | 18 | 15 | 54.5% | 38%-70% | 61.0% | 50.6% | 50.0% | +5.97 | 12.3% | 7.4% |
| spread | fav 10+ (unproven) | 13 | 13 | 5 | 8 | 38.5% | 18%-64% | 57.1% | 50.4% | 49.9% | -4.61 | -24.6% | -23.4% |
| spread | fav 4-6.5 | 19 | 19 | 9 | 10 | 47.4% | 27%-68% | 58.0% | 50.9% | 50.3% | -1.43 | -5.4% | -6.4% |
| spread | fav 7-9.5 (unproven) | 13 | 13 | 4 | 9 | 30.8% | 13%-58% | 60.8% | 50.7% | 50.2% | -9.29 | -45.1% | -38.8% |
| spread | fav |L|<=3.5 | 20 | 20 | 11 | 9 | 55.0% | 34%-74% | 59.5% | 50.3% | 49.8% | +5.06 | 16.4% | 9.1% |

### Total line bands
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| total | <215 (unproven) | 6 | 6 | 4 | 2 | 66.7% | 30%-90% | 56.6% | 50.5% | 50.0% | +2.61 | 33.7% | 31.5% |
| total | 215-224.5 | 48 | 48 | 22 | 26 | 45.8% | 33%-60% | 55.8% | 50.5% | 49.9% | -4.04 | -6.2% | -8.9% |
| total | 225-234.5 | 76 | 76 | 35 | 41 | 46.1% | 35%-57% | 57.8% | 50.9% | 50.2% | -9.21 | -8.8% | -9.8% |
| total | 235+ | 42 | 42 | 18 | 24 | 42.9% | 29%-58% | 60.4% | 52.8% | 51.4% | -8.10 | -14.1% | -18.2% |

### Total over/under x band
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| total | over 215-224.5 | 21 | 21 | 8 | 13 | 38.1% | 21%-59% | 54.8% | 50.0% | 49.5% | -6.62 | -23.7% | -22.9% |
| total | over 225-234.5 (unproven) | 14 | 14 | 7 | 7 | 50.0% | 27%-73% | 54.0% | 49.9% | 49.4% | +1.66 | 9.2% | 0.0% |
| total | over 235+ (unproven) | 6 | 6 | 0 | 6 | 0.0% | 0%-39% | 55.4% | 50.5% | 49.9% | -7.00 | -100.0% | -100.0% |
| total | over <215 (unproven) | 5 | 5 | 3 | 2 | 60.0% | 23%-88% | 56.1% | 50.4% | 49.9% | +1.41 | 21.7% | 18.5% |
| total | under 215-224.5 | 27 | 27 | 14 | 13 | 51.9% | 34%-69% | 56.7% | 50.8% | 50.3% | +2.58 | 7.0% | 2.0% |
| total | under 225-234.5 | 62 | 62 | 28 | 34 | 45.2% | 33%-57% | 58.7% | 51.1% | 50.4% | -10.87 | -12.5% | -12.0% |
| total | under 235+ | 36 | 36 | 18 | 18 | 50.0% | 34%-66% | 61.3% | 53.2% | 51.6% | -1.10 | -2.2% | -4.5% |
| total | under <215 (unproven) | 1 | 1 | 1 | 0 | 100.0% | 21%-100% | 58.9% | 51.0% | 50.5% | +1.20 | 96.2% | 96.2% |

## 4. Closing line value (quoted, snapshot-recomputed)
Rows with decision+last-pre-tip snapshot: 461 of 480 quoted. Strictly later capture than decision: 215.
Median gap last capture -> tip: 65 min (IQR 24-135).
Decision capture == last capture (CLV 0 by construction): 246.
| mkt | n | clv_mid | clv_ask | pos | neg | corr | hit_pos | hit_neg |
|---|---|---|---|---|---|---|---|---|
| ML | 43 | +0.21c | +0.21c | 9 | 9 | -0.079 | 55.6% | 55.6% |
| spread | 85 | +0.24c | +0.19c | 30 | 27 | -0.043 | 43.3% | 40.7% |
| total | 87 | +1.10c | +0.48c | 42 | 28 | +0.150 | 54.8% | 35.7% |
| ALL | 215 | +0.58c | +0.31c | 81 | 64 | +0.044 | 50.6% | 40.6% |

Main-line movement (fair 50c line, pick terms; + = moved toward pick), later capture only:
- spread: n=83, mean +0.12 pts, median +0.00, toward 23 / away 18; hit when toward 52.2%, when away 44.4%
- total: n=83, mean +0.29 pts, median +0.04, toward 35 / away 26; hit when toward 54.3%, when away 30.8%
Ledger closing_* filled on 191/480 quoted rows; mean ledger probability_clv -0.15c (n=191).

## 5. Injury / availability adjustment (v2-v5 only store it)
Quoted settled rows with availability fields: 123 (models: spread-margin-v2, spread-margin-v4, spread-margin-v5, total-margin-v2, total-margin-v4, total-margin-v5). v7/v9 rows store no availability component.
### Spread by |availability_points_gap|
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| spread | <=0.5 | 55 | 55 | 20 | 35 | 36.4% | 25%-50% | 58.9% | 50.6% | 50.1% | -18.78 | -23.6% | -28.5% |
| spread | 0.5-2 (unproven) | 2 | 2 | 1 | 1 | 50.0% | 9%-91% | 70.9% | 51.0% |  | +0.00 | 0.0% | 0.0% |
| spread | 4+ (unproven) | 3 | 3 | 2 | 1 | 66.7% | 21%-94% | 76.3% | 51.3% |  | +1.77 | 29.5% | 29.5% |

### Spread: adjustment pushed toward our side?
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| spread | False | 56 | 56 | 21 | 35 | 37.5% | 26%-51% | 59.1% | 50.6% | 50.1% | -16.78 | -20.6% | -26.2% |
| spread | True (unproven) | 4 | 4 | 2 | 2 | 50.0% | 15%-85% | 75.8% | 51.5% |  | -0.23 | -2.9% | -2.9% |

### Spread: pick only had +edge because of the adjustment
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| spread | False | 59 | 59 | 23 | 36 | 39.0% | 28%-52% | 60.1% | 50.7% | 50.1% | -15.01 | -17.1% | -23.3% |
| spread | True (unproven) | 1 | 1 | 0 | 1 | 0.0% | 0%-79% | 67.4% | 51.0% |  | -2.00 | -100.0% | -100.0% |

### Total by |availability_total_loss|
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| total | <=1 | 56 | 56 | 25 | 31 | 44.6% | 32%-58% | 60.5% | 52.2% | 51.0% | -7.61 | -9.6% | -14.3% |
| total | 3-6 (unproven) | 1 | 1 | 0 | 1 | 0.0% | 0%-79% | 70.1% | 51.9% |  | -2.00 | -100.0% | -100.0% |
| total | 6+ (unproven) | 6 | 6 | 4 | 2 | 66.7% | 30%-90% | 85.4% | 51.3% |  | +3.62 | 30.2% | 30.2% |

## 6. Timing and flips
### Final (settled) pick by lead time to tip
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ML | <1h | 49 | 49 | 35 | 14 | 71.4% | 58%-82% | 68.2% | 69.0% | 68.7% | +1.89 | 3.7% | 6.9% |
| ML | 1-3h | 64 | 64 | 42 | 22 | 65.6% | 53%-76% | 66.4% | 65.3% | 65.2% | -1.83 | -2.7% | -1.9% |
| ML | 3-8h | 22 | 22 | 14 | 8 | 63.6% | 43%-80% | 67.5% | 69.0% | 68.5% | -2.17 | -9.7% | -8.7% |
| ML | 8h+ (unproven) | 3 | 3 | 1 | 2 | 33.3% | 6%-79% | 62.7% | 62.7% | 62.2% | -1.51 | -50.2% | -50.2% |
| spread | <1h | 66 | 66 | 33 | 33 | 50.0% | 38%-62% | 60.2% | 51.3% | 50.8% | -3.50 | -3.6% | -2.4% |
| spread | 1-3h | 79 | 79 | 39 | 40 | 49.4% | 39%-60% | 60.0% | 50.4% | 49.9% | +1.63 | 1.4% | -2.3% |
| spread | 3-8h | 25 | 25 | 9 | 16 | 36.0% | 20%-55% | 58.7% | 50.9% | 50.3% | -9.63 | -27.0% | -29.7% |
| total | <1h | 65 | 65 | 27 | 38 | 41.5% | 30%-54% | 56.9% | 50.6% | 50.0% | -14.68 | -16.5% | -17.7% |
| total | 1-3h | 82 | 82 | 40 | 42 | 48.8% | 38%-59% | 57.6% | 51.2% | 50.4% | +0.08 | 0.1% | -4.1% |
| total | 3-8h | 24 | 24 | 11 | 13 | 45.8% | 28%-65% | 61.2% | 53.1% | 51.4% | -5.63 | -16.0% | -14.5% |
| total | 8h+ (unproven) | 1 | 1 | 1 | 0 | 100.0% | 21%-100% | 56.7% | 51.9% | 51.0% | +1.48 | 92.6% | 92.6% |

(event, market, model) groups with a settled pick: 516; mean runs per group 4.2; side flipped first-run -> settled: 37 (7%); groups with >1 distinct side across runs: 43.
| mkt | flipped | n | first_hit | last_hit |
|---|---|---|---|---|
| moneyline | False | 170 | 67.1% | 67.1% |
| moneyline | True | 1 | 0.0% | 100.0% |
| spread | False | 153 | 45.8% | 47.1% |
| spread | True | 19 | 52.6% | 52.6% |
| total | False | 156 | 46.2% | 45.5% |
| total | True | 17 | 58.8% | 47.1% |
first_hit = HYPOTHETICAL grade of the first-run pick at its own line (final scores); last_hit = settled.

## 7. Residuals: model projection vs market main line vs result (one row per event x model)
Spread (home margin):
| model | n | bias | mae | sd | n_mkt | mod_mae_same | mkt_mae | mkt_bias | closer |
|---|---|---|---|---|---|---|---|---|---|
| spread-margin-v2 | 21 | -3.18 | 13.53 | 18.36 | 21 | 13.53 | 11.64 | -3.97 | 76.2% |
| spread-margin-v4 (unproven) | 8 | -3.48 | 4.96 | 5.38 | 8 | 4.96 | 4.97 | -2.88 | 50.0% |
| spread-margin-v5 | 31 | -1.76 | 12.01 | 15.46 | 24 | 11.94 | 10.51 | -0.02 | 79.2% |
| spread-margin-v7 | 25 | -1.05 | 11.52 | 15.41 | 24 | 11.58 | 10.43 | -0.27 | 54.2% |
| spread-margin-v9 | 85 | +0.01 | 12.59 | 15.68 | 83 | 12.75 | 12.03 | +0.18 | 56.6% |
Total (bias = actual - projection; + means projection too low):
| model | n | bias | mae | sd | n_mkt | mod_mae_same | mkt_mae | mkt_bias | closer | mod_minus_mkt |
|---|---|---|---|---|---|---|---|---|---|---|
| total-margin-v2 | 22 | -2.07 | 13.90 | 18.07 | 22 | 13.90 | 14.26 | -5.77 | 63.6% | -3.71 |
| total-margin-v4 (unproven) | 8 | +13.67 | 16.28 | 16.97 | 8 | 16.28 | 13.53 | +11.38 | 75.0% | -2.28 |
| total-margin-v5 | 33 | +2.47 | 16.33 | 21.40 | 26 | 16.63 | 16.09 | -4.02 | 57.7% | -3.96 |
| total-margin-v7 | 24 | -0.20 | 13.87 | 17.99 | 24 | 13.87 | 12.59 | -1.68 | 62.5% | -1.47 |
| total-margin-v9 | 85 | +3.91 | 13.67 | 17.05 | 82 | 13.80 | 13.13 | +3.67 | 58.5% | -0.76 |

- spread: corr(model - market, actual - market) = -0.075 (n=160 event-model rows, 160 games); OLS slope -0.27
- total: corr(model - market, actual - market) = -0.061 (n=162 event-model rows, 162 games); OLS slope -0.27

## 8. Biggest losses and team patterns (quoted)
| date | game | model | pick | line | model | ask | edge | units | pnl | score |
|---|---|---|---|---|---|---|---|---|---|---|
| 11-19 | Utah Jazz @ Los Angeles Lakers | spread-margin-v9 | home | -11.5 | 69.5% | 51.0% | +18.6% | 2.00 | -2.00 | 109-97 |
| 11-09 | Miami Heat @ Sacramento Kings | spread-margin-v9 | away | -8.5 | 67.2% | 50.0% | +17.2% | 2.00 | -2.00 | 114-108 |
| 11-07 | Toronto Raptors @ Milwaukee Bucks | spread-margin-v7 | home | +6.5 | 79.0% | 50.0% | +29.0% | 2.00 | -2.00 | 121-107 |
| 11-14 | New York Knicks @ Indiana Pacers | spread-margin-v9 | away | -8.5 | 68.3% | 50.0% | +18.3% | 2.00 | -2.00 | 104-106 |
| 11-17 | Philadelphia 76ers @ Brooklyn Nets | spread-margin-v9 | home | +4.5 | 73.5% | 48.1% | +25.4% | 2.00 | -2.00 | 135-123 |
| 10-25 | Indiana Pacers @ Brooklyn Nets | spread-margin-v5 | home | +7.5 | 74.2% | 51.9% | +22.3% | 2.00 | -2.00 | 136-109 |
| 10-22 | Indiana Pacers @ New Orleans Pelicans | elo-trend-lr-v4 | home |  | 64.1% | 42.9% | +21.2% | 2.00 | -2.00 | 123-120 |
| 10-27 | Phoenix Suns @ Oklahoma City Thunder | total-margin-v5 | under | 219.5 | 70.2% | 51.0% | +19.2% | 2.00 | -2.00 | 114-125 |
| 10-29 | New York Knicks @ Chicago Bulls | total-margin-v5 | under | 234.5 | 74.6% | 51.0% | +23.7% | 2.00 | -2.00 | 133-127 |
| 11-17 | Orlando Magic @ Boston Celtics | spread-margin-v9 | home | -8.5 | 68.1% | 51.9% | +16.2% | 2.00 | -2.00 | 115-105 |
| 10-28 | Portland Trail Blazers @ Los Angeles Lakers | total-margin-v5 | under | 236.5 | 74.2% | 51.9% | +22.3% | 2.00 | -2.00 | 136-129 |
| 11-16 | Oklahoma City Thunder @ Sacramento Kings | spread-margin-v9 | home | +12.5 | 74.0% | 50.0% | +24.0% | 2.00 | -2.00 | 125-103 |

Spread+ML, team we backed (worst 8 by pnl):
                        n  W     pnl
pick_team                           
Sacramento Kings        8  1 -9.3173
New York Knicks        17  7 -8.7430
Washington Wizards      7  1 -6.0226
Miami Heat             10  4 -5.3687
Atlanta Hawks           9  3 -5.0533
Oklahoma City Thunder   9  4 -4.5364
Brooklyn Nets           9  3 -4.3981
Boston Celtics         13  7 -3.8656
Spread+ML, team we faded (worst 8 by pnl):
                        n   W     pnl
opp_team                             
Memphis Grizzlies      12   2 -9.0071
Boston Celtics          5   0 -7.5000
Houston Rockets         9   2 -6.4084
Chicago Bulls          12   6 -5.1819
New York Knicks         4   0 -4.5000
Utah Jazz              20  12 -3.7113
Washington Wizards     17  10 -2.8889
Oklahoma City Thunder  11   5 -2.7029
Totals, by team involved (worst 8 by pnl):
                       n  W     pnl
team                               
Brooklyn Nets         10  2 -8.7749
Milwaukee Bucks       12  3 -8.5958
Miami Heat            13  3 -8.3658
Houston Rockets       12  3 -6.9620
Atlanta Hawks         12  4 -6.6947
Sacramento Kings      10  3 -6.0603
New Orleans Pelicans  12  4 -5.7203
Memphis Grizzlies     12  5 -3.9360
### Rest differential present (v2-v5 only)
| mkt | model | n | games | W | L | hit | ci | p_model | p_ask | p_mid | pnl | roi_u | roi_1u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| spread | equal rest | 22 | 22 | 6 | 16 | 27.3% | 13%-48% | 60.4% | 50.7% | 50.0% | -12.77 | -39.1% | -47.0% |
| spread | rest edge exists | 38 | 38 | 17 | 21 | 44.7% | 30%-60% | 60.1% | 50.7% | 50.1% | -4.24 | -7.5% | -11.6% |
| total | equal rest | 36 | 36 | 16 | 20 | 44.4% | 30%-60% | 62.4% | 52.3% | 51.1% | -3.15 | -6.1% | -16.0% |
| total | rest edge exists | 27 | 27 | 13 | 14 | 48.1% | 31%-66% | 63.9% | 52.0% | 50.9% | -2.84 | -6.8% | -5.4% |

## Reproduction of 2026-11-18 figures (deduped, settled, event start before 11-18 12:00Z)
- total edge>=2% [all rows]: to 11-18 56-61, claimed 60.6%, ROI -3.8%; through today 61-68, ROI -5.3%
- total edge>=2% [quoted only]: to 11-18 56-61, claimed 60.6%, ROI -3.8%; through today 61-68, ROI -5.3%
- spread edge>=2% [all rows]: to 11-18 58-72, claimed 61.5%, ROI -9.6%; through today 67-73, ROI -3.5%
- spread edge>=2% [quoted only]: to 11-18 58-72, claimed 61.5%, ROI -9.6%; through today 67-73, ROI -3.5%
- moneyline edge>=10% [all rows]: to 11-18 17-18, claimed 66.0%, ROI -5.4%; through today 19-19, ROI -2.6%
- moneyline edge>=10% [quoted only]: to 11-18 9-13, claimed 64.7%, ROI -18.0%; through today 11-14, ROI -12.6%
