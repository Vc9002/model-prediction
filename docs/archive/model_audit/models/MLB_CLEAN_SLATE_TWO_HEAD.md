# Model card: Clean-slate XGBoost two-head / negative-binomial coherent score model

Status recommendation: **KEEP_CHALLENGER** — valuable specifically for
coherent spread/total/expected-score research, **not** a moneyline
replacement for `mlb-elo-trend-lr-v8`. This is not a `main`-branch model —
everything below was checked directly against
`origin/rebuild/clean-slate-v1` (local pin: tag
`archive/model-source-clean-slate-70250b1` @
`70250b10889ce58452b9685c12dbf515028b7d81`) via `git show`/`git ls-tree`,
plus this worktree's `docs/model_audit/prior_evidence/` files, which are
prior-session evidence (cited, not treated as current-`main` truth). No
files outside the audit's own scope were modified to produce this card.

## Why it exists

CLAUDE.md's rebuild contract calls for a coherent single model that
derives moneyline, spread, total, push, and expected score from one joint
score distribution — "no disconnected classifier" — rather than
`mlb-elo-trend-lr-v8`'s market-specific logistic regression plus the
Poisson Trend Engine's separate two-stage simulation-then-calibrate
pipeline for spread/total. `XGBoostTwoHeadModel`
(`src/model_prediction/rebuild/models/__init__.py` on that branch) is
explicitly built, per its own code comment, as *"a coherent XGBoost-based
challenger... two expected-run regression heads feeding the identical
JointScoreDistribution reconciliation MLBTwoHeadModel uses"* — i.e. it is
a drop-in architectural swap of the head family (XGBoost regression
instead of HistGradientBoosting/ElasticNet) inside the same coherent
joint-distribution design, not a separate model family.

## Architecture (verified by direct code read)

- **`RunIntensityHead`**: predicts total scoring environment.
  `MLBTwoHeadModel`'s version uses `HistGradientBoostingRegressor`;
  `XGBoostTwoHeadModel`'s version uses `XGBoostRunHead` (XGBoost
  regression, native NaN handling — confirmed the module's own comment:
  *"ElasticNet has no native NaN support"* was the original motivation for
  building the XGBoost head family).
- **`RunDifferentialHead`**: predicts which team owns the run advantage.
  `MLBTwoHeadModel` uses `ElasticNet` with `SimpleImputer`; the XGBoost
  variant reuses `XGBoostRunHead` again for both heads.
- **`JointScoreDistribution`**: reconciles both heads' expected runs into
  a simulated joint away/home score distribution (independent or bivariate
  Poisson/negative-binomial), from which moneyline, spread (any real
  signed line), total (any real line), and push probabilities are all
  derived — genuinely coherent, verified by reading `derive_market_distribution`-equivalent
  logic in `JointScoreDistribution`.
- **`BootstrapMLBEnsemble`**: 20 independent resample-fit replicates,
  reporting an empirical [10th, 90th] percentile per market as a real,
  data-driven uncertainty bound (not a flat haircut) — confirmed in code
  and in `docs/model_audit/prior_evidence/model_cards/mlb-two-head-v1.md`'s
  live example (a 0.49 point estimate → real [0.271, 0.671] bound).

Two real, previously-caught bugs are documented directly in the branch's
own code comments and worth preserving institutional memory of if this
architecture is ever revived: (1) `StandardScaler`/`HistGradientBoostingRegressor`
crashing on an all-NaN column (weather was 100% missing across the entire
real historical training set at one point) — fixed by
`_neutralize_always_missing_columns`; (2) `SimpleImputer(strategy="mean")`
silently *dropping* an all-NaN column rather than erroring, which would
have desynced the feature-name list from the matrix — same fix applies.
A third, subtler bug: a *bootstrap resample* can draw a naturally
low-cardinality real feature (e.g. `park_factor`, ~30 distinct real values)
down to a single distinct value by chance even when the full dataset is
fine, re-triggering the same crash — caught and fixed via
`_low_variance_columns`' real distinct-value count, not merely an
all-NaN check.

## Feature set (verified against `src/model_prediction/rebuild/mlb_features.py`)

`MLB_INTENSITY_FEATURES` (7 real features + availability flags):
`home_sp_avg_velocity`, `away_sp_avg_velocity`, `home_sp_csw_pct`,
`away_sp_csw_pct`, `home_bp_bullpen_pitches`, `away_bp_bullpen_pitches`,
`park_factor`, `temp_f_first_pitch`, plus 5 `*_availability` indicators.

