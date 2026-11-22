---
quick_id: 261122-ldm
status: complete
date: 2026-11-22
---

# Summary: ladder dispersion model (Polymarket ladder mean + historical sigma), ralph round 7

`scripts/nba_v10/ladder_dispersion_replay.py`, report `outputs/nba/LADDER_DISPERSION_REPLAY.md`.
NBA venue-implied spread sigma median 13.07 vs historical 13.79. NBA settled: 36 picks 16-20,
+32.47u gross / +29.47u net, but two long shots (Rockets -27.5 at 5c, under 193.5 at 10c) are
+28u of it; weeks +23.93 / (none in 11-02) / -5.45 / +10.99 net. Fails "profitable every week" and
the result is lottery-driven. Disclosed: prompted by round 6's in-sample NBA calibration table.
NCAAF check +109.87u net on 145 picks (CI +31%..+130%), unverified grading for NCAAF.
