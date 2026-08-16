# Research backlog (post-consolidation)

After consolidation the project is a repeatable model-research and
production-improvement system. Research is queued, not ad hoc: every
experiment has a hypothesis, owner, dataset, candidate ID, status,
result, and verdict — recorded in the experiment registry
(`python -m model_prediction.experiment_registry`).

**Gate**: promotion work starts only after `docs/BURN_IN.md` passes.

## P0 — MLB v9 (first full test of the new system)

Isolated feature ablations on ONE frozen feature table
(`python -m model_prediction.feature_freezer freeze --sport mlb`), all
sharing the same events, timestamps, labels, folds, and evaluation code:

A. v8 reproduction (control)
B. FIP
C. K-BB%
D. FIP + K-BB%
E. no trend
F. residualized trend
G. bullpen talent
H. bullpen availability
I. PIT park
J. PIT weather
K. projected lineup
L. confirmed lineup

Then: best-feature LR vs best-feature XGBoost, compared directly.
Then: MLB run-distribution model (μ_home, μ_away, dispersion; Poisson /
Negative Binomial / Poisson-lognormal; one coherent distribution feeding
ML + run lines + totals — MLB totals are the weakest structural piece).
Then: the first true promotion process — OOF → same-event → bootstrap →
freeze → prospective shadow → settled comparison → PROMOTION_CANDIDATE →
operator decides. `mlb-v8` stays rollback; `mlb-v9` promotes only if it
wins. A loss is still a successful research result.

## P1

- **WNBA v5** — Elo + trend challenger vs v4 (evidence says
  `defensive_trend_gap` was harmful). Audit spread sign / home-away
  orientation / push handling / settlement grading before spread work.
  Long term: possessions × PPP → score distribution → ML/spread/total.
- **NFL calibration** — incumbent OOF probs: Identity / Platt /
  Temperature / Isotonic. Calibration first; QB/EPA/CPOE/OL/injuries/
  weather only after, and only when PIT-safe data exists.
- **Tennis v2** — challenge the fixed 60/40 surface weighting with
  `w_max × n_surface/(n_surface + c)`; then K factor, inactivity, surface
  sample size, tournament level, retirement handling. Keep v1 until v2
  clearly wins.

## Revised post-burn-in order (operator, 2026-08-14)

1 freeze SHA → 2 freeze MLB v8 benchmark → 3 fix MLB reproduction →
4 MLB v9 starter/FIP/K-BB → 5 bullpen → 6 PIT park/weather → 7 lineup →
8 LR vs XGB → 9 calibration → 10 joint run distribution → 11 totals/
spread → 12 prospective v9 → 13 WNBA v5 → 14 WNBA possession/PPP →
15 NFL calibration → 16 Tennis v2 → 17 soccer framework split →
18-24 league models (EPL/La Liga/Bundesliga/Serie A/MLS/UCL first,
rest as sample permits) → 25-29 esports v7 per title (CS2, Valorant,
LoL, Dota2, R6) → 30 esports region heads → 31-33 KBO/NPB starter +
run distributions → 34 continuous evaluation.

The binding rule: **shared infrastructure ≠ shared model.** Share CV
code, calibration evaluator, artifact schema, experiment registry,
bootstrap, feature-store contracts, PIT checks — never predictive
assumptions across different sports/games.

## P2 — Soccer, split by league (operator directive 2026-08-14)

**The incumbent `soccer-poisson-dc-v1` with one global HOME_GOAL_BOOST /
DC_RHO is retired as a research target.** The math engine can be shared;
the fitted state must not be:

```text
soccer/
├── core/           (poisson, dixon_coles, calibration, distributions)
├── leagues/        (epl.py, la_liga.py, bundesliga.py, serie_a.py,
│                    mls.py, ucl.py, ...)
└── registry.py
```

