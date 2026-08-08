# Model Benchmark Report

**Date:** 2026-08-05, MLB section updated 2026-08-09 (Task 17 refresh, then corrected same day: the frozen head-family/distribution combination was never validated together — see "Correction (2026-08-09)" below)
**Status:** PARTIAL — full benchmark requires trained challenger models for all sports. MLB section below is RESEARCH_ONLY throughout; no promotion decision is made from the already-consumed final test (`outputs/rebuild/mlb_split_manifest.json`, `test_consumption_registry.json`). The frozen challenger does not yet clearly beat a naive constant-0.5 baseline on the identical rows — see the naive-baseline table below.

## Authoritative corrected result (2026-08-09)

The current MLB research benchmark is one jointly validated combination:
**XGBoost score heads + negative-binomial reconciliation + temperature
calibration, with no ensemble**. On 168 chronological cross-fit evaluations it
recorded log loss **0.6927** and Brier **0.2498**. The constant-0.5 baseline on
the corresponding 223 OOF rows recorded log loss **0.6931** and Brier
**0.2500**. That difference is negligible evidence, not a meaningful edge.

**Predictive qualification: NOT ESTABLISHED. Economic qualification: NOT
ESTABLISHED.** `mlb_moneyline_v2` remains an unconsumed prospective test; its
aggregate accuracy, log loss, Brier, ROI, and CLV must remain sealed until
deliberate consumption under the registry policy.

All earlier MLB benchmark sections below are **SUPERSEDED — RETAINED FOR AUDIT
HISTORY** where they disagree with this jointly validated combination. They are
not promotion evidence.

## Production model baselines

These are the incumbent models from the existing `main` branch, frozen as controls:

| Sport | Model | Features | Qualified | Key metric |
|-------|-------|----------|-----------|------------|
| MLB | elo-trend-lr-v8 | Elo, trend, park, weather, starter ERA, bullpen | No (Brier regression) | — |
| NBA | elo-trend-lr-v4 | Elo, offensive trend, defensive trend | No | Cal slope ~1.79 |
| WNBA | elo-trend-lr-v4 | Elo, offensive trend, defensive trend, availability | No | Threshold ~0.50 |
| NFL | elo-trend-lr-v4 | Elo, score trend | No | 122 games, ECE ~0.10 |
| Soccer | poisson-dc-v1 | EWMA goals, fixed home 1.15, fixed rho -0.10 | No | — |
| Tennis | surface-elo-v1 | Surface Elo, overall Elo, fixed 60/40 blend | No | Fixed K=32 |
| LoL | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| CS2 | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| Dota 2 | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| Valorant | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| R6 | tiered-elo-v6 | Neutral Elo + Platt | No | Organization-based |
| KBO | tie-aware-elo-v1 | Home Elo + tie heuristic | No | No pitcher/lineup |
| NPB | tie-aware-elo-v1 | Home Elo + tie heuristic | No | No pitcher/lineup |

## Rebuild challenger baselines

Superseded 2026-08-08: the row below (real Statcast-derived features,
`config/models/challengers/mlb-two-head-real-features-v1.json`) replaces
the prior placeholder row, which used rolling scoreboard averages, not
real pitcher/bullpen/park/weather features. Real held-out test (21
games, chronologically after training+calibration, never inspected while
selecting features/model family):

| Sport | Model | Features | Status | Brier | ECE | LogLoss | n (test) |
|-------|-------|----------|--------|-------|-----|---------|----------|
| MLB | two-head-real-features-v1 | Real Statcast starter/bullpen + park + weather | RESEARCH_ONLY | 0.321 | 0.264 | 0.865 | 21 |

**Real, disclosed reading:** this is worse than the superseded
placeholder row's numbers on this specific 21-game held-out set — not a
regression in the model, but the real held-out set's own real
cold-start composition mismatch (`outputs/rebuild/mlb_training_results_real_features.json`:
train mean starter-availability 0.167 vs test mean 0.929 — the short
~10-day real backfill window means most *training* rows have no real
prior-start history for their starter, while the *test* rows mostly do,
a genuinely different feature-availability regime between the two
splits caused by backfill depth, not a modeling error). More real
backfill days is the actual fix, not further feature engineering — see
`outputs/rebuild/FOUNDATION_FROZEN.md`'s known blockers.

### Part 2 refresh (Task 17, 2026-08-09): real chronological OOF, 435 matched games, dataset_hash `b3d8249d46ec...`, real games 2026-07-01 to 2026-08-07

