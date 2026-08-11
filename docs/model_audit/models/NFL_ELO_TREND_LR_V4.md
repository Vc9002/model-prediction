# Model Card: `nfl-elo-trend-lr-v4`

**Model ID:** `nfl-elo-trend-lr-v4`
**Artifact:** `config/models/nfl-elo-trend-lr-v4.json`
**Config:** `config/model.yaml` → `models.NFL` (`family: elo_trend_normal_approximation`, `status: shadow_qualified`, `active_production_version: nfl-elo-trend-lr-v4`, `min_edge: 0.0`)
**Market:** NFL moneyline only (`market_type: "moneyline"`, `positive_class: "home"`)
**Method (as stored in the artifact):** `logistic_regression` — the `elo_trend_normal_approximation` label in `model.yaml` is a family/lineage tag shared with NBA/WNBA, not a claim that this artifact itself uses a normal-approximation win function; the fitted object is a 2-coefficient sklearn `LogisticRegression` over `elo_probability`/`trend_gap`, same as MLB and soccer's binary artifacts. Worth flagging as a naming inconsistency, not a functional bug — `LearnedMarketArtifact.__init__` (`src/model_prediction/models/learned_market.py:47`) hard-requires `method == "logistic_regression"` and would reject anything else.
**Date of this audit:** 2026-08-11

---

## 1. Why it exists

NFL is one of five sports (MLB, NBA, WNBA, NFL, SOCCER) running the project's shared "Elo + trend" statistical baseline: a 2-team Elo rating system (`features/elo_ratings.py::build_elo`) plus an exponentially-weighted, opponent-adjusted trend/momentum signal (`features/trends.py::TrendEngine`), fit into a logistic regression via the same chronological-split pipeline (`validation.py::build_walk_forward_rows` → `qualify_*`). It is the smallest, most robust model form the project deploys before layering sport-specific complexity — `docs/MODEL_IMPROVEMENTS.md` §9 explicitly frames NFL as small-sample and path-dependent ("NFL samples are small, games are path-dependent... Team final scores alone throw away most of the available signal"), which is exactly why the incumbent stays deliberately simple rather than starting from a richer feature set.

NFL's Elo tuning (`features/elo_ratings.py:36`) is sport-specific: `k=20.0, home_advantage=55.0, offseason_regression=0.50` (vs. e.g. NBA/WNBA's own tuned constants) — it is not literally identical math to NBA/WNBA, only the same *family* and *code path*.

## 2. Feature set

| Feature | Coefficient | Notes |
|---|---:|---|
| `elo_probability` | 2.6154014596 | Dominant signal; smallest coefficient of the 5 sports running this feature (MLB 3.319, NBA 3.564, SOCCER 5.562, WNBA 3.134, **NFL 2.615**) per `config/tested_features.json`. |
| `trend_gap` | 0.0502116986 | Small, consistent with all 5 sports (`config/tested_features.json` fitted_coefficients: MLB -0.03, NBA -0.004, SOCCER -0.151, WNBA -0.007, **NFL +0.05**) — near-zero everywhere. |
| intercept | -1.330143552 | |
| `confidence_threshold` | 0.54411292 | Learned on the validation cohort only, never the locked holdout. |

**Confirmed against the background brief**: yes, the feature set is exactly `elo_probability` + `trend_gap`, matching `docs/MODEL_IMPROVEMENTS.md` line 33/36 ("NFL remains Elo + trend gap") and `FEATURE_VARIANTS["elo_trend"]` in `validation.py:104`.

Full detail on both features (including a real feature-ablation result) is in `docs/model_audit/features/NFL.md`.

## 3. Training method

- **Split**: chronological 60/20/20, `framework: locked_complete_date_60_20_20`.
  - Coefficient fit (train): 2024-08-18 → 2025-09-11, 366 observations.
  - Threshold selection (validation): 2025-09-14 → 2025-11-17, 146 observations ("later validation cohort; never locked holdout").
  - Locked holdout: 2025-11-20 → 2026-02-08, 122 observations.
- **Minimum history**: 50 prior games league-wide before any row is built (`build_walk_forward_rows(..., minimum_history_games=50)`), plus a live-serving floor of 10 games per team (`minimum_team_history_games=10` in `learned_forward.py`).
- **Walk-forward features**: `training.walk_forward_features: true` — Elo and trend are rebuilt from only the games strictly before each decision day, never the full season.
- **Market inputs**: `market_inputs_used: false` — no market price is a training input.
- Reproduced independently: `docs/PRODUCTION_FEATURE_ABLATION_2026-07-22.md`'s NFL section reports a **PASS** reproduction gate (max coefficient delta `0`, intercept delta `+0`) refitting the exact same feature spec on `data/processed/nfl/games.jsonl` (SHA-256 `17f7961c...`), 700 raw / 700 loaded / 634 walk-forward rows.

