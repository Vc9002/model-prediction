# Phase 1: Portfolio Circuit Breaker - Research

**Researched:** 2026-09-23
**Domain:** Live-money trading safety gate (Python, single-process, file/SQLite-backed state)
**Confidence:** HIGH

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SAFETY-01 | System-portfolio-level circuit breaker halts new position entry when `max_drawdown` crosses a configured threshold | Identified the single choke point (`AutoPolymarketBuyer.evaluate_and_execute`), the two existing-but-not-yet-wired drawdown computations, and the recommended way to combine them into one gate |
| SAFETY-02 | Circuit breaker trip state is visible and requires explicit human action to reset — no silent auto-resume | Identified the existing informal precedent (`max_daily_loss_usd`/`rejected_daily_loss_breaker`), why it does NOT satisfy SAFETY-02 (calendar-day auto-reset), the existing dashboard alert/state-toggle patterns to extend, and the exact persistence/reset mechanism to build instead |
</phase_requirements>

## Summary

This phase is an **extension of a pattern that already exists twice in the codebase, in two incomplete forms** — not a greenfield build. `src/model_prediction/portfolio/auto_executor.py` already has an informal, undocumented-to-user "drawdown circuit breaker" (`AutoExecutionConfig.max_daily_loss_usd`, checked at the very top of `AutoPolymarketBuyer.evaluate_and_execute()`), and `src/model_prediction/dashboard/status.py` already computes a real, portfolio-wide, cumulative peak-to-trough drawdown series (`_drawdown_summary()`) purely for dashboard display. Neither one satisfies both SAFETY-01 and SAFETY-02 today: the existing breaker resets automatically at local-midnight (a calendar-day cap, not a persisted human-gated halt), and the existing drawdown computation is display-only and never gates anything. `economic_gate.py::max_drawdown()` — the function REQUIREMENTS.md names — is a third, generic, already-tested pure function that is explicitly **not wired into any live decision yet** (its own module docstring says so).

