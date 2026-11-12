---
phase: quick-261111-wv9
plan: 01
subsystem: portfolio
tags: [auto-buyer, polymarket, ledger, dashboard, quarantine, model-attribution]

requires: []
provides:
  - "is_manual_auto_buyer_record: shared any-of predicate (Python) identifying hand-placed MANUAL rows"
  - "isManualAutoBuyerRow: JS mirror of the same predicate, wired into dashboard Auto-Buyer rendering"
  - "summarize_auto_buyer_performance excludes MANUAL rows from every metric, reports them separately"
  - "_get_quarantined_underperforming_models skips MANUAL rows before grouping by model_id"
  - "Dashboard Auto-Buyer KPI strip and performance panels (cohorts, sport P&L, cumulative curve, daily table) exclude MANUAL rows"
affects: [portfolio-circuit-breaker, auto-buyer-ledger-improvements]

actuals:
  tokens: 5233
  tasks: 3
  commits: 4
  plan_head_before: a2d9a43c3496a11d80e8a3bc69944a50df793011
  plan_head_after: b2ad1781cd0f9d4767a94edb3750314c80d4a11d

tech-stack:
  added: []
  patterns:
    - "Shared any-of predicate duplicated in Python and JS with an explicit why-comment pinning them to stay in sync, rather than a single source of truth across languages"
    - "Attribution metrics split a ledger into excluded/included subsets and report both, instead of silently dropping the excluded rows from the total"

key-files:
  created: []
  modified:
    - src/model_prediction/portfolio/auto_buyer_ledger.py
    - src/model_prediction/portfolio/auto_executor.py
    - tests/test_auto_polymarket_buyer.py
    - dashboard.html
    - tests/test_dashboard_html.py

key-decisions:
  - "_today_auto_buyer_totals (drawdown circuit breaker) was left byte-identical on purpose -- it is a bankroll risk control, not model attribution, and changing it changes when the live buyer halts. Flagged below for Vincent's decision."
  - "Operator-exited model rows (settlement_source manual_sell / manual_exit_shares) are explicitly NOT matched by the MANUAL predicate in either language -- they carry real model edge and are a separate class. Flagged below as a possible follow-up."

requirements-completed: [QUICK-261111-wv9]

coverage:
  - id: D1
    description: "summarize_auto_buyer_performance excludes MANUAL rows from every metric and reports manual_orders/manual_settled_orders/manual_realized_pnl_usd separately"
    requirement: "QUICK-261111-wv9"
    verification:
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_manual_position_row_is_classified_manual"
        status: pass
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_summarize_auto_buyer_performance_excludes_manual_rows"
        status: pass
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_summarize_auto_buyer_performance"
        status: pass
    human_judgment: false
  - id: D2
    description: "_get_quarantined_underperforming_models never groups MANUAL rows into a fake 'manual' model"
    requirement: "QUICK-261111-wv9"
    verification:
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_underperforming_quarantine_ignores_manual_rows"
        status: pass
      - kind: unit
        ref: "tests/test_auto_polymarket_buyer.py#test_underperforming_quarantine_is_scoped_to_ledger_and_latest_settlements"
        status: pass
    human_judgment: false
  - id: D3
    description: "Dashboard Auto-Buyer KPI strip and performance panels (cohorts, sport P&L, cumulative curve, daily table) exclude MANUAL rows while the ledger table keeps listing them"
    requirement: "QUICK-261111-wv9"
    verification:
      - kind: unit
        ref: "tests/test_dashboard_html.py#test_auto_buyer_metrics_exclude_manual_rows"
        status: pass
      - kind: unit
        ref: "tests/test_dashboard_html.py#test_auto_buyer_render_functions_filter_manual_rows"
        status: pass
      - kind: unit
        ref: "tests/test_dashboard_html.py#test_auto_buyer_performance_cohorts_and_et_day_grouping"
        status: pass
  - id: D4
    description: "Drawdown circuit breaker's realized P&L still includes MANUAL rows -- whether to exclude it like today's spend sum already does is a bankroll-risk decision for Vincent, not an automatic fix"
    human_judgment: true
    rationale: "Changing _today_auto_buyer_totals changes when the live buyer halts trading. That is a real-money-adjacent operational decision, not a correctness bug this plan's scope covers."
  - id: D5
    description: "Operator exits of model rows (settlement_source manual_sell) are currently counted as ordinary settled model results, not separated from resolved games in buyer win/loss -- whether to split them is a follow-up decision for Vincent"
    human_judgment: true
    rationale: "This is a modeling/attribution design choice (per memory buyer-ledger-manual-sell-2026-11-11) with no single objectively-correct answer; it needs Vincent's judgment, not an automated fix."

