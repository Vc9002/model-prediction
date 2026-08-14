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

## P2

- **Soccer v2** — Dixon-Coles family: home advantage, rho, league
  baselines, attack/defense strength, time decay, shrinkage; multiclass
  calibration (temperature/Dirichlet) on the 3-way vector — never
  calibrate HOME/DRAW/AWAY independently.
- **Esports v7** — title by title, calibration first, then per-title
  context (CS2 map pool/roster/LAN; LoL patch/roster/region/side;
  Valorant map/roster/patch; Dota roster/patch/draft). No generic
  esports feature model.
- **KBO/NPB** — Elo + starting pitcher first; then bullpen, lineup,
  park, weather; long term a score model producing P(home)/P(tie)/P(away)
  instead of a forced binary structure.

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