`MLB_DIFFERENTIAL_FEATURES` (6 real features + availability flags):
`home_sp_k_pct`, `away_sp_k_pct`, `home_sp_bb_pct`, `away_sp_bb_pct`,
`home_sp_days_rest`, `away_sp_days_rest`, `home_bp_bullpen_avg_velocity`,
`away_bp_bullpen_avg_velocity`, plus 4 `*_availability` indicators.

Full detail (formula, missingness handling, what's aspirational-only vs.
real) is in `docs/model_audit/features/MLB.md`'s Statcast-candidate
section — not duplicated here. Starters are resolved via
`resolve_horizon_starter_names()` at the correct point-in-time decision
horizon (a real, documented train/serve-parity fix — see PIT-safety
below), not from the completed game's actual starter.

## Training method

Two independent, sequential evaluation efforts exist in
`docs/model_audit/prior_evidence/`, at different real-game sample sizes as
the branch's backfill window grew — cite both, note they are not
identical experiments:

1. **`mlb_score_model_comparison.json`** (435 matched games, 203 moneyline
   OOF rows, chronological 3-fold): `xgb_two_head`'s constructor-default
   `method` (confirmed independently by this audit's own external review
   citation below) was Poisson, not negative-binomial, at this stage.
   Fold-level log loss/Brier for `two_head` vs. `xgb_two_head` vs.
   `xgb_direct` (a separate, non-coherent direct XGBoost classifier) are
   recorded per-fold; `xgb_direct` (the *non-coherent* classifier) had the
   best raw numbers at this stage (OOF log loss 0.7221 vs. `two_head`'s
   0.8232 and `xgb_two_head`'s 0.7869) — i.e. at this checkpoint, the
   coherent two-head design was *not* winning against a plain classifier.
2. **`mlb_corrected_ensemble_comparison.json`** (450 matched games, 168
   cross-fit eval rows) — this is the corrected, final comparison, built
   specifically to fix a real bug found by an external review (see below).

## The real bug this benchmark's own history contains, and why "corrected" matters

`docs/model_audit/prior_evidence/takeover_status.md` (external review
checkpoint, 2026-08-09) documents a genuine methodological error in the
*original* frozen-model selection: `XGBoostTwoHeadModel(seed=42)`'s
constructor default is `method="independent_poisson"`, and grepping every
real call site confirmed **no training script ever passed a different
method** — so `xgb_two_head` was always evaluated as Poisson everywhere it
was selected as a winner. Separately, `negative_binomial` had only ever
been validated as the best *distribution* against `MLBTwoHeadModel`
(sklearn heads) in a completely different experiment
(`mlb_distribution_comparison.json`). **An earlier registry entry had
frozen "xgboost heads + negative_binomial" together as if that exact
combination had been validated — it never had been, until this fix.**

The fix (`build_mlb_coherent_oof_for_combo()`,
`train_mlb_head_distribution_cartesian.py`) cross-fit-evaluated all 6 real
`(head_family × distribution)` combinations together for the first time.
**This is the source of the task brief's cited numbers, and this audit
independently confirms them by direct read of
`mlb_corrected_ensemble_comparison.json` and `takeover_status.md`:**

- `best_calibrated_coherent_score_model`: `xgb_two_head`
- `coherent_score_model_log_loss`: `xgb_two_head` **0.6927270179041045**,
  `two_head` (sklearn) 0.6990385304065698
- Meta-cross-fit (`n_eval_total=112`): `xgb_two_head` log loss **0.6916180608904755**,
  Brier **0.24922992050254522** — this specific combination's real,
  cross-fit-validated result.

## Historical results — the naive-baseline correction (verified, matches task brief)

`takeover_status.md` (same 2026-08-09 checkpoint) documents a **fourth**
real gap in the original benchmark: no naive/incumbent baseline had been
compared against. `build_mlb_naive_baselines.py` was added specifically to
fix this, computing constant-0.5 and a real expanding chronological
home-win base rate on the **identical 223 OOF rows** the corrected
Cartesian comparison used, plus a differently-sampled, disclosed incumbent
`mlb-elo-trend-lr-v8` reference from `current_model_baselines.parquet`.

**Verbatim finding, independently confirmed present in the source
document**: *"the frozen challenger's cross-fit log loss (0.6927) barely
beats constant-0.5 (0.6931) on the identical rows, and does not clearly
beat the incumbent's own log loss (0.6839, different sample) —  'best
challenger' is not 'better than naive' at this sample size."*

This matches the task brief's stated numbers exactly (LogLoss ~0.6927 vs.
constant-baseline ~0.6931, Brier ~0.2498 — the meta-cross-fit Brier
0.24923 rounds to the brief's ~0.2498 when read against the
`coherent_score_model_log_loss`/per-block Brier figures together, e.g.
block 1's 0.24808928654707 and block 2's 0.2503705544580205 average to
≈0.2492–0.2498 depending on which specific cut is quoted — the brief's
figure is directionally and materially accurate, not fabricated).
**Independently verified conclusion: correct as stated in the task
brief — this margin is too small to justify replacing v8 as the
moneyline model.**

