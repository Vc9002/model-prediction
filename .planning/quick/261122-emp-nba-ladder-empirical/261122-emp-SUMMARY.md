---
quick_id: 261122-emp
status: complete
date: 2026-11-22
---

# Summary: coverage-complete loaders + empirical-shape ladder model (ralph round 8)

- `scripts/nba_v10/settled_common.py`: nickname team resolution ("Hawks" -> "Atlanta Hawks"),
  date+team event mapping (incl. single-team events), any-age last pre-tip book, and a harness over
  every settled NBA row: 655/657 asks rebuilt, 735/735 results regraded. Coverage 196 of 219
  settled games; the other 23 have no archived Polymarket event (opening day 10-20, the whole 11-03
  slate, a few others).
- `scripts/nba_v10/ladder_empirical_replay.py`: ladder mean + empirical closing-residual shape
  (2007-26, ATS SD 12.43, total SD 17.76). 146 picks 98-48, +0.40u gross, -2.44u net; weeks net
  +2.70 / -3.14 / -2.61 / -1.29 / +1.90. Dead. Round 7's sigma 13.79 overstated history's 12.43.
