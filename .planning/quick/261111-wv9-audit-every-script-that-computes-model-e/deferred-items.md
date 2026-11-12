# Deferred Items — quick task 261111-wv9

Out-of-scope discovery found while running the plan's targeted test file in full
(beyond the plan's own `-k` selection) as an extra sanity check. Not fixed, per the
Scope Boundary rule (only auto-fix issues directly caused by this task's changes).

## `tests/test_auto_polymarket_buyer.py::test_buyer_fails_closed_when_stale_quote_refresh_has_network_error`

- **Symptom:** Fails on the current tree (pre-existing, unrelated to this plan).
  Expected `result.rejected_stale_quote == 1`, got `0`.
- **Root cause (isolated outside pytest):** The pick is rejected with
  `rejected_future_slate=1`, not `rejected_stale_quote`. This is slate-window
  filtering logic, unrelated to MANUAL-row attribution — nothing this plan's three
  tasks touch (`is_manual_auto_buyer_record`, `summarize_auto_buyer_performance`,
  `_get_quarantined_underperforming_models`) can affect `rejected_future_slate`.
- **Confirmed unrelated:** none of the three commits in this plan touch
  `evaluate_and_execute`, the future-slate check, or quote staleness handling.
- **Action:** Not fixed. Flagged here for separate investigation; likely a
  pre-existing drift in the future-slate window or the default event_start_utc
  offset used by the test fixture.
