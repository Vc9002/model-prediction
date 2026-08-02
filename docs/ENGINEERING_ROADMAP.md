# Engineering, dashboard, and portfolio-layer roadmap

This document covers everything `model_improvements.md` does not: software
architecture, dead code, test coverage, dashboard/product features, and
model-layer ideas that sit above any single sport (staking, ensembling,
cross-market consistency).

**Last reviewed**: 2026-07-20 (original snapshot). **Updated**: 2026-08-02
(removed completed items, updated line counts and statuses).

---

## 1. Verified bug: `/api/scan` is broken and unreachable

`dashboard_server.py`'s `/api/scan` route does:

```python
from model_prediction.data_sources.polymarket_us import capture_snapshots
...
results[s] = capture_snapshots(s, _today())
```

`capture_snapshots` does not exist in `polymarket_us.py`. The real function is
`capture_slate_snapshots(client, events_by_league, data_root, game_date)` — a
different name and a different signature (it needs a `PolymarketUSClient` and
pre-fetched `events_by_league`, not a bare sport string). Every call to
`/api/scan` raises `ImportError` and returns a 500. `dashboard.html` never
calls this route, so it is currently dead rather than user-visible, but it
would break immediately if wired up or curled directly. It also hardcodes a
sport fallback (`["mlb","nba","wnba"]`) that omits NFL entirely and all of
esports/kbo/npb, rather than reading `BBO_CAPTURE_SPORTS` from
`data_sources.polymarket_us`.

**Fix:** either repair the route (call `capture_slate_snapshots` with a real
client and discovered events, and default the sport list to
`BBO_CAPTURE_SPORTS`) or delete the route and its dead import if manual
rescan-from-dashboard isn't wanted.

---

## 2. Dead code: 12 orphaned modules, ~1,800 lines

These files are never imported by any other `src/` module and never
referenced by any test — verified by grepping every source file and test file
for each module's basename:

| File | Imported elsewhere? | Referenced in tests? |
|---|---|---|
| `features/soccer_form.py` | No | No |
| `features/lineup_strength.py` | No | No |
| `features/starting_pitcher.py` | No | No |
| `features/tennis_surface.py` | No | No |
| `features/guaranteed_signal.py` | No | No |
| `features/rest_travel.py` | No | No |
| `features/head_to_head.py` | No | No |
| `features/market_signals.py` | No | No |
| `features/pitchers.py` | No | No |
| `data_sources/openligadb.py` | No | No |
| `data_sources/mlb_statsapi.py` | No | No |
| `data_sources/football_data.py` | No | No |

`models/soccer.py`'s docstring even *describes* `soccer_form` conceptually
("Attack/defense strengths come from the soccer_form feature...") without
actually importing it — soccer.py was evidently refactored to compute this
inline, and the standalone module was never deleted. `features/pitchers.py`
also independently carries the ruff `E741` ambiguous-variable-name error
found in the last review.

This is real maintenance risk, not just clutter: a future contributor (or
agent) can plausibly believe one of these is wired into an active model
because it exists and looks complete, and spend time debugging or extending
code with zero production effect. Two options, in priority order:

1. **Delete** if the feature idea is fully superseded (e.g. `soccer_form.py`
   once its inline replacement in `models/soccer.py` is confirmed equivalent).
2. **Wire in + test** if the feature idea is still wanted (e.g.
   `starting_pitcher.py`/`bullpen.py`-adjacent work is exactly what
   `model_improvements.md` section 8 asks for as MLB's #1/#3 priorities —
   check whether these stubs are a useful starting point before writing that
   code from scratch again).

Do not leave them as-is; "maybe useful later" untested modules are exactly
the pattern that produced this session's earlier real bugs (falsy-zero
probability, chronological-split off-by-one) — code that nothing exercises
is code nothing catches when it's wrong.

---

## 3. File-size / single-responsibility gaps

