# Feature audit: SOCCER

Audited 2026-08-11 against branch `audit/model-feature-reconciliation-v1`
(based on `origin/main` @ `826c89342bd2f3f1ea44fc29eaf20fad520dc5d5`).

`soccer-poisson-dc-v1` (`src/model_prediction/models/soccer.py`) is a
coherent goal-distribution model — everything below derives from one shared
Poisson/Dixon-Coles score matrix, not independent per-market features in the
`elo_probability`/`trend_gap` sense used by the MLB/NBA/WNBA/NFL logistic
regressions. It does not route through `learned_forward.py`'s generic
`_compute_features`/feature-registry pipeline at all — none of the entries
below have `config/tested_features.json` rows, and none should be force-fit
into that framework; they're documented here as the model's real inputs and
constants instead.

## Feature/parameter entries

### EWMA attack/defense strength (per-team, shrunk toward league mean)

- **Name**: no registered feature name. The model's own module docstring
  (`models/soccer.py:4-5`) calls this "the soccer_form feature" — that name
  does **not** exist anywhere in `features/` (grep-confirmed: no
  `register_feature("soccer_form")` or similarly-named module exists). This
  is itself a minor, harmless documentation imprecision worth fixing:
  the computation is inline inside `SoccerModel._strengths`
  (`models/soccer.py:86-106`), not a separately registered/reusable
  feature.
- **Model(s) using it**: `soccer-poisson-dc-v1` only — this is the entire
  input to the goal-rate model (moneyline, total, and BTTS all derive from
  the resulting score matrix).
- **Source location**: `models/soccer.py:86-106` (`_strengths`), calling
  `features/trends.py::ewm_level` (`trends.py:53-63`).
- **Provider**: `store.games_before("soccer", game_date)` — ESPN completed
  soccer scores via `data_sources/espn.py`/`ingest.py`, enriched via
  `football_data` (`config/model.yaml:78`, `soccer_enrichment:
  football_data`).
- **Formula**: for each team, `attack = ewm_level(goals_scored, half_life=10,
  baseline, prior=8) / baseline`, `defense = ewm_level(goals_allowed,
  half_life=10, baseline, prior=8) / baseline` (`models/soccer.py:100-105`).
  `ewm_level` (`trends.py:53-63`) is an exponentially-weighted mean with
  half-life in **games** (not calendar time) shrunk toward `baseline`
  proportional to `n / (n + prior)`. `baseline` is the mean goals-per-team
  across **all** history passed in (`models/soccer.py:98`) — see
  "competition pooling" finding below.
- **Expected sign**: positive for attack (higher attack strength → higher
  expected goals scored), positive for defense in the "allowed" sense (a
  defense value > 1.0 means the team concedes more than league-average,
  which correctly *raises* the opponent's expected goal rate in
  `home_rate = baseline * home['attack'] * away['defense'] * HOME_GOAL_BOOST`,
  `models/soccer.py:135`).
- **PIT-safe?**: Yes. `store.games_before` enforces the same
  midnight-ET-cutoff chokepoint used project-wide
  (`features/base.py:188-198`); `_strengths` only ever receives games
  strictly before the target date, and both `qualify_soccer_poisson_model`/
  `qualify_soccer_total_model` (`validation.py`) walk day-by-day with the
  same snapshot-then-append discipline confirmed for NBA in
  `docs/model_audit/models/NBA_ELO_TREND_LR_V4.md`.
- **Train/serve parity?**: Yes. `qualify_soccer_poisson_model`/
  `qualify_soccer_total_model` (`validation.py:1252-1480`) instantiate the
  real `SoccerModel` and call the real `predict_games`/`_strengths` — no
  reimplementation. `soccer_forward.py:270-271` (live serving) does the
  same.
- **Coverage**: every team with >=1 historical goal-scoring/conceding
  observation gets a real shrunk estimate; a team never seen before falls
  back to the neutral default `{"attack": 1.0, "defense": 1.0, "games":
  0.0}` (`models/soccer.py:133-134`) — cold-start, not missing-and-excluded.
