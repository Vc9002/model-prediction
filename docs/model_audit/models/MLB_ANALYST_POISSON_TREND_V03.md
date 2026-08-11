# Model card: `mlb-analyst-poisson-trend-v0.3`

Status recommendation: **WORKING / HISTORICAL_CONTROL** — incumbent
spread/total score-simulation engine, retained as a control. Feeds two
downstream calibrated markets, `measured-edge-margin-v3` (spread) and
`measured-edge-totals-v3` (total), both verified in this audit. All
findings below verified directly against
`config/models/mlb-analyst-poisson-trend-v0.3.yaml`,
`config/models/measured-edge-margin-v3.json`,
`config/models/measured-edge-totals-v3.json`, and
`src/model_prediction/models/mlb.py` in this worktree on 2026-08-11.

## Why it exists

The project's non-moneyline MLB engine: a Poisson run-scoring simulation
("Trend Engine") that produces a joint away/home score distribution, from
which moneyline, spread, and total probabilities are all *derived from the
same simulated games* rather than fit independently — "one coherent
simulation, three markets," in contrast to the elo_trend_lr family's
market-specific logistic regressions. `ENGINE_VERSION = "mlb-analyst-poisson-trend-v0.3"`
in `src/model_prediction/models/mlb.py`; the formula spec is versioned
separately in YAML and self-hash-checked (`configuration_hash`) at load
time.

## Market(s) predicted

- **Spread** (`MeasuredEdgeMarginModel` / `measured-edge-margin-v3`) — real, sized Main-ledger rows.
- **Total** (`MeasuredEdgeTotalsModel` / `measured-edge-totals-v3`) — real, sized Main-ledger rows.
- Moneyline is also derivable from the same simulation
  (`derive_market_distribution(..., MarketType.MONEYLINE)`) but this
  head is not the active moneyline production model — `mlb-elo-trend-lr-v8`
  is (see that card).

## Feature set

Not a fitted-coefficient linear model — a parametric simulation with fitted
**elasticities** governing how each input multiplies expected runs.
Confirmed by reading `estimate_runs()` in `src/model_prediction/models/mlb.py`
and the current YAML spec:

| Input | Elasticity (fitted) | Role |
|---|---|---|
| Team offense index (`_offense_index`) | `offense_elasticity = 0.088237` | EWM-shrunk runs-scored, half-life 10 games |
| Starter weakness (`_starter_weakness`) | `starter_weakness_elasticity = 0.281107` | Credibility-shrunk season+recent ERA blended with K%/BB% discipline adjustment |
| Bullpen weakness (`away_bullpen_weakness`/`home_bullpen_weakness`) | `bullpen_elasticity = 0.069018` | Same `features/bullpen.py::bullpen_profile` index as v7/v8's `bullpen_weakness_gap` |
| Park factor | `park_elasticity = 0.259048` | Same static table as `elo_trend_lr`'s `park_factor` (`features/park_factors.py`) |
| Weather factor | `weather_elasticity = 0.040295` | Same live/historical split as `elo_trend_lr`'s `weather_factor` |
| Home/away field run factor | fixed, not fit | `home_field_run_factor = 1.04`, `away_field_run_factor = 1.0` |

Also uses `starter_season_weight`/`starter_recent_weight` (0.3/0.7 blend),
`strikeout_weight`/`walk_weight` (0.15/0.10) for the starter-discipline
adjustment, and simulation-noise parameters (`shared_environment_variance
= 0.06`, `team_specific_variance = 0.12`, `simulations = 20000`).

**Real, disclosed constraint on `bullpen_elasticity`**: the YAML's own
`_refit_note` states this coefficient is *forced to 0.0* unless a
bullpen-included fit shows a positive coefficient in every one of 4
chronological expanding-window folds — this run's real result was
`0.069018`, i.e. it passed that bar. This is a meaningfully more
conservative gate than a plain single-fit coefficient — worth noting as a
real methodological strength distinct from `elo_trend_lr`'s simpler
single-fit logistic coefficients.

## Training method

`_refit_note` (YAML, verified verbatim): *"Refit 2026-07 by
scripts/mlb_elasticity_refit.py against 1136 real completed games... Held-out
correlation by chronological expanding-window fold: fold0=0.1222,
fold1=0.1623, fold2=0.1046, fold3=0.1139."* Poisson regression fit,
4-fold chronological expanding-window validation. This is the *engine's*
own fit; the two Measured Edge heads (margin/totals) layer a **separate,
independent calibration** on top (see below) — this is a two-stage
pipeline, not one end-to-end fit.

Held-out correlation across the 4 folds is modest and inconsistent
(0.10–0.16) — the engine's own raw predictive signal, before calibration,
is weak on its own terms; this is corroborated by the downstream
calibration correlations below.

## Threshold selection

Not a classification threshold in the `elo_trend_lr` sense — this is a
regression/simulation engine. The relevant analog is each Measured Edge
head's **calibration scale/offset**, fit by OLS of the raw simulated cover
probability against real outcomes (`scripts/mlb_measured_edge_calibrate.py`):

