# Model Card: `soccer-poisson-dc-v1`

Audited 2026-08-11 against branch `audit/model-feature-reconciliation-v1`
(based on `origin/main` @ `826c89342bd2f3f1ea44fc29eaf20fad520dc5d5`).

See `docs/model_audit/features/SOCCER.md` for the full per-constant/
per-market audit this card summarizes.

## Why it exists

`SoccerModel` (`src/model_prediction/models/soccer.py`) is a coherent
independent-Poisson goal-distribution model with a Dixon-Coles-style
low-score correction. It already derives all three of soccer's markets
(moneyline/1X2, O/U 2.5 total, BTTS) from **one shared score matrix** —
architecturally, this is one of the least-worth-replacing models in the
project, and this audit's conclusion is to retain and refine it, not rebuild
it. It supersedes an older, structurally-incompatible binary model
(`soccer-elo-trend-lr-v2`, `config/model.yaml:282-283`,
`legacy_binary_research_version`) which — per `soccer_forward.py:3-4`'s
module docstring — "cannot represent draws" at all, a real architectural
limitation the Poisson-DC model fixes by construction.

## Market(s) predicted

Three, all from one `predict_games` call
(`models/soccer.py:125-209`):
1. **Moneyline (1X2)** — home/draw/away, `market_type: "moneyline"`.
2. **Total** — O/U 2.5 goals, `market_type: "total"`, `line: 2.5`.
3. **BTTS** (both teams to score) — yes/no, `market_type: "btts"`.

Per `config/model.yaml:305-306`, only `full_game_total_2_5` is listed as an
active market for one config section, but `soccer_forward.py:270-296` prices
and logs all three from the same slate build. Coverage: 19 configured
leagues (`config/model.yaml:286-306`: EPL, La Liga, Bundesliga, Serie A,
MLS, UCL, Brasileirao, several South American leagues, friendlies, etc.).

## Feature set

Not a discrete feature vector — a shared, learned goal-rate model. Inputs:

- **Per-team EWMA attack/defense strength**, half-life 10 games, shrinkage
  prior 8 games, shrunk toward a global baseline (`models/soccer.py:86-106`).
- **Global league baseline** — mean goals-per-team across **all pooled
  history from all 19 configured leagues/competitions** (confirmed: no
  per-league filtering anywhere in the call chain — see feature doc's
  "competition pooling" finding).
- **`HOME_GOAL_BOOST = 1.15`** — global home-advantage multiplier, applied
  uniformly across all competitions.
- **`DC_RHO = -0.10`** — Dixon-Coles low-score dependence correction,
  applied to the four lowest-scoring matrix cells only.
- **`BTTS_CALIBRATION_INTERCEPT = 0.1393` / `BTTS_CALIBRATION_SLOPE =
  0.4205`** — Platt-scale correction, BTTS only.

Full per-constant audit (formula, fitted-vs-hardcoded status, verdict) is in
`docs/model_audit/features/SOCCER.md`. Headline finding: **`HOME_GOAL_BOOST`
and `DC_RHO` are both hardcoded literals with no fitting evidence anywhere
in this repo** (grep-confirmed: neither name appears outside
`models/soccer.py` itself) — both are in textbook-plausible ranges (soccer
home advantage, and Dixon-Coles' own published rho range) but have not been
fit against this project's own data via MLE or grid search. The EWMA
half-life/prior (10/8) are similarly hardcoded and notably diverge from the
project's own shared `TrendEngine` defaults (3/10/25 half-lives, prior=12)
used elsewhere — soccer's Poisson model has its own separate, unreconciled
set of constants.

## Training/fitting method

No coefficient-fitting in the ML sense (Poisson rates + EWMA, not a
regression) except for BTTS's Platt calibration (real logistic-regression
fit, validation cohort, checked on locked holdout — see below). Everything
else is either a closed-form EWMA computation over PIT-filtered history, or
a hardcoded structural constant.

**Qualification** (the walk-forward validation that *is* real and rigorous)
uses the project's standard 60/20/20 chronological split, separately for
each market:

- `qualify_soccer_poisson_model` (`validation.py:1252-1378`) — moneyline,
  `minimum_history_games=200`, confidence = 3-way argmax probability.
- `qualify_soccer_total_model` (`validation.py:1381-1480`) — total,
  confidence = `abs(p_over - 0.5)`, explicitly noted as "the qualification
  that actually matters" since totals is soccer's market with real
  Polymarket depth.
