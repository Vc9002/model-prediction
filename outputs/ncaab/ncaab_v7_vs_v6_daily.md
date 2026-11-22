# NCAAB spread v7 vs v6: point-in-time replay of every settled game, day by day

Generated 2026-11-22T01:56:22.873493+00:00 by `scripts/replay_ncaab_v7_vs_v6_daily.py` (dedup `latest`). Research only: the ledger was opened read-only; nothing in config/, src/ or the ledgers changed.

- Models: `ncaab-spread-v6` (artifact `7c6d6d1c551f`) and `ncaab-spread-v7` (identity `53c37577d662`: same v6 artifact + frozen multi-season margin engine), both through the production factories and `predict_games`.
- Universe: 1851 settled NCAAB spread/moneyline ledger rows (all tiers, all model ids) -> 1227 game x market decisions (latest settled row before tip); 1227 priced, 0 skipped ({}).
- Prices: {'archived_bbo': 605, 'complement': 622}. `archived_bbo` = both sides' asks from the latest Polymarket two-sided quote observed at or before the decision; `complement` = logged ask for the logged side, other side = 1 - ask + 1.0 pts (median overround). Logged ask vs archived ask on the logged side: {'match': 478, 'differs': 127}.
- Grading: final scores {'espn_history_swapped': 56, 'espn_history': 1081, 'ledger_payload': 40, 'espn_scoreboard_fetch': 46, 'espn_scoreboard_fetch_swapped': 4}; pushes in universe 0; ledger result disagrees with the final score on 1 rows; payload score differs from ESPN history on 1 rows.
- Neutral flag source: {'espn_scoreboard': 466, 'missing_assumed_home_court': 40, 'logged_features': 721}. Placeholder rule (0U when the replay prices a side off the logged 50% indicative placeholder): {'v6': 43, 'v7': 52} bets zero-staked in the edge-scaled columns; they still count at 1U in the flat-1U columns.
- Side mapping check: logged ask sits on the other side's archived ask on 1 BBO-priced rows (would indicate a home/away mapping error in the replay or the ledger).
- Non-D1 games (declined by the live rule for both models; v7 has no engine there): 70. v7 engine on D1 rows: {'v7': 1157}; v7 exceptions caught: 0.
- Calling rule = `college_basketball_forward`: **flat** = favoured side (p >= 50%), spread best edge with no hurdle and a 12-point divergence gate, moneyline edge > 0; **qualified** (research/gated admission) = flat + edge >= 3.5%, |line| <= 16, moneyline ask >= 20%; **main** = qualified + model current-season sample >= 8 (no team reaches 8 before late November, so main is empty this season so far). Stakes `compute_dynamic_units` (1.0-2.0U, 1U cap at p >= 80%) unless marked 1U. Raw probabilities (NCAAB calibrators serve identity).

## Reproduction gate (replayed probability on the logged side vs the logged raw probability)

- logged engine v6: n 272, within 0.01% 270, within 1 pt 272, max |diff| 0.007
- logged engine v7: n 295, within 0.01% 295, within 1 pt 295, max |diff| 0.0

## Daily, spread, flat rule, edge-scaled 1.0-2.0U

| date | game-markets priced (D1) | v6 bets | v6 W-L | v6 U | v6 cum | v7 bets | v7 W-L | v7 U | v7 cum | both bet | same side |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | 112 (112) | 77 | 38-39 | -3.61 | -3.61 | 106 | 46-60 | -27.28 | -27.28 | 77 | 61 |
| 2026-11-03 | 20 (15) | 13 | 4-9 | -7.23 | -10.84 | 15 | 6-9 | -2.28 | -29.56 | 13 | 13 |
| 2026-11-04 | 10 (8) | 7 | 3-4 | -2.22 | -13.06 | 7 | 4-3 | +0.25 | -29.31 | 7 | 2 |
| 2026-11-05 | 43 (40) | 33 | 15-18 | -4.43 | -17.49 | 40 | 20-20 | -1.59 | -30.90 | 33 | 28 |
| 2026-11-06 | 56 (52) | 38 | 14-24 | -9.38 | -26.87 | 50 | 23-27 | -3.80 | -34.70 | 37 | 30 |
| 2026-11-07 | 28 (28) | 28 | 14-14 | +1.71 | -25.16 | 28 | 14-14 | +4.50 | -30.20 | 28 | 24 |
| 2026-11-08 | 18 (18) | 18 | 10-8 | +8.65 | -16.51 | 18 | 7-11 | -1.81 | -32.01 | 18 | 13 |
| 2026-11-09 | 7 (7) | 7 | 3-4 | -1.26 | -17.77 | 7 | 5-2 | +6.42 | -25.59 | 7 | 5 |
| 2026-11-10 | 26 (26) | 26 | 14-12 | +2.49 | -15.28 | 26 | 15-11 | +2.85 | -22.74 | 26 | 21 |
| 2026-11-11 | 39 (39) | 34 | 17-17 | +3.56 | -11.72 | 39 | 20-19 | +0.58 | -22.16 | 34 | 27 |
| 2026-11-12 | 22 (22) | 20 | 12-8 | +3.53 | -8.19 | 22 | 10-12 | -3.71 | -25.87 | 20 | 13 |
| 2026-11-13 | 45 (45) | 37 | 19-18 | +2.20 | -5.99 | 45 | 18-27 | -16.41 | -42.28 | 37 | 26 |
| 2026-11-14 | 54 (54) | 51 | 26-25 | -3.00 | -8.99 | 54 | 26-28 | -7.68 | -49.96 | 51 | 39 |
| 2026-11-15 | 29 (29) | 29 | 12-17 | -8.46 | -17.45 | 29 | 8-21 | -19.13 | -69.09 | 29 | 23 |
| 2026-11-16 | 18 (18) | 14 | 7-7 | -1.79 | -19.24 | 18 | 6-12 | -9.32 | -78.41 | 14 | 12 |
| 2026-11-17 | 44 (44) | 41 | 18-23 | -8.99 | -28.23 | 44 | 22-22 | -6.83 | -85.24 | 41 | 33 |
| 2026-11-18 | 55 (55) | 50 | 21-29 | -12.43 | -40.66 | 55 | 30-25 | +5.69 | -79.55 | 50 | 39 |
| 2026-11-19 | 24 (24) | 21 | 12-9 | +1.31 | -39.35 | 24 | 10-14 | -4.39 | -83.94 | 21 | 15 |
| 2026-11-20 | 45 (45) | 41 | 22-19 | -0.17 | -39.52 | 45 | 22-23 | -14.42 | -98.36 | 41 | 28 |
| 2026-11-21 | 37 (37) | 36 | 21-15 | +8.33 | -31.19 | 37 | 20-17 | +2.39 | -95.97 | 36 | 31 |

## Daily, spread, flat rule, flat 1U

