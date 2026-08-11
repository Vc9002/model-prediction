# Feature Audit: WNBA

Covers every feature currently wired into, or archived as a candidate for,
WNBA models: the three trained `wnba-elo-trend-lr-v4` features
(`elo_probability`, `trend_gap`, `defensive_trend_gap`), the serve-time
availability adjustment (`availability_points_gap` and its sibling
outputs), and the archived Four-Factors efficiency features from
`origin/rebuild/wnba-v1` (ORtg/DRtg/NetRtg/pace/eFG/TOV%/ORB%/FT-rate) as
research candidates. Verdicts use the fixed enum: KEEP, KEEP_CORE,
KEEP_BASELINE, KEEP_RESEARCH_ONLY, RETEST_REQUIRED,
REBUILD_IMPLEMENTATION, BLOCKED_PIT, REMOVE, REJECT, SUPERSEDED.

**Audit date:** 2026-08-11. **Repo state:** `audit/model-feature-
reconciliation-v1` @ `826c893` (origin/main), cross-referenced against
`config/tested_features.json` and `docs/FEATURE_REGISTRY.md` (not modified
by this audit — corrections below are recommendations for whatever process
consolidates those files, not edits to them). Companion model card:
`docs/model_audit/models/WNBA_ELO_TREND_LR_V4.md`. Companion archived-code
review: `docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md`.

---

## `elo_probability`

- **Model(s) using it:** all 5 standard production learned-LR models
  (MLB, NBA, WNBA, NFL, SOCCER); `wnba-elo-trend-lr-v4` moneyline.
- **Source location:** `src/model_prediction/features/elo_ratings.py`
  (`EloBook.expected_home_win`, `build_elo`).
- **Provider:** internal — computed entirely from locally cached completed
  games (`data/processed/.../games.jsonl` per sport), no external API at
  inference time.
- **Formula:** standard Elo logistic:
  `1 / (1 + 10^(-(home_rating + home_advantage - away_rating) / 400))`.
  WNBA config (`ELO_CONFIG["wnba"]`): `k=20.0`, `home_advantage=60.0`,
  `offseason_regression=0.40` (highest offseason-regression fraction of
  any team sport in `ELO_CONFIG` except NFL's 0.50 — reflects WNBA's
  relatively large year-over-year roster churn). Margin-of-victory
  multiplier uses the shared 538-style log-scaled/autocorrelation-damped
  formula in `EloBook.update`.
- **Expected sign:** positive (higher home Elo relative to away → higher
  home win probability). Confirmed: WNBA fitted coefficient **+3.134**.
- **PIT-safe?** Yes, by construction — `build_elo` processes `games` sorted
  chronologically and updates ratings strictly after computing each game's
  expected outcome; the caller (`validation.py`'s walk-forward loop /
  `learned_forward.py`) is responsible for only passing `history` strictly
  before the decision date, which is the invariant
  `outputs/rebuild/audit/elo_leakage_trace.py` (a real, pre-existing,
  read-only script found already in this worktree) independently traces
  against real NBA data using the identical `build_elo`/day-bucketing code
  WNBA shares. Not independently re-run for WNBA in this audit (no venv,
  no WNBA historical dataset available in this docs-only worktree —
  `data/processed/` is gitignored/absent here); cited as prior structural
  evidence for the shared mechanism, not a WNBA-specific re-verification.
- **Train/serve parity?** Yes — `learned_forward.py:73`
  (`elo.expected_home_win(home_team, away_team)`) calls the exact same
  `EloBook` method used in training via `build_elo`. Same primitive, no
  separate serving reimplementation.
- **Coverage:** Effectively 100% of games with any prior history — every
  team gets a rating (`DEFAULT_ELO = 1500.0` for never-seen teams,
  i.e. cold start uses a neutral prior rather than failing closed).
  `minimum_team_history_games` gates the *model call*, not this feature
  specifically (see `learned_forward.py`'s
  `insufficient_team_history` check).
