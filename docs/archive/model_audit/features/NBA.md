# Feature Audit: NBA

Covers the three features live in `nba-elo-trend-lr-v4`
(`elo_probability`, `trend_gap`, `defensive_trend_gap`) plus five candidate
features named in the audit brief that are not currently in any active NBA
model config (`consistency_gap`, `hot_cold_gap`, `rest_disparity`,
`games_last_7_gap`, `schedule_missingness`). Verdicts use exactly the
required enum. See `docs/model_audit/models/NBA_ELO_TREND_LR_V4.md` for the
full model card and the Elo-leakage trace this doc's `elo_probability`
entry depends on.

Source of truth cross-checked against `config/tested_features.json` (not
modified by this audit — read for evidence, cited below) and
`outputs/roadmap_challenger/roadmap-challenger-factorial-v1.json` (the raw
factorial-ablation run behind that file's NBA notes).

---

## `elo_probability`

- **Model(s) using it**: `nba-elo-trend-lr-v4` (coefficient 3.5640800015,
  by far the dominant term vs. intercept -1.907 and the other two
  coefficients — see model card); also MLB, WNBA, NFL, and soccer's
  elo+trend families.
- **Source location**: `src/model_prediction/features/elo_ratings.py`
  (`build_elo`, `EloBook.expected_home_win`); called from both
  `validation.py::build_walk_forward_rows` (training) and
  `learned_forward.py::build_learned_moneyline_slate`/`_compute_features`
  (live serving) — no separate implementation exists for either path.
- **Provider**: none external — computed entirely from locally cached
  completed NBA results (`FeatureStore.load_games`/`games_before`), no
  network call.
- **Formula**: standard Elo logistic:
  `1 / (1 + 10^(-(home_rating + 70 - away_rating) / 400))`, with NBA's
  `k=20.0`, `home_advantage=70.0` config row, margin-of-victory log-scaled
  update multiplier, and 35% offseason regression toward `DEFAULT_ELO=1500`
  triggered by a >90-day gap between chronologically consecutive games.
- **Expected sign**: positive (higher home Elo relative to away -> higher
  home win probability). Confirmed: coefficient +3.564, largest-magnitude
  term by two orders of magnitude.
- **PIT-safe?**: **Yes — verified this audit.** Traced 67 real historical
  events (11 game-days, 2024-01-09 through 2026-02-01) end to end: for
  every event, both `last_home_update_utc` and `last_away_update_utc` were
  strictly before `event_start_utc`, with zero violations. Verified in
  both the training walk-forward loop (`validation.py`, snapshot-then-append
  ordering) and the live serving path
  (`learned_forward.py`, `games_before(as_of_date)` cutoff at midnight ET +
  an independent `start <= observed_at` already-started guard). Full trace
  script: `outputs/rebuild/audit/elo_leakage_trace.py`.
- **Train/serve parity**: identical `build_elo()` call in both paths; the
  only difference is how `history` is assembled (day-bucket walk-forward
  vs. `games_before`), and both encode the same "strictly before target's
  calendar day" rule.
- **Coverage**: 100% of games in the sampled dataset once the
  `minimum_history_games=50` warm-up threshold is passed; 0/67 sampled
  games hit a cold-start team (`DEFAULT_ELO=1500` fallback via
  `EloBook.rating()`'s `dict.get(team, DEFAULT_ELO)`).
- **Missingness behavior**: never missing by construction — a team with no
  rating history is silently priced at 1500 (neutral), not excluded.
- **Correlation notes**: not separately ablated against `trend_gap`/
  `defensive_trend_gap` in this audit, but `config/tested_features.json`'s
  `trend_gap` entry flags that any future retest of `trend_gap` should be
  "a RESIDUAL trend orthogonalized against `elo_probability`, not the raw
  EWMA difference" — implying meaningful shared variance between Elo and
  the raw trend features today.
- **Coefficient/importance**: 3.5640800015 (v4 artifact); a near-identical
  3.5693394003 in the matched-refit "incumbent" row of the roadmap
  challenger's NBA factorial run (`nba-elo-trend-lr-v3` basis) — stable
  across artifact versions.
- **Ablation deltas**: `config/tested_features.json` reports NBA
  `production_ablation: KEEP` with `evidence_grade: A`, "the only
  consistently material fitted signal in the five standard production
  models." Confidence-gate sweep in the roadmap-challenger run shows the
  signal holds up even fully unselective (gate=0.5, 100% call rate):
  70.24% holdout hit rate on 662 calls, climbing to 79.19% at gate=0.625
  (394 calls) — a smooth, monotonic accuracy/selectivity tradeoff
  consistent with a genuine, well-calibrated probability estimate rather
  than an artifact of threshold selection.
- **Calibration impact**: v4 holdout calibration slope 1.785 (see model
  card) — model is if anything *under*-confident in this window, the
  opposite of what a leaking feature typically produces.
- **Known bugs**: none affecting correctness of the leakage question.
  Two lower-severity data-quality gaps found this audit (see model card
  "Secondary findings"): NBA preseason (144 games) and All-Star (4 games)
  results are not excluded from Elo history the way MLB excludes its own
  preseason/All-Star games (`features/base.py:120` filters only
  `sport.lower() == "mlb"`); and no true neutral-site override exists
  (`GameRecord` carries no `neutral_site` flag, so the full +70
  home-advantage constant applies even to the rare neutral-venue game).
  Neither is a point-in-time leak.
- **Verdict**: **KEEP_CORE** — this is the load-bearing signal of the
  model; leakage investigation closed with no finding; ablation evidence
  grade A.

---

## `trend_gap`

- **Model(s) using it**: `nba-elo-trend-lr-v4` (coefficient -0.0035535722);
  also MLB, WNBA, NFL, soccer.
- **Source location**: `src/model_prediction/features/trends.py`
  (`TrendEngine.team_trend`, `TeamTrend.offensive_momentum`); combined in
  `learned_forward.py::_compute_features`:
  `home_trend.offensive_momentum - away_trend.offensive_momentum`.
- **Provider**: none external — derived from the same locally cached game
  history as Elo.
- **Formula**: `offensive_momentum = offense["hl3"] - offense["hl25"]`,
  where each `offense["hlN"]` is an opponent-adjusted, league-shrunk,
  exponentially-weighted scoring level at half-life N games (3 and 25
  games respectively) via `ewm_level()`. `trend_gap` is the home team's
  momentum minus the away team's.
- **Expected sign**: ambiguous a priori (a team's short-term offensive
  surge relative to its own long-run level could mean-revert or persist).
  Fitted sign is negative and near zero (-0.0036) — essentially no
  measured effect once `elo_probability` is in the model.
- **PIT-safe?**: Yes — `TrendEngine` is built from the identical
  cutoff-filtered `history` list as `build_elo` in both training and
  serving paths (same `games_before`/day-bucket-walk-forward chokepoint).
- **Train/serve parity**: Yes — same `TrendEngine` class instantiated
  identically in `validation.py` and `learned_forward.py`.
- **Coverage**: full, subject to the same `minimum_team_history_games=10`
  gate `build_learned_moneyline_slate` applies to `home_trend`/`away_trend`
  before predicting any event.
- **Missingness behavior**: no explicit missingness flag; a team with too
  little history is excluded from prediction entirely upstream
  (`insufficient_team_history` ValueError), never silently zeroed for this
  particular feature.
- **Correlation notes**: `config/tested_features.json` explicitly flags
  meaningful overlap with `elo_probability` (see above); status field is
  `"production_mixed_direction"` — fitted sign varies by sport (positive
  for NFL, negative for NBA/MLB/WNBA/soccer).
- **Coefficient/importance**: -0.0035535722 (v4); -0.004394871 in the
  matched-refit v3-basis run — consistently near zero across artifact
  versions.
- **Ablation deltas**: `config/tested_features.json`: NBA leave-one-out
  `production_ablation: INCONCLUSIVE`. Retention is via the project's
  "zero-threshold retention policy" (kept because at least one
  out-of-sample cohort improves slightly), not because the leave-one-out
  evidence itself supports it. Source file's own `retest_when` field:
  *"Never as-is. If retested it must be as a RESIDUAL trend orthogonalized
  against elo_probability, not the raw EWMA difference."*
- **Calibration impact**: not separately isolated from `defensive_trend_gap`
  in the available evidence; the two are always tested together as the v4
  incumbent's 3-feature set.
- **Known bugs**: none. This is a design/signal-strength concern
  (near-zero, likely collinear with Elo), not a correctness bug.
- **Verdict**: **RETEST_REQUIRED** — coefficient is statistically
  indistinguishable from zero and leave-one-out evidence is INCONCLUSIVE
  for NBA; per the source evidence file's own note, any retest must use an
  Elo-orthogonalized residual formulation, not the current raw EWMA
  difference. The current v4 artifact is immutable and should not be
  touched over this; applies to a future v5 candidate only.

---

## `defensive_trend_gap`

- **Model(s) using it**: `nba-elo-trend-lr-v4` (coefficient
  -0.013059643); also WNBA and soccer.
- **Source location**: `src/model_prediction/features/trends.py`
  (`TeamTrend.defensive_momentum`); combined in
  `learned_forward.py::_compute_features`:
  `home_trend.defensive_momentum - away_trend.defensive_momentum`.
- **Provider**: none external, same underlying game history as
  `trend_gap`.
- **Formula**: `defensive_momentum = defense["hl25"] - defense["hl3"]`
  (note the reversed operand order vs. `offensive_momentum` — a team
  recently allowing *fewer* points than its own long-run average yields a
  *positive* defensive_momentum). `defensive_trend_gap` is home minus away.
- **Expected sign**: positive (home defense trending better than away
  defense should raise home win probability). Fitted sign is negative
  (-0.013) in NBA — opposite the naively expected sign, though the
  magnitude is small enough that this is not strong evidence of a real
  inverted effect rather than noise around zero.
- **PIT-safe?**: Yes, same mechanism/verification as `trend_gap` above.
- **Train/serve parity?**: Yes, same mechanism as `trend_gap` above.
- **Coverage**: same as `trend_gap`.
- **Missingness behavior**: same as `trend_gap`.
- **Correlation notes**: same Elo-overlap concern noted for `trend_gap`
  applies structurally (same `TrendEngine`/half-life machinery), though
  not separately quantified.
- **Coefficient/importance**: -0.013059643 (v4 NBA); -0.0140026801
  (v3-basis matched refit) — small but the largest-magnitude of the two
  trend features in NBA. WNBA's `defensive_trend_gap` coefficient is
  -0.0029373676 — smaller than NBA's, but both are near zero relative to
  `elo_probability`'s coefficient in their respective sports (NBA 3.564,
  WNBA 3.134). Confirms the "near-zero like WNBA's" premise in the task
  brief, with NBA's magnitude actually ~4.5x WNBA's while still being
  economically negligible next to Elo.
- **Ablation deltas**: `config/tested_features.json` verdict
  `remove_candidate`, status `"production_directionally_harmful"`, explicit
  correction note: *"CORRECTION OF A PRIOR CLAIM: this feature does NOT
  explain NBA/WNBA performance. Its fitted coefficients are near zero, and
  strict leave-one-out evidence is INCONCLUSIVE in both leagues."*
- **Calibration impact**: not separately isolated (see `trend_gap`).
- **Known bugs**: none beyond the sign-direction oddity noted above, which
  reads as noise given the near-zero magnitude, not a code defect.
- **Verdict**: **REMOVE** — near-zero, directionally-questionable
  coefficient, INCONCLUSIVE leave-one-out evidence, and an explicit prior
  correction on record stating it does not explain NBA/WNBA performance.
  Applies to a future v5 candidate only; the current v4 artifact is
  immutable and stays in production as-is — this is not a live-production
  action item.

---

## Candidate features not in the active NBA model

All five names below already exist as real, working code (confirmed by
`git grep` across every local and remote branch — `main`,
`audit/model-feature-reconciliation-v1`,
`fix/mlb-v3-schedule-schema-drift`, and all `origin/*` branches show the
identical set of hits, so this is not orphaned work on a side branch; it's
present and consistent everywhere). None of the five appears in
`nba-elo-trend-lr-v4`'s `feature_names`, but all five are already wired
into `learned_forward.py::_compute_features`'s dispatch (gated behind
`if name in wanted`, per the shadow-feature pattern in this repo's
`CLAUDE.md`) and were exercised in a real factorial ablation run
(`outputs/roadmap_challenger/roadmap-challenger-factorial-v1.json`,
2026-07-22).