| date | game-markets priced (D1) | v6 bets | v6 W-L | v6 U | v6 cum | v7 bets | v7 W-L | v7 U | v7 cum | both bet | same side |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | 112 (112) | 77 | 38-39 | -1.89 | -1.89 | 106 | 46-60 | -15.40 | -15.40 | 77 | 61 |
| 2026-11-03 | 20 (15) | 13 | 4-9 | -5.23 | -7.12 | 15 | 6-9 | -3.11 | -18.51 | 13 | 13 |
| 2026-11-04 | 10 (8) | 7 | 3-4 | -1.19 | -8.31 | 7 | 4-3 | +0.77 | -17.74 | 7 | 2 |
| 2026-11-05 | 43 (40) | 33 | 15-18 | -3.52 | -11.83 | 40 | 20-20 | -0.64 | -18.38 | 33 | 28 |
| 2026-11-06 | 56 (52) | 38 | 14-24 | -10.60 | -22.43 | 50 | 23-27 | -4.75 | -23.13 | 37 | 30 |
| 2026-11-07 | 28 (28) | 28 | 14-14 | -0.54 | -22.97 | 28 | 14-14 | -0.39 | -23.52 | 28 | 24 |
| 2026-11-08 | 18 (18) | 18 | 10-8 | +1.78 | -21.19 | 18 | 7-11 | -4.15 | -27.67 | 18 | 13 |
| 2026-11-09 | 7 (7) | 7 | 3-4 | -1.04 | -22.23 | 7 | 5-2 | +2.96 | -24.71 | 7 | 5 |
| 2026-11-10 | 26 (26) | 26 | 14-12 | +1.21 | -21.02 | 26 | 15-11 | +3.17 | -21.54 | 26 | 21 |
| 2026-11-11 | 39 (39) | 34 | 17-17 | -0.67 | -21.69 | 39 | 20-19 | +0.13 | -21.41 | 34 | 27 |
| 2026-11-12 | 22 (22) | 20 | 12-8 | +3.71 | -17.98 | 22 | 10-12 | -2.30 | -23.71 | 20 | 13 |
| 2026-11-13 | 45 (45) | 37 | 19-18 | +0.05 | -17.93 | 45 | 18-27 | -9.88 | -33.59 | 37 | 26 |
| 2026-11-14 | 54 (54) | 51 | 26-25 | +0.50 | -17.43 | 54 | 26-28 | -2.46 | -36.05 | 51 | 39 |
| 2026-11-15 | 29 (29) | 29 | 12-17 | -5.10 | -22.53 | 29 | 8-21 | -13.07 | -49.12 | 29 | 23 |
| 2026-11-16 | 18 (18) | 14 | 7-7 | -0.14 | -22.67 | 18 | 6-12 | -6.03 | -55.15 | 14 | 12 |
| 2026-11-17 | 44 (44) | 41 | 18-23 | -6.15 | -28.82 | 44 | 22-22 | -1.34 | -56.49 | 41 | 33 |
| 2026-11-18 | 55 (55) | 50 | 21-29 | -8.78 | -37.60 | 55 | 30-25 | +4.18 | -52.31 | 50 | 39 |
| 2026-11-19 | 24 (24) | 21 | 12-9 | +2.47 | -35.13 | 24 | 10-14 | -4.50 | -56.81 | 21 | 15 |
| 2026-11-20 | 45 (45) | 41 | 22-19 | +2.14 | -32.99 | 45 | 22-23 | -1.86 | -58.67 | 41 | 28 |
| 2026-11-21 | 37 (37) | 36 | 21-15 | +4.05 | -28.94 | 37 | 20-17 | +1.21 | -57.46 | 36 | 31 |

## Daily, spread, qualified rule, edge-scaled 1.0-2.0U

| date | game-markets priced (D1) | v6 bets | v6 W-L | v6 U | v6 cum | v7 bets | v7 W-L | v7 U | v7 cum | both bet | same side |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | 112 (112) | 38 | 18-20 | -5.22 | -5.22 | 39 | 20-19 | -5.56 | -5.56 | 33 | 30 |
| 2026-11-03 | 20 (15) | 4 | 1-3 | -2.86 | -8.08 | 2 | 0-2 | -3.78 | -9.34 | 2 | 2 |
| 2026-11-04 | 10 (8) | 2 | 1-1 | -1.03 | -9.11 | 3 | 1-2 | -3.24 | -12.58 | 1 | 1 |
| 2026-11-05 | 43 (40) | 13 | 8-5 | +3.62 | -5.49 | 12 | 7-5 | +1.79 | -10.79 | 8 | 8 |
| 2026-11-06 | 56 (52) | 12 | 3-9 | -6.43 | -11.92 | 14 | 6-8 | +0.78 | -10.01 | 8 | 7 |
| 2026-11-07 | 28 (28) | 21 | 11-10 | +3.04 | -8.88 | 19 | 11-8 | +6.57 | -3.44 | 15 | 15 |
| 2026-11-08 | 18 (18) | 10 | 6-4 | +7.80 | -1.08 | 12 | 5-7 | +0.27 | -3.17 | 7 | 7 |
| 2026-11-09 | 7 (7) | 6 | 3-3 | -0.26 | -1.34 | 7 | 5-2 | +6.42 | +3.25 | 6 | 5 |
| 2026-11-10 | 26 (26) | 19 | 13-6 | +7.57 | +6.23 | 21 | 12-9 | +2.19 | +5.44 | 14 | 13 |
| 2026-11-11 | 39 (39) | 17 | 9-8 | +2.34 | +8.57 | 19 | 11-8 | +4.09 | +9.53 | 16 | 16 |
| 2026-11-12 | 22 (22) | 13 | 6-7 | -1.93 | +6.64 | 7 | 2-5 | -4.38 | +5.15 | 5 | 4 |
| 2026-11-13 | 45 (45) | 13 | 3-10 | -10.73 | -4.09 | 19 | 3-16 | -24.74 | -19.59 | 10 | 9 |
| 2026-11-14 | 54 (54) | 34 | 18-16 | -0.49 | -4.58 | 37 | 18-19 | -3.14 | -22.73 | 28 | 25 |
| 2026-11-15 | 29 (29) | 13 | 5-8 | -4.29 | -8.87 | 13 | 4-9 | -8.18 | -30.91 | 10 | 10 |
| 2026-11-16 | 18 (18) | 6 | 3-3 | -0.56 | -9.43 | 4 | 2-2 | +0.18 | -30.73 | 4 | 4 |
| 2026-11-17 | 44 (44) | 25 | 10-15 | -11.22 | -20.65 | 22 | 9-13 | -10.15 | -40.88 | 20 | 19 |
| 2026-11-18 | 55 (55) | 23 | 9-14 | -9.51 | -30.16 | 20 | 11-9 | +1.20 | -39.68 | 15 | 13 |
| 2026-11-19 | 24 (24) | 10 | 5-5 | -2.05 | -32.21 | 10 | 6-4 | +2.26 | -37.42 | 7 | 6 |
| 2026-11-20 | 45 (45) | 24 | 12-12 | -1.32 | -33.53 | 21 | 6-15 | -21.87 | -59.29 | 16 | 13 |
| 2026-11-21 | 37 (37) | 25 | 13-12 | +2.72 | -30.81 | 13 | 8-5 | +3.42 | -55.87 | 11 | 11 |

