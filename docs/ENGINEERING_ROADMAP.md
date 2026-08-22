# Engineering, dashboard, and portfolio-layer roadmap

This document covers everything `model_improvements.md` does not: software
architecture, dead code, test coverage, dashboard/product features, and
model-layer ideas that sit above any single sport (staking, ensembling,
cross-market consistency).

**Last reviewed**: 2026-07-20 (original snapshot). **Updated**: 2026-08-22
(re-verified every item against the live tree — most of the 2026-08-02 list
is now done; see below for what's actually still open).

---

## 1. ~~`/api/scan` broken~~ ✅ DONE

Route no longer exists in this form — superseded by the split `dashboard/`
package and the real Polymarket scanner (`polymarket_dispatcher.py`,
`polymarket_scanner.py`, `/api/polymarket/scan`).

---

## 2. Dead code: 4 orphaned modules remain (was 12)

Re-verified 2026-08-22 by grepping every source and test file for each
module's basename. 8 of the original 12 are gone (deleted or superseded);
these 4 are still present and still unimported anywhere:

| File | Imported elsewhere? | Referenced in tests? |
|---|---|---|
| `features/lineup_strength.py` | No | No |
| `features/tennis_surface.py` | No | No |
| `features/head_to_head.py` | No | No |
| `data_sources/mlb_statsapi.py` | No | No |

`features/soccer_form.py`, `starting_pitcher.py`, `guaranteed_signal.py`,
`rest_travel.py`, `market_signals.py`, `pitchers.py`,
`data_sources/openligadb.py`, `data_sources/football_data.py` are all gone.

Still the same maintenance risk as before: a future contributor (or agent)
can plausibly believe one of these is wired into an active model because it
exists and looks complete. `tennis_surface.py` in particular is worth a
second look before deleting — `models/tennis.py` grew its own inline
Bayesian surface-Elo shrinkage on 2026-08-22 (`match_probability`), so check
whether that work already supersedes this module or whether the module was
an earlier, unfinished attempt at the same idea.

**Action**: delete if superseded, wire in + test if still wanted. Don't
leave as-is.

---

## 3. File-size / single-responsibility gaps — ✅ DONE

Both flagged monoliths are now packages:

- `cli.py` (was 3,943 lines, zero tests) → `cli/` package
  (`commands.py`, `daily.py`, `forecast.py`, `parser.py`, `settle.py`,
  `state.py`, `main.py`) with `tests/test_cli.py` now 2,394 lines of
  coverage.
- `dashboard_server.py` (was 4,782 lines) → `dashboard/` package
  (`routes.py`, `views` split across `backtests.py`, `common.py`,
  `data_service.py`, `evidence.py`, `jobs.py`, `matrix.py`, `orders.py`,
  `picks.py`, `status.py`).

`ledger.py` (single 1,028-line file) was **not** split into
`ledger/append.py`/`settlement.py`/`report.py` as originally suggested —
still one file, alongside a newer `ledger_parity.py`. Given it already has
strong test coverage (`test_ledger_hardening.py`) and the storage-layer
migration below already landed, this split is now low priority; revisit
only if the file keeps growing.

---

## 4. ~~No CI backstop~~ ✅ DONE (2026-08-02)

`.github/workflows/ci.yml` runs `ruff check` and `pytest` on every push/PR
(Python 3.12, ubuntu-latest). Local pre-push hook remains as a complement.

---

## 5. Missing or thin test coverage

Re-checked 2026-08-22 against `docs/PROJECT_STATUS.md`'s latest test count
(1,938 passed, 3 skipped, 0 failed) and TODO.md's P1/P2 checklists:

- ✅ `cli.py` coverage — `tests/test_cli.py` now exists (50+ tests per
  TODO.md; full monolith split was deliberately sequenced last, per
  `RESEARCH_BACKLOG.md`'s "developer ergonomics LAST" ordering — that
  ordering decision has been honored).
- ✅ Execution-ticket binding / cap-recomputation rejection —
  `tests/test_execution_gate.py`.
- ✅ Audit-failure recovery — crash-injection coverage in
  `test_ledger_hardening.py` (+ `_verify_chain` detection).
- ✅ Secret-redaction / future-timestamp — regression tests in
  `test_eligibility.py` / `test_the_odds_api.py`.

No open items left in this section as of 2026-08-22.

---

## 6. ~~Ledger storage: spreadsheets, no schema, no transactions~~ ✅ DONE

The SQLite migration this section used to *suggest* has already happened
and is the current live state (see root `CLAUDE.md`'s 2026-08-14/16 notes):
`RuntimeLedgerStore` / the runtime-root `ledgers/ledgers.db` (SQLite, WAL,
one transaction per mutation + audit event) is canonical;
`.xlsx`/`.csv` workbooks are now a best-effort **export**, not the source of
truth, and their write failures only log a warning rather than blocking the
commit. Verified 2026-08-16 (per CLAUDE.md): sqlite row counts match xlsx
Picks-sheet row counts exactly across all four ledger tiers. Don't re-flip
`MODEL_PREDICTION_LEDGER_AUTHORITY` back to `xlsx` without an explicit
decision — that's a real open governance question, not an unfinished task.

---

## 7. Dashboard product gaps

Re-checked 2026-08-22:

| Gap | Status |
|---|---|
| No push notifications | ✅ DONE — `notify_operator()` dispatcher in `run_supervisor.py` (2026-08-20). |
| No CLV/edge-decay chart | ✅ DONE — `/api/clv` (rolling 30-day CLV closing-beat rate), `dashboard/status.py`. |
| No BBO-capture health view | ✅ DONE — `/api/capture_health` (7-day BBO snapshot freshness), `dashboard/status.py`. |
| No drawdown/exposure chart | ⚠️ Still open — `economic_gate.py` computes `max_drawdown` and bootstrap CIs, but no `dashboard/*.py` route or view consumes it yet. |
| No export/report | ⚠️ Still open — no CSV/weekly-summary export route found in `dashboard/`. `performance()`/`bets_view()` still only assemble rows for in-app display. |
| Dashboard startup uses `pkill -f` | ✅ DONE — no `pkill` usage found anywhere in the repo; startup goes through the PID-tracked supervisor. |

~~Uncertainty-aware staking~~ ✅ DONE (2026-07-31, unchanged).

---

## 8. Portfolio/meta-model layer

Re-checked 2026-08-22 against what's actually wired:

1. ~~**Uncertainty-aware staking.**~~ ✅ DONE (2026-07-31).
2. ~~**Cross-market internal consistency check.**~~ ✅ DONE —
   `cross_market_consistency.py` validates monotonicity
   (P(cover -1.5) ≤ P(moneyline win)) and complementarity
   (P(over) + P(under) = 1.0), per PROJECT_STATUS's 2026-08-20 entry.
3. **CLV-triggered health monitoring** — ⚠️ still open. `/api/clv` (item 7
   above) now renders CLV, but nothing watches it *over time* and raises an
   alert on a negative trend across the last N graded picks — the rolling
   check itself doesn't exist yet, just the on-demand number.
4. ~~**Simple ensembling across sport families.**~~ ✅ DONE —
   `meta_calibrator.py` provides multi-sport pooled Platt scaling / isotonic
   regression across sports' out-of-fold predictions (2026-08-20).

Also landed since the original review, not on the original list:
- **Correlation-aware exposure sizing is still NOT done** — picks carry
  `correlation_tags` but same-slate same-game ML+spread+total exposure isn't
  jointly capped at the portfolio level yet (flagged in
  `RESEARCH_BACKLOG.md`'s 2026-08-17 brainstorm section, still queued/
  unscheduled). This is the single highest-value item left in this section
  given it's real-money risk, not just an accuracy nice-to-have.

---

## 9. Explicitly out of scope, and why

- **Kalshi cross-venue arbitrage/best-execution.** Still deliberately
  deferred — `KalshiDeferredError` stub, US-residency requirement unmet.
  (Note: `tennis-trader` — a separate repo — did add real Kalshi
  integration on 2026-08-21, but that's a different exchange account/
  project, not this one; don't conflate the two when reviewing scope.)
- ~~**Dashboard authentication.**~~ ✅ DONE (2026-08-02).

---

## 10. ~~sklearn `penalty=` deprecation~~ ✅ DONE (2026-08-22)

Full-suite run surfaced 756 warnings, all `FutureWarning` from sklearn 1.8:
`LogisticRegression(penalty=...)` is deprecated and removed in sklearn 1.10.
5 call sites fixed (`rebuild/ensemble.py`, `rebuild/calibration.py` ×2,
`rebuild/market_residual.py`, `rebuild/models/tennis.py`) — `penalty=None`
replaced with `C=np.inf`, `penalty="l2"` replaced with `l1_ratio=0`, per
sklearn's own migration guidance. Verified numerically inert: targeted
tests re-run with `-W error::FutureWarning` (61 tests, all pass, no
warning raised) — same `lbfgs` solver, same effective regularization,
just the new parameterization.

---

## Suggested order (updated 2026-08-22)

Most of the 2026-08-02 list is done. What's actually left, in priority
order:

1. **Correlation-aware exposure sizing** (section 8) — real-money risk gap,
   data (`correlation_tags`) already exists, just isn't enforced at the
   portfolio level.
2. **CLV-triggered health monitoring** (section 8) — cheap, data already
   computed on demand, just needs a rolling-window trend check.
3. **Delete-or-wire the 4 remaining orphaned modules** (section 2) — small,
   low-risk, mostly a documentation/clarity fix at this point. Check
   `tennis_surface.py` against the new inline shrinkage in `models/tennis.py`
   first.
4. **Drawdown/exposure chart + CSV export** (section 7) — data already
   exists server-side (`economic_gate.py`, `performance()`/`bets_view()`),
   just needs a route and a dashboard tab.
5. **Runtime-root offsite backup** (from `RESEARCH_BACKLOG.md`'s 2026-08-17
   operational-hardening brainstorm — not originally in this doc, but the
   same "boring infra debt" category as this doc's old section 6, and now
   arguably the single biggest remaining integrity gap since the SQLite
   migration itself is done: canonical ledger + audit chain still lives on
   one machine with no offsite copy).
6. `ledger.py` split into `ledger/append.py`/`settlement.py`/`report.py` —
   low priority, file is stable and well-tested.
