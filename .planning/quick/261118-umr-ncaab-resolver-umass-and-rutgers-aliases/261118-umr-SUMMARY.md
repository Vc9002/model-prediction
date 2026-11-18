---
quick_id: 261118-umr
description: NCAAB resolver: school-only UMass and Rutgers stop resolving to UMass Boston / Rutgers-Camden
status: complete
completed: 2026-11-18
commits: 1
---
# Quick 261118-umr: UMass and Rutgers aliases

## Commit

| Commit | Scope |
| --- | --- |
| 54e0028b | `_NCAAB_ALIASES`: `umass`, `umass minutemen`, `rutgers` + regression test |

## Evidence

- Live-history probe before: `UMass` -> `UMass Boston Beacons`, `UMass`+`Minutemen` -> `UMass Boston Beacons`, `Rutgers` -> `Rutgers-Camden RUTGERS-CAMDEN`, `UMass Minutemen` -> unresolved raw string. After: all four -> the flagship (`Massachusetts Minutemen`, `Rutgers Scarlet Knights`). `UMass Boston`, `UMass Lowell`, `Rutgers-Camden`, `Rutgers Camden` still resolve to themselves.
- Sweep of 3,519 history-derived (school, mascot) inputs, before vs after: 4 change. `Rutgers|` and `UMass|` are the intended fixes. `UMass|Boston Beacons` and `UMass|Lowell River Hawks` also now return the flagship: those are artificial first-token splits of the lookalike names; the slate sends the school as `UMass Boston` / `UMass Lowell` (ESPN `location`) or the Polymarket team string, so they do not occur in practice. No other input changed.
- New test fails against the HEAD module loaded from `git show HEAD:` (`'UMass Boston Beacons' == 'Massachusetts Minutemen'`), passes now. `pytest tests/test_college_basketball_forward.py tests/test_ncaab_main_totals.py tests/test_ncaab_tier1_guardrails.py -o addopts='' -q`: 32 passed. `ruff check` and `ruff format --check` clean on both files.

## Not changed

`Oklahoma St`, `St Mary's` and other school-only inputs that collide onto non-D1 schools (they decline, not mispriced); the real-money matcher; the 30 contaminated settled ledger rows (UConn 14, UMass/Rutgers 16, net -20.06U), still awaiting an explicit archive decision.
