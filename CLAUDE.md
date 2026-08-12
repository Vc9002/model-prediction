# CLAUDE.md — Working Guidelines for This Project

## 2026-08-12 CONSOLIDATION — All rebuild models on main, production canary deployed

 () now contains ALL accepted code: WNBA rebuild v1 (2-feat LR), 
Tennis rebuild v1 (surface Elo, faithful baseline), NFL rebuild v1 (Platt-calibrated LR), 
Soccer rebuild v1 (Dixon-Coles Poisson, 19 leagues). All challenger artifacts in 
 — , RESEARCH_SHADOW only.

**Production canary**:  via . 
CLI: .
Dashboard: . Scheduler: .
Automated orders: . Manual only.

Daily pipeline verified (exit 0, Main absent). 4 stale worktrees removed, 
18 retained as historical references. See  and 
 for full details.


This file is auto-loaded into every session. Keep it short and durable —
volatile status belongs in `docs/PROJECT_STATUS.md`, full audit history and
reproduction commands in `DEBUG.md`, and per-sport feature roadmaps in
`docs/MODEL_IMPROVEMENTS.md`. This file is about *how to work here*, not
*what's currently true*.

## Knowledge graph

A graphify knowledge graph of this repo lives at `graphify-out/` (graph.json,
GRAPH_REPORT.md, graph.html — built 2026-08-13, entire repo). For codebase
questions (architecture, cross-file relations, "what calls X"), use `/graphify`
first — the `graphify` MCP server (query_graph, get_node, get_neighbors,
shortest_path) is also registered in Claude Code. Refresh with
`/graphify . --update` (code changes, no LLM) or a full `/graphify .`
(includes the expensive docs/papers semantic pass).

## What this project is

A shadow-first, multi-sport prediction/research/ledger/dashboard system with
Polymarket US integration. Most sports are research/shadow (zero real units,
logged for track record only); a small number (MLB moneyline, WNBA moneyline)
are promoted to produce real, sized `QUALIFIED_SHADOW_CALL` rows in the Main
ledger. Real-money order execution exists behind an explicit, separately
gated dashboard surface — treat anything touching it as high-stakes.

## Read these first, in this order

1. `docs/PROJECT_STATUS.md` — current operational status and release verdict.
2. `DEBUG.md` — full audit history, every real bug found/fixed with its trace,
   and a "Reproduction commands" checklist. This is the most-current, most-
   trustworthy record of what actually works right now — trust it over
   older docs when they disagree.
3. `docs/MODEL_IMPROVEMENTS.md` — the per-sport feature roadmap and research
   contract (promotion rules, point-in-time feature contract, what's been
   tried and rejected). Section 1 and section 12 carry a live "what's done
   vs. still open" status, not just a wishlist.
4. `docs/AGENTS.md` — short, durable execution rules (walk-forward only,
   never hardcode thresholds, protected files, etc.). Still binding.
5. `docs/ARCHITECTURE.md` — the durable architecture contract (data flow,
   validation contract, invariants). Doesn't change often; check it before
   assuming something about the pipeline shape.
6. `docs/rebuild/*` — the separate clean-slate rebuild contract. Read these
   before rebuild work; its shadow-only and no-production-write boundaries
   are hard invariants, not configurable operating modes.

## The one invariant that matters most

**Point-in-time correctness.** A decision made at time `T` may only use
information with `observed_at_utc <= T`. This is, by a wide margin, the
single most common source of real bugs found across every session on this
project — weather timing, KBO/NPB home/away labels, MLB starter selection,
soccer team-name collisions, and (this session) an MLB transaction-date
same-day ambiguity and a KBO/NPB timestamp-ordering bug that silently zeroed
every real pick for as long as it existed. When building or reviewing
anything that touches a live decision:

- Check every timestamp comparison's actual granularity. A `date`-only field
  (no time-of-day) being compared against a full timestamp is a common
  leak — same-day is not the same as "before."
- Check the *order* two `utc_now()`/timestamp captures happen in, when one
  feeds a slow live-data-building call and the other feeds a `validate(now=)`
  check. Capturing "now" before a slow call that does its own internal
  timestamping is a real, easy-to-miss bug class (see DEBUG.md's KBO/NPB
  entry — it silently zeroed every real pick, for months, with no error
  surfaced anywhere).
- A live-only signal (e.g. a current roster snapshot) can never be used for
  a decision time in the past. A retroactively-queryable signal (e.g. a
  transaction log with real report dates) can — but only by filtering on
  the record's own report timestamp, never on when your system happened to
  fetch it.

