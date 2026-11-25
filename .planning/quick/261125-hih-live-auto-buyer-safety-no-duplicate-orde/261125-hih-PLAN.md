---
phase: quick-261125-hih
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/data_sources/polymarket_execute.py
  - src/model_prediction/portfolio/auto_buyer_ledger.py
  - src/model_prediction/portfolio/auto_executor.py
  - src/model_prediction/portfolio/auto_buyer_drawdown.py
  - src/model_prediction/auto_buyer_cancel_worker.py
  - src/model_prediction/cli/daily.py
  - src/model_prediction/cli/commands.py
  - src/model_prediction/run_supervisor.py
  - src/model_prediction/dashboard/routes.py
  - dashboard.html
  - scripts/register_windows_tasks.ps1
  - scripts/auto_polymarket_buyer.py
  - tests/test_live_order_safety.py
  - tests/test_auto_buyer_resting_lifecycle.py
  - tests/test_auto_buyer_cancel_worker.py
  - tests/test_auto_buyer_drawdown.py
  - tests/test_execution_gate.py
  - tests/test_dashboard_html.py
  - docs/DEBUG.md
  - docs/ROADMAP.md
  - docs/PROJECT_STATUS.md
autonomous: false
requirements:
  - HIH-B1   # duplicate orders from HTTP retry -> Idempotency-Key on every order POST, no retry of ambiguous outcomes (same-key retry behind a default-off switch), unknown row
  - HIH-B2   # order placed but not recorded -> intent before POST, audit failure after submit is an alarm
  - HIH-B3   # swallowed ledger-write failure -> raise, log, alarm, halt live buying
  - HIH-B4   # dedup gaps: total-line key, audit payload fields, top-up only on the same slug
  - HIH-B5   # execute-time live-ask re-check, 2 cents either direction
  - HIH-B6   # reconcile robustness: cancel without snapshots, cancel missing started fallbacks, post-cancel re-read
  - HIH-B7   # live cohorts refuse non-identity served calibration
  - HIH-D01  # resting fallback expires at tip-off (GTD, start - 90 s) + cancel backstop
  - HIH-D02  # kill switch cancels resting live orders by id (toggle + every disabled cycle)
  - HIH-D03  # live cohorts buy only picks whose model timestamp is <= 3 h old
  - HIH-D06  # live cohort pass: no auto-quarantine, advisory drawdown warning instead
  - HIH-C1   # dashboard kill-switch UI renders server state only
  - HIH-C2   # dashboard shows LIVE when live cohorts trade
  - HIH-C3   # scripts/auto_polymarket_buyer.py --execute governed
  - HIH-C4   # live quarantine latch and void rows in its sample (resolved by D-06)
  - HIH-C5   # post-cancel race: re-read after cancel, never void from a pre-cancel read
  - HIH-C6   # resting cost subtracts fallback_filled_shares
  - HIH-C7   # rows with pending exposure never dropped from dedup or own game exposure
  - HIH-C8   # direct test of the strict enabled check in daily step 12
  - HIH-C9   # running cycle re-checks enabled before each live submission
  - HIH-D07  # no test orders; GTD verified by per-order read-back and the first live fallback
  - HIH-DD   # live cohort drawdown report (status field, dashboard, one Telegram entry point)

estimate:
  tokens: 240000
  raw_tokens: 240000
  tasks: 4
  confidence: low

must_haves:
  truths:
    - "A POST /v1/orders that times out after the exchange accepted it never produces a second order. Every order POST carries one Idempotency-Key per leg, and an ambiguous outcome is never retried (same-key retry only behind a default-off switch). The ambiguous outcome becomes an ORDER_STATE_SUBMIT_UNKNOWN live-ledger row. That row blocks a re-buy of the pick and game market, counts its requested cost toward the caps, raises an alarm and halts live buying for the rest of the pass. A connect-phase failure, where nothing was sent, is REFUSED, never unknown (B1)"
    - "Every live order whose POST was sent ends up in the live ledger. Its intent is audited before the POST. An audit or ledger-write failure after a placed order is recorded as an alarm and halts live buying; it is never recorded as 'refused' or silently swallowed (B2, B3)"
    - "A live resting fallback is TIME_IN_FORCE_GOOD_TILL_DATE, with goodTillTime set to the event start minus 90 s (clamped to the exchange gameStartTime). It is read back by id and cancelled on any mismatch. If the exchange rejects the GTD leg, the IOC fill is still recorded and an alarm is raised. No auto-buyer order is ever GOOD_TILL_CANCEL (D-01)"
    - "Turning the buyer off cancels every resting live fallback by order id. This happens in toggle_auto_buyer(False), in every cycle and daily reconcile while disabled, and in the backstop. There is never an exchange-wide cancel. A cycle already running re-checks enabled before each live submission (D-02, C9)"
    - "The cancel-only backstop worker runs under its own lock (not daily.lock). It cancels by id any live fallback whose event starts within 30 s, and every live fallback while the buyer is disabled. It makes no network call when nothing is due and never writes the ledger (D-01, D-02)"
    - "A live cohort order is refused when the live ask differs from the ticket price by more than 2 cents, when the pick's model timestamp is older than 3 h, or when the served calibration is not identity (B5, D-03, B7)"
    - "Dedup links a total at a moved line (tsc- slugs) to the held total. It never tops up a partial row on a different contract. It keeps spread and total on one game independent under the shared $25 per-game cap. Reconcile cancels started-game fallbacks even when order snapshots fail, and never voids a row from a pre-cancel read (B4, B6)"
    - "The live cohort pass never auto-quarantines. Per live cohort, the settled record, ROI and peak-to-trough drawdown ($ and U) with advisory warnings appear in last_run, in /api/auto-buyer/status and on the dashboard auto-buyer panel. The dashboard shows LIVE whenever live cohorts trade, and shows OFF only when the server says enabled is not true (D-06, DD, C1, C2)"
  artifacts:
    - path: src/model_prediction/data_sources/polymarket_execute.py
      provides: "Idempotency-Key on order POSTs, OrderSubmitUnknownError, order_intent audit, GTD fallback with read-back, live-ask deviation check, open_orders / preview_order read helpers"
      contains: "TIME_IN_FORCE_GOOD_TILL_DATE"
    - path: src/model_prediction/portfolio/auto_buyer_ledger.py
      provides: "AutoBuyerLedgerWriteError, submit_unknown rows, total-line event key, robust reconcile with cancel_all_resting, cancel_resting_live_fallbacks (cancel-only, no ledger write)"
      contains: "def cancel_resting_live_fallbacks"
    - path: src/model_prediction/portfolio/auto_executor.py
      provides: "live submission alarms and halt, LiveAutoBuyerAlarm, enabled re-check, 3 h live age, identity-calibration guard, live quarantine off, kill-switch cancel in toggle and cycle, auto_buyer_status"
      contains: "LiveAutoBuyerAlarm"
    - path: src/model_prediction/auto_buyer_cancel_worker.py
      provides: "cancel-only backstop worker with its own lock and memo"
      contains: "def main"
    - path: src/model_prediction/portfolio/auto_buyer_drawdown.py
      provides: "live_cohort_drawdown_report, the single function the later Telegram task calls"
      contains: "def live_cohort_drawdown_report"
    - path: tests/test_live_order_safety.py
      provides: "end-to-end live order path tests over a faked httpx exchange"
      contains: "Idempotency-Key"
  key_links:
    - from: src/model_prediction/portfolio/auto_executor.py
      to: src/model_prediction/data_sources/polymarket_execute.py
      via: "OrderTicket(resting_expires_at_utc=start-90s, max_live_price_deviation=0.02) -> PolymarketExecutor.execute; OrderSubmitUnknownError and audit_error handled as alarms, never 'refused'"
      pattern: "resting_expires_at_utc"
    - from: src/model_prediction/portfolio/auto_executor.py
      to: src/model_prediction/portfolio/auto_buyer_ledger.py
      via: "record_auto_buy_execution raises AutoBuyerLedgerWriteError on the live ledger; toggle_auto_buyer(False) calls cancel_resting_live_fallbacks; the cycle passes cancel_all_resting when disabled"
      pattern: "cancel_resting_live_fallbacks"
    - from: src/model_prediction/auto_buyer_cancel_worker.py
      to: src/model_prediction/portfolio/auto_buyer_ledger.py
      via: "cancel_resting_live_fallbacks(cancel_all=not enabled, start_lead_seconds=30) under auto_buyer_cancel.lock"
      pattern: "cancel_resting_live_fallbacks"
    - from: src/model_prediction/run_supervisor.py
      to: src/model_prediction/auto_buyer_cancel_worker.py
      via: "WORKERS['auto-buyer-cancel'] + DEFAULT_WORKER_TIMEOUTS; Windows task ModelPrediction-AutoBuyerCancel every 2 min"
      pattern: "auto-buyer-cancel"
    - from: src/model_prediction/dashboard/routes.py
      to: src/model_prediction/portfolio/auto_executor.py
      via: "/api/auto-buyer/status -> auto_buyer_status() (live_cohorts, live_trading_active, live_drawdown); /api/auto-buyer/toggle returns kill_switch_cancel"
      pattern: "auto_buyer_status"
---

<objective>
Make the LIVE scheduled auto-buyer safe as a real-money system. It has been live since 2026-11-25 for `ncaab-spread-v7` and `ncaab-total-v6` (saved and live-ask edge 5-12%, $5/unit, $25/game, $500/day spend, $250/day settled-loss stop, 35-minute buy window). This plan fixes seven verified defects, implements five operator decisions, closes nine review items from critical-reviewer cycle 2 on 6ff6ac5c, and adds an advisory drawdown report.

Operator decisions (2026-11-25, direct; the orchestrator relayed D-06 as confirmed):
- **D-01** Resting fallbacks stay for live cohorts, but expire at tip-off.
  - They use TIME_IN_FORCE_GOOD_TILL_DATE with goodTillTime set to the event start minus about 90 s, plus a cancel backstop.
  - If the exchange rejects the GTD payload, no fallback is placed, the IOC result stands, and the rejection is logged and surfaced.
  - There is never a fallback to an un-expiring GTC.