- **Margin** (spread): `scale=0.6814`, `offset=0.1593` (primary/diagnostic
  fit, 290 games); `scale=0.7458`, `offset=0.1271` (real-market corroboration,
  65 games).
- **Totals**: `scale=0.2484`, `offset=0.3816` (primary/diagnostic, 284 games);
  `scale=0.2296`, `offset=0.3852` (real-market corroboration, 65 games).

Both `MeasuredEdgeMarginModel`/`MeasuredEdgeTotalsModel.__init__` enforce a
governance gate on load: `0 < scale <= 1.5` and `0.35 <= scale*0.5 + offset <= 0.65`
(no large systematic bias at a raw-coinflip input) — verified in code,
`src/model_prediction/models/mlb.py:471-477` and `:557-563`. This is a real
safety check that would reject a badly-fit calibration artifact at load
time, not just at training time.

## Historical results

Directly from each artifact's `calibration_note`/`calibration_windows`,
self-hash-verified:

**Margin (spread)**:
- Diagnostic (290 games, ESPN postgame pickcenter reconstruction,
  `timestamp_valid=False`, explicitly disclosed as *not* real historical
  Polymarket lines): correlation 0.208, flat -110: 285 picks, 60.0% hit
  rate, +41.45 units.
- Real-market corroboration (genuine captured Polymarket BBO, 65 games):
  correlation 0.2197, flat -110: 63 picks, 63.49% hit rate, +13.36 units.

**Totals**:
- Diagnostic (284 games): correlation **0.0414** — much weaker than margin.
  Flat -110: 68 picks, 52.94% hit rate, +0.73 units — essentially breakeven.
- Real-market corroboration (65 games): correlation 0.0314, flat -110:
  only 8 picks, 37.5% hit rate, **-2.27 units** — a real, disclosed losing
  result on real market data, small sample.

`docs/PROJECT_STATUS.md` (2026-08-04 snapshot, cited for context, not
re-verified independently in this audit beyond the artifact numbers above)
corroborates this asymmetry: margin's 2026-08-04 elasticity refit (F-62)
improved diagnostic correlation 0.2057→0.208 and hit rate 59.5%→60.0%,
while the same refit made totals *worse* (correlation 0.0585→0.0414, hit
rate 55.3%→52.9%) — and states the standing diagnosis is that totals needs
"an absolute-run-environment signal, not better relative elasticities."
This audit did not re-run that refit; citing it as prior-session evidence,
not independently reproduced.

**Bottom line**: margin/spread shows real, modest, consistently positive
signal across both the diagnostic and (much smaller) real-market sample.
Totals shows weak-to-negative signal, worst on the smallest, most credible
(real-market) sample — the two markets should not be evaluated as a single
"Measured Edge is working" claim.

## Calibration diagnostics

