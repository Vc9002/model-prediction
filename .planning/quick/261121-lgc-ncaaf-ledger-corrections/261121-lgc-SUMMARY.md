---
status: complete
---
# 261121-lgc: NCAAF ledger integrity corrections (operator-approved 2026-11-21, "go ahead execute all")

Method: `PickLedger.archive_settled_rows` via `main_ledger`/`flat_ledger`, authority sqlite, runtime root E:\model-prediction-runtime, daily.lock held, ledger and audit backed up before each phase. Exact pick_ids, never a time window.

| Phase | Rows | Item | PnL removed | Backup |
|---|---|---|---|---|
| 1 | 3 Flat | void_reason (fabricated 50% ask, 08-29) still settled | -1.59U | ledgers.db.pre-ncaaf-integrity-2026-11-21-20261121T042945Z.bak |
| 2 | 22 (2 Main, 20 Flat) | priced on another game's Polymarket quote record (10-24, 10-31; incl. Main 5f785b3ec8f3454d ODU @ Louisiana Tech) | +14.90U | ...-phase2-20261121T043247Z.bak |
| 2 | 8 Flat | duplicate copy of a Main pick at a 0.5-1 point different line | -2.39U | same |
| 3 | 1 Flat | 2b9c7715d9304cf5 South Carolina vs Tennessee priced on the Charleston Southern vs Tennessee State contract | +2.00U | ...-phase3-20261121T045456Z.bak |

- Phase 1's first attempt failed on `cfb_replay_engine_identity_mismatch` (archive validated replay strictly); fixed in cde2c4e7 (archive degrades to a warning like settle/void). That attempt left one orphan `settled_pick_archived` audit event (39ad8fec7207483d9bb92386a6c848f6, pick 26e45acf41d047be) before the real one.
- Result: Main settled 1,094 rows -80.24U (was -80.34U); Flat settled 731 rows -78.58U (was -65.56U).
- The two 10-31 quote records are no longer in the snapshot file, so the quote's own game cannot be named; at most one row per record could have been genuine.
- Ole Miss @ Oklahoma 2026-11-15T18:00Z verified against ESPN (Norman, 17-24); three Main legs correctly graded; no change.
- Archive: data/archive/2026-11-21-ncaaf-integrity-rows/ (settled_rows*.jsonl, MANIFEST*.json). Scripts: session scratchpad ncaaf_fix/apply.py, apply2.py, apply3.py.
