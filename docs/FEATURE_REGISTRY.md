# Feature registry — what has been tested, and what must not be re-tested

Machine-readable source of truth: `config/tested_features.json`. This document is the
human summary. When they disagree, the JSON wins.

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

## Current state: 22 features tracked, 5 keep, 3 remove candidates, 3 reject, 4 exclude, 7 untested

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `elo_probability` | all 5 | **keep** | Strict KEEP for NBA/SOCCER; directional KEEP for MLB/NFL/WNBA under the zero-threshold policy. |
| `trend_gap` | all 5 | **keep** | Directional KEEP for NBA/NFL/WNBA; directional removal candidate for MLB/SOCCER. Strict decisions remain inconclusive. |
| `park_factor` | MLB | **keep, research only** | Tiny positive holdout contribution, but static cross-season provenance is blocked. Not production-safe. |
| `weather_factor` | MLB | **keep, research only** | Tiny positive validation/holdout contribution, but forecast timestamps are missing. Not production-safe. |
| `player_availability` | WNBA | **keep** | Grade-B post-hoc research evidence; not part of the current exact-artifact strict ablation. |
| `neutral_elo_rating_difference` | LOL/CS2/Dota2/Valorant | untested | Current v3 artifacts are hash-invalid and lack locked metrics; do not inherit v2 evidence. |
| `defensive_trend_gap` | NBA/WNBA | remove candidate | Removal improves both validation and holdout proper scores in both leagues. |
| `pitcher_era_gap` | MLB | remove candidate | Removal improves validation and both holdout proper scores. |
| `starter_era_gap` | MLB | **remove** | Unservable train/serve skew; removed in MLB v5. Never reinstate as-is. |
| `starting_pitcher_fip` | MLB | **reject** | 84% coverage, zero effect. Collinear with `pitcher_era_gap`. |
| `head_to_head` | all 5 | **reject** | +0.11pp to +0.61pp, all inside 1 SE. NFL untestable at 10% coverage. |
| `lineup_strength` | NBA/WNBA | **reject** | +0.05pp / 0.00pp. Noise. |
| `market_signals` | — | **exclude** | Architectural. Violates market isolation. Residual layer only, never the outcome model. |
| `guaranteed_signal` | — | **exclude** | Not an input. A post-hoc tag. Not ablatable. |
| `tennis_surface` | TENNIS | **exclude** | No active tennis model. Untestable, not rejected. |
| `confidence_gate` | — | **exclude** | Threshold infrastructure, not a model input. |
| `schedule_load` | NBA/WNBA | untested | **Most promising untested feature in the repo.** Rest/travel is a real NBA/WNBA effect. |
| `bullpen` | MLB | untested | One import, never reaches a feature vector. |
| `team_runs` | MLB | untested | Supplies `pitcher_era_gap`; other outputs unused. |
| `consistency_gap`, `hot_cold_gap` | — | untested | In the `elo_trend_full` variant, never selected for production. |
| `trailing_home_win_rate_30d` | MLB | untested | Adaptive-HFA variant, researched but not promoted. |

## Five claims that were wrong and must not be repeated

1. **"MLB is underconfident, 60% → 75%."** False, and backwards. The bucket holding 237 of
   254 calls sits at 63.4% predicted vs 55.3% actual — *over*confident by ~8pp.
2. **"Current esports v3 inherits the validated v2 evidence."** False. The v2 baselines
   have locked-test results; current LOL/CS2/Dota2/Valorant v3 artifacts are hash-invalid,
   research-state, unqualified, and missing locked metrics.
3. **"NBA/WNBA win because of `defensive_trend_gap`."** False. Coefficients −0.013 and −0.003.
4. **"Zero calibration anywhere."** Misleading. Diagnostics are recorded in every artifact;
   what is missing is an *applied* calibrator at serving time.
5. **"`soccer_form.py`, `rest_travel.py`, `data_sources/pitchers.py` are dead code."** Those
   files do not exist.

## The unresolved question that outranks all feature work

NBA v4 hits **73.66%** while calling **88.2%** of games, from a model whose only meaningful
coefficient is `elo_probability` at 3.564. That is above the NBA favorite base rate. Either
the Elo ratings leak the game being predicted, the holdout window was unusually chalky, or
it is real. Until this is answered, do not build on top of Elo — a leak here would
invalidate the largest model in the project and everything stacked on it.