- No separate `qualify_soccer_btts_model` function exists — consistent with
  BTTS being research-only and never priced against a live market (see
  below).

Both instantiate the real `SoccerModel` and walk day-by-day with the same
snapshot-then-append PIT discipline used project-wide.

## Threshold selection

`config/model.yaml:253-286` (SOCCER block): `min_edge: 0.05`,
`research_confidence_gate: 0.230157` — per the adjacent
`qualification_override_reason` comment, this is the real learned threshold
from `qualify_soccer_total_model`'s 2026-08-03 run (65% target hit rate on
validation, graded on locked holdout), wired in by explicit operator
approval — not an arbitrary number. `MINIMUM_TEAM_GAMES = 10` (`cli.py:1635,
1856`) additionally gates `model_inputs_valid` on `min_team_games` from
`feature_basis`.

## Historical results

Real, walk-forward, locked-holdout numbers (from `config/model.yaml`'s
`qualification_override_reason` and `DEBUG.md:1929-1936`):

- **Moneyline**: 62.5% hit rate, +90.4 units, every month positive
  (`qualify_soccer_poisson_model`). Real settled Research picks: 61.5% win
  rate (8-5, n=13) — closely matches the backtest.
- **Total (2.5 goals)**: 66.7% hit rate on 162 locked-holdout calls, +44.2
  units at -110, every qualifying month positive
  (`qualify_soccer_total_model`, config comment). This is the market with
  real production sizing weight.
- **BTTS**: not hit-rate qualified the same way — raw accuracy 55.0% on the
  validation cohort, improved to 56.7% on locked holdout after Platt
  calibration (see below). Never priced against a live market (see
  "known defects").

## Calibration diagnostics (per market — audited separately, as instructed)

- **BTTS**: real, fitted, holdout-verified. Platt scaling
  (`_apply_btts_calibration`, `models/soccer.py:52-59`), fit on the
  validation cohort, checked on a disjoint locked holdout: accuracy 55.0% →
  56.7%; reliability buckets close to the diagonal (55.3% predicted vs.
  55.96% actual; 62.2% vs. 65.95%). This is the **only** market in this
  model with an actual applied probability calibration.
- **1X2 (moneyline)**: **none.** `qualify_soccer_poisson_model` grades
  using raw argmax probability as "confidence" and a learned selective-
  calling threshold — that is a decision threshold, not a probability
  recalibration. No binarized (draw-vs-not / away-vs-not) or proper
  multiclass calibration of any kind exists in this codebase for 1X2. The
  project's generic calibration tooling
  (`rebuild/calibration.py::calibration_intercept_slope`/
  `cross_fit_calibration_eval`) is fully built but has zero call sites
  anywhere in the repo (verified by grep) — it has simply never been run,
  for any sport, including soccer.
- **Total**: **none**, same gap as 1X2 — raw `p_over`, thresholded on
  `abs(p_over - 0.5)`, no calibration transform. This is the market with
  the most real market depth and sizing exposure, making the gap most
  consequential here.

**Direct answer to the audit task's question**: soccer does *not* use a
single calibrator across all derived markets — BTTS has a real one; 1X2 and
total have none at all (not even a binarized one). This is a genuine,
previously-undocumented gap, not a misconception to correct — the model
qualifies and produces real units without it, but the raw home/draw/away
and over/under probabilities used for edge-vs-market-price sizing have
never been checked for calibration quality.

## Known defects

- **No calibration for 1X2 or total** (above) — the most consequential
  finding of this audit.
- **`HOME_GOAL_BOOST`/`DC_RHO` are unfit hardcoded constants** — plausible,
  correctly-signed, textbook-range, but not derived from this project's
  data.
- **Competition pooling** — one global baseline/home-advantage across 19
  leagues with real scoring-rate and home-advantage heterogeneity (EPL vs.
  MLS vs. Brasileirao vs. continental cup fixtures), confirmed in code, not
  previously flagged in `DEBUG.md`/`docs/`.
- **Unbounded history window** — no rolling-window cutoff, only PIT upper
  bound + EWMA decay; a design choice, untested against alternatives.
- **`"soccer_form"` docstring name doesn't exist** — harmless, but the
  model's own module docstring (`models/soccer.py:4`) names a feature that
  isn't a registered feature anywhere in `features/`.
