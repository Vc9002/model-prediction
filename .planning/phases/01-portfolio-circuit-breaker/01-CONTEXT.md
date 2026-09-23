# Phase 1: Portfolio Circuit Breaker - Context

**Gathered:** 2026-09-23
**Status:** Ready for planning

<domain>
## Phase Boundary

The system automatically halts new position entry before losses cascade past an acceptable threshold, and only a human can resume trading afterward. Requirements: SAFETY-01, SAFETY-02.

</domain>

<decisions>
## Implementation Decisions

### Gate scope
- D-01: The new circuit breaker gates only the automated path — `AutoPolymarketBuyer.evaluate_and_execute()` in `src/model_prediction/portfolio/auto_executor.py`. Manual CLI `buy`/`sell` commands (`cli/commands.py`) are explicitly NOT gated — those are already human-confirmed, so a human trading manually while the breaker is tripped is a deliberate act, not a gap to close.

### Relationship to existing daily-loss breaker
- D-02: The existing `max_daily_loss_usd` calendar-day breaker in `auto_executor.py` stays as-is (it already works, auto-resets daily by design, and is out of scope for this phase). The new portfolio-level circuit breaker is a SEPARATE, additional gate — persisted, human-reset-only, checked alongside (not replacing) the existing daily breaker.

### Threshold
- D-03: Ship with a conservative placeholder `max_drawdown` threshold as a config value (do not hand-pick the "real" number now — that's a business decision for the operator to tune after seeing it live). The config key must be clearly documented as needing operator review before being treated as final. Research flagged a unit mismatch to resolve during planning: `economic_gate.py::max_drawdown()` and `pnl_units` operate in "units"; the existing informal `max_daily_loss_usd` breaker is USD-denominated. Pick one consistent unit for the new breaker's threshold and document the conversion if mixing is unavoidable.

### Persistence and reset mechanism
- D-04: Persisted trip state should mirror the existing `auto_buyer_state.json` pattern exactly — atomic temp-file + `Path.replace()` JSON write, a dedicated new state file (e.g. `circuit_breaker_state.json`) under `RuntimePaths`, and a reset function shaped like `toggle_auto_buyer()`/`set_auto_buyer_mode()`, audit-logged via the existing `AuditLog` class.

### Reset action shape
- D-05: Human reset follows `model_promotion.py`'s CLI pattern: hand-rolled `_arg()` flag parser, `--approved-by` required (never optional), JSON-printed success, `"... ERROR: {exc}"` on stderr, fail-LOUD.

### Visibility (SAFETY-02)
- D-06: No new frontend UI. Extend the existing `dashboard/status.py::status()` `alerts` list, following the precedent already set by `_auto_buyer_daily_loss_alert()` (prints "breaker tripped, no new buys" at `level: "error"`). A new dashboard POST route for the reset action follows the existing `X-Dashboard-Token` + `confirm: true` POST guard pattern (`routes.py` lines ~636-652).

### Choke point
- D-07: The check hangs on the buy branch of `dashboard/orders.py::submit_order`'s buy/sell split (lines ~646-675), so closing/selling existing positions is never blocked by a tripped breaker — only new entries.

### Claude's Discretion
- Exact config key name and file location for the threshold value (follow existing config conventions)
- Exact wording of dashboard alert / CLI error messages (follow existing tone in the codebase)
- Whether the state file lives alongside `auto_buyer_state.json` or in its own location, as long as it follows the same `RuntimePaths` + atomic-write idiom

</decisions>

<specifics>
## Specific Ideas

No additional specifics beyond the decisions above — scope, unit handling, and mechanism are all locked. This is a well-understood extension of existing, already-observed patterns, not a novel design.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase-specific research and patterns
- `.planning/phases/01-portfolio-circuit-breaker/01-RESEARCH.md` — choke point identification, existing partial breaker analysis, unit mismatch pitfall, persistence/reset mechanism recommendation
- `.planning/phases/01-portfolio-circuit-breaker/01-PATTERNS.md` — exact file/line analog patterns for gate structure, persisted state, CLI admin actions, dashboard routes

### Project-level context
- `.planning/PROJECT.md` — Core Value, Constraints (esp. "safety architecture" and "point-in-time correctness" constraints)
- `.planning/research/SUMMARY.md` — confirms no `circuit_breaker`/`kill_switch`/`halt_trading` exists anywhere in `src/` today; this phase is the first

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `economic_gate.py::max_drawdown()` — pure, unit-tested drawdown function; not yet wired into any live decision (its own docstring says so) — this phase is plausibly its first real caller
- `dashboard/status.py::_drawdown_summary()` — already computes portfolio-wide peak-to-trough drawdown from the Main-ledger `pnl_units`, but is currently display-only

### Established Patterns
- `auto_executor.py::load_auto_buyer_state`/`save_auto_buyer_state` (lines ~361-455) — the exact persisted-state pattern to copy
- `cli_production.py::_read_state`/`_write_state` (lines ~108-125) — alternate reference for the same atomic-write idiom
- `model_promotion.py` — CLI admin-action shape to copy for the reset command

### Integration Points
- `auto_executor.py`'s existing informal `max_daily_loss_usd` breaker (lines ~1050-1059) — the new breaker is additive alongside this, not a replacement
- `dashboard/orders.py::submit_order` — the buy/sell split where the new gate check is inserted
- `dashboard/status.py::status()`'s `alerts` list — where the new tripped-state alert is added

</code_context>

<deferred>
## Deferred Ideas

- Gating manual CLI buy/sell trades — explicitly out of scope per D-01; revisit only if manual trading during a tripped breaker becomes an observed problem
- Replacing/consolidating with the existing daily-loss breaker — explicitly deferred per D-02
- Picking the "real" production threshold value — explicitly deferred to operator review per D-03

</deferred>

---

*Phase: 01-portfolio-circuit-breaker*
*Context gathered: 2026-09-23*