- **Missingness behavior**: fail-open cold start (attack=defense=1.0,
  i.e. exactly league-average), gated downstream by `min_team_games`
  (`feature_basis`, `models/soccer.py:171`) checked against
  `MINIMUM_TEAM_GAMES = 10` in `cli.py:1635,1856` before a contract counts
  as `model_inputs_valid`.
- **Correlation notes**: attack and defense are computed independently per
  team from disjoint quantities (goals scored vs. goals allowed) but share
  the same `baseline`/`half_life`/`prior` constants — not independently
  tunable per side in the current code.
- **Coefficient/importance**: not a fitted coefficient (this is a Poisson
  rate model, not a logistic regression) — `half_life=10.0` and `prior=8.0`
  are **hardcoded literals inline in `_strengths`**
  (`models/soccer.py:102-103`), and notably **not** the same as the
  project's shared `TrendEngine` defaults (`HALF_LIVES = (3.0, 10.0, 25.0)`,
  `PRIOR_STRENGTH_GAMES = 12.0`, `features/trends.py:29-30`) — soccer's
  Poisson model has its own separate, narrower set of constants, not
  derived from or reconciled with the generic trend engine's tuning.
- **Ablation deltas**: none exist. No walk-forward comparison of
  half_life/prior values was found anywhere in this repo.
- **Calibration impact**: not isolated from the rest of the model — see
  model card's calibration section; the raw score matrix built from these
  strengths feeds moneyline/total directly with no separate calibration
  step, and BTTS with one (Platt-scaled, see below).