duration: ~20min
completed: 2026-11-12
status: complete
---

# Quick Task 261111-wv9: Exclude MANUAL rows from auto-buyer model-edge attribution

**One shared `is_manual_auto_buyer_record`/`isManualAutoBuyerRow` predicate now keeps hand-placed exchange positions out of the buyer performance summary, the dynamic model quarantine, and every dashboard Auto-Buyer KPI/performance panel -- while still showing them in the raw ledger table and the manual_* summary keys.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3 (all completed)
- **Files modified:** 5 (`src/model_prediction/portfolio/auto_buyer_ledger.py`, `src/model_prediction/portfolio/auto_executor.py`, `tests/test_auto_polymarket_buyer.py`, `dashboard.html`, `tests/test_dashboard_html.py`)

## Accomplishments

- Added a public `is_manual_auto_buyer_record` predicate (Python) and its JS mirror `isManualAutoBuyerRow`, both implementing the same any-of rule: `manual` flag true, `pick_id`/`order_id` starting with `MANUAL-`, or `model_id == "manual"`. Neither inspects `settlement_source`/`manual_exit_shares`, so operator exits of real model positions are never mistaken for MANUAL rows.
- `summarize_auto_buyer_performance` now computes every existing metric (record, win rate, ROI, P&L, avg edge) from model rows only, and reports the excluded rows separately via new `manual_orders`, `manual_settled_orders`, `manual_realized_pnl_usd` keys so nothing silently disappears from the ledger total.
- `_get_quarantined_underperforming_models` skips MANUAL rows before grouping by `model_id`, so a hand-placed position can no longer form a fake `"manual"` model that shows up in `quarantined_models`.
- The dashboard's `renderAutoBuyerKpisFor` keeps the ledger table's All/Pending/Settled counts on the full row set (MANUAL positions stay visible there and in `renderAutoBuyerTablesFor`/`renderAbsPendingTableFor`, untouched), but every performance metric (record, ROI, P&L, avg edge, streak, best/worst day, profit factor) now reads a MANUAL-filtered `modelRows`/`modelSettled`. `renderAutoBuyerPerformanceFor`'s cohorts, sport bars, cumulative curve and daily table are filtered the same way.
- `dashboard.html`'s three edits were staged via a HEAD-based git blob (index-only), so another session's unrelated uncommitted edits (27 lines added / 60 removed, confirmed via `git diff --numstat` before, during, and after this plan) were never touched or committed.

## Audit Inventory (final verdicts)

Every reader of `auto_buyer_ledger.jsonl` found via `grep -rlE "auto_buyer_ledger\.jsonl|read_auto_buyer_ledger" src scripts --include=*.py`, re-run after this plan and confirmed unchanged (same 7 files):

| Reader | What it computes | Verdict |
|---|---|---|
| `auto_buyer_ledger.py::summarize_auto_buyer_performance` | record, win rate, ROI, P&L, avg edge for `/api/auto-buyer/summary` + `/paper-summary` | **FIXED** (Task 1) |
| `auto_executor.py::_get_quarantined_underperforming_models` | rolling ROI per model, drives `quarantined_models` | **FIXED** (Task 2) |
| `dashboard.html::renderAutoBuyerKpisFor` / `renderAutoBuyerPerformanceFor` | KPI strip, cohorts, sport P&L, cumulative curve, daily table | **FIXED** (Task 3) |
| `auto_executor.py::_today_auto_buyer_totals` | today's spend (already excludes MANUAL) + today's realized P&L for the drawdown breaker | **OUT OF SCOPE, left byte-identical** -- see Decisions below |
| `auto_executor.py::_build_bought_index` | dedup / lockout | correct by design, no change -- MANUAL rows must keep blocking re-entry |
| `ncaab_paper_trial.py::_recorded_paper_dedup_keys` | dedup keys only | no attribution, no change |
| `manual_bet_ledger.py` | excludes buyer slugs from manual-bet sync | n/a, no change |
| `dashboard/routes.py` | pass-through endpoints | covered by Task 1's fix at the source; unchanged itself |
| `scripts/sync_auto_buyer_xlsx.py`, `scripts/reconcile_auto_buyer_ledger_live.py` | xlsx export, one-off repair | no win/loss attribution, no change |
| `scripts/audit_ledger_pnl.py`, `scripts/audit_best_picks.py` | read `ledgers.db` and tier xlsx | **confirmed not exposed** -- re-verified read-only against the live runtime `ledgers/ledgers.db`: 46,254 `ledger_records`, 0 with `pick_id LIKE 'MANUAL-%'`, 0 with `model_id = 'manual'`, 0 with `selection = 'manual'`. Identical to the planning-time baseline. No change. |