- **D-02** Turning the buyer off cancels resting live orders by order id from the ledger, never with an exchange-wide cancel-all. This happens immediately from toggle_auto_buyer(False), best effort, and again in every cycle while disabled.
- **D-03** Live cohorts buy only picks whose model timestamp is within 3 hours. The global max_model_age_minutes stays 720 for paper and legacy.
- **D-04** No thin-market or liquidity guard. This plan adds none.
- **D-05** The daily loss stop is unchanged: settled losses only. This plan does not touch it.
- **D-06** Auto-quarantine is OFF for the live cohort pass. Instead there is a concrete drawdown warning (spec DD below), shown in a status field and on the dashboard. Telegram wiring is a later task; leave one function it can call.
- **D-07** Never place a test order. GTD format verification has two parts:
  - In code, every fallback is read back and cancelled on mismatch.
  - The orchestrator observes and reads back the first live fallback.
  - The `/v1/order/preview` probe is optional and needs a separate operator yes, because the docs call it "preview before submission" but never state that it places nothing.

Defects (verified against code in this session):
- **B1** `_request` (polymarket_execute.py:641-685) retries any httpx.HTTPError on POST /v1/orders with no idempotency.
- **B2** The `order_executed` audit (:525) runs after `_submit`. An AuditLockTimeout is caught at auto_executor.py:3004 and recorded as "refused".
- **B3** The ledger-write failure is swallowed in two places: auto_executor.py:2999-3000 `pass`, and, deeper, `record_auto_buy_execution` itself. Its JSONL append (auto_buyer_ledger.py:472-476) and top-up rewrite (:392-395) only log, so the caller never sees a failure.
- **B4** Dedup gaps:
  - (a) Total-line slugs are not normalised by `event_key_from_slug` (:994-1004).
  - (b) The audit payload lacks event_id, selection and market_type, so audit-derived event keys are empty.
  - (c) A partial-row top-up can price a new-line contract (`_get_partial_fill_info` :2131-2163 matches by pick id or event+selection).
- **B5** `_verify_live_side_and_timing` (:560-618) never compares the live ask with ticket.price.
- **B6** Reconcile:
  - It returns before any cancel when `order_snapshots` fails (:617-620).
  - It skips a started fallback that is missing from the snapshot (:663-665).
  - It marks a row terminal or void from a fill count read before the cancel (:667-669, :797-818).
- **B7** The served calibration is not pinned for live cohorts (`calibrate_for_serving` :2673).

Coordinator items (critical-reviewer cycle 2):
- **C1** The dashboard toggle never checks res.ok.
- **C2** The dashboard shows "ON (PAPER)" while live cohorts trade.
- **C3** `scripts/auto_polymarket_buyer.py --execute` bypasses governance.
- **C4** The live quarantine latches, and void zero-fill rows count in its sample (resolved by D-06).
- **C5** Post-cancel race (same as B6).
- **C6** `_resting_fallback_cost` double-counts after a partial fallback fill.
- **C7** The dead-row filter and the own-pick game exposure drop rows that still have resting exposure.
- **C8** No direct test of the strict `enabled is True` check at daily.py:1799.
- **C9** A running cycle never re-checks `enabled` before a live submission.

**DD (drawdown warning spec).** For each active live cohort, compute from the live ledger:
- Rows: the cohort's model, sport and market, executed on or after the cohort's approved_at_utc date, excluding void zero-fill rows, manual rows and submit_unknown rows.
- Figures: settled record (W-L-P), cost, P&L in $ and U, ROI, last-30-settled ROI, max peak-to-trough drawdown, and current drawdown from peak, each in $ and U.
- Warning rules:
  - The last-30 settled ROI is below -10% with at least 15 settled (the old quarantine rule, now advisory).
  - Or the current drawdown from peak reaches `drawdown_warning_usd` (state key, default 100.0) or `drawdown_warning_units` (state key, default 20.0).
- Exposure: in `last_run` and `/api/auto-buyer/status`, and on the dashboard auto-buyer panel.
- Entry point for the Telegram task: `portfolio/auto_buyer_drawdown.py::live_cohort_drawdown_report`.

Purpose: no duplicate orders, every exchange order recorded, nothing resting into a live game, a kill switch that actually stops exposure, and an operator who can see the live book's health.

Output:
- Three atomic code commits (Task 1 exchange boundary and ledger truth; Task 2 dedup, reconcile, kill switch and live gates; Task 3 backstop worker, operator surfaces and drawdown), each landed separately into the live shared tree.
- Then the orchestrator's Task 4: land check, dashboard restart, registering only the new backstop task, observing the first GTD fallback.
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md
@.claude/CLAUDE.md
@.planning/quick/261125-31d-auto-buyer-per-model-cohorts-paper-ml-v7/261125-31d-PLAN.md
@.planning/quick/261125-31d-auto-buyer-per-model-cohorts-paper-ml-v7/261125-31d-SUMMARY.md
@docs/handoffs/HANDOFF.md
@src/model_prediction/data_sources/polymarket_execute.py
@src/model_prediction/portfolio/auto_executor.py
@src/model_prediction/portfolio/auto_buyer_ledger.py

## Coverage audit (every source item -> task)

| Item | Task | Item | Task | Item | Task |
|---|---|---|---|---|---|
| B1 retry / idempotency | 1 | D-01 GTD fallback | 1 (+3 backstop, 4 observe) | C1 toggle res.ok | 3 |
| B2 intent + audit-after-submit | 1 | D-02 kill switch cancel | 2 (+3 backstop) | C2 LIVE label | 3 |
| B3 ledger write failure | 1 | D-03 3 h live age | 2 | C3 script --execute | 3 |
| B4a total-line key | 2 | D-04 no liquidity guard | none (nothing added) | C4 quarantine latch | 2 (D-06) |
| B4b audit payload fields | 1 | D-05 loss stop unchanged | none (not touched) | C5 post-cancel race | 2 |
| B4c top-up same slug | 2 | D-06 live quarantine off + warning | 2 (off) + 3 (warning) | C6 resting cost | 2 |
| B5 live-ask re-check | 1 | D-07 no test orders | 1 (read-back) + 4 | C7 dead-row / own exposure | 2 |
| B6 reconcile robustness | 2 | DD drawdown spec | 3 | C8 daily strict test | 2 |
| B7 calibration identity | 2 | backstop worker + task | 3 (code) + 4 (register) | C9 re-check enabled | 1 |

Out of scope (separate later task): Telegram alerts (outside the repo), tracking/dashboard analytics beyond DD, a liquidity guard (D-04), loss-stop changes (D-05).

## Facts established while planning (the brief differs on four points; trust these)

1. **The Polymarket US API does support idempotency.**
   - Source: docs.polymarket.us/api-reference/orders/overview.md, "Idempotency and Ambiguous Retries": "Send the `Idempotency-Key` header with every `POST /v1/orders` request". The value must be non-empty and at most 255 bytes.
   - Make a new value for each placement intent, and reuse the same value only when retrying that same request.
   - The API keeps a successful response for 24 h and returns it on a same-key retry.
   - A concurrent retry can return `ABORTED`; wait briefly and retry with the same key.
   - After an ambiguous outcome, do not submit with a new key. Retry with the same key and reconcile through `GET /v1/orders/open` and recent fills.
   - The old `idempotencyKey` spelling is ignored.
   - The section text was read verbatim with curl (lines 315-325 of the .md). The HTTP status that carries `ABORTED` is not documented. The create-order schema page itself does not mention the header; only the overview page does.
   - **Plan decision:** the key goes on every order POST, but an ambiguous outcome is NOT retried. That is the operator's explicit instruction, and nothing short of a forbidden test order can prove the header is honoured. Same-key retry exists behind the default-off constant `ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY` for a later operator decision.
   - There is no client order id field (the create-order and open-orders schemas have only `id`).
   - `GET /v1/orders/open` takes repeated `slugs=` query params and has no pagination.
   - The rate limit is 20 req/s per key, and HTTP 429 when exceeded.
   - The create-order request has `tif` (DAY / GOOD_TILL_CANCEL / GOOD_TILL_DATE / IMMEDIATE_OR_CANCEL / FILL_OR_KILL) and `goodTillTime` (string, format unstated; the response types it date-time). The API is protobuf-JSON shaped, so use RFC 3339 UTC with a `Z` suffix and second precision. Read it back to confirm.
   - `ORDER_STATE_EXPIRED` is "Order expired (GTD orders)".
   - Batched cancel ids "do not certify that each ID was acted on". Always re-read an order's state after a cancel.
2. **Real NCAAB slug shapes** (E:/model-prediction-runtime/odds/ncaab/2026-11-25/polymarket_snapshots.jsonl: 6811 asc-, 7229 tsc-, 528 aec-):
   - Spread: `asc-cbb-jac-stbr-2026-11-25-pos-11pt5`, event key `cbb-jac-stbr-2026-11-25`.
   - Moneyline: `aec-cbb-lbst-sutah-2026-11-25`, key unchanged.
   - Total: `tsc-cbb-abchr-sdkst-2026-11-25-117pt5`, today unnormalised, so every total line is its own key.

   There is **no `-total-NptM` form**. Exchange positions are keyed by full market slug: `_manual_position_rows` matches position keys against ledger `market_slug`, and the 12c7f2e7 fixtures key positions as `asc-...-pos-1pt5`. No total position has been recorded yet, so how positions key a total is unverified. Task 2 checks this read-only first. Spread and total keys of one game must stay DIFFERENT: the 31d shared per-game cap deliberately allows a spread and a total on one game.
3. **A failing fallback leg erases the IOC fill today.** In `_submit` (:801), a raising fallback `submit_order` propagates out of `execute`, and auto_executor records the already-filled IOC as "refused". A rejected GTD payload would hit exactly this path, so the fallback leg must be isolated.
4. **An ambiguous IOC POST cannot be found in `/v1/orders/open`.** An IOC never rests. Only the resting leg can be adopted from open orders, and only by a heuristic match (intent, price, quantity, createTime), since there is no client id. An IOC that is still ambiguous after same-key retries becomes an unknown row plus an alarm. Never resubmit it with a new key.
5. **Current paths and helpers:**
   - `_request` keeps a 3-positional-argument signature. About 15 tests in tests/test_execution_gate.py fake it with `lambda method, path, payload: ...`.
   - The fixtures `fresh_quote` / `btts_quote` carry no `ask`, so a universal price check would break them. The check applies only when the new ticket field is set.
   - Snapshot asks are in outcome coordinates: `snapshot[side]["ask"]`, with short ask = 1 - best bid (polymarket_us.py `_build_snapshot` ~739-790), the same values auto_executor uses as `clob_ask`.
   - Only auto_executor.py:2824 sets `ioc_fallback_resting`.
   - `PolymarketExecutor(` is also constructed in cli/commands.py, auto_buyer_ledger.py and manual_bet_ledger.py.
