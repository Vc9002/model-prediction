---
quick_id: 261118-umr
description: NCAAB resolver: school-only UMass and Rutgers stop resolving to UMass Boston / Rutgers-Camden
---
# NCAAB resolver aliases: UMass and Rutgers

**Trigger:** the 261118-ke0 audit found school-only `UMass` and `Rutgers` resolve to non-D1 schools (`UMass Boston Beacons`, `Rutgers-Camden RUTGERS-CAMDEN`). Three ledger games (2026-11-03, 11-06, 11-07) were priced as the wrong team, and since 11-15 `decline_non_d1` turns the same inputs into no-picks on real D1 games.

**Root cause (reproduced on the live 804-name history):** the `umass` alias target `umass minutemen` is not a history name (history: `Massachusetts Minutemen`), so the alias missed and token-subset ranking picked `UMass Boston Beacons` (fewest extra tokens). `Rutgers Camden Rutgers Camden` (normalized) has a smaller token diff than `Rutgers Scarlet Knights`, so it won for school-only `Rutgers`.

## Tasks

1. Repoint `umass` to `massachusetts minutemen`, add `umass minutemen` -> same, add `rutgers` -> `rutgers scarlet knights` in `_NCAAB_ALIASES`.
2. Regression test `test_umass_and_rutgers_never_resolve_to_lookalike_schools` (synthetic names incl. the lookalikes).

## Verify

Micro-targeted pytest on the three NCAAB test files, `ruff check` and `ruff format --check` on the two touched files, a before/after sweep of every history-derived input (3,519), and the new test run against the HEAD module (must fail).

## Not in scope

Real-money matcher `polymarket_execute._team_name_matches`; archiving the contaminated ledger rows (operator decision, irreversible, Main ledger); `Oklahoma St` / `St Mary's` school-only aliases.
