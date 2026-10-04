# Phase 1: Portfolio Circuit Breaker - Pattern Map

**Mapped:** 2026-09-23
**Files analyzed:** 5 (estimated new/modified — exact plan split is the planner's job)
**Analogs found:** 5 / 5

## Context: no dedicated circuit-breaker module exists yet

There is no `portfolio_circuit_breaker.py`-shaped file today. There IS a
narrower, same-shape predecessor already live and tested:
`portfolio/auto_executor.py`'s **daily loss breaker** (`max_daily_loss_usd`,
checked in `AutoPolymarketBuyer.evaluate_and_execute`). It halts new buys
once today's realized P&L crosses a threshold — same mechanism SAFETY-01
wants, but scoped to one calendar day and one strategy (the auto-buyer),
and with **no persisted tripped flag**: it recomputes fresh from the ledger
every cycle via `_today_auto_buyer_totals()`, so it silently un-trips the
moment a later win pushes today's P&L back above threshold. That is exactly
the "silent auto-resume" SAFETY-02 forbids — do not copy that part.

What Phase 1 must add on top of this shape:
1. **Portfolio-level, not per-day-per-strategy** — scope is the whole
   portfolio (`_drawdown_summary()` in `dashboard/status.py` already computes
   an all-picks, non-resetting cumulative drawdown at `/api/drawdown`; see
   below). `economic_gate.max_drawdown()` is the general-purpose pure
   function REQUIREMENTS.md references ("already computed via
   economic_gate.py") — it is not yet wired to a live gate anywhere; Phase 1
   is plausibly its first live caller.
2. **Persisted trip state** — a JSON state file (pattern: `cli_production.py`'s
   `_read_state`/`_write_state`, or `auto_executor.py`'s
   `load_auto_buyer_state`/`save_auto_buyer_state`), with an explicit
   `tripped: bool` + `tripped_at_utc` + `tripped_reason`, not a value
   recomputed fresh every cycle.
3. **Human-only reset** — an explicit CLI/dashboard action (pattern:
   `model_promotion.py`'s `rollback` command / `cli_production.py`'s
   lifecycle commands), never a code path that flips `tripped` back to
   `False` as a side effect of a cycle running.

## File Classification

| New/Modified File (likely) | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `src/model_prediction/portfolio_circuit_breaker.py` (new) | service / gate | request-response (evaluate) + CRUD (persisted trip state) | `src/model_prediction/economic_gate.py` (gate logic) + `src/model_prediction/cli_production.py` (state persistence) | role-match (composite) |
| `src/model_prediction/portfolio/auto_executor.py` (modified: replace/extend `max_daily_loss_usd` check) | service | request-response | same file, `evaluate_and_execute` lines 1050-1059 | exact (it's the existing narrower version of this exact gate) |
| `src/model_prediction/dashboard/orders.py` (modified: `submit_order`, manual buy path) | controller-ish (dashboard action handler) | request-response | same file, `submit_order` lines 634-710 (fail-closed `{"status": "refused", "error": ...}` pattern) | exact |
| `src/model_prediction/dashboard/routes.py` (modified: expose trip state + reset route) | route | request-response | `toggle_auto_buyer` wiring, lines 719-721 (GET `/api/auto-buyer/status`, POST `/api/auto-buyer/toggle`) | exact |
| `src/model_prediction/dashboard/status.py` (modified or reused: portfolio drawdown feed) | service | transform/batch | `_drawdown_summary()`, lines 432-478 | exact (already computes the exact metric) |
| CLI entry point for human reset (new subcommand, e.g. on `model_promotion.py`-style module or a new `circuit_breaker.py` CLI) | route/CLI | request-response | `src/model_prediction/model_promotion.py` `rollback`/`main()`, lines 338-397, 440-515 | exact |
| `tests/test_portfolio_circuit_breaker.py` (new) | test | — | `tests/test_auto_polymarket_buyer.py::test_daily_loss_circuit_breaker_blocks_new_buys` / `_disabled_by_default`, lines 835-893; `tests/test_economic_gate.py`, lines 47-93 | exact |

## Pattern Assignments

### `portfolio_circuit_breaker.py` (new service/gate module)

**Primary analog:** `src/model_prediction/economic_gate.py` (pure evaluation logic)
**Secondary analog:** `src/model_prediction/cli_production.py` (persisted JSON state, atomic write)

**Imports pattern** (from `economic_gate.py` lines 19-23):
```python
from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
```

**Gate-result dataclass pattern** (`economic_gate.py` lines 102-107):
```python
@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)
```
Reuse this shape (or literally import `GateResult`) so the breaker's
evaluate function returns `passed=False, reasons=[...]` the same way every
other gate in this codebase does — `verification_checklist.py` already
imports `GateResult` from `economic_gate.py` for exactly this reason
(shared result contract across gates).

**Drawdown computation to call, not reimplement** (`economic_gate.py`
lines 26-65):
```python
@dataclass(frozen=True)
class DrawdownResult:
    max_drawdown_units: float
    peak_units: float
    trough_units: float
    peak_index: int
    trough_index: int


def max_drawdown(pnl_sequence: Sequence[float]) -> DrawdownResult:
    """Largest peak-to-trough decline in cumulative P&L.

    ``pnl_sequence`` must already be in chronological (settlement/event date)
    order -- drawdown computed on a shuffled sequence is meaningless.
    """
```
Call this against the portfolio's chronological settled-P&L sequence
(same source `dashboard/status.py::_drawdown_summary` already reads via
`read_picks()`/`read_flat_picks()`, lines 434-443) rather than
reimplementing peak/trough tracking a third time (`market_eval.py` already
has its own third copy, `_max_drawdown`, line 172 — do not add a fourth).

**Persisted trip-state pattern to copy** (`cli_production.py` lines 92-125,
adapted — this is the load-bearing part missing from the existing daily
breaker):
```python
def _paths() -> RuntimePaths:
    """One resolution for all canary mutable state.

    Fail closed: the canary is operational, so an env-less invocation
    must not fall back to a repo-local second runtime (split-brain).
    """
    return RuntimePaths.resolve(repo_root=PROJECT_ROOT, require_external_runtime=True)


def _state_path() -> Path:
    paths = _paths()
    paths.production_state_file.parent.mkdir(parents=True, exist_ok=True)
    return paths.production_state_file


def _read_state() -> dict[str, Any]:
    sp = _state_path()
    if not sp.is_file():
        return {}
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
        return dict(data) if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict[str, Any]) -> None:
    """Write the production state file atomically."""
    sp = _state_path()
    tmp = sp.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(sp)
```
Add a new `RuntimePaths` property (mirror `production_state_file`, line
~180 of `runtime_paths.py`) for the breaker's own state file — e.g.
`circuit_breaker_state.json` under `runtime_root` — rather than overloading
`production_state.json` (that file's schema is owned by the canary; adding
unrelated keys to it is exactly the kind of cross-concern coupling
`CLAUDE.md`'s "one resolver, not two conventions" note warns against).
State shape should be minimal and explicit:
```python
{
    "tripped": bool,
    "tripped_at_utc": str | None,
    "tripped_reason": str | None,
    "max_drawdown_units_at_trip": float | None,
    "reset_at_utc": str | None,
    "reset_by": str | None,   # operator identity, mirrors approved_by in model_promotion.py
}
```

**Toggle/reset function pattern** (`portfolio/auto_executor.py`
`toggle_auto_buyer`, lines 458-467, adapted for a one-way human reset
instead of a two-way toggle):
```python
def toggle_auto_buyer(enabled: bool | None = None) -> dict[str, Any]:
    """Toggle Auto-Buyer ON or OFF from the dashboard or API."""
    state = load_auto_buyer_state()
    if enabled is None:
        state["enabled"] = not state.get("enabled", False)
    else:
        state["enabled"] = bool(enabled)
    state["updated_at_utc"] = iso_utc(utc_now())
    save_auto_buyer_state(state)
    return state
```
The breaker's reset function must NOT accept a silent/automatic path —
unlike `toggle_auto_buyer` (togglable either direction, no approver
required), model the reset on `model_promotion.py::rollback` instead
(lines 338-397): it requires an explicit CLI invocation, records who did it
and when, and — like `promote()`'s re-validation step (line 231) — should
re-check that the underlying condition (drawdown) has actually improved
before declaring the reset safe would be a bonus, but is not required by
SAFETY-02 as written (SAFETY-02 only requires the reset be explicit and
human-triggered, not that the system re-validate).

**Audit-log-on-mutation pattern** (`portfolio/auto_executor.py`
`set_auto_buyer_mode`, lines 483-490):
```python
    try:
        AuditLog(DATA / "audit.jsonl").append(
            "auto_buyer_mode_updated",
            "auto_buyer.mode",
            {"previous_mode": previous, "mode": mode, "source": "dashboard"},
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
```
Apply the same shape for both the trip event and the reset event —
append-first, fail-soft on the audit write itself (never block the
trip/reset on audit-log failure), matching the "audit-first mutations"
convention in the project's `CLAUDE.md`.

---

### `portfolio/auto_executor.py` (modify existing gate check)

**Analog:** the file's own existing daily-loss breaker, lines 1050-1059:
```python
        # Drawdown circuit breaker: once today's realized loss crosses the configured
        # threshold, stop considering new buys for the rest of the calendar day.
        # Reconciliation/settlement of already-open positions happens separately in
        # run_auto_buyer_cycle, before this method is called, so it is unaffected.
        if self.config.max_daily_loss_usd is not None and today_realized_pnl_usd <= -abs(
            self.config.max_daily_loss_usd
        ):
            result.total_evaluated = len(picks)
            result.rejected_daily_loss_breaker = len(picks)
            return result
```
**Core pattern:** add a new check in the same place (before the per-pick
loop, after `today_spend_usd, today_realized_pnl_usd = _today_auto_buyer_totals(...)`)
that queries the new portfolio circuit breaker's persisted state
(`portfolio_circuit_breaker.is_tripped()` or similar) and short-circuits
identically — `result.rejected_by_circuit_breaker = len(picks); return result`.
Keep both checks (per-day breaker AND portfolio breaker) — they are
complementary, not a replacement of one for the other; the per-day one
stays useful as an early warning even when the portfolio one hasn't
tripped. Note in the corresponding `AutoExecutionResult` dataclass
(lines 143-168) a new counter field, mirroring `rejected_daily_loss_breaker`
(line 164).

**Error handling pattern:** none needed beyond the existing early-return —
this file's gates are all "check condition, mutate counter, `return
result`", never raise.

---

### `dashboard/orders.py` (modify `submit_order`, manual buy path)

**Analog:** same file, lines 634-675 (fail-closed refusal pattern):
```python
    action = ticket.get("action", "buy")
    if action == "sell":
        ...
    else:
        ready, reason = _order_readiness(row, quote)
        if not ready:
            return {"status": "refused", "error": reason}
```
**Core pattern:** add a circuit-breaker check inside the `action != "sell"`
(buy) branch only — SAFETY-01 halts new **position entry**, not exits;
closing an existing position via `sell` must keep working even while
tripped (mirrors the existing comment in `auto_executor.py` line 1052-1053:
"Reconciliation/settlement of already-open positions... is unaffected").
Return the same `{"status": "refused", "error": "<reason>"}` shape used
throughout this function so the dashboard's existing error-surfacing code
needs no new branch.

---

### `dashboard/routes.py` + `dashboard/status.py` (visibility, SAFETY-02's "visible to operator" requirement)

**Analog:** `/api/auto-buyer/status` (GET, line 521-522) +
`/api/auto-buyer/toggle` (POST, lines 719-721) + `/api/drawdown` (GET,
lines 546-549) + `_auto_buyer_daily_loss_alert()` (`dashboard/status.py`
lines 322-353 — this already implements the "warn at half threshold,
escalate to error once tripped" alerting shape for the narrower daily
breaker):
```python
def _auto_buyer_daily_loss_alert() -> dict[str, Any] | None:
    ...
    level = "error" if realized_pnl_today <= -threshold else "warn"
    return {
        "level": level,
        "kind": "auto_buyer_daily_drawdown",
        "text": (
            f"Auto-Buyer realized P&L today: ${realized_pnl_today:+.2f} "
            f"({'breaker tripped, no new buys' if level == 'error' else 'approaching'} "
            f"-${threshold:.2f} drawdown limit)"
        ),
    }
```
**Core pattern:** add
1. a new GET route `/api/circuit-breaker/status` returning the persisted
   state dict (mirrors `/api/auto-buyer/status`'s one-liner
   `self._send(load_auto_buyer_state())`, line 522),
2. a new POST route `/api/circuit-breaker/reset` requiring the same
   `confirm: true` + `X-Dashboard-Token` guard every other mutating POST
   route in this file already goes through (`do_POST`, lines 633-652 —
   this check runs once, before the route dispatch, so no new file needs
   to reimplement it),
3. an alert function shaped like `_auto_buyer_daily_loss_alert`, added to
   whatever aggregate alert list the dashboard already surfaces (grep
   `_auto_buyer_daily_loss_alert(` for its call site before wiring — it is
   almost certainly folded into a combined `/api/alerts`-style summary).

**Auth/guard pattern** (`routes.py` lines 636-641, applies to the new
reset route):
```python
        if not self._local_origin_ok():
            self._send({"status": "refused", "error": "cross-origin request rejected"}, code=403)
            return
        if not secrets.compare_digest(str(self.headers.get("X-Dashboard-Token") or ""), _DASHBOARD_TOKEN):
            self._send({"status": "refused", "error": "missing or invalid dashboard session token"}, code=401)
            return
        ...
        if payload.get("confirm") is not True:
            self._send(
                {"status": "refused", "error": "confirmation required: resend with confirm=true"}, code=400
            )
            return
```

---

### CLI reset command (new, `--approved-by`-style operator action)

**Analog:** `src/model_prediction/model_promotion.py`, full file —
specifically `rollback()` (lines 338-397) for the one-command
irreversible-without-a-record semantics, and `main()`'s arg parsing +
dispatch (lines 440-515) for the CLI shape:
```python
def _arg(args: list[str], name: str) -> str | None:
    if name in args:
        idx = args.index(name)
        if idx == len(args) - 1:
            raise ValueError(f"{name} requires a value")
        return args[idx + 1]
    return None


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    ...
    try:
        cmd = args[0]
        if cmd == "promote":
            ...
        if cmd == "rollback":
            ...
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"PROMOTION ERROR: {exc}", file=sys.stderr)
        return 1
```
**Core pattern:** a `python -m model_prediction.portfolio_circuit_breaker
reset --approved-by WHO [--note TEXT]` command, requiring `--approved-by`
the same way `promote`/`rollback` require it — never optional, since
SAFETY-02 is specifically about the reset being an accountable human
action. Return code / print-JSON conventions should match
`model_promotion.py`'s (`print(json.dumps(record, indent=2)); return 0` on
success, `print(f"... ERROR: {exc}", file=sys.stderr); return 1` on
failure).

**Error handling pattern:** fail-LOUD for the reset action (raise
`ValueError` with descriptive context on invalid state, e.g. "not
currently tripped" or missing `--approved-by`), matching
`cli_production.py`'s module docstring distinction (lines 27-29): "the
lifecycle commands are fail-LOUD: an operator action that silently no-ops
is worse than an error."

---

### `tests/test_portfolio_circuit_breaker.py` (new)

**Analog 1 (gate-trip / gate-disabled pair):**
`tests/test_auto_polymarket_buyer.py::test_daily_loss_circuit_breaker_blocks_new_buys`
and `::test_daily_loss_circuit_breaker_disabled_by_default`, lines 835-893:
```python
def test_daily_loss_circuit_breaker_blocks_new_buys(monkeypatch):
    now = utc_now()
    monkeypatch.setattr(
        "model_prediction.portfolio.auto_executor._today_auto_buyer_totals",
        lambda *_a, **_k: (50.0, -130.0),  # realized loss already past the 125 threshold
    )
    config = AutoExecutionConfig(
        whitelisted_models=("tennis-surface-elo-v1",),
        max_daily_loss_usd=125.0,
    )
    buyer = AutoPolymarketBuyer(
        config=config,
        live_quote_fn=lambda slug: {"ask": 0.50, "market_slug": slug, "side": "long"},
    )
    picks = [...]
    res = buyer.evaluate_and_execute(picks)
    assert len(res.dry_run_orders) == 0
    assert res.rejected_daily_loss_breaker == 1
    assert res.total_evaluated == 1
```
Mirror this exact monkeypatch-the-data-source shape for the portfolio
breaker's pure evaluation function, plus one test proving the trip
**persists** across a fresh call (unlike the daily breaker, which
recomputes from scratch every call — this is the specific regression the
new persisted-state design exists to prevent) and one proving a tripped
breaker still allows `sell`/close-position actions through
`dashboard/orders.py::submit_order`.

**Analog 2 (pure gate-function unit tests):** `tests/test_economic_gate.py`
lines 47-93 — `test_economic_gate_passes_with_healthy_metrics`,
`test_economic_gate_enforces_drawdown_limit` — for testing
`portfolio_circuit_breaker`'s pure `evaluate()`/`max_drawdown`-consuming
function in isolation from any state file.

**Analog 3 (isolated runtime root + CLI reset test):**
`tests/test_model_promotion.py` lines 24-29 (autouse fixture) and
190-219 (`test_rollback_restores_previous_champion_in_one_command`,
`test_rollback_without_rollback_pointer_rejected`):
```python
@pytest.fixture(autouse=True)
def _isolated_runtime_root(tmp_path: Path, monkeypatch) -> None:
    """Promotion is operational: it must only record to an external
    runtime root, so every test gets its own isolated one."""
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
```
Use this exact fixture pattern for any test that reads/writes the
breaker's persisted state file through `RuntimePaths` — required per
`CLAUDE.md`'s "operational entry points FAIL CLOSED on a missing runtime
root" contract.

## Shared Patterns

### Fail-closed gate result (`GateResult`)
**Source:** `src/model_prediction/economic_gate.py`, lines 102-107, already
reused by `src/model_prediction/verification_checklist.py`.
**Apply to:** the portfolio circuit breaker's pure evaluation function —
return `GateResult(passed=False, reasons=[...], metrics={...})` rather than
a bespoke tuple/dict, so any future code that already knows how to render a
`GateResult` (dashboard, CLI) works unmodified.

### RuntimePaths-resolved, fail-closed operational state
**Source:** `src/model_prediction/runtime_paths.py` (the `RuntimePaths`
dataclass + `.resolve(require_external_runtime=True)`), consumed by
`cli_production.py` (lines 92-105) and `model_promotion.py` (lines 63-69).
**Apply to:** any new persisted circuit-breaker state file or DB row — add
a new `RuntimePaths` property rather than hand-building a path with
`PROJECT_ROOT / "data" / ...`; this is the exact "one resolver, not two
conventions" invariant `CLAUDE.md`'s 2026-08-14 entry protects, and the
2026-08-13 split-brain incident (docs) was caused by exactly this pattern
being skipped once.

### Atomic JSON state write (tmp + os.replace)
**Source:** `cli_production.py::_write_state` (lines 120-125) and
`auto_executor.py::save_auto_buyer_state` (lines 450-455) — identical
pattern in both places:
```python
    tmp = sp.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(sp)
```
**Apply to:** every write of the circuit breaker's persisted trip state.
Never write the state file in place — a crash mid-write must never leave
a half-written (hence unparseable, hence fail-open-by-accident) state
file.

### Audit-first mutation
**Source:** `auto_executor.py::set_auto_buyer_mode` (lines 483-490);
project-wide convention documented in `.claude/CLAUDE.md` ("Audit-first
mutations: Append to audit log BEFORE writing ledger").
**Apply to:** both the trip event (system-initiated) and the reset event
(human-initiated) — append an `AuditLog(DATA / "audit.jsonl")` entry with
`source` distinguishing the two (`"system_auto_trip"` vs
`"operator_reset"`), fail-soft on the audit write itself so it can never
block the actual state mutation.

### CLI dispatch shape (`{name} {subcommand} --flag value`)
**Source:** `model_promotion.py::main()` (lines 440-511) and
`cli_production.py::main()` (lines 596-633) — both use a hand-rolled
`_arg(args, name)` flag parser (no `argparse`), `cmd = args[0]`
dispatch, JSON-printed success payloads, `"ERROR: {exc}"`-prefixed
stderr on failure, exit code 0/1/2 (2 = usage error).
**Apply to:** the new circuit-breaker CLI entry point, for consistency
with every other operator-facing CLI in this codebase.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| Any config/schema entry for circuit-breaker thresholds (e.g. `config/production.yaml`'s hypothetical `circuit_breaker:` section) | config | — | `config.py::economic_gate_thresholds()` (lines 211-221) is the closest analog for "load a thresholds dataclass from a named yaml section with dataclass-field-driven defaults" — reuse that exact helper shape, but there is no existing `circuit_breaker:` yaml section to copy verbatim; the planner should design its schema fresh, following `EconomicGateThresholds`'s dataclass-with-defaults pattern (`economic_gate.py` lines 109-123). |

## Metadata

**Analog search scope:** `src/model_prediction/` (economic_gate.py,
model_promotion.py, production_registry.py, cli_production.py,
runtime_paths.py, system_health.py, portfolio/auto_executor.py,
portfolio/polymarket_dispatcher.py, dashboard/status.py,
dashboard/routes.py, dashboard/orders.py, config.py, market_eval.py,
verification_checklist.py), `tests/` (test_economic_gate.py,
test_model_promotion.py, test_auto_polymarket_buyer.py,
test_auto_buyer_mode_boundary.py)
**Files scanned:** ~18 read/grepped, 9 read in full or by targeted range
**Pattern extraction date:** 2026-09-23
**Tracked-source verification:** all cited analog paths confirmed via
`git ls-files` (tracked, not gitignored mirrors).
