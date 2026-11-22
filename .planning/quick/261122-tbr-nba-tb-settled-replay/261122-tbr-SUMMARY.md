---
quick_id: 261122-tbr
status: complete
date: 2026-11-22
---

# Summary: T-B replay over every settled NBA game

- `scripts/nba_v10/tb_settled_replay.py` (read-only) writes `outputs/nba/TB_SETTLED_REPLAY.md` and
  `outputs/nba/tb_settled_replay_picks.csv`.
- Harness: 261/261 stored Flat v9/lr-v5 asks rebuilt from the snapshot archive, 261/261 results
  regraded.
- Primary T-B (pinned 3.0 pts): 36 picks, +6.67u gross, +5.35u net, CI -19%..+56%, weeks
  +2.26 / +3.56 / -2.00 / -0.09 / +2.95. All 36 use the backfilled DraftKings close (look-ahead);
  zero PIT-clean picks. Not profitable every week even as an upper bound.
- ESPN exposes no timestamped DraftKings movement for 2026-27, so PIT T-B exists only from the live
  capture (2026-11-21 on).
