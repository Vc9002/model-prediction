---
phase: quick-261121-v7r
plan: 01
status: complete
branch: ncaaf-v7route-261121 (worktree E:\MP-wt\ncaaf-v7route)
commits: d3b5dc9a (feature), a1e986f6 (tests)
---

# quick-261121-v7r SUMMARY: more than one challenger per sport/market; v7 routed beside v6.2

## Routing after this change

| model | role | where its NCAAF rows go | changed? |
|---|---|---|---|
| cfb-structural-v5 | champion, all three markets | Main (+ gated, research, Flat), auto-buyer whitelist | no. Pricing unchanged; `code_revision` changes because `college_football.py` is in its hash |
| cfb-structural-v6.2 | primary challenger (first in each list) | research + Flat only | no. Same call, same event dicts, same contracts; `cfb_structural_v62.py` untouched |
| cfb-structural-v7 | second challenger | research + Flat only, zero Main units, never the buyer | new. Live once `price_v7_slate` (quick-261121-v7l) is merged |

## What was built

- Schema: `challengers.<SPORT>.<market>` is a model id, a list of ids, or null. The first id is the primary
  (lifecycle contract, `registry.challenger()`, qualification) with no fall-through. `prospective_challenger`
  and `research_candidates` are never routes.
- `production_registry.py`: `challenger_route_ids`, `_resolve_challenger_routes`, `challengers_for`,
  `challenger_problems`, `remove_challenger_route`. Each listed challenger is checked on its own: registered,
  available, contract covers the route, not the champion, not listed twice, a string. None of these raises. A
  non-mapping sport value is reported; before, `dict(None)` raised and took the registry down. A list route no
  longer raises TypeError in contract construction. On today's config, `challenger()`, `champion()` and every
  lifecycle contract are byte-identical before and after the change.
- `model_promotion.py`: `promote` and `reject_challenger` remove one id from a list. A list collapses to a
  scalar when one id is left and is deleted when none is. The old `==` never matched a list.
- `college_football.build_cfb_slate`: routes come from the registry. Each challenger runs in its own
  try/except, v6.2 first. `challenger_pricing` (v6.2) is kept and `challenger_pricing_by_model` is added.
- `models/cfb_structural_v7_challenger.py`: v6.2's quote rules apply. Only a captured `polymarket_us` book
  observed at or before the decision is used, on the max-edge side, and a market is emitted only when edge
  > 0. Rows carry v7's own model id, an artifact hash checked against the registry entry, and
  `v7_serving_code_hash()`. Live data comes through `cfb_structural_v7.price_v7_slate(data_root,
  decision_time, games, *, model)`. While that function is absent: `NO_CALL_V7_LIVE_DATA_UNWIRED`, nothing
  written.
- Slug hygiene: v7 rows carry the real contract slug as `market_slug=<slug>`, with the moneyline slug
  unsuffixed and no parenthesised token. On the written rows, `_extract_market_slug`, `ncaaf_settlement_slug`,
  `_row_slug` and the ticket-gate regex all return the contract slug. All 3,731 real NCAAF slugs in the last
  six runtime snapshot days match the reader regex. v6.2's slug and rationale format is unchanged.

## Verification

- Targeted pytest, without MODEL_PREDICTION_* env: 292 tests across the cfb, college, ncaaf, registry,
  lifecycle, promotion, canary, system_health and qualification files. 290 passed; 2 failed, both already
  failing at HEAD (`test_qualification_integrity`: mlb-moneyline-v9-frozen, module-import). The new file
  passes 14/14, and `tests/research/test_ready_challenger_pairing.py` also passes.
- Mutation proofs, each caught by a test: `==` in reject and in promote; a moneyline `-home` suffix; a
  parenthesised rationale token; the captured-book guard off; a quote seen after the decision allowed; a
  missing seam result; no pbp check; primary fall-through; the old list `chall_id`; a single shared try/except
  in the hook.
- Merge check: `git merge-tree` of HEAD with v7l's 75627175 is clean. On the merged tree, my tests, v7l's
  `test_cfb_pbp_live.py`, v7, v6.2 and registry pass 68/68. A smoke run through the real `price_v7_slate`
  against an empty store no-calls with `NO_CALL_V7_PBP_MISSING` and writes nothing.
- `ruff check src/ tests/` is clean. mypy is clean on the four changed modules.

## Notes for the operator

- Merging this branch and v7l starts v7 Flat/research rows with no further switch. Rolling back is a one-line
  yaml edit (drop v7 from the lists) or `model_promotion reject_challenger`.
- v7 prices inside `build_cfb_slate` before any NCAAF ledger write. Its errors are isolated from v5 and v6.2;
  its runtime is not. The rating state is fitted once per slate.
- v6.2 must stay first in each list, because its own `routed_markets()` reads the primary.
- The qualification registry still tracks only the primary challenger (v6.2).
- `challenger_problems` now lists four pre-existing unregistered ids. They were already unserved:
  `mlb-structural-v11-residual`, `wnba-moneyline-v5`, `wnba-spread-structural-v3`, `wnba-total-possession-v3`.
- The `cfb_structural_v7.py` module docstring still says "registered but not routed". That file belongs to
  v7l, so it was not edited here.