## Daily, spread, qualified rule, flat 1U

| date | game-markets priced (D1) | v6 bets | v6 W-L | v6 U | v6 cum | v7 bets | v7 W-L | v7 U | v7 cum | both bet | same side |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | 112 (112) | 38 | 18-20 | -2.11 | -2.11 | 39 | 20-19 | +0.70 | +0.70 | 33 | 30 |
| 2026-11-03 | 20 (15) | 4 | 1-3 | -2.04 | -4.15 | 2 | 0-2 | -2.00 | -1.30 | 2 | 2 |
| 2026-11-04 | 10 (8) | 2 | 1-1 | +0.00 | -4.15 | 3 | 1-2 | -1.00 | -2.30 | 1 | 1 |
| 2026-11-05 | 43 (40) | 13 | 8-5 | +2.63 | -1.52 | 12 | 7-5 | +1.89 | -0.41 | 8 | 8 |
| 2026-11-06 | 56 (52) | 12 | 3-9 | -6.07 | -7.59 | 14 | 6-8 | -2.11 | -2.52 | 8 | 7 |
| 2026-11-07 | 28 (28) | 21 | 11-10 | +0.80 | -6.79 | 19 | 11-8 | +2.69 | +0.17 | 15 | 15 |
| 2026-11-08 | 18 (18) | 10 | 6-4 | +1.93 | -4.86 | 12 | 5-7 | -2.07 | -1.90 | 7 | 7 |
| 2026-11-09 | 7 (7) | 6 | 3-3 | -0.04 | -4.90 | 7 | 5-2 | +2.96 | +1.06 | 6 | 5 |
| 2026-11-10 | 26 (26) | 19 | 13-6 | +6.28 | +1.38 | 21 | 12-9 | +2.51 | +3.57 | 14 | 13 |
| 2026-11-11 | 39 (39) | 17 | 9-8 | +0.70 | +2.08 | 19 | 11-8 | +2.63 | +6.20 | 16 | 16 |
| 2026-11-12 | 22 (22) | 13 | 6-7 | -0.95 | +1.13 | 7 | 2-5 | -2.96 | +3.24 | 5 | 4 |
| 2026-11-13 | 45 (45) | 13 | 3-10 | -7.23 | -6.10 | 19 | 3-16 | -13.08 | -9.84 | 10 | 9 |
| 2026-11-14 | 54 (54) | 34 | 18-16 | +2.07 | -4.03 | 37 | 18-19 | -1.24 | -11.08 | 28 | 25 |
| 2026-11-15 | 29 (29) | 13 | 5-8 | -3.00 | -7.03 | 13 | 4-9 | -5.04 | -16.12 | 10 | 10 |
| 2026-11-16 | 18 (18) | 6 | 3-3 | -0.02 | -7.05 | 4 | 2-2 | +0.09 | -16.03 | 4 | 4 |
| 2026-11-17 | 44 (44) | 25 | 10-15 | -5.68 | -12.73 | 22 | 9-13 | -4.65 | -20.68 | 20 | 19 |
| 2026-11-18 | 55 (55) | 23 | 9-14 | -5.45 | -18.18 | 20 | 11-9 | +1.75 | -18.93 | 15 | 13 |
| 2026-11-19 | 24 (24) | 10 | 5-5 | -0.03 | -18.21 | 10 | 6-4 | +1.77 | -17.16 | 7 | 6 |
| 2026-11-20 | 45 (45) | 24 | 12-12 | -0.21 | -18.42 | 21 | 6-15 | -9.15 | -26.31 | 16 | 13 |
| 2026-11-21 | 37 (37) | 25 | 13-12 | +0.40 | -18.02 | 13 | 8-5 | +2.56 | -23.75 | 11 | 11 |

## Daily, moneyline, flat rule, edge-scaled 1.0-2.0U

| date | game-markets priced (D1) | v6 bets | v6 W-L | v6 U | v6 cum | v7 bets | v7 W-L | v7 U | v7 cum | both bet | same side |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | 90 (90) | 19 | 12-7 | +7.19 | +7.19 | 24 | 16-8 | +10.74 | +10.74 | 18 | 18 |
| 2026-11-03 | 15 (11) | 0 | 0-0 | +0.00 | +7.19 | 1 | 0-1 | -1.00 | +9.74 | 0 | 0 |
| 2026-11-04 | 25 (7) | 0 | 0-0 | +0.00 | +7.19 | 1 | 1-0 | +0.72 | +10.46 | 0 | 0 |
| 2026-11-05 | 53 (35) | 6 | 1-5 | -6.56 | +0.63 | 9 | 2-7 | -7.92 | +2.54 | 6 | 6 |
| 2026-11-06 | 54 (41) | 5 | 1-4 | -2.95 | -2.32 | 7 | 2-5 | -4.14 | -1.60 | 5 | 5 |
| 2026-11-07 | 14 (14) | 9 | 5-4 | +2.51 | +0.19 | 8 | 5-3 | +3.05 | +1.45 | 8 | 8 |
| 2026-11-08 | 15 (15) | 5 | 2-3 | -1.57 | -1.38 | 8 | 1-7 | -8.52 | -7.07 | 4 | 4 |
| 2026-11-10 | 2 (2) | 2 | 1-1 | +0.88 | -0.50 | 2 | 1-1 | +0.88 | -6.19 | 2 | 2 |
| 2026-11-11 | 33 (33) | 11 | 6-5 | -2.53 | -3.03 | 13 | 8-5 | -2.04 | -8.23 | 11 | 11 |
| 2026-11-12 | 23 (22) | 4 | 0-4 | -4.40 | -7.43 | 6 | 1-5 | -4.80 | -13.03 | 4 | 4 |
| 2026-11-13 | 40 (39) | 12 | 4-8 | -5.73 | -13.16 | 14 | 4-10 | -10.55 | -23.58 | 11 | 11 |
| 2026-11-14 | 52 (51) | 13 | 6-7 | +1.60 | -11.56 | 19 | 8-11 | -4.77 | -28.35 | 12 | 11 |
| 2026-11-15 | 7 (7) | 7 | 3-4 | +0.90 | -10.66 | 6 | 3-3 | +1.38 | -26.97 | 6 | 6 |
| 2026-11-16 | 4 (4) | 4 | 4-0 | +4.96 | -5.70 | 4 | 4-0 | +5.53 | -21.44 | 4 | 4 |
| 2026-11-17 | 17 (17) | 14 | 5-9 | -5.82 | -11.52 | 17 | 7-10 | -5.92 | -27.36 | 14 | 14 |
| 2026-11-18 | 11 (11) | 8 | 6-2 | +1.56 | -9.96 | 11 | 7-4 | -0.57 | -27.93 | 8 | 8 |
| 2026-11-19 | 8 (8) | 5 | 2-3 | -1.47 | -11.43 | 8 | 5-3 | +0.98 | -26.95 | 5 | 4 |
| 2026-11-20 | 19 (19) | 15 | 9-6 | +3.27 | -8.16 | 19 | 9-10 | -3.68 | -30.63 | 15 | 15 |
| 2026-11-21 | 13 (13) | 8 | 6-2 | +10.99 | +2.83 | 13 | 9-4 | +10.31 | -20.32 | 8 | 8 |

