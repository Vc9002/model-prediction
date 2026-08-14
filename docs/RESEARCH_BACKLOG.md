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
