---
phase: quick-261125-hih
plan: 01
subsystem: live auto-buyer / Polymarket US execution
status: in_progress
tags: [real-money, auto-buyer, idempotency, gtd, audit, ledger]
tasks_done: [1, 2, 3]
tasks_open: [4]
requirements_done_task1: [HIH-B1, HIH-B2, HIH-B3, HIH-B4 (b only), HIH-B5, HIH-D01 (order side; backstop is Task 3), HIH-D07 (code read-back), HIH-C9]
key-files:
  created:
    - tests/test_live_order_safety.py
  modified:
    - src/model_prediction/data_sources/polymarket_execute.py
    - src/model_prediction/portfolio/auto_executor.py
    - src/model_prediction/portfolio/auto_buyer_ledger.py
    - src/model_prediction/cli/commands.py
    - src/model_prediction/dashboard/routes.py
    - tests/test_execution_gate.py
---

# Phase quick-261125-hih Plan 01: Live auto-buyer safety -- Task 1 Summary

Task 1 (tracer): idempotent order POSTs with no resend of an ambiguous outcome, the intent audited before the POST, a GOOD_TILL_DATE remainder that expires at start minus 90 s and is read back by id, a 2-cent live-ask re-check, and every placed live order either recorded in the ledger or raised as an alarm that halts the pass.

## Task 1

**Commit:** 14bc409f (landed 14:07:53 ET, parent 894f9542; see "Landing").

### What changed

- **polymarket_execute.py**
  - Every `POST /v1/orders` carries `Idempotency-Key` = `mp-<intent uuid>-p` (IOC/primary) or `-f` (fallback). A leg's retries reuse its key; `_request` refuses an order POST with no key and sends nothing.
  - Retry policy for order POSTs (`_post_order`):
    - Connect-phase errors (ConnectError, ConnectTimeout, PoolTimeout) and 429 resend with the same key. If every attempt fails this way, the result is REFUSED (`ExecutionGateError`), never unknown.
    - Ambiguous outcomes raise `OrderSubmitUnknownError` (a RuntimeError, not an ExecutionGateError) after exactly one POST. Ambiguous means any other transport error after sending, 5xx, 409 or ABORTED, or a 2xx that is not JSON or carries no id.
    - `ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY = False` is shipped. When True, ambiguous outcomes retry with the same key inside the 3-attempt budget; that path is tested.
    - Any other 4xx is REFUSED.
  - `execute`:
    - Audits `order_intent` before `_submit`: intent_id, both keys, slug, side, price, size, event_id, lowercased selection, market_type, line, event_start_utc, resting_expires_at_utc. If the append fails, the order is refused and nothing is sent.
    - After the append, it re-checks that the event has not started, because the append can wait up to 120 s on the audit lock (`order_rejected`, nothing sent).
    - A refusal from `_submit` is audited as `order_rejected`; an unknown outcome is audited as `order_submit_unknown`. Both are best effort, then re-raised.
    - `order_executed` additionally carries event_id, selection, market_type, line, intent_id, both keys, good_till_time and fallback_error. If its append fails, `execute` returns `status: submitted` plus `audit_error` and never raises.
  - The single_order policy (`_prior_buy_event`) treats an executed buy, an `order_submit_unknown`, or an `order_intent` without an `order_rejected` as a prior buy.
  - GTD fallback (D-01):
    - Placement:
      - The remainder is placed as `TIME_IN_FORCE_GOOD_TILL_DATE`, post-only, at the same price.
      - `goodTillTime` is RFC 3339 UTC, floored to the second, with a `Z` suffix.
      - Expiry is the earlier of the ticket's `resting_expires_at_utc` and the snapshot `event_start_utc` minus 90 s.
      - No expiry gives `not_placed_no_expiry`; an expiry at or before now + 60 s gives `not_placed_near_start`.
    - Isolation:
      - A failed fallback leg never discards the IOC fill.
      - A 400 gives `rejected` with `fallback_error`.
      - An ambiguous or other failure tries adoption through `open_orders([slug])`. Adoption needs exactly one match on intent, price, quantity, GTD tif and createTime at or after intent time minus 5 s; otherwise the status is `submit_unknown`.
    - Every placed or adopted fallback is read back with `GET /v1/order/{id}`. It must report tif GTD and a goodTillTime within 2 s. Otherwise it is cancelled by id and marked `cancelled_unverified_gtd`, or `unverified_gtd_cancel_failed` if the cancel itself fails.
    - No auto-buyer POST can carry GOOD_TILL_CANCEL. Only a manual `limit_gtc` order still does, by design. `describe()` now names GOOD_TILL_DATE and the expiry.
  - Live-ask check (B5): when `max_live_price_deviation` is set, a missing or non-finite live ask is refused, and so is any ask more than 2 cents from `ticket.price` in either direction. Nothing is sent.
  - New read and probe helpers:
    - `open_orders(slugs)`: `GET /v1/orders/open?slugs=...`.
    - `preview_order(request_body, user_command)`: one POST to `/v1/order/preview`, never resent, and nothing calls it.
    - `order_snapshots` items also carry `tif` and `good_till_time`.
  - `cancel`: if the audit append fails after a successful cancel POST, it returns `cancelled` plus `audit_error`.
- **auto_buyer_ledger.py**
  - `AutoBuyerLedgerWriteError`. The JSONL append and the top-up rewrite raise it, after an ERROR log, when the target is the live ledger or the caller passes `raise_on_write_failure=True` (the live submission path always does). The paper ledger still only warns, and xlsx stays best effort.
  - These fields pass through to the row: requested_shares, requested_cost_usd, intent_id, both keys, good_till_time, fallback_status, fallback_error.
  - A `submit_unknown` payload becomes its own row, never a top-up merge. The row has order_id "", order_ids [], shares, cost and units 0, fill_known false, `ORDER_STATE_SUBMIT_UNKNOWN`, status open, and keeps requested_cost_usd.
  - `settle_auto_buyer_ledger` leaves an open submit_unknown row open and counts it as pending.
- **auto_executor.py**
  - Live tickets carry `resting_expires_at_utc` = start minus `LIVE_FALLBACK_EXPIRY_LEAD_SECONDS` (90) and `max_live_price_deviation` = `LIVE_MAX_PRICE_DEVIATION` (0.02).
  - `recheck_enabled_before_submit` is True for the live cohort pass and for the legacy `should_execute` config. Before each live submission, if `enabled is True` no longer holds, the pass stops with `live_halt_reason="kill_switch"` (C9).
  - The submission try now wraps `executor.execute` only.
    - `OrderSubmitUnknownError`: writes an unknown row, adds alarm `submit_unknown`, and halts.
    - `ExecutionGateError`: `refused`, and the pass continues.
    - Any other RuntimeError or OSError: alarm `execution_error`, then halt.
    - `audit_error`: the row is still recorded, alarm `audit_failed_after_submit`, then halt.
    - A failed ledger write: ERROR log with the order ids, alarm `ledger_write_failed`, then halt.
    - fallback_status rejected, submit_unknown, cancelled_unverified_gtd or unverified_gtd_cancel_failed: alarm `fallback_<status>`, and the pass continues.
  - `AutoExecutionResult.alarms` and `.live_halt_reason` appear in the pass summary and the global run_summary. `run_auto_buyer_cycle` saves state and history, then raises `LiveAutoBuyerAlarm(run_summary=..., alarms=...)` when a live pass has alarms.
  - `_resting_fallback_cost` counts an open submit_unknown row at its requested_cost_usd, and an unknown fallback leg at its remainder, toward the daily and per-game caps. This is the interim helper; Task 2 replaces it.
  - `_build_bought_index` audit section:
    - Outcomes are collected first. An intent with an `order_executed` or `order_rejected` is settled; an `order_submit_unknown` or an intent with no outcome is "unknown".
    - An unknown event hard-blocks its pick ids and market side; the same is done for live-ledger submit_unknown rows. These keys are re-added after the partial-fill exclusion and dropped from `partially_filled`, so an unanswered top-up is never retried.
    - Audit-derived event keys (event_selections, and the new event_markets from market_type) apply only when `mode == "live"` or `not dedup_own_ledger_only`, so a live fill never blocks a paper cohort row.
- **routes.py**: `/api/auto-buyer/run` catches `LiveAutoBuyerAlarm` and answers `{"status": "alarm", "alarms", "run"}`. The finally still releases the lock.
- **cli/commands.py**: `cmd_execute` and `cmd_sell_position` go through `_execute_or_report_unknown`, which returns `{"status": "submit_unknown", ..., "note": "...check the exchange... Do not resubmit."}` instead of a crash or REFUSED. The exit code is 0.

### Tests (all micro-targeted, never the full suite)