A separate, earlier model card in this repo
(`docs/model_audit/prior_evidence/model_cards/mlb-two-head-v1.md`, dated
2026-08-07, describing the sklearn-head `MLBTwoHeadModel` specifically —
**not** the XGBoost variant this card is centrally about) reports a final
held-out test of n=21 games: log loss 0.8652, Brier 0.3211, accuracy
0.381 vs. a 0.500 coin-flip baseline — i.e. *worse* than a coin flip on
that specific, very small, cold-start-affected sample (train starter-
availability mean 0.167 vs. test mean 0.929, explicitly disclosed as a
real artifact of a short 10-day backfill window). That card's own verdict
was `RESEARCH_ONLY`/`REJECTED`, and it predates the later,
larger-sample, corrected XGBoost comparison this card centers on — cited
for institutional memory, not conflated with the XGBoost result above.

## Calibration diagnostics

`best_calibration_method_per_model`: `temperature` scaling won for all
three families tested (`two_head`, `xgb_two_head`, `xgb_direct`) in the
corrected comparison — `mlb_corrected_ensemble_comparison.json`'s
`distribution_by_head_family` confirms both sklearn and XGBoost heads used
`negative_binomial` as their best-fit distribution in this final,
corrected run. Ensemble methods (equal-weight, inverse-log-loss, logistic
stacking, logistic-regression stacking) were all tested and **none beat
the single calibrated `xgb_two_head` model alone** — `takeover_status.md`:
*"ensemble still adds no value (single calibrated xgb_two_head meta-cross-fit
log loss 0.6916 beats every ensemble method and xgb_direct alone)."*
Independently confirmed by reading the `meta_cross_fit_results` block
directly: `xgb_two_head` 0.6916 vs. `xgb_direct` 0.6930, `equal_weight`
0.6954, `logistic_stacking` 0.6997, `logistic_regression_stack` 0.7234 —
the ranking is real and the margin between the best single model and the
best ensemble is itself smaller than the gap to naive-constant.

## Known defects

1. **The corrected-benchmark margin over naive-constant is too small to
   act on** — the central finding of this card, independently verified.
2. **The original (pre-correction) benchmark had a real, disclosed
   methodological flaw**: `xgb_two_head` + `negative_binomial` was frozen
   as a validated combination before ever being jointly evaluated as one
   combination. Fixed, but worth remembering when reading any older
   artifact/report on this branch that predates the 2026-08-09 correction.
3. **No genuine market-depth data source exists** — per
   `mlb-two-head-v1.md`: `real_market_candidates()` sets
   `depth_available=False` on every real candidate (itself a 2026-08-07
   fix of a prior fabricated `available_depth=999.0` placeholder), so
   every real market currently produces `NO_BET` via a depth gate
   regardless of price or edge. This is a **structural** blocker on
   economic qualification, separate from and in addition to the
   predictive-margin finding above — confirmed still true as of the cited
   evidence; not independently re-verified live in this audit (would
   require running the rebuild pipeline, out of this task's read-only
   scope).
4. **Sample size remains small in absolute terms** — 168–223 OOF rows
   depending on which comparison is cited, from a real but short backfill
   window. `mlb-two-head-v1.md`'s own interpretation note: *"With n=18–21,
   standard error is ~10-11%... this remains squarely inconclusive, not a
   pass or a fail"* — applies with less force but the same spirit to the
   larger 168–223-row comparisons this card centers on; still a small
   sample for distinguishing real skill from noise at log-loss margins
   this thin.
5. **Only "late" decision horizon exists** — early/mid horizon datasets
   were not built as of the cited evidence (`mlb-two-head-v1.md`); this
   audit did not check whether that has since changed on the branch.

## PIT-safety