### `consistency_gap`

- **Source location**: `src/model_prediction/features/trends.py`
  (`TeamTrend.consistency`); combined in `learned_forward.py`:
  `home_trend.consistency - away_trend.consistency`.
- **Formula**: `consistency = 1 / (1 + stdev(last 10 games' points scored))`
  — higher means steadier recent scoring.
- **Expected sign**: unclear a priori.
- **PIT-safe?**: Yes — same `TrendEngine`/`history` cutoff mechanism as
  `trend_gap`.
- **Train/serve parity?**: Yes — identical computation path; currently
  gated inert in production (never computed unless an artifact's
  `feature_names` lists it).
- **Coverage / missingness**: same gating as `trend_gap`/`defensive_trend_gap`.
- **Correlation notes / ablation deltas**: factorial roadmap-challenger run,
  NBA, variant `incumbent+consistency+back_to_back+schedule_density+
  schedule_missingness` (fitted coefficient -0.4336569139 for
  `consistency_gap` in that combined variant): holdout Brier 0.193787 vs.
  incumbent's 0.194854 (delta -0.001067), but the date-clustered bootstrap
  95% CI on that delta is [-0.0036109, +0.00146765] — crosses zero, not
  significant. `config/tested_features.json` notes NBA validation
  (-0.0010) and holdout (-0.0012) both improve in the tested combos
  ("directionally promising for NBA/NFL"), but flags overfitting risk
  observed in MLB/WNBA and explicitly says to "retest with current (v4/v5)
  artifacts before promoting" — the cited run used the v3-basis incumbent,
  not v4.