- New `tests/test_live_order_safety.py`: 62 tests (CRLF). The tracer test runs a real `PolymarketExecutor` over a `FakeExchange` at the `httpx.request` boundary. The fixture's teardown asserts that no POST in the file carries GOOD_TILL_CANCEL.
- Verify list in the worktree, run on the final commit:
  - Files: test_live_order_safety, test_execution_gate, test_auto_buyer_cohorts, test_auto_polymarket_buyer, test_auto_buyer_mode_boundary, test_auto_buyer_ordering_and_lineage, test_auto_buyer_ncaab_settle, test_fix_regressions, test_dashboard_server.
  - Result: **389 passed, 3 failed**.
  - The 3 failures are `test_dashboard_server.py::test_export_picks_endpoint`, `test_pick_quote_resolves_inverted_tennis_player_names` and `test_pick_quote_resolves_parenthesized_soccer_team_names`. They fail identically at HEAD in the worktree (baseline: 327 passed, 3 failed) and pass in E:/MP (80/80). They depend on worktree-absent data, not on this change.
- Own commit plus the foreign hunks plus the untracked nba_ml_v6 files, in a scratch worktree: the verify list without dashboard_server, plus test_auto_buyer_worker_pair, test_ncaab_auto_buyer_paper_trial, test_display_names and test_nba_ml_v6_paper_trial: **382 passed**.
- Adjacent files: test_audit_fixes_regression, test_auto_buyer_input_gate, test_auto_buyer_state_versioning, test_calibration_serving, test_ncaab_auto_buyer_paper_trial, test_runtime_root_test_isolation, test_team_matches_qualifiers, test_team_name_aliases, test_auto_buyer_worker_pair. Result: 85 passed, 1 failed. The failure, `test_audit_fixes_regression.py::test_nba_total_margin_v2_model_and_forecast`, also fails in E:/MP without this change (a model-id set assertion), so it is pre-existing.
- `ruff check` and `ruff format --check` are clean on all 7 files.
- mypy on the 5 source files shows only the pre-existing errors from HEAD (commands.py unused-ignore, auto_buyer_ledger.py float/operand at old lines 1064/1065/1088/1122, routes.py:754 list-item). The 4 new errors this change introduced were fixed.
- CRLF byte check: every edited and new file has as many `\r\n` as `\n` (polymarket_execute 1480/1480, auto_executor 3263/3263, auto_buyer_ledger 2490/2490, commands 1610/1610, routes 780/780, test_live_order_safety 1367/1367, test_execution_gate 1019/1019).
- Grep gates (comment lines excluded) all pass:

  | File | Token | Count | Required |
  |---|---|---|---|
  | polymarket_execute.py | `Idempotency-Key` | 4 | >=1 |
  | polymarket_execute.py | `TIME_IN_FORCE_GOOD_TILL_DATE` | 1 | >=1 |
  | polymarket_execute.py | `order_intent` | 3 | >=2 |
  | polymarket_execute.py | `/v1/orders/open` | 2 | >=1 |
  | auto_executor.py | `order_intent` | 2 | >=1 |
  | auto_buyer_ledger.py | `submit_unknown` | 5 | >=2 |

### Red output before the fix (first run of the new file, before any source change)

`56 failed, 1 passed, 3 errors`. Representative lines (`--tb=line`):

```
test_live_order_safety.py:199: TypeError: OrderTicket.__init__() got an unexpected keyword argument 'resting_expires_at_utc'   (25 tests)
test_live_order_safety.py:283: AttributeError: module '...polymarket_execute' has no attribute 'ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY'   (5)
test_live_order_safety.py:813: TypeError: AutoExecutionConfig.__init__() got an unexpected keyword argument 'recheck_enabled_before_submit'   (4)
test_live_order_safety.py:225: Failed: DID NOT RAISE <class '...ExecutionGateError'>   (order POST without a key)
test_live_order_safety.py:872: AssertionError: the second candidate must not be submitted   (unknown IOC)
test_live_order_safety.py:981: AssertionError: assert ['a1', 'a2'] == ['a1']   (audit failure after submit did not halt)
test_live_order_safety.py:835: KeyError: 'Idempotency-Key'   (tracer)
test_live_order_safety.py:696: KeyError: 'submit_unknown'   (ledger row)
test_live_order_safety.py:375: AuditLockTimeout: lock busy for order_cancelled   (cancel audit tolerance)
E   AssertionError: assert not [{... 'path': '/v1/orders' ... 'participateDontInitiate': True ...}]   (no-GTC sweep, teardown ERROR x3)
```

Four tests were added after the code, at the advisor's request: the post-intent start re-check, preview_order x2, and the CLI unknown report. Their red evidence is the mutation proofs M9-M11 below.

### Mutation proofs (break, run the named test, restore; `git status` clean afterwards)

Run by `$TEMP/261125-hih/mutate.py` and `mutate2.py`; every one went red.

| # | Mutation | Red line |
|---|---|---|
| M1a | switch ON, new key per attempt | `FAILED ...::test_same_key_retry_switch_on_reuses_the_key_and_records_one_order` |
| M1b | retry ambiguous with switch OFF | `FAILED ...::test_ambiguous_outcome_is_unknown_with_exactly_one_post[read-timeout]` (all 5 params) |
| M2a | ambiguous mapped to REFUSED | `FAILED ...::test_ambiguous_outcome_is_unknown_with_exactly_one_post[read-timeout]` and `[remote-protocol]` |
| M2b | connect-only exhaustion mapped to unknown | `FAILED ...::test_connect_failure_on_every_attempt_is_refused_never_unknown` |
| M3 | fallback tif back to GOOD_TILL_CANCEL | `FAILED ...::test_tracer_one_live_cohort_order_end_to_end`, `test_gtd_fallback_payload_and_describe`, plus 2 ERROR from the no-GTC sweep |
| M4 | fallback isolation removed | `FAILED ...::test_fallback_400_keeps_the_ioc_fill_and_reports_rejected` |
| M5 | deviation check dropped | `FAILED ...::test_live_ask_deviation_two_cents_either_way[0.55-False]` (also 0.49, None, nan) |
| M6 | ledger write failure swallowed (`pass`) | `FAILED ...::test_ledger_write_failure_after_submit_alarms_and_halts` |
| M7 | intent appended after `_submit` | `FAILED ...::test_intent_audit_failure_refuses_before_any_post`, `test_intent_is_audited_before_the_post_with_event_fields` |
| M8 | enabled re-check dropped | `FAILED ...::test_kill_switch_mid_pass_stops_the_next_live_submission` |
| M9 | post-intent start re-check dropped | `FAILED ...::test_event_start_during_the_intent_append_sends_nothing` |
| M10a | preview without user_command gate | `FAILED ...::test_preview_requires_an_explicit_user_command` |
| M10b | preview ambiguity mapped to REFUSED | `FAILED ...::test_preview_ambiguous_outcome_is_never_resent` |
| M11 | CLI lets the unknown escape | `FAILED ...::test_cli_reports_an_unknown_outcome_instead_of_refused` |

### Tests updated to new required behaviour

- `tests/test_execution_gate.py::test_ioc_partial_fill_rests_remainder_at_same_price_not_chased` and `::test_ioc_zero_fill_rests_full_remainder`. D-01 supersedes the GTC remainder. Both now set `resting_expires_at_utc`, assert `TIME_IN_FORCE_GOOD_TILL_DATE` (and a `Z` goodTillTime), and answer the GTD read-back GET in their fakes. No other existing assertion was changed or weakened.

## Deviations from Plan

1. **[Rule 2] Post-intent pregame re-check.** The intent append can block up to 120 s on the audit lock after the not-started check. The event start is now re-checked after the append; if the event has started, the order is refused (`order_rejected`) and nothing is sent.
2. **[Rule 2] Beyond the plan's literal list, all in the conservative direction:**
   - alarm `execution_error`: a non-gate exception from `execute` halts instead of reading as "refused"
   - fallback status `unverified_gtd_cancel_failed`
   - dangling intents (no recorded outcome) hard-block their pick and market side
   - `raise_on_write_failure=True` from the live submission path, so a strict write never depends on the resolved runtime root
   - a fallback `submit_unknown` leg counts its remainder toward the caps, and its row is recorded even with zero primary fill

   Settle holds only primary submit_unknown rows, as planned. A row whose fallback leg is `submit_unknown` settles on its known primary shares, and its alarm is the operator's cue.
3. **[Rule 1] Top-up unknown.** An unknown outcome on a top-up ticket is written as its own row (`is_topup` false) and its keys survive the partial-fill exclusion. Before this, `record_auto_buy_execution` would have merged it into the filled row, and `difference_update` would have unblocked the pick.
4. **Landing mechanics.** The plan says to commit with plain `git commit`. Not done in E:/MP: the repo's installed pre-commit framework hook stashes every unstaged tracked change with `git checkout -- .`, which would briefly revert the foreign hunks and the live `data/` files. The worktree commit ran the hook (ruff and ruff-format passed). Landing then applied that commit's exact diff to the index and working tree, confirmed `git write-tree` equals the commit tree, and moved the branch with a compare-and-swap `git update-ref`.

