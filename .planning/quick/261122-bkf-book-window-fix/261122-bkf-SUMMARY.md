---
quick_id: 261122-bkf
status: complete
date: 2026-11-22
---

# Summary: critical-review fix to the settled-replay decision book + forward scorecard (ralph round 20)

- `critical-reviewer` (independent) found a blocker: `settled_common.decision_book` merged markets last
  quoted hours apart (Denver @ Houston 2026-11-09 mixed 10:20Z and 21:04Z rungs), creating fake
  ladder gaps. Fixed: one capture per book (markets within 15 min of the newest), cutoff
  min(ledger tip, record start) - 5 min, timestamp-invalid records dropped.
- Re-measured every book-dependent replay. Round-13 rule (moneyline vs spread ladder) NBA: 13
  signals 12-1, +6.75u net, all five weeks positive, but 12 of 13 are favourites (11 wins vs 7.5
  expected at the asks); NCAAB -3.21u (19, all favourites), WNBA +1.16u (17). Too few games and not
  confirmed out of sample; the goal is not met.
- Added `scripts/nba_v10/ml_vs_ladder_forward.py`: the rule frozen 2026-11-22T12:00Z with a
  pinned verdict (PENDING < 60 signals; PASS needs every week with >= 5 signals positive and the
  net-ROI CI above 0). Currently PENDING, 0 forward signals.
- Open reviewer minors (not fixed): event mapping can attach one event to two games and accepts
  +/-1 day in the first pass; tb_settled_replay compares DraftKings to the newest quote, not each
  quote; pm_momentum's own tip-minute fallback; the empirical survival is step-flat (coarse fair grid).