- **Calibration impact**: not isolated separately from the combined variant.
- **Known bugs**: none found.
- **Verdict**: **RETEST_REQUIRED** — real, working, PIT-safe code;
  directionally promising for NBA in a factorial run but tested against a
  superseded (v3-basis) incumbent and not statistically significant on
  its own; needs a clean single-feature ablation against the current v4
  artifact before any promotion decision.

### `hot_cold_gap`

- **Source location**: `src/model_prediction/features/trends.py`
  (`TeamTrend.hot_cold_score`); combined in `learned_forward.py`:
  `home_trend.hot_cold_score - away_trend.hot_cold_score`.
- **Formula**: z-score of the team's half-life-3 raw offensive level
  against its own season mean and stdev (`hot_cold = (raw_recent -
  season_mean) / season_sd`, computed only once >=5 games are in history).
- **Expected sign**: unclear a priori.
- **PIT-safe?**: Yes, same mechanism as `consistency_gap`.
- **Train/serve parity?**: Yes, same as above; inert unless requested.
- **Coverage / missingness**: same gating pattern; `hot_cold_score`
  defaults to 0.0 for teams with fewer than 5 games in history.
- **Ablation deltas**: `config/tested_features.json`: "Same pattern as
  `consistency_gap`: NBA/NFL benefit, MLB/WNBA show holdout improvement
  but validation regression. Best NBA combo (consistency+hot_cold+
  schedule_density+schedule_missingness) improves holdout Brier by
  -0.0012. Retest with current artifacts." Same non-significant bootstrap
  CI caveat as `consistency_gap` applies (they were tested jointly, not
  in isolation, in the cited combo).