## Landing

- **Commit `14bc409f`** (`fix(quick-261125-hih): live order safety -- idempotent POSTs, intent before send, GTD fallback, recorded or alarmed`) landed on refactor/cleanup-2026-11-11 at **14:07:53 ET**, minute :07, inside a window.
  - Pre-landing checks passed: runs.db showed no daily or auto-buyer run in flight, the index was clean, and the foreign diff matched its snapshot byte for byte.
- **First attempt aborted.** At 14:06, `HEAD moved`: another session committed 894f9542 at 13:50 (`backtests.py` plus a new test, no overlap with this change).
  - The worktree branch was rebased onto 894f9542.
  - The verify list was rerun: 324 passed.
  - The combination test with the foreign hunks and `test_spread_side_ambiguous_team.py` passed: 394.
- **Mechanics:**
  1. `git apply --cached` then `git apply` of the exact commit diff.
  2. `git write-tree` equalled the commit tree.
  3. A compare-and-swap `git update-ref` from 894f9542 to 14bc409f.
- **After landing:**
  - The index is clean against the new HEAD.
  - `git diff` shows exactly the foreign hunks (identical to the snapshot apart from index and @@ lines): auto_executor.py, auto_buyer_worker.py, ncaab_paper_trial.py, display_names.py, and the 4 foreign test files.
  - The untracked nba_ml_v6 files are untouched.
- **Verify in E:/MP after landing:** the 9-file verify list passed, **405 passed** (test_dashboard_server is 80/80 here). The CRLF byte check passed on all 7 files in the shared tree (auto_executor 3276/3276, including the 13 foreign lines). ruff is clean.
- The worktree and the merged `agent-hih-t1` branch were removed.

## Open items for the orchestrator

- **Restart the dashboard right after landing; don't wait for Task 4.** The long-running dashboard keeps pre-Task-1 code. Its Run button would still place GTC fallbacks without keys, and its Settle has no hold for a submit_unknown row.
- **Worker exit code.** Any live alarm, including a fallback alarm such as `fallback_rejected` on the first partial fill, now makes the 15-minute worker run exit 1 through `LiveAutoBuyerAlarm`. The first partial fill is the real GTD-format test (D-07), so a failed run then is expected; read `last_run` alarms and the `fallback_error` text.
- **Pre-commit hook.** Every `git commit` in E:/MP, including doc commits, goes through the stash described in deviation 4. `ls ~/.cache/pre-commit/patch*` shows whether earlier commits did.
- **CLI.** `execute` and `sell-position` exit 0 with `status: submit_unknown` on an unknown outcome.
- **Not proven without a real order (D-07).** The exchange may not echo `tif`/`goodTillTime` on `GET /v1/order/{id}` (field names assumed from the create schema). In that case every fallback is cancelled as `cancelled_unverified_gtd` and buying continues IOC-only. Task 4 observes this.
- **Tasks 2-3** remain: total-line key, top-up same slug, reconcile robustness, kill-switch cancels, live age, identity calibration, quarantine off, backstop worker, dashboard, drawdown.

## Review cycle 1 fixes

The reviewer's findings on 14bc409f were each checked against the code before fixing. Every fix was test-first: the test was written, run red on 14bc409f, then run green after the fix. Fix 6 (the wrong-side spread re-check) is a separate commit.

### Findings and fixes

1. **[MAJOR] An unknown fallback leg was never tracked.**
   - **Finding.** The IOC partly filled, the GTD remainder POST was ambiguous, and `_adopt_open_fallback` found nothing. The ledger then wrote `fallback_order_id` None with `fallback_reconciled` True. As a result:
     - reconcile skipped the row
     - `_live_book_has_pending_orders` ignored it
     - settlement settled it on the IOC shares alone
     - `_build_bought_index` treated it as a top-up-eligible partial and unblocked its pick and market side

     The next cycle therefore sent a fresh top-up IOC while the original remainder might still rest or fill unrecorded.
   - **Fix, in `auto_buyer_ledger.py`.**
     - New `is_fallback_submit_unknown(record)`. It is true for an open row with no `fallback_order_id` that has `fallback_submit_unknown` True. When that flag is absent, as on rows written by 14bc409f, `fallback_status == "submit_unknown"` alone qualifies.
     - `record_auto_buy_execution` writes such a row with `fallback_reconciled` False and `fallback_submit_unknown` True.
     - `fill_known` stays True, because the IOC fill is known. Setting it False would make reconcile treat the IOC id as the target and double-count its fill.
     - `settle_auto_buyer_ledger` holds the row open and counts it pending, next to the primary `submit_unknown` hold.
   - **Fix, in `auto_executor.py`.**
     - `_live_book_has_pending_orders` counts the row.
     - `_resting_fallback_cost` counts its remainder through the shared predicate.
     - In `_build_bought_index`, the row is never a partial. Its pick id, market side, event selection, event market and matchup are re-added after the partial-fill exclusion; audit unknowns also re-add their event keys on the live book.
     - Any partial entry sharing an unknown pick, market side or event selection is dropped, so it gets no top-up.
   - **Operator resolution.** Either find the order on the exchange and set `fallback_order_id` (reconciliation takes over), or confirm none exists and set `fallback_submit_unknown` False and `fallback_reconciled` True. `manually_settle_auto_buyer_order` also works.
2. **[minor, latent] Same-key retry could end REFUSED after an ambiguous attempt.**
   - **Finding.** With `ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY` on, a later ExecutionGateError (a final connect error, a final 429, or a non-409 4xx) turned an earlier ambiguous attempt into `order_rejected`, and the pick became re-buyable.
   - **Fix.** Every such raise in `_post_order` now raises `last_unknown` when it is set. The fallback leg then reports `submit_unknown`, not `rejected`. The constant is still shipped False.
3. **[minor, latent] The top-up merge skipped the passthrough fields.**
   - **Finding.** The merge returned before `_EXECUTION_PASSTHROUGH_FIELDS` was applied. It marked an unknown fallback reconciled and kept the old `fallback_status`.
   - **Fix.**
     - The merge now applies the passthrough fields, so the fallback, intent, key and good_till fields describe the top-up's own legs.
     - It carries fix 1's unknown semantics.
     - It promotes to `ORDER_STATE_FILLED` only when the fallback is reconciled.
4. **[minor] Connect-phase resends were not re-checked against the event start.**
   - **Fix.** The order-post context is now `(key, leg, event_start)`, where `event_start` is the earlier of the row's start and the exchange's start. Any attempt after the first raises once `utc_now() >= event_start`: `last_unknown` if one is set, otherwise ExecutionGateError "...it was never sent".
5. **[nit] The single_order refusal read "order None at None"** for an unknown or dangling prior.
   - **Fix.** `_describe_prior_buy` names the event: `order <id> at <time>`, `order_submit_unknown for intent <id>`, or `order_intent <id> created at <time> with no recorded outcome`. The `single_order policy refuses a second buy` wording is kept.

### Red on 14bc409f (`--tb=line`, the 14 new tests)

```
polymarket_execute.py:931: ExecutionGateError: REFUSED: could not connect to Polymarket US (ConnectError); the order was never sent.   (fix 2, ReadTimeout->ConnectError x2)
polymarket_execute.py:952: ExecutionGateError: REFUSED by Polymarket US (429): rate limited on every attempt   (fix 2, 502->429->429)
polymarket_execute.py:956: ExecutionGateError: REFUSED by Polymarket US (400): {"error":"duplicate"}   (fix 2, ReadTimeout->400)
test_live_order_safety.py:1419: AssertionError: assert 'rejected' == 'submit_unknown'   (fix 2, fallback leg)
test_live_order_safety.py:1438: Failed: DID NOT RAISE <class '...ExecutionGateError'>   (fix 4, resend after start)
test_live_order_safety.py:1453: Failed: DID NOT RAISE <class '...OrderSubmitUnknownError'>   (fix 4 with an earlier unknown)
test_live_order_safety.py:1483: AssertionError: assert 'None' not in 'REFUSED: a ... accumulate.'   (fix 5)
test_live_order_safety.py:1577: AssertionError: no order may be sent for a pick whose fallback leg is unknown   (fix 1, next-cycle top-up went out)
test_live_order_safety.py:1596: assert True is False   (fix 1, fallback_reconciled)
test_live_order_safety.py:1621 / 1666: AttributeError: ... has no attribute 'is_fallback_submit_unknown'   (fix 1, zero-fill and pre-fix row)
test_live_order_safety.py:1721: AssertionError: assert 'rejected' == 'submit_unknown'   (fix 3, top-up merge kept the old status)
test_live_order_safety.py:1763: AssertionError: assert 'rejected' == 'resting'   (fix 3, passthrough)
13 failed, 1 passed
```

The one test that passed is the control: a resend before the start still goes out.

**Green:** `tests/test_live_order_safety.py`, 76 passed.

