---
quick_id: 261113-jqw
mode: quick
status: complete
date: 2026-11-13
---

# Quick task 261113-jqw: whitelist the serving NBA moneyline champion in the auto-buyer

Operator directive 2026-11-13: "nba ml can stay in auto buyer".

1. `DEFAULT_WHITELIST_MODELS` += `nba-elo-trend-lr-v5` (serving champion since promo-20261113T184744Z-6b0b74);
   NBA comment block reworded; `test_auto_polymarket_buyer` nba- prefix assertion updated.
2. Live buyer state (`E:\model-prediction-runtime\auto_buyer_state.json`, overrides the code constants):
   append `nba-elo-trend-lr-v5` to `whitelist_models` via `load_auto_buyer_state`/`save_auto_buyer_state`
   (versioned), backup first.
3. Document the remaining gate: the buyer rejects `PAPER_CALL_*` rows and all NBA moneyline Main rows are
   `PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED` while v5's `evidence_status` is `historical_only`, so the entry is
   inert until that flag changes (operator decision, HANDOFF item 3).