- **Calibration impact**: not isolated separately.
- **Known bugs**: none found.
- **Verdict**: **RETEST_REQUIRED** — same reasoning as `consistency_gap`;
  tested only in combination, against a superseded incumbent, evidence
  directionally positive for NBA but not statistically significant alone.

### `rest_disparity`

- **Source location**: `src/model_prediction/features/schedule_load.py`
  (`team_schedule_load`, `matchup_schedule_load`); wired in
  `learned_forward.py`'s `schedule_names` dispatch set.
- **Formula**: home team's rest days (capped at 7) minus away team's, where
  rest days = days since that team's most recent prior game strictly
  before `event_start`.
- **Expected sign**: positive (more rest -> fresher team -> better
  performance) is the naive prior, though NBA rest effects are known to be
  small and inconsistent in public research.
- **PIT-safe?**: Yes — `team_schedule_load` explicitly filters to
  `game.start < event_start` before computing rest; module docstring
  states it is "shared by validation and forward paths."
- **Train/serve parity?**: Yes — same `matchup_schedule_load` function
  called from both `validation.py` and `learned_forward.py`.
- **Coverage**: `TeamScheduleLoad.available=False` (and all four schedule
  features zeroed) when a team has zero prior games in the supplied
  history — i.e., before a team's first tracked game each dataset. Rest is
  explicitly capped at 7 days "so offseason and long-break gaps do not
  dominate the coefficient" (module docstring).
- **Missingness behavior**: explicit zero-fill + `schedule_available=0.0`
  flag rather than a silent default — this is exactly the missingness
  signal `schedule_missingness` (below) is meant to capture.
- **Ablation deltas**: `config/tested_features.json`: "NBA: combined with
  consistency+hot_cold+schedule_density improves holdout by -0.0012...
  Currently wired in learned_forward.py but not in any active model
  config." Not tested in isolation for NBA in the available evidence.