### Mutation proofs (break, run, restore by bytes, sha256 re-checked)

| # | Mutation | Red |
|---|---|---|
| M1 | settlement hold for an unknown fallback leg removed | `'settled' == 'open'` in the next-cycle, zero-fill and pre-fix-row tests (3 failed) |
| M2 | partial-fill exclusion removed | pre-fix-row test, `'cbb-g7-2026-11-25' in set()` (held_events) |
| M3 | pending-orders check ignores the leg | 3 failed, `assert False` |
| M4 | unknown leg recorded reconciled (the original bug) | 2 failed, `assert True is False` |
| M5 | remainder not counted toward the caps | `assert 2.04 == 10.0011 +- 0.01` |
| M6 | top-up merge skips the passthrough fields | 2 failed, `'rejected' == 'submit_unknown'` and `'rejected' == 'resting'` |
| M7 | resend start check removed | 2 failed, DID NOT RAISE |

Each settlement test also settles an ordinary control row for the same game against the same final score, so the fixture is shown to settle rows.

### Verify list (worktree; MODEL_PREDICTION_* unset; each file `-o addopts='' -q`)

The list covers test_live_order_safety, test_execution_gate, test_spread_side_ambiguous_team, test_dashboard_server, and every test file that imports auto_buyer_ledger or auto_executor: audit_fixes_regression, auto_buyer_clv, auto_buyer_cohorts, auto_buyer_input_gate, auto_buyer_mode_boundary, auto_buyer_ncaab_settle, auto_buyer_ordering_and_lineage, auto_buyer_state_versioning, auto_polymarket_buyer, calibration_serving, cfb_multi_challenger, cfb_structural_v51, manual_bet_ledger, mlb_nrfi_v2, ncaab_auto_buyer_paper_trial, production_registry, and runtime_root_test_isolation.

- **Baseline at 14bc409f:** 522 passed, 4 failed.
- **After fixes 1-5:** 536 passed, 4 failed. The difference is the 14 new tests.
- **The 4 failures are the same at the base:**
  - test_dashboard_server: `test_export_picks_endpoint`, `test_pick_quote_resolves_inverted_tennis_player_names` and `test_pick_quote_resolves_parenthesized_soccer_team_names`. They depend on data absent from a worktree.
  - `test_audit_fixes_regression.py::test_nba_total_margin_v2_model_and_forecast`, which was already pre-existing in Task 1.

### Fix 6 (separate commit): wrong-side spread mapping in the execution re-check

`polymarket_execute._resolve_spread_side` now applies 894f9542's rule: the market team must match exactly one of the row's away and home teams. Neither or both raises `ExecutionGateError` REFUSED. The real case is Polymarket "UCA" for Central Arkansas @ Jacksonville, where "Central Arkansas -3.5" against a UCA +3.5 market was resolved to short (Jacksonville -3.5) and the ticket was let through. The tests are in `tests/test_resolve_spread_side_ambiguous_team.py`.

## Task 2

Task 2 covers dedup, reconcile, the kill switch and the live gates: B4a, B4c, B6, B7, C4-C8, D-02, D-03, and the quarantine-off half of D-06. It is one code commit, and this section is committed with it.

### What changed

- **auto_buyer_ledger.py**
  - **B4a, total key.** A new pattern, `_TOTAL_LINE_SLUG = ^(tsc-.+)-\d+pt\d+$`. `event_key_from_slug` maps every line of one game's total to its `tsc-<event>` prefix.
    - The `tsc-` marker stays in the key, so a total key never equals that game's spread key or moneyline key. The 31d shared per-game cap depends on this.
    - `_SPREAD_SIDE_SUFFIX` is unchanged, so `_manual_position_rows` labels spreads exactly as before.
    - The docstring lists the real slug shapes.
  - **B6, C5 and D-02: `reconcile_pending_auto_buyer_fallbacks(..., *, cancel_all_resting=False)`.**
    - When `order_snapshots` fails, reconcile no longer returns early. It continues with no reads, and the summary gets `snapshot_error`.
    - A true fallback that is not known to be terminal is cancelled by id when its event has started (`game_started`) or when `cancel_all_resting` is set (`kill_switch`). This also applies when the order is missing from the snapshot or the snapshot failed.
    - After every successful cancel, including the existing edge, market-moved and market-closed cancels, the id is re-read with `order_snapshots([fid])`.
      - A terminal re-read restates shares and cost from its `cum_quantity`, then closes the row (filled, partial, or a zero-fill void).
      - Otherwise the row stays pending, with `cancel_requested_at_utc` set.
      - No row is closed or voided from a read taken before the cancel. A pre-cancel read still restates fills, because a fill count only grows.
    - A cancel that raises adds the id to `cancel_failed` and counts an error.
    - `cancel_failed` and `snapshot_error` appear only when non-empty, so the exact four-key dict of an uneventful run is unchanged.
    - The terminal-state set is now `_TERMINAL_ORDER_STATES`.
    - Executor construction goes through `_default_live_executor(root)`.
    - Cancels go through `_cancel_by_id`, which keeps the TypeError fallback for cancel fakes that take no `market_slug`.
  - **D-02: `cancel_resting_live_fallbacks(*, data_root=None, executor=None, now=None, cancel_all, start_lead_seconds=30.0, skip_order_ids=frozenset(), reason)`.**
    - It is cancel-only: it reads the live ledger and never writes it.
    - Candidates are unsettled rows with an unreconciled true fallback whose id is not in `skip_order_ids`, and which are due: `cancel_all` is set, or the event starts at or before now + lead. An unparseable start counts as due.
    - With no candidates it returns `candidates: 0` and builds no executor.
    - Otherwise it makes one `order_snapshots(ids)` read (a failure is tolerated). Terminal ids go to `already_terminal`; every other id gets one `cancel(order_id, user_command=True, market_slug=slug)`.
    - It returns `{reason, candidates, cancelled, already_terminal, failed: [{order_id, error}]}`.
    - It never calls a cancel-all or batch endpoint.
  - **Coordinator item (final review of 0db5d783).** The top-up merge now always writes `rec["fallback_submit_unknown"] = fallback_unknown` and never pops it.
    - The passthrough keeps a stale `fallback_status: "submit_unknown"` when the top-up payload carries none.
    - With the key popped, `is_fallback_submit_unknown()` fell back to that stale status and held a resolved row again.
  - **C6, top-up merge.** The merge also zeroes `fallback_filled_shares` and `fallback_filled_cost_usd`. The earlier leg's fills are already in the new `primary_filled_*` baseline, and a stale count would be subtracted from the new leg's remainder and under-count the caps (deviation 7).
- **auto_executor.py**
  - **C6.** `_pending_exposure_cost(row)` replaces `_resting_fallback_cost`. `tests/` had no references to the old name.
    - A true fallback counts `max(0, fallback_resting_shares - fallback_filled_shares) * entry_price`.
    - An unknown-fill primary counts `max(0, fallback_resting_shares - shares) * entry_price`.
    - An open submit_unknown row counts `requested_cost_usd`, and an unknown fallback leg its remainder.
    - `_today_auto_buyer_totals`, game exposure in `_build_bought_index`, and `_live_book_has_pending_orders` all read this helper.
  - **C7.**
    - The dead-row filter in `_build_bought_index` no longer skips a row with pending exposure. A CANCELED, zero-fill IOC whose GTD remainder still rests now blocks its pick.
    - Game exposure keeps a pick's pending cost under `"<pick>#pending"`. The game-room check (`key != pick_id`) therefore excludes only the pick's own filled cost, which the top-up math already subtracts.
  - **B4c.**
    - Right after `_get_partial_fill_info`, a partial whose `market_slug` differs from the newly resolved slug counts as `rejected_dedup`. It is never topped up.
    - A partial row also records `partial_contracts[event_key] = {(slug, side)}`. In `_is_already_bought`, any other contract in that game market is a dedup. This is deviation 1.
  - **D-02.**
    - `toggle_auto_buyer(False)` saves first. It then returns `kill_switch_cancel` from `cancel_resting_live_fallbacks(data_root=DATA, cancel_all=True, reason="kill_switch")`.
      - The value is not persisted.
      - A failure is logged and returned as `{"status": "error", ...}`; it never raises and never undoes the save.
    - The cycle's pre-cycle reconcile passes `cancel_all_resting=not cycle_enabled`.
  - **D-03.** New `LIVE_COHORT_MAX_MODEL_AGE_MINUTES = 180.0`. A live-cohort row uses `min(max_model_age_minutes, 180)`, also when `require_model_timestamp` is off. Paper and legacy rows keep 720.
  - **B7.** After `calibrate_for_serving`, a live-cohort row is refused when `applied` is true, the method is not `identity`, or the probability differs from the raw probability.
    - It counts the new `rejected_live_calibration` (in AutoExecutionResult, the pass summary and the global run_summary) and logs ERROR.
    - It adds alarm `live_calibration_not_identity` once per model per pass, so the run raises `LiveAutoBuyerAlarm`.
  - **D-06 and C4.**
    - `_cohort_pass_config` sets `auto_quarantine_underperforming=False` for every cohort book. The paper pass's explicit override is kept, and the legacy global pass keeps the quarantine.
    - `_get_quarantined_underperforming_models` skips result void, submit_unknown, and explicit zero-share rows. A row with no `shares` key still counts; the legacy fixtures carry none.
