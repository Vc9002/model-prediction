---
quick_id: 261113-r2d
status: complete
date: 2026-11-13
commit: c3522f2a
---

# Summary: disable NBA totals in the paper auto-buyer

## What changed

- `src/model_prediction/portfolio/auto_executor.py`: `("nba", "total")` added to
  `DISABLED_AUTO_BUYER_SPORT_MARKETS`; `nba-total-margin-v9` removed from
  `DEFAULT_WHITELIST_MODELS` (not blacklisted; it stays the Main serving champion).
- `tests/test_auto_polymarket_buyer.py`:
  `test_buyer_categorically_rejects_nba_total_even_when_model_is_whitelisted`
  (fails with the gate reverted: `rejected_disabled_sport_market` 0 != 1).
- Live runtime state `E:\model-prediction-runtime\auto_buyer_state.json` updated through
  `load_auto_buyer_state`/`save_auto_buyer_state` (version 173 -> 174): `nba:total` in
  `disabled_sport_markets`, `nba-total-margin-v9` removed from `whitelist_models`. The
  dashboard rebuilds its buyer config from this file each cycle, so the block is live
  without a restart; the code constant applies after the next dashboard restart.

## Evidence

- Paper ledger had 11 settled NBA total rows (7 `nba-total-margin-v7`, 4 `nba-total-margin-v9`),
  all left in place -- deleting settled rows is a ledger mutation for the operator.
- Verification in a detached worktree at c3522f2a: ruff clean; buyer test file 68 passed /
  3 failed, the same 3 as at HEAD~1 and already triaged in `docs/DEBUG.md` (test-suite status).
- `test_audit_fixes_regression::test_nba_total_margin_v2_model_and_forecast` fails before and
  after (champion is v9, test accepts v2-v7) -- pre-existing.

## Found, not fixed

- The live state's `whitelist_models` diverges from code defaults: it still lists
  `nba-elo-trend-lr-v4` (also in its `blacklist_models`, so blacklist wins) and carries
  `ncaaf:*` in `disabled_sport_markets` that the code constant does not. The persisted copy
  wins at runtime; any future "remove model X from the whitelist" code change must also
  edit the state file or it is a no-op in production.