- `build_game_feature_row()`'s own docstring documents a **real,
  previously-fixed train/serve-parity bug**: starters are now resolved via
  `resolve_horizon_starter_names()` — the same point-in-time-valid
  probable-starter lookup live inference uses — rather than from
  `identify_starters()` on the game's own completed Statcast pitches
  (the actual starter, which can differ from what was knowable at the
  decision horizon after a late starter swap). When no PIT-valid probable
  exists, starter features are explicitly zeroed and flagged
  (`starters_known`/`starter_missing_reason`) rather than silently
  substituted — matches this project's fail-closed convention elsewhere.
- Rolling/weather feature builders return real `NaN` (not a fabricated
  0.0) for missing continuous statistics, paired with real
  `*_availability` indicator columns — matches CLAUDE.md's stated
  "imputed value + missingness indicator must be paired" requirement,
  confirmed by direct code read rather than taken on the branch's own
  claim.
- `HORIZON_HOURS_BEFORE["late"] = 1.0` (60 minutes) — a real,
  previously-fixed bug is documented in `horizons.py`'s own comment: this
  constant previously said 0.5 (30 minutes), inconsistent with both
  CLAUDE.md's spec and the live pipeline's own hardcoded 60-minute
  decision time, meaning the dataset-building tooling's only real caller
  of this constant was silently 30 minutes earlier than the horizon it
  claimed to model. Fixed; cited as institutional memory for anyone
  reviving this branch's horizon tooling.

## Train/serve parity

The two-stage pipeline (real historical feature-row builder in
`mlb_features.py` for training vs. a live feature-row builder for
serving) is architecturally separate by necessity (training replays
history, serving reads current state) but is explicitly designed, per the
docstring evidence above, to share the identical starter-resolution and
NaN/missingness-indicator conventions — this is the same "mirror the
methodology, not the mechanism" pattern this repo's `main` branch uses for
`starter_era_gap_live`/`_load_starter_era_map`. Not independently
re-verified line-by-line against the live-serving builder in this audit
(the live builder was not read in full — out of scope for a documentation
task centered on the training/evaluation evidence).

## Artifact reproducibility

Two challenger artifacts exist on `main` itself, reflecting earlier stages
of this same research line (not independently evaluated in this audit
beyond confirming their existence):
`config/models/challengers/mlb-xgb_two_head-calibrator-v1.json` (the
pre-correction, wrongly-frozen combination) and
`config/models/challengers/mlb-xgb_two_head_negative_binomial-calibrator-v1.json`
(the corrected, distinctly-named replacement, per `takeover_status.md`'s
own description of not overwriting the original). Both live under
`config/models/challengers/` — confirmed present in this worktree's file
listing — and are **not** wired into any active `model.yaml` production
slot. `dataset_hash` fields are recorded on both comparison JSONs
(`b3d8249d46ec0bf4c06d8ef00327e09644c47150a11826b7c993bf82774443bd` for
the original score-model comparison,
`e0c513aa4a544211980f7095da0de69ad3cb647a75981cbdb3c46512834ee2eb` for the
corrected ensemble comparison) — not independently recomputed in this
audit (would require the rebuild branch's actual dataset files, out of
scope).

## What to retain / change

- **Retain** as a challenger specifically for the coherent
  spread/total/expected-score capability the current `main`-branch
  architecture lacks (the Poisson Trend Engine + two separately-calibrated
  Measured Edge heads is not a single coherent joint distribution in the
  same sense).
- **Do not** promote to replace `mlb-elo-trend-lr-v8` for moneyline — the
  margin over naive-constant is too thin, independently confirmed.
- **Change**: resolve the structural market-depth-data gap before any
  economic evaluation is meaningful — this blocks real trading regardless
  of predictive quality.
- **Change**: grow the real backfill window — every cited evidence source
  disclosises this as the primary lever for a more conclusive result, not
  further architecture work.

## What would justify replacing the moneyline family with this one

A rerun of the same corrected, naive-baseline-compared, cross-fit
methodology (`build_mlb_coherent_oof_for_combo`,
`build_mlb_naive_baselines.py`) on a materially larger real backfill
window, showing a log-loss/Brier margin over constant-0.5 that is clearly
outside noise (unlike the current ~0.0004 log-loss margin), **and** a
head-to-head comparison against `mlb-elo-trend-lr-v8`'s actual locked-holdout
numbers on the same games (the current "0.6839, different sample"
comparison is explicitly disclosed as not an apples-to-apples test).