Everything in this section is out-of-fold (never fit-and-scored on the same
rows), chronological (3 expanding folds, 35 distinct real dates, never
random K-fold), and deliberately separate from the already-consumed final
test above (n=21, `2026-08-02T20:05Z`–`2026-08-04T23:40Z`, `consumed=true`
in `test_consumption_registry.json`) — none of this section's model-family,
calibration, or ensemble selection ever inspected that held-out range.
Source files: `mlb_distribution_comparison.json`, `mlb_score_model_comparison.json`,
`mlb_calibration_comparison.json`, `mlb_calibrated_ensemble_comparison.json`,
`mlb_uncertainty_demo.json` — all share the identical `dataset_hash` above.

#### Moneyline — raw (uncalibrated), 203 OOF predictions

| Model family | LogLoss | Brier | Coherent score distribution? |
|---|---|---|---|
| `two_head` (independent Poisson, control) | 0.8232 | 0.2819 | Yes |
| `xgb_two_head` (coherent XGBoost score heads) | 0.7869 | 0.2816 | Yes |
| `xgb_direct` (direct XGBoost binary classifier) | 0.7221 | 0.2641 | **No — moneyline only, no spread/total** |

Real, disclosed reading: `xgb_direct` wins on raw log loss/Brier at this
sample size, consistent with the 2026-08-08 finding this table replaces.
Per CLAUDE.md's architecture rule, `xgb_direct` remains an independent
moneyline challenger — it must never silently generate spread/total, and
does not (see the two rows below, which exclude it by construction).

#### Score distribution family (Poisson vs. negative binomial vs. Skellam), same 203 OOF predictions

| Distribution | LogLoss | Brier | ECE |
|---|---|---|---|
| Independent Poisson | 0.8232 | 0.2819 | 0.1270 |
| **Negative binomial (best by OOF log loss)** | **0.7580** | **0.2692** | 0.1144 |
| Skellam (exact closed-form margin) | 0.8219 | 0.2818 | 0.1420 |

#### Totals — predeclared line grid `[7.5, 8.0, 8.5, 9.0, 9.5]` (Task 13.5 fix: never derived from the realized total), `two_head` vs. `xgb_two_head` only — `xgb_direct` has no totals probability by design

| Model | Line | n | n push (real) | mean(over) | LogLoss (non-push) |
|---|---|---|---|---|---|
| two_head | 7.5 | 203 | 0 | 0.747 | 0.8492 |
| two_head | 8.0 | 203 | 22 | 0.687 | 0.8295 |
| two_head | 8.5 | 203 | 0 | 0.628 | 0.7937 |
| two_head | 9.0 | 203 | 11 | 0.564 | 0.7439 |
| two_head | 9.5 | 203 | 0 | 0.501 | 0.6941 |
| xgb_two_head | 7.5 | 203 | 0 | 0.707 | 0.9566 |
| xgb_two_head | 8.0 | 203 | 22 | 0.658 | 0.9446 |
| xgb_two_head | 8.5 | 203 | 0 | 0.608 | 0.9630 |
| xgb_two_head | 9.0 | 203 | 11 | 0.558 | 0.9117 |
| xgb_two_head | 9.5 | 203 | 0 | 0.507 | 0.8660 |

`two_head` beats `xgb_two_head` on every totals line here — the reverse
ranking from moneyline, a real, disclosed model-family disagreement, not
an error.

#### Spread — predeclared signed home-line grid `[-2.5, -1.5, -0.5, 0.5, 1.5, 2.5]` (Task 17 fix: `spread_market_breakdown()` closes the same push-folding gap Task 13.5 closed for totals), same two coherent models only

| Model | Home line | n | n push (real) | mean(home) | LogLoss (non-push) |
|---|---|---|---|---|---|
| two_head | -2.5 | 203 | 0 | 0.192 | 0.6009 |
| two_head | -1.5 | 203 | 0 | 0.283 | 0.6971 |
| two_head | -0.5 | 203 | 0 | 0.391 | 0.8491 |
| two_head | +0.5 | 203 | 0 | 0.509 | 0.8186 |
| two_head | +1.5 | 203 | 0 | 0.626 | 0.8960 |
| two_head | +2.5 | 203 | 0 | 0.731 | 0.7903 |
| xgb_two_head | -2.5 | 203 | 0 | 0.237 | 0.6847 |
| xgb_two_head | -1.5 | 203 | 0 | 0.329 | 0.7428 |
| xgb_two_head | -0.5 | 203 | 0 | 0.434 | 0.8008 |
| xgb_two_head | +0.5 | 203 | 0 | 0.546 | 0.7957 |
| xgb_two_head | +1.5 | 203 | 0 | 0.655 | 0.7963 |
| xgb_two_head | +2.5 | 203 | 0 | 0.751 | 0.7242 |