- **Calibration impact**: not isolated.
- **Known bugs**: none found. One deliberate scope limitation, not a bug:
  travel/distance is explicitly excluded ("the repository does not yet
  carry a versioned venue-coordinate history" — module docstring).
- **Verdict**: **RETEST_REQUIRED** — real, PIT-safe, parity-clean code;
  positive evidence only in combination with other schedule/trend features
  against a superseded incumbent; needs an isolated single-feature
  ablation against v4.

### `games_last_7_gap`

- **Source location**: `src/model_prediction/features/schedule_load.py`
  (`team_schedule_load.games_last_7_days`); same `matchup_schedule_load`
  dispatch as `rest_disparity`.
- **Formula**: home team's count of games in the trailing 7 days (as of
  `event_start`, exclusive) minus away team's — a schedule-density /
  fatigue proxy distinct from simple days-of-rest.
- **Expected sign**: negative (more recent games -> more fatigue -> worse
  performance) is the naive prior.
- **PIT-safe? / Train/serve parity?**: Yes — identical mechanism and
  function to `rest_disparity` (both come out of the same
  `matchup_schedule_load` call).
- **Coverage / missingness**: identical gating to `rest_disparity`
  (`schedule_available` flag governs all four schedule features jointly).
- **Ablation deltas**: `config/tested_features.json`: "Strongest in
  combination with rest_disparity+consistency+hot_cold. Currently wired in
  learned_forward.py." Same non-isolated-evidence caveat.
- **Calibration impact**: not isolated.
- **Known bugs**: none found.
- **Verdict**: **RETEST_REQUIRED** — same reasoning as `rest_disparity`.

### `schedule_missingness`

- **Naming note (real finding)**: no symbol literally named
  `schedule_missingness` exists in the current codebase. The
  `config/tested_features.json` name corresponds to
  `schedule_load.py`'s **`schedule_available`** field/key — a 1.0/0.0
  indicator of whether both teams had a resolvable prior-game history at
  prediction time (semantically the *inverse* of "missingness": 1.0 =
  data present, 0.0 = missing). Confirmed by reading
  `outputs/roadmap_challenger/roadmap-challenger-factorial-v1.json`'s raw
  NBA variant coefficients, which list `schedule_available` (not
  `schedule_missingness`) as the actual feature name fit into the model
  (e.g. coefficient -0.8935310876 in the
  `incumbent+consistency+back_to_back+schedule_density+schedule_missingness`
  variant — the variant *label* uses "schedule_missingness" but the
  *fitted feature name* is `schedule_available`). Any future promotion
  work should use the codebase's real name, `schedule_available`.
- **Source location**: `src/model_prediction/features/schedule_load.py`
  (`matchup_schedule_load`'s `"schedule_available"` key); wired in
  `learned_forward.py`'s `schedule_names` dispatch set alongside
  `rest_disparity`/`back_to_back_gap`/`games_last_7_gap`.
- **Formula**: `1.0` if both home and away teams have at least one prior
  game in `history` before `event_start`, else `0.0` (and all four
  schedule-family features zero out together in the missing case).
- **Expected sign**: ambiguous/interaction-only by nature (it's a
  missingness indicator, not a directional signal) — a logistic
  regression fits it a coefficient anyway since it's just another column,
  but its role is to let the model distinguish "true schedule parity" (a
  real zero in `rest_disparity` etc.) from "we don't know" (also a zero,
  without this flag).
- **PIT-safe? / Train/serve parity?**: Yes — computed by the same
  `matchup_schedule_load` call as the other three schedule features, same
  file, same PIT cutoff.
- **Coverage / missingness**: by definition, this *is* the missingness
  signal for the other three schedule features; near-100% coverage past
  the early-season bootstrap window (same as `rest_disparity`).
- **Ablation deltas**: `config/tested_features.json`: verdict
  `tested_marginal`, "Consistently appears in top-performing variants
  across all sports, suggesting it helps the model distinguish between
  real zeros and missing data." This is the most consistently positive of
  the five candidates in the available evidence, though still not
  isolated as a single-feature ablation.
- **Calibration impact**: not isolated.
- **Known bugs**: the name mismatch above (audit finding, not a code
  defect — the code is internally consistent, it's the tracking doc's
  label that differs from the actual column name).
- **Verdict**: **RETEST_REQUIRED** — real, PIT-safe, parity-clean, and the
  most consistently positive of the five candidates in existing evidence,
  but still not isolated in a clean single-feature ablation against the
  current v4 artifact; also needs the naming discrepancy resolved
  (`schedule_available` vs. `schedule_missingness`) before it appears in
  any future artifact's `feature_names`.