Each league gets an independently fitted artifact
(`soccer-epl-poisson-dc-v2`, `soccer-la-liga-poisson-dc-v2`, ...) with
its own goal baseline, home advantage, rho, time-decay rate,
attack/defense strengths, shrinkage, dispersion, calibrator. No
universal constants. Thin leagues use hierarchical shrinkage toward a
global soccer prior (`theta = w*theta_league + (1-w)*theta_global`,
w from sample size). UCL gets a later challenger (domestic rating +
cross-league coefficient + UCL environment), not part of the first
split. Data must never cross leagues silently (every record carries
competition_id + season_id); evaluation and promotion are per league
(add Ranked Probability Score for 1X2; aggregate soccer metrics only
as a weighted summary). Registry gains a `competition` dimension —
champion identity becomes sport + competition + market — as a MODEL
phase infrastructure extension, not during consolidation.

## P2 — Esports, split by title (operator directive 2026-08-14)

No generic `esports-v7`. The shared `esports.py` framework is replaced
with title-specific packages (common/ holds only genuinely universal
utilities — identity, chronology, calibration, evaluation, rating):

```text
esports/{cs2,valorant,lol,dota2,rainbow_six}/{features,model,training,predictor}.py
```

Title-specific feature sets (CS2: map Elo/map pool/roster/LAN/tier/
BO format; Valorant: patch/agent-meta/attack-defense side strength;
LoL: region/patch/blue-red side/objectives, pre- vs post-draft horizons;
Dota: Radiant/Dire/draft/hero pool; R6: map-specific Elo/attack-defense),
independent hyperparameters (K, inactivity/recency decay, calibrator —
nothing inherited between titles), and a hard identity invariant:
(game_title, provider, provider_team_id/player_id) — Cloud9 CS2 vs
Cloud9 LoL are distinct entities; an organization name is metadata.
Region/competition specialization comes second (global title prior +
region-specific parameters). Model IDs: `cs2-series-v7-lr`,
`valorant-series-v7-lr`, etc.

## P2 — KBO/NPB

Elo + starting pitcher first; then bullpen, lineup, park, weather; long
term a score model producing P(home)/P(tie)/P(away) instead of a forced
binary structure.

## P3

New experimental features as they become PIT-safe.

## Cross-cutting (after probability quality is stable)

- **Economic evaluator** — one standard market evaluator (model prob ×
  market ask, fees, slippage, depth, quote age → net EV), separate from
  the sports models. Every candidate reports proper-score improvement AND
  economic improvement independently; a profitable-looking backtest never
  rescues a poorly calibrated model.
- **Continuous prospective evaluation** — rolling reports per model and
  challenger (30d / 100 events / season / all): LogLoss, Brier, ECE,
  accuracy, coverage, CLV and ROI where valid; drift alerts (calibration
  drift, missing-feature increase, provider coverage drop, prediction
  distribution shift, model-vs-market divergence).
- **Developer ergonomics LAST** — split cli.py, dashboard modules, CLI
  polish, experiment UI, comparison dashboard, artifact browser,
  automated reports. After the science is reliable, not before.

## Experiment template

```
hypothesis:  FIP predicts run prevention better than ERA, MLB v9
owner:       <operator>
dataset:     pit_mlb.jsonl  (dataset_hash from the freeze manifest)
candidate:   mlb-elo-trend-lr-v9-fip
incumbent:   mlb-elo-trend-lr-v8
status:      queued | running | completed | void
result:      OOF Brier delta, units delta
verdict:     promote | retain | reject
```

`python -m model_prediction.experiment_registry record --model-id ...`
registers every run; invalidated results become `void` with a reason.

## 2026-08-17 brainstorm session (data/structure ideas — queued, not scheduled)

Recorded for future ordering decisions; none of these has an owner or
start date. Honest scoping notes included so the queue stays triageable.

- **`statsmodels` for residual diagnostics** — scikit-learn/xgboost give
  point predictions but nothing for time-series/GLM diagnostics on
  calibration residuals (autocorrelation, heteroskedasticity). Low
  effort to add as a dev-only dependency; useful before the calibration
  work (P1/P0 step 9), not urgent before.
- **`pyarrow`/parquet caching for `game_snapshots.jsonl`** —
  `load_starter_index` re-reads and re-parses the whole JSONL on every
  cold process start (observed 2026-08-16/17 while running feature
  backtests). A parquet cache (or feather) with mtime-based invalidation
  would speed every cold-start feature read. Low risk, moderate effort.