Real push probability is 0 at every line in this grid by construction — the
grid is all half-integer (the real MLB run-line convention), and MLB run
margins are always integers, so no line here can ever push. Push is still
computed and reported explicitly (not assumed 0), so a future whole-integer
line would be priced correctly too.

#### Calibration — cross-fitted (4 chronological blocks, fit on strictly earlier blocks, scored on a later block never seen by the fit), best method selected by OOF log loss only

| Model | Best method | Cross-fit LogLoss | Cross-fit Brier | Cross-fit ECE | Cal. intercept | Cal. slope | n (eval) |
|---|---|---|---|---|---|---|---|
| two_head | temperature | 0.7228 | 0.2633 | 0.0823 | -0.012 | -0.687 | 153 |
| xgb_two_head | temperature | 0.6964 | 0.2515 | 0.0165 | 0.014 | 0.064 | 153 |
| xgb_direct | temperature | 0.7016 | 0.2542 | 0.0441 | 0.018 | -1.164 | 153 |

Temperature scaling won for all three real model families on this sample —
not assumed, each of identity/Platt/temperature/isotonic was independently
cross-fit-evaluated per model (`mlb_calibration_comparison.json`'s
`cross_fit_results`). Calibrator artifacts persisted independently from the
base model, each bound by hash: `config/models/challengers/mlb-{two_head,xgb_two_head,xgb_direct}-calibrator-v1.json`.
Cohort calibration (starter-availability, weather) was computed as a
diagnostic only — no separate per-cohort calibrator was selected, per
CLAUDE.md's "do not select separate calibrators per tiny cohort."

#### Calibrated ensemble — meta-cross-fit (3 chronological blocks), on the calibrated OOF predictions above

| Method | Meta-cross-fit LogLoss | Meta-cross-fit Brier | n (eval) |
|---|---|---|---|
| **`xgb_two_head` alone (best single calibrated coherent model)** | **0.6950** | **0.2509** | 102 |
| `xgb_direct` alone | 0.7098 | 0.2582 | 102 |
| Equal-weight ensemble | 0.7003 | 0.2536 | 102 |
| Inverse-log-loss weighted | 0.7005 | 0.2537 | 102 |
| Logistic stacking (nonneg., no intercept) | 0.7143 | 0.2605 | 102 |
| Logistic regression stack (unconstrained) | 1.0507 | 0.3427 | 102 |

Real, disclosed reading: **the ensemble adds no value here.** Every
ensemble method's meta-cross-fit log loss is worse than (or, for
inverse-log-loss, statistically indistinguishable from) the single best
calibrated coherent model, `xgb_two_head`, alone. The full-history logistic
stack independently collapsed to `{"two_head": 0.0, "xgb_two_head": 1.0,
"xgb_direct": ~0.0}` — the same `two_head=0, xgboost=1` collapse pattern
flagged in advance. Per the explicit instruction governing this
comparison: report plainly that ensembling adds no value on this sample,
and use the single calibrated `xgb_two_head` coherent model as the
moneyline research benchmark instead of a constructed ensemble. Note this
differs from the raw (uncalibrated) moneyline table above, where
`xgb_direct` led — calibration changed the ranking, which is exactly why
Task 15 (ensemble) was ordered after Task 14 (calibration), not before.

#### Uncertainty decomposition — live demonstration, last real fold's validation block, 76 games

| Component | Mean value |
|---|---|
| Model-family disagreement (`two_head` vs. `xgb_two_head`) | 0.2238 |
| Calibration uncertainty (bootstrap of the calibration fit) | 0.0106 |
| Missingness penalty | 0.0271 |
| **Total mean haircut from raw to conservative probability** | **0.2993** |

Lineup uncertainty is `unavailable` (`None`) for every game — no real
timestamp-valid lineup source exists yet, never fabricated. This is a
demonstration that `uncertainty.py`'s full decomposition runs end to end on
real predictions (`outputs/rebuild/mlb_uncertainty_demo.json`), not yet
wired into the live shadow pipeline's `build_forecast()` (disclosed gap:
that requires loading and predicting with all three model families at
live-prediction time, which isn't currently wired there).