| File | Lines (as of 2026-08-02) | Concerns mixed together |
|---|---:|---|
| `dashboard_server.py` | 4,782 (was 2,978) | HTTP transport (`BaseHTTPRequestHandler`, manual if/elif routing for ~20 GET + 8 POST routes), pick/portfolio decoration, order preview/submission (real-money surface), audit tail, job status, caching, token-based auth |
| `cli.py` | 3,943 (was 1,831) | argparse wiring for ~25 subcommands across forecast/ledger/backfill/esports/international-baseball/validation, **still no dedicated test file** (`tests/test_cli.py` doesn't exist) |
| `validation.py` | 1,179 | per-sport walk-forward feature construction, chronological split, calibration, promotion-gate reporting |
| `roadmap_challenger.py` | 1,029 | factorial experiment harness; large but single-purpose and well-isolated, lower priority to split |
| `ledger.py` | 1,028 | append, settlement, closing-price capture, review workflow, reporting |
| `backtester.py` | 796 | — not reviewed in depth this pass |

**Notable**: `dashboard_server.py` has grown from 2,978 to 4,782 lines since
the original review — a 60% increase, nearly all of it in the same monolithic
file. The token-based auth, SELL-path P&L fix, portfolio-history, and
multi-ledger scan all landed in this same file. The split recommended below
is now *more* urgent, not less.

`cli.py` having no direct unit test despite being the largest file in the repo
is the sharpest gap: argument parsing, default-date wiring (the Eastern-time
work from this session lives here), and command dispatch are only exercised
indirectly through whatever other tests happen to call CLI functions.

Recommended split, in order of value:

1. **`cli.py` → `cli/` package.** One module per command family
   (`cli/forecast.py`, `cli/ledger.py`, `cli/backfill.py`, `cli/esports.py`,
   `cli/international_baseball.py`, `cli/validate.py`), a thin
   `cli/__main__.py`/`cli.py` that just builds the `argparse` tree and
   dispatches. Add `tests/test_cli.py` covering argument defaults (especially
   that every `--date` truly defaults to `eastern_today()`) and dispatch
   routing.
2. **`dashboard_server.py` → `dashboard/` package.** `dashboard/routes.py`
   (a route-name → handler dict instead of the if/elif chain — trivially
   testable and immediately would have caught the `/api/scan` typo above),
   `dashboard/views.py` (pick/portfolio/matrix decoration), `dashboard/orders.py`
   (preview/submit — isolate the real-money surface behind its own module
   boundary so it's obvious at a glance what code can move money), thin
   `dashboard_server.py` entrypoint left in place for the existing launchd
   job and `dash` script to keep working unchanged.
3. **`ledger.py` split** into `ledger/append.py`, `ledger/settlement.py`,
   `ledger/report.py` once the above two are done — lower urgency since it
   already has strong test coverage (`test_ledger_hardening.py` and others).

---

## ~~4. No CI backstop~~ ✅ DONE (2026-08-02)

`.github/workflows/ci.yml` now exists — runs `ruff check` and `pytest` on
every push and PR. Python 3.12 on ubuntu-latest.

The local pre-push hook (pytest blocking, mypy advisory) remains as a
complement, not a replacement.

---

## 5. Missing or thin test coverage

Existing gaps (2026-08-02 check):

- **`cli.py`**: still has no direct test file (`tests/test_cli.py` does not
  exist). The 3,943-line file has *zero* dedicated unit tests.
- **`dashboard_server.py`**: `tests/test_dashboard_server.py` exists (65
  tests) but the monolithic server file has nearly doubled in size.
- **Execution-ticket binding**: no test injects a mismatched ticket to
  confirm the cap-recomputation path rejects it.
- **Audit-failure recovery**: no test injects an audit failure between
  ledger-write and audit-append to confirm the gap is surfaced.
- **Secret-redaction / future-timestamp**: no dedicated test.

~~- `tests/test_research_io.py`~~ ✅ DONE — file exists now.

---

## 6. Ledger storage: spreadsheets, no schema, no transactions

Multiple real ledger bugs have been caused or made harder to catch by the
Excel-based storage layer: inconsistent odds-field precision, sign errors in
`research_pnl_units`, spreadsheet-as-database drift (no schema enforcement,
no transactions, full-file rewrite on every append). The new `ModelLedger`
(`model_ledger.py`, 2026-08-02) is also Excel-based by design — following the
existing pattern rather than fixing it at the storage layer.

Two concurrent writers touching the same `.xlsx` files raises real corruption
risk. The `.lock` files (via `fcntl.flock`) mitigate this but do not provide
ACID guarantees — a crash during write leaves a partially-written file.

**Suggestion:** migrate the ledger to SQLite (`data/ledger.db`), a single
file with no server, ACID transactions, and a real schema — while keeping an
`.xlsx`/`.csv` export for human review, generated from the DB rather than
being the source of truth. This is a genuinely bigger change (every ledger
read/write call site would need updating), so it belongs on the roadmap
rather than something to do opportunistically; but it's the highest-leverage
data-integrity fix available given the bug history this session already
uncovered in the current storage layer.

---

## 7. Dashboard product gaps

| Gap | Why it matters | What exists already |
|---|---|---|
| No push notifications | Dashboard is pull-only; the live WNBA "stale filled order-control" bug was only caught by looking directly. A local macOS notification or Slack webhook on new qualified pick/settlement/stale-order would catch these without requiring the tab open. | `_daily_pipeline_status()` already detects staleness |
| No CLV/edge-decay chart | `cli.py clv` already computes closing-line-value; it's never rendered. | The CLI command and output format already exist |
| No drawdown/exposure chart | `economic_gate.py` already computes `max_drawdown` and bootstrap CIs; backend-only. | Data exists, no dashboard tab consumes it |
| No BBO-capture health view | Open question about live slate-discovery counts never resolved — no view showing captured-vs-discovered per sport/day. | `market_snapshots()`/`_pick_quote()` and odds directories contain everything needed |
| No export/report | No CSV/weekly-summary export for offline review outside the dashboard. | `performance()`/`bets_view()` already assemble the underlying rows |
| Dashboard startup uses `pkill -f` | CHECKLIST.md flag; should use PID-file approach instead. | `.codewhale/instructions.md` explicitly forbids `pkill` on dashboard |

~~Uncertainty-aware staking~~ ✅ DONE (2026-07-31) — `edge_scaled_units` in
`units.py` now actually reads `model_uncertainty` and haircuts the raw edge
before scaling. Unit range widened to 1.0U-2.0U.

---

## 8. Portfolio/meta-model layer (new model-layer ideas, not sport-specific)

`model_improvements.md` is exhaustive per-sport, but nothing in this repo
currently sits *above* individual sport models. Four ideas that don't require
new data sources — only combining evidence that's already collected:

1. ~~**Uncertainty-aware staking.**~~ ✅ DONE (2026-07-31). `edge_scaled_units`
   now uses `model_uncertainty` to haircut the edge, and unit range is 1.0-2.0U.
2. **Cross-market internal consistency check.** Within Polymarket, a game's
   moneyline, spread, and total markets imply related no-vig probabilities.
   A large mismatch is either a data-quality problem or a genuine mispricing.
   Buildable from existing BBO data.
3. **CLV-triggered health monitoring.** `clv` is already computed on demand;
   nothing watches it over time. A rolling check — "has realized CLV trended
   negative over the last N graded picks?" — could flag automatic-review-needed.
4. **Simple ensembling across sport families.** MLB/NBA/WNBA/NFL train fully
   independent artifacts. A shared meta-calibrator (one isotonic/Platt layer
   across all four sports' out-of-fold predictions) could correct systematic
   miscalibration common to the shared feature-engineering pipeline.

None of these are sport-specific research (they don't touch
`model_improvements.md`'s per-league tables) and none require a new paid or
authenticated data source.

---

## 9. Explicitly out of scope, and why

- **Kalshi cross-venue arbitrage/best-execution.** `data_sources/kalshi.py`
  is a deliberate `KalshiDeferredError` stub: the account holder is outside
  the US and Kalshi requires US residency (CFTC-regulated). There is nothing
  to build here until that changes.
- ~~**Dashboard authentication.**~~ ✅ DONE (2026-08-02). The dashboard now
  has per-session token-based auth on the real-money order-execution endpoint
  (`POST /api/order/submit`). The token is generated at server start, injected
  into the served page, and required on every POST. Localhost bind still
  provides the primary security boundary.

---

## Suggested order (updated 2026-08-02)

1. Fix or delete `/api/scan` (small, isolated, verified bug — still present).
2. Delete the 12 orphaned modules (removes false signal about what's active;
   low risk since nothing depends on them).
3. Add `tests/test_cli.py` (closes the single biggest test-coverage gap in the
   repo — 3,943-line file with zero dedicated tests).
4. Split `cli.py` → `cli/` package (moderate effort, enables testing).
5. Split `dashboard_server.py` → `dashboard/` package (moderate effort, 4,782
   lines and growing; the route-table refactor would have caught `/api/scan`).
6. Dashboard product gaps (section 7) — push notifications and BBO-capture
   health view are the highest-value since they catch real problems.
7. Portfolio/meta-model layer (section 8, items 2-4) — genuinely new modeling
   work; CLV-triggered monitoring is the cheapest since data already exists.
8. SQLite ledger migration (section 6) — largest effort, highest integrity
   payoff; do this once the smaller fixes above stop competing for the same
   files.
9. Replace `pkill -f` dashboard startup with PID-file approach.