- **BTTS is calibrated but unpriceable** — Polymarket has never listed a
  BTTS market as of repeated live checks (`soccer_forward.py:116-127`,
  checked 2026-07-25 through 07-30); every BTTS prediction reaches
  `unmatched`. The calibration work is real and correct; it currently has
  no market to act on.
- **Team-ban enforcement gap** (ops-layer, not model math) —
  `DEBUG.md:306-330` documents this never got built for soccer (or
  esports/KBO/NPB/tennis); noted for completeness.
- **Draw settlement bug (already fixed)** — `DEBUG.md:5-53` (2026-08-02)
  documents and confirms the fix for a real settlement-layer bug where
  soccer moneyline draws were incorrectly graded PUSH instead of LOSS; not
  a live defect as of this audit, included for context since it directly
  affects the moneyline market's real-money accounting.

## PIT-safety

Confirmed safe. `store.games_before("soccer", game_date)`
(`features/base.py:188-198`) applies the project's standard
midnight-ET-at-start-of-date cutoff; both qualification and live serving
route through it (or the equivalent day-bucketed walk-forward loop in
`validation.py`). No feature reads anything not strictly prior to the
target date.

## Train/serve parity

Confirmed. `qualify_soccer_poisson_model`/`qualify_soccer_total_model`
(`validation.py`) and `build_soccer_total_slate` (`soccer_forward.py`) all
instantiate the real `SoccerModel` and call its real `predict_games` — no
reimplementation of the Poisson/DC math exists anywhere else in the
codebase.

## Artifact reproducibility

**Weak, same gap as tennis.** No versioned JSON artifact exists for
`soccer-poisson-dc-v1` under `config/models/` — `config/model.yaml`'s own
`qualification_override_reason` states this explicitly: *"no artifact file
with a qualified/qualified_for_betting field exists for it at all, unlike
every other shadow_qualified league."* The model was promoted by explicit
operator override (`qualification_override: true`), and the only durable
record of the real qualification numbers (62.5%/66.7% hit rates, +90.4u/
+44.2u) is a YAML prose comment, not a structured, machine-checkable
artifact. `model_code_hash` (`soccer_forward.py:547-548`,
`hashlib.sha256` of `models/soccer.py`) provides per-request code
provenance but nothing re-runs qualification automatically on a code
change — a silent tweak to `HOME_GOAL_BOOST`/`DC_RHO` would ship without a
fresh qualification check unless a human remembers to run one.

## What to retain

- The shared-architecture design itself — one score matrix deriving all
  three markets is exactly right and should not be abandoned for
  per-market model families.
- BTTS's Platt-scaling approach as a template for how 1X2/total calibration
  *should* be done.
- The EWMA attack/defense engine, Dixon-Coles low-score correction
  mechanism (the math, independently confirmed correct), and the
  moneyline/total walk-forward qualification methodology.

## What to change

- Add real calibration diagnostics for 1X2 and total (the biggest gap found
  in this audit) — reuse `rebuild/calibration.py`'s existing, unused
  tooling rather than building new.
- Run a real MLE/grid-search fit for `HOME_GOAL_BOOST` and `DC_RHO` against
  this project's own data instead of relying on textbook-plausible
  defaults.
- Reconcile the EWMA half-life/prior (10/8) against or with the shared
  `TrendEngine` constants (3/10/25 half-lives, prior=12), or document why
  soccer deliberately diverges.
- Evaluate competition-specific attack/defense and home advantage by
  competition (named directly in the task background as feature
  candidates) — the current global pooling across 19 heterogeneous leagues
  is a real, unquantified simplification.
- Produce a real, versioned qualification artifact instead of leaving the
  only record of the real holdout numbers in a YAML comment.
- Fix the `"soccer_form"` docstring reference (minor).

## What would justify replacing the family

Nothing found in this audit. The architecture is sound, PIT-safe,
train/serve-parity-clean, and produces real, positive, walk-forward-
qualified results on two of its three markets. Replacement would only be
justified by a real competition-specific/home-advantage-by-competition
ablation showing the current pooled approach leaves meaningful accuracy on
the table, or a 1X2/total calibration check (once built) revealing the raw
probabilities are unusable for edge-sizing despite good hit rates. This
audit surfaces the absence of that evidence, not a finding that the model
is broken — the standing recommendation is audit-and-refine, not rebuild.