- **Missingness behavior:** Never missing in the NO_CALL sense — cold-start
  teams get `DEFAULT_ELO`. This is a deliberate default-rather-than-fail
  design (unlike the availability feature's fail-closed pattern below),
  appropriate because a new team's Elo genuinely *is* "no informative
  prior," not an error state.
- **Correlation notes:** Not independently computed in this audit
  (`data/processed/` unavailable here). `elo_probability` is definitionally
  the dominant relative-strength signal; `trend_gap`/`defensive_trend_gap`
  are separately-computed EWMA momentum differences over the same
  underlying game list, so some collinearity with Elo is structurally
  plausible but not measured here.
- **Coefficient/importance:** WNBA fitted coefficient **3.1343561088**
  (v4), **3.1372112172** (v3) — stable across refits, in the same range as
  every other sport's Elo coefficient (2.6-5.6). `evidence_grade: A` in
  `tested_features.json`.
- **Ablation deltas:** `tested_features.json`'s `production_ablation.WNBA`
  is `"INCONCLUSIVE"` (strict leave-one-out not conclusive for WNBA
  specifically, unlike NBA/SOCCER which are strict `KEEP`) — but this is
  the *strict* test; the feature's coefficient magnitude and stability
  across versions is itself strong directional evidence, and no source
  reviewed in this audit suggests removing it.
- **Calibration impact:** Not isolated from the other two features in any
  evidence reviewed; the artifact's overall calibration slope (1.27,
  underconfident) reflects the full 3-feature model, not `elo_probability`
  alone.
- **Known bugs:** None specific to WNBA found in this audit. The
  project-wide open question flagged in `docs/FEATURE_REGISTRY.md` ("NBA
  v4 hits 73.66% while calling 88.2% of games... Until this is answered,
  do not build on top of Elo") is about NBA's calling rate, not WNBA's
  (WNBA's `called_rate` is 1.0 for a different, already-understood reason —
  the near-0.5 threshold plus the project's "every candidate is real" 2026
  -07-30 policy, both documented in the model card) — flagging it here
  only because the underlying Elo *code* is shared, not because WNBA
  exhibits the same anomaly.
- **`strict_statistical_verdict`:** RETEST_REQUIRED (WNBA-specific strict
  ablation is formally INCONCLUSIVE per `tested_features.json`, though
  directionally strong).
- **`operator_retention_verdict`:** **KEEP_CORE** — the dominant,
  consistently material signal in this model and every sibling model in
  the project; nothing in this audit suggests reconsidering it.

---

## `trend_gap`

- **Model(s) using it:** all 5 standard production learned-LR models;
  `wnba-elo-trend-lr-v4` moneyline.
- **Source location:** `src/model_prediction/features/trends.py`
  (`TrendEngine.team_trend`, `TeamTrend.offensive_momentum`); combined in
  `learned_forward.py:74`.
- **Provider:** internal, same cached-games source as Elo.
- **Formula:** `home_trend.offensive_momentum - away_trend.offensive_momentum`,
  where `offensive_momentum = offense[hl3] - offense[hl25]` — the
  difference between a team's short-half-life (3-game) and long-half-life
  (25-game) opponent-adjusted, league-baseline-shrunk offensive EWMA
  level. Positive means the home team's offense is trending up relative to
  its own longer baseline more than the away team's.
- **Expected sign:** positive (home team trending up → more likely to
  win). Confirmed: WNBA fitted coefficient **-0.0068** (v4), **-0.0155**
  (v3) — **both are negative**, opposite the naively expected sign, though
  extremely small in magnitude. `tested_features.json` frames this
  project-wide: "near-zero coefficients everywhere... MLB and SOCCER are
  directional removal candidates" — WNBA is not currently listed as a
  removal candidate for this feature specifically (unlike
  `defensive_trend_gap`), but the sign inversion is worth noting alongside
  the magnitude.
- **PIT-safe?** Same structural answer as Elo — `TrendEngine` consumes an
  already time-sliced `context.games` list; it does not itself re-check
  timestamps, so PIT-safety is a property of the caller correctly slicing
  `history`, matching the shared walk-forward loop Elo uses.
- **Train/serve parity?** Yes — same `TrendEngine.team_trend` call in both
  training (via `validation.py`) and serving (`learned_forward.py:74`).
- **Coverage:** Same as Elo — every team with any game history gets a
  trend vector; a team with zero prior games returns the league baseline
  (`ewm_level` returns `baseline` when `values` is empty), not a NO_CALL.
- **Missingness behavior:** Defaults to league baseline rather than
  failing closed, same philosophy as Elo's cold start.
- **Correlation notes:** Not independently computed here. Structurally,
  `offensive_momentum` and `elo_probability` are derived from overlapping
  information (both from the same game history), so some collinearity is
  plausible.
- **Coefficient/importance:** Small and stable in sign-of-magnitude but
  unstable in actual sign relative to the "expected" direction across
  refits (-0.0068 v4 vs -0.0155 v3 — same sign as each other, both
  negative, but neither matches the naive positive-momentum-helps
  intuition).
- **Ablation deltas:** `tested_features.json`: `production_ablation.WNBA:
  "INCONCLUSIVE"`. Retained project-wide under the "zero-threshold
  directional policy" (kept if at least one out-of-sample cohort improves
  with it present), per the registry's notes.
