---
quick_id: 261122-lsh
status: complete
date: 2026-11-22
---

# Summary: favourite-longshot harvest (buy the 85-97c side of ladder rungs), ralph round 6

`scripts/nba_v10/longshot_harvest_replay.py`, report `outputs/nba/LONGSHOT_HARVEST_REPLAY.md`.
Validation on other sports FAILED (pooled net ROI -6.1%, CI -8.7%..-3.7%, 969 picks), and NBA
settled lost too (192 picks 168-24, -6.93u gross, -7.95u net; weeks -3.92 / +0.63 / -4.25 / -0.50
net). Caveat found while debugging: the NFL/MLB snapshot archive mixes period markets (NFL totals at
6.5-12.5, MLB inning/F5 lines) into spread/total, and NCAAB team names do not match, so those
validation sports are contaminated; NBA (harness-verified) and NCAAF look clean.
