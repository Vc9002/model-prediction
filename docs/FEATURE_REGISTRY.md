# Feature registry — what has been tested, and what must not be re-tested

Machine-readable source of truth: `config/tested_features.json`. This document is the
human summary. When they disagree, the JSON wins.

## The rule

**Before proposing, building, or testing any feature, check `config/tested_features.json`.**
A feature with verdict `reject` or `exclude` must not be re-tested unless its
`retest_when` condition is satisfied. Record every new evaluation there, including
rejections. A rejection backed by numbers is a result worth keeping — it is the only
thing that stops the same dead end being explored a fourth time.

## Current state: 20 features tracked, 4 keep, 3 reject, 4 exclude, 6 untested, 3 pending/under-test

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `elo_probability` | all 5 | **keep** | The only load-bearing signal anywhere. Coefficients 2.6–5.6. |
| `park_factor` | MLB | **keep** | Coefficient −1.05. The only non-Elo feature in production with real weight. |
| `player_availability` | WNBA | **keep** | +1.4pp accuracy, −2.3% Brier, +3.82U on a 142-game cohort. Best-evidenced non-Elo win in the project. |
| `weather_factor` | MLB | pending | Coefficient −0.318. Needs a leave-one-out to survive its own SE. |
| `trend_gap` | all 5 | pending | Coefficients −0.004 to −0.151. Inert. Retest only as an Elo-orthogonalized residual. |
| `defensive_trend_gap` | NBA/WNBA | pending | Coefficients −0.013 / −0.003. Inert. |
| `pitcher_era_gap` | MLB | under test | Coefficient 0.022. In head-to-head vs `starter_era_gap`. |
| `starter_era_gap` | MLB | under test | Variant exists; source file has zero imports. Verify coverage first. |
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
2. **"Esports/international baseball were never validated."** False. CS2 alone has n=7,578
   in its locked test. `units: 0` is the research-only design, not a missing step.
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