## Daily, moneyline, qualified rule, edge-scaled 1.0-2.0U

| date | game-markets priced (D1) | v6 bets | v6 W-L | v6 U | v6 cum | v7 bets | v7 W-L | v7 U | v7 cum | both bet | same side |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | 90 (90) | 16 | 9-7 | +5.62 | +5.62 | 20 | 12-8 | +8.53 | +8.53 | 15 | 15 |
| 2026-11-03 | 15 (11) | 0 | 0-0 | +0.00 | +5.62 | 0 | 0-0 | +0.00 | +8.53 | 0 | 0 |
| 2026-11-04 | 25 (7) | 0 | 0-0 | +0.00 | +5.62 | 0 | 0-0 | +0.00 | +8.53 | 0 | 0 |
| 2026-11-05 | 53 (35) | 5 | 1-4 | -5.56 | +0.06 | 6 | 1-5 | -6.17 | +2.36 | 5 | 5 |
| 2026-11-06 | 54 (41) | 3 | 0-3 | -3.08 | -3.02 | 4 | 1-3 | -2.49 | -0.13 | 2 | 2 |
| 2026-11-07 | 14 (14) | 6 | 3-3 | +2.27 | -0.75 | 8 | 5-3 | +3.05 | +2.92 | 6 | 6 |
| 2026-11-08 | 15 (15) | 3 | 1-2 | -1.42 | -2.17 | 8 | 1-7 | -8.52 | -5.60 | 3 | 3 |
| 2026-11-10 | 2 (2) | 2 | 1-1 | +0.88 | -1.29 | 2 | 1-1 | +0.88 | -4.72 | 2 | 2 |
| 2026-11-11 | 33 (33) | 7 | 2-5 | -5.08 | -6.37 | 10 | 5-5 | -3.68 | -8.40 | 7 | 7 |
| 2026-11-12 | 23 (22) | 3 | 0-3 | -3.40 | -9.77 | 3 | 0-3 | -3.19 | -11.59 | 3 | 3 |
| 2026-11-13 | 40 (39) | 6 | 2-4 | -3.37 | -13.14 | 11 | 4-7 | -7.55 | -19.14 | 5 | 5 |
| 2026-11-14 | 52 (51) | 11 | 5-6 | +1.64 | -11.50 | 10 | 4-6 | -2.05 | -21.19 | 10 | 10 |
| 2026-11-15 | 7 (7) | 5 | 3-2 | +2.90 | -8.60 | 6 | 3-3 | +1.38 | -19.81 | 5 | 5 |
| 2026-11-16 | 4 (4) | 2 | 2-0 | +4.10 | -4.50 | 4 | 4-0 | +5.53 | -14.28 | 2 | 2 |
| 2026-11-17 | 17 (17) | 11 | 5-6 | -2.82 | -7.32 | 14 | 5-9 | -6.13 | -20.41 | 11 | 11 |
| 2026-11-18 | 11 (11) | 5 | 3-2 | -0.28 | -7.60 | 8 | 4-4 | -2.41 | -22.82 | 5 | 5 |
| 2026-11-19 | 8 (8) | 5 | 2-3 | -1.47 | -9.07 | 5 | 2-3 | -1.57 | -24.39 | 4 | 4 |
| 2026-11-20 | 19 (19) | 11 | 7-4 | +4.05 | -5.02 | 10 | 5-5 | -1.76 | -26.15 | 9 | 9 |
| 2026-11-21 | 13 (13) | 6 | 6-0 | +12.99 | +7.97 | 9 | 7-2 | +10.60 | -15.55 | 6 | 6 |

## Daily, all, flat rule, edge-scaled 1.0-2.0U

| date | game-markets priced (D1) | v6 bets | v6 W-L | v6 U | v6 cum | v7 bets | v7 W-L | v7 U | v7 cum | both bet | same side |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | 202 (202) | 96 | 50-46 | +3.58 | +3.58 | 130 | 62-68 | -16.54 | -16.54 | 95 | 79 |
| 2026-11-03 | 35 (26) | 13 | 4-9 | -7.23 | -3.65 | 16 | 6-10 | -3.28 | -19.82 | 13 | 13 |
| 2026-11-04 | 35 (15) | 7 | 3-4 | -2.22 | -5.87 | 8 | 5-3 | +0.97 | -18.85 | 7 | 2 |
| 2026-11-05 | 96 (75) | 39 | 16-23 | -10.99 | -16.86 | 49 | 22-27 | -9.51 | -28.36 | 39 | 34 |
| 2026-11-06 | 110 (93) | 43 | 15-28 | -12.33 | -29.19 | 57 | 25-32 | -7.93 | -36.29 | 42 | 35 |
| 2026-11-07 | 42 (42) | 37 | 19-18 | +4.22 | -24.97 | 36 | 19-17 | +7.55 | -28.74 | 36 | 32 |
| 2026-11-08 | 33 (33) | 23 | 12-11 | +7.08 | -17.89 | 26 | 8-18 | -10.33 | -39.07 | 22 | 17 |
| 2026-11-09 | 7 (7) | 7 | 3-4 | -1.26 | -19.15 | 7 | 5-2 | +6.42 | -32.65 | 7 | 5 |
| 2026-11-10 | 28 (28) | 28 | 15-13 | +3.37 | -15.78 | 28 | 16-12 | +3.73 | -28.92 | 28 | 23 |
| 2026-11-11 | 72 (72) | 45 | 23-22 | +1.03 | -14.75 | 52 | 28-24 | -1.46 | -30.38 | 45 | 38 |
| 2026-11-12 | 45 (44) | 24 | 12-12 | -0.87 | -15.62 | 28 | 11-17 | -8.51 | -38.89 | 24 | 17 |
| 2026-11-13 | 85 (84) | 49 | 23-26 | -3.53 | -19.15 | 59 | 22-37 | -26.96 | -65.85 | 48 | 37 |
| 2026-11-14 | 106 (105) | 64 | 32-32 | -1.40 | -20.55 | 73 | 34-39 | -12.45 | -78.30 | 63 | 50 |
| 2026-11-15 | 36 (36) | 36 | 15-21 | -7.57 | -28.12 | 35 | 11-24 | -17.75 | -96.05 | 35 | 29 |
| 2026-11-16 | 22 (22) | 18 | 11-7 | +3.17 | -24.95 | 22 | 10-12 | -3.79 | -99.84 | 18 | 16 |
| 2026-11-17 | 61 (61) | 55 | 23-32 | -14.81 | -39.76 | 61 | 29-32 | -12.76 | -112.60 | 55 | 47 |
| 2026-11-18 | 66 (66) | 58 | 27-31 | -10.87 | -50.63 | 66 | 37-29 | +5.11 | -107.49 | 58 | 47 |
| 2026-11-19 | 32 (32) | 26 | 14-12 | -0.16 | -50.79 | 32 | 15-17 | -3.42 | -110.91 | 26 | 19 |
| 2026-11-20 | 64 (64) | 56 | 31-25 | +3.11 | -47.68 | 64 | 31-33 | -18.10 | -129.01 | 56 | 43 |
| 2026-11-21 | 50 (50) | 44 | 27-17 | +19.32 | -28.36 | 50 | 29-21 | +12.70 | -116.31 | 44 | 39 |