- **`retest_when` (from `tested_features.json`, project-wide, not
  WNBA-specific):** "Never as-is. If retested it must be as a RESIDUAL
  trend orthogonalized against `elo_probability`, not the raw EWMA
  difference." This is directly relevant to the sign-inversion /
  small-magnitude observation above — an orthogonalized retest is the
  registry's own prescribed next step, and this audit did not find any
  reason to override that prescription.
- **Calibration impact:** Not isolated in evidence reviewed.
- **Known bugs:** None found specific to WNBA.
- **`strict_statistical_verdict`:** RETEST_REQUIRED, specifically per the
  registry's own `retest_when` clause (orthogonalize against Elo before
  re-testing; do not re-test the raw form again).
- **`operator_retention_verdict`:** **KEEP** — zero-threshold policy keeps
  it, magnitude is small enough that removal risk is low either way, but
  this is a weaker retention case than `elo_probability`'s and should not
  be treated as equally load-bearing.

---

## `defensive_trend_gap`

- **Model(s) using it:** NBA and WNBA only (not MLB/NFL — those don't
  carry this feature at all; SOCCER has its own separately-tested
  instance). `wnba-elo-trend-lr-v4` moneyline.
- **Source location:** `src/model_prediction/features/trends.py`
  (`TeamTrend.defensive_momentum`); combined in `learned_forward.py:75`.
- **Provider:** internal, same source as the above two.
- **Formula:** `home_trend.defensive_momentum -
  away_trend.defensive_momentum`, where `defensive_momentum =
  defense[hl25] - defense[hl3]` — note the subtraction order is *reversed*
  relative to `offensive_momentum` (`offense[hl3] - offense[hl25]`), which
  is correct given `defense` measures points *allowed*: a team improving
  defensively has a *falling* short-half-life points-allowed level, so
  `long - short` is positive when defense is improving. Read directly in
  `trends.py`, confirmed intentional (not a copy-paste sign bug).
