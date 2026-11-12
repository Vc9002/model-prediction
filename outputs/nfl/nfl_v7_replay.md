# NFL v7 point-in-time replay and calibration

Generated 2026-11-12T04:44:44.456260+00:00 -- spread artifact `6fb773b9425f`, total artifact `f6a4eab2bab1`.

## Cohort

- 1489 completed games with closing lines, seasons 2021-2026; 0 failures.
- spread decided 1451 (pushes 38); total decided 1476 (pushes 13).
- both teams had prior-season-games efficiency data in 1473 games; QB source {'depth_chart': 65, 'attempts_fallback': 1424}; QB injury penalty applied in 0 games; recorded weather in 871 games.

## Spread (P(home cover) vs closing line; market Brier at the closing line = 0.2500)

- n 1451: Brier 0.2496, log loss 0.6924, ECE 0.0083, slope 0.789, intercept 0.014, favoured-side hit 0.5272.
- bets at 52.38% ask with edge >= 0.03: 63 bets, hit 0.4762, -5.73u, ROI -0.0909.
- bets at 52.38% ask with edge >= 0.05: 16 bets, hit 0.3750, -4.55u, ROI -0.2841.
- bets at 52.38% ask with edge >= 0.08: 2 bets, hit 0.0000, -2.00u, ROI -1.0000.
- |line| >= 8 subset: n 258, Brier 0.2506, favoured-side hit 0.5116.

| season | n | Brier | log loss | favoured hit | edge>=5% bets | units |
|---|---|---|---|---|---|---|
| 2021 | 281 | 0.2506 | 0.6944 | 0.5160 | 3 | -1.09 |
| 2022 | 274 | 0.2463 | 0.6856 | 0.5474 | 3 | +2.73 |
| 2023 | 271 | 0.2512 | 0.6956 | 0.4982 | 1 | -1.00 |
| 2024 | 281 | 0.2493 | 0.6917 | 0.5409 | 5 | -1.18 |
| 2025 | 284 | 0.2509 | 0.6950 | 0.5282 | 3 | -3.00 |
| 2026 | 60 | 0.2485 | 0.6902 | 0.5500 | 1 | -1.00 |

## Total (P(over) vs closing line)

- A: weather unavailable: n 1476, Brier 0.2517, log loss 0.6966, ECE 0.0317, slope 0.197, favoured-side hit 0.5054; edge>=5%: 150 bets, -6.82u.
- B: recorded weather: n 1476, Brier 0.2506, log loss 0.6944, ECE 0.0300, slope 0.428, favoured-side hit 0.5102; edge>=5%: 145 bets, +9.64u.
- weather changed the projected total in 248 games.

| season | n | Brier (B) | log loss | favoured hit | edge>=5% bets | units |
|---|---|---|---|---|---|---|
| 2021 | 282 | 0.2536 | 0.7005 | 0.4929 | 32 | -1.45 |
| 2022 | 281 | 0.2492 | 0.6916 | 0.5267 | 27 | +1.64 |
| 2023 | 281 | 0.2488 | 0.6908 | 0.5160 | 28 | +6.36 |
| 2024 | 282 | 0.2501 | 0.6933 | 0.4929 | 21 | +5.73 |
| 2025 | 285 | 0.2507 | 0.6946 | 0.5123 | 30 | -1.36 |
| 2026 | 65 | 0.2525 | 0.6984 | 0.5538 | 7 | -1.27 |

## Walk-forward calibration (select on first 80%, score once on locked last 20%)

- spread: champion `identity` (selection n 1160, OOF Brier 0.2494 vs raw 0.2496); holdout n 291 from 2025-09-28: raw Brier 0.2504 / log loss 0.6941 -> calibrated 0.2504 / 0.6941; final parameters {}.
- total: champion `identity` (selection n 1180, OOF Brier 0.2505 vs raw 0.2506); holdout n 296 from 2025-09-28: raw Brier 0.2509 / log loss 0.6949 -> calibrated 0.2509 / 0.6949; final parameters {}.

## Flat-ledger replay (110 settled spread/total picks re-priced at their own line and ask; 0 skipped)

- spread: 59 picks as logged (nfl-spread-structural-v5, nfl-spread-structural-v6, nfl-spread-structural-v7) Brier 0.2792, recorded -15.88u.
  - fixed v7 raw: Brier 0.2500, 1u on every recorded selection -7.34u; fires at edge>=5%: 3 bets, 2 wins, +1.38u.
  - fixed v7 calibrated: Brier 0.2500, 1u on every recorded selection -7.34u; fires at edge>=5%: 3 bets, 2 wins, +1.38u.
  - weather used: {None: 59}.
- total: 51 picks as logged (nfl-total-structural-v5, nfl-total-structural-v7) Brier 0.2759, recorded +0.53u.
  - fixed v7 raw: Brier 0.2485, 1u on every recorded selection -2.23u; fires at edge>=5%: 5 bets, 4 wins, +3.15u.
  - fixed v7 calibrated: Brier 0.2485, 1u on every recorded selection -2.23u; fires at edge>=5%: 5 bets, 4 wins, +3.15u.
  - weather used: {'unavailable': 33, 'verified_forecast': 18}.
- moneyline as logged (nfl-elo-trend-lr-v4): 85 picks, 55W/29L/1P, +5.55u. moneyline champion is nfl-elo-trend-lr-v4 (logistic regression), not re-priced by the v7 engines.