## Totals by market

### spread

- flat v6: 621 bets 302-319, -31.19U (ROI -3.3%), mean edge 9.8%, mean ask 50.9c; 1U: -28.95U (ROI -4.7%)
  - BBO-priced rows only: 361 bets 165-196, -60.03U (ROI -10.4%), mean edge 8.8%, mean ask 51.0c
- flat v7: 709 bets 332-377, -95.98U (ROI -9.2%), mean edge 9.4%, mean ask 50.9c; 1U: -57.44U (ROI -8.1%)
  - BBO-priced rows only: 390 bets 178-212, -82.85U (ROI -14.0%), mean edge 7.4%, mean ask 51.0c
- flat agreement: both bet 620, same side 483, only v6 1, only v7 89
- flat dyn v7 - v6: ROI -5.9% (95% CI -13.0% to +2.5%, P(diff <= 0) 0.92); PnL -64.79U (CI -146.36 to +18.90); 20 day clusters
- flat u1 v7 - v6: ROI -3.4% (95% CI -11.0% to +4.7%, P(diff <= 0) 0.80); PnL -28.49U (CI -80.75 to +25.56); 20 day clusters
- qualified v6: 328 bets 157-171, -30.81U (ROI -5.7%), mean edge 9.9%, mean ask 50.6c; 1U: -18.01U (ROI -5.5%)
  - BBO-priced rows only: 196 bets 84-112, -52.53U (ROI -15.1%), mean edge 9.4%, mean ask 50.6c
- qualified v7: 314 bets 147-167, -55.88U (ROI -10.5%), mean edge 10.7%, mean ask 50.5c; 1U: -23.74U (ROI -7.6%)
  - BBO-priced rows only: 178 bets 75-103, -68.59U (ROI -21.7%), mean edge 9.8%, mean ask 50.6c
- qualified agreement: both bet 236, same side 218, only v6 92, only v7 78
- qualified dyn v7 - v6: ROI -4.8% (95% CI -16.9% to +6.3%, P(diff <= 0) 0.80); PnL -25.07U (CI -88.65 to +33.62); 20 day clusters
- qualified u1 v7 - v6: ROI -2.1% (95% CI -12.5% to +8.0%, P(diff <= 0) 0.65); PnL -5.74U (CI -38.14 to +26.48); 20 day clusters
- main v6: 0 bets; 1U: +0.00U (ROI n/a)
  - BBO-priced rows only: 0 bets
- main v7: 0 bets; 1U: +0.00U (ROI n/a)
  - BBO-priced rows only: 0 bets
- main agreement: both bet 0, same side 0, only v6 0, only v7 0

### moneyline

- flat v6: 147 bets 73-74, +2.83U (ROI +1.2%), mean edge 8.1%, mean ask 53.4c; 1U: -5.43U (ROI -3.7%)
  - BBO-priced rows only: 91 bets 42-49, -9.43U (ROI -6.6%), mean edge 7.4%, mean ask 54.0c
- flat v7: 190 bets 93-97, -20.33U (ROI -7.0%), mean edge 7.7%, mean ask 56.1c; 1U: -18.46U (ROI -9.7%)
  - BBO-priced rows only: 122 bets 59-63, -18.20U (ROI -9.8%), mean edge 6.9%, mean ask 56.0c
- flat v7m: 213 bets 106-107, -42.36U (ROI -13.4%), mean edge 7.9%, mean ask 59.2c; 1U: -29.96U (ROI -14.1%)
  - BBO-priced rows only: 124 bets 59-65, -34.22U (ROI -18.3%), mean edge 7.5%, mean ask 58.3c
- flat agreement: both bet 141, same side 139, only v6 6, only v7 49
- flat dyn v7 - v6: ROI -8.3% (95% CI -17.2% to +0.1%, P(diff <= 0) 0.97); PnL -23.15U (CI -49.63 to +0.55); 19 day clusters
- flat u1 v7 - v6: ROI -6.0% (95% CI -14.6% to +2.4%, P(diff <= 0) 0.92); PnL -13.03U (CI -30.98 to +3.14); 19 day clusters
- qualified v6: 107 bets 52-55, +7.97U (ROI +4.2%), mean edge 10.4%, mean ask 50.4c; 1U: -0.29U (ROI -0.3%)
  - BBO-priced rows only: 65 bets 31-34, -1.53U (ROI -1.3%), mean edge 9.7%, mean ask 50.9c
- qualified v7: 138 bets 64-74, -15.54U (ROI -6.6%), mean edge 9.9%, mean ask 53.3c; 1U: -13.68U (ROI -9.9%)
  - BBO-priced rows only: 85 bets 38-47, -16.32U (ROI -10.9%), mean edge 9.0%, mean ask 53.3c
- qualified v7m: 154 bets 69-85, -37.59U (ROI -14.7%), mean edge 10.3%, mean ask 55.8c; 1U: -25.19U (ROI -16.4%)
  - BBO-priced rows only: 93 bets 42-51, -29.44U (ROI -18.9%), mean edge 9.4%, mean ask 56.3c
- qualified agreement: both bet 100, same side 100, only v6 7, only v7 38
- qualified dyn v7 - v6: ROI -10.8% (95% CI -21.3% to -1.3%, P(diff <= 0) 0.99); PnL -23.51U (CI -45.85 to -2.66); 17 day clusters
- qualified u1 v7 - v6: ROI -9.6% (95% CI -21.1% to +1.5%, P(diff <= 0) 0.95); PnL -13.39U (CI -28.00 to +0.14); 17 day clusters
- main v6: 0 bets; 1U: +0.00U (ROI n/a)
  - BBO-priced rows only: 0 bets
- main v7: 0 bets; 1U: +0.00U (ROI n/a)
  - BBO-priced rows only: 0 bets
- main v7m: 0 bets; 1U: +0.00U (ROI n/a)
  - BBO-priced rows only: 0 bets
- main agreement: both bet 0, same side 0, only v6 0, only v7 0

### all

- flat v6: 768 bets 375-393, -28.36U (ROI -2.4%), mean edge 9.4%, mean ask 51.4c; 1U: -34.38U (ROI -4.5%)
  - BBO-priced rows only: 452 bets 207-245, -69.46U (ROI -9.7%), mean edge 8.6%, mean ask 51.6c
- flat v7: 899 bets 425-474, -116.31U (ROI -8.7%), mean edge 9.1%, mean ask 52.0c; 1U: -75.90U (ROI -8.4%)
  - BBO-priced rows only: 512 bets 237-275, -101.05U (ROI -13.0%), mean edge 7.3%, mean ask 52.2c
