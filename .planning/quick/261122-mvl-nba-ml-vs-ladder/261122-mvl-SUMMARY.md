---
quick_id: 261122-mvl
status: complete
date: 2026-11-22
---

# Summary: moneyline book vs spread ladder (ralph round 13)

`scripts/nba_v10/ml_vs_ladder_replay.py`, report `outputs/nba/ML_VS_LADDER_REPLAY.md`. Ladder mean
from the spread rungs (empirical history shape), implied P(win) vs the moneyline ask, bet if >= 3
points. NBA: 189 games evaluated, 14 signals 12-2, +5.92u net (CI +5.5%..+74%), every week positive
but only 1-7 bets a week. Independent check with the same frozen rule: NCAAB 25 signals +0.28u net
(one losing week), WNBA 21 signals -0.37u net. Not confirmed; too few NBA games to meet the goal.