## The shadow-feature pattern

New predictive signals in this codebase follow a consistent shape (see
`features/mlb_player_availability.py` and `features/player_availability.py`
for two live examples):

1. Build a data-source module that captures/normalizes raw provider data
   with explicit `observed_at_utc` provenance.
2. Build a feature module with fail-closed error handling (`NO_CALL_*`
   reason codes) — missing/stale/ambiguous data means the feature declines
   to answer, never guesses.
3. Wire it into `learned_forward.py`'s generic `_compute_features` as its
   own dispatch branch, gated by its own feature-name constant. It stays
   **inert in live production** until some future artifact's own
   `feature_names` config explicitly lists those names — this is
   deliberate, not a placeholder to "finish later." Promoting a feature
   into an active model is a separate, explicit decision requiring real
   walk-forward validation, not a side effect of building it.
4. Test against real, live data before calling it done — synthetic
   fixtures alone have repeatedly missed real bugs this session (a wrong
   field name, a status enum that doesn't match what the real API
   actually returns, a schema assumption that was true in fixtures but
   false in production).

## Before promoting anything into a live model

Match `docs/ARCHITECTURE.md`'s validation contract: 60/20/20 chronological
split, fit on train only, select thresholds on validation only, locked
holdout untouched until the final check. Reuse the existing tooling —
`validation.py::build_walk_forward_rows`/`chronological_split`,
`production_feature_ablation.py`, `roadmap_challenger.py` — rather than
reimplementing chronological splitting or feature replay from scratch; it's
easy to accidentally leak future information into a hand-rolled backtest.
Report the delta with real numbers (Brier, calibration, locked-holdout
accuracy), not just "it correlates" — a promising standalone correlation
(see this session's `starter_era_gap` finding) is a reason to run the real
ablation, not a promotion decision by itself.

## Testing conventions

- `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` and
  `.venv/bin/ruff check src/ tests/` should both stay clean. Ruff baseline
  is ~117-118 pre-existing findings unrelated to any one session's work;
  don't chase them down, just don't add new ones.
- When you fix a bug, verify the regression test actually catches it:
  temporarily revert the fix, confirm the new test fails, then restore the
  fix. This has caught real "the test doesn't actually test anything"
  mistakes this session.
- Mocking `utc_now()` to one fixed value everywhere hides timestamp-
  ordering bugs (see the KBO/NPB incident) — when testing something whose
  correctness depends on *when* two timestamps are captured relative to
  each other, use a mock clock that actually advances between calls.

## Real-money and destructive actions

- Ledger mutation only through `archive_settled_rows`/`remove_open_rows` —
  never raw deletion. `allow_staked_removal=True` is reserved for ledgers
  that can never hold real money (research/gated/flat), not Main.
- MLB Main-ledger philosophy is "no gates, show everything, human decides."
  Esports/soccer/tennis Gated Research is deliberately curated/tightened.
  These are sport-specific and intentional — don't homogenize them without
  asking first.
- Dashboard order execution (BUY/SELL) requires the same care as any
  real-money code path: check quote freshness, market state, and
  game-not-started before touching it.
- Config/model promotion (`status: research` -> `shadow_qualified`, or a
  new artifact version becoming the active one) is a real decision with
  real governance requirements (see the Promotion rule in
  `docs/MODEL_IMPROVEMENTS.md` section 2). Don't do it as a side effect of
  something else; confirm scope first if it's not explicitly requested.

## Working with subagents on this codebase

If you delegate a review or research task to a subagent running in an
isolated worktree (`isolation: "worktree"`), remember it can only see
**committed** git history — not your own session's uncommitted working-
directory changes. If there's substantial uncommitted work, either commit
first or explicitly instruct the agent to read the shared checkout path
directly instead of its own worktree. This session had two review agents
run stale by several commits' worth of real, tested, verified work; one
caught it and adjusted, one didn't and reported several already-fixed
things as broken. Verify surprising or contradictory subagent findings
against the actual current code yourself before acting on them — this is
generally good practice, but especially so here given the fast commit
cadence.

## Style

Comments explain *why*, not *what* — a hidden constraint, a workaround for
a specific real bug, a non-obvious invariant. Don't write comments that
just restate the code. This project's own code is written this way
throughout; match it.

## 2026-08-13 — Champion/challenger + settled-picks + cleanup (this session)

**New module `src/model_prediction/champion_challenger.py`** — production
freeze + paired comparison harness. CLI: `freeze-production`,
`compare-champion`. Docs: `docs/CHAMPION_CHALLENGER.md`.

**MLB v9 Phase 1 features wired** — `starter_kbb_gap`, `residual_trend_gap`,
`bullpen_fatigue_gap` added to `validation.py` / `learned_forward.py` /
`features/starter_history.py`. Runner: `scripts/mlb_v9_ablation.py`.
Ablation result: residual-trend variant wins (+56.4u vs +43.1u raw trend).

**MLB v9 Phase 2** — `park_factor_at()` PIT-correct park factors added
(`features/park_factors.py`), wired into `validation.py` walk-forward.

**WNBA spread fix** — `wnba-spread-baseline-v1` was predicting moneyline not
spread (never used the line). Replaced with `wnba-spread-margin-v1`
(margin_normal, `P(away_cover)=Φ(line; margin, 10.5)`). Broken artifact
archived. `config/model.yaml` spread/total refs corrected.

**Cleanup** — 38 files removed (12 `*.previous.json`, 22 obsolete configs, 4
dead rebuild models). Config root 63 → 27 files.

**Known gap** — canonical `data/model_ledgers/` froze 2026-08-03 (retired main
ledger was its only writer). Dashboard + settled-picks loader read stale data.
See `docs/SETTLEMENT_GAP.md`. Do NOT silently re-route production data paths
without an explicit decision.

**Settlement routing fix** — model-ledger mirror now writes to canonical
`data/model_ledgers/` (threaded `model_ledgers_dir` through `PickLedger`,
`main_ledgers.py`, `research_ledgers.py`). Previously each tier mirrored to
its own subdir (`data/flat/model_ledgers/`, …) while the dashboard + loader
read only canonical — that froze on 2026-08-03. See `docs/SETTLEMENT_GAP.md`.

**MLB distribution methods** — `simulate_game` now takes a `method` argument
(`gamma_poisson` default / `negative_binomial` / `independent_poisson`);
`compare_distribution_methods()` prices moneyline/spread/total from one
coherent joint draw per method. Wired through `MeasuredEdgeMarginModel` /
`MeasuredEdgeTotalsModel.predict(..., method=...)`. NB is the first serious
challenger to the incumbent gamma-Poisson; it is runnable but not yet
promoted. Tests: `tests/test_mlb_distribution_methods.py`.

## 2026-08-13 (later) — deep-audit fix pass (F-72 → F-84)

Full audit run against this tree; every finding fixed with regression tests.
Details in `MASTER.md`'s 2026-08-13 session entry and
`docs/PROJECT_STATUS.md`'s "2026-08-13 deep-audit fix pass". Things that
change how you should work here:

- **Main ledger is un-retired** (operator directive): `main_ledger_enabled:
  true`, workbooks restored to `data/main/`. Phase B's `retired` mechanism
  still exists; don't re-flip without a directive.
- **Train/serve parity is now a testable invariant** —
  `tests/test_validation.py::test_train_serve_parity_for_v9_features` proves
  `validation.py` (training) and `learned_forward.py` (serving) compute
  identical values per feature. Any new feature must hold both sides in
  literal sync — the 2026-08-13 audit found three v9 features where they
  silently diverged (F-79). v9 variants use the new `park_factor_pit`
  feature name; `park_factor` stays the static table for v8's trained
  contract. **Prior v9 ablation numbers are void — re-run
  `scripts/mlb_v9_ablation.py` before trusting them.**
- **Stable seeds are load-bearing**: `simulate_game`'s default
  `gamma_poisson` stream must stay bit-for-bit across refactors; the seed
  pin test in `test_mlb_distribution_methods.py` must never be updated to
  "match new output" — it pins history (F-80).
- **ProductionLedger is live**: every `cli_production predict` writes
  `data/production/predictions.db` fail-soft; lifecycle transitions are
  guarded open→terminal only (settle/void/supersede/error). Operator
  commands are fail-LOUD, scheduler path fail-soft.
- **`pytest` runs WITHOUT the MODEL_PREDICTION_* env vars** (documented safe
  form) — several tests pin the no-env repo-colocated default; setting the
  launchd env vars makes ~12 of them red by retargeting them at the live
  runtime root.
- **Still open (explicit operator action needed)**: loading the
  `com.modelprediction.production` and `com.modelprediction.rebuild-shadow`
  launchd agents (plists installed, never loaded — canary predictions and
  rebuild shadow.db frozen since 08-11); regenerating
  `outputs/rebuild/verification.json` (gitignored CI evidence; rebuild
  status shows degraded while absent); the v8 park-factor 2026-table leak
  (needs a refit under v8's contract — v9 is clean via `park_factor_pit`).