- flat agreement: both bet 761, same side 622, only v6 7, only v7 138
- flat dyn v7 - v6: ROI -6.3% (95% CI -12.6% to +0.9%, P(diff <= 0) 0.96); PnL -87.94U (CI -175.43 to +2.65); 20 day clusters
- flat u1 v7 - v6: ROI -4.0% (95% CI -10.2% to +3.0%, P(diff <= 0) 0.87); PnL -41.52U (CI -95.57 to +15.91); 20 day clusters
- qualified v6: 435 bets 209-226, -22.83U (ROI -3.1%), mean edge 10.0%, mean ask 50.6c; 1U: -18.29U (ROI -4.2%)
  - BBO-priced rows only: 261 bets 115-146, -54.06U (ROI -11.7%), mean edge 9.5%, mean ask 50.7c
- qualified v7: 452 bets 211-241, -71.42U (ROI -9.3%), mean edge 10.4%, mean ask 51.4c; 1U: -37.42U (ROI -8.3%)
  - BBO-priced rows only: 263 bets 113-150, -84.90U (ROI -18.3%), mean edge 9.6%, mean ask 51.5c
- qualified agreement: both bet 336, same side 318, only v6 99, only v7 116
- qualified dyn v7 - v6: ROI -6.2% (95% CI -16.7% to +3.4%, P(diff <= 0) 0.89); PnL -48.59U (CI -126.73 to +22.42); 20 day clusters
- qualified u1 v7 - v6: ROI -4.1% (95% CI -13.7% to +5.1%, P(diff <= 0) 0.80); PnL -19.12U (CI -61.23 to +21.09); 20 day clusters
- main v6: 0 bets; 1U: +0.00U (ROI n/a)
  - BBO-priced rows only: 0 bets
- main v7: 0 bets; 1U: +0.00U (ROI n/a)
  - BBO-priced rows only: 0 bets
- main agreement: both bet 0, same side 0, only v6 0, only v7 0

### Window since_2026-11-12 (v6 artifact out of sample)

- spread flat v6: 340 bets 170-170, -19.47U (ROI -3.6%), mean edge 9.2%, mean ask 51.0c; 1U -7.26U
- spread flat v7: 373 bets 172-201, -73.82U (ROI -13.2%), mean edge 7.4%, mean ask 51.0c; 1U -36.05U
  - v7 - v6 dyn: ROI -9.5% (95% CI -20.6% to +1.8%); PnL -54.35U; 10 days
  - v7 - v6 u1: ROI -7.5% (95% CI -20.1% to +4.4%); PnL -28.78U; 10 days
- spread qualified v6: 186 bets 84-102, -39.38U (ROI -12.1%), mean edge 9.6%, mean ask 50.6c; 1U -20.09U
- spread qualified v7: 166 bets 69-97, -65.41U (ROI -22.4%), mean edge 9.7%, mean ask 50.6c; 1U -29.94U
  - v7 - v6 dyn: ROI -10.2% (95% CI -28.0% to +6.3%); PnL -26.02U; 10 days
  - v7 - v6 u1: ROI -7.2% (95% CI -22.4% to +8.3%); PnL -9.84U; 10 days
- moneyline flat v6: 90 bets 45-45, +5.86U (ROI +4.3%), mean edge 7.0%, mean ask 54.4c; 1U -3.86U
- moneyline flat v7: 117 bets 57-60, -12.11U (ROI -6.9%), mean edge 6.5%, mean ask 56.2c; 1U -12.79U
  - v7 - v6 dyn: ROI -11.2% (95% CI -20.4% to +0.1%); PnL -17.97U; 10 days
  - v7 - v6 u1: ROI -6.6% (95% CI -16.2% to +5.3%); PnL -8.93U; 10 days
- moneyline qualified v6: 65 bets 35-30, +14.34U (ROI +12.8%), mean edge 9.1%, mean ask 51.7c; 1U +4.62U
- moneyline qualified v7: 80 bets 38-42, -7.15U (ROI -5.2%), mean edge 8.6%, mean ask 53.9c; 1U -7.82U
  - v7 - v6 dyn: ROI -18.0% (95% CI -26.0% to -9.1%); PnL -21.49U; 10 days
  - v7 - v6 u1: ROI -16.9% (95% CI -24.3% to -8.1%); PnL -12.45U; 10 days

### Window since_2026-11-16 (v7 engine serving)

- spread flat v6: 203 bets 101-102, -13.74U (ROI -4.2%), mean edge 9.3%, mean ask 51.2c; 1U -6.41U
- spread flat v7: 223 bets 110-113, -26.89U (ROI -8.1%), mean edge 6.8%, mean ask 51.1c; 1U -8.34U
  - v7 - v6 dyn: ROI -4.0% (95% CI -19.7% to +10.8%); PnL -13.15U; 6 days
  - v7 - v6 u1: ROI -0.6% (95% CI -20.1% to +13.7%); PnL -1.92U; 6 days
- spread qualified v6: 113 bets 52-61, -21.94U (ROI -10.8%), mean edge 9.6%, mean ask 50.7c; 1U -10.99U
- spread qualified v7: 90 bets 42-48, -24.96U (ROI -15.6%), mean edge 9.6%, mean ask 50.7c; 1U -7.63U
  - v7 - v6 dyn: ROI -4.8% (95% CI -31.9% to +19.8%); PnL -3.02U; 6 days
  - v7 - v6 u1: ROI +1.2% (95% CI -23.0% to +23.5%); PnL +3.36U; 6 days
- moneyline flat v6: 54 bets 32-22, +13.49U (ROI +15.9%), mean edge 7.3%, mean ask 55.5c; 1U +5.87U
- moneyline flat v7: 72 bets 41-31, +6.63U (ROI +5.9%), mean edge 6.7%, mean ask 55.5c; 1U +3.70U
  - v7 - v6 dyn: ROI -10.0% (95% CI -24.0% to +5.3%); PnL -6.86U; 6 days
  - v7 - v6 u1: ROI -5.7% (95% CI -19.2% to +10.8%); PnL -2.17U; 6 days
- moneyline qualified v6: 40 bets 25-15, +16.57U (ROI +23.4%), mean edge 9.3%, mean ask 52.1c; 1U +8.95U
- moneyline qualified v7: 50 bets 27-23, +4.26U (ROI +4.7%), mean edge 8.8%, mean ask 53.8c; 1U +1.33U
  - v7 - v6 dyn: ROI -18.6% (95% CI -35.9% to -6.9%); PnL -12.32U; 6 days
  - v7 - v6 u1: ROI -19.7% (95% CI -33.8% to -7.1%); PnL -7.62U; 6 days

## Probability quality (D1 rows with a two-sided archived quote; frame = P(home covers) / P(home wins))

