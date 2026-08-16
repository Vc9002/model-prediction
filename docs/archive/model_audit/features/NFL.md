# NFL feature audit

Companion to `docs/model_audit/models/NFL_ELO_TREND_LR_V4.md`. Covers the two production
features and the candidate features named in this audit's brief: schedule load
(`rest_disparity`, `games_last_7_gap`, plus the `schedule_load` module's other
sub-signals), `consistency_gap`, `hot_cold_gap`, and the nflverse-style candidates
(QB state, EPA/dropback, success rate, CPOE, pressure, explosive plays). Also covers
`head_to_head`'s NFL-specific re-test condition, quoted verbatim from the registry.

Verdict enum used below: `KEEP`, `KEEP_CORE`, `KEEP_BASELINE`, `KEEP_RESEARCH_ONLY`,
`RETEST_REQUIRED`, `REBUILD_IMPLEMENTATION`, `BLOCKED_PIT`, `REMOVE`, `REJECT`, `SUPERSEDED`.

Sources of record: `config/tested_features.json` (machine-readable, wins on
disagreement — same rule `docs/FEATURE_REGISTRY.md` states), `docs/FEATURE_REGISTRY.md`,
`docs/MODEL_IMPROVEMENTS.md` §9, `docs/PRODUCTION_FEATURE_ABLATION_2026-07-22.md`,
`outputs/roadmap_challenger/ROADMAP_CHALLENGER_DECISION_DOSSIER.md` and its underlying
`roadmap-challenger-factorial-v1.json`, and direct source reads of `src/model_prediction/features/*.py`
and `src/model_prediction/rebuild/nfl/*` / `src/model_prediction/rebuild/providers/nflverse.py`.

---

## `elo_probability`

