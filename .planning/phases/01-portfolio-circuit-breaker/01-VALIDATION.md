---
phase: "1"
slug: "portfolio-circuit-breaker"
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-09-23"
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8+ |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`, `testpaths = ["tests"]`) |
| **Quick run command** | `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_economic_gate.py tests/test_auto_polymarket_buyer.py -q` |
| **Full suite command** | `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_circuit_breaker.py tests/test_auto_polymarket_buyer.py tests/test_economic_gate.py -q`
- **After every plan wave:** Run `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` and `.venv/bin/ruff check src/ tests/`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-TBD | 01 | 0 | SAFETY-01 | T-01-01 | Portfolio `max_drawdown` crossing configured threshold halts new automated buys | unit | `pytest tests/test_circuit_breaker.py::test_trip_on_drawdown_threshold -x` | ❌ W0 | ⬜ pending |
| 01-01-TBD | 01 | 0 | SAFETY-01 | T-01-01 | `AutoPolymarketBuyer.evaluate_and_execute()` rejects all picks when breaker is tripped | unit | `pytest tests/test_auto_polymarket_buyer.py::test_rejects_all_when_circuit_breaker_tripped -x` | ❌ W0 | ⬜ pending |
| 01-01-TBD | 01 | 0 | SAFETY-02 | T-01-02 | Trip state persists across a fresh `AutoPolymarketBuyer`/process reconstruction | unit | `pytest tests/test_circuit_breaker.py::test_trip_survives_reconstruction -x` | ❌ W0 | ⬜ pending |
| 01-01-TBD | 01 | 0 | SAFETY-02 | T-01-01 | No code path auto-clears `tripped` — only the explicit reset function does | unit | `pytest tests/test_circuit_breaker.py::test_only_explicit_reset_clears_trip -x` | ❌ W0 | ⬜ pending |
| 01-01-TBD | 01 | 0 | SAFETY-02 | — | Tripped state surfaces in `status()`'s `alerts` list at `level: "error"` | unit | `pytest tests/test_dashboard_server.py::test_status_reports_circuit_breaker_alert -x` | ❌ W0 | ⬜ pending |
| 01-01-TBD | 01 | 0 | SAFETY-02 | T-01-01 / T-01-03 | Reset route mutates state and audit-logs the action | unit | `pytest tests/test_dashboard_server.py::test_circuit_breaker_reset_route -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_circuit_breaker.py` — new file; stubs for core trip/persist/reset logic (SAFETY-01, SAFETY-02) — no existing file covers this module since it doesn't exist yet
- [ ] Extend `tests/test_auto_polymarket_buyer.py` — add circuit-breaker-tripped rejection case, reusing the existing `_isolate_auto_buyer_daily_totals`-style fixture pattern
- [ ] Extend `tests/test_dashboard_server.py` (or wherever `status()`/`routes.py` are tested) — add alert-surfacing and reset-route cases
- No framework install needed — pytest 8+ already installed and configured

---

## Manual-Only Verifications

*None — all phase behaviors have automated verification.*

---

## Threat Model (Security Domain)

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | No | Internal admin action, not a new auth surface; existing dashboard token auth already gates admin POST routes |
| V3 Session Management | No | Reuses existing dashboard session/token mechanism unmodified |
| V4 Access Control | Yes | New `/api/circuit-breaker/reset` route must be reachable only through the same access-controlled dashboard surface as `/api/auto-buyer/toggle` — never expose unauthenticated |
| V5 Input Validation | Yes | `reset_circuit_breaker(approved_by=...)` must validate input like `set_auto_buyer_mode()` does — explicit `ValueError` on bad input, never silently coerced |
| V6 Cryptography | No | Not applicable |

### Known Threat Patterns (T-01-01, T-01-02, T-01-03)

| Threat Ref | Pattern | STRIDE | Standard Mitigation |
|---------|--------|--------|-----------------------|
| T-01-01 | Unauthenticated or scheduler-triggered auto-reset of a safety-critical flag | Elevation of Privilege / Repudiation | Reset must be an explicit, audit-logged, human-invoked action only (mirrors `toggle_auto_buyer`/`set_auto_buyer_mode`); no scheduled job may ever call `reset_circuit_breaker()` |
| T-01-02 | Race between a scheduled daily-cycle write and a concurrent dashboard-triggered reset write to the same state file | Tampering | Reuse the exact atomic temp-file + `Path.replace()` write pattern already proven for `auto_buyer_state.json` |
| T-01-03 | Silent failure of the audit-log append masking a reset that "didn't really happen" | Repudiation | For this safety-critical reset action, surface (do not swallow) an audit-log write failure — unlike the existing lower-stakes `set_auto_buyer_mode()` precedent, which broadly catches and passes. Flagged for planner discretion. |

**ASVS Level:** L2 (default) | **Block on:** BLOCKER severity threats only

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
