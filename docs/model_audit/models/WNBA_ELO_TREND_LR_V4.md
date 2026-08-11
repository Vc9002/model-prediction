# Model Card: `wnba-elo-trend-lr-v4`

**Model ID:** `wnba-elo-trend-lr-v4`
**Artifact:** `config/models/wnba-elo-trend-lr-v4.json`
**Family:** `elo_trend_normal_approximation` (`config/model.yaml` → `WNBA.family`)
**Status in config:** `shadow_qualified`, `active_production_version: wnba-elo-trend-lr-v4`
**Market:** moneyline only
**Audit date:** 2026-08-11
**Repo state audited:** `audit/model-feature-reconciliation-v1` @ `826c893` (origin/main)

Scope note: this card documents the model **as it exists today**, for the
purpose of deciding what to retain. Per this audit's operator directive, the
`wnba-elo-trend-lr-v4` artifact itself is being **retained as-is and treated
as immutable** — nothing here is a request to retrain or re-threshold v4.
Findings that suggest changes are scoped to a *future* `wnba-elo-trend-lr-v5`.

---

## Why it exists

WNBA moneyline is one of two sports (with MLB) whose Main-ledger picks are
promoted out of pure research/shadow status (`CLAUDE.md`: "a small number
(MLB moneyline, WNBA moneyline) are promoted to produce real, sized
`QUALIFIED_SHADOW_CALL` rows"). `wnba-elo-trend-lr-v4` is the incumbent
model backing that promotion. It descends from a `v1` (2-feature,
never qualified, 97 calls, 65.98% hit rate) → `v3` (3-feature, qualified,
178 calls, 69.3% hit rate) → `v4` (current, 3-feature, requalified on a
later window, 163 calls, 67.5% hit rate) lineage. `v2` was never archived —
no artifact or record of it exists in `config/models/archive/`.

## Market(s) predicted

Moneyline win probability only. WNBA spread and total are served by a
separate, much weaker sibling artifact — see "Sibling model" at the bottom
of this card. There is no WNBA spread/total learned model in this family.

## Feature set

Exactly three features, per `market_models.moneyline.feature_names` in the
artifact:

| Feature | Coefficient (v4) | Coefficient (v3, prior) |
|---|---|---|
| `elo_probability` | 3.1343561088 | 3.1372112172 |
| `trend_gap` | -0.0067554948 | -0.0154698977 |
| `defensive_trend_gap` | -0.0029373676 | 0.0089977272 |
| intercept | -1.5717634496 | -1.5583205662 |

Method: `logistic_regression`. `positive_class: "home"`. No market inputs
used in training (`training.market_inputs_used: false`).

`availability_points_gap` (from `features/player_availability.py`) is
**not** a regression feature in this artifact — verified directly against
`market_models.moneyline.feature_names`, which lists only the three features
above. It is instead applied as a **post-hoc probit adjustment** to the
model's output probability at serve time
(`learned_forward.py:458-497`), gated so it only fires when the adjustment
would move the probability by ≥5pp (`delta >= 0.05`). This confirms the
background brief's framing: v4 is a genuine 3-feature Elo+trend LR, and
availability is a separate serving-time layer, not a fourth trained
coefficient. See `docs/model_audit/features/WNBA.md` for the availability
feature's own evaluation.

## Training method

- Chronological 60/20/20 split (`training` block): coefficient fit on
  2024-05-31→2025-08-15 (457 obs), threshold selection on 2025-08-16→
  2026-05-18 (143 obs, "later validation cohort; never locked holdout"),
  locked holdout on 2026-05-19→2026-07-19 (163 obs).
- `walk_forward_features: true` — features for each row are built from
  `history` strictly before that row's date (matches
  `validation.py::build_walk_forward_rows`'s day-bucketing loop, the same
  loop independently traced for NBA's identical `elo_ratings.py`/
  `trends.py` code path in `outputs/rebuild/audit/elo_leakage_trace.py`,
  found in this worktree — see PIT-safety below).
- `framework: "locked_complete_date_60_20_20"`, `locked_holdout: true`.

## Threshold selection

- `confidence_threshold: 0.50013272` — effectively calls the entire slate.
  This matches the background brief and the inventory snapshot's note
  ("Threshold ~0.50013 — effectively calls the complete slate"). `v3`'s
  threshold was `0.50507187`, similarly close to 0.5.