- **Model(s) using it**: `nfl-elo-trend-lr-v4` (moneyline), and the same-named feature in MLB/NBA/WNBA/SOCCER's elo-trend LR models.
- **Source location**: `src/model_prediction/features/elo_ratings.py::build_elo` / `EloBook.expected_home_win`.
- **Provider / data source**: `FeatureStore.load_games("nfl")` → `data/processed/nfl/games.jsonl`, ESPN-sourced (`config/model.yaml`'s `sport_data.provider: espn_public`). **Not** the `nflverse` rebuild pipeline — see the model card §7.
- **Formula**: standard Elo expected-score `1 / (1 + 10^(-(rating_home + home_advantage - rating_away)/400))`, with NFL-tuned constants `k=20.0, home_advantage=55.0, offseason_regression=0.50` (`elo_ratings.py:36`).
- **Expected sign**: positive (higher home Elo → higher home win probability). Confirmed: coefficient `+2.6154`.
- **PIT-safe?**: Yes. Rebuilt from `history` strictly before each decision day in both training (`validation.py::build_walk_forward_rows`) and live serving (`learned_forward.py::build_learned_moneyline_slate`, `store.games_before`).
- **Train/serve parity?**: Yes, identical function call in both paths (model card §7).
- **Coverage**: 100% — Elo has a defined value for every team (cold-start teams get `DEFAULT_ELO`), no missingness.
- **Missingness behavior**: N/A (never missing).
- **Correlation notes**: none reported specific to NFL; it is described project-wide as *"the only consistently material fitted signal in the five standard production models"* (`config/tested_features.json`).
- **Coefficient**: `+2.6154014596` — the **smallest** of the 5 sports (MLB 3.319, NBA 3.564, SOCCER 5.562, WNBA 3.134, NFL 2.615).
- **Ablation delta** (`docs/PRODUCTION_FEATURE_ABLATION_2026-07-22.md`, NFL section, 122 holdout obs): omitting it moves holdout Brier from 0.217894 → 0.237480 (**+0.019585**, worse) and log loss +0.042328 (worse); validation Δ +0.021561. Raw p=0.0734, Holm-adjusted p=0.8806 → **strict decision: INCONCLUSIVE** (does not survive multiplicity correction), but the raw p-value is the closest to significance of any NFL feature tested and directionally consistent (holdout, validation, and log loss all move the same way on removal).
- **Calibration impact**: not separately isolated by feature in the available evidence; see the model card's calibration section for the full-model diagnostic.
- **Known bugs**: none found. One flagged open question from the registry, **not NFL-specific but worth carrying forward**: NBA's Elo-alone model calls 88.2% of games above the NBA favorite base rate, and the registry marks a possible leakage-vs-chalky-holdout-window concern as *"UNRESOLVED and under active investigation... Do not build on top of Elo until answered"* — that investigation was scoped to NBA, this audit did not find equivalent evidence of the same pattern in NFL's numbers (NFL's called_rate is 71.3%, not extreme), but it is the same `build_elo` code path and worth keeping in mind if NFL's called rate ever climbs unusually high.
- **Verdict: `KEEP_CORE`** — the dominant, highest-magnitude signal in the model; ablation is directionally supportive even though it doesn't clear the strict multiplicity-adjusted bar at only 122 holdout rows.

## `trend_gap`

- **Model(s) using it**: same five sports as `elo_probability`.
- **Source location**: `src/model_prediction/features/trends.py::TrendEngine.team_trend` → `offensive_momentum`; computed as `home_trend.offensive_momentum - away_trend.offensive_momentum` in both `validation.py:329` and `learned_forward.py:74`.
- **Provider / data source**: same `data/processed/nfl/games.jsonl` history. Shared `trend_analysis` config in `model.yaml`: `method: exponentially_weighted_opponent_adjusted`, `half_lives_games: [3, 10, 25]`, `prior_strength_games: 12`.
- **Formula**: opponent-adjusted EWMA of scoring margin/efficiency across three half-lives, blended; see `TrendEngine._offense_simple`/`opponent_adjusted_ewm_trend`.
- **Expected sign**: positive (hot home team should win more). Confirmed: coefficient `+0.0502`.
- **PIT-safe?**: Yes, same walk-forward construction as `elo_probability`.
- **Train/serve parity?**: Yes, identical call site.
- **Coverage**: 100%, same as Elo (default/prior value for cold-start teams via `prior_strength_games`).
- **Missingness behavior**: N/A.
- **Correlation notes**: `config/tested_features.json` explicitly flags this as *"near-zero coefficients everywhere"* across all 5 sports — the smallest fitted magnitude of any retained feature.
- **Coefficient**: `+0.0502116986`, the largest-magnitude of the 5 sports' `trend_gap` fits (MLB -0.03, NBA -0.004, SOCCER -0.151, WNBA -0.007) but still tiny in absolute terms and even flips sign in MLB/NBA/WNBA/SOCCER vs. NFL.
- **Ablation delta**: omitting it moves NFL holdout Brier 0.217894 → 0.221237 (**+0.003342**, worse) and log loss +0.007759 (worse); validation Δ **-0.002535** (validation actually *improves* on removal). Raw p=0.3587, Holm p=1.0 → **strict decision: INCONCLUSIVE**, and the validation/holdout disagreement in sign is a real inconsistency, not noise-free support for keeping it.
- **Calibration impact**: not separately isolated.
- **Known bugs**: none. The registry's own retest guidance is unusually pointed: *"Never [retest] as-is. If retested it must be as a RESIDUAL trend orthogonalized against elo_probability, not the raw EWMA difference."* — worth honoring if NFL feature work resumes.
- **Verdict: `KEEP`** — retained under the project's zero-threshold directional policy (removal worsens holdout Brier and log loss), but explicitly not a strong or independently-significant signal; treat as inert baggage rather than a lever.

---

## Schedule-load family (`src/model_prediction/features/schedule_load.py`)

Background names `schedule load`, `rest_disparity`, and `games_last_7_gap` individually; the module also emits `back_to_back_gap` and a `schedule_available`/`schedule_missingness` coverage flag as part of the same computation (`matchup_schedule_load`). Covered together because they share one evidence source: `outputs/roadmap_challenger/roadmap-challenger-factorial-v1.json` (generated 2026-07-20) and its dossier.

**Shared context (applies to every row below)**: this evidence was generated against **`nfl-elo-trend-lr-v3`**, not the current production `v4` (dossier: *"Artifact: `config/models/nfl-elo-trend-lr-v3.json`"*), on a smaller split (train 382 / validation 143 / holdout 110, vs. v4's 366/146/122). None of these features are wired into any active NFL model config today (`model.yaml`'s NFL section lists only `elo_probability`/`trend_gap`). All are computed live in `learned_forward.py::_compute_features` whenever an artifact's `feature_names` requests them, but no NFL artifact does.

**A documentation-tone discrepancy worth flagging explicitly** (not a data discrepancy — same underlying numbers, described two different ways in two different docs): `docs/leagues/NFL.md` characterizes this same 2026-07-20 test as an outright failure — *"Simple rest, back-to-back, schedule-density, consistency, hot/cold, and schedule-availability additions failed the 2026-07-20 isolated audit. Keep them out of the predictive roadmap."* `config/tested_features.json` (dated 2026-07-22, citing the identical source file) frames the same result more neutrally as `tested_borderline`, and `docs/FEATURE_REGISTRY.md` echoes that framing. The actual numbers support the softer reading better: the combined `incumbent+consistency+hot_cold+rest_disparity+schedule_density` variant is the dossier's own pick for *"largest directional gain [of any of the 4 sports tested]... but only 110 holdout games and interval crosses zero"* (validation Δ Brier -0.002472, holdout Δ Brier -0.003782, 95% CI [-0.007820, +0.000726]) — a real, directionally-promising, but not statistically established result, not a clean failure. `docs/leagues/NFL.md` should be reconciled or updated; this audit treats `config/tested_features.json`'s framing as authoritative per the project's own stated precedence rule.

### `rest_disparity`

- **Formula**: home rest days minus away rest days, capped (`schedule_load.py::team_schedule_load`/`matchup_schedule_load`).
- **Expected sign**: positive (more rest → better home performance), untested for actual fitted sign in NFL since it's never been in a production NFL artifact.
- **PIT-safe?**: Yes — derived from completed prior games' dates only, same `history` cutoff as Elo/trend.
- **Train/serve parity?**: Same `matchup_schedule_load` call in both `validation.py` and `learned_forward.py` — parity holds structurally, though it has never been exercised in a *promoted* NFL artifact.
- **Coverage / feature distribution** (from the dossier's factorial table, NFL holdout, n=110): 5 unique values, 66.4% exactly zero, mean -0.027, std 0.667, range [-2, 2].
- **Missingness behavior**: `schedule_available` flag (see below) marks whether the underlying schedule data resolved at all; in this dataset it was 100% available (no missingness exercised).
- **Correlation notes**: tested only in combination with `consistency_gap`/`hot_cold_gap`/`schedule_density`, never fully isolated for NFL alone in the available evidence.
- **Ablation delta**: best NFL combo (with consistency+hot_cold+schedule_density) holdout Δ Brier **-0.003782**, 95% CI crosses zero.
- **Known bugs**: none found.
- **Verdict: `RETEST_REQUIRED`** — directionally the most promising schedule signal for NFL of any sport tested, but (a) statistically inconclusive (CI crosses zero), (b) tested against the superseded v3 artifact and a smaller holdout, (c) never independently isolated from `consistency_gap`/`hot_cold_gap` for NFL specifically. Retest against v4 with fresh data before considering promotion.

### `games_last_7_gap`

- **Formula**: home games played in the last 7 days minus away games played in the last 7 days (schedule density).
- **Expected sign**: negative (more recent games → more fatigue → worse home performance), untested fitted sign for NFL.
- **PIT-safe?** / **Train/serve parity?**: same as `rest_disparity` — structurally sound, never in a promoted NFL artifact.
- **Coverage / feature distribution** (holdout, n=110): only 3 unique values, 79.1% exactly zero, mean +0.027, std 0.457. NFL's weekly cadence means most matchups show zero density difference; the signal only fires around bye weeks or short-week (Thursday) games.
- **Missingness behavior**: same `schedule_available` flag, 100% coverage in this window.
- **Correlation notes**: registry notes it is *"strongest in combination with `rest_disparity`+`consistency`+`hot_cold`"* — i.e. not shown to carry independent signal on its own for NFL.
- **Ablation delta**: only evaluated as part of the same combined variant as `rest_disparity` above (holdout Δ Brier -0.003782 for the joint variant); no NFL-specific isolated number found.
- **Known bugs**: none found.
- **Verdict: `RETEST_REQUIRED`** — same reasoning as `rest_disparity`: real but small, sparse (only 3 distinct values, ~79% zero), untested in isolation, evidence predates v4.

### `back_to_back_gap`

- **Formula**: indicator for whether a team is playing on zero days' rest (back-to-back).
- **Coverage / feature distribution** (holdout, n=110): **1 unique value, 100% zero, std 0.0** — structurally degenerate for NFL. The registry itself notes: *"NFL has no variance (all games have similar rest)."* NFL's weekly schedule makes true back-to-backs essentially nonexistent, unlike NBA/WNBA where this feature is meaningful.
- **PIT-safe?** / **Train/serve parity?**: mechanically fine, but there is nothing to learn from a zero-variance column.
- **Ablation delta**: negligible (+0.0001 Brier on top of other schedule combos, i.e. essentially inert), consistent with zero variance.
- **Verdict: `REJECT`** (NFL-specific) — not because it's unsound, but because it is a constant in this sport's data and cannot carry information. This diverges from the registry's overall `tested, marginal` framing (which pools all 5 sports); scoped strictly to NFL, zero variance means reject, full stop, until NFL scheduling ever produces a real back-to-back (e.g. a rescheduled game).

### `schedule_available` / `schedule_missingness`

- **Formula**: coverage/missingness indicator for whether schedule data resolved for a given matchup.
- **Coverage**: 100% in every cohort tested (train/validation/holdout) — no missingness was actually exercised in this dataset.
- **Verdict: `REJECT`** — the dossier explicitly calls out variants including this flag as degenerate: *"Apparent formal wins that include `schedule_available` are rejected as degenerate: the field is constant or almost constant in validation/holdout and acts like a cohort/intercept marker, not a durable predictive signal."* Matches `docs/FEATURE_REGISTRY.md`'s own `tested, marginal` framing but this audit treats the dossier's explicit degenerate-variant rejection as the more precise read for NFL specifically.

## `consistency_gap`

- **Source location**: `src/model_prediction/features/trends.py::TeamTrend.consistency`, computed as `home_trend.consistency - away_trend.consistency`.
- **Model(s) using it**: none currently promoted; computed live only if requested (`learned_forward.py`: `if "consistency_gap" in wanted`).
- **Expected sign**: untested/unfitted for NFL (never in a production artifact).
- **PIT-safe? / Train/serve parity?**: Yes structurally — same `TrendEngine` object already used for `trend_gap`.
- **Coverage / feature distribution** (holdout, n=110): continuous, 0% exact-zero rate, mean -0.0003, std 0.0425 — the least sparse of the candidate schedule/trend additions.
- **Correlation notes**: registry notes NFL is one of only two sports (with NBA) where *"both validation and holdout improve"* when this is added, in combination with `hot_cold_gap`/`rest_disparity`/`schedule_density` — MLB/WNBA show holdout improvement but validation regression, i.e. an overfitting pattern NFL does not share in this evidence.
- **Ablation delta**: only available as part of the joint combo (validation Δ -0.002472, holdout Δ -0.003782); no NFL-specific single-feature isolation found.
- **Verdict: `RETEST_REQUIRED`** — same overall caveats as the schedule-load family (tested against v3, CI crosses zero on the joint variant, never isolated), but flagged as the most consistently-behaved of the group (no train/validation sign disagreement for NFL, unlike MLB/WNBA).

## `hot_cold_gap`

- **Source location**: `src/model_prediction/features/trends.py::TeamTrend.hot_cold_score`, computed as `home_trend.hot_cold_score - away_trend.hot_cold_score`.
- **Model(s) using it**: none currently promoted; same on-demand computation path as `consistency_gap`.
- **Expected sign**: untested/unfitted for NFL.
- **PIT-safe? / Train/serve parity?**: Yes, same as `consistency_gap`.
- **Coverage / feature distribution** (holdout, n=110): continuous, 0.9% exact-zero, mean +0.0156, std 0.422 — widest spread of the candidate group.
- **Correlation notes**: registry: *"Same pattern as `consistency_gap`: NBA/NFL benefit, MLB/WNBA show holdout improvement but validation regression."*
- **Ablation delta**: only evaluated jointly with the rest of the combo (same -0.002472/-0.003782 figures); no isolated NFL number found.
- **Verdict: `RETEST_REQUIRED`** — same reasoning as `consistency_gap`.

---

## nflverse-style candidates: QB state, EPA/dropback, success rate, CPOE, pressure, explosive plays

None of these have a feature module, a `validation.py` `ValidationRow` field, or a `learned_forward.py` dispatch branch anywhere in the repo — confirmed by grepping `src/model_prediction/features/` (no `nfl_*.py` file exists) and `config/tested_features.json` (no entry for any of these names). `docs/MODEL_IMPROVEMENTS.md` §12 independently confirms this is a real, current gap, not an oversight in this audit: *"Build NFL QB/unit-efficiency and injury states. Not started at all."* The roadmap-challenger dossier's "Untestable high-value roadmap additions" table lists NFL's QB/EPA/CPOE/pressure/drive-state group with the reason *"No historical decision-time feature archive exists."*

**What does exist, and where it stops** — this is the part flagged in the task brief as needing direct inspection, and it does change the picture from "nothing exists" to "a raw-first data foundation exists but nothing consumes it":

- `src/model_prediction/rebuild/providers/nflverse.py::NFLVerseProvider` — fetches official nflverse GitHub release Parquet assets (`schedule`, `pbp`, `weekly_rosters`) with content-hash-verified raw capture and schema-drift detection. Real, working HTTP client code.
- `src/model_prediction/rebuild/nfl/normalize.py::_pbp` — **already selects and normalizes `epa` and `success` per play** (`_as_float(row.get("epa"))`, `_as_float(row.get("success"))`), along with `pass_attempt`, `rush_attempt`, `touchdown`, `interception`, `fumble_lost`, `down`, `yards_to_go`, `yardline_100`. It does **not** select `cpoe`, any pressure/pass-rush field, or an explosive-play indicator — those columns exist upstream in nflverse's real pbp release but are simply not read by this normalizer today.
- `src/model_prediction/rebuild/nfl/pit.py::eligible_prior_team_plays`/`eligible_weekly_roster` — real point-in-time filtering logic (only complete, observed, prior-to-decision plays/rosters).
- `src/model_prediction/rebuild/models/nfl.py::NFLModel`/`NFLPrediction` — a **dormant, previously-unflagged** drive-based Monte Carlo score simulator: separate `Ridge` EPA scalers for home/away, a `HistGradientBoostingRegressor` for expected drives, 1000-iteration per-game simulation over a 4-outcome drive distribution (`no_score`/`field_goal`/`touchdown`/`safety`). **Never trained** (no `.fit()` call anywhere in the repo), **never wired** (zero references outside the file itself — confirmed via repo-wide grep), **no test file**. It is essentially a plausible skeleton for exactly the "model expected drives and discrete scoring events" approach `docs/MODEL_IMPROVEMENTS.md` §9 recommends, but it is unevaluated research scaffolding, not evidence of progress.
- **Nothing above ever reaches `validation.py` or `learned_forward.py`.** All of it is confined to the `rebuild/` namespace, which — per `CLAUDE.md` — is a separate clean-slate rebuild track with hard shadow-only/no-production-write boundaries; it is architecturally correct that it doesn't feed the live model, not a bug.

**The central blocker, in the project's own words, twice over**: the `NFLFoundation.backfill` manifest hard-codes `"retrospective_pit_qualified": False` and `"production_allowed": False` for every NFL season captured this way (`foundation.py:97-98`), and the module docstring explains why: *"nflverse releases are mutable snapshots, not historical observation logs... A season/week column never substitutes for evidence that a row was available at an earlier decision time."* `rebuild/nfl/audit.py`'s own `qualification_note` repeats it: *"Mutable nflverse releases captured now are not retrospective PIT evidence; only vintages observed by a decision time are eligible."* This means a single download today of "2015-2025 NFL play-by-play" cannot be used to backtest a walk-forward model honestly — nflverse can and does revise historical rows, and there is no proof what was knowable in real time on any given historical date. The fix is not a code change but calendar time: start capturing daily snapshots now (same `observed_at_utc`-stamped pattern already used for MLB's `starter_era_gap`/bullpen shadow features per `CLAUDE.md`'s "shadow-feature pattern"), and only validate against the portion of history captured that way going forward.

| Feature | Verdict | Why |
|---|---|---|
| `epa_per_dropback` | `BLOCKED_PIT` | Raw `epa` already captured per-play in `rebuild/nfl/normalize.py`; a rolling opponent-adjusted aggregate is a real but moderate build (no `ValidationRow` field, no `learned_forward.py` dispatch, no aggregation module exists yet). The harder blocker is provenance: the source manifest itself is marked `retrospective_pit_qualified: False`, so no walk-forward backtest built on today's bulk download would be trustworthy evidence, regardless of how the feature is aggregated. |
| `success_rate` | `BLOCKED_PIT` | Same situation as `epa_per_dropback` — raw `success` column already captured, same aggregation gap, same PIT provenance blocker. |
| `cpoe` (completion % over expected) | `REBUILD_IMPLEMENTATION` | Not even captured at the normalizer layer today (`_pbp` doesn't select it) — a prerequisite build step before the PIT question is even reachable. Present in nflverse's real upstream pbp release; would need `normalize.py`'s `_pbp` function extended, plus a contract-schema update in `contracts.py`. |
| `pressure` (e.g. pressure rate, time-to-throw) | `REBUILD_IMPLEMENTATION` | Not captured in the normalizer; nflverse's pbp release has partial pressure-adjacent fields (`qb_hit`, sack indicators) but this project's normalizer selects none of them today. Also flagged by `docs/MODEL_IMPROVEMENTS.md` §9 rank 4 as partly proprietary/aggregated outside nflverse for full pressure detail — use only the reproducible nflverse-native fields. |
| `explosive_plays` (rate of 20+/40+ yard gains) | `REBUILD_IMPLEMENTATION` | Directly derivable from already-captured `yardline_100`/play outcome fields with a threshold rule, but no aggregation module or feature wiring exists; same PIT-provenance caveat applies once built. |
| `qb_state` (expected starter, backup probability, injury/practice status) | `BLOCKED_PIT` | `weekly_rosters` normalizer exists and captures season/week-vintage roster snapshots with real `observed_at_utc`/`effective_at_utc` provenance, but that is a *weekly* vintage, not a live-updating injury/practice-report timeline — the roadmap-challenger dossier states directly: *"No historical decision-time feature archive exists"* for this group. This is the single highest-priority item in `docs/MODEL_IMPROVEMENTS.md`'s own NFL ranking (rank 1: *"QB is the highest-impact identity feature and a primary no-call gate"*), but building it requires new prospective daily capture infrastructure (official practice-report timestamps, active/inactive confirmations), not just reading more of what nflverse already publishes. |

---

## `head_to_head`

- **File**: `src/model_prediction/features/head_to_head.py`.
- **Sports**: MLB, NBA, WNBA, NFL, SOCCER.
- **Measured effect** (`config/tested_features.json`, evidence grade B, `evaluate_orphaned_features.py`): *"+0.11pp to +0.61pp accuracy across all five sports, every result within ~1 standard error"* — noise everywhere it could be measured.
- **NFL-specific note**: *"NFL was untestable at only 10% non-zero coverage"* — i.e. the NFL rejection is not on the same footing as the other four sports' rejections; NFL simply didn't have enough head-to-head history coverage to test at all, so its inclusion in the blanket "reject, all 5 sports" line is really "reject-by-default due to insufficient data," not "reject on measured evidence" the way MLB/NBA/WNBA/SOCCER were.
- **Registry status**: `status: registered_orphan` — imported by `features/__init__.py`, registered, but never requested by `learned_forward.py` for any sport.
- **Re-test condition (quoted verbatim, per the task brief's request)**: `config/tested_features.json`'s `retest_when` field for this feature reads:

  > **"NFL only, and only after H2H coverage exceeds 50%. Never retest the other four sports."**

  `docs/FEATURE_REGISTRY.md`'s summary table doesn't carry this NFL-specific carve-out explicitly (it lists `head_to_head | all 5 | reject | +0.11pp to +0.61pp, all inside 1 SE.` as one flat line) — the JSON is the more precise and authoritative source here, consistent with the project's own stated precedence rule ("when they disagree, the JSON wins").
- **Verdict: `RETEST_REQUIRED`** for NFL specifically (contingent on coverage crossing the stated 50% threshold — not yet checked as part of this audit; coverage would need to be re-measured against current data before a retest is warranted). **`REJECT`** stands, unchanged, for MLB/NBA/WNBA/SOCCER — consistent with the task brief's framing, and this audit found no evidence to disturb that.

---

## Summary table

| Feature | Verdict |
|---|---|
| `elo_probability` | `KEEP_CORE` |
| `trend_gap` | `KEEP` |
| `rest_disparity` | `RETEST_REQUIRED` |
| `games_last_7_gap` | `RETEST_REQUIRED` |
| `back_to_back_gap` (NFL) | `REJECT` |
| `schedule_available`/`schedule_missingness` | `REJECT` |
| `consistency_gap` | `RETEST_REQUIRED` |
| `hot_cold_gap` | `RETEST_REQUIRED` |
| `epa_per_dropback` | `BLOCKED_PIT` |
| `success_rate` | `BLOCKED_PIT` |
| `cpoe` | `REBUILD_IMPLEMENTATION` |
| `pressure` | `REBUILD_IMPLEMENTATION` |
| `explosive_plays` | `REBUILD_IMPLEMENTATION` |
| `qb_state` | `BLOCKED_PIT` |
| `head_to_head` (NFL) | `RETEST_REQUIRED` (contingent on >50% coverage) |
| `head_to_head` (MLB/NBA/WNBA/SOCCER) | `REJECT` (unchanged, out of scope for this doc) |