6. **There is no ledger lock.** `_write_jsonl_atomic` is a read-modify-write with an atomic rename (auto_buyer_ledger.py:91-116). Everything that rewrites the live ledger runs under `daily.lock`: the worker, the daily job, and the dashboard `/api/auto-buyer/run`. **Decision:** no ledger lock is added. Every new cancel-only path (the toggle in the dashboard process, and the backstop) cancels by id and NEVER writes the ledger. Restating fills stays with reconcile under daily.lock.
7. `calibrate_for_serving` returns `ServingCalibration(probability, raw_probability, applied, method, reason)`. Identity means `applied is False` and `method == "identity"` (calibration_pipeline.py:792-835).
8. Every file this plan edits is CRLF in the worktree (checked: 972/972, 3013/3013, 2422/2422 CRLF lines, and so on).

## Shared-tree protocol (applies to EVERY code task; same protocol 31d review cycle 1 used)

The scheduled buyer imports the LIVE working tree every 15 min, and daily step 12 runs the buy cycle too. auto_executor lazily imports auto_buyer_ledger and dashboard.orders inside functions, so landing mid-run could mix old and new modules. Another session has UNCOMMITTED hunks in:
- `src/model_prediction/portfolio/auto_executor.py` (13 lines: NCAAF always-off in `_is_disabled_auto_buyer_sport_market` ~716-730; NBA spread 0.035 floor in `_effective_min_edge` ~2237-2242)
- `auto_buyer_worker.py`, `ncaab_paper_trial.py`, `display_names.py`
- `tests/test_auto_buyer_worker_pair.py`, `tests/test_auto_polymarket_buyer.py`, `tests/test_display_names.py`, `tests/test_ncaab_auto_buyer_paper_trial.py`
- untracked `nba_ml_v6_paper_trial.py` and `tests/test_nba_ml_v6_paper_trial.py`
- many data/ and outputs/ files

Never commit, revert, reformat or overwrite them. This plan does not edit `auto_buyer_worker.py` at all; surface alarms through exceptions it already catches.

1. **Work only in a separate git worktree.** Create it at `$TEMP/261125-hih/wt-<task>` on branch `agent-hih-<task>` from the current HEAD. Run tests there with `PYTHONPATH=<wt>/src` and `E:/MP/.venv/Scripts/python`, and first confirm `python -c "import model_prediction; print(model_prediction.__file__)"` resolves into the worktree.
2. **Snapshot the foreign hunks** before landing: `git diff -- <file>` for each edited file goes to `$TEMP/261125-hih/foreign-<name>.patch` (may be empty). Record `git log -1 --format=%H -- <file>`.
3. **Land only a complete, tested commit.** Export own patch = `git diff <base>..agent-hih-<task>`. Land only at minute :06-:09, :21-:24, :36-:39 or :51-:54, never within 5 min of :00/:15/:30/:45. Before landing:
   - Query runs.db (resolve its path through `RuntimePaths`) and confirm no `daily`, `auto-buyer` or `auto-buyer-cancel` run is started without completion.
   - HEAD must still equal `<base>`; if it moved, rebase the worktree branch and retest.

   Then, in E:/MP, run `git apply --cached --check` and `git apply --check`, then `git apply --cached <patch>`, then `git apply <patch>`. `git diff` must equal the foreign snapshots, apart from `index`/`@@` lines, and `git diff --cached --name-only` must list only own paths. Commit with plain `git commit` (never `git commit <paths>`), then confirm `git diff agent-hih-<task> HEAD -- <own files>` is empty.
4. **Message trailer.** Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
5. **After landing**, re-run the task's verify files in E:/MP itself. Tests read tmp dirs only, so this checks the code together with the foreign hunks.

**Each landed commit must be safe alone,** because it goes live within 15 minutes:
- Task 1 alone: GTD fallbacks, idempotent POSTs and alarms are live.
- Task 2 alone: the kill switch cancels, and reconcile is robust.
- Task 3 alone: the backstop code exists but is unscheduled until Task 4.

Interim behaviour must only ever be more conservative.

## Line endings, test policy, red-first

- Write new files CRLF. After editing, check bytes with python: every `\n` must be preceded by `\r`.
- Run micro-targeted pytest only, never the full suite: `env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY timeout 900 E:/MP/.venv/Scripts/python -m pytest <files> -o addopts='' -q`.
- Run ruff check, ruff format --check, and mypy on every changed Python file.
- Write a red test first for every fix, and save its red output.
- For every safety guard, do a mutation proof: break the guard, watch the named test go red, restore it. Record the red line in SUMMARY.
- Never place a real order. Every exchange interaction in tests is a fake (monkeypatched `httpx.request`, `_request`, or a fake executor). Never edit runtime state files (`auto_buyer_state.json`, ledgers).
- `time.sleep` inside retry loops is monkeypatched to a no-op in tests.
</context>

<tasks>