- `threshold_source: "later validation cohort; never locked holdout"` — the
  threshold-selection cohort is chronologically after the coefficient-fit
  cohort and before the locked holdout, so it does not touch holdout data.
  This is the correct split discipline per `docs/ARCHITECTURE.md`.
- **Operationally, the near-0.5 threshold is close to moot**: per
  `learned_forward.py`'s 2026-07-30 operator directive comment (same file,
  lines ~502-509), every candidate becomes a real sized
  `QUALIFIED_SHADOW_CALL` regardless of whether it clears
  `confidence_threshold` — the threshold is retained on the candidate only
  as an informational number for a human, not as a hard gate. So this
  model's `called_rate: 1.0` is not really a threshold effect; it is a
  policy effect layered on top of an already-near-0.5 threshold.

## Historical results (locked holdout, per artifact)

| Metric | v4 | v3 (prior artifact) |
|---|---|---|
| Calls | 163 | 176 (178 total, 98.9% called) |
| Hit rate | 67.48% | 69.32% |
| Brier score | 0.21414 | 0.21654 |
| Log loss | 0.61874 | 0.62424 |
| Units at -110 | +47.0 | +56.9 |
| Monthly hit rate | May 54.5%, Jun 73.2%, Jul 66.7% | May 58.3%, Jun 76.25%, Jul 68.75% |

`every_called_month_positive_at_minus_110: true` for both. `qualified: true`
for both (`minimum_calls: 50`, `minimum_hit_rate: 0.6` both cleared).
`config/model.yaml`'s WNBA block additionally documents a flat-vs-Kelly
sizing finding not in the artifact itself: "Kelly/edge-scaled sizing
underperforms flat betting for WNBA due to thin edges. Flat 2.0u produced
+2.64u on 13 settled picks vs +0.40u ledger P&L" (2026-07-22 analysis) —
hence `sizing_recommendation: flat_1.5_to_2.0u`, not edge-scaled.

## Calibration diagnostics

