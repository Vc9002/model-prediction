# Feature registry — what has been tested, and what must not be re-tested

**Last updated**: 2026-08-11 (model/feature reconciliation audit corrections — tennis_surface,
bullpen, defensive_trend_gap, player_availability, NBA Elo leakage question resolved)

Machine-readable source of truth: `config/tested_features.json`. This document is the
human summary. When they disagree, the JSON wins.

**2026-08-11 audit**: a full model/feature reconciliation audit ran against this registry
and the models that consume it — see `docs/model_audit/MODEL_INVENTORY.md`,
`docs/model_audit/FEATURE_RETENTION_MATRIX.md`, and the per-sport files under
`docs/model_audit/models/` and `docs/model_audit/features/` for full evidence and citations
behind every correction below.

## The rule

**Before proposing, building, or testing any feature, check `config/tested_features.json`.**
A feature with verdict `reject` or `exclude` must not be re-tested unless its
`retest_when` condition is satisfied. Record every new evaluation there, including
rejections. A rejection backed by numbers is a result worth keeping — it is the only
thing that stops the same dead end being explored a fourth time.

Retention uses Vincent's zero-threshold directional policy: keep a feature when its
omission worsens validation Brier or both locked-holdout proper scores by any positive
amount. The registry also preserves the stricter multiplicity-adjusted decision.
`KEEP` with blocked point-in-time provenance means research retention only; it does not
make the feature production-safe or establish profitability.

## Current state: 25 features tracked, 6 keep, 3 remove candidates, 3 reject, 1 exclude, 5 tested (borderline), 2 tested (marginal), 5 untested

### Production features (active in current models)

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `elo_probability` | all 5 | **keep** | Strict KEEP for NBA/SOCCER; directional KEEP for MLB/NFL/WNBA. The only materially-sized signal in the project. |
| `trend_gap` | all 5 | **keep** | Directional KEEP for NBA/NFL/WNBA; directional removal candidate for MLB/SOCCER. Near-zero coefficients everywhere. |
| `park_factor` | MLB | **keep, research only** | Tiny positive holdout contribution, but static cross-season provenance is blocked. Not production-safe. |
| `weather_factor` | MLB | **keep, research only** | Tiny positive validation/holdout contribution, but forecast timestamps are missing. Not production-safe. |
| `pitcher_era_gap` | MLB | **keep (operator override)** | Directional removal candidate (Brier -0.0003/-0.0002) but kept: removal costs 3.4 units at frozen threshold. Profit over accuracy. |

### Tested via Roadmap Challenger (factorial design)

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `schedule_load` | MLB, NBA, WNBA, NFL | **tested, borderline** | NBA best: -0.0012 holdout, -0.0010 validation. NFL: -0.0038 holdout. MLB/WNBA: holdout improves but validation regresses. |
| `consistency_gap` | MLB, NBA, WNBA, NFL | **tested, borderline** | NBA/NFL: both validation and holdout improve. MLB/WNBA: holdout improves, validation regresses. |
| `hot_cold_gap` | MLB, NBA, WNBA, NFL | **tested, borderline** | Same pattern as consistency_gap. |
| `rest_disparity` | MLB, NBA, WNBA, NFL | **tested, borderline** | Home-minus-away rest days. NBA: -0.0012 holdout in best combo. |
| `games_last_7_gap` | MLB, NBA, WNBA, NFL | **tested, borderline** | Strongest in combination with rest_disparity+consistency+hot_cold. |
| `back_to_back_gap` | MLB, NBA, WNBA, NFL | **tested, marginal** | Adds ~0.0001 Brier on top of rest+schedule. |
| `schedule_missingness` | MLB, NBA, WNBA, NFL | **tested, marginal** | Helps model distinguish real zeros from missing data. |

### Rejected or removed

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `defensive_trend_gap` | NBA/WNBA | remove candidate | Removal improves both validation and holdout proper scores. |
| `starter_era_gap` | MLB | **remove** | Shipped broken in v4, removed in v5. Formally ablated 2026-08-01: removal *improves* every metric. Never reinstate. **2026-08-10 addendum**: a real, architecturally-different `starter_era_gap_live` provider now exists (`features/starter_history.py`, PIT-safe rolling per-starter history, not the old unservable event_id map) — dormant, not in any promoted model's feature list. Original verdict stands until an operator re-evaluates it; see `config/tested_features.json`. |
| `starting_pitcher_fip` | MLB | **reject** | 84% coverage, zero effect. Collinear with `pitcher_era_gap`. |
| `head_to_head` | all 5 | **reject** | +0.11pp to +0.61pp, all inside 1 SE. |
| `lineup_strength` | NBA/WNBA | **reject** | +0.05pp / 0.00pp. Noise. |
| `tennis_surface` | TENNIS | **exclude** | Only this specific registered feature *function* is dead (different file/PIT mechanism, never called). **CORRECTED 2026-08-11**: the old reason ("no active tennis model") was false — `tennis-surface-elo-v1` is active, shadow_qualified, and blends per-surface Elo at 60% weight into every match probability. Surface signal is very much live; this one artifact just isn't how it gets there. |

