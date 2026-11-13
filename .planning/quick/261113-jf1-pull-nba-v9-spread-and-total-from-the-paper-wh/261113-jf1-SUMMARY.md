---
status: complete
quick_id: 261113-jf1
---

# Pull NBA v9 spread (and total) from the paper auto-buyer whitelist

Executed inline by the orchestrating session. Commit `c648e048` (only this session's hunk of
`auto_executor.py` was staged; another session's cfb-structural-v5 whitelist hunk stays unstaged).

- `DEFAULT_WHITELIST_MODELS`: `nba-spread-margin-v9` removed with the loss-review evidence in the
  comment. `nba-total-margin-v9` had already been removed and `("nba","total")` categorically
  disabled by quick-261113-r2d (`c3522f2a`).
- Persisted runtime state `E:\model-prediction-runtime\auto_buyer_state.json`: whitelist edited
  through `load_auto_buyer_state` / `save_auto_buyer_state` (versioned atomic save, 175 -> 176),
  backup `auto_buyer_state.json.pre-nba-v9-whitelist-pull-20261113-135938.bak`, key superset
  verified. The persisted list had already dropped the total model; only the spread model was
  pulled here. Effective NBA entries left: `nba-elo-trend-lr-v4` (persisted only; the serving
  moneyline champion is now v5, which is not whitelisted, so no NBA market is buyable).
- Tests: `test_auto_polymarket_buyer` asserts no `nba-` model in the default whitelist;
  `test_audit_fixes_regression` NBA spread/total tests now assert the champion is NOT whitelisted
  (they still fail earlier on their pre-existing stale version-set pins). The three
  `nba-moneyline-v9` fixture tests were already red before this task.
- Not changed: the models keep serving forecasts to Main/Flat; `config/production.yaml` untouched.

Verification owed: the next scheduled auto-buyer cycle must show NBA spread candidates under
`rejected_not_whitelisted` and zero NBA orders.