- **Declarative schema-validation layer at ingestion** — the codebase
  hand-rolls a lot of "fail closed on bad data" logic per provider; a
  data-contract layer (pydantic is already a dependency; a thin validator
  over ingested JSONL records is enough — no need for a heavyweight
  framework) could consolidate it. Architectural change; needs care not
  to duplicate the existing fail-closed behavior it would replace.
- **Batter-level lineup features** — `game_snapshots.jsonl` carries full
  box-score player data but only `pitcher_order[0]` (and partially the
  bullpen) is consumed; batter-level data is untapped for lineup-strength
  signals. Depends on P0 step K/L ordering (projected vs confirmed
  lineup); don't start before the PIT-safe lineup work defines its source
  contract.
- **Wind-direction × park-orientation** — `weather` is captured per game
  but the model flattens it to one scalar `weather_factor`; wind direction
  relative to park orientation is a real, known park-specific effect. Data
  is already collected; needs a per-park orientation table + validation.
  Fits naturally inside P0 step J (PIT weather).
- **Line-movement features** — shadow module built 2026-08-16/17
  (`features/line_movement.py`, inert, not wired). First backtest on 104
  real settled v7/v8 MLB moneyline picks: movement toward the picked side
  won 55.3% vs 50.0% away/flat; point-biserial r ≈ 0.08 (n=104). Weak,
  directionally consistent, nowhere near promotable. Revisit when the
  settled-pick sample is several times larger, or run as a real ablation
  with the frozen feature table.
- **Opponent-quality (SOS) adjustment for pitcher rolling stats** — the
  rolling ERA/FIP/K-BB% features average raw starts with no adjustment
  for opponent strength or park context. Standard sabermetric gap; worth
  revisiting after the v9 starter-feature ablations (P0 A–D) land, since
  SOS interacts with every one of them.
- **Gap-flagging for starter windows** — a start from >90 days ago is
  blended into "last 5 starts" as if equally recent (the 2026-08-16
  Tidwell case: 2 starts from 2025-05/06 + 2 from 2026-08). Shadow
  variant `starter_era_gap_recency_gated` built 2026-08-16; backtest on
  10 diverged real settled picks showed no clear win (70% win rate on
  diverged, but n=10 and mixed sign-flips). Not rejected, not promoted —
  needs the full walk-forward ablation (same gate as any feature) if
  revisited.
- **Shared cross-sport rest/travel module** — Elo/trend infrastructure is
  duplicated per sport (soccer, esports, NFL, tennis); a shared
  "days-since-last-game / travel distance / rest disparity" feature
  module could serve all of them. High effort (touches 4+ pipelines),
  good payoff only after the per-sport work stabilizes.
- **ESPN → MLB Stats API player_id crosswalk** — no ready-made bridge
  exists (pybaseball covers mlbam/bbref/retrosheet/FG, not ESPN). ESPN's
  athlete id is now captured prospectively in
  `mlb_probable_starters.jsonl` (2026-08-16) so a future crosswalk
  doesn't need historical re-fetching. Remaining name-based misses are
  the 6 known-unmatchable starters (Cole Winn, Yohan Ramirez, Edgardo
  Henriquez, Eddy Yean, Thomas Pannone, Caleb Ferguson — in the MLB Stats
  API data under different names or not at all, verified 2026-08-16).

## 2026-08-17 brainstorm #2 (portfolio, data already collected, ops hardening)

Second pass — focused on risk/portfolio structure, signals whose raw data
the system ALREADY captures but never uses, and operational hardening.
None scheduled; same triage contract as the first pass.

### Risk & portfolio (real-money adjacent — highest impact per unit effort)

- **Correlation-aware exposure sizing.** Every pick payload already carries
  `correlation_tags` (`same_event`, `same_team_same_day`,
  `same_league_same_day`) but sizing treats each pick independently. Use the
  tags for portfolio-level caps: correlated picks share one exposure bucket
  so a same-game ML+spread+total call can't silently triple the stake the
  per-pick math approved. Natural home: the exposure check that already
  runs under `lock_exclusive` in the daily writer.
- **Formal bankroll re-scaling policy.** When units get re-sized after
  win/loss streaks is currently an operator moment, not a rule. Specify the
  re-scaling trigger and cadence (e.g. review every N settled picks, move
  only in small steps, never mid-slump) so streaks can't produce
  emotional or drift-driven sizing changes.
