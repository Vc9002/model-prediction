# CLAUDE.md — Working Guidelines for This Project

This file is auto-loaded into every session. It holds only durable working
rules. Volatile status lives in `docs/PROJECT_STATUS.md`, the audit and
session history in `docs/DEBUG.md`, open items in `docs/ROADMAP.md`, and the
documentation index in `docs/INDEX.md`. When a dated note stops being a rule,
move it to `docs/DEBUG.md` instead of leaving it here.

## What this project is

A multi-sport quantitative prediction, research, ledger, and local-dashboard
system with Polymarket US CLOB integration. It is **both** of these, by
operator decision (2026-11-13), and this paragraph is the single place that
decision is recorded:

- **Paper trading is the operating mode.** Every scheduled run writes to
  simulated ledgers with full market fidelity: real Polymarket executable asks,
  Kelly/edge-scaled unit sizing (1.0U–2.0U), daily bankroll loss caps,
  mark-to-market settlement, and closing-line-value PnL accounting. The paper
  auto-buyer is the only thing that ever buys unattended.
- **A real-money execution path exists and stays gated.** Order submission to
  Polymarket (`portfolio/`, `polymarket_execute.py`, the dashboard BUY/SELL
  endpoint) runs only on an explicit per-request operator instruction with
  confirmation, never from the scheduler, and is held to real-money care:
  quote freshness, market state, game-not-started, idempotent audit.

All models are wired directly to the current champion per sport and market
(moneyline, spread, total, NRFI). There are no shadow-qualification hurdles
or quarantine holdouts in front of serving.

## Scope (operator directive 2026-11-13)

Production forecasts **NBA, NCAAF, NFL, MLB, WNBA, and NCAAB** only. Soccer,
tennis, the five esports titles, KBO, NPB, hockey, darts, Euroleague, and NBL
are switched off, not deleted:

- The switch is `daily_forecast_enabled: false` on the sport's entry in
  `config/model.yaml`. `config.is_sport_daily_enabled` is the one gate every
  daily forecast task consults (`cli/daily.py`), so re-enabling a sport is a
  one-line config change. `tests/test_focus_sports_config.py` pins the list.
- Their code, artifacts, and calibrators stay in place. Settlement of
  already-open picks and score ingestion continue so history stays
  continuous. `cli forecast --sport <x>` still works for a manual run.
- **Re-enabling esports, KBO, or NPB needs a backfill first.** Their Elo
  ratings refresh only inside the daily forecast block (14-day incremental
  merge), so while they are off the ratings and match history go stale. Run
  `cli esports-backfill` / `cli international-baseball-backfill` over the gap
  and re-validate before flipping the flag back on.
- Research tooling (ablations, champion/challenger harness, experiment
  registries, replay research, the rebuild dashboard panel) is tested under
  `tests/research/`, which the default `pytest` skips (`norecursedirs`). The
  shadow-rebuild tree (`src/model_prediction/rebuild/`, `tests/rebuild/`) is
  frozen: its Windows scheduled task `ModelPrediction-RebuildShadow` is
  disabled in place and its registration in
  `scripts/register_windows_tasks.ps1` is commented out. Research must never
  block or slow the daily run. Run those suites on demand:
  `pytest tests/research -o addopts='' -q`, `pytest tests/rebuild -o addopts='' -q`.

Widening scope again is an explicit operator decision, not a side effect of
fixing something in a disabled sport.

## Fast iteration (operator directive 2026-10-18)

- **No pytest for minor changes.** Refactors, dead-code cleanup, renames,
  formatting, comments, documentation: rely on `ruff check` only.
- **Never run the whole suite** (`pytest tests/`) unless Vincent explicitly
  asks. It takes minutes and stalls the loop.
- **Micro-targeted tests only.** When a pricing or model algorithm changes,
  run the single affected file with `-o addopts='' -q`.
- **Batch before verifying.** Finish all planned edits across files, then
  verify once.

## Pricing and odds format (operator directive 2026-11-07)

Never use American odds anywhere (not -110, not +120). Express every price,
quote, line, and break-even as a percentage / implied probability (52.4%) or
probability cents (52¢), in all user-facing tables, summaries, and reports.