Live read-only sanity check against the runtime Auto-Buyer ledger (`$MODEL_PREDICTION_RUNTIME_ROOT/auto_buyer_ledger.jsonl`, path passed explicitly): `manual_orders` = 1, `manual_settled_orders` = 0, `manual_realized_pnl_usd` = 0, `total_orders` = 22. The one MANUAL row is still open (unsettled) at the time of this check -- consistent with the 1-open-of-23 baseline recorded during planning (22 model + 1 manual = 23 total).

## RED/GREEN Evidence

**Task 1 (`is_manual_auto_buyer_record` + summary fix):**
- RED: `ImportError: cannot import name 'is_manual_auto_buyer_record'` on Test A; Test B failed with `assert 3 == 2` (manual row still counted in `total_orders`).
- GREEN: `3 passed, 60 deselected` on `-k "summarize or manual"` after adding the predicate and splitting `summarize_auto_buyer_performance` into model/manual subsets.

**Task 2 (quarantine fix):**
- RED: Test C failed with `AssertionError: assert 'manual' not in {'manual': {...}, 'test-model-v1': {...}}` -- the fake `"manual"` model was quarantined alongside the real underperforming model.
- GREEN: `6 passed, 58 deselected` on `-k "summarize or quarantine or manual"` after skipping MANUAL rows before grouping. `_today_auto_buyer_totals` verified byte-identical to HEAD via the plan's `diff --strip-trailing-cr` gate.

**Task 3 (dashboard fix):**
- RED (confirmed by temporarily reverting the three JS edits and re-running): Test D failed with `subprocess.CalledProcessError` (`isManualAutoBuyerRow is not defined` inside the Node script). Test E failed with `AssertionError: assert 'isManualAutoBuyerRow' in '...'` against both `renderAutoBuyerKpisFor` and `renderAutoBuyerPerformanceFor` bodies.
- GREEN: `3 passed, 18 deselected` on `-k "auto_buyer_performance or manual_rows"` after re-applying the predicate, the KPI function's `modelRows`/`modelSettled` split, and the performance function's `.filter(row => !isManualAutoBuyerRow(row))`.
- Staging safety: `git diff --cached --numstat -- dashboard.html` showed `25  13` (within the plan's 40-added/15-deleted gate); `git diff --numstat -- dashboard.html` (index-to-worktree) showed the unrelated edits unchanged at `27  60` both before staging and after the commit.

## Task Commits

Each task was committed atomically with explicit pathspecs (never `git add -A`):

1. **Task 1: shared MANUAL predicate + buyer summary fix** - `53be95d1` (fix) -- `src/model_prediction/portfolio/auto_buyer_ledger.py`, `tests/test_auto_polymarket_buyer.py`
2. **Task 2: quarantine never scores MANUAL rows as a model** - `ef0545b2` (fix) -- `src/model_prediction/portfolio/auto_executor.py`, `tests/test_auto_polymarket_buyer.py`
3. **Task 3: dashboard KPIs/performance exclude MANUAL rows** - `b2ad1781` (fix) -- `dashboard.html` (index-only, HEAD-based blob), `tests/test_dashboard_html.py`

**Note on commit 4 in range:** `git rev-list --count a2d9a43c..HEAD` reports 4 commits, not 3. The fourth, `3fbbf16a "Replace ModelState enum with a model_enabled flag"`, was made by a **separate, concurrent session** on this shared working tree during this plan's execution (confirmed via `git show --stat` -- it touches `cli/forecast.py`, `config.py`, `domain.py`, etc., none of which this plan's tasks touch). This is the exact shared-dirty-tree risk this sequential-execution run was warned about; it is not part of this plan's work and is called out here for traceability rather than silently absorbed into the commit count.