## 4. Historical results (locked holdout)

From `config/models/nfl-elo-trend-lr-v4.json::qualification`:

| Metric | Value |
|---|---|
| Locked-holdout hit rate | 71.26% (62/87 calls) |
| Sample | 122 locked-holdout games, 87 called (`called_rate` 71.3% at `confidence_threshold=0.544`) |
| Units at -110 | +31.36u |
| Brier score | 0.204736 |
| Log loss | 0.597916 |
| `qualified` | **true** — clears the project's own automatic bar (60% min hit rate, 50 min calls) with no operator override, unlike soccer/tennis/esports which needed explicit `qualification_override` directives. |
| Monthly | Nov 2025: 22 calls, 77.3% hit, +10.45u · Dec 2025: 44 calls, 65.9% hit, +11.36u · Jan 2026: 21 calls, 76.2% hit, +9.55u — every qualifying month positive at -110. |

**Correction to a background claim**: `docs/leagues/NFL.md` (undated, "NFL research contract") currently states "the current Elo-plus-trend candidate fails the active qualification contract because November 2025 is a losing complete month with enough calls to bind." That is **not true of the current v4 artifact** — v4's November 2025 slice (2025-11-20 → 11-30, the tail of the month inside the locked-holdout window) is 22 calls at 77.3% hit rate, +10.45u, and every monthly cohort in the holdout is positive. This doc is stale — it almost certainly predates v4's promotion (archived `nfl-elo-trend-lr-v1/v2/v3.json` exist in `config/models/archive/`, and the 2026-07-20 roadmap-challenger dossier explicitly tested against `nfl-elo-trend-lr-v3`, not v4). It should be updated or removed rather than trusted as current status.

## 5. Calibration diagnostics (critical section)

**The background claim is correct and independently verified from two sources**, not just restated:

1. The artifact's own `qualification.calibration` block: `expected_calibration_error: 0.10085016...`, `calibration_slope: 1.2319`, `calibration_intercept: 0.1845`, sample size **87** (the called subset, not all 122).
2. `config/tested_features.json`'s `corrected_claims` entry (dated 2026-07-22, evidence grade A): *"NFL has the worst ECE at 0.1009"* — an explicit project-level finding, not something this audit derived on its own.

**Comparison to the other retained learned models** (same `qualification.calibration` schema, read directly from each artifact):

| Model | ECE | Sample | Brier | Cal. slope | Cal. intercept |
|---|---:|---:|---:|---:|---:|
| `nba-elo-trend-lr-v4` | 0.0605 | 577 | 0.1854 | 1.785 | -0.226 |
| `wnba-elo-trend-lr-v4` | 0.0465 | 163 | 0.2141 | 1.270 | +0.076 |
| **`nfl-elo-trend-lr-v4`** | **0.1009** | **87** | **0.2047** | **1.232** | **+0.185** |
| `mlb-elo-trend-lr-v8` | *(not computed in this artifact)* | — | — | — | — |

NFL's ECE really is roughly **1.7–2.2× worse** than NBA's and WNBA's. One caveat the background brief did not raise: **MLB's current artifact (`mlb-elo-trend-lr-v8`) simply has no `calibration` sub-object at all** — its `qualification` block only carries `brier_score`, not the slope/intercept/ECE breakdown NBA/WNBA/NFL have. So "worse than the other retained learned models" is fully supported for NBA and WNBA, but MLB cannot be directly compared on this metric from its artifact as stored — MLB's own prior audit evidence (`docs/model_audit/prior_evidence/calibration_report.md`) uses a *different* MLB model family (`mlb-two-head-v1`, not the elo-trend LR) and a different metric (log loss), so it is not a like-for-like substitute either.

**Reading the miscalibration**: `calibration_slope = 1.232 > 1` combined with `calibration_intercept = 0.185 > 0` indicates the raw probabilities are systematically shifted toward the home team beyond what the fitted logit actually supports, on top of being under-dispersed (extreme predictions are, on average, not extreme enough) — i.e. the model is directionally biased toward home, not simply "overconfident" or "underconfident" in one uniform sense. The 3 reliability buckets in the artifact (0.5–0.6, 0.6–0.7, 0.7–0.8 predicted-probability bands) show hit rates of 74.1%, 61.5%, and 85.7% against mean predicted probabilities of 56.6%, 65.3%, and 73.3% — non-monotonic, which is consistent with **small-sample noise dominating the calibration read at n=87**, not necessarily a stable structural miscalibration. This is the single most important caveat for this whole section: an ECE built from 87 points, bucketed into 3 groups of 21–39 each, has wide uncertainty on its own — the qualitative finding ("materially worse than NBA/WNBA") is credible because it's corroborated by an independent project record, but the exact 0.1009 number should not be treated as a precise, stable estimate.

