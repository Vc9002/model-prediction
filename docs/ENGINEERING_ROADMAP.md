# Engineering, dashboard, and portfolio-layer roadmap

This document covers everything `model_improvements.md` does not: software
architecture, dead code, test coverage, dashboard/product features, and
model-layer ideas that sit above any single sport (staking, ensembling,
cross-market consistency). It is a review snapshot from 2026-07-20 against the
current working tree. Nothing here changes model config, artifacts, the
ledger, or execution behavior — it is a punch list, not a completed change.

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

## 2. Dead code: 11 orphaned modules, ~1,800 lines

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

| File | Lines | Concerns mixed together |
|---|---:|---|
| `dashboard_server.py` | 2,978 | HTTP transport (`BaseHTTPRequestHandler`, manual if/elif routing for ~20 GET + 8 POST routes), pick/portfolio decoration, order preview/submission (real-money surface), audit tail, job status, caching |
| `cli.py` | 1,831 | argparse wiring for ~25 subcommands across forecast/ledger/backfill/esports/international-baseball/validation, **zero dedicated test file** |
| `validation.py` | 1,179 | per-sport walk-forward feature construction, chronological split, calibration, promotion-gate reporting |
| `roadmap_challenger.py` | 1,029 | (new, from this session's review) — factorial experiment harness; large but single-purpose and well-isolated, lower priority to split |
| `ledger.py` | 1,028 | append, settlement, closing-price capture, review workflow, reporting |
| `backtester.py` | 796 | — not reviewed in depth this pass |

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

## 4. No CI backstop

There is no `.github/workflows/`. The only automated gate is the local
pre-push hook installed this session (pytest blocking, mypy advisory). That
hook only runs on this machine, only on `git push`, and is bypassable with
`--no-verify`. Given there's a second, concurrent agent (Codex) actively
committing to this same repo, a server-side check that runs regardless of
which machine or agent pushes is worth adding:

```yaml
# .github/workflows/ci.yml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: macos-latest   # matches local Python 3.14 .venv behavior
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }   # matches pyproject.toml's declared version
      - run: pip install -e .
      - run: PYTHONPATH=src:. python -m pytest tests/ -q
      - run: pip install ruff && ruff check src/ tests/
```

This is also the natural place to eventually pin down the
Python-3.14-venv-vs-3.11-pyproject.toml mismatch that currently makes mypy
advisory-only in the pre-push hook — CI running the *declared* interpreter
version would surface real version-specific type errors that the local venv
currently can't.

---

## 5. Untested new infrastructure from this session

`research_io.py` (the shared `utc_now`/`canonical_json`/`sha256_file`/
`atomic_write`/`identity_key` helpers extracted from esports.py and
international_baseball.py this session) has no dedicated test file — it's
only exercised indirectly through its two callers' test suites. A small
`tests/test_research_io.py` covering each helper directly (especially
`atomic_write`'s crash-safety guarantee and `canonical_json`'s
key-ordering/float-formatting stability, since other code hashes its output)
would close this gap cheaply.

---

## 6. Data-layer fragility: Excel as the ledger's system of record

`data/picks.xlsx` and `data/flat_picks.xlsx` (openpyxl-backed) are read and
rewritten on every relevant CLI/dashboard operation. This session's ledger
audit found real corruption from this shape of storage: blank scores on
forced-call rows, inconsistent odds-field precision, one sign error in
`research_pnl_units` — none caused by a single catastrophic bug, but by the
accumulated drift you get from a spreadsheet acting as a database (no
schema enforcement, no transactions, full-file rewrite on every append, easy
to hand-edit inconsistently). Two concurrent writers (this session and
Codex's) touching the same `.xlsx` files raises real corruption risk with no
locking.

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

| Gap | Why it matters | What exists already that this would build on |
|---|---|---|
| No push notifications | Dashboard is pull-only; the live WNBA "stale filled order-control" bug fixed this session was only caught because you happened to be looking at the dashboard. A local macOS notification (`osascript -e 'display notification'`) or a Slack webhook on new qualified pick / settlement / stale-order-state would catch these without requiring you to keep the tab open. | `_daily_pipeline_status()` (this session) already detects staleness; the settlement path already knows when a position closes |
| No CLV/edge-decay chart | `cli.py clv` already computes closing-line-value; it's never rendered. | The CLI command and its output format already exist — this is a rendering gap, not a data gap |
| No drawdown/exposure chart | `economic_gate.py` (this session) already computes `max_drawdown` and bootstrap CIs; it's backend-only. | Same — data exists, no dashboard tab consumes it |
| No BBO-capture health view | This session left an open question ("theres alot of games in npb and kbo and esports... future events tho") about live slate-discovery counts that was never resolved because there's no view showing captured-vs-discovered counts per sport/day. | `market_snapshots()`/`_pick_quote()` and the odds directories already contain everything needed to compute discovered-vs-captured deltas per sport per day |
| No export/report | No CSV/weekly-summary export for offline review outside the dashboard. | `performance()`/`bets_view()` already assemble the underlying rows |

---

## 8. Portfolio/meta-model layer (new model-layer ideas, not sport-specific)

`model_improvements.md` is exhaustive per-sport, but nothing in this repo
currently sits *above* individual sport models. Four ideas that don't require
new data sources — only combining evidence that's already collected:

1. **Uncertainty-aware staking.** Units currently appear to be fixed-size per
   pick (e.g. `0.50U`, per the WNBA bug this session). A fractional-Kelly
   sizing layer — stake scaled by `(model edge, model calibration confidence,
   current bankroll drawdown)` rather than a flat unit — would use
   `economic_gate.py`'s existing drawdown/CI machinery and each artifact's
   own calibration metrics, with no new provider integration required.
2. **Cross-market internal consistency check.** Within Polymarket itself, a
   game's moneyline, spread, and total markets imply related no-vig
   probabilities. A large mismatch between what moneyline-implied and
   spread-implied win probability say about the same game is itself a
   signal — either a data-quality problem (stale/mismatched snapshot) or a
   genuine mispricing worth a closer look. This is buildable entirely from
   BBO data already being captured; no new source needed.
3. **CLV-triggered health monitoring.** `clv` is already computed on demand;
   nothing currently watches it over time. A rolling check — "has realized
   CLV trended negative over the last N graded picks for this artifact?" —
   could flag automatic-review-needed rather than relying on someone running
   `clv` and reading it manually. This is the natural automated complement to
   the manual "Immediate repair order" review process `PROJECT_STATUS.md`
   currently asks a human to do by hand.
4. **Simple ensembling across sport families.** MLB/NBA/WNBA/NFL currently
   train fully independent artifacts. Nothing pools their calibration
   behavior even though they share a validation/reporting pipeline
   (`validation.py`). A shared meta-calibrator (e.g., one isotonic/Platt
   layer fit across all four sports' out-of-fold predictions) could correct
   for a systematic miscalibration mode common to the shared feature-
   engineering pipeline, separate from each sport's own coefficients.

None of these are sport-specific research (they don't touch
`model_improvements.md`'s per-league tables) and none require a new paid or
authenticated data source — they're purely about better use of evidence
already flowing through the ledger, BBO snapshots, and validation reports.

---

## 9. Explicitly out of scope, and why

- **Kalshi cross-venue arbitrage/best-execution.** `data_sources/kalshi.py`
  is a deliberate `KalshiDeferredError` stub: the account holder is outside
  the US and Kalshi requires US residency (CFTC-regulated). There is nothing
  to build here until that changes — noting it so it isn't mistaken for an
  unfinished integration.
- **Dashboard authentication.** The server binds to `127.0.0.1` only
  (`dashboard_server.py`, `ThreadingHTTPServer(("127.0.0.1", options.port), ...)`);
  it's a single-user local tool, so adding auth would be complexity with no
  corresponding threat model change.

---

## Suggested order

1. Fix or delete `/api/scan` (small, isolated, verified bug).
2. Delete or wire-in the 11 orphaned modules (removes false signal about
   what's actually active; low risk since nothing currently depends on them).
3. Add the GitHub Actions CI workflow (small, immediately valuable given the
   concurrent-agent situation).
4. Add `tests/test_research_io.py` (small, closes a gap from this session's
   own new code).
5. Split `cli.py` and add `tests/test_cli.py` (moderate effort, closes the
   single biggest test-coverage gap in the repo).
6. Split `dashboard_server.py` (moderate effort, most valuable done together
   with #1 since the route-table refactor is what would have caught it).
7. Dashboard product gaps (section 7) — pick whichever is most useful to you
   day-to-day; CLV/drawdown charts are the cheapest since the data already
   exists.
8. Portfolio/meta-model layer (section 8) — genuinely new modeling work,
   sequence behind the per-sport P0 items already queued in
   `model_improvements.md`.
9. SQLite ledger migration (section 6) — largest effort, highest integrity
   payoff; do this once the smaller fixes above stop competing for the same
   files.
