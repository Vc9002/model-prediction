---
quick_id: 261118-hlt
description: Health audit fixes: settlement name-match fallback, zero-stake health exemption, quote warehouse bit-flip repair
status: complete
completed: 2026-11-18
commits: 2
---
# Quick 261118-hlt: health audit

## Commits
| Commit | Scope |
| --- | --- |
| 16f92f3b | `cli/settle.py` name-token fallback; `system_health.py` UNQUOTED_SHADOW exemption; tests (3 stale fixtures in `test_resolution_settlement.py` also repaired: missing `event_start_utc`, ledger stub signature, void-on-non-binary expectation; those 2 tests already failed at HEAD) |

## Results
- Settlement: dry-run of the fixed code against the real markets (stub ledger) settles all four stale picks as wins (hockey `d287bfd7` away, Efes `e8d1ee09` home, Besiktas `0f1bb992` moneyline and `e4c5162c` +1.5 spread). Without it the 7-day void would have hit ~18:00Z 11-19 (hockey) and 11-20 (EuroLeague). Settlement happens on the next daily run; confirm the four rows leave `open`.
- Health: economics check 46 -> 0 semantic errors; the new test fails against HEAD, and an unmarked zero-stake row still reports.
- `market_quotes.db`: `integrity_check` found 8 rows where table and index disagree. Labels were single-bit flips (`soccer`->`sgccer`, `mlb`->`elb`, event_id `mlb-`->`mdb-`, all bit 0x08). Backup `market_quotes.db.pre-bitflip-repair-20261118T230737Z.bak` (832 MB); indexes dropped/rebuilt, 2 table values restored (siblings prove the spelling); `integrity_check` ok; monitor warehouse status HEALTHY. Non-indexed columns cannot be verified.
- Other databases: full `integrity_check` ok on ledgers.db (1.3 GB), runs.db, production.db, dashboard_cache.db.
- Dashboard restarted via its scheduled task (HTTP 200). It died 11-16 (python.exe crash at 4:25 PM); the task is logon-triggered with no restart.
- Remaining DEGRADED reason: 6 open rows >72h (4 settle on the next daily run, 2 NCAAB postponed rows auto-void ~11-22).

## Hardware suspicion (needs the operator)
Single-bit flips at the same bit position in two DB files' pages, python.exe crashes with module `unknown`/`python312.dll`, the 00:30 memory exhaustion, and a bugcheck 0x139 on 11-07. No WHEA or disk errors logged; all three SSDs report Healthy. RAM is three mixed DIMMs (16 GB G.Skill + 2 x 8 GB Corsair, 3200). Recommended: Windows Memory Diagnostic or MemTest86 overnight, then one-DIMM-at-a-time if errors appear.