**Applied calibration status**: none. `model.yaml`'s `calibration:` block (`default_method: identity`, `default_version: identity-v1`, `minimum_metric_sample: 30`) confirms the live serving path applies **no correction** — every production artifact only records calibration as a *diagnostic*, per `selection_gate.calibration_role: secondary_reporting`. This matches `config/tested_features.json`'s corrected claim #4almost verbatim: *"Every production artifact records calibration DIAGNOSTICS... What is absent is an APPLIED calibrator — the serving path uses identity."*

**The tooling to do the recommended sequencing already exists and does not need to be built from scratch.** `src/model_prediction/calibration.py` (used live) has `IdentityCalibrator`, `FixedPlattCalibrator`, `TrainablePlattCalibrator`, and `IsotonicCalibrator`. A second, more complete implementation lives in `src/model_prediction/rebuild/calibration.py` (not yet wired to production) and adds `TemperatureScaling`, plus — critically — a **chronological expanding-window cross-fit evaluator** purpose-built for exactly this task:

- `cross_fit_calibration_eval(probs, labels, method, n_blocks=4)` — for each evaluation block, fits the named calibrator (`identity`/`platt`/`isotonic`/`temperature`) only on strictly-earlier blocks, then scores it on the next block, so no calibration method ever sees the labels it is judged on. Returns per-block and pooled log loss/Brier/ECE/calibration-intercept-slope.
- This is the *identical* tool already used to produce `docs/model_audit/prior_evidence/calibration_report.md` for MLB's two-head model (compares identity/platt/temperature/isotonic in one table, picks by lowest cross-fit log loss, "identity is always a valid winner, never forced out"). That report is a ready-made template for what an NFL version should look like.

**What this audit did *not* do**: actually run `cross_fit_calibration_eval` against NFL's real OOF predictions. The stored artifact only has 87 called / 122 total locked-holdout rows and no probability+outcome log was found checked into this worktree at row-level granularity (only the aggregated `qualification` block and the 3-bucket summary) — `data/processed/nfl/games.jsonl` has the raw game outcomes but reconstructing the exact per-game predicted probability requires re-running the walk-forward feature build, which needs `scikit-learn` (this docs-only audit environment does not install it, matching the precedent in `outputs/rebuild/audit/elo_leakage_trace.py`'s own note about the same constraint). **Recommended next step, concretely**: extend `docs/PRODUCTION_FEATURE_ABLATION_2026-07-22.md`'s NFL reproduction (which already rebuilds the exact 634-row walk-forward set and matches the artifact bit-for-bit) to also emit the per-row OOF probability/outcome pairs in chronological order, then feed those into `cross_fit_calibration_eval` for identity/Platt/temperature/isotonic exactly as the MLB report did. With only ~634 rows total and 122 in the locked holdout, block counts should probably be smaller than MLB's (n=203) split — 3 blocks, not 4, to keep each fit/eval block above the calibrators' own minimum-sample floors (`PlattCalibrator` needs ≥50, `IsotonicCalibrator` ≥100/200) sanely populated. Given NFL's small n, do not expect isotonic to clear its own minimum-sample floor inside individual blocks — it will very likely fall back to identity by construction, which is itself a legitimate, informative result.

## 6. Point-in-time (PIT) safety

**Live serving is PIT-safe by direct code inspection.** `learned_forward.py::build_learned_moneyline_slate` (the shared entry point for MLB/NBA/WNBA/NFL/SOCCER) enforces two independent gates:

1. `history = store.games_before(key, game_date)` — Elo and trend are only built from games strictly before the decision date.
2. Per-event: `if start <= observed_at: raise ValueError("event_started")` — a game that has already started is never scored.

Training (`validation.py::build_walk_forward_rows`) uses the identical day-bucketing invariant: Elo/trend snapshots are rebuilt once per day from `history` accumulated strictly before that day, and that day's games are appended to `history` only *after* being scored (see the docstring in `outputs/rebuild/audit/elo_leakage_trace.py`, which traces this exact loop for the NBA sibling model — the loop is sport-generic and applies identically to NFL).

**One real, separate PIT caveat, unrelated to the live model**: the `nflverse`-sourced rebuild data foundation (`src/model_prediction/rebuild/providers/nflverse.py`, `src/model_prediction/rebuild/nfl/*`) is explicitly **not** retrospective-PIT-qualified. Its own manifest sets `retrospective_pit_qualified: False` and `production_allowed: False` unconditionally (`foundation.py:97-98`), and its docstring states the reason directly: *"nflverse releases are mutable snapshots, not historical observation logs... A season/week column never substitutes for evidence that a row was available at an earlier decision time."* `pit.py` does implement real PIT-filtering logic (`eligible_prior_team_plays`, `eligible_weekly_roster`), but it operates on data whose *provenance* the project's own code has already flagged as unusable for backtesting until a genuine day-by-day capture history accumulates. See §9 for why this matters for any future EPA/CPOE/QB-state feature work — it does **not** affect the live `nfl-elo-trend-lr-v4` model at all, which never reads from this pipeline (see §7).

