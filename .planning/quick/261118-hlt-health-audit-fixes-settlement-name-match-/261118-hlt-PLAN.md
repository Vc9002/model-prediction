---
quick_id: 261118-hlt
description: Health audit fixes: settlement name-match fallback, zero-stake health exemption, quote warehouse bit-flip repair
---
# Whole-project health audit (2026-11-18) and fixes

`system_health` was DEGRADED and the dashboard was down. Root causes, traced read-only first:

1. 46 settled NCAAB flat v6 rows with zero stake: by design (placeholder 50% price, quick 261115-1m3), but `_ledger_economics` did not know the `[UNQUOTED_SHADOW:]` marker.
2. 6 open rows >72h: 2 NCAAB postponed (auto-void at 7 days, correct); 4 Euroleague/hockey wins that `_settle_by_resolution` could not match by name and would have voided.
3. Quote warehouse "stale sgccer/elb": single-bit flips in `market_quotes.db` (table and index pages disagree on 8 rows).
4. Dashboard down since 11-16 (python crash, logon-only task).

## Tasks
1. `_settle_by_resolution`: second pass on distinctive name tokens, fail closed on ties (+ tests, red-proved).
2. `system_health._ledger_economics`: exempt the exact marker (+ test, red-proved).
3. Repair `market_quotes.db` (backup, writer lock, drop/rebuild indexes, fix 2 flipped table values).
4. Restart the dashboard; correct CLAUDE.md's score-ingestion claim.

## Not in scope
Pushing 79 unpushed commits (outward-facing, needs an explicit yes); hardware diagnosis (needs the operator at the machine).