- **cli/daily.py**
  - New `_auto_buyer_enabled(state)` (`enabled is True`).
  - New `_daily_cancels_all_resting()` (not enabled; an unreadable state loads as disabled, so it fails toward cancelling).
  - New `_run_auto_buyer_step12(forecast_date)`: step 12's body moved verbatim behind the predicate.
  - Both of daily's own reconcile calls (11b and 12b) pass `cancel_all_resting=_daily_cancels_all_resting()`.

### Total position key (plan action 1, read-only)

- No exchange position snapshot is stored in the runtime root. `netPosition` appears in no runtime JSON or JSONL file, and audit.jsonl holds no position payload. How positions key a total is therefore still unverified (open item). The new key handles any full total slug, which is how `_manual_position_rows` and the 12c7f2e7 fixtures key positions.
- Plan fact 2 is incomplete. The `-total-NptM` form does exist: in the 2026-11-2x odds snapshots, cfb has 470 and nfl 783 slugs such as `tsc-cfb-kentst-bowlgr-2026-11-10-total-44pt5`, and esports use `-tot-NptM`. NCAAB (8121), NBA and soccer use the bare `-NptM`. Every total slug ends in `NptM`, so each game gets one key.

### Tests updated to new required behaviour

- `tests/test_auto_buyer_cohorts.py::test_paper_cohort_pass_never_quarantines_live_pass_does` became `test_no_cohort_pass_quarantines_the_legacy_pass_still_does`. D-06 flips the live assertion to False, and the test now also asserts that the legacy global pass keeps the quarantine.
- **C5.** Five `FakeExecutor`s in `tests/test_auto_polymarket_buyer.py` now read a cancelled id back as `ORDER_STATE_CANCELED`. Each returned the same pre-cancel NEW/0 snapshot after its cancel, which is the race C5 removes. Assertions are unchanged. The tests:
  - `test_reconcile_cancels_resting_order_once_event_has_started`
  - `test_reconcile_cancels_resting_order_when_edge_drops_below_min`
  - `test_reconcile_preserves_resting_order_when_edge_remains_strong` (no cancel)
  - `test_reconcile_cancels_resting_order_when_market_moves_up_significantly`
  - `test_reconcile_cancels_resting_order_when_market_closed`
- Zero-argument reconcile fakes now accept the keyword. They were in the cohorts `_cycle_harness` and its two failure tests, in `test_auto_buyer_mode_boundary.py`, and in `test_auto_polymarket_buyer.py::test_live_cycle_stops_when_precycle_reconciliation_fails`.
  - Without this, the cycle's `except (..., TypeError, ...)` would have swallowed the mismatch, and the failure tests would pass for the wrong reason.
  - The harness and mode-boundary tests now also assert the value: `cancel_all_resting == not enabled`.
- `tests/test_live_order_safety.py::test_topup_merge_carries_the_execution_fields_of_the_topup`: `"fallback_submit_unknown" not in merged` became `merged.get("fallback_submit_unknown") is not True`. This is a contract update from the coordinator item.
- The edits to `tests/test_auto_polymarket_buyer.py` touch only line 59 and the reconcile region (about lines 2262-2500), away from the foreign hunks.

### Red before the fix (new tests run against base 0db5d783 src)

`tests/test_auto_buyer_resting_lifecycle.py` (46 tests) was run on an untouched 0db5d783 checkout. Its executor-factory monkeypatches took `raising=False` there, because the factory did not exist yet. Result: `39 failed, 7 passed`. The 7 that pass are guards and controls: spread/total independence x2, the same-contract top-up, an unstarted fallback left resting, 179 min still buying, paper age at 600 min, and toggle-on cancelling nothing.

```
:240 AssertionError: assert 'tsc-cbb-abch...-11-25-117pt5' == 'tsc-cbb-abch...-11-25-120pt5'      (B4a key)
:260 AssertionError: assert [(OrderTicket...'CALL', ...})] == []                                (exchange total position, moved line bought)
:346 AssertionError: assert [(OrderTicket...'CALL', ...})] == []                                (B4c partial topped up on another line)
:438 assert 22.5 == 18.5                                                                         (C6 double-counted pending exposure)
:461 AssertionError: assert [(OrderTicket...'CALL', ...})] == []                                (C7 dead-looking row with resting fallback)
:582 AssertionError: assert [] == ['gtd-1']                                                     (B6 snapshot failure: no cancel)
:614 AssertionError: assert 'void' != 'void'                                                    (C5 voided from the pre-cancel read)
:655 TypeError: reconcile_pending_auto_buyer_fallbacks() got an unexpected keyword argument 'cancel_all_resting'
:749 KeyError: 'kill_switch_cancel'                                                             (toggle)
:800 AssertionError: assert [] == ['gtd-1']                                                     (disabled cycle)
:843 AttributeError: module 'model_prediction.cli.daily' has no attribute '_run_auto_buyer_step12'   (C8)
:860 AssertionError: assert True is False                                                       (D-03, 181 min bought)
:889 AssertionError: assert [(OrderTicket...'CALL', ...})] == []                                (B7)
:936 AssertionError: assert True is False                                                       (D-06 live pass quarantined)
:951 AssertionError: assert {'m': {... 'sample': 19}} == {}                                     (C4 void/unknown rows in the sample)
39 failed, 7 passed
```

The updated existing tests run on base src: `8 failed, 162 passed`. The failures:
- the cohorts harness, `[{}] == [{'cancel_all_resting': ...}]` x4
- the D-06 test, `True is False`
- mode-boundary, `assert False` x3

The coordinator-item test on base `auto_buyer_ledger.py`: `test_live_order_safety.py:1852: AssertionError: assert not True`. It is green after the fix.

### Mutation proofs (break, run, restore by bytes with a sha256 check; the `git diff` hash was identical before and after)

All 21 mutations went red.

| # | Mutation | Red |
|---|---|---|
| M1 | total key reverted | 3 failed, the B4a key and both moved-line tests |
| M2 | total key collapsed to the spread key | spread/total independence [spread] and [total], `assert 1 == 0` (dedup) |
| M3 | early return on a snapshot failure | `assert [] == ['gtd-1']` |
| M4 | close/void from the pre-cancel read | late-fill `'void' != 'void'`, unconfirmed-cancel x2 |
| M5 | missing-from-snapshot fallback not cancelled | `assert [] == ['gtd-1']` |
| M6 | cancel_all_resting ignored | `['gtd-2'] == ['gtd-1', 'gtd-2']`, disabled-cycle test |
| M7 | cancel helper builds the executor with nothing due | "a default (real) executor must not be built" |
| M8 | toggle without cancel | 3 failed, `KeyError: 'kill_switch_cancel'` |
| M9 | cycle reconcile without cancel_all | disabled-cycle test, cohorts harness [False-*] x2 |
| M10 | live age back to 720 | `[181.0-False]`, `True is False` |
| M11 | calibration guard removed | 3 failed (each served shape) |
| M12 | live quarantine on | live-pass test, cohorts D-06 test |
| M13 | top-up on any slug (both guards off) | other-line and legacy-slug tests |
| M13b | slug guard off only | legacy-slug test |
| M13c | partial-contract block off only | other-selection moved-line test |
| M14 | exposure double-count restored | `20.5 == 18.5` |
| M15 | dead-row filter drops pending rows again | dead-looking-row test |
| M16 | quarantine sample counts void/unknown rows | `{'m': {... 'sample': 19}} == {}` |
| M17 | daily enabled predicate truthy | step 12 ["true"] and [1], daily reconcile ["true"] and [1] |
| M18 | top-up merge pops the unknown flag again | coordinator test, `assert not True` |
| M19 | top-up merge keeps the earlier leg's fallback fills | `assert 1.0 == 3.0` |

### Deviations from plan

1. **[Rule 2] Partial-contract block (B4c).** A partial row skips the hard lockouts so it can be topped up, and that also opened its game market to a moved line with the other selection (an under at a new line next to a held over). The slug check alone does not close this.
   - `partial_contracts` keeps only the partial's own `(slug, side)` buyable in that game market.
   - Test: the other-selection test (M13c).