- spread v6: {'n': 392, 'market_brier': 0.24784, 'market_logloss': 0.68834, 'v6_brier': 0.27582, 'v6_logloss': 0.75322}
- spread v7: {'n': 392, 'market_brier': 0.24784, 'market_logloss': 0.68834, 'v7_brier': 0.2697, 'v7_logloss': 0.73434}
- spread v6 since 11-16: {'n': 223, 'market_brier': 0.24794, 'market_logloss': 0.68882, 'v6_brier': 0.27593, 'v6_logloss': 0.75609}
- spread v7 since 11-16: {'n': 223, 'market_brier': 0.24794, 'market_logloss': 0.68882, 'v7_brier': 0.26457, 'v7_logloss': 0.72408}
- moneyline v6: {'n': 190, 'market_brier': 0.20909, 'market_logloss': 0.59241, 'v6_brier': 0.22499, 'v6_logloss': 0.63914}
- moneyline v7: {'n': 190, 'market_brier': 0.20909, 'market_logloss': 0.59241, 'v7_brier': 0.22657, 'v7_logloss': 0.64071}
- moneyline v7m: {'n': 190, 'market_brier': 0.20909, 'market_logloss': 0.59241, 'v7m_brier': 0.22891, 'v7m_logloss': 0.64505}
- moneyline v6 since 11-16: {'n': 70, 'market_brier': 0.25217, 'market_logloss': 0.7003, 'v6_brier': 0.24928, 'v6_logloss': 0.69498}
- moneyline v7 since 11-16: {'n': 70, 'market_brier': 0.25217, 'market_logloss': 0.7003, 'v7_brier': 0.2577, 'v7_logloss': 0.71394}

## As logged vs replayed (deduplicated decisions, by logged model id | engine | hash)

| key | n | logged W-L | logged U | logged 1U | replay engine | replay flat | same side | logged 0U rows | logged staked U | replay staked U (same side) | same side 1U logged ask | same side 1U replay price |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ncaab-spread-v5|other|hash=False|moneyline | 5 | 3-2 | +0.89 | +0.89 | v6 (logged by other; not the same model) | 3 bets 2-1, +2.04U (ROI +46.1%), mean edge 7.8%, mean ask 48.4c | 2 | 0 | 5.0 | 3.0 | +2.10 | +2.10 |
| ncaab-spread-v5|other|hash=False|spread | 9 | 4-5 | -2.76 | -1.38 | v6 (logged by other; not the same model) | 9 bets 4-5, -0.47U (ROI -3.4%), mean edge 11.8%, mean ask 51.7c | 9 | 0 | 18.0 | 13.83 | -1.38 | -1.38 |
| ncaab-spread-v6|v6-prefix|hash=False|moneyline | 50 | 30-20 | +3.08 | +3.07 | v6 (logged by v6-prefix; not the same model) | 18 bets 9-9, -3.22U (ROI -11.1%), mean edge 7.8%, mean ask 53.2c | 12 | 0 | 50.0 | 21.25 | -0.61 | -0.61 |
| ncaab-spread-v6|v6-prefix|hash=False|spread | 90 | 49-41 | +12.80 | +6.25 | v6 (logged by v6-prefix; not the same model) | 85 bets 44-41, +13.45U (ROI +11.7%), mean edge 7.8%, mean ask 50.8c | 74 | 0 | 164.39 | 103.86 | +4.59 | +4.51 |
| ncaab-spread-v6|v6|hash=False|moneyline | 122 | 82-40 | -12.81 | -12.82 | v6 | 36 bets 13-23, -7.63U (ROI -14.6%), mean edge 6.6%, mean ask 52.8c | 36 | 0 | 122.0 | 52.08 | -9.49 | -9.73 |
| ncaab-spread-v6|v6|hash=False|spread | 150 | 74-76 | +3.48 | -4.39 | v6 | 137 bets 69-68, -5.73U (ROI -2.8%), mean edge 8.9%, mean ask 50.7c | 137 | 9 | 209.4 | 207.51 | -1.01 | -0.85 |
| ncaab-spread-v6|v7|hash=False|moneyline | 59 | 32-27 | -3.41 | -1.41 | v7 | 59 bets 32-27, -3.68U (ROI -4.0%), mean edge 6.7%, mean ask 55.9c | 59 | 4 | 55.0 | 91.32 | -1.41 | -1.37 |
| ncaab-spread-v6|v7|hash=False|spread | 186 | 91-95 | -6.47 | -7.47 | v7 | 186 bets 90-96, -29.28U (ROI -10.4%), mean edge 7.5%, mean ask 50.8c | 186 | 35 | 201.14 | 282.21 | -7.47 | -9.54 |
| ncaab-spread-v7|v7|hash=True|moneyline | 13 | 9-4 | +5.08 | +5.08 | v7 | 13 bets 9-4, +10.31U (ROI +50.1%), mean edge 6.9%, mean ask 53.6c | 13 | 0 | 13.0 | 20.57 | +5.08 | +5.08 |
| ncaab-spread-v7|v7|hash=True|spread | 37 | 20-17 | +2.19 | +2.17 | v7 | 37 bets 20-17, +2.39U (ROI +4.8%), mean edge 3.6%, mean ask 52.5c | 37 | 11 | 35.13 | 49.67 | +2.17 | +1.21 |
| ncaab-tempo-v2|other|hash=False|moneyline | 69 | 14-55 | -20.79 | -20.79 | v6 (logged by other; not the same model) | 17 bets 11-6, +8.82U (ROI +30.0%), mean edge 12.1%, mean ask 48.7c | 14 | 0 | 69.0 | 24.59 | +3.79 | +3.98 |
| ncaab-tempo-v2|other|hash=False|spread | 86 | 40-46 | -13.93 | -7.70 | v6 (logged by other; not the same model) | 59 bets 32-27, +9.55U (ROI +10.8%), mean edge 12.0%, mean ask 50.6c | 55 | 0 | 165.04 | 83.31 | +2.08 | +2.20 |
| ncaab-tempo-v3|other|hash=False|moneyline | 177 | 26-151 | -61.69 | -61.69 | v6 (logged by other; not the same model) | 19 bets 6-13, -10.68U (ROI -37.8%), mean edge 9.5%, mean ask 53.7c | 11 | 0 | 177.0 | 18.0 | -4.72 | -4.72 |
| ncaab-tempo-v3|other|hash=False|spread | 174 | 85-89 | -13.28 | -6.24 | v6 (logged by other; not the same model) | 128 bets 52-76, -34.25U (ROI -18.2%), mean edge 11.5%, mean ask 50.8c | 110 | 0 | 329.34 | 164.67 | -15.66 | -15.66 |

## Best-pick tracker (best_pick_audit.csv + widget grades.json / superseded.json)

| label | counted rows | graded | W-L | widget U | slate dates | ledger engine behind the rows | grade mismatches |
|---|---|---|---|---|---|---|---|
| NCAAB ML v6 | 2 | 1 | 1-0 | +1.56 | 2026-11-19 | {'v7': 1} | 0 |
| NCAAB ML v7 | 7 | 5 | 4-1 | +3.41 | 2026-11-21 | {'v7': 5} | 0 |
| NCAAB spread v6 | 12 | 12 | 6-6 | -1.88 | 2026-11-16, 2026-11-17, 2026-11-18, 2026-11-19, 2026-11-20 | {'v7': 12} | 0 |
| NCAAB spread v7 | 29 | 15 | 12-3 | +11.57 | 2026-11-21 | {'v7': 15} | 0 |