- **Known bugs**: none in the math itself (2026-07-31 full-project review,
  `DEBUG.md:1887-1889`, explicitly traced "soccer's actual Dixon-Coles
  matrix (home/away orientation, the rho low-score adjustment, BTTS Platt
  calibration bounds)" and found it correct — EWMA strength computation
  itself wasn't separately called out but is the same code path). The
  "soccer_form" docstring name-that-doesn't-exist is the one real (harmless)
  documentation defect.
- **Verdict: `KEEP_CORE`** — this is the entire predictive engine of the
  model; not optional, not ablatable without replacing the model family.
  Recommend fixing the docstring's "soccer_form feature" naming and running
  a real half_life/prior sensitivity check as future work, not urgent.

### League baseline (global mean goals-per-team)

- **Name**: `baseline` (local variable, `models/soccer.py:98`,
  `home_goal_rate`/`away_goal_rate` inputs; surfaced downstream as
  `league_goals_per_team` in `feature_basis`, `models/soccer.py:164`).
- **Model(s) using it**: `soccer-poisson-dc-v1` — the anchor value every
  team's attack/defense ratio and the final Poisson rate multiply from.
- **Source location**: `models/soccer.py:98` — `sum(goals) / len(goals) if
  goals else 1.35`, computed once per `predict_games` call over the entire
  `history` argument.
- **Provider**: same `store.games_before("soccer", ...)` history as the
  EWMA strengths above.
- **Formula**: simple arithmetic mean of every team's goals scored across
  every game in `history` (both home and away goals pooled into one flat
  list, `models/soccer.py:90`) — **not** league-specific, **not**
  home/away-split, **not** time-decayed (no EWMA here, unlike the per-team
  strengths above). Static fallback `1.35` if `history` is empty.
- **Expected sign**: N/A (a scale anchor, not a directional signal).
- **PIT-safe?**: Yes, same PIT-filtered `history` input as above.
- **Train/serve parity?**: Yes, same call site.
- **Coverage**: always defined (falls back to `1.35` if no history at all).
- **Missingness behavior**: fail-open to a plausible-looking hardcoded
  constant (`1.35`) rather than refusing to predict — this path is only hit
  with zero historical games in scope at all, which should be rare in
  practice given `MINIMUM_TEAM_GAMES`/`minimum_history_games` gates
  upstream, but is untested (no test found exercising the empty-history
  branch specifically).
- **Correlation notes**: N/A.
- **Coefficient/importance**: `1.35` fallback and the underlying mean
  computation are both unfit constants/formulas — no evidence of a
  cross-validated or literature-derived value; `1.35` is a plausible
  real-world average goals-per-team figure but not shown to be derived from
  this project's own data.
- **Ablation deltas**: none.
- **Calibration impact**: not isolated.
- **Known bugs**: **competition pooling — confirmed, real, unflagged
  elsewhere.** `SoccerModel._strengths` takes `history: Sequence[GameRecord]`
  with **no league filter** (`models/soccer.py:86-106`); `GameRecord` does
  carry a `league` field (`features/base.py:50`) but `_strengths` never
  reads it. Traced the live call site: `soccer_forward.py:223`
  (`history = store.games_before("soccer", game_date)`) loads **every**
  cached soccer game across **all** configured leagues (`config/model.yaml`
  lists 19: EPL, LA_LIGA, BUNDESLIGA, SERIE_A, MLS, UCL, BRASILEIRAO, and
  more — `model.yaml:286-306`) into one combined list, and
  `soccer_forward.py:271` passes that single combined `history` into
  `model.predict_games`. This means: one global `baseline` (average goals
  per team across EPL + MLS + Brasileirao + UCL + friendlies + everything
  else pooled together) is used for **every** match regardless of
  competition, and `HOME_GOAL_BOOST` (below) is likewise applied uniformly
  across all 19 leagues even though real home-advantage magnitude varies by
  competition/country. This is exactly the audit task's "competition
  pooling" concern, confirmed in code, not previously documented anywhere
  found in `DEBUG.md`/`docs/`.
- **Verdict: `KEEP_BASELINE`** — functions correctly as a global anchor and
  the walk-forward qualification results (62.5%/66.7% hit rates) are real
  against this exact pooled implementation, so it is not broken. Flagged
  for future competition-specific refinement (see "feature candidates"
  below) rather than an immediate change to the locked, qualified
  behavior.

### `HOME_GOAL_BOOST` (home-advantage multiplier)

- **Name**: `HOME_GOAL_BOOST` (module constant, `models/soccer.py:20`).
- **Model(s) using it**: `soccer-poisson-dc-v1` — multiplies the home
  team's expected goal rate up and the away team's down
  (`home_rate = baseline * home['attack'] * away['defense'] *
  HOME_GOAL_BOOST`; `away_rate = ... / HOME_GOAL_BOOST`,
  `models/soccer.py:135-136`).
- **Source location**: `models/soccer.py:20`, value `1.15`.
- **Provider**: N/A — hardcoded literal.
- **Formula**: symmetric multiplicative adjustment (home rate ×1.15, away
  rate ÷1.15) applied uniformly regardless of competition, team, or venue.
- **Expected sign**: positive (home teams score more on average — a
  well-established real effect in soccer), direction is correct.
- **PIT-safe?**: N/A — a constant, not a data-derived feature; cannot leak.
- **Train/serve parity?**: Yes — same constant used everywhere the model is
  called, no separate serving-time override found.
- **Coverage**: applies to every prediction, no missingness possible.
- **Missingness behavior**: N/A.
- **Correlation notes**: N/A.
- **Coefficient/importance**: **This is a hardcoded magic number with no
  fitting evidence found anywhere in this repo.** Grepped the entire tree:
  `HOME_GOAL_BOOST` appears nowhere outside `models/soccer.py` itself — no
  test, no calibration script, no `DEBUG.md`/`docs/` entry computes or
  justifies `1.15` specifically. It is a plausible real-world figure (home
  advantage in soccer typically corresponds to roughly a 0.2-0.3 extra
  expected goal split, in the right ballpark for a ~15% rate multiplier)
  but not shown to be fit against this project's own data via MLE, a
  regression, or a grid search.
- **Ablation deltas**: none — no walk-forward run in this repo varies
  `HOME_GOAL_BOOST` and compares results.
- **Calibration impact**: indirectly affects everything (shifts the entire
  score matrix), but no isolated calibration check of the home/away split
  specifically exists.
- **Known bugs**: none in application (the multiply/divide symmetry and
  home/away orientation were explicitly traced and confirmed correct in
  `DEBUG.md:1888-1889`). The defect is evidentiary, not computational: a
  production constant presented with no visible derivation.
- **Verdict: `RETEST_REQUIRED`** — the model's qualified, real holdout
  results (62.5%/66.7% hit rates) are evidence the *current* value works
  well enough to qualify, but that is not the same as evidence `1.15` is
  near-optimal, nor that a single global value (vs. competition-specific
  home advantage, one of the task's named feature candidates) isn't leaving
  real accuracy on the table. Recommend a real MLE/grid-search fit against
  locked-holdout-safe data before treating `1.15` as anything more than a
  reasonable starting guess that happens to work.

### `DC_RHO` (Dixon-Coles low-score dependence parameter)

- **Name**: `DC_RHO` (module constant, `models/soccer.py:21`).
- **Model(s) using it**: `soccer-poisson-dc-v1` — the `_dc_adjustment`
  function (`models/soccer.py:69-79`) applies it to exactly four score
  cells (0-0, 1-0, 0-1, 1-1) to correct the independence assumption
  between home/away Poisson rates that the classic Dixon-Coles paper
  identifies as biased at low scores.
- **Source location**: `models/soccer.py:21`, value `-0.10`.
- **Provider**: N/A — hardcoded literal.
- **Formula**: standard Dixon-Coles tau function
  (`_dc_adjustment`, `models/soccer.py:69-79`) — `1 - home_rate*away_rate*rho`
  for 0-0, `1 + home_rate*rho` for 0-1, `1 + away_rate*rho` for 1-0, `1 -
  rho` for 1-1, `1.0` elsewhere. Traced and confirmed to match the
  published Dixon-Coles tau formula's structure.
- **Expected sign**: negative, per the original Dixon-Coles paper (low
  scorelines are slightly *less* independent than a pure product-Poisson
  model implies — draws/1-0s are somewhat more common than independence
  predicts). `-0.10` has the right sign.
- **PIT-safe?**: N/A — constant.
- **Train/serve parity?**: Yes.
- **Coverage**: applies to every prediction (4 of the `(MAX_GOALS+1)^2 =
  121` score-matrix cells get adjusted; the rest get `1.0`, i.e. no
  adjustment).
- **Missingness behavior**: N/A.
- **Correlation notes**: N/A.
- **Coefficient/importance**: **Same finding as `HOME_GOAL_BOOST` — hardcoded,
  no fitting evidence.** Grepped the entire tree: `DC_RHO` appears nowhere
  outside `models/soccer.py`. The original Dixon-Coles (1997) paper reports
  rho values typically in the -0.1 to -0.2 range depending on league/era,
  so `-0.10` is within the historically plausible range, but again not
  shown to be fit against this project's own data (the textbook approach is
  a joint MLE alongside the attack/defense ratings, not a standalone
  literature-typical constant).
- **Ablation deltas**: none.
- **Calibration impact**: directly and measurably relevant to BTTS
  specifically per the model's own comment (`models/soccer.py:23-38`):
  the DC correction is explicitly "tuned for the 0-0/1-0/0-1/1-1 cells
  specifically, not [the derived BTTS] probability," which is given as
  part of the reason BTTS needed its own separate Platt-scaling fix (raw
  BTTS was overconfident, 55.0% actual vs. higher predicted).
- **Known bugs**: none in application (confirmed correct in
  `DEBUG.md:1888-1889`'s trace).
- **Verdict: `RETEST_REQUIRED`** — same reasoning as `HOME_GOAL_BOOST`: a
  plausible, correctly-signed, textbook-range constant with no
  project-specific fitting evidence. A joint MLE refit (attack/defense
  ratings + rho simultaneously, as the original paper does) is the
  textbook-correct next step and has not been done here.

### History window / competition pooling (structural, not a single constant)

- **Name**: no formal name — the effective lookback behavior of
  `store.games_before("soccer", game_date)`.
- **Model(s) using it**: `soccer-poisson-dc-v1`.
- **Source location**: `features/base.py:188-198` (`games_before`) called
  from `soccer_forward.py:223` and `validation.py:1270-1271`
  (`store.load_games("soccer")` then date-bucketed).
- **Formula**: `games_before` applies **only** a point-in-time upper-bound
  cutoff (`game.start < midnight-ET-at-start-of(as_of_date)`) — there is
  **no lower bound / no rolling window**. Every soccer game ever ingested
  into `data/processed/soccer/games.jsonl`, back to whenever ingestion
  started, is eligible input to `_strengths` for every prediction. Recency
  weighting is handled *only* by the EWMA half-life=10-games decay inside
  `_strengths` — there is no hard cutoff (e.g. "last 2 seasons only").
- **Expected sign**: N/A.
- **PIT-safe?**: Yes (upper bound is correctly enforced).
- **Train/serve parity?**: Yes — same `games_before`/`load_games` call
  shape in both qualification (`validation.py`) and live serving
  (`soccer_forward.py`).
- **Coverage**: grows monotonically over the life of the project; a team
  that stopped existing/got relegated years ago still contributes to the
  pooled global `baseline` if it's still in the file (though its own
  attack/defense ratings would only matter if it appeared in a future
  `upcoming` match, which wouldn't happen for a defunct team).
- **Missingness behavior**: N/A.
- **Correlation notes**: interacts with the competition-pooling finding
  above — an unbounded, all-competitions-pooled history means a
  league with unusually high/low scoring (e.g. a lower-tier league with
  fewer total games in the pool) has proportionally less influence on the
  global `baseline` than a heavily-covered league like EPL, an unquantified
  bias.
- **Coefficient/importance**: N/A.
- **Ablation deltas**: none — no comparison of windowed vs. unbounded
  history exists in this repo.
- **Calibration impact**: unmeasured directly, but plausible contributor
  to any mis-calibration in leagues/competitions under-represented in the
  pooled history.
- **Known bugs**: none crashing/incorrect — this is a design choice
  (unbounded history + EWMA decay instead of a hard window), not a bug, but
  it was not previously documented as a deliberate choice anywhere found in
  `DEBUG.md`/`docs/`.
- **Verdict: `RETEST_REQUIRED`** — reasonable default, unvalidated against
  alternatives (e.g. a rolling 2-season window, or per-competition
  baselines instead of one global pool).

### BTTS calibration (Platt scaling)

- **Name**: `BTTS_CALIBRATION_INTERCEPT` / `BTTS_CALIBRATION_SLOPE`
  (`models/soccer.py:39-40`, values `0.1393` / `0.4205`).
- **Model(s) using it**: `soccer-poisson-dc-v1`, BTTS market only.
- **Source location**: `_apply_btts_calibration` (`models/soccer.py:52-59`),
  applied to `raw_btts` before it's returned (`models/soccer.py:150`).
- **Provider**: fitted from the model's own historical predictions (Platt
  scaling — logistic regression of real outcome on `logit(raw_probability)`)
  — the module comment (`models/soccer.py:23-38`) documents the fit
  cohort/holdout split and real numbers, reproduced here for the record:
  raw BTTS validation-cohort accuracy 55.0%, with an example overconfident
  bucket (predicted 72%, actual 59%); after Platt fit on validation and
  checked on a **separate locked holdout**: accuracy 55.0% → 56.7%,
  calibration buckets close to the diagonal (55.3% predicted vs. 55.96%
  actual; 62.2% vs. 65.95%).
- **Formula**: `sigmoid(intercept + slope * logit(raw_probability))`
  (`models/soccer.py:52-59`).
- **Expected sign**: slope should be positive and roughly near 1 for a
  reasonable base model; `0.4205` (well below 1) indicates the raw model
  was substantially overconfident and the calibration compresses
  predictions meaningfully toward 0.5 — consistent with the comment's own
  "raw BTTS ... meaningfully overconfident" framing.
- **PIT-safe?**: Yes — fit on the validation cohort only, checked on a
  disjoint locked holdout, per the module comment; consistent with the
  project's stated calibration-fitting discipline
  (`rebuild/calibration.py`'s own docstring: "All calibrators are fitted on
  data DISJOINT from base-model training").
- **Train/serve parity?**: Yes — `_apply_btts_calibration` is unconditional
  inside `predict_games`, applied identically in qualification and live
  serving (both call the same `SoccerModel.predict_games`).
- **Coverage**: applies to every BTTS prediction, no missingness.
- **Missingness behavior**: N/A.
- **Correlation notes**: BTTS raw probability is itself derived from the
  same score matrix as moneyline/total (joint tail where both `home_goals
  >= 1` and `away_goals >= 1`), so it inherits all upstream
  strength/baseline/DC-rho behavior; the Platt fit corrects only the final
  probability, not any upstream input.
- **Coefficient/importance**: real, documented fit — see above. This is the
  **only** market in this model with an actual applied calibration
  correction.
- **Ablation deltas**: the accuracy delta itself (55.0% → 56.7% on locked
  holdout) *is* the ablation evidence — a genuine before/after comparison,
  unlike every other constant in this model.
- **Calibration impact**: real, measured, holdout-verified (bucket-level
  reliability numbers quoted above).
- **Known bugs**: none — `DEBUG.md:1888-1889` explicitly traced "BTTS
  Platt calibration bounds" and confirmed correct. One real operational
  caveat, not a bug in the calibration itself:
  **BTTS currently has no executable market to price against.**
  `soccer_forward.py:116-127` documents that Polymarket has never listed a
  BTTS market as of repeated live checks (2026-07-25, 07-27, 07-29,
  07-30) — `_latest_btts_snapshots` always returns `[]`, so every BTTS
  prediction reaches `unmatched` with reason `"no unique timestamp-valid
  BTTS market matched"` (`soccer_forward.py:505`). The calibration is real
  and correct; it is simply never exercised against a live, tradeable
  market today.
- **Verdict: `KEEP_RESEARCH_ONLY`** — matches the model's own comment
  verbatim: "BTTS stays research-only regardless of any future promotion
  decision for moneyline/totals from the same model" (`models/soccer.py:36-38`).
  This is the single best-evidenced calibration decision anywhere in the
  soccer model; retain as-is.

### 1X2 (moneyline) calibration

- **Name**: none — there is no calibration transform applied to the
  moneyline probabilities at all.
- **Model(s) using it**: N/A — this entry documents an **absence**.
- **Source location**: `models/soccer.py:138-140,182-186` — `home_win`,
  `away_win`, `draw` are returned directly from the raw normalized score
  matrix (`score_matrix`, `models/soccer.py:108-123`), with no Platt/
  isotonic/temperature adjustment applied anywhere between the matrix and
  the returned `probabilities` dict.
- **Provider**: N/A.
- **Formula**: N/A — raw `sum(matrix cells)` per outcome.
- **Expected sign**: N/A.
- **PIT-safe?**: N/A.
- **Train/serve parity?**: N/A (nothing to have parity issues with).
- **Coverage**: N/A.
- **Missingness behavior**: N/A.
- **Correlation notes**: N/A.
- **Coefficient/importance**: N/A — no fitted parameters exist for this
  market.
- **Ablation deltas**: N/A.
- **Calibration impact**: **This directly answers the audit task's
  question.** `qualify_soccer_poisson_model` (`validation.py:1252-1378`)
  grades moneyline using `confidence = max(probabilities.values())` and
  `selection = argmax(probabilities)` (`validation.py:1309-1310`) — this is
  a **selective-calling decision threshold on the raw 3-way argmax
  probability**, not a probability recalibration, and it is **not** a
  binarized draw/away split either (verified: no code anywhere converts
  the 3-way outcome into two binary one-vs-rest problems for calibration
  purposes — the argmax-and-threshold approach sidesteps the
  binarization question entirely by never calibrating probabilities at
  all, only thresholding the model's own stated confidence). So, precisely:
  **no multiclass calibration approach of any kind (proper or binarized) is
  used for 1X2** — raw simulated probabilities are used directly for both
  sizing (`edge_vs_executable_ask`-style logic in `soccer_forward.py`) and
  for the confidence-threshold gate. The generic calibration diagnostic
  tooling in `rebuild/calibration.py` (which does support a real
  intercept/slope/reliability check) has zero call sites anywhere in this
  repo — never run for 1X2, or for any market.
- **Known bugs**: not a bug per se — the model qualifies (62.5%
  locked-holdout hit rate) using this uncalibrated-probability-plus-threshold
  approach, so it is not obviously broken. But it means the *raw* home/
  draw/away probabilities used for edge-sizing have never been checked for
  calibration quality the way BTTS's were, despite 1X2 being a 3-way
  market where miscalibration (e.g. systematically underrating draws, a
  common failure mode for naive Poisson models before a Dixon-Coles-style
  correction) is a well-known real risk.
- **Verdict: `RETEST_REQUIRED`** — the model works well enough to qualify
  on hit-rate/units alone, but per the task's explicit instruction to
  check for a proper multiclass calibration approach: there isn't one.
  Recommend a genuine multiclass calibration check (e.g. per-class
  reliability curves, or a proper multiclass Platt/Dirichlet calibration
  fit on the validation cohort and checked on locked holdout, mirroring
  BTTS's own methodology) before trusting the raw probabilities for
  anything beyond argmax-and-threshold selection.

### Total (O/U 2.5) calibration

- **Name**: none — same absence as 1X2.
- **Model(s) using it**: N/A.
- **Source location**: `models/soccer.py:141-146,191-198` — `over25` is the
  raw summed probability mass where `home_goals + away_goals > 2.5`, no
  calibration transform applied.
- **Provider**: N/A.
- **Formula**: N/A.
- **Expected sign**: N/A.
- **PIT-safe?**: N/A.
- **Train/serve parity?**: N/A.
- **Coverage**: N/A.
- **Missingness behavior**: N/A.
- **Correlation notes**: shares the same upstream score matrix as
  moneyline/BTTS.
- **Coefficient/importance**: N/A.
- **Ablation deltas**: N/A.
- **Calibration impact**: `qualify_soccer_total_model`
  (`validation.py:1381-1480`) grades using `confidence = abs(p_over - 0.5)`,
  `selection_over = p_over >= 0.5` — again a threshold on the raw
  probability, not a calibrated one. This is soccer's **primary priced
  market** per that function's own docstring ("the only [market] with real
  historical Polymarket depth") — the market that actually matters most for
  real sizing decisions has never had its raw probability calibration
  checked, same gap as 1X2.
- **Known bugs**: none causing incorrect results (66.7% hit rate on 162
  locked-holdout calls, +44.2 units, qualifies real). Same evidentiary gap
  as 1X2: no calibration diagnostic exists.
- **Verdict: `RETEST_REQUIRED`** — same reasoning as 1X2; this is the
  market where it matters most given it's the one with real market depth
  and Main-ledger exposure.

## Feature candidates for later (per task background) — not yet built

None of these exist as code anywhere in `src/`. Verdicts reflect "not yet
evaluable" rather than any judgment on their merit.

| candidate | grep-confirmed absent? | verdict |
|---|---|---|
| Competition-specific attack/defense | Yes — `_strengths` has no league parameter | `RETEST_REQUIRED` |
| Home advantage by competition | Yes — `HOME_GOAL_BOOST` is a single global constant | `RETEST_REQUIRED` |
| Rest days | Yes — no rest/schedule feature referenced in `models/soccer.py` or `soccer_forward.py` | `RETEST_REQUIRED` |
| Schedule density | Yes — same | `RETEST_REQUIRED` |
| xG (expected goals) where available | Yes — no `xg`/`expected_goals` reference anywhere in soccer source | `RETEST_REQUIRED` |
| Confirmed-lineup-only when PIT-valid | Yes — no lineup/roster feature in soccer source (the project's own `player_availability`/`mlb_player_availability` shadow-feature pattern exists for MLB/WNBA, not soccer) | `RETEST_REQUIRED` |

Each would need the project's standard shadow-feature build pattern
(data-source module with `observed_at_utc` provenance, fail-closed feature
module, wiring, then a real walk-forward ablation) before any stronger
verdict is possible — none of that groundwork exists yet for soccer beyond
what's audited above.