2. **[Rule 2] C5 re-read on every cancel branch**, not only game start. The edge, market-moved and market-closed cancels closed rows from the pre-cancel read too.
3. **[Rule 2] Live age gate.** A live-cohort row is held to 180 min even if a config sets `require_model_timestamp` off. The live cohort pass sets it on anyway.
4. **[Rule 1] B7 alarm, once per model per pass.** Every row is still counted and logged.
5. **C8 call site.** `run_daily` cannot be reached in a unit test, so step 12's body moved into `_run_auto_buyer_step12` and is tested directly. An AST test pins that `run_daily` calls it once, and that both of daily's own reconcile calls pass `cancel_all_resting=_daily_cancels_all_resting()`.
6. **Not changed: ungoverned, disabled cycles.** `_cycle_manages_live_book` was left alone. A disabled cycle with no `cohorts` key still does not reconcile the live book itself; daily step 12b does, with `cancel_all_resting`, on every daily run. The live state is governed, so the 15-minute cycle covers it.
7. **[Rule 1] Top-up merge resets the earlier leg's fallback fills.**
   - `record_auto_buy_execution`'s merge reset the fallback id, resting shares and reconciled flag, but not `fallback_filled_shares` or `fallback_filled_cost_usd`.
   - With C6 subtracting fallback fills from the resting remainder, a new leg would have read as `(new remainder - old fills)` pending.
   - Found in the final self-review. The test was added red (`1.0 == 3.0`), then the fix.
   - The `toggle_auto_buyer` test in `test_auto_buyer_cohorts.py` now also points `auto_executor.DATA` at tmp, because the toggle now reads DATA's live ledger.

### Verify (MODEL_PREDICTION_* unset, `-o addopts='' -q`, never the full suite)