## Handoffs

- Open work lives in one file: `docs/handoffs/HANDOFF.md`. Read it at session
  start if it exists. Overwrite it for new open work; never create a second.
- Delete it when every item is done. Keep it short: next action first, then
  facts with evidence (paths, PIDs, row counts). No secrets.

## Knowledge graph

A graphify graph of the repo lives at `graphify-out/` (graph.json,
GRAPH_REPORT.md, graph.html). For codebase questions (architecture, what calls
X), use `/graphify` first; the `graphify` MCP server (query_graph, get_node,
get_neighbors, shortest_path) is registered. Refresh with
`/graphify . --update` (code only) or a full `/graphify .`.

## Read these first, in this order

1. `docs/PROJECT_STATUS.md` — current operational status and release verdict.
2. `docs/DEBUG.md` — the audit history, every real bug with its trace, and the
   reproduction checklist. The most trustworthy record of what works now;
   trust it over older docs when they disagree.
3. `docs/ROADMAP.md` — the consolidated roadmap, promotion rules, verdict
   taxonomy, and what is still open. Add new open items here; do not
   re-create the planning docs it replaced.
4. `docs/AGENTS.md` — short execution rules (walk-forward only, never
   hardcode thresholds, protected files). Binding.
5. `docs/ARCHITECTURE.md` — the architecture contract (data flow, validation
   contract, invariants).
6. `docs/rebuild/*` — the clean-slate rebuild contract. Shadow-only and
   no-production-write are hard invariants. The tree is frozen (see Scope).

## The one invariant that matters most

**Point-in-time correctness.** A decision made at time `T` may only use
information with `observed_at_utc <= T`. This is by far the most common source
of real bugs here (weather timing, KBO/NPB home/away labels, MLB starter
selection, soccer team-name collisions, an MLB transaction-date ambiguity, a
KBO/NPB timestamp-ordering bug that silently zeroed every real pick for
months). When touching a live decision:

- Check every timestamp comparison's granularity. A date-only field compared
  against a full timestamp leaks: same-day is not "before".
- Check the order two `utc_now()` captures happen in when one feeds a slow
  live-data call and the other feeds `validate(now=)`. Capturing "now" before
  a slow call that timestamps internally is a real, easy-to-miss bug.
- A live-only signal (a current roster snapshot) can never serve a past
  decision time. A retroactively queryable signal (a transaction log with
  report dates) can, filtered on the record's own report timestamp, never on
  fetch time.

## The shadow-feature pattern

New predictive signals follow one shape (`features/mlb_player_availability.py`,
`features/player_availability.py`):

1. A data-source module that captures raw provider data with explicit
   `observed_at_utc` provenance.
2. A feature module that fails closed (`NO_CALL_*` reason codes): missing,
   stale, or ambiguous data means decline, never guess.
3. A dispatch branch in `learned_forward.py::_compute_features`, gated by its
   own feature-name constant. It stays inert until an artifact's
   `feature_names` lists it. Promotion into a model is a separate, explicit
   decision with real walk-forward validation.
4. Test against real live data before calling it done. Synthetic fixtures have
   repeatedly missed real bugs (wrong field names, status enums the API never
   returns).

## Before promoting anything into a live model

Match `docs/ARCHITECTURE.md`'s validation contract: 60/20/20 chronological
split, fit on train only, thresholds on validation only, locked holdout
untouched until the final check. Reuse `validation.py::build_walk_forward_rows`
/ `chronological_split`, `production_feature_ablation.py`, and
`roadmap_challenger.py` rather than hand-rolling splits. Report the delta with
real numbers (Brier, calibration, locked-holdout accuracy). A standalone
correlation is a reason to run the ablation, not a promotion decision.

Promotion is one command, never a hand-edited yaml:
`python -m model_prediction.model_promotion promote --new … --sport … --market
… --approved-by … [--evidence …]`; rollback likewise. Records land in the
`promotions` table of the runtime `runs.db`. Champion (what serves, the
`champions:` map) is not the same as primary (what the canary runs).

## Durable contracts

These came out of the 2026-08 consolidation; the dated narrative is in
`docs/DEBUG.md` (2026-11-13 entry). The rules still bind:

- **All mutable control-plane state resolves through `RuntimePaths`**
  (`runtime_paths.py`): `MODEL_PREDICTION_RUNTIME_ROOT` when set, repo
  `data/` otherwise. Operational entry points (supervisor, canary, promotion,
  system_health, dashboard, cli_production) use
  `resolve(require_external_runtime=True)` and fail closed on a missing root.
  Never hand-build a path to runs.db, production.db, or the supervisor logs.
- **`config/production.yaml` is schema v3**; `production_registry.py` is the
  single source of truth and fails each broken model closed. Keep
  `production_canary.py`'s public API (tests pin its messages).
- **Every scheduled run goes through `run_supervisor`** (workers: daily,
  production, rebuild-shadow). Exit 75 is a coalesced lock skip, never a
  failure. The daily job runs several times a day on purpose; that is safe
  because the forecast step replaces today's picks and settlement, ingestion,
  and the auto-buyer's bought-index are idempotent.
- **`ProductionPredictionStore` is the only writer to `production.db`.**
  Schema changes for legacy DBs go in `_migrate_columns`, not only `_SCHEMA`.
- **SQLite is the ledger authority** (`MODEL_PREDICTION_LEDGER_AUTHORITY=
  sqlite`; `ledgers/ledgers.db`). The xlsx workbooks are a best-effort export
  whose failure only warns. Do not flip authority back without a decision.
- **Rolling vs frozen artifacts.** `config/models/*.json` are frozen promoted
  artifacts; retrained ratings go to the runtime root's `models/`
  (`RuntimePaths.models_root`). Route new retrainable artifacts through that.
- **The `data/` trees are untracked operational output**; the SQLite ledger is
  the evidence store, `data/archive/` and `snapshots/` stay tracked. Do not
  re-add files under the ignored trees.
- **Historical JSONL is ingest-ordered, not event-ordered**: "newest event" is
  a max-scan, never the last line.
- **Train/serve parity is a tested invariant**
  (`tests/test_validation.py::test_train_serve_parity_for_v9_features`). Any
  new feature must hold `validation.py` and `learned_forward.py` in literal
  sync.
- **Stable seeds are load-bearing**: `simulate_game`'s default gamma-Poisson
  stream must stay bit-for-bit; the seed pin test pins history and is never
  updated to match new output.
- **MLB Main-ledger philosophy is "no gates, show everything, human
  decides."** Other sports are deliberately curated. Don't homogenize without
  asking.
- **v8's park-factor static-table leak is documented and v8 is never
  modified** (`docs/V8_REPRODUCTION.md`); v9 uses `park_factor_pit`.

## Testing conventions

- `ruff check src/ tests/` is clean and stays clean.
- `pytest` runs **without** the `MODEL_PREDICTION_*` env vars; several tests
  pin the no-env repo-colocated default.
- The default `pytest` is the core suite; `tests/research/` and
  `tests/rebuild/` run only when asked (see Scope).
- When you fix a bug, prove the regression test catches it: revert the fix,
  watch the test fail, restore the fix.
- Don't mock `utc_now()` to one fixed value when correctness depends on the
  order two timestamps are captured; use a clock that advances.

## Real-money and destructive actions

- Ledger mutation only through `archive_settled_rows` / `remove_open_rows`,
  never raw deletion. `allow_staked_removal=True` is for ledgers that can
  never hold real money (research/gated/flat), not Main.
- Dashboard order execution (BUY/SELL) gets the same care as any real-money
  path: quote freshness, market state, game-not-started.
- Config or model promotion is a governed decision. Never do it as a side
  effect of something else; confirm scope first if it was not asked for.
- The live `auto_buyer_state.json` overrides the code constants (whitelists,
  disabled sport-markets). Update both layers when changing buyer behaviour.

## Working with subagents

An agent in an isolated worktree sees only committed history, not this
session's uncommitted work. Commit first or point it at the shared checkout.
Verify surprising subagent findings against the current code before acting;
review agents here have reported already-fixed things as broken.

## Style

Comments explain *why*, not *what*: a hidden constraint, a workaround for a
specific real bug, a non-obvious invariant. Match the existing code.
