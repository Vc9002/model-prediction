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

## Current state: 27 features tracked, 5 keep, 3 remove candidates, 3 reject, 1 remove, 4 exclude, 5 tested (borderline), 2 tested (marginal), 5 untested

### Production features (active in current models)

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `elo_probability` | all 5 | **keep** | Strict KEEP for NBA/SOCCER; directional KEEP for MLB/NFL/WNBA. The only materially-sized signal in the project. |
| `trend_gap` | all 5 | **keep** | Directional KEEP for NBA/NFL/WNBA; directional removal candidate for MLB/SOCCER. Near-zero coefficients everywhere. |
| `park_factor` | MLB | **keep, research only** | Tiny positive holdout contribution, but static cross-season provenance is blocked. Not production-safe. |
| `weather_factor` | MLB | **keep, research only** | Tiny positive validation/holdout contribution, but forecast timestamps are missing. Not production-safe. |
| `pitcher_era_gap` | MLB | **keep (operator override)** | Directional removal candidate (Brier -0.0003/-0.0002) but kept: removal costs 3.4 units at frozen threshold. Profit over accuracy. |

### Tested via Roadmap Challenger (v3 incumbents, factorial design)

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `schedule_load` | MLB, NBA, WNBA, NFL | **tested, borderline** | Sub-features (rest_disparity, back_to_back_gap, games_last_7_gap, schedule_missingness) tested. NBA best: -0.0012 holdout, -0.0010 validation. NFL: -0.0038 holdout. MLB/WNBA: holdout improves but validation regresses (overfitting). |
| `consistency_gap` | MLB, NBA, WNBA, NFL | **tested, borderline** | NBA/NFL: both validation and holdout improve. MLB/WNBA: holdout improves, validation regresses. Best in combo with hot_cold+schedule. |
| `hot_cold_gap` | MLB, NBA, WNBA, NFL | **tested, borderline** | Same pattern as consistency_gap. NBA/NFL benefit; MLB/WNBA show overfitting. |
| `rest_disparity` | MLB, NBA, WNBA, NFL | **tested, borderline** | Home-minus-away rest days. NBA: -0.0012 holdout improvement in best combo. NFL: -0.0038. MLB: -0.0004 but validation regresses. |
| `games_last_7_gap` | MLB, NBA, WNBA, NFL | **tested, borderline** | Schedule density. Strongest in combination with rest_disparity+consistency+hot_cold. |
| `back_to_back_gap` | MLB, NBA, WNBA, NFL | **tested, marginal** | Adds ~0.0001 Brier on top of rest+schedule. NFL has no variance. |
| `schedule_missingness` | MLB, NBA, WNBA, NFL | **tested, marginal** | Availability indicator. Consistently appears in top variants — helps model distinguish real zeros from missing data. |

### Rejected or removed

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `defensive_trend_gap` | NBA/WNBA | remove candidate | Removal improves both validation and holdout proper scores in both leagues. |
| `starter_era_gap` | MLB | **remove** | Unservable train/serve skew; shipped broken in v4, removed in v5. Never reinstate. |
| `starting_pitcher_fip` | MLB | **reject** | 84% coverage, zero effect. Collinear with `pitcher_era_gap`. |
| `head_to_head` | all 5 | **reject** | +0.11pp to +0.61pp, all inside 1 SE. NFL untestable at 10% coverage. |
| `lineup_strength` | NBA/WNBA | **reject** | +0.05pp / 0.00pp. Noise. |
| `market_signals` | — | **exclude** | Architectural. Violates market isolation. |
| `guaranteed_signal` | — | **exclude** | Post-hoc tag, not an input. |
| `tennis_surface` | TENNIS | **exclude** | No active tennis model. |
| `confidence_gate` | — | **exclude** | Infrastructure, not a model input. |

### Untested or blocked

| Feature | Sports | Verdict | Why |
|---|---|---|---|
| `neutral_elo_rating_difference` | LOL/CS2/Dota2/Valorant | **untested** | v3 artifacts hash-invalid, no locked metrics. Core feature of the tiered-elo models. |
| `tie_aware_elo_rating_difference` | KBO/NPB | **untested** | Locked backfills exist but no omission study. Core feature of tie-aware models. |
| `bullpen` | MLB | **untested** | Code exists (features/bullpen.py) but data source passes None — needs StatsAPI integration. |
| `team_runs` | MLB | **untested** | Supplies `pitcher_era_gap`; run differential outputs computed but never reach feature vector. |
| `trailing_home_win_rate_30d` | MLB | **untested** | Adaptive-HFA variant in validation.py; researched but not formally ablated. |
| `player_availability` | WNBA | **keep** | Grade-B post-hoc research evidence; not in current exact-artifact ablation. |

## Key findings from roadmap challenger (2026-07-22)

1. **NBA is the best target for schedule features.** The combination consistency+hot_cold+rest_disparity+schedule_density+schedule_missingness improves both validation (-0.0010) and holdout (-0.0012) Brier. This is the most promising untested addition in the project.

2. **NFL shows the largest raw improvements** (-0.0025 val, -0.0038 hold) but the sample is tiny (110 games). Need more data before promoting.

3. **MLB does not benefit from schedule/momentum additions.** Every variant improves holdout but regresses on validation — textbook overfitting. The incumbent v3 model was already well-tuned.

4. **WNBA pattern matches MLB**: holdout improvement, validation regression.

5. **All features are already wired in `learned_forward.py`** — they're gated on the artifact's feature_names list. Promoting a feature means adding its name to the model config, retraining, and re-running the locked-holdout ablation.

## Next steps for promotion

1. Create `nba-elo-trend-lr-v5` with consistency_gap + hot_cold_gap + rest_disparity + games_last_7_gap + schedule_missingness
2. Run full 60/20/20 split on the new artifact
3. Ablate each feature individually against the new baseline
4. If holdout improves and validation doesn't regress: promote to shadow_qualified

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