- **Files.** The plan's verify list (`test_auto_buyer_resting_lifecycle`, `test_live_order_safety`, `test_auto_buyer_cohorts`, `test_auto_polymarket_buyer`, `test_auto_buyer_ncaab_settle`, `test_auto_buyer_mode_boundary`, `test_auto_buyer_state_versioning`, `test_execution_gate`), plus every test file that imports auto_buyer_ledger, auto_executor or cli.daily, plus `test_dashboard_server`, both spread-side files, and `test_fix_regressions`. That is 37 files.
- **Results.**
  - Worktree, final code: **821 passed, 5 failed**.
  - Base 0db5d783 with the same files (and the new file present): the same 5 failures, plus the 39 red new tests.
  - This change plus the foreign hunks and the untracked nba_ml_v6 files (a scratch tree: base, then E:/MP's `git diff -- src tests`, then this diff), plus `test_auto_buyer_worker_pair`, `test_display_names` and `test_nba_ml_v6_paper_trial`: **879 passed, 5 failed**.
- **The 5 pre-existing failures** are identical at base:
  - test_dashboard_server x3 (`test_export_picks_endpoint` and the two pick_quote tests; they need data absent from a worktree)
  - `test_audit_fixes_regression.py::test_nba_total_margin_v2_model_and_forecast` (known)
  - `test_cfb_structural_v6.py::test_no_module_under_src_imports_or_names_v6_and_no_config_routes_to_it`
- **Checks.**
  - ruff check and ruff format are clean.
  - mypy on the 3 changed source files shows 77 errors, the same count as base. Every error in those files is pre-existing, at shifted line numbers.
  - CRLF: every changed file has `\r\n` == `\n` (daily 1967, auto_buyer_ledger 2686, auto_executor 3420, cohorts tests 1288, mode-boundary 43, auto_polymarket_buyer tests 3098, test_live_order_safety 1854, test_auto_buyer_resting_lifecycle 993).
- **Grep gates** (comment lines excluded):

  | File | Token | Count | Required |
  |---|---|---|---|
  | auto_buyer_ledger.py | `def cancel_resting_live_fallbacks` | 1 | ==1 |
  | auto_buyer_ledger.py | `cancel_all_resting` | 3 | >=2 |
  | auto_executor.py | `LIVE_COHORT_MAX_MODEL_AGE_MINUTES` | 2 | >=2 |
  | auto_executor.py | `auto_quarantine_underperforming` | 4 | >=4 |
  | daily.py | `cancel_all_resting` | 2 | >=2 |

### Open items

- **Restart the dashboard after landing.** The toggle's kill-switch cancel runs in the dashboard process. Until a restart, the toggle only saves the state; the next 15-minute cycle (governed and disabled, so it reconciles with `cancel_all_resting`) and daily 11b/12b cancel the resting fallbacks.
- **The total position key is still unverified**, because no positions are stored. It will show on the first live total position.
- **B7 is inert today.** The buyer reads calibration from the runtime root, which has no `calibration/` directory (`no_artifact`). The repo's `data/calibration` serves `identity_champion` for both live models. The guard arms itself if an artifact appears.
- **D-03 gap.** After the last daily forecast run (21:00 ET), a pick for a late tip can be more than 3 h old at buy time, and it is refused. This is by design.
- **Pre-existing, unchanged.** With credentials, `_build_bought_index` adds `held_events` for every exchange position, including a partial row's own contract. On the live book, a same-contract top-up is therefore already blocked by the position read.

## Task 3

Task 3 adds the cancel-only backstop worker, the governed manual script, the dashboard kill switch and LIVE label, and the live drawdown report (D-01 and D-02 backstop, DD, D-06 warning, C1, C2, C3), plus the docs. It is one code commit, and this section is committed with it. The worker is registered in code only; Task 4 schedules it on the host.

### What changed

- **Backstop worker**, new `src/model_prediction/auto_buyer_cancel_worker.py`.
  - `main()`:
    - Demotes its own priority, then resolves `RuntimePaths(require_external_runtime=True)`. Without the env var it raises; it never falls back to repo `data/`.
    - Takes its OWN lock, `locks/auto_buyer_cancel.lock`, non-blocking. If that lock is held it prints `skipped` and exits 0.
  - `_run()`:
    - Calls Task 2's `cancel_resting_live_fallbacks(data_root=<runtime root>, cancel_all=not enabled, start_lead_seconds=30, skip_order_ids=<memo>, reason=backstop_tipoff | backstop_kill_switch)`.
    - Memoises cancelled and already-terminal ids in `auto_buyer_cancel_worker_memo.json` (atomic write under its lock, pruned after 3 days). Failed ids are never memoised.
    - Prints one JSON line. It exits 1 when any cancel failed or the helper raised, otherwise 0.
  - It never writes the ledger. With nothing due it builds no executor and makes no network call.
- **Scheduling.**
  - `run_supervisor.py`:
    - `WORKERS["auto-buyer-cancel"]`.
    - `DEFAULT_WORKER_TIMEOUTS["auto-buyer-cancel"] = 120.0`.
  - `system_health.py`: `_SKIP_ESCALATION["auto-buyer-cancel"] = (15, 0.5)`.
  - `scripts/register_windows_tasks.ps1`:
    - Adds a `ModelPrediction-AutoBuyerCancel` block: daily at 08:00, every 2 min, for 18 h.
    - The `-LogonType` default is now `S4U`. Every live ModelPrediction-* task is S4U (read 2026-11-25), so re-running the whole script no longer flips them to Interactive.
    - The stale "every 30 minutes" header is corrected.
  - New `scripts/register_auto_buyer_cancel_task.ps1` registers ONLY the new task. It copies the principal, the settings and the action from the live `ModelPrediction-AutoBuyer` task and swaps only `run auto-buyer }` for `run auto-buyer-cancel }`, checking that the match is exactly 1. It does not start the task. Both ps1 files parse with 0 errors, and a dry string swap against the live action was checked.
- **Drawdown**, new `src/model_prediction/portfolio/auto_buyer_drawdown.py::live_cohort_drawdown_report`, the single entry point for the Telegram task.
  - Cohorts:
    - Active live cohorts come from `_parse_cohorts`.
    - `approved_at_utc` is taken from the raw state entry, since `BuyCohort` has no such field.
    - A date-only approval (the real value is `"2026-11-25"`) means midnight Eastern. `parse_utc` rejects a date-only value, so a naive parse would have silently dropped the filter.
  - Rows:
    - The live ledger is read at `<data_root>/auto_buyer_ledger.jsonl`. This avoids `resolve_auto_buyer_paths`'s paper-root redirect.
    - Rows match the model, the sport casefolded (`NCAAB` in real rows) and the market.
    - Excluded: void zero-fill, manual and submit_unknown rows.
  - Figures and warnings:
    - Exact figures as in the plan, plus `rows`, `open` and `open_cost_usd`.
    - Thresholds come from the state keys `drawdown_warning_usd` and `drawdown_warning_units`, with defaults 100 and 20. A bool, a non-finite, a zero or a negative value falls back to the default.
    - The comparison is made after rounding to cents (USD) and 0.01U.
    - The warning uses CURRENT drawdown, so it clears on recovery; the max drawdown is reported alongside.
  - It never raises: an error returns `status: error`.
- **auto_executor.py.**
  - `_live_drawdown(state)` reads `DATA` at call time and never raises.
  - `auto_buyer_status()` returns the state plus:
    - `live_cohorts` (labels)
    - `live_trading_active`: `enabled is True`, and either live cohorts exist or the legacy ungoverned `mode: live` applies (no `cohorts` key)
    - `live_drawdown`
  - `run_auto_buyer_cycle`, on a governed, non-preview cycle:
    - sets `run_summary["live_drawdown"]` (saved in `last_run`);
    - copies the warnings into the live pass summary as `drawdown_warnings`.

    The warnings never enter `alarms`, so they cannot raise `LiveAutoBuyerAlarm`.
  - Edits sit near lines 1478-1512 and 1997-2003, away from the foreign hunks near 820 and 2668.
- **routes.py**: `/api/auto-buyer/status` returns `auto_buyer_status()`. `/api/auto-buyer/toggle` already returned `toggle_auto_buyer`'s dict, including `kill_switch_cancel`.
- **dashboard.html.**
  - One derivation, `autoBuyerLiveState`, decides ON and LIVE for every surface:
    - ON: `enabled === true` only.
    - LIVE: `live_trading_active === true`, or the legacy `mode: live` with no `cohorts` key.
    - The surfaces: the button, the pill, `updateNcaabPaperUI` (which used to overwrite the LIVE pill with "MAIN + PAPER ON"), `renderOverview` and the "Run main buyer check" confirm.
  - `toggleAutoBuyer`:
    - Reads the current state from the cached server status, not the button text.
    - With live cohorts, the enable confirm lists each one, says REAL MONEY, and is `danger`.
    - On failure (`res.ok`, a non-JSON 401, `status` refused or error, or a fetch throw), it alerts and re-renders from a fresh `/api/auto-buyer/status`. It never renders from the failed body.
    - On success it also renders from a fresh status, then reports `kill_switch_cancel` counts.
  - `loadAutoBuyerStatus` puts the pill into "status unavailable, reload" (`data-state="unknown"`) when the status can't be read. A `_autoBuyerStatusOk` flag stops the paper-trial pill from re-rendering the main pill from the stale cache afterwards (found in self-review: red `test_status_failure_marks_the_pill_unavailable[True]`, then green).
  - Live state: "ON (LIVE)" with the `live` class, and the pill "LIVE: real orders ON" with a red dot.
  - New drawdown block `#autoBuyerLiveDrawdown` in the auto-buyer menu, rendered by `liveDrawdownHtml`: W-L-P, ROI, P&L in $ and U, current and max drawdown, and warnings in `class="red"`. Prices only, no American odds.
- **scripts/auto_polymarket_buyer.py (C3).** `main(argv=None)` changes:
  - `--execute` refuses with exit 2 when the state has a `cohorts` key or `enabled` is not `True`.
  - It then takes `<runtime lock_root>/daily.lock` non-blocking, resolved with `require_external_runtime=True`. If the lock is held it refuses with exit 3; otherwise it holds the lock for the run.
  - The dry run is unchanged and takes no lock.
- **Docs.**
  - `docs/DEBUG.md`: a new top entry, "2026-11-25 — Live auto-buyer safety (quick-261125-hih)". It covers every defect with its trace and fix, the Idempotency-Key facts, the GTD design and its read-back, the no-ledger-lock decision, the kill switch, the backstop, the drawdown, the unknown-row procedure, and the known limits.
  - `docs/ROADMAP.md`: Horizon 1 item 4 lists the open items: unknown-row tooling, Telegram wiring, the total position key, and the same-key retry switch.
  - `docs/PROJECT_STATUS.md`: a one-paragraph top entry.

### Tests

- **New `tests/test_auto_buyer_cancel_worker.py`.**
  - Worker tests use a strict per-id exchange fake, which raises on anything but `order_snapshots` and `cancel`. `httpx.request` raises.
  - Worker: nothing due, due, disabled, missing or unreadable state (4 variants), memo, corrupt memo, memo pruning, failed cancel (exit 1, not memoised, retried), helper failure, own lock held, `daily.lock` held (still runs), and fail-closed without a runtime root.
  - Scheduling: supervisor, ps1, the standalone snippet, and health.
  - Script: refusals (5 states), `daily.lock` held, an allowed run under the lock, and the dry run.
- **New `tests/test_auto_buyer_drawdown.py`.** Exact figures with every exclusion, last-30 at 15 versus 14, last-30 window, $100 versus $99.99, 20U independent of USD, overrides and bad values, recovery, never raises, no live cohorts, `auto_buyer_status` (3 enabled values, legacy live mode, drawdown failure), the status route, and cycle `last_run` plus a warning that never alarms, and a preview that computes nothing.
- **`tests/test_dashboard_html.py` (+9 node tests).** The auto-buyer controls block runs in Node against a stub DOM and a scripted fetch: the refused toggle (401 non-JSON, and 200 refused), string `"true"` not ON, LIVE with the paper trial on, armed, the enable confirm, a successful disable rendered from a fresh status with cancel counts, status unavailable, and the drawdown block.
- **Combined:** the three files run 74 tests, all passing.

### Red before the fix

- **Against base 1e49d24c.** The two new test files fail at collection: `ImportError: cannot import name 'auto_buyer_cancel_worker'` and `ModuleNotFoundError: No module named 'model_prediction.portfolio.auto_buyer_drawdown'`. The new node tests: `9 failed, 20 passed`.

  ```
  test_dashboard_html.py:1111: AssertionError: assert 'REAL MONEY' in 'Enable Polymarket Auto-Buyer in PAPER mode?...'
  test_dashboard_html.py:1137: AssertionError: assert ('ON (PAPER)' == 'OFF'
  test_dashboard_html.py:1152: AssertionError: assert 'unavailable' in 'auto-buyer on · paper'
  test_dashboard_html.py:1198: AssertionError: assert 'ncaab-spread-v7:spread:[0.05,0.12):live' in ''
  9 failed, 20 passed
  ```

- **Base plus only the two new modules (wiring absent):** `19 failed, 25 passed`.

  ```
  test_auto_buyer_cancel_worker.py:410: AttributeError: <module 'auto_polymarket_buyer_script'> has no attribute 'load_auto_buyer_state'
  test_auto_buyer_drawdown.py:204: AttributeError: module '...auto_executor' has no attribute 'auto_buyer_status'
  test_auto_buyer_drawdown.py:238: AttributeError: <module '...dashboard.routes'> has no attribute 'auto_buyer_status'
  test_auto_buyer_drawdown.py:278: KeyError: 'live_drawdown'
  FAILED test_supervisor_registers_the_backstop_worker, test_windows_task_script_registers_the_backstop_every_two_minutes,
         test_standalone_registration_snippet_registers_only_the_backstop, test_health_has_a_skip_escalation_for_the_backstop,
         test_execute_refuses_under_cohorts_or_unless_enabled_is_true x5, test_execute_refuses_while_daily_lock_is_held, ...
  19 failed, 25 passed
  ```

### Mutation proofs

Each mutation was a break, then a run of the named test, then a restore by bytes with a sha256 check. The `git diff` hash was identical before and after. All 13 went red, and the set was re-run after the final edit.

| # | Mutation | Red |
|---|---|---|
| M1 | the no-candidate short-circuit removed from the cancel helper | `test_nothing_due_builds_no_executor_and_exits_zero` (executor built) |
| M2 | the worker takes `daily.lock` | `test_daily_lock_held_does_not_stop_the_backstop` |
| M3 | the worker writes the ledger (marks cancelled rows reconciled) | `test_due_fallback_is_cancelled_by_id_and_the_ledger_is_untouched` (ledger bytes changed) |
| M4 | cancel by id replaced with an account-wide cancel | same test (the strict exchange refuses) |
| M5 | failed cancels memoised | `test_failed_cancel_exits_one_and_is_not_memoised` |
| M6 | an unreadable state counts as enabled | `test_missing_or_unreadable_state_counts_as_disabled[None]`, `[{not json]` |
| M7 | the drawdown includes void zero-fill rows | `test_figures_are_exact_and_excluded_rows_never_count` |
| M8 | current drawdown replaced by max | `test_warning_clears_on_recovery_but_max_drawdown_stays` |
| M9 | drawdown warnings joined to the live alarms | `test_cycle_last_run_carries_live_drawdown_and_a_warning_never_alarms` (LiveAutoBuyerAlarm) |
| M10 | the toggle renders from the failed body | `test_refused_toggle_...` x2 |
| M11 | a truthy enabled check | `test_string_true_is_not_on` |
| M12 | the script ignores the cohorts key | `test_execute_refuses_...[state0]`, `[state1]` |
| M13 | the script skips `daily.lock` | `test_execute_refuses_while_daily_lock_is_held` |

### Verify (MODEL_PREDICTION_* unset, `-o addopts='' -q`)

- **Files.** The plan's list (cancel_worker, drawdown, dashboard_html, dual_auto_buyer_dashboard, resting_lifecycle, live_order_safety, auto_buyer_cohorts), plus auto_polymarket_buyer, mode_boundary, state_versioning, run_supervisor, system_health, nba_pretip_capture, ncaaf_prekickoff_capture, auto_buyer_worker_pair, dashboard_server, execution_gate and auto_buyer_ncaab_settle.
- **Worktree, rebased onto d6152144 (the settlement fix): 620 passed, 4 failed** (615 passed on 1e49d24c before the rebase and the self-review test). All 4 fail identically at base in a worktree:
  - `test_run_supervisor.py::test_worker_registry_commands_exist_on_disk`
  - the 3 known `test_dashboard_server` worktree-only failures
- **Checks.**
  - `ruff check` is clean.
  - `ruff format --check` is clean on every changed Python file. The two files it would reformat in the tree, `espn.py` and `test_ingest.py`, are pre-existing.
  - mypy on the 6 changed or new source files: 78 errors, identical to base. Diffing the sorted messages shows no new error; the 6 union-attr errors first introduced were fixed.
  - CRLF holds on every edited and new file (`\r\n` == `\n`).
- **Grep gates** (comment lines excluded):

  | File | Token | Count | Required |
  |---|---|---|---|
  | run_supervisor.py | `auto-buyer-cancel` | 2 | >=2 |
  | register_windows_tasks.ps1 | `auto-buyer-cancel` | 1 | >=1 |
  | auto_buyer_drawdown.py | `def live_cohort_drawdown_report` | 1 | ==1 |
  | dashboard.html | `live_trading_active` | 1 | >=1 |

### Deviations from plan

1. **[Rule 2] The worker reads `enabled` straight from `<runtime root>/auto_buyer_state.json`, not through `load_auto_buyer_state`.** That loader falls back to the repo seed file when the runtime file is missing. A missing, corrupt or non-dict file, or any non-boolean `enabled`, counts as disabled, which means cancel all.
2. **[Rule 2] `register_windows_tasks.ps1` now defaults to `-LogonType S4U`.** The coordinator asked that a full re-run stay safe, and the live tasks are all S4U. The standalone script is still the one to run.
3. **[Rule 1] C1 also fixed in three places the plan did not name:**
   - `setAutoBuyerMode` rendered from a refused body; it now re-renders from a fresh status.
   - `updateNcaabPaperUI` overwrote the LIVE pill.
   - `renderOverview` and the "Run main buyer check" confirm used the legacy `mode` with a truthy `enabled`.
4. **`live_trading_active` also covers the legacy ungoverned `mode: live`** (no `cohorts` key), so a legacy live buyer still shows LIVE.
5. **The drawdown output adds `rows`, `open`, `open_cost_usd` and `approval_filter`.** An unparseable non-date approval keeps every row (`approval_filter: "none"`) rather than hiding them.
6. **`_SKIP_ESCALATION` entry for the new worker.** `system_health` reports a worker that has never run as degraded, so it shows degraded until Task 4 registers the task.
7. **[Rule 1] The plan's unknown-row procedure named `remove_open_rows`, which cannot act on the auto-buyer JSONL** (it is a `PickLedger` method). DEBUG.md instead gives a backed-up hand edit under `daily.lock`: void a primary unknown that never filled, or restate it from the exchange.

### Open items for the orchestrator (Task 4)

- **Register only the new task:** `powershell -NoProfile -ExecutionPolicy Bypass -File E:\MP\scripts\register_auto_buyer_cancel_task.ps1`, elevated if S4U registration requires it. Do not run `register_windows_tasks.ps1`. Then run `Start-ScheduledTask ModelPrediction-AutoBuyerCancel` once and confirm that runs.db shows an `auto-buyer-cancel` run completed with exit 0 and `candidates` 0 (or a cancel of a genuinely due fallback).
- **Restart the dashboard.** The status, LIVE label, drawdown block and toggle handling reach it only after a restart.
- **State keys (optional; the code defaults apply when absent):** `"drawdown_warning_usd": 100.0` and `"drawdown_warning_units": 20.0`.
- **Health.** Until the task's first run, system_health reports "worker 'auto-buyer-cancel' has never run under the supervisor" (degraded).
- **Known limits.**
  - A cancel's audit append can wait about 120 s on the audit lock, which equals the backstop's supervisor timeout. The cancel itself is already sent by then.
  - The backstop never re-verifies a memoised "cancelled" id; reconcile does.
  - The backstop runs 08:00-02:00, the buyer's own window.
  - With a 2-minute cadence and a 30-second lead, the first due run can come up to about 90 s after tip. GTD expiry is the primary mechanism.
  - The new worker adds about 540 runs.db rows a day. `_read_daily_run_history` and system_health filter by worker, but the dashboard's `/runs` list (last 20, unfiltered) and the `latest_run` fingerprint will be dominated by `auto-buyer-cancel`. This is cosmetic, and nothing was changed.
  - Until the dashboard restarts, the new page talks to the old server, which sends no `live_cohorts`. It still reads PAPER while live cohorts trade, which is no worse than before.

## Task 3 review fixes (critical-reviewer: CHANGES REQUIRED)

### Fixes

1. **[MAJOR] Real-money buttons decided from a stale or empty cache.**
   - New `_abFreshStatusOrAlert()` in `dashboard.html`. `toggleAutoBuyer` and `runAutoBuyerNow` (live path) first re-read `/api/auto-buyer/status`. If that read fails, they alert "status unavailable" and send nothing. Otherwise the confirm text and `danger` come from the fresh read.
   - The NCAAB paper-trial run is unchanged; it does not depend on the main status.
2. **[minor] One failed state read counted as the kill switch.** `_buyer_enabled` in the worker now reads up to 3 times, 0.3 s apart. A file that stays missing or corrupt still means cancel all.
3. **[minor] A non-admin S4U re-run deleted tasks.**
   - `register_windows_tasks.ps1` throws before touching any task when `-LogonType S4U` runs in a non-administrator shell.
   - `register_auto_buyer_cancel_task.ps1` throws when not run as administrator, before it unregisters anything.
4. **[minor] Timeout.**
   - The worker stops starting new cancels after `RUN_DEADLINE_SECONDS = 90`. Ids not reached go to `deadline_skipped`, the note is `deadline`, the exit is 0, and they are not memoised.
   - `cancel_resting_live_fallbacks` takes an optional `stop_before` callable, checked before the order read and before each cancel.
   - The supervisor timeout is now 115 s, and the comment is corrected.
5. **Nits.**
   - (a) The drawdown now drops a row whose `cohort` label names a different cohort; untagged rows still match on model, sport and market. The ledger passthrough of `cohort` (plus `cohort_min_edge` and `cohort_max_edge`) is in the parallel buyerfix branch's `_EXECUTION_PASSTHROUGH_FIELDS`, so it was not duplicated here.
   - (b) The single-task script sets `$settings.Enabled = $true` on the copied settings.

### Red (new tests on the landed Task 3 code)

```
test_dashboard_html.py:1240: assert [{'body': '{"...uyer/toggle'}] == []        (toggle POSTed with status unreadable)
test_dashboard_html.py:1259: AssertionError: assert ('REAL MONEY' in 'Enable Polymarket Auto-Buyer in PAPER mode?...no real orders')
test_dashboard_html.py:1273: assert [{'body': '{"...o-buyer/run'}] == []        (run POSTed with status unreadable)
test_dashboard_html.py:1291: AssertionError: assert ('REAL' in 'Run the main Auto-Buyer check now using its saved mode and enabled state?')
test_auto_buyer_cancel_worker.py:289: assert 120.0 == 115.0
test_auto_buyer_cancel_worker.py:318/327: ValueError: substring not found         (no admin pre-check in either ps1)
test_auto_buyer_cancel_worker.py:456/467: AttributeError: ... has no attribute '_sleep'      (no retry)
test_auto_buyer_cancel_worker.py:481/494: AttributeError: ... has no attribute '_monotonic'  (no deadline)
test_auto_buyer_drawdown.py:317: assert (1, 2) == (1, 1)                          (other-cohort row counted)
```

One existing assertion was updated to the new required behaviour: `test_refused_toggle_...` now checks that the LAST status read follows the toggle POST, because the toggle now also reads the status before the POST. Its intent (re-render from a read taken after the failed POST) is unchanged.

### Mutation proofs (break, run, restore by bytes with a sha256 check)

All went red.

| # | Mutation | Red |
|---|---|---|
| N1 | toggle without the fresh-status check | 2 failed |
| N2 | run-now without the check | 2 failed |
| N3 | `STATE_READ_ATTEMPTS = 1` | transient-read test |
| N4 | worker passes no deadline | both deadline tests |
| N5 | helper ignores the deadline between cancels | deadline test |
| N6 | full-script guard disabled | 1 failed |
| N6b | snippet guard disabled | 1 failed |
| N7 | snippet keeps the copied `Enabled` | 1 failed |
| N8 | drawdown ignores the cohort label | 1 failed |

The first N6 run survived because the test only checked that the guard text was present. The test now requires a live `if (... IsInRole(Administrator)) { throw` guard placed before any task change; with that, N6 goes red.