<task type="tracer" tdd="true">
  <name>Task 1 (tracer): one live cohort order end to end. Idempotent POST, intent before send, GTD fallback with read-back, live-ask check, and every outcome recorded or alarmed (B1, B2, B3, B4b, B5, D-01, D-07, C9)</name>
  <files>src/model_prediction/data_sources/polymarket_execute.py, src/model_prediction/portfolio/auto_buyer_ledger.py, src/model_prediction/portfolio/auto_executor.py, src/model_prediction/cli/commands.py, src/model_prediction/dashboard/routes.py, tests/test_live_order_safety.py, tests/test_execution_gate.py</files>
  <read_first>
    - src/model_prediction/data_sources/polymarket_execute.py (whole file: execute :375-556, _verify_live_side_and_timing :560-618, _request :641-685, _submit :687-848, order_snapshots :909-947, cancel :951-972)
    - src/model_prediction/portfolio/auto_executor.py :1828-1890 (_build_bought_index audit section), :2802-3013 (ticket and live submission block), :1457-1560 and :1606-1695 (run_auto_buyer_cycle passes and state save)
    - src/model_prediction/portfolio/auto_buyer_ledger.py :278-545 (record_auto_buy_execution), :1548-1615 (settle loop void guard)
    - src/model_prediction/audit.py :20-130 (AuditLockTimeout, append, reversed_events)
    - tests/test_execution_gate.py :150-192 (fixtures), :590-820 (submit/IOC/fallback tests that fake _request)
    - tests/test_auto_buyer_cohorts.py (how live cohort passes and fake executors are built)
  </read_first>
  <behavior>
    Tracer (end to end, written first, red before any code). Setup: an AutoPolymarketBuyer live cohort config, a real PolymarketExecutor, a monkeypatched `httpx.request` faking the exchange, and a tmp ledger and audit. The IOC POST /v1/orders carries an `Idempotency-Key` header and the fake answers with a partial IOC fill. Expected: a GTD fallback POST with its own, different key, tif TIME_IN_FORCE_GOOD_TILL_DATE, goodTillTime equal to the event start minus 90 s formatted `YYYY-MM-DDTHH:MM:SSZ`, and participateDontInitiate true. The GET read-back confirms GTD. Result: exactly one exchange IOC order, one live-ledger row with the IOC fill, the fallback id and both keys, and the audit holds order_intent then order_executed carrying event_id, selection and market_type.

    - **Keys and safe retries.** Every order POST carries a key, and a retry of a leg reuses that leg's key. ConnectError then success makes 2 calls with the same key. 429 backs off and reuses the key: rate limits are enforced at the Cloudflare edge before the API.
    - **Connect-phase failure on every attempt.** Nothing was sent, so the result is REFUSED (ExecutionGateError) plus an `order_rejected` audit. It is never an unknown row: an outage must not leave a permanent blocking row and an alarm every cycle.
    - **Ambiguous response (ReadTimeout, 5xx, a 2xx without an id) with the same-key retry switch OFF, the default.** Exactly ONE POST is made. OrderSubmitUnknownError is raised, with no second POST, no new key and no fallback leg. The buyer writes an ORDER_STATE_SUBMIT_UNKNOWN row (shares 0, requested cost kept, fill_known false, submit_unknown true), adds alarm `submit_unknown`, and halts the pass (a second candidate is not submitted). A next-cycle dedup blocks the pick. The row's requested cost counts toward the daily and per-game caps. run_auto_buyer_cycle saves state and history and then raises LiveAutoBuyerAlarm.
    - **Same ambiguity with the switch ON.** The retry reuses the SAME key. A fake that returns the retained original order yields exactly one exchange order, recorded normally. An ABORTED answer (HTTP status undocumented, so detect 409 or a body containing ABORTED) waits and retries with the same key.
    - **Definite 400 on the IOC leg.** REFUSED, plus an order_rejected audit. That intent then no longer blocks the pick in either the single_order policy or the bought index.
    - **order_intent append raises** (AuditLockTimeout). ExecutionGateError, and zero POSTs.
    - **order_executed append raises after a placed order.** execute returns status submitted with audit_error. The ledger row is recorded, alarm `audit_failed_after_submit`, and the pass halts.
    - **Live JSONL append raises.** record_auto_buy_execution raises AutoBuyerLedgerWriteError. The buyer logs ERROR with the order ids, adds alarm `ledger_write_failed`, and halts. The paper ledger path still only warns.
    - **Fallback leg 400 after a partial IOC.** The IOC fill is recorded, fallback_status `rejected` with fallback_error, alarm, and no GOOD_TILL_CANCEL payload. Across the whole file, no POST payload ever carries GOOD_TILL_CANCEL for a ticket with ioc_fallback_resting.
    - **Fallback leg ambiguous.** GET /v1/orders/open?slugs=<slug> runs. With exactly one matching order (intent, price, quantity, tif GTD, createTime at or after intent time minus 5 s), it is adopted as resting. With zero or two matches, fallback_status is `submit_unknown` plus an alarm.
    - **Read-back mismatch.** tif GTC, goodTillTime off by more than 2 s, or the GET fails: cancel by that fallback id, and fallback_status `cancelled_unverified_gtd` plus an alarm.
    - **Expiry timing.** Effective expiry at or before now + 60 s places no fallback (`not_placed_near_start`). A snapshot gameStartTime earlier than the ticket's start clamps the expiry. A ticket with ioc_fallback_resting and no resting_expires_at_utc places no fallback (`not_placed_no_expiry`).
    - **Live-ask deviation 0.02, ticket price 0.52.** Live ask 0.55 is REFUSED before any POST. 0.54 passes. 0.50 passes. 0.49 is REFUSED. A missing or non-finite ask is REFUSED. With the field unset, existing fixtures without an ask pass unchanged.
    - **Kill switch mid-pass.** The state flips to enabled false after the first submission, so the second candidate is never submitted, and live_halt_reason is `kill_switch`.
    - **Cancel audit tolerance.** A cancel whose audit append raises returns status cancelled with audit_error.
    - **Settlement.** settle_auto_buyer_ledger leaves a submit_unknown row open.
  </behavior>
  <action>
    **Exchange client: idempotency and retries (B1)** — src/model_prediction/data_sources/polymarket_execute.py
    - Every POST /v1/orders sends an `Idempotency-Key` header. The key is per placement intent, at most 255 bytes: `mp-<intent_id>-p` for the IOC/primary leg and `mp-<intent_id>-f` for the fallback leg. Any retry of a leg reuses it. Never generate a new key for a leg.
    - Keep `_request(method, path, payload=None)` callable with 3 positional args, so the existing 3-arg test fakes keep working. Carry the key through an instance attribute set in try/finally around the call (or an equivalent that keeps those fakes working).
    - `_request` refuses a POST to /v1/orders that has no key set: ExecutionGateError, nothing sent.
    - Retry policy for order POSTs. This follows the operator's instruction "on read timeout or ambiguous errors do NOT retry". The docs do prescribe same-key retries, but no test order may prove the header is honoured (D-07), and the docs record that the old spelling was silently ignored.
      - 429: back off and retry with the same key. The edge rejects it before the API.
      - Connect-phase errors (httpx.ConnectError, ConnectTimeout, PoolTimeout): nothing was sent, so retry with the same key. If every attempt fails this way, raise ExecutionGateError REFUSED, never "unknown".
      - Ambiguous (ReadTimeout, WriteTimeout, ReadError, RemoteProtocolError, other TransportError, 5xx, a 2xx with a non-JSON or non-dict body, a 2xx without an order id): do NOT retry. Raise the new `OrderSubmitUnknownError(RuntimeError)` at once. It is deliberately NOT an ExecutionGateError subclass, whose docstring promises "Nothing was submitted". Attributes: idempotency_key, market_slug, leg, detail.
      - Module constant `ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY = False`, with a why-comment naming the operator decision it awaits. When True, ambiguous outcomes, and 409 or a body containing ABORTED, retry with the SAME key within the existing 3-attempt budget, then raise OrderSubmitUnknownError. Tests cover both settings; the shipped value is False.
      - Other 4xx: ExecutionGateError REFUSED.
    - GET and cancel POST keep their current retries.

    **Exchange client: intent and audit (B2, B4b)**
    - In `execute`, after every gate and the confirmation and before `_submit`, append audit `order_intent` (subject = ticket.pick_id). Payload: intent_id (uuid4 hex), both idempotency keys, market_slug, token_side, action, order_type, price, size_shares, event_id, selection (lowercased), market_type, line, event_start_utc and created_at_utc, taken from pick_row.
    - If that append raises, refuse with ExecutionGateError before any network call.
    - Definite refusal from `_submit`: best-effort `order_rejected` {intent_id, error}, then re-raise.
    - OrderSubmitUnknownError: best-effort `order_submit_unknown` {intent_id, keys, detail}, then re-raise.
    - The `order_executed` payload also carries event_id, selection, market_type, line, intent_id, idempotency_key, fallback_idempotency_key, good_till_time and fallback_error.
    - If the `order_executed` append raises (OSError, TimeoutError incl. AuditLockTimeout, ValueError, RuntimeError), log ERROR and return `{"status": "submitted", **submission, "audit_error": str(exc)}`. Never raise after an order was placed.
    - The single_order policy treats each of these as a prior buy: an `order_executed` for the pick, an `order_submit_unknown` for the pick, or an `order_intent` for the pick whose intent_id has no `order_rejected`. reversed_events yields newest first, so collect rejected intent ids as you scan.
    - `cancel`: after a successful cancel POST, an audit failure returns `{"status": "cancelled", "order_id": ..., "audit_error": ...}` instead of raising.

    **Exchange client: GTD fallback (D-01)**
    - OrderTicket gains `resting_expires_at_utc: str | None = None` and `max_live_price_deviation: float | None = None`.
    - The IOC remainder fallback is GTD-only:
      - tif `TIME_IN_FORCE_GOOD_TILL_DATE`, `goodTillTime` formatted RFC 3339 UTC to the second with `Z`, participateDontInitiate true, same limit price.
      - Effective expiry = min(ticket expiry, live snapshot `event_start_utc` minus 90 s when the snapshot has one). `_verify_live_side_and_timing` returns the snapshot so `execute` can pass it on.
      - No ticket expiry, or an effective expiry at or before now + 60 s: no fallback.
      - Nothing in the fallback path may ever send GOOD_TILL_CANCEL. Update `describe()` to match.
    - Isolate the fallback leg so its failure never discards the IOC result:
      - ExecutionGateError: fallback_status `rejected` plus fallback_error.
      - OrderSubmitUnknownError: try adoption through the new `open_orders(slugs)` helper (GET /v1/orders/open with repeated `slugs=`); otherwise `submit_unknown`.
    - After placing or adopting a fallback, read it back with GET /v1/order/{id}. It must show tif GOOD_TILL_DATE and a goodTillTime within 2 s of the request. On a mismatch or an unreadable response, cancel it by id: `cancelled_unverified_gtd`.
    - `order_snapshots` items also carry `tif` and `good_till_time`.
    - Return `good_till_time`, `fallback_error`, `idempotency_key` and `fallback_idempotency_key` from `_submit`.
    - The standalone `limit_gtc` order type (the manual resting order) is unchanged.

    **Exchange client: live-ask check (B5)**
    - In `_verify_live_side_and_timing`, when `ticket.max_live_price_deviation` is set: live ask = `snapshot[ticket.token_side]["ask"]`. Refuse a missing or non-finite ask, and refuse `abs(ask - ticket.price) > deviation + 1e-9` in either direction. Nothing is sent.

    **Exchange client: preview probe (D-07)**
    - Add `preview_order(request_body, user_command)`: POST /v1/order/preview with `{"request": ...}`, requiring user_command and credentials, never retried on ambiguity. It exists only for the orchestrator's optional probe; the buyer never calls it.

    **cli/commands.py**
    - Wherever `PolymarketExecutor.execute` is called, catch OrderSubmitUnknownError and report "order outcome unknown, check the exchange before any retry, do not resubmit", never a generic crash or REFUSED. (`grep -n "\.execute(" src/model_prediction/cli/commands.py` finds them.)

    **Ledger truth (B3)** — auto_buyer_ledger.py
    - `record_auto_buy_execution` adds `AutoBuyerLedgerWriteError(RuntimeError)`. When `_uses_live_auto_buyer_ledger(j_path)`, the JSONL append (:472-476) and the top-up `_write_jsonl_atomic` (:392-395) log ERROR and raise it. The paper path keeps warning only. The xlsx export stays best effort.
    - Pass these fields through into the JSONL row: submit_unknown, requested_shares, requested_cost_usd, intent_id, idempotency_key, fallback_idempotency_key, good_till_time, fallback_status, fallback_error.
    - A submit_unknown row has order_id "", order_ids [], shares 0, cost 0, fill_known false, order_state `ORDER_STATE_SUBMIT_UNKNOWN`, status open.
    - In `settle_auto_buyer_ledger`, right after the void / zero-share guard (~1583-1613), leave a submit_unknown row open and count it pending. Only the operator resolves it.

    **Buyer** — auto_executor.py
    - Live tickets set `resting_expires_at_utc` to the event start minus `LIVE_FALLBACK_EXPIRY_LEAD_SECONDS = 90` and `max_live_price_deviation=LIVE_MAX_PRICE_DEVIATION = 0.02`. Both are module constants with a why-comment citing D-01 / B5.
    - New `AutoExecutionConfig.recheck_enabled_before_submit: bool = False`. It is True for the live cohort pass config and for the legacy `should_execute` config in run_auto_buyer_cycle. Before each live `executor.execute`, re-read `load_auto_buyer_state()`. If `enabled` is not exactly True, stop live buying for the pass: no submission, `live_halt_reason="kill_switch"` (C9).
    - Replace the `except ...: pass` around the ledger write (~2985-3000) and the outer `except (ExecutionGateError, RuntimeError, OSError)` handling so that:
      - OrderSubmitUnknownError records an unknown row (requested shares and cost kept), adds alarm `submit_unknown`, and halts.
      - `sub.get("audit_error")` records normally, adds alarm `audit_failed_after_submit`, and halts.
      - Any exception from record_auto_buy_execution after a submission logs ERROR (order ids, pick, slug), adds alarm `ledger_write_failed`, and halts (B3).
      - fallback_status in {rejected, submit_unknown, cancelled_unverified_gtd} adds an alarm and does not halt.
      - Only an ExecutionGateError raised before submission stays "refused".
    - AutoExecutionResult gains `alarms: list[dict]` and `live_halt_reason: str | None`. `_cohort_pass_summary` and the global run_summary carry both.
    - run_auto_buyer_cycle saves state and appends history exactly as today, then raises the new `LiveAutoBuyerAlarm(RuntimeError)` when any live pass has alarms. The exception carries `run_summary`. Its callers:
      - The worker run is recorded as failed. auto_buyer_worker.py already catches RuntimeError, so that foreign-hunk file is not edited.
      - Daily step 12 already catches it (`except Exception`, daily.py ~1843) and still reaches the 12b reconcile.
      - The dashboard `/api/auto-buyer/run` route (routes.py ~733-737) has a try/finally and no except. Add `except LiveAutoBuyerAlarm` there, answering `{"status": "alarm", "alarms": ..., "run": exc.run_summary}` while the lock is still released in the finally.
    - Interim exposure: in `_resting_fallback_cost`, a submit_unknown row returns its requested_cost_usd. That way, between Task 1 and Task 2 landing, an unknown row still counts toward the daily and per-game caps. Task 2 replaces the helper.
    - `_build_bought_index` audit section (:1849-1887):
      - Index `order_intent` (minus rejected intent ids) and `order_submit_unknown` like `order_executed`: pick ids and market sides.
      - Audit-derived event-level keys (event_selections, plus the new event_markets from payload market_type) apply only when `self.config.mode == "live"` or `not self.config.dedup_own_ledger_only`. Paper cohort books keep taking only exact pick-id and market-side keys from the audit, so the enriched payload never makes a live fill block a paper cohort row. This preserves the 31d per-book dedup design.

    **Tests**
    - New tests/test_live_order_safety.py (CRLF): everything in <behavior>. The tracer test is first.
    - Update the two tests in tests/test_execution_gate.py that pin the old GTC fallback (`test_ioc_partial_fill_rests_remainder_at_same_price_not_chased`, `test_ioc_zero_fill_rests_full_remainder`) to supply resting_expires_at_utc and assert GTD. D-01 supersedes the GTC behaviour. Say so in SUMMARY. No other existing assertion may be weakened.

    **Mutation proofs**
    - M1: with the switch ON, a new key per attempt turns the same-key-retry test red. Separately, retrying ambiguous errors while the switch is OFF turns the exactly-one-POST test red.
    - M2: mapping ambiguous to REFUSED turns the unknown test red. Mapping connect-only exhaustion to unknown turns the connect-failure test red.
    - M3: fallback tif back to GOOD_TILL_CANCEL turns the tracer and the no-GTC sweep red.
    - M4: remove fallback isolation turns the fallback-400 test red.
    - M5: drop the deviation check turns the 0.55 test red.
    - M6: restore `pass` on the ledger write turns the ledger_write_failed test red.
    - M7: append the intent after `_submit` turns the intent-failure test red.
    - M8: drop the enabled re-check turns the kill-switch-mid-pass test red.
  </action>
  <verify>
    <automated>cd $TEMP/261125-hih/wt-t1 && PYTHONPATH=$PWD/src env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY timeout 900 E:/MP/.venv/Scripts/python -m pytest tests/test_live_order_safety.py tests/test_execution_gate.py tests/test_auto_buyer_cohorts.py tests/test_auto_polymarket_buyer.py tests/test_auto_buyer_mode_boundary.py tests/test_auto_buyer_ordering_and_lineage.py tests/test_auto_buyer_ncaab_settle.py tests/test_fix_regressions.py tests/test_dashboard_server.py -o addopts='' -q</automated>
    Grep gates, run on the worktree files, comment lines excluded with `grep -v '^\s*#' FILE | grep -c TOKEN`:
    - polymarket_execute.py `Idempotency-Key` [HEAD=0 EXPECT>=1]
    - polymarket_execute.py `TIME_IN_FORCE_GOOD_TILL_DATE` [HEAD=0 EXPECT>=1]
    - polymarket_execute.py `order_intent` [HEAD=0 EXPECT>=2]
    - polymarket_execute.py `/v1/orders/open` [HEAD=0 EXPECT>=1]
    - auto_executor.py `order_intent` [HEAD=0 EXPECT>=1]
    - auto_buyer_ledger.py `submit_unknown` [HEAD=0 EXPECT>=2]

    `ruff check`, `ruff format --check` and mypy on the 4 changed source files must be clean. The CRLF byte check must pass.
  </verify>
  <done>
    - All behaviours above pass, with recorded red output before the fix and the 8 mutation proofs.
    - The verify files pass in the worktree and again in E:/MP after landing.
    - The commit holds only own hunks; the foreign auto_executor.py hunks are still unstaged.
    - A live auto-buyer order can no longer be duplicated by a retry, lost to an audit or ledger failure, or left resting into a game without expiry.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: dedup, reconcile, kill switch, live pick age, identity calibration, live quarantine off (B4a, B4c, B6, B7, D-02, D-03, D-06 part, C4-C8)</name>
  <files>src/model_prediction/portfolio/auto_buyer_ledger.py, src/model_prediction/portfolio/auto_executor.py, src/model_prediction/cli/daily.py, tests/test_auto_buyer_resting_lifecycle.py, tests/test_auto_buyer_cohorts.py, tests/test_auto_polymarket_buyer.py</files>
  <read_first>
    - src/model_prediction/portfolio/auto_buyer_ledger.py :548-850 (reconcile), :993-1005 (event_key_from_slug), :1103-1190 (_manual_position_rows uses _SPREAD_SIDE_SUFFIX to label spreads, so leave it untouched)
    - src/model_prediction/portfolio/auto_executor.py :896-956 (_resting_fallback_cost, _live_book_has_pending_orders, _today_auto_buyer_totals), :1179-1192 (toggle_auto_buyer), :1331-1381 (_cohort_pass_config, _cycle_manages_live_book), :1506-1525 (cycle reconcile call), :1698-1754 (quarantine), :1944-2130 (_build_bought_index ledger section, partial rows), :2131-2163, :2477-2488 (model age), :2667-2700 (calibration and cohort edge), :2731-2805 (game exposure, top-up sizing)
    - src/model_prediction/cli/daily.py :264-278, :1756-1810, :1850-1866
    - The Task 1 SUMMARY (submit_unknown row fields, requested_cost_usd)
  </read_first>
  <behavior>
    - **B4a total-line key.**
      - `event_key_from_slug("tsc-cbb-abchr-sdkst-2026-11-25-117pt5") == event_key_from_slug("tsc-cbb-abchr-sdkst-2026-11-25-120pt5")`.
      - That key differs from the spread key `cbb-abchr-sdkst-2026-11-25` and from the moneyline slug key.
      - The two spread sides of one game still share a key.
      - A held total at one line (ledger row, and separately an exchange position keyed by full slug) blocks a total at a moved line on the same game.
      - A held spread does not block a same-game total, nor the reverse, and the shared $25 per-game cap still applies across them.
    - **B4c top-up.** A partial row whose market_slug differs from the newly resolved slug is rejected as dedup, never topped up. The same slug still tops up.
    - **C6 pending exposure.** After a fallback partially fills (cost_usd already includes fallback_filled_cost_usd), the pending resting cost counts only `fallback_resting_shares - fallback_filled_shares`. An unknown-fill primary counts requested minus already-filled shares. A submit_unknown row counts requested_cost_usd. All three use one helper, read by game exposure, `_today_auto_buyer_totals` and `_live_book_has_pending_orders`.
    - **C7 rows with pending exposure.** A row with pending exposure is never dropped by the dead-row filter. A pick's own pending (unfilled, still resting or unknown) exposure counts in the game room check; only its filled cost is excluded, which the top-up math already subtracts.
    - **B6 snapshots fail.** order_snapshots raises, yet a started true fallback is cancelled by id, the summary carries `snapshot_error`, and nothing is voided.
    - **B6 missing order.** A started true fallback missing from the snapshot is cancelled by id.
    - **C5 post-cancel race.**
      - After a successful cancel, the order is re-read. A re-read showing CANCELED with cum_quantity 3 restates shares and cost with those 3 shares and does not void.
      - A failed or non-terminal re-read keeps the row pending with `cancel_requested_at_utc` set.
      - A pre-cancel zero read never produces result=void.
    - **D-02, cancel_all_resting.** `reconcile_pending_auto_buyer_fallbacks(cancel_all_resting=True)` cancels every non-terminal true fallback (reason `kill_switch`), started or not.
    - **D-02, cancel-only helper.**
      - `cancel_resting_live_fallbacks(cancel_all=False, start_lead_seconds=30)` cancels only due fallbacks.
      - With no candidates, it constructs no executor (the executor factory asserts it is never called).
      - It skips ids already terminal on the exchange, and skips skip_order_ids.
      - It never calls any endpoint other than per-id GET and per-id cancel.
      - The ledger file bytes are unchanged afterwards.
    - **D-02, toggle.** toggle_auto_buyer(False) saves enabled false first, then returns `kill_switch_cancel` with the cancelled ids (fake executor). A cancel failure is reported, never raised, and the state still saves.
    - **D-02, disabled cycle.** A disabled cycle with a pending live fallback cancels it through reconcile.
    - **D-02, daily.** daily.py's own reconcile calls pass cancel_all_resting when the state's enabled is not True.
    - **C8.** daily step 12 does not run the cycle for `enabled: "true"` (string) or 1, and does for True. Test it directly; if step 12 is not reachable in a unit test, extract a tiny predicate used at ~1799 and test both the predicate and the call site.
    - **D-03.** A live cohort row with a model timestamp 181 min old is rejected_stale_model; at 179 min it passes. A paper cohort row at 600 min still passes the 720 global limit.
    - **B7.** A live cohort row whose calibrate_for_serving result has applied true, or a method other than identity, is rejected (new counter rejected_live_calibration, ERROR log, alarm `live_calibration_not_identity`). Paper cohort rows are unaffected.
    - **D-06 / C4.**
      - The live cohort pass config has auto_quarantine_underperforming False, so a live model with 20 losing settled rows still buys.
      - The legacy global pass keeps the quarantine.
      - The quarantine sample excludes void zero-fill rows and submit_unknown rows.
  </behavior>
  <action>
    **auto_buyer_ledger.py**

    (1) Establish the total position key first, read-only. Search the runtime audit (`raw_response` of past portfolio reads) and any cached portfolio snapshot for a `tsc-` position key, without network. Record the finding in SUMMARY. If none exists, rely on full-slug keys, which `_manual_position_rows` and the 12c7f2e7 fixtures already establish, and list it as an open item.

    (2) Total key (B4a). Add `_TOTAL_LINE_SLUG = re.compile(r"^(tsc-.+)-\d+pt\d+$")`, and in `event_key_from_slug` map a total slug to its `tsc-<event>` prefix, keeping the `tsc-` market marker. That way the total key never equals the game's spread key or moneyline key, which the 31d shared per-game cap relies on. Leave `_SPREAD_SIDE_SUFFIX` itself unchanged. Update the docstring with the three real slug shapes.

    (3) Reconcile (B6, C5, D-02).
    - New keyword `cancel_all_resting: bool = False`.
    - An `order_snapshots` failure no longer returns early. Continue with an empty `by_id` and record `snapshot_error`.
    - A true fallback (`fallback_order_id != order_id`) that is not known terminal, either missing from the snapshot or with snapshots failed, gets a cancel by id when its event started or when cancel_all_resting is set (reason `game_started` or `kill_switch`).
    - After every successful cancel, re-read that id with `order_snapshots([fid])`:
      - A terminal state: restate shares and cost from its cum_quantity (capped as today) and mark it terminal.
      - Otherwise: set `cancel_requested_at_utc` and keep it pending.
    - Never set result void, or reconciled, from a read taken before the cancel.
    - The summary gains `cancel_failed` (ids) and `snapshot_error`, but ONLY when they are non-empty. tests/test_auto_polymarket_buyer.py:2228 and :2260 assert the exact 4-key dict, and those tests must still pass.

    (4) New `cancel_resting_live_fallbacks(*, data_root=None, executor=None, now=None, cancel_all: bool, start_lead_seconds: float = 30.0, skip_order_ids: frozenset[str] = frozenset(), reason: str)`. It cancels only and never writes the ledger: no ledger lock exists, and its callers (the dashboard toggle, the backstop) do not hold daily.lock.
    - Read the live ledger read-only.
    - Candidates: rows with a true unreconciled fallback whose id is not in skip_order_ids, and where cancel_all is set or the event start is at or before now + lead. An unparseable start counts as due.
    - No candidates: return `{"candidates": 0, ...}` without constructing an executor.
    - Otherwise build the default executor exactly as reconcile does, and call `order_snapshots(ids)` (failure tolerated). Report terminal ids as `already_terminal`. Cancel the rest one by one with `executor.cancel(order_id, user_command=True, market_slug=slug)`.
    - Return `{candidates, cancelled, already_terminal, failed:[{order_id, error}]}`.
    - Never call any cancel-all or batch endpoint.

    **auto_executor.py**

    (5) Pending exposure (C6). Replace `_resting_fallback_cost` with `_pending_exposure_cost(row)`, with a why-comment:
    - A true fallback: max(0, fallback_resting_shares - fallback_filled_shares) times entry_price.
    - An unknown-fill primary (`fallback_order_id == order_id`): max(0, fallback_resting_shares - shares) times entry_price.
    - A submit_unknown row: requested_cost_usd.

    Use it in `_build_bought_index` game exposure, in `_today_auto_buyer_totals` and in `_live_book_has_pending_orders` (a submit_unknown row counts as pending).

    (6) Dead-row filter (C7). In the `_build_bought_index` ledger loop (~1976-1985), never `continue` past a row whose `_pending_exposure_cost(r) > 0`. In the game-room check (~2735-2740), include the same pick's pending exposure and exclude only its filled cost.

    (7) Top-up (B4c). Right after `_get_partial_fill_info` (~2762), when `partial_info["market_slug"] != market_slug`, count rejected_dedup and continue.

    (8) Kill switch (D-02).
    - `toggle_auto_buyer`: after `save_auto_buyer_state`, when the saved value is False, set `state["kill_switch_cancel"]` from `cancel_resting_live_fallbacks(cancel_all=True, reason="kill_switch")`. Wrap it in try/except and return an error dict on failure; never block or undo the save.
    - run_auto_buyer_cycle passes `cancel_all_resting=not cycle_enabled` to its reconcile call (non-preview).
    - daily.py: both own `reconcile_pending_auto_buyer_fallbacks(data_directory)` calls (~1770, ~1860) pass `cancel_all_resting=load_auto_buyer_state().get("enabled") is not True`. Add the C8 test, extracting a predicate only if needed.

    (9) Live pick age (D-03). Add `LIVE_COHORT_MAX_MODEL_AGE_MINUTES = 180.0` with a comment citing D-03. In the model-age gate (~2484), when `cohort is not None and cohort.mode == "live"`, also reject when the age exceeds it. Paper and legacy rows are unchanged.

    (10) Identity calibration (B7). After `calibrate_for_serving` (~2673), for a live cohort row, reject when `calibration.applied` is true, the method is not "identity", or the probability is not equal to the raw probability. Count it in the new `rejected_live_calibration` (in AutoExecutionResult and the pass summary), log ERROR, and append alarm `live_calibration_not_identity`. Fail closed, so a calibrator written by the calibrate job can never silently move the live window.

    (11) Quarantine (D-06, C4). `_cohort_pass_config` for the live book sets `auto_quarantine_underperforming=False`, with a comment citing the operator decision; the paper book already does. `_get_quarantined_underperforming_models` skips rows with result void, rows with 0 shares, and submit_unknown rows.

    **Tests and mutations**
    - New tests/test_auto_buyer_resting_lifecycle.py (CRLF): everything in <behavior>.
    - Existing tests that pin superseded behaviour. Update each to the new required behaviour and name it in SUMMARY:
      - tests/test_auto_buyer_cohorts.py:1214-1221, `test_paper_cohort_pass_never_quarantines_live_pass_does`, asserts the live pass quarantines. D-06 flips this to False.
      - tests/test_auto_polymarket_buyer.py, `test_reconcile_cancels_resting_order_once_event_has_started` (~2263-2290). Its FakeExecutor returns the same pre-cancel NEW/0 snapshot after the cancel, which is the C5 race it pinned. Make the fake stateful, so a cancelled id then reads ORDER_STATE_CANCELED, and keep its assertions.
      - That file carries FOREIGN hunks: line 32, and 55 lines added after ~3098 (NCAAF and NBA-floor tests). Follow the shared-tree protocol for it. Snapshot the foreign patch, edit only the reconcile test region, build the own patch in the worktree from HEAD, and confirm after landing that `git diff` still shows exactly those foreign hunks.
    - Grep `tests/` for `_resting_fallback_cost` before renaming it (none found at planning time).
    - Mutation proofs:
      - Total key reverted: the moved-line test goes red.
      - Total key collapsed to the spread key: the same-game spread+total test goes red.
      - Early return on a snapshot failure restored: the started-cancel test goes red.
      - Void from the pre-cancel read: the race test goes red.
      - Toggle without cancel: red.
      - cancel_all ignored: red.
      - Live age back to 720: red.
      - Calibration guard removed: red.
      - Live quarantine on: red.
      - Top-up allowed on any slug: red.
      - Exposure double-count restored: red.
  </action>
  <verify>
    <automated>cd $TEMP/261125-hih/wt-t2 && PYTHONPATH=$PWD/src env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY timeout 900 E:/MP/.venv/Scripts/python -m pytest tests/test_auto_buyer_resting_lifecycle.py tests/test_live_order_safety.py tests/test_auto_buyer_cohorts.py tests/test_auto_polymarket_buyer.py tests/test_auto_buyer_ncaab_settle.py tests/test_auto_buyer_mode_boundary.py tests/test_auto_buyer_state_versioning.py tests/test_execution_gate.py -o addopts='' -q</automated>
    Grep gates, comment lines excluded:
    - auto_buyer_ledger.py `def cancel_resting_live_fallbacks` [HEAD=0 EXPECT==1]
    - auto_buyer_ledger.py `cancel_all_resting` [HEAD=0 EXPECT>=2]
    - auto_executor.py `LIVE_COHORT_MAX_MODEL_AGE_MINUTES` [HEAD=0 EXPECT>=2]
    - auto_executor.py `auto_quarantine_underperforming` [HEAD=3 EXPECT>=4]
    - daily.py `cancel_all_resting` [HEAD=0 EXPECT>=2]

    ruff, format and mypy must be clean on the changed files, and the CRLF byte check must pass.
  </verify>
  <done>
    - All behaviours pass, with red output first and the 11 mutation proofs recorded.
    - The verify files pass in the worktree and again in E:/MP after landing.
    - The foreign auto_executor.py hunks are untouched.
    - Disabling the buyer cancels resting live orders by id from every path.
    - A moved-line total can no longer re-enter a held game.
    - Reconcile cancels at tip-off even when order snapshots fail.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: cancel-only backstop worker, governed manual script, dashboard kill switch and LIVE label, live drawdown report (D-01/D-02 backstop, DD, C1, C2, C3)</name>
  <files>src/model_prediction/auto_buyer_cancel_worker.py, src/model_prediction/run_supervisor.py, scripts/register_windows_tasks.ps1, src/model_prediction/portfolio/auto_buyer_drawdown.py, src/model_prediction/portfolio/auto_executor.py, src/model_prediction/dashboard/routes.py, dashboard.html, scripts/auto_polymarket_buyer.py, tests/test_auto_buyer_cancel_worker.py, tests/test_auto_buyer_drawdown.py, tests/test_dashboard_html.py, docs/DEBUG.md, docs/ROADMAP.md, docs/PROJECT_STATUS.md</files>
  <read_first>
    - src/model_prediction/auto_buyer_worker.py (pattern only; do not edit, it has foreign hunks)
    - src/model_prediction/daily_lock.py (acquire_lock)
    - src/model_prediction/run_supervisor.py :68-130
    - scripts/register_windows_tasks.ps1 (whole file, 83 lines)
    - dashboard.html :3800-3910 (loadAutoBuyerStatus, updateAutoBuyerUI, toggleAutoBuyer) and :416 (panel row)
    - src/model_prediction/dashboard/routes.py :495-520, :700-745
    - tests/test_dashboard_html.py :1-90 (node harness)
    - scripts/auto_polymarket_buyer.py (whole file, 131 lines)
    - The Task 2 SUMMARY (the cancel_resting_live_fallbacks signature)
    - `grep -rn '"auto-buyer"' src scripts`, to find any other worker registry (system_health, watchdog) that must learn the new worker name
  </read_first>
  <behavior>
    - **Worker, nothing due.** With only fallbacks starting 10 min out and the buyer enabled: no executor is constructed, no network call, and exit 0 with candidates 0.
    - **Worker, due.** A fallback starting in 20 s gets a per-id cancel only. Never a cancel-all, and the ledger bytes are unchanged.
    - **Worker, disabled.** With the buyer disabled, every pending fallback is cancelled.
    - **Worker, memo.** An id in the memo (cancelled or already terminal) is never re-sent on the next run.
    - **Worker, lock.** If its lock is held, it prints skipped and exits 0.
    - **Worker, runtime root.** A missing `MODEL_PREDICTION_RUNTIME_ROOT` fails closed (`require_external_runtime=True`) and never reads repo `data/`.
    - **Worker, exit code.** A failed cancel exits 1.
    - **Supervisor.** `WORKERS["auto-buyer-cancel"]` and its timeout exist, and the ps1 registers ModelPrediction-AutoBuyerCancel every 2 min from 08:00 for 18 h.
    - **Drawdown, synthetic live ledger.** Record W-L-P, cost, P&L in $ and U, ROI, last-30 ROI, max drawdown and current drawdown in $ and U are all exact. Excluded: void zero-fill rows, manual rows, submit_unknown rows, rows executed before the cohort's approved_at_utc, and rows of other models.
    - **Drawdown, warnings.**
      - Last-30 ROI -12% with 15 settled warns; with 14 settled it does not.
      - A current drawdown of $100 warns; $99.99 does not.
      - With unit $5, 20U warns independently.
      - State keys override the thresholds.
      - The function never raises; on error it returns status error.
    - **Status.** `auto_buyer_status()` returns the state plus `live_cohorts` (labels), `live_trading_active` (enabled is True and live cohorts exist) and `live_drawdown`. run_auto_buyer_cycle's last_run carries `live_drawdown`.
    - **Dashboard toggle (node harness).**
      - A toggle POST answering 401 or `{"status":"refused"}` shows the error, re-fetches /api/auto-buyer/status, and renders ON when the server says enabled is true.
      - `enabled: "true"` (string) renders as not ON.
      - With live cohorts active and enabled, the button and pill say LIVE with live/danger styling.
      - The enable confirm lists the live cohort labels and says REAL MONEY.
      - The drawdown block lists each live cohort, with warnings flagged.
      - The inline JS still parses (existing test).
    - **Script.** `scripts/auto_polymarket_buyer.py --execute` exits nonzero without building a live config when the state has a `cohorts` key or enabled is not True, and refuses when daily.lock is held. The dry run is unchanged.
  </behavior>
  <action>
    **Backstop worker (D-01, D-02)** — src/model_prediction/auto_buyer_cancel_worker.py
    - `main()`:
      - Call `enable_background_priority()` and `RuntimePaths.resolve(require_external_runtime=True)`, failing closed.
      - Take `acquire_lock(paths.lock_root / "auto_buyer_cancel.lock")`, non-blocking, its OWN lock. It must not be daily.lock: daily holds that for 17-49 min, and the buyer waits up to 20 min for it.
      - Read `enabled = load_auto_buyer_state().get("enabled") is True`. An unreadable state yields defaults, which count as disabled, so it fails toward cancelling.
      - Load the memo `auto_buyer_cancel_worker_memo.json` from the runtime root. Only this worker writes it, atomically, under its lock, pruned to 3 days.
      - Call `cancel_resting_live_fallbacks(cancel_all=not enabled, start_lead_seconds=30.0, skip_order_ids=memo, reason="backstop_tipoff" or "backstop_kill_switch")`.
      - Add cancelled and already_terminal ids to the memo, print one JSON line, and exit 1 when `failed` is non-empty, else 0.
    - It never writes the ledger: restatement is left to the next reconcile under daily.lock. It makes no network call when nothing is due.
    - The 30 s lead is the operator's spec. Note in a comment that the GTD expiry at start minus 90 s is the primary mechanism and this worker is the backstop.

    **Scheduling**
    - run_supervisor.py: `WORKERS["auto-buyer-cancel"] = [_VENV_PYTHON, "-m", "model_prediction.auto_buyer_cancel_worker"]` and `DEFAULT_WORKER_TIMEOUTS["auto-buyer-cancel"] = 120.0`, with a comment.
    - register_windows_tasks.ps1: add a `ModelPrediction-AutoBuyerCancel` block that mirrors the auto-buyer trigger pattern: daily at 08:00, repetition 2 min, duration 18 h. Its description says cancel-only, own lock, never places orders.
    - Update any other worker registry found by the grep in read_first.

    **Drawdown (DD, D-06)** — new src/model_prediction/portfolio/auto_buyer_drawdown.py
    - `live_cohort_drawdown_report(state=None, *, data_root=None, now=None) -> dict` is the single entry point for the later Telegram task. Note that in its docstring.
    - It parses the state's cohorts with auto_executor's `_parse_cohorts` (active, mode live) and reads the live ledger with `read_auto_buyer_ledger`.
    - Per cohort, rows match the model_id, the sport (casefolded), and market_type, and must have executed_at_utc on or after approved_at_utc (when parseable). Excluded: result void with 0 shares, `manual` rows, submit_unknown rows.
    - Settled means result in win/loss/push, ordered by settled_at_utc.
    - Output per cohort: wins, losses, pushes, settled, cost_usd, pnl_usd, pnl_units (pnl_usd / the row's unit_value_usd), roi, last30_n, last30_roi, max_drawdown_usd, max_drawdown_units, current_drawdown_usd and current_drawdown_units (peak minus current of cumulative P&L), and warnings.
    - Warning rules exactly as DD: thresholds `drawdown_warning_usd` (default 100.0) and `drawdown_warning_units` (default 20.0), read from the state with code defaults. Do not edit the runtime state. The warning uses CURRENT drawdown so it clears on recovery; max drawdown is reported too. Say so in SUMMARY for the operator.
    - Top-level output: `{status, computed_at_utc, thresholds, cohorts:[...], warnings:[flat strings]}`. It never raises.
    - auto_executor.py:
      - run_auto_buyer_cycle sets `run_summary["live_drawdown"]` (non-preview, governed) and copies the warnings into the live cohort pass summary.
      - Add `auto_buyer_status()`, which returns `{**load_auto_buyer_state(), "live_cohorts": [...], "live_trading_active": bool, "live_drawdown": live_cohort_drawdown_report(state)}`.

    **Routes** — routes.py
    - `/api/auto-buyer/status` returns `auto_buyer_status()`.
    - `/api/auto-buyer/toggle` returns toggle_auto_buyer's dict, including `kill_switch_cancel`, unchanged otherwise.

    **Dashboard (C1, C2)** — dashboard.html
    - updateAutoBuyerUI:
      - `on = data.enabled === true`.
      - The live state comes from `data.live_trading_active === true`, or from the legacy `mode === "live"`.
      - With live cohorts, the button reads "ON (LIVE)", the pill "LIVE: real orders ON" with the live/danger classes, and the title lists `data.live_cohorts`.
      - A disabled buyer with live cohorts configured shows the existing armed state.
    - toggleAutoBuyer:
      - Derive the current state from `_autoBuyerStatusCache.enabled === true`, not from the button text.
      - The enable confirm, when live cohorts exist, lists each live cohort and states REAL MONEY orders ($/unit, game and day caps), with `{danger:true}`.
      - Check `res.ok` and `data.status !== "refused"`. On failure, show the error with customAlert and `await loadAutoBuyerStatus()`. Never render from the failed body.
      - On success, render from a fresh `loadAutoBuyerStatus()`, and show the kill_switch_cancel counts when present.
    - loadAutoBuyerStatus: on a non-ok response, put the pill into an explicit "status unavailable, reload" state instead of silently leaving the last render.
    - Add a compact live-drawdown block to the auto-buyer panel (near :416): per live cohort, record, ROI, P&L in $ and U, and current and max drawdown, with warnings in the danger color. Prices and numbers only; never American odds.
    - Add node-harness tests in tests/test_dashboard_html.py for the toggle and render behaviours above.

    **Script (C3)** — scripts/auto_polymarket_buyer.py
    - `--execute` refuses, with a nonzero exit and a clear message, when `load_auto_buyer_state()` has a `cohorts` key or enabled is not True.
    - When allowed, it holds `acquire_lock(<runtime lock_root>/daily.lock)`, non-blocking, for the run, and refuses if the lock is held.
    - Test the refusals with monkeypatched state and lock, in tests/test_auto_buyer_cancel_worker.py or a small dedicated section of it.

    **Docs**
    - docs/DEBUG.md: a new top entry "2026-11-25 — Live auto-buyer safety (quick-261125-hih)" covering:
      - the defects, each with trace and fix
      - the Idempotency-Key facts
      - the GTD design and how to read back a fallback
      - the no-ledger-lock decision and why
      - the backstop worker
      - the unknown-row operator procedure: check the exchange positions and activities for the slug; if nothing filled, remove the row with `remove_open_rows`; if it filled, record the operator decision. Resolution tooling is an open ROADMAP item.
      - known limits: the total position key, if it was not established
    - docs/ROADMAP.md: open items for unknown-row resolution tooling, Telegram wiring to `live_cohort_drawdown_report`, and the total position key if still open.
    - docs/PROJECT_STATUS.md: one line on the live safety layer.

    **Mutation proofs**
    - Worker skips the no-candidate short-circuit: the no-network test goes red.
    - Worker uses daily.lock: the lock test goes red.
    - Drawdown includes void rows: red.
    - Current drawdown replaced by max: the recovery test goes red.
    - Toggle renders from a failed body: the node test goes red.
    - Script ignores cohorts: red.
  </action>
  <verify>
    <automated>cd $TEMP/261125-hih/wt-t3 && PYTHONPATH=$PWD/src env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY timeout 900 E:/MP/.venv/Scripts/python -m pytest tests/test_auto_buyer_cancel_worker.py tests/test_auto_buyer_drawdown.py tests/test_dashboard_html.py tests/test_dual_auto_buyer_dashboard.py tests/test_auto_buyer_resting_lifecycle.py tests/test_live_order_safety.py tests/test_auto_buyer_cohorts.py -o addopts='' -q</automated>
    Grep gates, comment lines excluded:
    - run_supervisor.py `auto-buyer-cancel` [HEAD=0 EXPECT>=2]
    - register_windows_tasks.ps1 `auto-buyer-cancel` [HEAD=0 EXPECT>=1]
    - auto_buyer_drawdown.py `def live_cohort_drawdown_report` [HEAD=file absent EXPECT==1]
    - dashboard.html `live_trading_active` [HEAD=0 EXPECT>=1]

    ruff, format and mypy must be clean on the changed Python files, and the CRLF byte check must pass on every edited or new file.
  </verify>
  <done>
    - All behaviours pass, with red output first and the 6 mutation proofs recorded.
    - The verify files pass in the worktree and again in E:/MP after landing.
    - The backstop worker exists and is registered in code only. It is not yet scheduled on the host: that is Task 4.
    - The dashboard can no longer show OFF while the server is enabled, and shows LIVE when live cohorts trade.
    - The drawdown report is in last_run, the status API and the panel.
  </done>
</task>

<task type="checkpoint:human-action" gate="blocking-human">
  <name>Task 4 (ORCHESTRATOR): land check, dashboard restart, register only the backstop task, observe the first GTD fallback</name>
  <files>none (host scheduler, dashboard process, read-only checks)</files>
  <precondition>Tasks 1-3 are committed and landed in E:/MP, and the top-level critical-reviewer loop on their diff has no open finding.</precondition>
  <action>Orchestrator-run after review: follow the numbered steps in instructions below (D-01 backstop scheduling, D-07 GTD observation without a test order). The executor agent stops before this task and touches neither the host scheduler nor the dashboard process.</action>
  <instructions>
    EXECUTOR: STOP before this task and return; the orchestrator runs it.

    1. **Land check.**
       - `git log` shows the Task 1-3 commits on refactor/cleanup-2026-11-11.
       - `git diff` shows exactly the foreign hunks (compare with the snapshots).
       - The three tasks' verify files pass in E:/MP.
    2. **Restart ModelPredictionDashboard.** It is long-running, and the status, toggle and kill-switch changes reach it only after a restart. Its task sets MODEL_PREDICTION_RUNTIME_ROOT=E:/model-prediction-runtime.
    3. **Register ONLY the new task.** Do NOT run the whole scripts/register_windows_tasks.ps1: it re-registers every task with the default `-LogonType Interactive`, while the live worker tasks run S4U. Instead:
       - Read the existing task's principal (`(Get-ScheduledTask ModelPrediction-AutoBuyer).Principal`) and action.
       - Register `ModelPrediction-AutoBuyerCancel` alone, with the same action pattern, worker `auto-buyer-cancel`, the same principal and logon type, and the 2-min/18-h trigger (elevation may be needed; ask the operator if so).
       - Run `Start-ScheduledTask` once.
       - Confirm runs.db records a completed `auto-buyer-cancel` run with exit 0 and output `candidates` 0 (or a cancel of a genuinely due fallback).
    4. **Observe the first live GTD fallback (D-07; no test order).** After the first live buy with a partial IOC fill, read the ledger row: fallback_status `resting`, `good_till_time` equal to start minus 90 s, idempotency keys present. Then call `order_snapshots([fallback_id])`: tif GOOD_TILL_DATE and good_till_time match. After the expiry, the state is ORDER_STATE_EXPIRED (or filled or cancelled).
       - If fallback_status is `rejected`, the GTD payload format is wrong. Report the exchange error text. Live buying continues safely IOC-only, with no un-expiring rest, until a follow-up fixes the format.
       - `cancelled_unverified_gtd` means the exchange accepted but did not honour GTD. Report it.
    5. **Optional, ONLY with a separate explicit operator yes.** Call `PolymarketExecutor.preview_order` once with the exact GTD fallback payload for a future market at 1 cent, post-only, minimum quantity, expiring now + 10 min. Then call `open_orders([slug])` and confirm nothing was placed. If an order appears, cancel it by id immediately and report it. The docs describe preview as "before submission" but do not state it places nothing.
    6. **Optional, ONLY with an operator yes.** Run a kill-switch drill: toggle the buyer off and on from the dashboard while a live fallback rests, and confirm `kill_switch_cancel` lists it.
    7. Report to the operator: the commits, the task registration, the first-fallback observation, any alarms in last_run, and the drawdown block.
  </instructions>
  <verification>
    - runs.db shows ModelPrediction-AutoBuyerCancel runs completing every ~2 min with exit 0.
    - The dashboard shows LIVE and the drawdown block.
    - The first live fallback reads back GTD at start minus 90 s, or a surfaced `rejected` alarm with IOC-only behaviour.
  </verification>
  <verify>
    <human-check>Orchestrator confirms the three verification bullets above and reports them to the operator.</human-check>
  </verify>
  <resume-signal>Operator says "yes" to steps 5 or 6 (optional probes), or "done" after reading the report; no answer means steps 5 and 6 are skipped.</resume-signal>
  <done>
    - The three commits have landed with the foreign hunks intact.
    - The dashboard has restarted.
    - Only the new backstop task is registered (S4U or matching principal) and running.
    - The GTD behaviour has been observed on a real fallback, or its rejection reported, with no test order placed unless the operator separately approved the preview probe.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| buyer -> Polymarket US REST | Real-money order placement over an unreliable network; responses can be lost after the exchange acts |
| dashboard (browser) -> /api/auto-buyer/* | Kill switch and status; a stale token or refusal must never render a false OFF |
| scheduler workers -> live ledger / audit | Concurrent readers and writers with no ledger lock; daily.lock guards rewrites |
| calibrate job -> data/calibration/models | Artifacts written 4x/day can change served probabilities |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-HIH-01 | Tampering (duplicate action) | polymarket_execute._request POST /v1/orders | critical | mitigate | Idempotency-Key per leg; no retry of ambiguous outcomes (same-key retry only behind a default-off switch); OrderSubmitUnknownError, then an unknown row that blocks dedup and counts toward the caps, plus an alarm and a halt (Task 1) |
| T-HIH-02 | Repudiation | execute audit after submit | high | mitigate | order_intent before the POST; an audit failure after submit returns audit_error, and the ledger still records (Task 1) |
| T-HIH-03 | Repudiation | record_auto_buy_execution | high | mitigate | AutoBuyerLedgerWriteError on the live ledger, then ERROR log, alarm, halt, and LiveAutoBuyerAlarm marks the run failed (Task 1) |
| T-HIH-04 | Elevation (unintended exposure) | resting fallback | critical | mitigate | GTD at start minus 90 s, verified by read-back and cancelled on mismatch; never GTC; backstop worker cancels at start minus 30 s (Tasks 1, 3) |
| T-HIH-05 | Denial of control | kill switch | critical | mitigate | Toggle cancels by id; disabled cycles and daily reconcile cancel all; enabled re-checked before each submit; backstop cancels all while disabled (Tasks 1-3) |
| T-HIH-06 | Spoofing (false UI state) | dashboard toggle | high | mitigate | Checks res.ok, re-fetches from the server, ON only when `enabled === true`, LIVE label for live cohorts (Task 3) |
| T-HIH-07 | Tampering (price drift) | executor live re-check | high | mitigate | Refuse when the live ask differs from ticket.price by more than 2 cents (Task 1) |
| T-HIH-08 | Tampering (silent model change) | calibrate_for_serving for live cohorts | high | mitigate | Live rows refuse non-identity calibration with an alarm (Task 2) |
| T-HIH-09 | Tampering (double exposure) | dedup keys / top-up | high | mitigate | tsc- total key, top-up only on the same slug, pending exposure never dropped (Task 2) |
| T-HIH-10 | Elevation (ungoverned path) | scripts/auto_polymarket_buyer.py --execute | high | mitigate | Refuse under cohorts or when not enabled; take daily.lock (Task 3) |
| T-HIH-11 | Denial of service | backstop worker vs gateway rate limit | low | mitigate | No network when nothing is due; per-id GETs and cancels only; memo prevents re-sends |
| T-HIH-12 | Tampering (concurrent ledger write) | cancel-only paths | medium | mitigate | Toggle and backstop never write the ledger; restatement stays under daily.lock (no new lock needed) |
| T-HIH-13 | Information disclosure | idempotency keys and audit payloads | low | accept | Keys are random uuids with no secret; credentials never logged (unchanged signing code) |
| T-HIH-SC | Tampering | npm/pip/cargo installs | high | mitigate | No package installs in this plan; no legitimacy gate needed. Any install an executor believes necessary stops for a blocking human checkpoint |
</threat_model>

<verification>
- Each code task's automated verify passes in its worktree and again in E:/MP after landing; each has red-first output and its mutation proofs recorded in SUMMARY.
- `git diff` in E:/MP after each landing equals the foreign-hunk snapshots (index and @@ lines aside); no foreign hunk is ever staged.
- No test or step places a real order; no runtime state or ledger file is edited by Tasks 1-3.
- ruff check, ruff format --check and mypy are clean on every changed Python file; every edited file stays CRLF.
- Task 4 confirms the backstop task runs and the first GTD fallback is observed or its rejection is surfaced.
</verification>

<success_criteria>
- Duplicate orders from retries are impossible by construction: one Idempotency-Key per leg, no retry after an ambiguous outcome, and an unknown outcome blocks re-buy and alarms.
- Every placed live order is in the live ledger, or the cycle is marked failed with a named alarm.
- No live fallback can rest past tip-off. GTD expiry is verified per order, and the backstop cancels at start minus 30 s and whenever the buyer is disabled.
- The kill switch stops new orders within the running cycle and cancels resting live orders from the toggle, the cycle, the daily job and the backstop.
- Live cohort orders are refused when the price drifts more than 2 cents, the pick is older than 3 h, or calibration is non-identity.
- The operator sees LIVE, the per-cohort record, ROI and drawdown with warnings on the dashboard and status API. Telegram has one function to call.
</success_criteria>

<output>
Create `.planning/quick/261125-hih-live-auto-buyer-safety-no-duplicate-orde/261125-hih-SUMMARY.md` when done. Include per task:
- commits
- red outputs and mutation proofs
- test counts
- the total-position-key finding
- interim states while landing
- any test updated to new required behaviour (with reason)
- open items
</output>