## 7. Train/serve parity

Confirmed identical code path, not just a documentation claim. Both the training loop (`validation.py:74` `elo = build_elo(history, sport)`; `trends = TrendEngine(history)`) and the live serving loop (`learned_forward.py:73-75`) call the exact same `features/elo_ratings.py::build_elo` and `features/trends.py::TrendEngine` with the same sport key, and `_compute_features`'s `elo_probability`/`trend_gap` computation lines are textually identical to the ones in `ValidationRow` construction. Both pull from the same underlying data: `FeatureStore.load_games("nfl")` reads `data/processed/nfl/games.jsonl` (ESPN-sourced, per `model.yaml`'s `sport_data.provider: espn_public`), and live serving's `client.scoreboard("NFL", game_date)` is the live ESPN feed feeding that same cache. **The `nflverse` rebuild pipeline is entirely disconnected from this model** — it is a separate, not-yet-wired data foundation under `src/model_prediction/rebuild/`, with no caller anywhere in `learned_forward.py`, `validation.py`, or any test that trains/serves the live model. Nothing about `nfl-elo-trend-lr-v4` currently depends on it.

## 8. What to retain / what to change

- **Retain**: the family (Elo + EWMA trend, logistic regression), the two features, the chronological 60/20/20 split, and the walk-forward discipline. Nothing in this audit's evidence supports the model *family* or *feature set* being the problem — both features are directionally "keep" under the project's own zero-threshold policy, and `elo_probability`'s ablation p-value (0.0734) is the closest to significance of any INCONCLUSIVE NFL result found.
- **Change first, before any feature work**: apply an actual calibrator. Given the tooling already exists (§5) and the config already has a `calibration:` block wired for exactly this (`default_method`, `default_version`, `minimum_metric_sample`), this is a config-and-evaluation change, not new engineering. Test identity/Platt/temperature/isotonic via `cross_fit_calibration_eval` on NFL's real OOF predictions, select by lowest cross-fit log loss (matching the MLB precedent), and only then move to feature work — this is the same sequencing the background brief specified, and the evidence here supports it: NFL's holdout Brier (0.2047) and hit rate (71.26%) are already competitive, the diagnostic that stands out as an outlier is specifically calibration, not discrimination.
- **Do not** re-open `elo_probability`/`trend_gap` retention as if it were in question — see `docs/model_audit/features/NFL.md` for the full ablation record.

## 9. What would justify replacing the family (not just recalibrating)

1. **Calibration fix doesn't resolve it.** If a properly cross-fit calibrator (identity vs Platt vs temperature vs isotonic, per §5) still leaves NFL's held-out ECE/log loss materially worse than NBA/WNBA's *after* correction, that would suggest the miscalibration is not a simple scale/shift artifact of a 2-feature logistic fit but something more structural to how few, coarse features are being asked to carry a small, high-variance sample (NFL: ~272 games/season across 32 teams, vs. NBA's 82-game/30-team season) — in which case richer features (not just recalibration) become the load-bearing fix.
2. **A genuinely PIT-safe, walk-forward-validated feature set clears the project's own KEEP bar.** Per `docs/MODEL_IMPROVEMENTS.md` §9 and the roadmap-challenger dossier, the highest-value untested NFL features are QB identity/state, EPA/dropback, success rate, and pressure — none of which have a real historical decision-time archive today (§5's PIT caveat, and `docs/model_audit/features/NFL.md`'s entries for these). If/when that archive exists and a feature set built on it clears the same Holm-adjusted significance bar `docs/PRODUCTION_FEATURE_ABLATION_2026-07-22.md` uses (not just a favorable holdout number), that is real evidence for a richer model. At that point, the already-existing but completely dormant `src/model_prediction/rebuild/models/nfl.py::NFLModel` — a drive-based, EPA-scaled Monte Carlo score simulator (untrained, unwired, zero callers or tests anywhere in the repo; found during this audit, not previously flagged) — is a concrete candidate architecture, not something that would need to be designed from scratch. It should not be treated as evidence of progress on its own; it has never been fit to real data.
3. **Structural sample-size ceiling.** If neither (1) nor (2) resolves it — i.e. NFL's calibration and discrimination both remain the outlier among the elo-trend family even after correction and richer features — that's evidence the 2-feature LR-on-Elo approach has hit a real ceiling for this sport's sample size, and a fundamentally different estimator (e.g. partial pooling across seasons, a hierarchical/Bayesian shrinkage model, or the drive-simulation approach in point 2) would be the honest next step rather than continuing to add features to the same linear form.