### Untested or blocked

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `neutral_elo_rating_difference` | LOL/CS2/Dota2/Valorant/RainbowSix | **untested** | v5 artifacts are now hash-valid (2026-07-31 rebuild); omission study still not run. |
| `tie_aware_elo_rating_difference` | KBO/NPB | **untested** | Locked backfills exist but no omission study. |
| `bullpen` (as `bullpen_weakness_gap`) | MLB | **keep** | **CORRECTED 2026-08-11**: this row was stale. `data_sources/espn.py` does pass real values (not None); the feature is live-wired in `learned_forward.py` with fitted, non-zero coefficients in both `mlb-elo-trend-lr-v7` (0.0729) and current `mlb-elo-trend-lr-v8` (0.1520). Formal with/without ablation on v8 is still open. |
| `team_runs` | MLB | **untested** | Run differential outputs computed but never reach feature vector. |
| `trailing_home_win_rate_30d` | MLB | **untested** | Adaptive-HFA variant; researched but not formally ablated. |
| `player_availability` | WNBA | **keep** | Grade-B post-hoc research evidence; not in current exact-artifact ablation. |
| `probable_starter_unavailable` | MLB | **untested (new)** | Shadow feature (`features/mlb_player_availability.py`), wired 2026-08-02. Computed and logged only; never adjusts a live forecast. |
| `starter_fip_gap` | MLB | **untested (new)** | Shadow feature (`features/starter_history.py`), wired 2026-08-05 (F-68). Not `starting_pitcher_fip` (different mechanism, not the collinear one above). Code comment claims "+1pp hit rate, -39% ECE, +11 units vs ERA" from a locked holdout; not independently re-verified or backed by a committed evaluation artifact. Dormant, not in any promoted model. |

## Key findings from roadmap challenger (2026-07-22)

1. **NBA is the best target for schedule features.** The combination consistency+hot_cold+rest_disparity+schedule_density+schedule_missingness improves both validation (-0.0010) and holdout (-0.0012) Brier.

2. **NFL shows the largest raw improvements** (-0.0025 val, -0.0038 hold) but the sample is tiny (110 games). Need more data before promoting.

3. **MLB does not benefit from schedule/momentum additions.** Every variant improves holdout but regresses on validation — textbook overfitting.

4. **WNBA pattern matches MLB**: holdout improvement, validation regression.

5. **All features are already wired in `learned_forward.py`** — they're gated on the artifact's feature_names list.

## Next steps for promotion

1. Create `nba-elo-trend-lr-v5` with consistency_gap + hot_cold_gap + rest_disparity + games_last_7_gap + schedule_missingness
2. Run full 60/20/20 split on the new artifact
3. Ablate each feature individually against the new baseline
4. If holdout improves and validation doesn't regress: promote to shadow_qualified

## Five claims that were wrong and must not be repeated

1. **"MLB is underconfident, 60% → 75%."** False, and backwards. The bucket holding 237 of
   254 calls sits at 63.4% predicted vs 55.3% actual — *over*confident by ~8pp.
2. **"Current esports v3 inherits the validated v2 evidence."** False. The v2 baselines
   have locked-test results; v3 artifacts were hash-invalid and research-state. **Updated
   2026-08-02**: v5 artifacts (2026-07-31 rebuild) use proper scoring rules for
   K/threshold selection, are hash-valid, and have real locked metrics. The claim that
   v3 was valid was still wrong — it wasn't — but the current state has moved past it.
3. **"NBA/WNBA win because of `defensive_trend_gap`."** False. Coefficients −0.013 and −0.003.
4. **"Zero calibration anywhere."** Misleading. Diagnostics are recorded in every artifact;
   what is missing is an *applied* calibrator at serving time.
5. **"`soccer_form.py`, `rest_travel.py`, `data_sources/pitchers.py` are dead code."** Those
   files do not exist. (Other orphaned modules do exist — see `ENGINEERING_ROADMAP.md` §2).

## The question that used to outrank all feature work — now resolved

**RESOLVED 2026-08-11** (audit, `docs/model_audit/models/NBA_ELO_TREND_LR_V4.md`):
**ELO INTEGRITY CONFIRMED — no leakage.** NBA v4 hits **73.66%** while calling **88.2%** of
games, from a model whose only meaningful coefficient is `elo_probability` at 3.564 — above
the NBA favorite base rate, which is exactly what made this an open question worth closing
before building anything else on top of Elo. The audit traced 67 real historical games
across 11 game-days (2024-01-09 through 2026-02-01, covering regular season, playoffs, and
season openers) and found every sampled event's rating-update timestamps strictly precede
its own event start — zero invariant violations. The same snapshot-then-append ordering was
independently confirmed in both the training walk-forward loop and the live serving path,
and a calibration slope of 1.785 (under-confident) is the opposite signature of what a leak
typically produces. The strong result is best explained by a genuinely well-separated Elo
signal combined with an 88%-selective confidence gate — the model still hits 70.2% fully
unselective. Two lower-severity, non-leakage data-quality items surfaced for a future v5:
NBA preseason/All-Star games aren't excluded from Elo history the way MLB excludes its own,
and there's no true neutral-site override. Feature work on top of `nba-elo-trend-lr-v4` may
now proceed.