- **Expected sign:** positive (home defense trending better relative to
  away defense's trend → higher home win probability). Confirmed: WNBA
  fitted coefficient **-0.0029** (v4), **+0.0090** (v3) — **the sign
  flips between the two most recent refits**, on top of both being
  extremely close to zero. This is the strongest sign-instability finding
  of the three trained features.
- **PIT-safe?** Same structural answer as the other two `trends.py`-based
  features.
- **Train/serve parity?** Yes, same shared-primitive pattern.
- **Coverage / missingness:** Same as `trend_gap` — defaults to league
  baseline for cold-start teams, never a NO_CALL.
- **Correlation notes:** Not independently computed here.
- **Coefficient/importance — this is the central finding of this audit,
  verified directly against the live artifact, not just cited from
  documentation:**
  - `wnba-elo-trend-lr-v4.json` coefficient: **-0.0029373676**.
  - `wnba-elo-trend-lr-v3.json` (prior, archived) coefficient:
    **+0.0089977272**.
  - `config/tested_features.json`'s `defensive_trend_gap` entry:
    `fitted_coefficients.WNBA: -0.003`, `fitted_coefficients.NBA: -0.013`,
    matching this audit's direct artifact read for WNBA.
  - `docs/FEATURE_REGISTRY.md` corrected-claims section: "3.
    '"NBA/WNBA win because of `defensive_trend_gap`.' False. Coefficients
    -0.013 and -0.003" — same numbers, independently corroborating.
  - **This confirms the background brief's claim that the feature has a
    near-zero coefficient.** The additional finding beyond "near zero" is
    that the sign is not even stable across the two most recent artifact
    versions (+0.0090 → -0.0029), which is a stronger signal that the
    fitted value in any single artifact reflects noise in that
    training window, not a real, reproducible defensive-trend effect.
- **Ablation deltas:** `tested_features.json`: `production_ablation.WNBA:
  "INCONCLUSIVE"` (strict leave-one-out), but `verdict:
  "remove_candidate"` with notes stating "Removal improves both validation
  and holdout proper scores" — i.e. the *directional* (zero-threshold)
  evidence, which is the project's stated primary retention policy, points
  to removal, even though the stricter multiplicity-adjusted test hasn't
  formally flipped to REJECT. `docs/FEATURE_REGISTRY.md`'s production
  feature table lists it as `remove candidate` for NBA/WNBA with identical
  framing.
- **Calibration impact:** Not isolated in evidence reviewed; plausible
  that removing a noisy near-zero coefficient could marginally tighten
  calibration, but this audit did not find a direct measurement of that
  specific counterfactual.
- **Known bugs:** `docs/model_audit/prior_evidence/current_system_audit.md`
  (2026-08-05, `rebuild/clean-slate-v1` branch) records that this feature
  "was silently blank in audit trail until 2026-08-04" — an audit-logging
  gap (the feature was computed and scored correctly; the *logged* audit
  row didn't record its value), not a leakage or serving-correctness bug.
  Documented in more detail in the model card.
- **`strict_statistical_verdict`:** RETEST_REQUIRED (formally
  INCONCLUSIVE under the strict multiplicity-adjusted test — has not been
  re-run against the exact current v4 artifact per
  `tested_features.json`'s own framing).
- **`operator_retention_verdict`:** **REMOVE** (recommended for the next
  same-family revision only — `wnba-elo-trend-lr-v5`, not v4). This is the
  one feature in this audit where the directional evidence (removal
  improves both proper scores), the near-zero magnitude, and the
  sign-instability across refits all point the same way, and where the
  background brief's premise is fully confirmed by direct artifact
  inspection. Per this audit's scope, **v4 itself stays as-is and
  immutable** — this verdict is a recommendation for what v5 should test
  first, not an instruction to touch the current production artifact.

---

## `availability_points_gap` (and sibling outputs `home_available_minutes_share`, `away_available_minutes_share`, `availability_uncertainty`, `availability_report_age_hours`)

- **Model(s) using it:** WNBA only. **Not** a `wnba-elo-trend-lr-v4`
  regression feature — verified directly against the artifact's
  `feature_names` (only `elo_probability`/`trend_gap`/
  `defensive_trend_gap` are listed). It is a **serve-time post-hoc probit
  adjustment** applied to the LR's output probability in
  `learned_forward.py`'s WNBA-specific block (lines ~460-497), gated to
  only fire when it would move the probability by ≥5 percentage points
  (`delta >= 0.05`).
- **Source location:** `src/model_prediction/features/player_availability.py`
  (feature computation), `src/model_prediction/wnba_availability_evaluation.py`
  (`adjust_home_probability`, `historical_margin_sigma`, `build_and_save_priors`
  — the probit transform and prior-building layer), wired in
  `learned_forward.py`.
- **Provider:** official WNBA injury-report PDF snapshots
  (`data/availability/wnba/snapshots/`) merged with timestamped ESPN
  event-injury snapshots (`data/availability/wnba/espn_event_snapshots/`),
  plus versioned pregame minutes/impact priors
  (`data/player_priors/wnba/`).
- **Formula:** For each team, expected points lost =
  `sum over rotation players of (1 - active_probability) * (minutes/40 *
  (impact_points_per_100 - replacement_impact_points_per_100) * 0.80)`,
  where `active_probability` comes from a fixed status→probability map
  (`Available=1.0, Probable=0.90, Questionable=0.50, Doubtful=0.25,
  Out=0.0`, versioned as `wnba-status-prior-v1`) and `0.80` is a versioned
  ~80-possessions-per-40-minute-game assumption (explicitly documented in
  the code as "a versioned initial prior, not a learned coefficient").
  `availability_points_gap = away_expected_loss - home_expected_loss`
  (positive favors home). The probability adjustment itself is
  `norm.cdf(norm.ppf(base_probability) + points_gap / margin_sigma)` — an
  empirical-probit translation of a point-margin shift into a win
  -probability shift, using `margin_sigma` = the population stdev of all
  historical WNBA margins strictly before the decision cutoff
  (`historical_margin_sigma`, filters `game.start < cutoff` — PIT-safe by
  inspection).
- **Expected sign:** positive coefficient sense (home team losing less
  expected value than away → higher home win probability) — this is how
  it's defined, not fitted, since it's an adjustment, not a regression
  term.
- **PIT-safe?** **Yes, for the feature-computation layer itself, verified
  directly** — every lookup (`_latest_snapshot`, `_load_priors`,
  `_latest_espn_snapshot`) filters strictly on `observed_at`/`report_at`/
  `as_of` timestamps against the decision time and raises a `NO_CALL_*`
  `ValueError` if no eligible, sufficiently-fresh snapshot exists (default
  max report age 12h, max prior age 168h). `matchup_player_availability_
  from_payloads` additionally hard-checks `observed >= start → raise` (not
  pregame → refuse). **Caveat, found directly in
  `docs/model_audit/prior_evidence/current_system_audit.md` (2026-08-05,
  a related-branch audit) and not independently re-verified against
  current `main` in this pass**: `build_and_save_priors()` runs once daily
  and "the prior for today's games includes data from today, which could
  leak if today's games are predicted after priors are built" — this is
  about whether the *prior itself* (the projected-minutes/impact numbers)
  was constructed using same-day information, one level up from
  `player_availability.py`'s own per-request timestamp filtering, which
  only checks that the prior *file* was already `as_of <= observed_at`.
  If the prior file's own construction pulled in same-day box scores or
  news before that day's games, the per-request filter wouldn't catch it.
  Flagging this as the one unresolved PIT question for this feature,
  worth a direct trace by whoever next touches this code, since this audit
  could not run it.
- **Train/serve parity?** N/A in the usual trained-coefficient sense — the
  probit transform and 5pp gate are fixed serving-time logic, not fitted
  parameters, and the same `matchup_player_availability`/
  `adjust_home_probability` functions are the only implementation (no
  separate training-time version exists to drift from).
- **Coverage:** From `docs/MODEL_IMPROVEMENTS.md` (`§7`, "Player-
  availability implementation status") reconstruction: 208 official
  reports covered 180 scheduled matchups over the May 14-Jul 20 window;
  the V3 forward pass produced 169 candidates, 164 settled, and **142 had
  conflict-free, fully-mapped availability inputs** — roughly 79% of the
  180 scheduled matchups, or 87% of the 164 settled ones, actually got a
  usable availability read; the rest presumably fell to one of the
  `NO_CALL_AVAILABILITY_*` fail-closed paths (stale report, unmapped
  player, incomplete rotation minutes, source conflict, etc.).
  `current_system_audit.md`'s features-not-fully-audited table separately
  notes `availability_points_gap` as "Populated but uncertain coverage" —
  consistent with this ~79-87% figure not being a hard, currently-verified
  guarantee going forward (roster/reporting conditions can shift it).
- **Missingness behavior:** Fail-closed by design, and unusually
  thoroughly so — nine distinct `NO_CALL_AVAILABILITY_*` reason codes
  found in `player_availability.py` (`UNAVAILABLE`, `PRIORS_UNAVAILABLE`,
  `ESPN_UNAVAILABLE`, `PRIORS_INCOMPLETE`, `STATUS_UNKNOWN`,
  `SOURCE_CONFLICT`, `PLAYER_UNMAPPED`, `PRIORS_INVALID`,
  `REPORT_INCOMPLETE`, `INVALID_TIME`, `STALE`). On any of these,
  `learned_forward.py` catches the exception, logs a note, and **falls
  back to the unadjusted 3-feature LR probability** rather than either
  guessing or blocking the underlying moneyline call — a genuinely
  fail-closed pattern for the adjustment layer specifically (not a NO_CALL
  on the whole game).
- **Correlation notes:** Not independently computed in this audit.
- **Coefficient/importance:** Not a fitted coefficient (see Train/serve
  parity above) — its "importance" is the gated 5pp-or-nothing effect
  size, which by construction is discontinuous (either 0 or ≥5pp).
- **Ablation deltas — real, grade-B, measured, and specifically about
  probability quality rather than accuracy:** From
  `docs/MODEL_IMPROVEMENTS.md` §7's 2026-07-20 reconstruction (142-game
  paired subset, May 14-Jul 20): winner accuracy moved from 71.83% to
  71.13% (**slightly worse**) while Brier improved from 0.21278 to
  0.20680 (delta -0.00599, paired bootstrap 95% CI [-0.01076, -0.00119] —
  a real, statistically distinguishable-from-zero improvement, not noise).
  Seven selections flipped (three corrections, four new errors — a wash
  on net winner-calls, but the probabilities on the calls that didn't flip
  got measurably better-calibrated). `tested_features.json`'s
  `player_availability` entry separately reports a different framing on
  what appears to be a related but not identical cohort: "142-game WNBA
  cohort May 14 - Jul 20: accuracy 71.8% -> 73.2% (+1.4pp), Brier 0.21278
  -> 0.20799 (-2.3%)... zero harmful selection flips" — **this accuracy
  number (+1.4pp, zero harmful flips) directly contradicts the
  `MODEL_IMPROVEMENTS.md` narrative (-0.7pp, seven flips, three corrections
  vs four new errors) on the same nominal 142-game window.** Both cite the
  same date range and same n=142; this audit could not resolve which
  reflects the current code path without re-running the reconstruction
  (no venv here), so this is flagged as an open discrepancy for whoever
  consolidates `tested_features.json`/`FEATURE_REGISTRY.md` to resolve
  (likely two different snapshots of the same ongoing research, only one
  of which was correctly updated in both places) rather than silently
  picking one number.
- **Calibration impact:** Directionally positive and the more consistent
  claim across both sources above (-0.00599 vs -0.00479 pp Brier
  improvement — both sources agree Brier improves, only the accuracy
  figure and flip-direction disagree).
- **Known bugs:** The Dallas/Paige Bueckers case study in
  `docs/MODEL_IMPROVEMENTS.md` §7 documents a real, found-and-fixed
  status-disagreement bug: the official PDF omitted a player the
  timestamped ESPN feed had already marked `Out` with an undisclosed
  issue; the merge logic now captures that (moved Dallas's probability
  from 67.878% to 59.313% once resolved). The DEBUG.md P0 #3 finding
  ("does not fail closed on source conflicts") is addressed in detail in
  the model card and found, on direct code read, to be resolved in current
  `main` (default `conflict_policy="fail_closed"`, exercised by
  `tests/test_wnba_availability.py::test_conflicting_explicit_sources_
  fail_closed`) — treat that specific DEBUG.md entry as stale pending
  confirmation by a run of the actual test suite.
- **`strict_statistical_verdict`:** RETEST_REQUIRED — `tested_features.json`
  itself states this evidence "was not part of the current exact-artifact
  strict ablation," and this audit found a real unresolved discrepancy
  between the two sources reporting the same cohort's accuracy figure
  (above) that should be reconciled before treating either number as
  settled.
- **`operator_retention_verdict`:** **KEEP** — `tested_features.json`'s
  own verdict, and this audit did not find a reason to override it: the
  Brier-improvement direction is consistent across both cited sources, the
  feature-computation layer's fail-closed discipline is genuinely strong,
  and it is architecturally isolated (a gated post-hoc adjustment, not a
  trained coefficient) so its risk to the core LR model if wrong is
  bounded to the ≤5pp-or-nothing adjustment window. The one open item
  (daily-prior same-day-leak risk, and the accuracy-figure discrepancy
  above) should be traced before this is treated as fully settled, but
  neither rises to BLOCKED_PIT on the evidence read in this audit — the
  per-request timestamp filtering is real and verified; the concern is one
  level upstream of it (prior construction), not proven to actually leak.

---

## Archived Four-Factors efficiency features: `ortg`, `drtg`, `netrtg`, `pace`, `efg`, `tov_pct`, `orb_pct`, `ft_rate` (research candidates, `origin/rebuild/wnba-v1`)

- **Model(s) using it:** None currently — these exist only in the archived
  `origin/rebuild/wnba-v1` branch (`src/model_prediction/rebuild/wnba/
  features.py`, `METRICS` tuple), not merged into `main`, not consumed by
  any live or research model. `origin/rebuild/wnba-v1:baselines.py`'s
  `regularized_logistic`/`linear_margin`/`linear_total` research baselines
  consume the `season`-window versions of all eight as their
  `FEATURE_COLUMNS` (16 total: 8 metrics × home/away).
- **Source location:** `src/model_prediction/rebuild/wnba/features.py`
  (archived; not present in `main` — see
  `docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md` for full review).
- **Provider:** SportsDataverse WNBA release data (`wehoop`-equivalent),
  ingested via the already-in-`main`
  `rebuild/providers/sportsdataverse.py` and normalized by the
  already-in-`main` `rebuild/wnba/normalize.py`.
- **Formula:** Standard basketball Four-Factors-family definitions —
  `ortg`/`drtg` = 100 × points / estimated possessions (`FGA + 0.44*FTA -
  OREB + TOV`); `netrtg = ortg - drtg`; `pace` = average of both teams'
  estimated possessions; `efg = (FGM + 0.5*3PM) / FGA`; `tov_pct = TOV /
  possessions`; `orb_pct = OREB / (OREB + opponent DREB)`; `ft_rate = FTA /
  FGA`. Computed over rolling last-5/last-10/last-20-game and season
  windows, per team, as of a decision timestamp. See the companion archive
  review for the full PIT-safety and code-quality assessment (both
  strong).
- **Expected sign:** Not applicable yet — no fitted model in `main`
  consumes these; the archived `baselines.py` research module does fit a
  logistic/Ridge model on them, but that module is itself
  qualification-blocked (`production_allowed: False`,
  `commercial_use_status: "unresolved"`) and its own fitted coefficients
  were not reviewed for sign/magnitude in this pass (out of scope — this
  audit's job was to assess the archived *code*, not run it).
- **PIT-safe?** Yes, verified directly against the archived `features.py`
  and `horizon_builder.py` code (see the archive review for full detail):
  strict `observed_at <= decision_time` filtering at every layer, plus a
  postponement-safe cutoff-stabilization loop in `horizon_builder.py` that
  is more defensive about schedule changes than anything currently in
  `main`'s live WNBA path. Not executed in this audit (no venv).
- **Train/serve parity?** The archived `horizon_builder.py` is explicitly
  built as "One PIT-safe WNBA feature path shared by replay and live
  operation" (its own module docstring) — i.e. designed from the start to
  avoid a separate train/serve implementation. Not executed/verified here.
- **Coverage:** Not measured in this audit — would depend on how much
  SportsDataverse WNBA release history has actually been backfilled via
  `rebuild-data backfill --sport wnba`, which is outside this audit's
  scope (data volume, not code review).
- **Missingness behavior:** `_metrics_complete()` in the archived
  `horizon_builder.py` requires every rolling-window metric to be present
  and finite for both teams before a feature row is emitted at all
  (`team_form_metrics_unavailable` reason code otherwise) — fail-closed at
  the row level, consistent with this project's general philosophy.
- **Correlation notes:** Not measured — no fitted artifact exists to
  analyze.
- **Coefficient/importance:** None — not in any deployed or promoted
  model.
- **Ablation deltas:** None — the archived `baselines.py` module explicitly
  states it "evaluates controls; it does not create a deployable
  challenger," so there is no ablation-against-production evidence to
  cite, only an internal comparison among the archived baselines
  themselves (constant/expanding-base-rate/Elo/logistic/margin-Ridge/
  total-Ridge), which this audit did not execute.
- **Calibration impact:** Unknown — no evidence exists.
- **Known bugs:** One real, minor discrepancy found in this audit (detailed
  in the archive review): the archived `baselines.py`'s Elo baseline uses
  `home_advantage=65.0`, while `main`'s live WNBA Elo config
  (`ELO_CONFIG["wnba"]`) uses `home_advantage=60.0` — not a bug in either
  file individually, but a reconciliation item before treating the
  archived Elo baseline's numbers as directly comparable to the live
  `elo_probability` feature.
- **`strict_statistical_verdict`:** KEEP_RESEARCH_ONLY — no statistical
  evidence exists yet either way; nothing to retest because nothing has
  been tested against a real holdout inside `main`.
- **`operator_retention_verdict`:** **KEEP_RESEARCH_ONLY** — matches the
  archive review's RECOVER verdict for `features.py`/`horizon_builder.py`:
  strong, PIT-conscious groundwork for exactly the Four-Factors feature
  group `docs/MODEL_IMPROVEMENTS.md` §7 calls for as WNBA roadmap rank #3,
  worth recovering into `main` as a *research* feature path, but with zero
  production evidence yet and (via the archived `baselines.py`'s own
  self-imposed gate) currently blocked from any production use by
  unresolved SportsDataverse/ESPN commercial-use rights regardless of how
  well it performs statistically. Not REBUILD_IMPLEMENTATION, because the
  implementation itself does not need rebuilding — it needs recovering,
  wiring into a real challenger, and then testing.

---

## Summary table

| Feature | Model(s) | Coefficient (WNBA, v4) | `strict_statistical_verdict` | `operator_retention_verdict` |
|---|---|---|---|---|
| `elo_probability` | all 5 sports | +3.1344 | RETEST_REQUIRED | **KEEP_CORE** |
| `trend_gap` | all 5 sports | -0.0068 | RETEST_REQUIRED (orthogonalize vs. Elo) | **KEEP** |
| `defensive_trend_gap` | NBA, WNBA | -0.0029 (sign-flipped from v3's +0.0090) | RETEST_REQUIRED | **REMOVE** (target: v5, not v4) |
| `availability_points_gap` (+ siblings) | WNBA (serve-time adjustment, not a trained feature) | n/a (gated ±0/adjustment) | RETEST_REQUIRED (cohort-figure discrepancy found) | **KEEP** |
| Four-Factors (`ortg`/`drtg`/`netrtg`/`pace`/`efg`/`tov_pct`/`orb_pct`/`ft_rate`) | none (archived, `origin/rebuild/wnba-v1`) | n/a | KEEP_RESEARCH_ONLY | **KEEP_RESEARCH_ONLY** |

This audit's central, directly-verified finding: **the background brief's
claim that `defensive_trend_gap` has a near-zero coefficient is confirmed**
— WNBA -0.0029 in the live v4 artifact, matching both
`config/tested_features.json` (-0.003) and `docs/FEATURE_REGISTRY.md`
independently — and the coefficient's sign additionally flips between the
two most recent artifact versions, which is stronger evidence against it
than "near zero" alone. Per the operator directive, `wnba-elo-trend-lr-v4`
itself stays as the immutable moneyline control; removing this feature is
recommended as the first thing a future `wnba-elo-trend-lr-v5` should
test, not as a change to v4.