- **Sequential promotion testing (SPRT).** Replace fixed-sample promotion
  gates with a pre-specified sequential test: promote earlier when a
  challenger is decisively ahead, keep running longer when it's ambiguous,
  with the stopping rule registered BEFORE the run starts (anti-threshold-
  fishing). Fits directly on the existing experiment registry.
- **Pre-registered experiment thresholds.** Extend the experiment template
  with a `registered_threshold` field: hypothesis + success criteria must
  be recorded before the ablation runs, not after seeing the OOF deltas.
  Cheap discipline, directly counters the "run it until it looks good"
  failure mode the registry exists to prevent.

### Signals whose data is already being captured (near-zero acquisition cost)

- **Umpire over/under factors.** `game_snapshots.jsonl` already records
  `officials` per game (home-plate umpire included). Umpire-specific
  strike-zone tendencies are a classic, well-documented totals edge; a
  per-umpire historical over/under + strike-call rate table is buildable
  from data we already have. Fits inside P0 totals work.
- **Altitude / park elevation.** `venue_name` is captured per game; park
  factors exist for v9. Coors Field alone is a multi-tenths-of-a-run effect
  on totals that a static park factor may not fully separate from general
  park effects — an elevation term is a small addition to the v9 park work.
- **Starter velocity/spin trend.** The rebuild track already has a Statcast
  provider; incumbent side doesn't use pitch-level data at all. A
  declining-velocity trend across a starter's last 3 outings is a known
  fatigue/injury proxy — stronger and more PIT-safe than most box-score
  aggregates.
- **Sharp-book lead/lag signal.** The Odds API already aggregates multiple
  books; treating Pinnacle (or the sharpest available book) as a reference
  price and measuring Polymarket's lag behind sharp moves gives a
  lead/lag feature for execution timing — when the sharp line moves, delay
  or skip the order until Polymarket catches up, or size accordingly.
- **Market-as-prior shrinkage.** Blend the model probability with the
  market-implied probability with a shrinkage weight learned out-of-fold
  (Bayesian shrinkage toward the market consensus). Standard in sports
  modeling; near-free to evaluate as a calibration-layer variant given the
  existing calibration pipeline.

### Operational hardening

- **Runtime-root backup + offsite copy.** The canonical sqlite ledgers and
  audit chain are real-money evidence living on one machine. Nightly
  snapshot (sqlite backup API or wal checkpoint + copy) to an offsite
  target is the single most important piece of boring infra not yet
  present.
- **Push alerting on evidence states.** `system_health` computes
  evidence-based DOWN/DEGRADED/HEALTHY states, but discovering them is
  pull-only (dashboard visit). A push channel (Telegram/webhook) on
  DEGRADED transitions, stale quotes before a slate, and missed cycles
  closes the loop for a system that runs unattended 20+ hours a day.
- **Paper-trading rehearsal of the execution path.** The order-readiness
  gate and execution tickets are tested in CI, but the live dashboard
  order path gets exercised rarely (manual-orders-only). A scheduled
  rehearsal that walks every step up to (but not including) submission
  against live quotes keeps the real-money path warm and detects bit-rot
  between operator sessions.
- **Hypothesis stateful testing of ledger APIs.** Hypothesis is now a dev
  dependency; the next step beyond property tests is stateful testing of
  the ledger mutation surface (create → settle → void sequences against
  chain invariants), which example-based tests can't exhaust.
- **Systematic post-loss review workflow.** Payloads already carry
  `loss_cause` / `loss_classification` on settled rows. Wire a review
  trigger: every settled loss cluster gets classified variance-vs-signal
  from those fields, and consecutive signal losses raise an operator
  review item instead of silently accumulating.

### Explainability & operator UX

- **Per-pick feature-contribution panel.** Decision payloads store every
  feature value and the models are linear/logistic — a dashboard panel
  showing feature × coefficient per pick ("why did the model say 62%")
  is nearly free to build from existing payload data, and makes
  operator-side judgment (like the 2026-08-16 Tidwell discussion) far
  easier to have.