Recorded in the artifact (`qualification.calibration`), not applied at
serving time (serving uses identity — this matches
`docs/FEATURE_REGISTRY.md`'s corrected claim #4: diagnostics are recorded
everywhere, an *applied* calibrator is what's missing):

- `calibration_slope: 1.2700`, `calibration_intercept: 0.0758` — slope > 1
  indicates **underconfidence** (predicted probabilities are too close to
  0.5 relative to realized outcomes), consistent with
  `current_system_inventory.json`'s note "WNBA 1.270 indicate
  underconfidence."
- `expected_calibration_error: 0.0465`.
- Reliability buckets (3 buckets, 163 obs): 0.5-0.6 predicted → 57.1% actual
  (56 obs); 0.6-0.7 predicted → 69.2% actual (78 obs); 0.7-0.8 predicted →
  82.8% actual (29 obs). All three buckets show actual > predicted,
  consistent with the underconfidence slope reading — the model is, if
  anything, too conservative in this window, not overconfident.

## Known defects / risks

1. **`defensive_trend_gap` coefficient is near zero and unstable in sign**
   across versions: -0.0029 (v4) vs. +0.0090 (v3) — the coefficient isn't
   just small, it flips sign between refits on overlapping-but-shifted
   windows. This independently confirms it is not doing real work; see the
   feature doc and the recommendation below.
2. **Audit-trail blind spot (documented, largely orthogonal to model
   validity)**: `docs/model_audit/prior_evidence/current_system_audit.md`
   (2026-08-05, `rebuild/clean-slate-v1` branch) records that
   `defensive_trend_gap` "was silently blank in audit trail until
   2026-08-04" — the feature was computed and scored correctly but not
   serialized into the logged audit row, so historical audit logs
   undercount what actually drove the prediction for that window. This is
   a logging/observability gap, not a leakage or serving bug — model
   probability was unaffected (scored from the features dict directly).
3. **WNBA availability adjustment historically had a fail-closed gap.**
   `DEBUG.md`'s P0 finding #3 ("WNBA availability does not fail closed on
   source conflicts") describes `merge_availability_sources` defaulting to
   a research-only `most_conservative` policy and the production path
   suppressing conflict exceptions. **Verified against current code and
   found stale**: `features/player_availability.py`'s
   `merge_availability_sources` signature now defaults
   `conflict_policy: str = "fail_closed"`, and the current call site in
   `matchup_player_availability` (same file, line ~309) does not override
   it. `learned_forward.py`'s WNBA availability block (lines 460-497)
   catches `(ValueError, KeyError, TypeError)` broadly around the whole
   availability computation and, on any of those (including the
   fail-closed conflict `ValueError`), logs a warning, records a note, and
   continues **without applying an adjustment** — i.e. it degrades to the
   unadjusted three-feature LR probability rather than serving a bad
   adjustment. `tests/test_wnba_availability.py::test_conflicting_explicit_
   sources_fail_closed` exercises this path directly. This appears to have
   been fixed since the DEBUG.md P0 list was written; treat the P0 #3 entry
   as resolved in current main pending a targeted regression check by
   whoever consolidates the audit, since this card cannot run the test
   suite (docs-only worktree, no venv).
4. **WNBA priors freshness risk (documented, not independently re-verified
   here)**: the same `current_system_audit.md` flags that
   `build_and_save_priors()` runs daily and "the prior for today's games
   includes data from today, which could leak if today's games are
   predicted after priors are built." This is about the *prior-freshness*
   input to the availability adjustment layer, not the three trained LR
   features — see the feature doc for detail.

## PIT-safety

- **Elo/trend features**: `elo_ratings.py::build_elo` and
  `trends.py::TrendEngine` are the same shared implementations used by
  MLB/NBA/NFL/soccer (only per-sport K/home-advantage/regression constants
  differ; WNBA config: `k=20, home_advantage=60, offseason_regression=0.40`
  in `ELO_CONFIG`). `outputs/rebuild/audit/elo_leakage_trace.py` — a
  leftover, real, read-only trace script found already present in this
  worktree — independently replicates `validation.py`'s day-bucketing
  walk-forward loop against real NBA game data and checks the invariant
  that each team's Elo snapshot for day `D` only reflects games with
  `start < D`. It was written for NBA (the only historical dataset
  available in this docs-only environment — `data/processed/` is
  gitignored/absent here), but exercises the *identical* `build_elo`/
  `TrendEngine` code WNBA uses, so its structural finding (the day-bucket
  loop takes an Elo snapshot before extending history with that day's
  games) transfers to WNBA's use of the same functions. This card did not
  re-run that script (no venv in this worktree); it is cited as evidence
  the mechanism has already been traced, not as a WNBA-specific
  re-verification.
- **`trend_gap` / `defensive_trend_gap`**: both are differences of
  `TeamTrend.offensive_momentum` / `.defensive_momentum`, which are
  themselves EWMA levels over `context.games` — the same games list fed to
  Elo, so the same walk-forward discipline applies.
  `trends.py::TrendEngine` reads from an already-chronologically-filtered
  game list; it does not itself re-check timestamps, so PIT-safety here is
  a property of the *caller* (`learned_forward.py`/`validation.py`)
  correctly slicing `history` before construction, not of `TrendEngine`
  itself.
- **`availability_points_gap` (adjustment layer, not a trained feature)**:
  `player_availability.py` is unusually strict about this — every snapshot
  and prior lookup explicitly compares `observed_at`/`report_at`/`as_of`
  against the decision timestamp and raises a `NO_CALL_*` `ValueError`
  otherwise (`_latest_snapshot`, `_load_priors`,
  `matchup_player_availability_from_payloads`'s `observed >= start` check).
  The one caveat is the priors-freshness point in "Known defects" #4 above
  — that's about whether the *prior itself* was built using same-day data,
  which is a level up from this function's own point-in-time filtering.

## Train/serve parity

- Training uses `validation.py::build_walk_forward_rows` (not independently
  re-read line-by-line in this card beyond the docstring/citation in
  `elo_leakage_trace.py`, since `scikit-learn` isn't installed in this
  docs-only worktree and `validation.py` imports it at module level).
  Serving uses `learned_forward.py::_compute_features`, which builds the
  same three features (`elo.expected_home_win`,
  `home_trend.offensive_momentum - away_trend.offensive_momentum`,
  `home_trend.defensive_momentum - away_trend.defensive_momentum` — see
  `learned_forward.py:73-75`) from the same `TrendEngine`/`build_elo`
  primitives. This is the same shared-primitive pattern used by every
  other Elo+trend sport in this project, which reduces (but doesn't
  eliminate) train/serve drift risk relative to a model with
  separately-implemented training and serving feature code.
- The availability-adjustment layer is serve-only by construction (it
  post-processes `home_probability`, it isn't in the trained coefficient
  vector), so there's no train/serve parity question for it in the usual
  sense — but see "Known defects" #4 for a related freshness risk.

## Artifact reproducibility

- Whole-file hash: `shasum -a 256 config/models/wnba-elo-trend-lr-v4.json`
  → `e0f9ccb37851e2000021b2cf90f8b777970e0b31e5e1298840c13cd9826cc1bf`,
  which matches `docs/model_audit/prior_evidence/incumbent_artifact_hashes.txt`
  line 45 exactly (verified directly in this audit).
- The artifact also carries its own internal `artifact_hash` field
  (`7afd5274214b58b7efd7f7febc7be3ab33b92351cda5cc6403c0a39faea0cc8c`) — a
  different, canonical-content hash computed over the artifact's own JSON
  (pattern seen in `esports.py`/`international_baseball.py`:
  `hashlib.sha256(_canonical_json(artifact).encode())`), used for
  tamper-detection at load time rather than as a whole-file checksum. Both
  mechanisms are present and internally consistent; they are not expected
  to be equal to each other, and are not.
- `config/model.yaml`'s `protected_versions` for WNBA lists
  `wnba-elo-trend-v1`, `wnba-elo-trend-lr-v1`, `wnba-elo-trend-lr-v3` —
  confirming `v4` is protected-by-being-current rather than
  protected-by-list, and that `v1`/`v3` (but not a `v2`) are the retained
  prior versions.

## What to retain / what to change

**Retain (this audit, per operator directive): the v4 artifact itself,
unmodified.** It is qualified, chronologically validated, and its
coefficients/threshold/hashes are internally consistent.

**Recommend for the next same-family revision (`wnba-elo-trend-lr-v5`),
not for v4 itself:**

1. Test removing `defensive_trend_gap`. Evidence for this is now
   triple-sourced and consistent: (a) `config/tested_features.json`'s
   `defensive_trend_gap` entry — WNBA fitted coefficient -0.003,
   `production_ablation.WNBA: "INCONCLUSIVE"`, `verdict:
   "remove_candidate"`, notes state "Removal improves both validation and
   holdout proper scores"; (b) `docs/FEATURE_REGISTRY.md` lists it as a
   **remove candidate** for NBA/WNBA with the same rationale; (c) this
   card's own artifact read confirms the coefficient is not just small but
   sign-unstable across v3→v4 refits (+0.0090 → -0.0029). This confirms
   the background brief's claim. The retest should follow
   `docs/tested_features.json`'s retention rule (a feature is kept only if
   its omission *worsens* validation Brier or both locked-holdout proper
   scores by any positive amount) — the current evidence says omission
   *improves* both, so v5 is a reasonable place to actually drop it, not
   just flag it again.
2. Investigate the calibration slope (1.27, underconfident) for an applied
   (not just diagnostic) calibrator, consistent with corrected claim #4 in
   `docs/FEATURE_REGISTRY.md` — this is a project-wide gap, not
   WNBA-specific, but WNBA's slope is one of the more material ones on
   record (alongside NBA's 1.785).
3. Re-run the `defensive_trend_gap` retest and the calibration
   investigation together rather than serially, since removing a
   near-zero, sign-flipping feature can itself shift the fitted
   intercept/slope relationship used for calibration diagnostics.

**Do not**, on this evidence alone: drop `elo_probability` or `trend_gap`.
`elo_probability` is `evidence_grade: A` and the only consistently material
coefficient in the entire project (WNBA fitted value 3.134, in the same
range as MLB/NBA/NFL/SOCCER). `trend_gap`'s WNBA coefficient is also small
(-0.007) and its production ablation is `INCONCLUSIVE`, but
`tested_features.json`'s zero-threshold retention policy keeps it because
at least one out-of-sample cohort improves with it present — that's a
weaker case than `defensive_trend_gap`'s (whose removal improves *both*
proper scores), so treat them differently rather than lumping "the two
small coefficients" together.

## What would justify replacing the family

The current family is a 3-feature (2, after a v5 that drops
`defensive_trend_gap`) Elo+trend logistic regression with no possessions,
efficiency, pace, roster, or matchup signal. Per
`docs/MODEL_IMPROVEMENTS.md` §7 ("WNBA feature roadmap"), replacement would
be justified once a **combined model** — Elo/trend control +
opponent-adjusted pace/Four-Factors (with heavy shrinkage) + projected
minutes × player impact + roster continuity — clears the same
locked-holdout discipline (chronological 60/20/20, threshold selected on
validation only, holdout touched once) and beats the current elo_trend
control on both validation and locked-holdout proper scores, not just
accuracy — `docs/MODEL_IMPROVEMENTS.md` explicitly warns not to promote a
richer model on accuracy alone, per the WNBA player-availability
reconstruction's own finding (accuracy dropped, Brier improved, when
availability was added). The `rebuild/wnba-v1` archived research code
(`docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md`) is relevant
groundwork for exactly this Four-Factors step, but is presently
`RESEARCH_ONLY`/qualification-blocked on unresolved data-source commercial
rights — that blocker would need to be resolved (or a rights-clear data
source substituted) before any Four-Factors-based challenger could clear
even a research qualification gate, let alone promotion.

