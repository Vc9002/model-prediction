---
quick_id: 261122-ect
status: complete
date: 2026-11-22
---

# Summary: T-C1 early-season totals (ralph round 15)

`scripts/nba_v10/early_totals_replay.py`, report `outputs/nba/EARLY_TOTALS_REPLAY.md`. Current-season
points for/against shrunk to the league (K=5), vs the Polymarket total ladder. Model MAE 14.44 vs the
ladder's 13.89 (n 154). 120 signals 66-54, +6.82u net (CI -10.8%..+23.2%); weeks net +8.37 / -1.80 /
+7.14 / -6.89. Dead (two losing weeks, model less accurate than the market).
Also found: the round-8 history shape SD 12.43 is era-biased; 2021-22+ ATS residual SD is 13.2-13.7.