## Caveats

- Most asks are indicative: before 2026-11-13 about two thirds of rows have no archived BBO for the logged line (complement pricing), and the archived BBOs themselves are not 'executable' by the forward's own test: NCAAB snapshots carry `transact_time_utc: null`, so `college_contract_evidence._valid_quote` rejects nearly all of them as `invalid_time` (candidate packets 2026-11-2x: ~1,600-2,500 invalid_time vs 3-14 matched per run). That is why the ledger's NCAAB rows are `indicative_slate`.
- The v6 artifact's round-2 holdout ran to 2026-11-11 and was mostly this season's opening fortnight, so v6 is partly in sample before 2026-11-12; the `since_2026-11-12` window is the fair one for v6.
- Dedup sensitivity: `--dedup first` changes only 5 of 1,227 game-markets (settled rows are already one decision per run; the superseded re-runs are excluded), and the totals are identical to the latest-row dedup.
- 20 day clusters overall, 6-10 in the late windows: every CI is wide and the late-window CIs are not reliable even where they exclude zero; a 15-bet or one-day record says nothing either way.
- Settlement bug seen in the grade cross-check (pick 53c03c58cbaa4a05, Samford @ South Carolina Upstate, 2026-11-18, 0U row): `cli/settle.py::_find_espn_result` also name-matched UNC Greensboro Spartans @ Gardner-Webb Runnin' Bulldogs through the inverted branch (mascots 'Spartans'/'Bulldogs'), and the start-time tie-break picked it because the Polymarket start (23:30Z) sat closer to that game (01:00Z) than to the real one (01:30Z). The row was settled 78-72 as a win; the real final was 64-74, a loss.

## Game-markets with conflicting logged side/line (latest kept)

- Northern Kentucky Norse @ UTEP Miners spread: main ncaab-tempo-v2 2026-11-02T16:33 away 10.5 win; main ncaab-tempo-v2 2026-11-02T16:33 away 8.5 win; main ncaab-tempo-v2 2026-11-02T16:33 away 6.5 win; main ncaab-tempo-v2 2026-11-02T16:33 away 4.5 win; main ncaab-tempo-v2 2026-11-02T16:33 away 2.5 win; main ncaab-tempo-v2 2026-11-02T16:33 away 1.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 21.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 24.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 18.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 16.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 14.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 12.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 10.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 8.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 6.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 4.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 2.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away 1.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away -3.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away -5.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away -7.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away -9.5 loss; flat ncaab-tempo-v2 2026-11-02T16:33 home 19.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away -11.5 loss; flat ncaab-tempo-v2 2026-11-02T16:33 home 22.5 win; flat ncaab-tempo-v2 2026-11-02T16:33 away -13.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away -3.5 win; main ncaab-tempo-v2 2026-11-02T17:42 away -16.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away -9.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away -22.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away -13.5 loss
- UTSA Roadrunners @ San José State Spartans spread: main ncaab-tempo-v2 2026-11-02T17:42 home -27.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 home -20.5 win; main ncaab-tempo-v2 2026-11-02T17:42 away 12.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away 10.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away 8.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away 7.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away 6.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away 4.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away 2.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away -3.5 loss; main ncaab-tempo-v2 2026-11-02T17:42 away -13.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 home -27.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 home -20.5 win; flat ncaab-tempo-v2 2026-11-02T17:42 away 12.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 away 10.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 away 8.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 away 7.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 away 6.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 away 4.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 away 2.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 away -3.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 away -13.5 loss
- UMBC Retrievers @ George Mason Patriots spread: main ncaab-tempo-v2 2026-11-02T17:42 home -20.5 win; main ncaab-tempo-v2 2026-11-02T17:42 home -14.5 win; flat ncaab-tempo-v2 2026-11-02T17:42 home -44.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 home -38.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 home -32.5 loss; flat ncaab-tempo-v2 2026-11-02T17:42 home -26.5 win; flat ncaab-tempo-v2 2026-11-02T17:42 home -20.5 win; flat ncaab-tempo-v2 2026-11-02T17:42 home -14.5 win; main ncaab-tempo-v2 2026-11-02T17:45 home -16.5 win; main ncaab-tempo-v2 2026-11-02T17:45 home -12.5 win; main ncaab-tempo-v2 2026-11-02T17:45 away 10.5 loss; main ncaab-tempo-v2 2026-11-02T17:45 away 8.5 loss; main ncaab-tempo-v2 2026-11-02T17:45 away 6.5 loss; main ncaab-tempo-v2 2026-11-02T17:45 away 2.5 loss; main ncaab-tempo-v2 2026-11-02T17:45 away 4.5 loss; main ncaab-tempo-v2 2026-11-02T17:45 away -5.5 loss; flat ncaab-tempo-v2 2026-11-02T17:45 home -16.5 win; flat ncaab-tempo-v2 2026-11-02T17:45 home -12.5 win; flat ncaab-tempo-v2 2026-11-02T17:45 away 10.5 loss; flat ncaab-tempo-v2 2026-11-02T17:45 away 8.5 loss; flat ncaab-tempo-v2 2026-11-02T17:45 away 6.5 loss; flat ncaab-tempo-v2 2026-11-02T17:45 away 2.5 loss; flat ncaab-tempo-v2 2026-11-02T17:45 away 4.5 loss; flat ncaab-tempo-v2 2026-11-02T17:45 away -5.5 loss; main ncaab-tempo-v2 2026-11-02T18:56 away 19.5 loss; flat ncaab-tempo-v2 2026-11-02T18:56 away 19.5 loss
- Radford Highlanders @ California spread: main ncaab-tempo-v3 2026-11-05T00:56 away 16.5 win; flat ncaab-tempo-v3 2026-11-05T00:56 away 16.5 win; main ncaab-tempo-v3 2026-11-05T01:33 away 15.5 win; flat ncaab-tempo-v3 2026-11-05T01:33 away 15.5 win
- Florida A&M Rattlers @ UTEP Miners spread: flat ncaab-spread-v6 2026-11-15T11:15 away 19.5 loss; flat ncaab-spread-v6 2026-11-16T17:15 away 20.5 loss

## Ledger result vs final score mismatches

- {'pick_id': '53c03c58cbaa4a05', 'game': 'Samford Bulldogs @ South Carolina Upstate Spartans', 'market': 'spread', 'selection': 'home', 'line': 6.5, 'ledger_result': 'win', 'final': [64, 74]}

## Payload score vs ESPN history mismatches

- {'pick_id': '53c03c58cbaa4a05', 'game': 'Samford Bulldogs @ South Carolina Upstate Spartans', 'day': '2026-11-18', 'history': [64, 74], 'payload': ['78', '72']}