**Plan metadata commit:** pending (orchestrator handles the docs commit for SUMMARY.md/STATE.md/ROADMAP.md per this run's constraints).

## Files Created/Modified

- `src/model_prediction/portfolio/auto_buyer_ledger.py` - added `is_manual_auto_buyer_record`; `summarize_auto_buyer_performance` now excludes MANUAL rows from every metric and reports them via `manual_orders`/`manual_settled_orders`/`manual_realized_pnl_usd`
- `src/model_prediction/portfolio/auto_executor.py` - `_get_quarantined_underperforming_models` skips MANUAL rows before grouping by model; `_today_auto_buyer_totals` verified byte-identical to HEAD
- `tests/test_auto_polymarket_buyer.py` - Tests A/B/C: producer-shaped MANUAL-row regression tests for the predicate, the summary split, and the quarantine fix
- `dashboard.html` - added `isManualAutoBuyerRow`; `renderAutoBuyerKpisFor` and `renderAutoBuyerPerformanceFor` filter MANUAL rows out of every performance metric while table counts stay on the full row set
- `tests/test_dashboard_html.py` - Tests D/E: Node-executed predicate/metrics test and static wiring assertions on both render functions

## Decisions Made

- **Left `_today_auto_buyer_totals` unchanged (flagged for Vincent).** Its realized-P&L sum feeding the daily drawdown circuit breaker still includes MANUAL rows, even though the function's spend sum already excludes them (pre-existing, from an earlier fix). This is a bankroll risk control, not model attribution -- changing it changes when the live buyer halts trading for the day. **Decision needed:** should the drawdown breaker's realized P&L exclude MANUAL rows the same way its spend sum already does?
- **Operator-exited model rows stay in scope for buyer win/loss (flagged for Vincent).** A model row the operator sold out of early (`settlement_source: "manual_sell"` or `manual_exit_shares > 0`) is currently scored as an ordinary settled model result everywhere, including the new model-only metrics in this plan. It is NOT matched by the MANUAL predicate (tested explicitly in Tests A and D) because it carries real model edge. **Decision needed:** should these operator exits be separated from resolved-game outcomes in the buyer's win/loss record, per memory `buyer-ledger-manual-sell-2026-11-11`?

## Deviations from Plan

None - plan executed exactly as written. All three tasks matched their specified behavior, action, and verification steps; no Rule 1-4 auto-fixes were needed.

## Issues Encountered

- A full (non-`-k`-scoped) run of `tests/test_auto_polymarket_buyer.py` surfaced one pre-existing, unrelated failure: `test_buyer_fails_closed_when_stale_quote_refresh_has_network_error` (expects `rejected_stale_quote == 1`, actually gets `rejected_future_slate == 1`). Isolated and confirmed unrelated to this plan's three changes (none of which touch `evaluate_and_execute`, future-slate filtering, or quote-staleness handling). Logged to `deferred-items.md` in this phase directory per the Scope Boundary rule; not fixed.
- `tests/test_dashboard_html.py::test_auto_buyer_consolidated_tab_markup_and_routing` fails on the current tree for reasons unrelated to this plan (per the plan's own baseline note) and was excluded from every targeted run, as instructed.

## Known Stubs

None.

## Threat Flags

None - all three STRIDE mitigations in the plan's threat model (T-261111-wv9-01 through -04) were implemented and tested as specified; T-261111-wv9-05 was accepted (not mitigated) by design, per the Decisions section above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Every reader of the Auto-Buyer ledger that computes model-edge attribution now excludes MANUAL rows through one tested, bilingual rule. No new readers were discovered; the inventory is unchanged at 7 files.
- Two follow-up decisions are open for Vincent (see Decisions Made above) and are not blockers to closing this quick task.
- `.planning/quick/261111-wv9-audit-every-script-that-computes-model-e/deferred-items.md` records one unrelated pre-existing test failure for separate investigation.

---
*Phase: quick-261111-wv9*
*Completed: 2026-11-12*

## Self-Check: PASSED

All claimed files confirmed present on disk; all three task commits (`53be95d1`, `ef0545b2`, `b2ad1781`) confirmed as ancestors of HEAD.