### Correction (2026-08-09): the frozen v2 model was never validated as one exact combination

A real bug was found in the Task 17 refresh above and Task 18's registry
freeze: `xgb_two_head`'s "best calibrated coherent model" result (used
throughout the sections above) was always constructed as
`XGBoostTwoHeadModel(seed=42)` — its constructor default distribution is
`independent_poisson` — at every real call site
(`train_mlb_score_model_comparison.py`, `train_mlb_calibration_comparison.py`,
`train_mlb_calibrated_ensemble_comparison.py`). Meanwhile `negative_binomial`
was validated as the best distribution only against `MLBTwoHeadModel`
(sklearn heads), in the separate score-distribution-family comparison
above. `mlb_moneyline_v2`'s frozen definition combined these two winners
from different experiments — `xgboost heads + negative_binomial` had
never actually been fit and OOF-scored together anywhere.

**Fix**: `train_mlb_head_distribution_cartesian.py` fits and cross-fit
calibrates all 6 real combinations (2 head families × 3 distributions),
picking the winner as one exact combination:

| Head family | Distribution | Best calibration | Cross-fit LogLoss | Cross-fit Brier | Cross-fit ECE |
|---|---|---|---|---|---|
| sklearn | independent_poisson | temperature | 0.6992 | — | — |
| sklearn | negative_binomial | temperature | 0.6990 | — | — |
| sklearn | skellam | temperature | 0.6993 | — | — |
| xgboost | independent_poisson | temperature | 0.6933 | — | — |
| **xgboost** | **negative_binomial** | **temperature** | **0.6927** | **0.2498** | **0.0326** |
| xgboost | skellam | temperature | 0.6933 | — | — |

`xgboost + negative_binomial` does turn out to be the real winner (n=223
OOF, n=168 cross-fit eval, 450 matched games as of this refreshed run) —
but it is now actually validated as one combination, not assembled after
the fact. The registry's `mlb_moneyline_v2` entry has been corrected
in place (superseded, not silently overwritten — see its own
`correction_note`), including moving `test_start` forward to strictly
after the real games this correction's own comparisons used for
selection.

`train_mlb_corrected_ensemble_comparison.py` re-ran the Task 15 ensemble
comparison using each head family's own real best distribution instead of
its constructor default. Conclusion unchanged: every ensemble method
still scores worse than or indistinguishable from the single best
calibrated model alone (`xgb_two_head` meta-cross-fit log loss 0.6916 vs.
`xgb_direct` 0.6930, equal-weight 0.6954, inverse-log-loss 0.6955,
logistic-stacking 0.6997) — the single calibrated `xgb_two_head` model
remains the moneyline research benchmark.

#### Naive and incumbent baselines (real, added to close a gap: "beats another challenger" was never checked against "beats a naive forecast")

Computed on the identical 223 real OOF rows the corrected Cartesian
comparison uses (`build_mlb_naive_baselines.py`):

| Baseline | n | LogLoss | Brier |
|---|---|---|---|
| Constant 0.5 | 223 | 0.6931 | 0.2500 |
| Expanding historical home-win base rate (real, chronological, no leakage) | 223 | 0.6944 | 0.2506 |
| **Frozen `xgb_two_head` (xgboost + negative_binomial + temperature), cross-fit** | **168** | **0.6927** | **0.2498** |
| Incumbent `elo-trend-lr-v8` (real, but a DIFFERENT n=67 sample/date range — not row-identical to the above) | 67 | 0.6839 | 0.2455 |

**Real, disclosed reading**: the frozen challenger's real cross-fit log
loss (0.6927) barely beats a constant-0.5 baseline (0.6931) on the
identical rows — not yet a real discriminative edge at this sample size.
It also does not clearly beat the incumbent's own real log loss (0.6839),
though that number comes from a different, smaller, non-overlapping real
sample and is not a strict apples-to-apples comparison. "Best challenger"
in this document should never be read as "better than a naive forecast"
or "better than the incumbent" — neither claim is supported yet. A real
timestamp-valid market no-vig probability baseline is not included: no
real market quote is currently linked to these exact OOF games (the same
real gap disclosed in `economic_report.md`).

**Note:** Only MLB has real rebuild challengers. All other sports are pending collector completion.

## Common characteristics across all models

Still true for every sport except the MLB rebuild challenger row above
(noted inline where MLB is now a real exception, as of 2026-08-08):

