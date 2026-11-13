---
quick_id: 261113-jqw
status: complete
date: 2026-11-13
commits:
  - 4fb785cd (code hunks rode along in a peer's commit via the shared index; content unaltered)
  - docs commit (HEAD of quick task)
---

# Summary — whitelist the serving NBA moneyline champion in the auto-buyer

Operator directive 2026-11-13: "nba ml can stay in auto buyer".

- **Code constant:** `DEFAULT_WHITELIST_MODELS` += `nba-elo-trend-lr-v5`; NBA comment block reworded;
  `test_auto_polymarket_buyer` nba- prefix assertion now expects spread + moneyline. These hunks were
  staged by this session and committed in peer commit `4fb785cd` (perf: bounded dashboard snapshot
  cache) because the index is shared; the peer confirmed nothing was altered.
- **Live runtime state (what the buyer actually reads):** `E:\model-prediction-runtime\auto_buyer_state.json`
  whitelist updated through `load_auto_buyer_state`/`save_auto_buyer_state`, version 179 -> 180; backup
  `auto_buyer_state.json.pre-nba-ml-v5-20261113-191519.bak`. Added `nba-elo-trend-lr-v5`, dropped the
  dead `nba-elo-trend-lr-v4` whitelist entry (it is blacklisted and no longer serves; blacklist untouched).
  Effective config verified via `_base_auto_execution_kwargs_from_state`: v5 whitelisted, mode paper.
- **Remaining gate (operator decision, HANDOFF item 3):** the buyer rejects every `PAPER_CALL_*` row, and
  all NBA moneyline Main rows are `PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED` while v5's `evidence_status` is
  `historical_only` (7 of 7 Main rows 2026-11-12..13). Nothing is bought until that flag changes.
- Verification: ruff clean; `test_auto_polymarket_buyer` fails on the same three tests as HEAD `6cda44d2`
  (edge outliers, win-probability floor, stale-quote network), unrelated to this change.