The `calibration_windows` block itself **is** the calibration diagnostic
for this model family — there is no separate Brier/log-loss/ECE block on
either the margin or totals artifact (confirmed by reading both JSON files
in full; no `calibration.brier_score` or `expected_calibration_error` key
exists on these artifacts, unlike `mlb-elo-trend-lr-v7`'s). The
`calibration_method` is `"flat_probability_shrinkage_toward_half"` — a
simple linear shrink (`calibrated = scale * raw + offset`), not a Platt/
isotonic fit against a proper scoring rule. `calibrate_selected_side()`
explicitly re-validates the output is inside `(0, 1)` at call time and
raises rather than silently returning an out-of-range "probability" — a
real, verified fail-closed guard (`src/model_prediction/models/mlb.py:506-522`).

## Known defects

1. **Totals calibration is weak-to-negative on the smallest, most credible
   sample** (real-market: -2.27 units, 37.5% hit rate over 8 picks) — see
   Historical results. `docs/PROJECT_STATUS.md`'s standing diagnosis
   (needs an absolute run-environment signal, not relative elasticities)
   is the current unresolved hypothesis; this audit did not test it.
2. **No Brier/log-loss/ECE-style calibration diagnostic recorded on either
   artifact** — only correlation and flat -110 hit-rate/units. A materially
   thinner evidence record than `mlb-elo-trend-lr-v7`'s.
3. **Diagnostic-vs-real-market sample size gap** — the primary
   scale/offset fit used for live serving (`offset`/`scale` top-level
   fields, confirmed equal to the `diagnostic` window's values on both
   artifacts, not the `real_market` window's) is fit on the larger but
   explicitly disclosed **not-real-Polymarket-lines** ESPN-postgame-reconstruction
   dataset (290/284 games), with the smaller, genuinely real-market
   65-game sample serving only as corroboration, not as the fitted values
   actually shipped. This is disclosed honestly in each artifact's own
   `calibration_note`, but worth restating plainly: **the live serving
   calibration is not fit on real captured market data.**
4. **`park_factor`/`weather_factor` PIT-provenance gaps are inherited
   from the same shared feature modules `elo_trend_lr` uses** — see the
   feature doc. This model shares those two blocked-PIT inputs.

## PIT-safety

- Team offense/starter-weakness/bullpen-weakness inputs: PIT-safe, same
  underlying rolling/credibility-shrunk providers as `elo_trend_lr`'s
  `bullpen_weakness_gap` and (for starter weakness — note this is a
  *different* quantity than `starter_era_gap`, an ERA/K%/BB%-blended
  index, not a raw ERA gap) analogous rolling design.
- `park_factor`/`weather_factor`: same blocked-PIT status as documented in
  the feature doc — shared modules, shared defect.
- Simulation seeding (`stable_seed`) is deterministic given
  `(event_id, formula_version, decision_timestamp_utc, market_snapshot_hash,
  feature_snapshot_hash, seed_namespace)` — confirmed reproducible, not a
  PIT concern but worth noting for reproducibility.

## Train/serve parity

`estimate_runs()`/`simulate_game()` are the single code path for both
training-time backtest and live serving (no separate training-only
replay function was found for the score-simulation engine itself, unlike
`elo_trend_lr`'s starter/bullpen features) — real parity by construction
for the engine. The two Measured Edge calibration heads
(`MeasuredEdgeMarginModel`/`MeasuredEdgeTotalsModel`) load their
scale/offset from the versioned JSON artifacts and apply the identical
`calibrate_selected_side()` function at both training-evaluation and live
serving time — confirmed by reading the single shared implementation.

## Artifact reproducibility

All three artifacts self-hash-verified in this audit
(`sha256` over the canonical JSON minus `artifact_hash`, matching the
stored field exactly):

- `mlb-analyst-poisson-trend-v0.3.yaml`: hashed via `configuration_hash`
  at `load_formula_spec()` time (not a static stored hash in the YAML
  itself — computed from the raw file bytes on every load) — confirmed by
  reading the loader code, not independently recomputed in this audit
  since it requires running Python against the loader, out of this
  documentation-only task's scope.
- `measured-edge-margin-v3.json`: hash match confirmed
  (`dd339a2eed50d7d83ab2127837995ddab82e879afd27f5414bb6c1f9cd60ba4c`).
- `measured-edge-totals-v3.json`: hash match confirmed
  (`95b84332c75d2ce54036eb8de6ada9c78d0f80228c50825c0347281a8b9b49c7`).

Unlike `mlb-elo-trend-lr-v8.json`, `git log --oneline` for all three of
these files shows a clean, single-branch history on the current worktree's
`HEAD` (826c893) — `d63f163` ("fix: promote MLB trend engine elasticity
refit (P1-17), fix calibration script bug (F-62)") is the most recent
commit touching the margin/totals artifacts, and it **is** an ancestor of
current `HEAD` (confirmed via `git status --porcelain` showing no
uncommitted diff and no divergent-history pattern like v8's). No
threshold/state divergence of the kind found on `mlb-elo-trend-lr-v8` was
found here.

## What to retain / change

- **Retain** the spread/margin side as-is — real, modest, consistent
  positive signal on both the larger diagnostic sample and the smaller
  real-market corroboration sample.
- **Change**: do not treat totals as production-ready on current evidence
  — the real-market sample is small but consistently the worst-performing
  cut (-2.27 units). Either build the "absolute run-environment signal"
  `docs/PROJECT_STATUS.md` already diagnoses as missing, or gate totals
  more conservatively than spread until real-market evidence improves.
  This audit did not evaluate what that signal should be — out of scope.
- **Change**: add a proper-scoring-rule calibration diagnostic (Brier/log
  loss/ECE/reliability buckets) to both Measured Edge artifacts, matching
  the standard already set by `mlb-elo-trend-lr-v7`.
- **Change**: grow the real-market (genuine Polymarket BBO) calibration
  sample — the live-serving `scale`/`offset` values are currently fit on
  ESPN-postgame-reconstructed data, not real captured market lines; this
  is disclosed but should not be a permanent state.

## What would justify replacing this family

For **spread**: a challenger that beats 0.2197 real-market correlation and
63.49% real-market hit rate (65 games) without narrowing the sample below
what's currently available, plus adds a real calibration diagnostic this
family currently lacks.

For **totals**: almost any credible signal — the current real-market
result (-2.27 units, 8 picks) is close to the bar a naive constant-line
predictor would clear; a legitimate absolute-run-environment feature
(rather than another elasticity refit of existing relative factors, which
`docs/PROJECT_STATUS.md` reports was already tried and made totals worse)
is the standing recommendation.

The clean-slate two-head model (`MLB_CLEAN_SLATE_TWO_HEAD.md`) is a
structurally different, coherent joint-distribution approach that derives
moneyline/spread/total from one model rather than a separate engine +
two independently-calibrated heads. It is not yet validated well enough
(see that card) to replace either side of this family today, but its
architecture is the most direct path to the "coherent expected-score"
capability this project has flagged as valuable for exactly this
spread/total gap — see the Poisson Trend Engine's own weak per-fold
held-out correlation (0.10–0.16) as the baseline a coherent joint model
would need to beat.