1. **Thin features**: Most models use 2-7 features, predominantly Elo-based
2. **No player-level data**: No pitcher quality, lineup strength, player availability (except WNBA and MLB's rebuild challenger, which has real Statcast starter/bullpen/clean-rate features)
3. **No market-residual model**: Sports probability and market price are not modeled separately (a real `MarketResidualModel` now exists for the rebuild platform generally — `market_residual.py`, unit-tested `daa6985` — but has never been trained on real settled outcomes; see `economic_report.md`)
4. **No out-of-fold calibration**: Probability estimates are uncalibrated or calibrated on training data (MLB's rebuild challenger uses real chronological cross-fitted calibration across 4 methods -- identity/Platt/temperature/isotonic -- selected by OOF log loss, never fit on the rows it's scored on)
5. **Hypothetical economics**: P&L uses -110 default odds when executable quotes are unavailable (the rebuild platform's real decision engine never does this -- it fails closed on a stale/thin quote instead; see `economic_report.md`'s real reason-code breakdown)
6. **Unified scoring**: All models score moneyline only (except MLB with spread/total)

## Spec-required challenger types (Part 2-C)

For each sport, the spec requires:

| # | Type | MLB | NBA | WNBA | NFL | Soccer | Tennis | Esports |
|---|---|-----|-----|------|-----|--------|--------|---------|
| 1 | Transparent control | ✓ (incumbent) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 2 | Sport-specific statistical | ✓ (independent Poisson / negative binomial / Skellam, `models/__init__.py`'s `JointScoreDistribution`) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 3 | Regularized linear | ✓ (`ElasticNet`, `RunDifferentialHead`) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 4 | Nonlinear sklearn | ✓ (`HistGradientBoostingRegressor`, `RunIntensityHead`) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 5 | XGBoost | ✓ (`XGBoostChallenger`, wired to real OOF data 2026-08-08) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 6 | Out-of-fold ensemble | ✓ (`Ensemble`: equal-weight, inverse-log-loss, nonneg. logistic stacking, unconstrained logistic regression — all meta-cross-fit chronologically 2026-08-09; real finding: none beats the single best calibrated model on this sample) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 7 | Independently fitted calibrator | ✓ (identity/Platt/temperature/isotonic, cross-fit-compared per model 2026-08-09; temperature won for all three MLB model families; artifact hash-bound separately from the base model) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |

Rows 2-4 were architecturally present before 2026-08-08 (the two-head
model was already built on ElasticNet + HistGradientBoostingRegressor +
a Poisson/NegBinom joint distribution) but are marked ✓ here for the
first time because that session also added Skellam (closing gap 2) and,
separately, found and fixed a real bug where the negative-binomial
method produced internally inconsistent moneyline-vs-total pricing
(`9fbb037`) — the statistical-distribution row wasn't honestly
verifiable as working until that was fixed and tested.

## Verdict

The incumbent models serve as valid controls (requirement #1 for every
sport). MLB is the only sport with requirements #2-7 completed — real,
tested, and wired to live data, not just present in source, and as of
2026-08-09 also includes real cross-fitted calibration and a real
meta-cross-fit ensemble comparison. No other sport has begun this work
(correct per CLAUDE.md's own MLB-first sequencing). This remains an
architecture-completeness and small-sample-directional verdict only:
MLB's real held-out test above (n=21) does not clear a predictive
qualification gate on this small a sample, the real OOF comparisons
above (n=203, n=153, n=102 depending on stage) are directional, and no
sport has attempted economic qualification (see
`outputs/rebuild/economic_report.md`: real shadow ledger data shows zero
real BET decisions have ever been placed — 192 real settlements now
exist for NO_BET/research rows, but zero carry a real closing price yet,
an honest data-timing blocker, not a missing-code one).

Critically, the frozen `mlb_moneyline_v2` challenger's own real cross-fit
log loss (0.6927) barely beats a constant-0.5 naive baseline (0.6931) on
the identical rows, and does not clearly beat the incumbent's real log
loss (0.6839, different sample). "Architecture-completeness" and
"predictively qualified" are not the same claim, and this document does
not make the second one.

`outputs/rebuild/model_benchmark.parquet` (this document's machine-readable
counterpart) exists as of 2026-08-09 with one real row per MLB
model/market/line combination above, plus real naive/incumbent baseline
rows. It cannot be extended to other sports until challenger models are
trained for at least one sport beyond MLB.
