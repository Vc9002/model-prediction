---
quick_id: 261122-srr
status: complete
date: 2026-11-22
---

# Summary: structural residual on the Polymarket ladder (ralph round 5)

`scripts/nba_v10/structural_residual_replay.py`, report `outputs/nba/STRUCTURAL_RESIDUAL_REPLAY.md`.
Ridge on schedule features vs closing-line residuals, train <= 2022-23, validation 2023-24..2025-26:
spread corr +0.036 (CI +0.004..+0.070, PASS), total -0.000 (FAIL). On the settled 2026-27 games
(market mean = Polymarket ladder at the decision quote, PIT): 40 spread picks 18-22, -4.37u gross,
-5.40u net; weeks -1.07 / -4.11 / +1.97 / -0.03 (incl. 13 total picks shown as context). Dead.