The correct architecture threads these three pieces together rather than inventing a fourth: reuse `economic_gate.max_drawdown()`'s peak-to-trough algorithm (already unit-tested, already the name REQUIREMENTS.md anchors on), feed it the same chronological, portfolio-wide `pnl_units` series `_drawdown_summary()` already extracts from the Main ledger (`read_picks()`/`read_flat_picks()`), persist a trip flag to a new JSON state file following the exact same atomic-write pattern as `auto_buyer_state.json` (temp-file + `Path.replace`, survives process restarts), gate `AutoPolymarketBuyer.evaluate_and_execute()`'s new-position-entry loop on that persisted flag (same choke point the existing informal breaker already occupies), surface the trip through the existing dashboard alert list (`status()`'s `alerts` array — same shape `_auto_buyer_daily_loss_alert()` already uses), and provide a human reset action that mirrors `toggle_auto_buyer()`/`set_auto_buyer_mode()` exactly: a small state-mutating function, audit-logged via the existing `AuditLog` class, exposed through a new `/api/...` dashboard POST route (no new frontend UI needed — the dashboard already has a generic POST-action dispatch pattern in `routes.py`).

**Primary recommendation:** Build a new module (e.g. `src/model_prediction/portfolio/circuit_breaker.py`) that (1) computes portfolio drawdown by calling `economic_gate.max_drawdown()` on the same chronological `pnl_units` series `_drawdown_summary()` already builds, (2) persists trip state to a new `circuit_breaker_state.json` (same atomic-write idiom as `auto_buyer_state.json`), (3) is checked and can trip inside `AutoPolymarketBuyer.evaluate_and_execute()` before any pick is evaluated (same position as the existing `max_daily_loss_usd` check), and (4) exposes a `reset_circuit_breaker()` function audit-logged and callable only through an explicit new dashboard POST route — never auto-called by any scheduled job.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Drawdown computation (peak-to-trough over settled P&L) | API / Backend (`economic_gate.py`, pure function) | — | Pure computation over ledger data; no I/O, no UI concerns; already lives here and is already tested |
| Portfolio P&L series extraction (chronological `pnl_units`) | API / Backend (`dashboard/status.py::_drawdown_summary` today; should become a shared helper) | Database / Storage (Main ledger xlsx via `read_picks()`) | Reads settled-pick rows from the ledger; currently duplicated logic that should be extracted into one shared function callable from both the dashboard and the executor |
| Circuit breaker trip decision + persistence | API / Backend (new `portfolio/circuit_breaker.py`) | Database / Storage (new JSON state file under `RuntimePaths` data root) | Must survive process restarts (SAFETY-02) — an in-memory flag on the `AutoPolymarketBuyer` instance is recreated fresh every scheduler cycle and would not persist |
| New-position-entry gate | API / Backend (`AutoPolymarketBuyer.evaluate_and_execute()`) | — | This is the single, already-existing choke point every whitelisted-model automated buy passes through; see "Single Choke Point" section below |
| Trip visibility | API / Backend (`dashboard/status.py`'s `alerts` list, consumed by existing `/api/status` route) | Browser / Client (existing dashboard renders `alerts` already — no new UI needed) | PROJECT.md explicitly scopes out rebuilding the dashboard; the alerts list is an existing, already-rendered surface |
| Human reset action | API / Backend (new dashboard POST route + audit-logged state-mutating function) | — | Mirrors `toggle_auto_buyer()`/`set_auto_buyer_mode()` exactly — both are backend functions invoked via dashboard POST, not new UI |

## Standard Stack

No new dependencies. This phase is pure extension of existing, already-installed code:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| stdlib `json` + `pathlib.Path` | builtin | Persist circuit-breaker trip state | `auto_buyer_state.json` already uses exactly this (temp-file write + `Path.replace` for atomicity) — no reason to introduce a database table for a single boolean+metadata flag |
| `model_prediction.economic_gate.max_drawdown` | in-repo, existing | Peak-to-trough drawdown computation | Already exists, already unit-tested (`tests/test_economic_gate.py`), and is the exact function REQUIREMENTS.md names |
| `model_prediction.audit.AuditLog` | in-repo, existing | Append-only, hash-chained audit trail for trip/reset events | Every other state-mutating admin action in this codebase (`toggle_auto_buyer`, `set_auto_buyer_mode`, `set_auto_buyer_unit_value`) audit-logs through this exact class |
| `model_prediction.runtime_paths.RuntimePaths` | in-repo, existing | Resolve where the new state file lives | CLAUDE.md: "mutable operator state lives at the runtime root, not in the repo checkout" — the new state file must route through this, not a hand-built path |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `model_prediction.dashboard.picks.read_picks` / `read_flat_picks` | in-repo, existing | Source portfolio-wide settled `pnl_units` history | Reuse exactly as `_drawdown_summary()` already does — do not re-implement ledger reading |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| JSON state file | New SQLite table in `runs.db` or a new `.db` file | SQLite would be more consistent with the codebase's broader direction (ledger/runs already SQLite-canonical) but is heavier than needed for a single trip flag; the codebase's own precedent for exactly this kind of admin flag (`auto_buyer_state.json`) already uses plain JSON — matching that precedent is lower-risk than introducing a new persistence mechanism for a phase this narrow |
| Gating only `auto_executor.py` | Also gating the manual CLI `buy`/`sell` commands (`cli/commands.py`) | Manual CLI buys already require an explicit human `--execute-flag` per order and are not "automated new position entry" — REQUIREMENTS.md's SAFETY-01 wording ("halts new position entry") and the roadmap's framing ("trading halts automatically") point at the automated path. Flagged as an Open Question below rather than assumed. |

**Installation:** None required — every symbol used here already exists in `src/model_prediction/`.

**Version verification:** N/A — no external package versions to verify; this phase adds zero entries to `pyproject.toml`.

## Package Legitimacy Audit

Not applicable — this phase installs no external packages. All building blocks (`economic_gate.py`, `audit.py`, `runtime_paths.py`, `dashboard/status.py`, `dashboard/routes.py`, `portfolio/auto_executor.py`) are existing, already-committed, first-party modules in this repository.

## Architecture Patterns

### System Architecture Diagram

```
Scheduled daily job (4x/day, via run_supervisor)
        |
        v
run_auto_buyer_cycle()  [auto_executor.py]
        |
        v
AutoPolymarketBuyer.evaluate_and_execute()   <-- SINGLE CHOKE POINT for automated new positions
        |
        |--(NEW) circuit_breaker.is_tripped()? ---------> if True: reject all picks, return early
        |                                                  (mirrors existing max_daily_loss_usd check,
        |                                                   which stays in place alongside this)
        v
   [existing whitelist / blacklist / staleness / edge / budget checks, unchanged]
        |
        v
   OrderTicket -> PolymarketExecutor.execute() -> Polymarket US API


Settlement path (existing, unchanged):
cli/settle.py --> ledger.py (writes pnl_units on settled rows) --> data/main/<sport>.xlsx (Main ledger)
        |
        v
(NEW/shared) portfolio_pnl_sequence()  <-- extracted from dashboard/status.py::_drawdown_summary's
        |                                   existing read_picks()/read_flat_picks() + chronological sort
        v
economic_gate.max_drawdown(sequence) --> DrawdownResult(max_drawdown_units, peak_units, trough_units, ...)
        |
        v
(NEW) circuit_breaker.check_and_maybe_trip(DrawdownResult, threshold)
        |
        |--> writes circuit_breaker_state.json (tripped=True, tripped_at_utc, reason, metrics)  [RuntimePaths data root]
        |--> AuditLog(...).append("circuit_breaker_tripped", ...)
        v
dashboard/status.py::status()  -->  alerts.append({level: "error", kind: "circuit_breaker_tripped", ...})
        |
        v
Existing dashboard UI renders alerts (no new frontend work)


Human reset (explicit, never automatic):
Operator --> POST /api/circuit-breaker/reset (new route, mirrors /api/auto-buyer/toggle)
        |
        v
(NEW) reset_circuit_breaker(approved_by=...) --> rewrites circuit_breaker_state.json (tripped=False)
        |
        v
AuditLog(...).append("circuit_breaker_reset", ..., {"approved_by": ...})
```

### Single Choke Point (SAFETY-01, item 1)

`AutoPolymarketBuyer.evaluate_and_execute()` in `src/model_prediction/portfolio/auto_executor.py` is the single place every automated, whitelisted-model real-money buy passes through before an `OrderTicket` is built. It already has an early-exit drawdown check at its top (lines ~1050-1059 of the file, verbatim):

```python
# Drawdown circuit breaker: once today's realized loss crosses configured
# threshold, stop considering new buys for rest of the calendar day.
if self.config.max_daily_loss_usd is not None and today_realized_pnl_usd <= -abs(
    self.config.max_daily_loss_usd
):
    result.total_evaluated = len(picks)
    result.rejected_daily_loss_breaker = len(picks)
    return result
```
[VERIFIED: src/model_prediction/portfolio/auto_executor.py:1050-1059]

This is the exact insertion point for the new, persisted circuit breaker check — add it immediately before (or replacing) this block, following the identical "reject everything, set a counter, return early" shape.

**Manual order paths are separate and out of scope by default.** `cli/commands.py` builds `OrderTicket`s for a human-typed CLI `buy`/`sell` command and always requires an explicit `--execute-flag`/`execute_flag=args.execute_flag` per invocation [VERIFIED: src/model_prediction/cli/commands.py — `cmd_buy_position`/`cmd_sell_position`, ~lines 860-920]. This is a different trust boundary (a human already explicitly authorized this specific order) than "automated new position entry" — see Open Questions.

### Drawdown computation — two existing, unwired implementations (SAFETY-01, item 2)

**(a) `economic_gate.py::max_drawdown()`** — the function REQUIREMENTS.md names. Pure, already unit-tested, generic:

```python
# Source: src/model_prediction/economic_gate.py:35-64 [VERIFIED]
def max_drawdown(pnl_sequence: Sequence[float]) -> DrawdownResult:
    """Largest peak-to-trough decline in cumulative P&L.

    ``pnl_sequence`` must already be in chronological (settlement/event date)
    order -- drawdown computed on a shuffled sequence is meaningless.
    """
    if not pnl_sequence:
        return DrawdownResult(0.0, 0.0, 0.0, -1, -1)
    cumulative = 0.0
    peak = 0.0
    peak_index = 0
    worst = 0.0
    worst_peak_index = 0
    worst_trough_index = 0
    for index, pnl in enumerate(pnl_sequence):
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
            peak_index = index
        decline = peak - cumulative
        if decline > worst:
            worst = decline
            worst_peak_index = peak_index
            worst_trough_index = index
    return DrawdownResult(
        max_drawdown_units=round(worst, 6),
        peak_units=round(peak, 6),
        trough_units=round(peak - worst, 6),
        peak_index=worst_peak_index,
        trough_index=worst_trough_index,
    )
```

The module's own docstring states, verbatim: *"Neither gate function is wired into a live promotion decision yet"* [VERIFIED: src/model_prediction/economic_gate.py:1-17 — docstring reads "Neither gate is wired into the live promotion decision yet"]. It operates on **units**, not USD (see "Common Pitfalls" — this is a unit-mismatch trap against the existing USD-denominated informal breaker).

**(b) `dashboard/status.py::_drawdown_summary()`** — display-only, but already portfolio-wide and already reading real settled data:

```python
# Source: src/model_prediction/dashboard/status.py:432-479 [VERIFIED]
def _drawdown_summary(sport: str | None = None) -> dict:
    """Calculate realized cumulative P&L curve, peak high water mark, maximum drawdown."""
    from model_prediction.dashboard.picks import read_flat_picks, read_picks

    picks = read_picks() or read_flat_picks()
    if sport:
        picks = [
            p for p in picks if str(p.get("sport") or p.get("league") or "").casefold() == sport.casefold()
        ]

    settled_picks = [p for p in picks if str(p.get("status") or "").lower() == "settled"]
    settled_picks.sort(key=lambda p: str(p.get("event_start_utc") or p.get("created_at_utc") or ""))

    cumulative_pnl = 0.0
    high_water_mark = 0.0
    max_drawdown_units = 0.0
    for p in settled_picks:
        pnl = float(p.get("pnl_units") or 0.0)
        cumulative_pnl += pnl
        high_water_mark = max(high_water_mark, cumulative_pnl)
        drawdown = high_water_mark - cumulative_pnl
        max_drawdown_units = max(max_drawdown_units, drawdown)
    # ... builds a per-date series and returns totals ...
```

`pnl_units` is a real, canonical Main-ledger column — confirmed in the ledger schema's field list: `"probability_clv", "pnl_units", "settled_at_utc", ...` [VERIFIED: src/model_prediction/ledger.py:213-215]. `read_picks()` parses "every sport's Main ledger (`data/main/<sport>.xlsx`)" — i.e. this is already portfolio-wide across all sports, not sport-scoped, when called with `sport=None` [VERIFIED: src/model_prediction/dashboard/picks.py:93-97, docstring quoted verbatim].

**Recommendation:** extract the "read settled Main-ledger picks, sort chronologically, pull `pnl_units`" logic out of `_drawdown_summary()` into a small shared helper (e.g. `portfolio_pnl_sequence(sport: str | None = None) -> list[float]`), call it from both `_drawdown_summary()` (unchanged output) and the new circuit-breaker module, and feed its result into `economic_gate.max_drawdown()`. This reconciles the requirement's literal wording ("`max_drawdown` … already computed via `economic_gate.py`") with the only implementation that is actually wired to real portfolio data today.

### Persisted trip state, surviving restarts (SAFETY-02, item 1)

`auto_executor.py` already has the exact atomic-write idiom to copy for the new circuit-breaker state file:

```python
# Source: src/model_prediction/portfolio/auto_executor.py — save_auto_buyer_state (~line 450) [VERIFIED]
def save_auto_buyer_state(state: dict[str, Any]) -> None:
    AUTO_BUYER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = AUTO_BUYER_STATE_FILE.with_suffix(".json.tmp")
    temp_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp_file.replace(AUTO_BUYER_STATE_FILE)
```

`AUTO_BUYER_STATE_FILE = DATA / "auto_buyer_state.json"`, where `DATA = _paths.repo_root / "data"` and `_paths = RuntimePaths.resolve()` [VERIFIED: src/model_prediction/portfolio/auto_executor.py:40-41,102]. A new `circuit_breaker_state.json` should live alongside it at the same `RuntimePaths`-resolved data root — this guarantees it survives process restarts (it's a real file on disk, read fresh every time `evaluate_and_execute()` runs, not an in-memory flag on the `AutoPolymarketBuyer` instance, which is reconstructed every scheduler cycle per `_run_auto_buyer_cycle` / `run_auto_buyer_cycle`'s `AutoPolymarketBuyer(config=config)` call).

Recommended shape for the new state file (mirrors `auto_buyer_state.json`'s style of a flat dict with an `updated_at_utc` field):

```json
{
  "tripped": true,
  "tripped_at_utc": "2026-09-23T14:02:11Z",
  "reason": "portfolio max_drawdown 42.5U exceeds 40.0U limit",
  "metrics": {"max_drawdown_units": 42.5, "peak_units": 61.0, "trough_units": 18.5},
  "reset_by": null,
  "reset_at_utc": null
}
```

### Human reset action (SAFETY-02, item 2)

Existing precedent is a small, pure state-mutation function, audit-logged, invoked via a dashboard POST route — never a CLI subcommand (none exists for the analogous `toggle_auto_buyer`/`set_auto_buyer_mode` actions today; they are dashboard-only):

```python
# Source: src/model_prediction/portfolio/auto_executor.py:458-467 [VERIFIED]
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

And its audit-logging sibling:

```python
# Source: src/model_prediction/portfolio/auto_executor.py — set_auto_buyer_mode (~line 470-491) [VERIFIED]
def set_auto_buyer_mode(raw_mode: Any) -> dict[str, Any]:
    mode = str(raw_mode or "").strip().lower()
    if mode not in AUTO_BUYER_MODES:
        raise ValueError(f"Auto-Buyer mode must be one of {sorted(AUTO_BUYER_MODES)}")
    state = load_auto_buyer_state()
    previous = state.get("mode", DEFAULT_AUTO_BUYER_MODE)
    state["mode"] = mode
    state["updated_at_utc"] = iso_utc(utc_now())
    save_auto_buyer_state(state)
    try:
        AuditLog(DATA / "audit.jsonl").append(
            "auto_buyer_mode_updated",
            "auto_buyer.mode",
            {"previous_mode": previous, "mode": mode, "source": "dashboard"},
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return state
```

Wired to a dashboard POST route exactly like this [VERIFIED: src/model_prediction/dashboard/routes.py:718-721]:

```python
elif parsed.path == "/api/auto-buyer/toggle":
    enabled = payload.get("enabled")
    self._send(toggle_auto_buyer(enabled))
```

**Recommendation:** add `reset_circuit_breaker(approved_by: str | None = None) -> dict[str, Any]` following this exact shape (load state, require it be currently tripped or no-op, set `tripped=False`, `reset_at_utc`, `reset_by`, save, audit-log `circuit_breaker_reset`), and a new route `elif parsed.path == "/api/circuit-breaker/reset":`. No new frontend UI is required — PROJECT.md/REQUIREMENTS.md explicitly scope out rebuilding the dashboard, and the existing dashboard already has a generic pattern of POST-driven admin toggles that a human clicks; adding one more button/row that calls this existing POST-action dispatcher is UI-config, not UI-rebuilding.

### Recommended Project Structure

No new top-level directories needed — add one new module inside the existing `portfolio/` package, consistent with `auto_executor.py`'s location:

```
src/model_prediction/
├── portfolio/
│   ├── auto_executor.py        # existing — add the new gate check near its top-of-cycle checks
│   └── circuit_breaker.py      # NEW — trip decision, state persistence, reset function
├── economic_gate.py             # existing — max_drawdown() reused unmodified
└── dashboard/
    ├── status.py                # existing — extract shared pnl-sequence helper; add new alert
    └── routes.py                 # existing — add one new POST route for reset
```

### Anti-Patterns to Avoid
- **Reinventing peak-to-trough drawdown a third time:** `economic_gate.max_drawdown()` and `dashboard/status.py::_drawdown_summary()` already compute the same thing two different ways. Do not write a fourth version inside the new circuit-breaker module — call the existing tested function.
- **In-memory-only trip flag:** `AutoPolymarketBuyer` and its `AutoExecutionConfig` are reconstructed fresh every scheduler cycle (see `load_auto_buyer_state()`-driven config construction in `run_auto_buyer_cycle()`). A flag stored only on the instance or in a module-level global is invisible across the 4x/day launchd re-invocations and is exactly the "dangerous across restarts" failure mode SAFETY-02 is guarding against.
- **Reusing `max_daily_loss_usd`/`auto_buyer_state.json`'s `enabled` field as the trip mechanism:** these are semantically a *different* control (an operator toggle for whether the auto-buyer runs at all, and a same-day loss cap that auto-resets) — conflating them with a persisted "human must explicitly clear this" circuit breaker would make the existing toggle silently double as a safety gate, confusing both.
- **Unit mismatch:** `economic_gate.max_drawdown()` operates in **U (units)** consistent with `pnl_units`; the existing informal breaker's `max_daily_loss_usd` is in **USD**. Any new configured threshold must be explicit about which unit it's in, and the conversion (`unit_value_usd`) must not be silently dropped or double-applied.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| Peak-to-trough drawdown math | A new drawdown algorithm | `economic_gate.max_drawdown()` | Already exists, already unit-tested (4 tests in `tests/test_economic_gate.py` covering monotonic gains, worst-case peak/trough, and empty-sequence edge cases) |
| Atomic JSON state persistence | A new file-locking / temp-file scheme | The existing `save_auto_buyer_state()` temp-file + `Path.replace()` idiom | `Path.replace()` is atomic on POSIX; this exact pattern is already proven in production for `auto_buyer_state.json` |
| Audit trail for the trip/reset events | A bespoke log file | `model_prediction.audit.AuditLog` | Already provides hash-chained, append-only, lock-protected event logging; every other admin action in this codebase uses it |
| Trip visibility surface | A new dashboard page/panel | The existing `status()` function's `alerts` list (`dashboard/status.py`) | Already rendered by the existing dashboard UI; PROJECT.md explicitly excludes rebuilding the dashboard |
| Reset action UI | A new frontend control | The existing generic POST-route dispatch pattern in `dashboard/routes.py` (`/api/auto-buyer/toggle` etc.) | Same mechanism already used for every other admin toggle; adding a route is not "rebuilding the dashboard" |

**Key insight:** every piece this phase needs — drawdown math, atomic persistence, audit logging, alert surfacing, POST-route reset — already exists in this codebase in a directly analogous form. The engineering work is connecting four already-built pieces into one coherent gate, not building new mechanisms.

## Runtime State Inventory

Not a rename/refactor/migration phase — this section is omitted per the standard skip condition. (New state introduced: one new JSON file, `circuit_breaker_state.json`, under the `RuntimePaths`-resolved data root; this is a net-new file, not a migration of an existing one.)

## Common Pitfalls

### Pitfall 1: Unit mismatch between the two existing drawdown signals
**What goes wrong:** `economic_gate.max_drawdown()` and `_drawdown_summary()` both operate in **units** (`pnl_units`), while the existing informal breaker (`max_daily_loss_usd`) and its dashboard alert (`_auto_buyer_daily_loss_alert()`) operate in **USD**. A new configured threshold that mixes these up would trip at the wrong real-dollar level.
**Why it happens:** The codebase has both a units-based ledger convention (`units`, `pnl_units`) and a USD-based auto-buyer sizing convention (`unit_value_usd`, `max_daily_spend_usd`) living side by side.
**How to avoid:** Pick one unit for the new circuit breaker's threshold (units is more consistent with `max_drawdown()`'s native output and with the Main ledger's `pnl_units` column) and convert explicitly and once, at the boundary, if a USD-denominated config value is exposed to the operator.
**Warning signs:** A threshold value that "feels too easy or too hard to trip" relative to the account's actual `unit_value_usd`.

### Pitfall 2: Treating the existing `max_daily_loss_usd` breaker as already satisfying SAFETY-02
**What goes wrong:** It looks like a circuit breaker (same choke point, same "reject everything and set a counter" shape), but it silently resets every calendar day (`_today_auto_buyer_totals()` filters by `today` in Eastern time) — there is no persisted "stays halted until human reset" state anywhere for it.
**Why it happens:** It was built as a same-day loss cap (a risk-management convenience), not as the portfolio-level, restart-surviving, human-gated halt SAFETY-01/02 describe.
**How to avoid:** Build the new circuit breaker as a genuinely separate, persisted mechanism (see "Persisted trip state" above) rather than "wiring a reset requirement onto" the existing daily breaker — the two can coexist (the daily cap is still useful) but must not be conflated.
**Warning signs:** A plan that proposes modifying `max_daily_loss_usd`'s existing check in place instead of adding a new, separately-persisted check.

### Pitfall 3: Gating on an in-memory flag that doesn't survive the scheduler's per-cycle reconstruction
**What goes wrong:** The daily job invokes `run_auto_buyer_cycle()` fresh on every one of its 4x/day runs, which reconstructs `AutoExecutionConfig` and `AutoPolymarketBuyer` from `load_auto_buyer_state()` each time. Any trip state stored only as a Python attribute on the buyer instance, or a module-level variable, is lost the moment the process exits.
**Why it happens:** It's tempting to add a boolean field to `AutoExecutionConfig`/`AutoExecutionResult` and think that's "persisted" because it flows through `load_auto_buyer_state()` — but that only round-trips through the *existing* `auto_buyer_state.json`, which the new circuit breaker should not silently piggyback on (see Pitfall 2).
**How to avoid:** The trip check must read a dedicated on-disk file fresh on every `evaluate_and_execute()` call, exactly as `load_auto_buyer_state()` does for the auto-buyer's own config.
**Warning signs:** Tests pass locally within one process but the breaker "un-trips" after a real scheduled restart.

### Pitfall 4: Missing test isolation for the "today" clock, mirrored for the new breaker
**What goes wrong:** existing tests already had to add an `autouse` fixture (`_isolate_auto_buyer_daily_totals`) specifically because `evaluate_and_execute()` reads real production data by default, and a bad real-money day could make unrelated unit tests fail unpredictably [VERIFIED: tests/test_auto_polymarket_buyer.py:33-45, comment references "the 09-05 drawdown"]. The new circuit breaker's data source (Main ledger `pnl_units`) has the exact same risk.
**Why it happens:** `read_picks()`, `_drawdown_summary()`, and any new shared `portfolio_pnl_sequence()` helper default to reading the real repo `data/` tree unless a test explicitly overrides the data root.
**How to avoid:** Any new test suite for the circuit breaker must inject a `tmp_path`-based ledger/data root (or monkeypatch the read function) rather than relying on default paths, following the exact isolation pattern already established for the daily-loss breaker's tests.
**Warning signs:** A new test that passes when run alone but fails (or passes for the wrong reason) when run after other tests that mutate the real ledger fixtures.

## Code Examples

See the verbatim, line-cited snippets embedded above under "Architecture Patterns" — `max_drawdown()`, `_drawdown_summary()`, `save_auto_buyer_state()`, `toggle_auto_buyer()`, `set_auto_buyer_mode()`, and the `/api/auto-buyer/toggle` route — all sourced directly from this repository, not external docs (this is an in-repo extension phase, not a new-library-integration phase).

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| No portfolio-level halt exists | This phase adds one | Phase 1 (in progress) | Closes the single largest documented safety gap in the live-money system, per `.planning/research/SUMMARY.md`'s project-level research pass |
| Informal, calendar-day-only "circuit breaker" (`max_daily_loss_usd`) | A new, persisted, portfolio-wide, human-reset-only breaker, coexisting with the informal one | Phase 1 (in progress) | The informal breaker remains useful as a same-day risk cap; the new one is the actual SAFETY-01/02-compliant mechanism |

**Deprecated/outdated:** None — nothing is being removed in this phase; `max_daily_loss_usd` stays as-is unless the planner explicitly decides otherwise (see Open Questions).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|----------------|
| A1 | The new circuit breaker should gate only `AutoPolymarketBuyer.evaluate_and_execute()` (automated whitelisted-model buys), not the manual CLI `buy`/`sell` commands in `cli/commands.py` | Standard Stack (Alternatives Considered), Single Choke Point | If SAFETY-01's intent actually includes manual orders, a tripped breaker would not stop a human from manually running `cmd_buy_position --execute`, which may not match the user's real safety intent |
| A2 | The new circuit breaker's state file should be a new, separate JSON file (`circuit_breaker_state.json`) rather than new fields added to the existing `auto_buyer_state.json` | Persisted trip state | If the planner instead extends `auto_buyer_state.json`, it works functionally but couples an operator convenience toggle (`enabled`/`mode`) with a safety-critical persisted halt in one file — a design choice, not a fact, that should be confirmed |
| A3 | The threshold should be denominated in units (consistent with `max_drawdown()`'s native output), not USD | Common Pitfalls (Pitfall 1) | If the user actually wants a USD threshold to match the existing `max_daily_loss_usd` convention, the config schema and any UI label need the opposite unit |

## Open Questions

1. **Should the circuit breaker also block the manual CLI buy/sell commands, or only automated auto-buyer entries?**
   - What we know: `AutoPolymarketBuyer.evaluate_and_execute()` is the only path with no per-order human confirmation step; `cmd_buy_position`/`cmd_sell_position` require an explicit `--execute-flag` per invocation already.
   - What's unclear: whether "new position entry" in SAFETY-01's wording is meant narrowly (automated only) or broadly (any new BUY regardless of trigger).
   - Recommendation: default to gating the automated path only (lowest-risk, matches the roadmap's "trading halts automatically" framing); confirm with the user before the planner locks task scope, since gating the manual path too is a small addition if wanted (one more check inside `cmd_buy_position`, reading the same state file).

2. **Should the new persisted breaker supersede or run alongside the existing `max_daily_loss_usd` calendar-day breaker?**
   - What we know: they serve different purposes (same-day loss cap vs. persisted portfolio-level halt) and can coexist without conflict — both are simple early-return checks at the same point in `evaluate_and_execute()`.
   - What's unclear: whether the user wants the existing informal breaker retired/absorbed into the new one, to avoid having two separately-configured "circuit breaker" concepts.
   - Recommendation: keep both, distinctly named and independently configured, unless the user says otherwise — removing an existing safety check as a side effect of adding a new one is higher-risk than leaving it in place.

3. **What threshold value should trip the breaker, and where should it be configured?**
   - What we know: existing config precedent (`config/production.yaml`, `auto_buyer_state.json`) puts operator-tunable numeric limits either in the YAML config or the JSON state file, both readable/writable through existing loaders.
   - What's unclear: the actual numeric drawdown threshold — this is a business/risk decision, not a research question, and REQUIREMENTS.md does not specify a number.
   - Recommendation: planner should treat the threshold as an explicit, named config value (e.g. `circuit_breaker_max_drawdown_units` in `config/production.yaml` or a field in the new state file with a sane conservative default), not a hardcoded literal — consistent with `docs/AGENTS.md`'s "never hardcode thresholds" rule referenced in this project's `CLAUDE.md`.

## Environment Availability

Skipped — this phase has no external tool/service/runtime dependencies beyond what's already installed and running in this repository (Python 3.11+, existing SQLite/xlsx ledger, existing dashboard server). No new CLI, database, or network dependency is introduced.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ [VERIFIED: pyproject.toml:34-35] |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`, `testpaths = ["tests"]`) [VERIFIED: pyproject.toml:55-56] |
| Quick run command | `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_economic_gate.py tests/test_auto_polymarket_buyer.py -q` |
| Full suite command | `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|---------------------|--------------|
| SAFETY-01 | Portfolio `max_drawdown` crossing configured threshold halts new automated buys | unit | `pytest tests/test_circuit_breaker.py::test_trip_on_drawdown_threshold -x` | ❌ Wave 0 |
| SAFETY-01 | `AutoPolymarketBuyer.evaluate_and_execute()` rejects all picks when breaker is tripped (mirrors existing `rejected_daily_loss_breaker` shape) | unit | `pytest tests/test_auto_polymarket_buyer.py::test_rejects_all_when_circuit_breaker_tripped -x` | ❌ Wave 0 (extend existing file) |
| SAFETY-02 | Trip state persists across a fresh `AutoPolymarketBuyer`/process reconstruction (restart simulation) | unit | `pytest tests/test_circuit_breaker.py::test_trip_survives_reconstruction -x` | ❌ Wave 0 |
| SAFETY-02 | No code path auto-clears `tripped` — only the explicit reset function does | unit | `pytest tests/test_circuit_breaker.py::test_only_explicit_reset_clears_trip -x` | ❌ Wave 0 |
| SAFETY-02 | Tripped state surfaces in `status()`'s `alerts` list at `level: "error"` | unit | `pytest tests/test_dashboard_server.py::test_status_reports_circuit_breaker_alert -x` | ❌ Wave 0 (extend existing file — pattern already proven for `_auto_buyer_daily_loss_alert`) |
| SAFETY-02 | Reset route mutates state and audit-logs the action | unit | `pytest tests/test_dashboard_server.py::test_circuit_breaker_reset_route -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_circuit_breaker.py tests/test_auto_polymarket_buyer.py tests/test_economic_gate.py -q`
- **Per wave merge:** `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` and `.venv/bin/ruff check src/ tests/`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_circuit_breaker.py` — new file, covers SAFETY-01/SAFETY-02 core trip/persist/reset logic; no existing test file covers this module because it does not exist yet
- [ ] Extend `tests/test_auto_polymarket_buyer.py` — add circuit-breaker-tripped rejection case, reusing the existing `_isolate_auto_buyer_daily_totals`-style fixture pattern for the new module's data source too [VERIFIED: tests/test_auto_polymarket_buyer.py:32-45]
- [ ] Extend `tests/test_dashboard_server.py` (or wherever `status()`/`routes.py` are currently tested) — add alert-surfacing and reset-route cases
- No framework install needed — pytest 8+ is already installed and configured

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | No | This phase adds an internal admin action, not a new authentication surface; the existing dashboard's token-based auth (per `.claude/CLAUDE.md`: "Dashboard token: Per-session UUID generated at login, validated for real-money endpoint") already gates access to the dashboard's admin POST routes generally |
| V3 Session Management | No | Reuses existing dashboard session/token mechanism unmodified |
| V4 Access Control | Yes | The new `/api/circuit-breaker/reset` route must be reachable only through the same access-controlled dashboard surface as the existing `/api/auto-buyer/toggle` route — do not expose it unauthenticated |
| V5 Input Validation | Yes | `reset_circuit_breaker(approved_by=...)` should validate its input the same way `set_auto_buyer_mode()` validates its `mode` argument (explicit `ValueError` on bad input, never silently coerced) |
| V6 Cryptography | No | Not applicable — no new cryptographic operation introduced |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|-----------------------|
| Unauthenticated or scheduler-triggered auto-reset of a safety-critical flag | Elevation of Privilege / Repudiation | Reset must be an explicit, audit-logged, human-invoked action only (mirrors `toggle_auto_buyer`/`set_auto_buyer_mode`'s existing audit-logging discipline); no scheduled job may ever call `reset_circuit_breaker()` |
| Race between a scheduled daily-cycle write and a concurrent dashboard-triggered reset write to the same state file | Tampering | Reuse the exact atomic temp-file + `Path.replace()` write pattern already used for `auto_buyer_state.json` — this is already the codebase's proven approach to this exact class of race |
| Silent failure of the audit-log append masking a reset that "didn't really happen" | Repudiation | Follow the existing pattern's own caution: `set_auto_buyer_mode()` wraps its `AuditLog(...).append(...)` in a broad `try/except (OSError, RuntimeError, TypeError, ValueError): pass` — for a safety-critical reset action, consider whether this phase should surface (not swallow) an audit-log failure, since a silently-failed audit entry for a circuit-breaker reset is a bigger governance gap than for a routine mode toggle. Flagged for planner discretion, not asserted as a defect in the existing code (which is fine for its own, lower-stakes use case). |

## Sources

### Primary (HIGH confidence)
- Direct repo reads (this session): `src/model_prediction/economic_gate.py` (full file), `src/model_prediction/portfolio/auto_executor.py` (full file, 1485 lines), `src/model_prediction/dashboard/status.py` (relevant functions: `status()`, `_auto_buyer_daily_loss_alert()`, `_drawdown_summary()`), `src/model_prediction/dashboard/routes.py` (relevant route dispatch sections), `src/model_prediction/audit.py` (`AuditLog` class), `src/model_prediction/ledger.py` (field-name schema list), `src/model_prediction/dashboard/picks.py` (`read_picks`/`read_flat_picks`), `src/model_prediction/cli/commands.py` (manual buy/sell command construction), `src/model_prediction/runtime_paths.py` (`RuntimePaths.resolve`), `tests/test_economic_gate.py`, `tests/test_auto_polymarket_buyer.py`
- `.planning/research/SUMMARY.md` (project-level research, already read as required input) — confirms no `circuit_breaker`/`kill_switch`/`halt_trading` exists anywhere in `src/` prior to this phase

### Secondary (MEDIUM confidence)
- None used — this phase required no external documentation or web research; every finding is grounded directly in first-party source reads.

### Tertiary (LOW confidence)
- None.

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH — no new dependencies; every recommended building block is an existing, already-tested, already-committed module read directly this session
- Architecture: HIGH — the choke point, both existing drawdown implementations, the persistence idiom, and the reset-action pattern were all located and read verbatim from source, not inferred
- Pitfalls: HIGH — all four pitfalls are grounded in directly-observed code (the unit mismatch between `max_drawdown()`'s units output and `max_daily_loss_usd`'s USD field; the calendar-day-reset logic in `_today_auto_buyer_totals`; the per-cycle reconstruction in `run_auto_buyer_cycle`; the existing test-isolation fixture and its own comment referencing a real historical incident)

**Research date:** 2026-09-23
**Valid until:** 30 days (stable, internal-codebase-only research; not dependent on any external library's release cadence)