`strict_statistical_verdict`: **RETEST_REQUIRED** for `defensive_trend_gap`
specifically (production ablation is formally `INCONCLUSIVE`, not
`REJECT`, despite the removal-improves-both-scores finding — the strict
leave-one-out test has not been re-run against the current exact v4
artifact per `tested_features.json`'s own framing). The `elo_probability` +
`trend_gap` core is **KEEP_CORE**.

`operator_retention_verdict`: **KEEP** — v4 stays as the immutable
moneyline control per this audit's scope; the `defensive_trend_gap` retest
and any resulting feature drop are explicitly deferred to a future v5, not
applied to v4.

---

## Sibling model note (context only): `wnba-spread-baseline-v1`

Not a moneyline model and not the subject of this card's main analysis, but
directly relevant to "what the WNBA model family currently is" — the
project's spread/total incumbent for WNBA is
`config/models/wnba-spread-baseline-v1.json`:

- **Method**: `baseline_heuristic`, not a fitted model. Spread side:
  `feature_names: ["elo_margin"]`, coefficient `1.0`, i.e. the predicted
  spread probability is a raw pass-through of the Elo-implied margin — no
  fitted parameters at all. Total side: `feature_names:
  ["league_avg_total"]`, coefficient `1.0` — predicts every game's total
  as the league average, with zero game-specific information.
- **Qualification**: `qualified: false`, `status: "active_research"`.
  Spread: 340 calls, **32.9% hit rate**, **-126.18 units** — well below
  break-even and actively losing. Total: 387 calls, 78.3% hit rate, +191.5
  units — this side looks strong, but a "predict the league average every
  time" heuristic beating a market line at a 78% clip on 387 calls is the
  kind of result that warrants scrutiny of the *evaluation methodology*
  (line source, push handling, sample overlap with the mean it's
  predicting) before being taken as a real edge, not immediate promotion.
  This card does not have the total-side backtest methodology in scope to
  adjudicate that; flagging it as a specific follow-up rather than
  asserting it either way.
- **Classification (per this audit's background directive)**:
  `KEEP_BASELINE` / `REBUILD_IMPROVE_HEAD_REQUIRED` — retain as the
  reference/floor baseline (it is honestly labeled `active_research` and
  `qualified: false`, not silently presented as production-ready), but the
  spread side in particular needs a real head (a fitted margin model, e.g.
  the Ridge-based approach `wnba-total-score-ridge-v1.json` already
  demonstrates for totals — see below) before any promotion consideration.
  Do not delete this baseline before a replacement clears the same
  locked-holdout bar; it is the only thing currently benchmarking WNBA
  spread/total performance at all.
- **A real ridge-regression WNBA total-score model already exists as a
  research candidate**: `config/models/wnba-total-score-ridge-v1.json`
  (`method: "ridge_regression"`, 9 features — league mean + rolling 5/10
  game scored/allowed for both teams — `status:
  "research_score_model_candidate"`). Its own locked holdout shows
  `beats_baseline_mae: false` (MAE 19.87 vs. a 17.92 rolling-league-mean
  baseline, `statistically_clear_mae_gain: false`) — so this specific
  research candidate does **not** yet beat even a simple baseline on score
  MAE, and its `market_qualification.reason` is explicitly
  `"DATA_READY_PENDING_OVER_UNDER_EVALUATION"` (2,123 timestamp-valid total
  snapshots exist, but the contract-level over/under backtest against real
  market lines has not been run). This is useful, honest groundwork for
  the "REBUILD/IMPROVE HEAD REQUIRED" direction, not a ready replacement.
