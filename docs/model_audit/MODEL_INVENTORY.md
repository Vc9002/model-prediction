# MODEL_INVENTORY.md

Inventory of every model found in `config/model.yaml`, `config/models/**/*.json` (including `archive/` and `challengers/`), `src/model_prediction/models/`, `src/model_prediction/rebuild/models/`, and the equivalent locations on the six historical rebuild branches (`origin/rebuild/clean-slate-v1`, `origin/rebuild/mlb-v3-research`, `origin/rebuild/wnba-v1`, `origin/rebuild/nfl-v1`, `origin/rebuild/soccer-v1`, `origin/rebuild/tennis-v1`, `origin/fix/mlb-v2-prospective-ops`), inspected via `git show`/`git log`/`git diff` without checking any branch out. All refs pinned by the `archive/*` tags listed in the task background.

**Scope note on 'current main':** this repo's default branch is far ahead of every historical rebuild branch (each diverges at a shared merge-base and has only a handful of unique, unmerged commits -- see `ARCHIVE_RECOVERY_MAP.md` for the per-branch divergence detail). Historical-branch content that is byte-identical to current main is recorded here as `currently_callable_from_main` / `historically_callable`, not duplicated as a separate record, unless the branch version differs materially (e.g. the WNBA research baseline ensemble, which never reached main at all).

**Total records: 97**. Machine-readable twin: `outputs/rebuild/audit/model_inventory.json` (same records, one JSON object each, same field names as the definition-list keys below).

## Recommendation summary

| Recommendation | Count |
|---|---|
| RETIRE | 53 |
| KEEP_PRIMARY | 11 |
| KEEP_CHALLENGER | 11 |
| KEEP_ROLLBACK | 10 |
| KEEP_BASELINE | 6 |
| REBUILD_DISTRIBUTION_HEAD | 2 |
| CALIBRATE | 2 |
| REPLACE_ONLY_IF_AUDIT_FAILS | 1 |
| REPAIR_SERVING | 1 |

## Overview

One row per model record. `Callable` = currently callable from current main (condensed to yes/no/partial). Full detail (features, training window, qualification numbers, defects, notes) is in the per-model sections below the table.

| Model ID | Sport | Capability | Family | Status | Recommendation | Callable |
|---|---|---|---|---|---|---|
| [measured-edge-margin-v1](#measured-edge-margin-v1) | MLB | spread | flat_probability_shrinkage_ols_calibration | superseded | **RETIRE** | no |
| [measured-edge-margin-v2](#measured-edge-margin-v2) | MLB | spread | flat_probability_shrinkage_ols_calibration | superseded | **RETIRE** | no |
| [measured-edge-margin-v3](#measured-edge-margin-v3) | MLB | spread | flat_probability_shrinkage_ols_calibration | active_research | **KEEP_BASELINE** | yes |
| [measured-edge-totals-v1](#measured-edge-totals-v1) | MLB | total | flat_probability_shrinkage_ols_calibration | superseded | **RETIRE** | no |
| [measured-edge-totals-v2](#measured-edge-totals-v2) | MLB | total | flat_probability_shrinkage_ols_calibration | superseded | **RETIRE** | no |
| [measured-edge-totals-v3](#measured-edge-totals-v3) | MLB | total | flat_probability_shrinkage_ols_calibration | active_research, flagged problem_cohort | **REBUILD_DISTRIBUTION_HEAD** | yes |
| [mlb-analyst-poisson-trend-v0.2](#mlb-analyst-poisson-trend-v0-2) | MLB | spread, total | poisson_run_simulation_with_hand_tuned_factors | superseded | **RETIRE** | no |
| [mlb-analyst-poisson-trend-v0.3](#mlb-analyst-poisson-trend-v0-3) | MLB | spread, total | poisson_run_simulation_with_ols_calibration | active_research (problem_cohorts.tota... | **REBUILD_DISTRIBUTION_HEAD** | yes |
| [mlb-elo-trend-lr-v1](#mlb-elo-trend-lr-v1) | MLB | moneyline | elo_trend_logistic_regression | archived | **RETIRE** | no |
| [mlb-elo-trend-lr-v2](#mlb-elo-trend-lr-v2) | MLB | moneyline | elo_trend_logistic_regression | archived | **RETIRE** | no |
| [mlb-elo-trend-lr-v3](#mlb-elo-trend-lr-v3) | MLB | moneyline | elo_trend_logistic_regression | archived | **RETIRE** | no |
| [mlb-elo-trend-lr-v4](#mlb-elo-trend-lr-v4) | MLB | moneyline | elo_trend_logistic_regression | archived | **RETIRE** | no |
| [mlb-elo-trend-lr-v5](#mlb-elo-trend-lr-v5) | MLB | moneyline | elo_trend_logistic_regression | retired | **RETIRE** | no |
| [mlb-elo-trend-lr-v6](#mlb-elo-trend-lr-v6) | MLB | moneyline | elo_trend_logistic_regression | retired | **RETIRE** | no |
| [mlb-elo-trend-lr-v7](#mlb-elo-trend-lr-v7) | MLB | moneyline | elo_trend_logistic_regression | rollback / legacy_research_rollback | **KEEP_ROLLBACK** | yes |
| [mlb-elo-trend-lr-v8](#mlb-elo-trend-lr-v8) | MLB | moneyline | elo_trend_logistic_regression | shadow_qualified (config), qualificat... | **KEEP_PRIMARY** | yes |
| [mlb-spread-baseline-v1](#mlb-spread-baseline-v1) | MLB | spread, total | baseline_heuristic (elo_margin / league_avg_total) | orphaned | **RETIRE** | no |
| [mlb-total-score-ridge-v1](#mlb-total-score-ridge-v1) | MLB | total (raw score regression) | ridge_regression | research_score_model_candidate | **KEEP_CHALLENGER** | partial |
| [mlb-two-head-real-features-v1](#mlb-two-head-real-features-v1) | MLB | moneyline, run line (spread), total, expected score | independent_poisson two-head (sklearn: HistGradientBoosti... | RESEARCH_ONLY | **KEEP_CHALLENGER** | yes |
| [mlb-two-head-v1 (early sklearn baseline, superseded)](#mlb-two-head-v1-early-sklearn-baseline-superseded) | MLB | moneyline | independent_poisson two-head (sklearn: HistGradientBoosti... | superseded by mlb-two-head-real-featu... | **RETIRE** | yes |
| [mlb-two_head-calibrator-v1](#mlb-two-head-calibrator-v1) | MLB | calibration support artifact (moneyline) | temperature scaling | support artifact for a KEEP_CHALLENGE... | **KEEP_CHALLENGER** | yes |
| [mlb-v0.2-platt-2026-07-07-to-10-v1](#mlb-v0-2-platt-2026-07-07-to-10-v1) | MLB | spread/total calibration (superseded) | platt_scaling | superseded | **RETIRE** | no |
| [mlb-xgb_direct-calibrator-v1](#mlb-xgb-direct-calibrator-v1) | MLB | calibration support artifact (moneyline) | temperature scaling | support artifact for a KEEP_CHALLENGE... | **KEEP_CHALLENGER** | yes |
| [mlb-xgb_two_head-calibrator-v1](#mlb-xgb-two-head-calibrator-v1) | MLB | calibration support artifact (moneyline) | temperature scaling | support artifact for a KEEP_CHALLENGE... | **KEEP_CHALLENGER** | yes |
| [mlb-xgb_two_head_negative_binomial-calibrator-v1](#mlb-xgb-two-head-negative-binomial-calibrator-v1) | MLB | calibration support artifact (moneyline) -- the FROZEN primary challenger's calibrator | temperature scaling | support artifact for the frozen mlb_m... | **KEEP_CHALLENGER** | yes |
| [mlb_moneyline_v2_frozen_v1 (XGBoost two-head + negative-binomial, frozen candidate)](#mlb-moneyline-v2-frozen-v1-xgboost-two-head-negative-binomial-frozen-candidate) | MLB | moneyline (primary); spread/total explicitly rejected as independent challenger outputs per CLAUDE.md's rule that a disconnected classifier may contribute disagreement evidence but never generate spread/total on its own | XGBoostTwoHeadModel (intensity head + differential head) ... | frozen, prepared, NOT YET SEALED | **KEEP_CHALLENGER** | yes |
| [mlb_trend_score_v2 (rejected backtest challenger)](#mlb-trend-score-v2-rejected-backtest-challenger) | MLB | score/trend challenger (unspecified market in the card) | unspecified in docs/mlb_trend_score_v2/MODEL_CARD.md | Rejected backtest challenger. Zero un... | **RETIRE** | no |
| [production-feature-ablation-2026-07-22 (diagnostic artifact, not a deployable model)](#production-feature-ablation-2026-07-22-diagnostic-artifact-not-a-deployable-model) | MLB/NBA/WNBA/NFL (cross-sport) | n/a - leave-one-feature-out ablation study over configured production_artifact entries | n/a - diagnostic evidence artifact | stale diagnostic snapshot | **RETIRE** | no |
| [nba-elo-trend-lr-v1](#nba-elo-trend-lr-v1) | NBA | moneyline | elo_trend_logistic_regression | archived | **KEEP_ROLLBACK** | no |
| [nba-elo-trend-lr-v2](#nba-elo-trend-lr-v2) | NBA | moneyline | elo_trend_logistic_regression | archived | **RETIRE** | no |
| [nba-elo-trend-lr-v3](#nba-elo-trend-lr-v3) | NBA | moneyline | elo_trend_logistic_regression | archived | **KEEP_ROLLBACK** | no |
| [nba-elo-trend-lr-v4](#nba-elo-trend-lr-v4) | NBA | moneyline | elo_trend_normal_approximation (moneyline head: logistic ... | shadow_qualified | **KEEP_PRIMARY** | yes |
| [nba-spread-baseline-v1](#nba-spread-baseline-v1) | NBA | spread, total | baseline_heuristic | active_research | **KEEP_BASELINE** | yes |
| [nba-total-score-ridge-v1](#nba-total-score-ridge-v1) | NBA | total (raw score regression) | ridge_regression | research_score_model_candidate | **KEEP_CHALLENGER** | partial |
| [nba-wnba-possessions-efficiency-v1 (rebuild/models/basketball.py, orphaned)](#nba-wnba-possessions-efficiency-v1-rebuild-models-basketball-py-orphaned) | NBA/WNBA | moneyline/spread/total via one joint score distribution | PossessionsModel (Ridge) for pace + efficiency model (His... | dead code / unwired | **RETIRE** | no |
| [wnba-elo-trend-lr-v1](#wnba-elo-trend-lr-v1) | WNBA | moneyline | elo_trend_logistic_regression | archived | **KEEP_ROLLBACK** | no |
| [wnba-elo-trend-lr-v2](#wnba-elo-trend-lr-v2) | WNBA | moneyline | elo_trend_logistic_regression | archived | **RETIRE** | no |
| [wnba-elo-trend-lr-v3](#wnba-elo-trend-lr-v3) | WNBA | moneyline | elo_trend_logistic_regression | archived | **KEEP_ROLLBACK** | no |
| [wnba-elo-trend-lr-v4](#wnba-elo-trend-lr-v4) | WNBA | moneyline | elo_trend_normal_approximation (moneyline head: logistic ... | shadow_qualified | **KEEP_PRIMARY** | yes |
| [wnba-research-baseline-ensemble (unmerged)](#wnba-research-baseline-ensemble-unmerged) | WNBA | moneyline (logistic), spread/margin (Ridge+Elo), total (Ridge), joint score distribution | regularized_logistic + ridge (margin, total) + EloModel e... | RESEARCH_ONLY (module constant) | **REPLACE_ONLY_IF_AUDIT_FAILS** | no |
| [wnba-spread-baseline-v1](#wnba-spread-baseline-v1) | WNBA | spread, total | baseline_heuristic | active_research | **KEEP_BASELINE** | yes |
| [wnba-total-score-ridge-v1](#wnba-total-score-ridge-v1) | WNBA | total (raw score regression) | ridge_regression | research_score_model_candidate | **KEEP_CHALLENGER** | partial |
| [nfl-drive-v2 (rebuild/models/nfl.py, orphaned)](#nfl-drive-v2-rebuild-models-nfl-py-orphaned) | NFL | moneyline, spread, total via drive-outcome Monte Carlo | expected-drives x drive-outcome-distribution simulation (... | dead code / unwired | **RETIRE** | partial |
| [nfl-elo-trend-lr-v1](#nfl-elo-trend-lr-v1) | NFL | moneyline | elo_trend_logistic_regression | archived | **RETIRE** | no |
| [nfl-elo-trend-lr-v2](#nfl-elo-trend-lr-v2) | NFL | moneyline | elo_trend_logistic_regression | archived | **RETIRE** | no |
| [nfl-elo-trend-lr-v3](#nfl-elo-trend-lr-v3) | NFL | moneyline | elo_trend_logistic_regression | archived | **RETIRE** | no |
| [nfl-elo-trend-lr-v4](#nfl-elo-trend-lr-v4) | NFL | moneyline | elo_trend_normal_approximation (moneyline head: logistic ... | shadow_qualified | **KEEP_PRIMARY** | yes |
| [nfl-spread-baseline-v1](#nfl-spread-baseline-v1) | NFL | spread, total | baseline_heuristic | active_research | **KEEP_BASELINE** | yes |
| [nfl-total-score-ridge-v1](#nfl-total-score-ridge-v1) | NFL | total (raw score regression) | ridge_regression | research_score_model_candidate | **KEEP_CHALLENGER** | partial |
| [soccer-dc-v2 (rebuild/models/soccer.py, orphaned)](#soccer-dc-v2-rebuild-models-soccer-py-orphaned) | SOCCER | 1X2 + totals + BTTS (draw-aware) | Dixon-Coles Poisson, attack/defense strengths learned via... | dead code / unwired | **REPAIR_SERVING** | no |
| [soccer-elo-trend-lr-v1](#soccer-elo-trend-lr-v1) | SOCCER | moneyline (binary) | elo_trend_logistic_regression | superseded | **RETIRE** | no |
| [soccer-elo-trend-lr-v2](#soccer-elo-trend-lr-v2) | SOCCER | moneyline (binary reference model) | elo_trend_logistic_regression | legacy reference | **KEEP_BASELINE** | yes |
| [soccer-poisson-dc-v1](#soccer-poisson-dc-v1) | SOCCER | total (O/U 2.5, the only configured/gated market); 1X2 and BTTS computed by the same code but not separately gated | poisson_dixon_coles (correlated Poisson score matrix, Dix... | shadow_qualified via qualification_ov... | **KEEP_PRIMARY** | yes |
| [tennis-elo-sr-v2 (rebuild/models/tennis.py, orphaned)](#tennis-elo-sr-v2-rebuild-models-tennis-py-orphaned) | TENNIS | match winner probability | surface Elo (K=32 default) with per-surface rating tracks... | see tennis-surface-elo-v1 | **KEEP_PRIMARY** | partial |
| [tennis-surface-elo-v1](#tennis-surface-elo-v1) | TENNIS | moneyline (WTA + ATP; ESPN has no ITF scoreboard) | surface_blended_elo (60% surface-specific / 40% overall E... | shadow_qualified via qualification_ov... | **KEEP_PRIMARY** | yes |
| [esports-roster-v1 (rebuild/models/esports.py, orphaned)](#esports-roster-v1-rebuild-models-esports-py-orphaned) | LOL/CS2/DOTA2/VALORANT/RAINBOW_SIX | single-game and series probability | per-title roster-based, player-level, map/patch/draft-awa... | dead code / unwired | **RETIRE** | no |
| [lol-neutral-series-elo-v1](#lol-neutral-series-elo-v1) | LOL | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [lol-neutral-series-elo-v2](#lol-neutral-series-elo-v2) | LOL | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [lol-tiered-elo-v3](#lol-tiered-elo-v3) | LOL | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [lol-tiered-elo-v4](#lol-tiered-elo-v4) | LOL | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [lol-tiered-elo-v5](#lol-tiered-elo-v5) | LOL | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | superseded | **KEEP_ROLLBACK** | no |
| [lol-tiered-elo-v5.previous](#lol-tiered-elo-v5-previous) | LOL | best-of match/series winner | neutral_series_elo (pre-write backup snapshot) | backup snapshot | **RETIRE** | no |
| [lol-tiered-elo-v6](#lol-tiered-elo-v6) | LOL | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | shadow_qualified | **KEEP_PRIMARY** | yes |
| [cs2-neutral-series-elo-v1](#cs2-neutral-series-elo-v1) | CS2 | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [cs2-neutral-series-elo-v2](#cs2-neutral-series-elo-v2) | CS2 | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [cs2-tiered-elo-v3](#cs2-tiered-elo-v3) | CS2 | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [cs2-tiered-elo-v4](#cs2-tiered-elo-v4) | CS2 | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [cs2-tiered-elo-v5](#cs2-tiered-elo-v5) | CS2 | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | superseded | **KEEP_ROLLBACK** | no |
| [cs2-tiered-elo-v5.previous](#cs2-tiered-elo-v5-previous) | CS2 | best-of match/series winner | neutral_series_elo (pre-write backup snapshot) | backup snapshot | **RETIRE** | no |
| [cs2-tiered-elo-v6](#cs2-tiered-elo-v6) | CS2 | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | shadow_qualified | **KEEP_PRIMARY** | yes |
| [dota2-neutral-series-elo-v1](#dota2-neutral-series-elo-v1) | DOTA2 | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [dota2-neutral-series-elo-v2](#dota2-neutral-series-elo-v2) | DOTA2 | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [dota2-tiered-elo-v3](#dota2-tiered-elo-v3) | DOTA2 | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [dota2-tiered-elo-v4](#dota2-tiered-elo-v4) | DOTA2 | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [dota2-tiered-elo-v5](#dota2-tiered-elo-v5) | DOTA2 | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | superseded | **KEEP_ROLLBACK** | no |
| [dota2-tiered-elo-v5.previous](#dota2-tiered-elo-v5-previous) | DOTA2 | best-of match/series winner | neutral_series_elo (pre-write backup snapshot) | backup snapshot | **RETIRE** | no |
| [dota2-tiered-elo-v6](#dota2-tiered-elo-v6) | DOTA2 | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | shadow_qualified | **KEEP_PRIMARY** | yes |
| [valorant-neutral-series-elo-v1](#valorant-neutral-series-elo-v1) | VALORANT | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [valorant-neutral-series-elo-v2](#valorant-neutral-series-elo-v2) | VALORANT | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [valorant-tiered-elo-v3](#valorant-tiered-elo-v3) | VALORANT | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [valorant-tiered-elo-v4](#valorant-tiered-elo-v4) | VALORANT | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [valorant-tiered-elo-v5](#valorant-tiered-elo-v5) | VALORANT | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | superseded | **KEEP_ROLLBACK** | no |
| [valorant-tiered-elo-v5.previous](#valorant-tiered-elo-v5-previous) | VALORANT | best-of match/series winner | neutral_series_elo (pre-write backup snapshot) | backup snapshot | **RETIRE** | no |
| [valorant-tiered-elo-v6](#valorant-tiered-elo-v6) | VALORANT | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | shadow_qualified | **KEEP_PRIMARY** | yes |
| [rainbow_six-neutral-series-elo-v1](#rainbow-six-neutral-series-elo-v1) | RAINBOW_SIX | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [rainbow_six-neutral-series-elo-v2](#rainbow-six-neutral-series-elo-v2) | RAINBOW_SIX | best-of match/series winner | neutral_series_elo (earliest, pre-tiered) | archived | **RETIRE** | no |
| [rainbow_six-tiered-elo-v3](#rainbow-six-tiered-elo-v3) | RAINBOW_SIX | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [rainbow_six-tiered-elo-v4](#rainbow-six-tiered-elo-v4) | RAINBOW_SIX | best-of match/series winner | neutral_series_elo (tiered) | superseded | **RETIRE** | no |
| [rainbow_six-tiered-elo-v5](#rainbow-six-tiered-elo-v5) | RAINBOW_SIX | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | superseded | **KEEP_ROLLBACK** | no |
| [rainbow_six-tiered-elo-v5.previous](#rainbow-six-tiered-elo-v5-previous) | RAINBOW_SIX | best-of match/series winner | neutral_series_elo (pre-write backup snapshot) | backup snapshot | **RETIRE** | no |
| [rainbow_six-tiered-elo-v6](#rainbow-six-tiered-elo-v6) | RAINBOW_SIX | best-of match/series winner | neutral_series_elo (schema esports-neutral-elo-v2, Platt-... | research | **KEEP_CHALLENGER** | yes |
| [kbo-npb-run-dist-v1 (rebuild/models/kbo_npb.py, orphaned)](#kbo-npb-run-dist-v1-rebuild-models-kbo-npb-py-orphaned) | KBO/NPB | moneyline with tie settlement, total | league-specific starter/lineup/bullpen run distribution w... | dead code / unwired | **RETIRE** | no |
| [kbo-tie-aware-elo-v1.previous (backup)](#kbo-tie-aware-elo-v1-previous-backup) | KBO | expected moneyline settlement, tie pays 0.50 | tie_aware_home_elo (pre-write backup snapshot) | backup snapshot | **RETIRE** | no |
| [kbo-tie-aware-elo-v2 (file named kbo-tie-aware-elo-v1.json)](#kbo-tie-aware-elo-v2-file-named-kbo-tie-aware-elo-v1-json) | KBO | expected moneyline settlement, tie pays 0.50 | tie_aware_home_elo (decisive-result Elo plus empirical ti... | research (research_outputs_zero_units... | **CALIBRATE** | yes |
| [npb-tie-aware-elo-v1.previous (backup)](#npb-tie-aware-elo-v1-previous-backup) | NPB | expected moneyline settlement, tie pays 0.50 | tie_aware_home_elo (pre-write backup snapshot) | backup snapshot | **RETIRE** | no |
| [npb-tie-aware-elo-v2 (file named npb-tie-aware-elo-v1.json)](#npb-tie-aware-elo-v2-file-named-npb-tie-aware-elo-v1-json) | NPB | expected moneyline settlement, tie pays 0.50 | tie_aware_home_elo (decisive-result Elo plus empirical ti... | research (research_outputs_zero_units... | **CALIBRATE** | yes |
| [market-residual-logistic-v1-identity-fallback](#market-residual-logistic-v1-identity-fallback) | ALL (cross-sport) | probability calibration layer combining model_p and market_p (the only place market prices may touch model output) | logistic_regression on [logit(model_p), logit(market_p)],... | active, currently in identity fallback | **KEEP_BASELINE** | yes |

## Model records

### MLB

#### measured-edge-margin-v1

- **Sport:** MLB
- **Market capability:** spread
- **Model family:** flat_probability_shrinkage_ols_calibration
- **Artifact path:** config/models/measured-edge-margin-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, prior generation
- **Feature names:** raw simulated cover probability (single input)
- **Training / validation / holdout period:** 162 games (v1, thru 2026-07-12) / 290 games (v2)
- **Model status:** superseded
- **Qualification status:** diagnostic only
- **Calibration method + metrics:** correlation 0.062, 80 picks 53.7% hit rate +2.09u
- **Save/load support:** yes (artifact exists)
- **PIT status:** same diagnostic-data caveat as v3
- **Train/serve parity notes:** n/a - not served
- **Known defects:** superseded by v3's rebuild against v0.3 elasticities
- **Notes:** Prior calibration generation, tied to mlb-analyst-poisson-trend-v0.2.
- **Recommendation:** **RETIRE**

#### measured-edge-margin-v2

- **Sport:** MLB
- **Market capability:** spread
- **Model family:** flat_probability_shrinkage_ols_calibration
- **Artifact path:** config/models/measured-edge-margin-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, prior generation
- **Feature names:** raw simulated cover probability (single input)
- **Training / validation / holdout period:** 162 games (v1, thru 2026-07-12) / 290 games (v2)
- **Model status:** superseded
- **Qualification status:** diagnostic only
- **Calibration method + metrics:** correlation 0.2057, 289 picks 59.52% hit rate +39.36u
- **Save/load support:** yes (artifact exists)
- **PIT status:** same diagnostic-data caveat as v3
- **Train/serve parity notes:** n/a - not served
- **Known defects:** superseded by v3's rebuild against v0.3 elasticities
- **Notes:** Prior calibration generation, tied to mlb-analyst-poisson-trend-v0.2.
- **Recommendation:** **RETIRE**

#### measured-edge-margin-v3

- **Sport:** MLB
- **Market capability:** spread
- **Model family:** flat_probability_shrinkage_ols_calibration
- **Artifact path:** config/models/measured-edge-margin-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - MLB.spread_research_artifact in config/model.yaml
- **Historically callable:** yes since 2026-08-04
- **Feature names:** raw simulated cover probability from mlb-analyst-poisson-trend-v0.3 (single input, scale+offset OLS calibration)
- **Training / validation / holdout period:** 290 games, data/historical/mlb_market_lines_reconstructed.jsonl (ESPN postgame reconstruction, explicitly labeled timestamp_valid=False / diagnostic only, NOT real historical Polymarket lines); real-market corroboration on 65 genuine captured Polymarket BBO games
- **Model status:** active_research
- **Qualification status:** diagnostic only - not run through the project's locked-holdout qualification gate (this is a calibration artifact, not a classifier)
- **Calibration method + metrics:** correlation with real outcomes 0.208 (up from v2's 0.0585); flat -110 diagnostic 285 picks, 60.0% hit rate, +41.45 units; real-market corroboration 63 picks, 63.49% hit rate, +13.36 units
- **Save/load support:** yes - scale/offset JSON, loaded and applied by models/mlb.py
- **PIT status:** uses reconstructed diagnostic data, explicitly NOT point-in-time-verified market lines (data source itself is postgame reconstruction)
- **Train/serve parity notes:** same OLS scale/offset applied identically at calibration-fit time and serve time
- **Known defects:** Diagnostic-only training data (not real historical Polymarket lines) -- real-market corroboration sample is small (65 games)
- **Notes:** Best-performing MLB spread signal in the lineage; margin genuinely improved from the v0.3 elasticity refit unlike totals.
- **Recommendation:** **KEEP_BASELINE**

#### measured-edge-totals-v1

- **Sport:** MLB
- **Market capability:** total
- **Model family:** flat_probability_shrinkage_ols_calibration
- **Artifact path:** config/models/measured-edge-totals-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, prior generation
- **Feature names:** raw simulated over-probability (single input)
- **Training / validation / holdout period:** 162 games (v1) / 284 games (v2)
- **Model status:** superseded
- **Qualification status:** diagnostic only
- **Calibration method + metrics:** correlation +0.166 (first positive totals signal seen), 91 picks 56.0% hit rate +6.36u
- **Save/load support:** yes (artifact exists)
- **PIT status:** same diagnostic-data caveat
- **Train/serve parity notes:** n/a - not served
- **Known defects:** v1 showed the first-ever positive totals correlation; v2's rebuild against v0.2's refit elasticities partially eroded it (0.166 -> 0.0585) before v3 eroded it further
- **Notes:** v1 is scientifically interesting (first positive totals signal) but operationally superseded.
- **Recommendation:** **RETIRE**

#### measured-edge-totals-v2

- **Sport:** MLB
- **Market capability:** total
- **Model family:** flat_probability_shrinkage_ols_calibration
- **Artifact path:** config/models/measured-edge-totals-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, prior generation
- **Feature names:** raw simulated over-probability (single input)
- **Training / validation / holdout period:** 162 games (v1) / 284 games (v2)
- **Model status:** superseded
- **Qualification status:** diagnostic only
- **Calibration method + metrics:** correlation 0.0585, 123 picks 55.28% hit rate +6.82u
- **Save/load support:** yes (artifact exists)
- **PIT status:** same diagnostic-data caveat
- **Train/serve parity notes:** n/a - not served
- **Known defects:** v1 showed the first-ever positive totals correlation; v2's rebuild against v0.2's refit elasticities partially eroded it (0.166 -> 0.0585) before v3 eroded it further
- **Notes:** v1 is scientifically interesting (first positive totals signal) but operationally superseded.
- **Recommendation:** **RETIRE**

#### measured-edge-totals-v3

- **Sport:** MLB
- **Market capability:** total
- **Model family:** flat_probability_shrinkage_ols_calibration
- **Artifact path:** config/models/measured-edge-totals-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - MLB.total_research_artifact in config/model.yaml
- **Historically callable:** yes since 2026-08-04
- **Feature names:** raw simulated over-probability from mlb-analyst-poisson-trend-v0.3
- **Training / validation / holdout period:** 284 games, data/historical/mlb_market_lines_reconstructed.jsonl (diagnostic, not real historical Polymarket lines); real-market corroboration on 65/8 genuine Polymarket BBO games
- **Model status:** active_research, flagged problem_cohort
- **Qualification status:** diagnostic only, not locked-holdout qualified
- **Calibration method + metrics:** correlation with real outcomes REGRESSED to 0.0414 (from v2's 0.0585); flat -110 diagnostic 68 picks, 52.94% hit rate, +0.73 units; real-market corroboration only 8 picks, 37.5% hit rate, -2.27 units (negative)
- **Save/load support:** yes - scale/offset JSON
- **PIT status:** same diagnostic-data caveat as margin-v3
- **Train/serve parity notes:** same OLS application pattern as margin-v3
- **Known defects:** config/model.yaml's problem_cohorts.totals: 'absolute_run_environment_miss' -- correlation and diagnostic units both regressed from the v0.3 refit despite margin improving from the identical underlying formula change. Real-market corroboration sample went NEGATIVE (-2.27u on 8 picks).
- **Notes:** This is the artifact directly evidencing MLB's documented totals problem; config explicitly plans a 'branched_absolute_run_intensity_head' experiment next.
- **Recommendation:** **REBUILD_DISTRIBUTION_HEAD**

#### mlb-analyst-poisson-trend-v0.2

- **Sport:** MLB
- **Market capability:** spread, total
- **Model family:** poisson_run_simulation_with_hand_tuned_factors
- **Artifact path:** config/models/mlb-analyst-poisson-trend-v0.2.yaml
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded by v0.3 in config/model.yaml pointers
- **Historically callable:** yes, was the live engine before 2026-08-04
- **Feature names:** same shape as v0.3 (offense/park/starter/bullpen/weather elasticities) but hand-bumped round-number values, not GLM-fit
- **Training / validation / holdout period:** none - hand-tuned, not fit against real data
- **Model status:** superseded
- **Qualification status:** never formally qualified (predates elasticity refit methodology)
- **Calibration method + metrics:** see measured-edge-margin-v1/v2, measured-edge-totals-v1/v2 (its downstream calibration heads)
- **Save/load support:** yes (artifact exists, still loadable, but not pointed to by any config key)
- **PIT status:** same feature set as v0.3
- **Train/serve parity notes:** n/a - not served
- **Known defects:** Replaced specifically because its elasticities were hand-bumped round numbers rather than real fitted values (see v0.3's _refit_note)
- **Notes:** Direct predecessor to v0.3; kept for reproducibility of the earlier measured-edge-v1/v2 calibration heads.
- **Recommendation:** **RETIRE**

#### mlb-analyst-poisson-trend-v0.3

- **Sport:** MLB
- **Market capability:** spread, total
- **Model family:** poisson_run_simulation_with_ols_calibration
- **Artifact path:** config/models/mlb-analyst-poisson-trend-v0.3.yaml (code: src/model_prediction/models/mlb.py estimate_runs/simulate_game)
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - MLB spread_research_artifact/total_research_artifact pipeline (_forecast_mlb/_forecast_mlb_totals_flat in cli.py) uses this engine live
- **Historically callable:** yes, since 2026-08-04 (replaced v0.2)
- **Feature names:** offense_elasticity, park_elasticity, starter_weakness_elasticity, bullpen_elasticity, weather_elasticity, recent_half_life_games trend features
- **Training / validation / holdout period:** Poisson GLM elasticity refit against 1136 real completed games, 4 chronological expanding-window folds (fold correlations 0.1046-0.1623)
- **Model status:** active_research (problem_cohorts.totals) / active production for margin via measured-edge-margin-v3
- **Qualification status:** Not itself walk-forward qualified as a standalone artifact (a formula/engine, not a binary classifier); downstream calibration heads (measured-edge-margin/totals-v3) are OLS-fit against real games and separately diagnosed.
- **Calibration method + metrics:** see measured-edge-margin-v3 / measured-edge-totals-v3 records for the fitted downstream heads
- **Save/load support:** yes - YAML parameter file loaded by FormulaSpec; simulation is deterministic given seed
- **PIT status:** uses point-in-time bullpen/park/weather features per CLAUDE.md's PIT invariant; does not use probable_starter_era_gap (documented as not-yet-safe)
- **Train/serve parity notes:** same estimate_runs()/simulate_game() code path used for both refit calibration and live forecasting
- **Known defects:** config/model.yaml's problem_cohorts.totals documents an unresolved 'absolute_run_environment_miss': the v0.3 refit improved margin/spread (correlation 0.2057->0.208, hit rate 59.5%->60.0%) but did NOT resolve totals (correlation regressed 0.0585->0.0414, diagnostic hit rate fell 55.3%->52.9%). Elasticities capture teams' RELATIVE run differentiation (helps margin) but not the ABSOLUTE run-environment signal totals needs.
- **Notes:** Config's own experiments_in_order lists 'totals_specific_market_residual' and 'branched_absolute_run_intensity_head' as the next planned fixes -- matches the REBUILD_DISTRIBUTION_HEAD recommendation for the totals side specifically. Margin/spread side is healthier (see measured-edge-margin-v3).
- **Recommendation:** **REBUILD_DISTRIBUTION_HEAD**

#### mlb-elo-trend-lr-v1

- **Sport:** MLB
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/mlb-elo-trend-lr-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest production lineage
- **Feature names:** see market_models.moneyline.feature_names in artifact (schema_version 1, qualification dict)
- **Training / validation / holdout period:** not in current inventory scope (pre-standardized walk-forward pipeline)
- **Model status:** archived
- **Qualification status:** qualified=true recorded on v4 only; earlier versions carry a qualification dict without a top-level flag
- **Calibration method + metrics:** not surfaced for this audit
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived, not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Earliest MLB Elo+trend lineage, kept only for historical reproducibility in config/models/archive/.
- **Recommendation:** **RETIRE**

#### mlb-elo-trend-lr-v2

- **Sport:** MLB
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/mlb-elo-trend-lr-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest production lineage
- **Feature names:** see market_models.moneyline.feature_names in artifact (schema_version 1, qualification dict)
- **Training / validation / holdout period:** not in current inventory scope (pre-standardized walk-forward pipeline)
- **Model status:** archived
- **Qualification status:** qualified=true recorded on v4 only; earlier versions carry a qualification dict without a top-level flag
- **Calibration method + metrics:** not surfaced for this audit
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived, not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Earliest MLB Elo+trend lineage, kept only for historical reproducibility in config/models/archive/.
- **Recommendation:** **RETIRE**

#### mlb-elo-trend-lr-v3

- **Sport:** MLB
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/mlb-elo-trend-lr-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest production lineage
- **Feature names:** see market_models.moneyline.feature_names in artifact (schema_version 1, qualification dict)
- **Training / validation / holdout period:** not in current inventory scope (pre-standardized walk-forward pipeline)
- **Model status:** archived
- **Qualification status:** qualified=true recorded on v4 only; earlier versions carry a qualification dict without a top-level flag
- **Calibration method + metrics:** not surfaced for this audit
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived, not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Earliest MLB Elo+trend lineage, kept only for historical reproducibility in config/models/archive/.
- **Recommendation:** **RETIRE**

#### mlb-elo-trend-lr-v4

- **Sport:** MLB
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/mlb-elo-trend-lr-v4.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest production lineage
- **Feature names:** see market_models.moneyline.feature_names in artifact (schema_version 1, qualification dict)
- **Training / validation / holdout period:** not in current inventory scope (pre-standardized walk-forward pipeline)
- **Model status:** archived
- **Qualification status:** qualified=true recorded on v4 only; earlier versions carry a qualification dict without a top-level flag
- **Calibration method + metrics:** not surfaced for this audit
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived, not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Earliest MLB Elo+trend lineage, kept only for historical reproducibility in config/models/archive/.
- **Recommendation:** **RETIRE**

#### mlb-elo-trend-lr-v5

- **Sport:** MLB
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/mlb-elo-trend-lr-v5.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** retired
- **Qualification status:** superseded, not cited in config/model.yaml
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists) but unreferenced
- **PIT status:** unknown, predates the walk-forward pipeline standardization
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none documented beyond being superseded
- **Notes:** Earlier lineage entry between archive/v4 and v6.
- **Recommendation:** **RETIRE**

#### mlb-elo-trend-lr-v6

- **Sport:** MLB
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/mlb-elo-trend-lr-v6.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - not referenced by any config/model.yaml key
- **Historically callable:** yes, briefly, before v7 replaced it
- **Feature names:** see market_models.moneyline.feature_names in artifact (7-key qualification dict, ad-hoc variant)
- **Training / validation / holdout period:** ad-hoc 90-day/242-call operator experiment (per v7's training.note)
- **Model status:** retired
- **Qualification status:** never cleared the bar; v7's own training note calls it 'self-documented contaminated probable_starter_era_gap, never cleared this project's own bar'
- **Calibration method + metrics:** not surfaced in inventory scope; superseded
- **Save/load support:** yes (artifact exists) but unreferenced
- **PIT status:** documented as using a leak-prone probable_starter_era_gap feature (ESPN live probables, not point-in-time safe)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** Self-documented feature contamination (probable_starter_era_gap look-ahead risk) per v7's own training.note
- **Notes:** Ad-hoc experimental version between v5 and v7; superseded and explicitly disowned by v7's own commit note.
- **Recommendation:** **RETIRE**

#### mlb-elo-trend-lr-v7

- **Sport:** MLB
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/mlb-elo-trend-lr-v7.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - legacy_research_rollback in config/model.yaml, loadable by learned_forward on operator request
- **Historically callable:** yes, was production before v8 (2026-08-04)
- **Feature names:** elo_probability, trend_gap, park_factor, weather_factor, pitcher_era_gap, bullpen_weakness_gap
- **Training / validation / holdout period:** coefficient_fit 2024-04-06..2025-07-22 (3814 obs); threshold_selection 2025-07-23..2026-04-10 (1082 obs); locked_holdout 2026-04-11..2026-07-29 (1391 obs, 118 calls)
- **Model status:** rollback / legacy_research_rollback
- **Qualification status:** qualified=false. hit_rate 58.47% below the 60% bar; non-positive qualifying month 2026-04 (-2.27u). failures explicitly listed in the artifact.
- **Calibration method + metrics:** brier_score=0.246456, calibration_slope=1.645, ECE=0.0741, sample_size=118
- **Save/load support:** yes - same JSON coefficient-artifact pattern as v8
- **PIT status:** walk_forward_features=true; pitcher_era_gap is team-level rolling runs-allowed, not per-starter (that's what v8 replaced it with)
- **Train/serve parity notes:** same shared feature dispatch as v8
- **Known defects:** Never cleared this project's own 60%/50-call automatic promotion bar; kept only as an explicit rollback target
- **Notes:** Explicit rollback target for v8; both use the identical Elo+trend LR family, differing only in the pitcher-quality feature.
- **Recommendation:** **KEEP_ROLLBACK**

#### mlb-elo-trend-lr-v8

- **Sport:** MLB
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/mlb-elo-trend-lr-v8.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version in config/model.yaml, served live via learned_forward.build_learned_moneyline_slate
- **Historically callable:** yes since 2026-08-04 promotion
- **Feature names:** elo_probability, trend_gap, park_factor, weather_factor, starter_era_gap, bullpen_weakness_gap
- **Training / validation / holdout period:** coefficient_fit 2024-04-06..2025-07-22 (3814 obs); threshold_selection 2025-07-23..2026-04-10 (1082 obs); locked_holdout 2026-04-11..2026-07-29 (1391 obs, 148 calls)
- **Model status:** shadow_qualified (config), qualification.qualified=false in the artifact itself
- **Qualification status:** qualification_override=true (operator-directed 2026-08-04). Locked-holdout clears the 60%/50-call bar (60.81% hit rate, 148 calls, +23.8u at -110) but validation Brier REGRESSED vs v7 (0.24702 vs 0.24655) -- the exact 'peek at holdout' pattern docs/AGENTS.md's promotion rule exists to catch. Promoted anyway by explicit operator directive, not automatic pass.
- **Calibration method + metrics:** locked-holdout brier_score=0.246354; validation_brier_score=0.24702 (worse than v7's 0.24655, delta +0.00047)
- **Save/load support:** yes - JSON artifact stores fitted LR coefficients/intercept, loaded and applied at serve time (no retraining needed)
- **PIT status:** walk_forward_features=true; training_data_note flags starter_era_gap depends on a daily capture step (cli.py _capture_mlb_starter_snapshots, added same day as this artifact) -- verify that capture is still running before trusting this feature live
- **Train/serve parity notes:** feature computation shared between training (validation.py walk-forward) and serving (learned_forward.py _compute_features) via the same feature-name dispatch
- **Known defects:** Promoted despite a documented validation-Brier regression vs. its own predecessor (operator override, not a clean pass); starter_era_gap's live daily-capture dependency is a single point of failure not covered by this artifact's own tests
- **Notes:** Current live MLB moneyline production model. Replaces v7's pitcher_era_gap (team-level) with starter_era_gap (real per-starter rolling ERA from mlb_statsapi boxscore history).
- **Recommendation:** **KEEP_PRIMARY**

#### mlb-spread-baseline-v1

- **Sport:** MLB
- **Market capability:** spread, total
- **Model family:** baseline_heuristic (elo_margin / league_avg_total)
- **Artifact path:** config/models/mlb-spread-baseline-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - config/model.yaml's own P0-5 comment states this artifact is orphaned: 'no code in src/model_prediction ever reads for MLB'
- **Historically callable:** no evidence found that it was ever read
- **Feature names:** unfitted heuristic (elo_margin, league_avg_total)
- **Training / validation / holdout period:** none - unfitted
- **Model status:** orphaned
- **Qualification status:** qualification dict present in artifact but never consumed
- **Calibration method + metrics:** none - heuristic, not fit
- **Save/load support:** yes (artifact exists, unreachable)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a - dead reference
- **Known defects:** P0-5 fix (2026-08-03) redirected MLB's real spread/total config keys to measured-edge-margin/totals-*; this file is now an orphaned artifact with no live reader
- **Notes:** Config's own comment documents this as a stale, mismatched artifact that was fixed by repointing config keys elsewhere -- kept on disk but dead.
- **Recommendation:** **RETIRE**

#### mlb-total-score-ridge-v1

- **Sport:** MLB
- **Market capability:** total (raw score regression)
- **Model family:** ridge_regression
- **Artifact path:** config/models/mlb-total-score-ridge-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** unclear - status research_score_model_candidate, not referenced by config/model.yaml's active pointers
- **Historically callable:** unknown
- **Feature names:** 9 features (coefficients/feature_names lists, len 9)
- **Training / validation / holdout period:** see artifact's training/locked_holdout dicts (9 keys each)
- **Model status:** research_score_model_candidate
- **Qualification status:** market_qualification dict present (3 keys) but not wired to the promotion gate
- **Calibration method + metrics:** validation_residual_sd=4.44 runs
- **Save/load support:** yes - coefficients/intercept JSON
- **PIT status:** not verified in this pass
- **Train/serve parity notes:** not verified - no calling code found in config/model.yaml pointers
- **Known defects:** Candidate status only; not integrated into the live spread/total pipeline (which uses the Trend Engine simulation instead)
- **Notes:** A simpler linear alternative to the Trend Engine simulation; same NBA/NFL/WNBA pattern exists per-sport.
- **Recommendation:** **KEEP_CHALLENGER**

#### mlb-two-head-real-features-v1

- **Sport:** MLB
- **Market capability:** moneyline, run line (spread), total, expected score
- **Model family:** independent_poisson two-head (sklearn: HistGradientBoostingRegressor intensity head [starter velocity/CSW%, bullpen workload, park factor, temperature], ElasticNet differential head [starter K%, bullpen workload differential])
- **Artifact path:** config/models/challengers/mlb-two-head-real-features-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - MLBTwoHeadModel, real-Statcast-features version, used for model_disagreement measurement only (never spread/total independently, per CLAUDE.md)
- **Historically callable:** yes, current as of the 2026-08-07 model card
- **Feature names:** 8 intensity features + 8 differential features (real_statcast_v1 feature set)
- **Training / validation / holdout period:** 126/150 real completed games matched to Statcast; persisted split (outputs/rebuild/mlb_split_manifest.json): train 84 games (2026-07-26..08-01), calibration 21 games (08-01..08-02), final test 21 games (08-02..08-04, consumed once)
- **Model status:** RESEARCH_ONLY
- **Qualification status:** Final held-out test (n=21): log_loss=0.8652, Brier=0.3211, accuracy=0.381 (WORSE than the 0.500 coin-flip baseline). Quality-filtered subset (both starters have real prior history, n=18): Brier=0.2832, accuracy=0.444. Explicitly disclosed cold-start composition mismatch: train mean starter-availability=0.167 vs test mean=0.929.
- **Calibration method + metrics:** see mlb-two_head-calibrator-v1.json (temperature scaling, base_model_hash-bound)
- **Save/load support:** yes - JSON artifact with fold_metrics/final_metrics/quality_filtered_metrics/cold_start_composition
- **PIT status:** real, chronological train/calibration/test split; calibrator fit and evaluated on genuinely separate blocks (a real methodology fix vs. an earlier version that leaked)
- **Train/serve parity notes:** pipeline runs end-to-end against real data: collection -> real features -> real chronological training -> real calibration-independent held-out test -> real market matching -> winner-first decision -> SQLite shadow persistence
- **Known defects:** Sample size (n=18-21 on final test) is too small to distinguish real skill from noise -- both the pre-fix (n=25, accuracy 0.320) and post-fix (n=21, accuracy 0.381) numbers sit inside plausible noise around a coin flip. Economically REJECTED for a structural reason: real_market_candidates() sets depth_available=False on every real candidate because the Polymarket source exposes no order-book depth, so decision.py's depth gate fails closed unconditionally -- every real market currently produces NO_BET regardless of price/edge (live-verified: a real 2-game slate, 32 candidate markets, 0 BET).
- **Notes:** Full model card at docs/model_audit/prior_evidence/model_cards/mlb-two-head-v1.md (2026-08-07, predates current main by several commits but verified structurally consistent with the live code in this pass). Verdict per that card: 'Not ready for promotion; correctly held at RESEARCH_ONLY/REJECTED' -- genuinely unresolved on sample size, structurally blocked economically on missing order-book depth.
- **Recommendation:** **KEEP_CHALLENGER**

#### mlb-two-head-v1 (early sklearn baseline, superseded)

- **Sport:** MLB
- **Market capability:** moneyline
- **Model family:** independent_poisson two-head (sklearn: HistGradientBoostingRegressor intensity head, ElasticNet differential head)
- **Artifact path:** config/models/challengers/mlb-two-head-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes as MLBTwoHeadModel (disagreement-input role only, per CLAUDE.md's rule), but this specific artifact is the earliest, rolling-scoreboard-only version
- **Historically callable:** yes, this was the 2026-08-05 baseline consumed by an earlier model card
- **Feature names:** 6 intensity features + 4 differential features (early feature set, pre real-Statcast integration)
- **Training / validation / holdout period:** 188 total completed games, 150 train / 38 test
- **Model status:** superseded by mlb-two-head-real-features-v1
- **Qualification status:** RESEARCH_ONLY. accuracy=0.500 (exact coin flip), log_loss=0.7138, Brier=0.2601, ECE=0.2073 -- no real Statcast/weather/lineup/bullpen signal in this version, per the current model card's own note that this architecture 'no longer exists in the live pipeline.'
- **Calibration method + metrics:** see mlb-two_head-calibrator-v1.json (temperature scaling)
- **Save/load support:** yes (artifact exists)
- **PIT status:** rolling-scoreboard-only, pre-Statcast
- **Train/serve parity notes:** n/a - superseded
- **Known defects:** Exact coin-flip accuracy (0.500) -- the current model card (docs/model_audit/prior_evidence/model_cards/mlb-two-head-v1.md) explicitly states this architecture is retired
- **Notes:** Kept on disk as a historical baseline for comparison against mlb-two-head-real-features-v1's improvement.
- **Recommendation:** **RETIRE**

#### mlb-two_head-calibrator-v1

- **Sport:** MLB
- **Market capability:** calibration support artifact (moneyline)
- **Model family:** temperature scaling
- **Artifact path:** config/models/challengers/mlb-two_head-calibrator-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - loaded by the rebuild-shadow MLB pipeline for the corresponding model family
- **Historically callable:** yes
- **Feature names:** single input: raw model logit/probability
- **Training / validation / holdout period:** n_training_oof=203 (out-of-fold), training_range in artifact
- **Model status:** support artifact for a KEEP_CHALLENGER-rated model
- **Qualification status:** not independently qualified -- a calibration layer, not a standalone classifier
- **Calibration method + metrics:** temperature parameter fit via OOF predictions, dataset_hash/oof_split_manifest_hash-pinned for reproducibility
- **Save/load support:** yes - JSON with calibrator_hash
- **PIT status:** n/a - post-hoc calibration on OOF predictions
- **Train/serve parity notes:** base_model_hash binds this calibrator to one specific fitted model version
- **Known defects:** none newly found
- **Notes:** temperature calibrator for MLBTwoHeadModel (sklearn two-head baseline), base_model_hash-bound
- **Recommendation:** **KEEP_CHALLENGER**

#### mlb-v0.2-platt-2026-07-07-to-10-v1

- **Sport:** MLB
- **Market capability:** spread/total calibration (superseded)
- **Model family:** platt_scaling
- **Artifact path:** config/models/mlb-v0.2-platt-2026-07-07-to-10-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** yes, brief early calibration attempt
- **Feature names:** logit(base_model_probability)
- **Training / validation / holdout period:** 2026-07-07..2026-07-10, sample_size=115
- **Model status:** superseded
- **Qualification status:** none
- **Calibration method + metrics:** intercept=-0.5, slope=1.2
- **Save/load support:** yes (artifact exists)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a - not served
- **Known defects:** Superseded by the measured-edge-* flat_probability_shrinkage lineage
- **Notes:** Earliest MLB calibration attempt, tied to mlb-analyst-poisson-trend-v0.2.
- **Recommendation:** **RETIRE**

#### mlb-xgb_direct-calibrator-v1

- **Sport:** MLB
- **Market capability:** calibration support artifact (moneyline)
- **Model family:** temperature scaling
- **Artifact path:** config/models/challengers/mlb-xgb_direct-calibrator-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - loaded by the rebuild-shadow MLB pipeline for the corresponding model family
- **Historically callable:** yes
- **Feature names:** single input: raw model logit/probability
- **Training / validation / holdout period:** n_training_oof=203 (out-of-fold), training_range in artifact
- **Model status:** support artifact for a KEEP_CHALLENGER-rated model
- **Qualification status:** not independently qualified -- a calibration layer, not a standalone classifier
- **Calibration method + metrics:** temperature parameter fit via OOF predictions, dataset_hash/oof_split_manifest_hash-pinned for reproducibility
- **Save/load support:** yes - JSON with calibrator_hash
- **PIT status:** n/a - post-hoc calibration on OOF predictions
- **Train/serve parity notes:** base_model_hash binds this calibrator to one specific fitted model version
- **Known defects:** none newly found
- **Notes:** temperature calibrator for XGBoostChallenger (xgb_direct, independent binary classifier used for disagreement only)
- **Recommendation:** **KEEP_CHALLENGER**

#### mlb-xgb_two_head-calibrator-v1

- **Sport:** MLB
- **Market capability:** calibration support artifact (moneyline)
- **Model family:** temperature scaling
- **Artifact path:** config/models/challengers/mlb-xgb_two_head-calibrator-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - loaded by the rebuild-shadow MLB pipeline for the corresponding model family
- **Historically callable:** yes
- **Feature names:** single input: raw model logit/probability
- **Training / validation / holdout period:** n_training_oof=203 (out-of-fold), training_range in artifact
- **Model status:** support artifact for a KEEP_CHALLENGER-rated model
- **Qualification status:** not independently qualified -- a calibration layer, not a standalone classifier
- **Calibration method + metrics:** temperature parameter fit via OOF predictions, dataset_hash/oof_split_manifest_hash-pinned for reproducibility
- **Save/load support:** yes - JSON with calibrator_hash
- **PIT status:** n/a - post-hoc calibration on OOF predictions
- **Train/serve parity notes:** base_model_hash binds this calibrator to one specific fitted model version
- **Known defects:** none newly found
- **Notes:** temperature calibrator for XGBoostTwoHeadModel under its non-frozen default Poisson distribution (as opposed to the frozen negative_binomial combination)
- **Recommendation:** **KEEP_CHALLENGER**

#### mlb-xgb_two_head_negative_binomial-calibrator-v1

- **Sport:** MLB
- **Market capability:** calibration support artifact (moneyline) -- the FROZEN primary challenger's calibrator
- **Model family:** temperature scaling
- **Artifact path:** config/models/challengers/mlb-xgb_two_head_negative_binomial-calibrator-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - loaded by mlb_v2_artifact.py's sealed-bundle path (see mlb_moneyline_v2_frozen_v1 record)
- **Historically callable:** yes
- **Feature names:** single input: raw model logit/probability from the frozen XGBoost two-head + negative-binomial combination
- **Training / validation / holdout period:** n_training_oof=223, training_range in artifact
- **Model status:** support artifact for the frozen mlb_moneyline_v2 candidate
- **Qualification status:** not independently qualified -- see mlb_moneyline_v2_frozen_v1 for the candidate's own gating state
- **Calibration method + metrics:** temperature parameter fit via 223 OOF predictions/labels (oof_probs/oof_labels persisted in the artifact itself)
- **Save/load support:** yes - JSON with calibrator_hash, content-hash-bound to the exact booster per the mlb-v2-prospective-ops fix
- **PIT status:** n/a - post-hoc calibration on OOF predictions
- **Train/serve parity notes:** the exact artifact the booster-byte content-hash binding fix (mlb-v2-prospective-ops) protects
- **Known defects:** none newly found
- **Notes:** This is the single most important support artifact in the MLB challenger stack -- it backs the frozen mlb_moneyline_v2 candidate explicitly named in this audit's scope.
- **Recommendation:** **KEEP_CHALLENGER**

#### mlb_moneyline_v2_frozen_v1 (XGBoost two-head + negative-binomial, frozen candidate)

- **Sport:** MLB
- **Market capability:** moneyline (primary); spread/total explicitly rejected as independent challenger outputs per CLAUDE.md's rule that a disconnected classifier may contribute disagreement evidence but never generate spread/total on its own
- **Model family:** XGBoostTwoHeadModel (intensity head + differential head) + negative_binomial joint score distribution + temperature calibration
- **Artifact path:** src/model_prediction/rebuild/mlb_v2_artifact.py (sealed bundle loader, content-hash-bound); calibrator config/models/challengers/mlb-xgb_two_head_negative_binomial-calibrator-v1.json; readiness registry outputs/rebuild/test_consumption_registry.json
- **Source branch:** origin/rebuild/clean-slate-v1 (144 unique commits), sealing/binding fixes merged to main via PR #8 (origin/fix/mlb-v2-prospective-ops, commits 963d24e + 308601f, squash-merged as 77e612d, plus additional main-only mypy cleanup)
- **Currently callable from main:** yes, code-complete via `rebuild-shadow --sport mlb` (shadow-only, writes to data/rebuild/shadow.db SQLite ledger, never the real Main ledger) -- but fails closed on frozen_artifact_anchor.status=='sealing_required' until an operator performs the manual seal step
- **Historically callable:** same status as current main (this is the frozen candidate's live state, not a historical-only fact)
- **Feature names:** MLB_INTENSITY_FEATURES + MLB_DIFFERENTIAL_FEATURES (src/model_prediction/rebuild/mlb_features.py) for the two-head model; XGB_DIRECT_FEATURES = union of both for the independent xgb_direct disagreement classifier
- **Training / validation / holdout period:** candidate selected via outputs/rebuild/mlb_head_distribution_cartesian.json (2 head families x 3 distributions x 4 calibration methods, cross-fit n=168); training window through 2026-08-08; prospective evaluation window open-ended from 2026-08-08T02:20Z, 0/100 real completed games accumulated toward the predeclared evaluation floor as of the most recent check
- **Model status:** frozen, prepared, NOT YET SEALED
- **Qualification status:** Not qualified -- blocked pending (1) a manual operator seal action (frozen_artifact_anchor.status must become 'sealed') and (2) 100 real completed games accumulating toward the predeclared prospective-evaluation floor. Cross-fit calibration numbers (n=168, log_loss=0.6927, Brier=0.2498) barely beat the constant-0.5 baseline (0.6931) -- explicitly disclosed as not yet real evidence of edge.
- **Calibration method + metrics:** temperature scaling; cross-fit log_loss=0.6927, Brier=0.2498, ECE=0.0326 (n=168)
- **Save/load support:** yes, and unusually strict: exact content-hash-bound bundle, fails closed on a dirty source tree or an unsealed registry anchor (fixed by the mlb-v2-prospective-ops branch: calibrator is now bound to the actual fitted booster's bytes, not just schema metadata, closing a real risk of silently pairing a calibrator with the wrong fitted model)
- **PIT status:** verified_source_tree_hash plus PIT-safe feature builders (mlb_features.py, shared with the live-serving path)
- **Train/serve parity notes:** explicit design goal of the mlb-v2-prospective-ops fixes: booster-byte content-hash binding guarantees the calibrator and the model it calibrates are the exact same fitted object, closing a real train/serve mismatch risk
- **Known defects:** No open code defect found -- the sealing/binding/readiness-gating issues the mlb-v2-prospective-ops branch fixed are all present and working on main. The candidate is simply not yet evaluated: 0/100 real games, un-sealed by design (a deliberate manual gate, not an oversight).
- **Notes:** This is the model named in this audit's background as 'the clean-slate MLB XGBoost two-head/negative-binomial model as a challenger (not replacement)'. Shadow-only, SQLite-only persistence -- structurally incapable of touching the real Main ledger while in this state. Also produces real conservative-probability bounds via BootstrapMLBEnsemble (20 resample-fit replicates) for model_disagreement measurement, alongside two more independent families used only for disagreement (never spread/total): MLBTwoHeadModel (sklearn HistGradientBoosting/ElasticNet baseline) and XGBoostChallenger (a direct binary XGBoost classifier, xgb_direct).
- **Recommendation:** **KEEP_CHALLENGER**

#### mlb_trend_score_v2 (rejected backtest challenger)

- **Sport:** MLB
- **Market capability:** score/trend challenger (unspecified market in the card)
- **Model family:** unspecified in docs/mlb_trend_score_v2/MODEL_CARD.md
- **Artifact path:** docs/mlb_trend_score_v2/MODEL_CARD.md (model card only, no config/models/ artifact)
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** no evidence of live wiring
- **Feature names:** not enumerated in this pass
- **Training / validation / holdout period:** not enumerated in this pass
- **Model status:** Rejected backtest challenger. Zero units. (per the card's own status line)
- **Qualification status:** rejected
- **Calibration method + metrics:** not enumerated in this pass
- **Save/load support:** unknown
- **PIT status:** unknown
- **Train/serve parity notes:** n/a - rejected
- **Known defects:** A naming collision worth flagging: this is a THIRD, unrelated 'v2' in the MLB model space, distinct from both mlb-elo-trend-lr-v2 (archived production lineage) and mlb_moneyline_v2_frozen_v1 (the clean-slate XGBoost/NB challenger). Confirmed identical on current main and origin/fix/mlb-v2-prospective-ops by the MLB v2 ops audit agent.
- **Notes:** Surfaced only because of the MLB-v2-ops branch audit; already rejected, kept here to document the naming collision for future readers of config/model.yaml or docs referencing 'v2'.
- **Recommendation:** **RETIRE**

### MLB/NBA/WNBA/NFL (cross-sport)

#### production-feature-ablation-2026-07-22 (diagnostic artifact, not a deployable model)

- **Sport:** MLB/NBA/WNBA/NFL (cross-sport)
- **Market capability:** n/a - leave-one-feature-out ablation study over configured production_artifact entries
- **Model family:** n/a - diagnostic evidence artifact
- **Artifact path:** config/models/production-feature-ablation-2026-07-22.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - a dated snapshot, not loaded by any live code path
- **Historically callable:** was evidence for a 2026-07-22 promotion decision
- **Feature names:** n/a
- **Training / validation / holdout period:** as_of_date=2026-07-22, holdout_status=reused_locked_holdouts_development_evidence (explicitly NOT a fresh locked holdout)
- **Model status:** stale diagnostic snapshot
- **Qualification status:** promotion_eligible=False, economic_claims_allowed=False, market_prices_used=False (self-declared non-authoritative)
- **Calibration method + metrics:** n/a - contains a models dict (9 entries) and predeclared_gates (7 entries), not its own fit
- **Save/load support:** n/a
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** Predates every current production artifact's own embedded qualification block (v4-v8 generation); functionally superseded by the qualification data now embedded directly in each artifact
- **Notes:** Not itself a model -- included per the task's instruction to inventory every file in config/models/*.json. Its own schema_version is production-feature-ablation-v1, explicitly a study artifact.
- **Recommendation:** **RETIRE**

### NBA

#### nba-elo-trend-lr-v1

- **Sport:** NBA
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/nba-elo-trend-lr-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived, but v1 and v3 are named in config/model.yaml's protected_versions list (see known_defects)
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** archived
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived
- **Known defects:** Referenced by config's protected_versions list (intended to be preserved, not deleted) -- confirm this protection is honored by any future archive cleanup tooling
- **Notes:** v1 and v3 are explicitly protected in config/model.yaml; v2 is not listed and has no other reference.
- **Recommendation:** **KEEP_ROLLBACK**

#### nba-elo-trend-lr-v2

- **Sport:** NBA
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/nba-elo-trend-lr-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived, but v1 and v3 are named in config/model.yaml's protected_versions list (see known_defects)
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** archived
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived
- **Known defects:** Referenced by config's protected_versions list (intended to be preserved, not deleted) -- confirm this protection is honored by any future archive cleanup tooling
- **Notes:** v1 and v3 are explicitly protected in config/model.yaml; v2 is not listed and has no other reference.
- **Recommendation:** **RETIRE**

#### nba-elo-trend-lr-v3

- **Sport:** NBA
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/nba-elo-trend-lr-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived, but v1 and v3 are named in config/model.yaml's protected_versions list (see known_defects)
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** archived
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived
- **Known defects:** Referenced by config's protected_versions list (intended to be preserved, not deleted) -- confirm this protection is honored by any future archive cleanup tooling
- **Notes:** v1 and v3 are explicitly protected in config/model.yaml; v2 is not listed and has no other reference.
- **Recommendation:** **KEEP_ROLLBACK**

#### nba-elo-trend-lr-v4

- **Sport:** NBA
- **Market capability:** moneyline
- **Model family:** elo_trend_normal_approximation (moneyline head: logistic regression)
- **Artifact path:** config/models/nba-elo-trend-lr-v4.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version, served via learned_forward.build_learned_moneyline_slate
- **Historically callable:** yes
- **Feature names:** elo_probability, trend_gap, defensive_trend_gap
- **Training / validation / holdout period:** coefficient_fit 2024-01-08..2025-05-27 (2171 obs); threshold_selection 2025-05-28..2026-01-23 (753 obs); locked_holdout 2026-01-24..2026-06-13 (654 obs, 577 calls)
- **Model status:** shadow_qualified
- **Qualification status:** qualified=true. hit_rate 73.66% on 577 calls, every qualifying month positive, +234.4 units at -110.
- **Calibration method + metrics:** brier_score=0.18541, calibration_slope=1.785, ECE=0.0605
- **Save/load support:** yes - JSON coefficient artifact
- **PIT status:** walk_forward_features=true
- **Train/serve parity notes:** shared feature dispatch with learned_forward.py
- **Known defects:** config/model.yaml lists protected_versions=['nba-elo-trend-v1','nba-elo-trend-lr-v1','nba-elo-trend-lr-v3'] -- 'nba-elo-trend-v1' (no '-lr-') does not correspond to any file in config/models/ or config/models/archive/ (only nba-elo-trend-lr-v1/v2/v3 exist); this looks like a stale/typo'd protected-version reference.
- **Notes:** Strongest-performing production model in the current inventory by units and hit rate.
- **Recommendation:** **KEEP_PRIMARY**

#### nba-spread-baseline-v1

- **Sport:** NBA
- **Market capability:** spread, total
- **Model family:** baseline_heuristic
- **Artifact path:** config/models/nba-spread-baseline-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - NBA.spread_research_artifact and total_research_artifact
- **Historically callable:** yes
- **Feature names:** elo_margin-derived heuristic (unfitted)
- **Training / validation / holdout period:** none - heuristic baseline
- **Model status:** active_research
- **Qualification status:** qualification dict present (10 keys) but heuristic, not walk-forward fit
- **Calibration method + metrics:** none - unfitted heuristic
- **Save/load support:** yes (artifact exists)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** none newly found
- **Notes:** Unlike MLB, NBA's spread/total research artifact IS the live-read baseline (no P0-5-style orphan issue found for NBA).
- **Recommendation:** **KEEP_BASELINE**

#### nba-total-score-ridge-v1

- **Sport:** NBA
- **Market capability:** total (raw score regression)
- **Model family:** ridge_regression
- **Artifact path:** config/models/nba-total-score-ridge-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** unclear - status research_score_model_candidate, not referenced by config/model.yaml active pointers
- **Historically callable:** unknown
- **Feature names:** 9 features
- **Training / validation / holdout period:** see artifact training/locked_holdout dicts
- **Model status:** research_score_model_candidate
- **Qualification status:** market_qualification dict present (4 keys)
- **Calibration method + metrics:** validation_residual_sd=19.32 points
- **Save/load support:** yes
- **PIT status:** not verified
- **Train/serve parity notes:** no calling code found
- **Known defects:** Not wired into the live spread/total pipeline
- **Notes:** Parallel to mlb/nfl/wnba-total-score-ridge-v1.
- **Recommendation:** **KEEP_CHALLENGER**

### NBA/WNBA

#### nba-wnba-possessions-efficiency-v1 (rebuild/models/basketball.py, orphaned)

- **Sport:** NBA/WNBA
- **Market capability:** moneyline/spread/total via one joint score distribution
- **Model family:** PossessionsModel (Ridge) for pace + efficiency model (HistGradientBoostingRegressor) -> JointScoreDistribution (normal approximation or simulation); NBA and WNBA trained independently, WNBA using stronger shrinkage per the module docstring
- **Artifact path:** none
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - rebuild-model CLI reports NOT_IMPLEMENTED for every sport (model_lifecycle.py's SUPPORTED_MODEL_SPORTS all route to _NotImplementedLifecycle); nothing in sport_adapter.py wires this class either
- **Historically callable:** no evidence of ever being wired on any of the 6 audited historical branches (confirmed byte-identical/unwired on origin/rebuild/wnba-v1 by the WNBA branch audit)
- **Feature names:** expected possessions x lineup-adjusted points per possession (design-level; no concrete feature list implemented/wired)
- **Training / validation / holdout period:** none - never trained end-to-end
- **Model status:** dead code / unwired
- **Qualification status:** none
- **Calibration method + metrics:** none
- **Save/load support:** fit()/predict() exist on PossessionsModel but no end-to-end persistence found
- **PIT status:** not evaluable, unwired
- **Train/serve parity notes:** not evaluable, unwired
- **Known defects:** A materially different, more expressive family (possessions x efficiency) than both production NBA/WNBA (elo_trend_normal_approximation) and the WNBA-branch research ensemble (box-score rolling form) -- three distinct unpromoted NBA/WNBA model families now exist in this codebase's history, only one of which (production) is actually served.
- **Notes:** Confirmed identical across current main and every historical branch that includes it -- a design that was written once and never revisited.
- **Recommendation:** **RETIRE**

### WNBA

#### wnba-elo-trend-lr-v1

- **Sport:** WNBA
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/wnba-elo-trend-lr-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived, but v1 and v3 named in config/model.yaml protected_versions
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** archived
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived
- **Known defects:** Same protected_versions stale-reference issue as NBA (see nba-elo-trend-lr-v1's record)
- **Notes:** Given WNBA is real-money production, its rollback chain matters more than research-only sports.
- **Recommendation:** **KEEP_ROLLBACK**

#### wnba-elo-trend-lr-v2

- **Sport:** WNBA
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/wnba-elo-trend-lr-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived, but v1 and v3 named in config/model.yaml protected_versions
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** archived
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived
- **Known defects:** Same protected_versions stale-reference issue as NBA (see nba-elo-trend-lr-v1's record)
- **Notes:** Given WNBA is real-money production, its rollback chain matters more than research-only sports.
- **Recommendation:** **RETIRE**

#### wnba-elo-trend-lr-v3

- **Sport:** WNBA
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/wnba-elo-trend-lr-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived, but v1 and v3 named in config/model.yaml protected_versions
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** archived
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived
- **Known defects:** Same protected_versions stale-reference issue as NBA (see nba-elo-trend-lr-v1's record)
- **Notes:** Given WNBA is real-money production, its rollback chain matters more than research-only sports.
- **Recommendation:** **KEEP_ROLLBACK**

#### wnba-elo-trend-lr-v4

- **Sport:** WNBA
- **Market capability:** moneyline
- **Model family:** elo_trend_normal_approximation (moneyline head: logistic regression)
- **Artifact path:** config/models/wnba-elo-trend-lr-v4.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version, real-money-sized (Main ledger) per CLAUDE.md
- **Historically callable:** yes
- **Feature names:** elo_probability, trend_gap, defensive_trend_gap
- **Training / validation / holdout period:** coefficient_fit 2024-05-31..2025-08-15 (457 obs); threshold_selection 2025-08-16..2026-05-18 (143 obs); locked_holdout 2026-05-19..2026-07-19 (163 obs, 163 calls)
- **Model status:** shadow_qualified
- **Qualification status:** qualified=true. hit_rate 67.48% on 163 calls (called_rate=1.0, no confidence gating applied), +47.0 units at -110.
- **Calibration method + metrics:** brier_score=0.21414, calibration_slope=1.270, ECE=0.0465
- **Save/load support:** yes - JSON coefficient artifact
- **PIT status:** walk_forward_features=true
- **Train/serve parity notes:** shared feature dispatch with learned_forward.py
- **Known defects:** config/model.yaml protected_versions=['wnba-elo-trend-v1','wnba-elo-trend-lr-v1','wnba-elo-trend-lr-v3'] -- same 'wnba-elo-trend-v1' (no '-lr-') stale-reference pattern as NBA.
- **Notes:** One of only two models (with MLB moneyline) promoted to real, sized Main-ledger execution per CLAUDE.md. sizing_recommendation in config notes flat 2.0u outperforms Kelly/edge-scaled sizing due to thin edges (76.9% cited hit rate on 13 settled picks vs the 163-call locked-holdout figure above).
- **Recommendation:** **KEEP_PRIMARY**

#### wnba-research-baseline-ensemble (unmerged)

- **Sport:** WNBA
- **Market capability:** moneyline (logistic), spread/margin (Ridge+Elo), total (Ridge), joint score distribution
- **Model family:** regularized_logistic + ridge (margin, total) + EloModel ensemble/comparison, not a single deployable artifact
- **Artifact path:** none - only ephemeral evidence at <runtime_root>/wnba_baselines/{hash}.json + .parquet (report/OOF only, immutable, never a deployable model)
- **Source branch:** origin/rebuild/wnba-v1 (tag archive/model-source-rebuild-wnba-v1-95c7dcc2)
- **Currently callable from main:** no - src/model_prediction/rebuild/wnba/{baselines,features,horizon_builder}.py and the _WNBAAdapter class that wires them do not exist on main; main routes WNBA rebuild-shadow requests through the generic _BasicEloAdapter which has no build_features/predict path for WNBA at all
- **Historically callable:** yes on the branch, feature-build stage only (predict/match_markets/decide were STAGE_NOT_IMPLEMENTED / rights-blocked even there)
- **Feature names:** 16 season-level features (home/away x season_ortg/drtg/netrtg/pace/efg/tov_pct/orb_pct/ft_rate) plus ~68 rolling-window columns (last_5/10/20/season x 8 metrics per side)
- **Training / validation / holdout period:** expanding chronological date-folds (n_splits=4, min_train_dates=3), dataset-driven, no fixed calendar window
- **Model status:** RESEARCH_ONLY (module constant)
- **Qualification status:** BLOCKED (module constant) -- SportsDataverse/ESPN upstream commercial-use rights were never cleared; historical captures are capture_time_only, not retrospective PIT evidence
- **Calibration method + metrics:** log_loss/brier/accuracy per moneyline variant, MAE/RMSE for margin/total, computed per fold and reported OOF; no calibration curve/isotonic step
- **Save/load support:** no, by design -- write_research_baseline_artifacts persists only the evaluation report and OOF predictions, never a fitted, loadable model object
- **PIT status:** strong -- eligible_prior_team_games gate plus horizon_builder.py's own decision-cutoff stabilization loop, fails closed on missing capture_time_only/unresolved/production_allowed=False provenance
- **Train/serve parity notes:** strong by construction -- build_team_form_snapshot explicitly documented as serving both research and live rows; branch's own test asserts replay/live path PIT-feature identity
- **Known defects:** Rights-blocked at the data layer (SportsDataverse/ESPN commercial-use terms unresolved), not a code or accuracy defect. horizon_builder.py's public entry points have only indirect test coverage (through the never-merged _WNBAAdapter's tests).
- **Notes:** A genuinely different, more expressive WNBA model family (box-score rolling form features vs. production's 3-feature Elo+trend) than either production WNBA model. Well-engineered but categorically unpromotable today due to data-rights blocking, not performance. Worth reconsidering only if wnba-elo-trend-lr-v4/wnba-spread-baseline-v1 fail the audit AND a rights review clears the source.
- **Recommendation:** **REPLACE_ONLY_IF_AUDIT_FAILS**

#### wnba-spread-baseline-v1

- **Sport:** WNBA
- **Market capability:** spread, total
- **Model family:** baseline_heuristic
- **Artifact path:** config/models/wnba-spread-baseline-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - WNBA.spread_research_artifact/total_research_artifact
- **Historically callable:** yes
- **Feature names:** elo_margin-derived heuristic (unfitted)
- **Training / validation / holdout period:** none - heuristic baseline
- **Model status:** active_research
- **Qualification status:** qualification dict present (10 keys), heuristic not walk-forward fit
- **Calibration method + metrics:** none - unfitted heuristic
- **Save/load support:** yes
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** config documents a real sizing finding: Kelly/edge-scaled sizing underperforms flat betting here due to thin edges (flat 2.0u: +2.64u on 13 settled picks vs +0.40u actual ledger P&L)
- **Notes:** research_outputs_zero_units is NOT set for WNBA (unlike KBO/NPB) -- this baseline can size real research rows.
- **Recommendation:** **KEEP_BASELINE**

#### wnba-total-score-ridge-v1

- **Sport:** WNBA
- **Market capability:** total (raw score regression)
- **Model family:** ridge_regression
- **Artifact path:** config/models/wnba-total-score-ridge-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** unclear - status research_score_model_candidate
- **Historically callable:** unknown
- **Feature names:** 9 features
- **Training / validation / holdout period:** see artifact training/locked_holdout dicts
- **Model status:** research_score_model_candidate
- **Qualification status:** market_qualification dict present (4 keys)
- **Calibration method + metrics:** validation_residual_sd=16.55 points
- **Save/load support:** yes
- **PIT status:** not verified
- **Train/serve parity notes:** no calling code found
- **Known defects:** Not wired into the live spread/total pipeline
- **Notes:** Parallel to mlb/nba/nfl-total-score-ridge-v1.
- **Recommendation:** **KEEP_CHALLENGER**

### NFL

#### nfl-drive-v2 (rebuild/models/nfl.py, orphaned)

- **Sport:** NFL
- **Market capability:** moneyline, spread, total via drive-outcome Monte Carlo
- **Model family:** expected-drives x drive-outcome-distribution simulation (HistGradientBoostingRegressor for drive count, two Ridge regressors for home/away EPA, 1000-sim scoring)
- **Artifact path:** none - fit()/predict() only, no serialization
- **Source branch:** byte-identical on origin/rebuild/nfl-v1 and origin/main (predates the branch, not introduced by it)
- **Currently callable from main:** technically importable but not referenced anywhere else in the codebase -- no CLI, no tests, no config artifact, not wired into model_cli.py/model_lifecycle.py beyond the generic SUPPORTED_MODEL_SPORTS sport-name tuple
- **Historically callable:** same - orphaned in this exact form on both refs
- **Feature names:** not evaluable - never trained end-to-end
- **Training / validation / holdout period:** none - never trained/validated in-repo
- **Model status:** dead code / unwired
- **Qualification status:** none
- **Calibration method + metrics:** none
- **Save/load support:** no
- **PIT status:** not evaluable, unwired
- **Train/serve parity notes:** not evaluable, unwired
- **Known defects:** Defined but never imported/wired into any sport_adapter; a materially different model family from production's elo_trend_normal_approximation, sitting unused in the tree
- **Notes:** Confirmed by direct code search (git grep NFLModel returns only its own definition file) and independently by the NFL-branch audit agent.
- **Recommendation:** **RETIRE**

#### nfl-elo-trend-lr-v1

- **Sport:** NFL
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/nfl-elo-trend-lr-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived, not referenced by config/model.yaml at all (no protected_versions list for NFL)
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** archived
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found
- **Notes:** No config reference at all, unlike NBA/WNBA's protected_versions pattern.
- **Recommendation:** **RETIRE**

#### nfl-elo-trend-lr-v2

- **Sport:** NFL
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/nfl-elo-trend-lr-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived, not referenced by config/model.yaml at all (no protected_versions list for NFL)
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** archived
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found
- **Notes:** No config reference at all, unlike NBA/WNBA's protected_versions pattern.
- **Recommendation:** **RETIRE**

#### nfl-elo-trend-lr-v3

- **Sport:** NFL
- **Market capability:** moneyline
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/archive/nfl-elo-trend-lr-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived, not referenced by config/model.yaml at all (no protected_versions list for NFL)
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** archived
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown, predates current PIT contract
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found
- **Notes:** No config reference at all, unlike NBA/WNBA's protected_versions pattern.
- **Recommendation:** **RETIRE**

#### nfl-elo-trend-lr-v4

- **Sport:** NFL
- **Market capability:** moneyline
- **Model family:** elo_trend_normal_approximation (moneyline head: logistic regression)
- **Artifact path:** config/models/nfl-elo-trend-lr-v4.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version, served via learned_forward.build_learned_moneyline_slate
- **Historically callable:** yes
- **Feature names:** elo_probability, trend_gap
- **Training / validation / holdout period:** coefficient_fit 2024-08-18..2025-09-11 (366 obs); threshold_selection 2025-09-14..2025-11-17 (146 obs); locked_holdout 2025-11-20..2026-02-08 (122 obs, 87 calls)
- **Model status:** shadow_qualified
- **Qualification status:** qualified=true. hit_rate 71.26% on 87 calls, every qualifying month positive, +31.4 units at -110.
- **Calibration method + metrics:** brier_score=0.20474, calibration_slope=1.232, ECE=0.1009 (highest ECE of the four Elo+trend production models)
- **Save/load support:** yes - JSON coefficient artifact
- **PIT status:** walk_forward_features=true
- **Train/serve parity notes:** shared feature dispatch with learned_forward.py
- **Known defects:** Smallest feature set of the four production Elo+trend models (only 2 features vs. NBA/WNBA's 3 and MLB's 6) and the highest ECE -- flagged as the least-elaborated of the four production models, worth a feature-expansion look though it still qualifies cleanly on hit rate/units.
- **Notes:** NFL's config/model.yaml section, unlike NBA/WNBA, has no protected_versions list at all.
- **Recommendation:** **KEEP_PRIMARY**

#### nfl-spread-baseline-v1

- **Sport:** NFL
- **Market capability:** spread, total
- **Model family:** baseline_heuristic
- **Artifact path:** config/models/nfl-spread-baseline-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - NFL.spread_research_artifact/total_research_artifact
- **Historically callable:** yes
- **Feature names:** elo_margin-derived heuristic (unfitted)
- **Training / validation / holdout period:** none - heuristic baseline
- **Model status:** active_research
- **Qualification status:** qualification dict present (10 keys), heuristic not walk-forward fit
- **Calibration method + metrics:** none - unfitted heuristic
- **Save/load support:** yes
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** none newly found
- **Notes:** Parallel to nba/wnba-spread-baseline-v1.
- **Recommendation:** **KEEP_BASELINE**

#### nfl-total-score-ridge-v1

- **Sport:** NFL
- **Market capability:** total (raw score regression)
- **Model family:** ridge_regression
- **Artifact path:** config/models/nfl-total-score-ridge-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** unclear - status research_score_model_candidate
- **Historically callable:** unknown
- **Feature names:** 9 features
- **Training / validation / holdout period:** see artifact training/locked_holdout dicts
- **Model status:** research_score_model_candidate
- **Qualification status:** market_qualification dict present (4 keys)
- **Calibration method + metrics:** validation_residual_sd=13.30 points
- **Save/load support:** yes
- **PIT status:** not verified
- **Train/serve parity notes:** no calling code found
- **Known defects:** Not wired into the live spread/total pipeline
- **Notes:** Parallel to mlb/nba/wnba-total-score-ridge-v1.
- **Recommendation:** **KEEP_CHALLENGER**

### SOCCER

#### soccer-dc-v2 (rebuild/models/soccer.py, orphaned)

- **Sport:** SOCCER
- **Market capability:** 1X2 + totals + BTTS (draw-aware)
- **Model family:** Dixon-Coles Poisson, attack/defense strengths learned via SGD, home-advantage and rho fit from data (not hardcoded, unlike the production soccer-poisson-dc-v1)
- **Artifact path:** none - no saved weights
- **Source branch:** byte-identical on origin/rebuild/soccer-v1 and origin/main (predates the branch's unique commits)
- **Currently callable from main:** no - nothing in sport_adapter.py/build_adapter() wires this class into the rebuild-shadow pipeline; build_adapter('soccer', ...) returns _BasicEloAdapter, which never calls it
- **Historically callable:** same - appears unwired on both refs as far as located
- **Feature names:** attack/defense strengths (learned via SGD), home advantage, Dixon-Coles rho -- all data-fit rather than fixed constants (a genuine methodological upgrade over the production model's fixed HOME_GOAL_BOOST/DC_RHO constants)
- **Training / validation / holdout period:** none - never trained end-to-end in any located run
- **Model status:** dead code / unwired
- **Qualification status:** none
- **Calibration method + metrics:** none
- **Save/load support:** fit()/predict() exist but no persistence path found
- **PIT status:** not evaluable, unwired
- **Train/serve parity notes:** not evaluable, unwired
- **Known defects:** origin/rebuild/soccer-v1's own docs/rebuild/SOCCER_DATA.md states 'soccer model stages remain disabled until a draw-aware 1X2 model and replay-safe PIT feature set exist' -- yet this already-draw-aware class exists, unused, in that exact state on both the branch and main; it was written but never integrated or validated.
- **Notes:** Unlike the other orphaned rebuild/models/*.py files (basketball/tennis/kbo_npb/esports/nfl, all recommended RETIRE), this one is flagged REPAIR_SERVING rather than RETIRE because its math is a genuine, data-fit improvement over the production model's fixed constants and the SOCCER_DATA.md doc treats wiring it up as the actual open task, not an abandoned direction.
- **Recommendation:** **REPAIR_SERVING**

#### soccer-elo-trend-lr-v1

- **Sport:** SOCCER
- **Market capability:** moneyline (binary)
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/soccer-elo-trend-lr-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded by v2
- **Historically callable:** yes, prior generation
- **Feature names:** see market_models.moneyline.feature_names in artifact
- **Training / validation / holdout period:** not in current inventory scope
- **Model status:** superseded
- **Qualification status:** not surfaced for this audit
- **Calibration method + metrics:** not surfaced
- **Save/load support:** yes (artifact exists)
- **PIT status:** unknown
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Sits outside config/models/archive/ despite being superseded -- an organizational inconsistency worth fixing (every other sport's oldest versions live under archive/).
- **Recommendation:** **RETIRE**

#### soccer-elo-trend-lr-v2

- **Sport:** SOCCER
- **Market capability:** moneyline (binary reference model)
- **Model family:** elo_trend_logistic_regression
- **Artifact path:** config/models/soccer-elo-trend-lr-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - legacy_binary_research_version in config/model.yaml
- **Historically callable:** yes
- **Feature names:** elo_probability, trend_gap
- **Training / validation / holdout period:** coefficient_fit 2024-01-13..2025-08-08 (5476 obs); threshold_selection 2025-08-09..2026-01-28 (2009 obs); locked_holdout 2026-01-30..2026-07-17 (1601 obs, 1381 calls)
- **Model status:** legacy reference
- **Qualification status:** qualified=true. hit_rate 64.88% on 1381 calls, +329.5 units at -110 -- the largest locked-holdout sample and unit total of any model in this inventory.
- **Calibration method + metrics:** brier_score=0.22015, calibration_slope=0.776 (only production model with slope < 1, i.e. mildly overconfident), ECE=0.0367
- **Save/load support:** yes - JSON coefficient artifact
- **PIT status:** walk_forward_features=true
- **Train/serve parity notes:** same learned_forward.py-style dispatch pattern as MLB/NBA/WNBA/NFL, though this model is invoked as a reference rather than the live primary
- **Known defects:** Kept only as a legacy binary comparison point now that soccer-poisson-dc-v1 (multinomial 3-way) is the active model
- **Notes:** Despite strong qualification numbers, superseded as primary because it cannot represent draws -- kept explicitly as legacy_binary_research_version for comparison.
- **Recommendation:** **KEEP_BASELINE**

#### soccer-poisson-dc-v1

- **Sport:** SOCCER
- **Market capability:** total (O/U 2.5, the only configured/gated market); 1X2 and BTTS computed by the same code but not separately gated
- **Model family:** poisson_dixon_coles (correlated Poisson score matrix, Dixon-Coles low-score correction, Platt-scaled BTTS)
- **Artifact path:** none - code-parameterized only (src/model_prediction/models/soccer.py); no JSON/YAML artifact exists on main or any historical branch
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_research_version, live via registry.get_model('soccer')
- **Historically callable:** yes
- **Feature names:** EWMA attack/defense strength, home-goal boost (HOME_GOAL_BOOST=1.15), Dixon-Coles low-score dependence (DC_RHO=-0.10)
- **Training / validation / holdout period:** validation.qualify_soccer_total_model ran 2026-08-03: chronological 60/20/20 split, confidence threshold learned on validation at a 65% target hit rate, graded on a separate locked holdout
- **Model status:** shadow_qualified via qualification_override=true (manual, not genuine promotion)
- **Qualification status:** Only the TOTALS side (the market actually configured in config/model.yaml's SOCCER.markets: [full_game_total_2_5]) has a real validation result: qualified=true, 66.7% hit rate on 162 locked-holdout calls, +44.2u at -110, every qualifying month positive. Config's own comment states no artifact file with a qualified/qualified_for_betting field exists for this model at all -- confirmed absent on main and on every historical branch checked (soccer-v1 agent).
- **Calibration method + metrics:** BTTS calibration: Platt scaling (intercept=0.1393, slope=0.4205), holdout accuracy 55.0%->56.7%, calibration buckets close to diagonal. Moneyline/totals from the identical model score ~62.5% per the code's own comment; BTTS alone was weaker before Platt correction.
- **Save/load support:** no - fit live from features each run, no persisted trained-parameter artifact (unlike the Elo+trend LR family)
- **PIT status:** market prices never enter this model (features/trends.ewm_level EWMA form only)
- **Train/serve parity notes:** same Poisson simulation code path used for both the 2026-08-03 validation run and live serving
- **Known defects:** No persisted, versioned qualification artifact exists for this model at all (unlike every other shadow_qualified league) -- qualification evidence lives only in a config/model.yaml comment, not a reproducible file. _row_artifact_qualified in cli.py fails closed for this override, so real unattended execution stays blocked without --manual-research-order. Separately (rebuild-shadow pipeline only, not this production model): the rebuild-shadow CLI's _BasicEloAdapter forces a binary Elo baseline onto soccer's 3-way outcome space for the shadow/research track -- a fix for this (_SoccerCollectionOnlyAdapter, disabling the unsafe binary path) exists on origin/rebuild/soccer-v1 but was never merged to main.
- **Notes:** Named explicitly in this audit's incumbent-retention list. The missing persisted qualification artifact is the most actionable single gap found for this model -- recommend generating one via validation.py so future audits don't have to re-derive qualification state from a YAML comment.
- **Recommendation:** **KEEP_PRIMARY**

### TENNIS

#### tennis-elo-sr-v2 (rebuild/models/tennis.py, orphaned)

- **Sport:** TENNIS
- **Market capability:** match winner probability
- **Model family:** surface Elo (K=32 default) with per-surface rating tracks, dynamic surface-match-count blend weight, plus a logistic serve/return model (sklearn LogisticRegression) fit from data when available, falling back to Elo-only otherwise
- **Artifact path:** none
- **Source branch:** origin/main (current)
- **Currently callable from main:** ambiguous - this file is byte-identical to the file backing the LIVE tennis-surface-elo-v1 model (src/model_prediction/rebuild/models/tennis.py is imported by both the registry path and referenced by the rebuild pipeline)
- **Historically callable:** see tennis-surface-elo-v1's record -- this IS that model's source file, not a separate orphaned model
- **Feature names:** see tennis-surface-elo-v1
- **Training / validation / holdout period:** see tennis-surface-elo-v1
- **Model status:** see tennis-surface-elo-v1
- **Qualification status:** see tennis-surface-elo-v1
- **Calibration method + metrics:** see tennis-surface-elo-v1
- **Save/load support:** see tennis-surface-elo-v1
- **PIT status:** see tennis-surface-elo-v1
- **Train/serve parity notes:** see tennis-surface-elo-v1
- **Known defects:** This module-internal version string (tennis-elo-sr-v2, TennisPrediction.model_version default) does not match the externally-facing TENNIS_MODEL_VERSION='tennis-surface-elo-v1' constant used elsewhere (src/model_prediction/models/tennis.py) -- a naming inconsistency between the two tennis model wrapper layers, though both resolve to the same underlying Elo computation.
- **Notes:** De-duplicated with tennis-surface-elo-v1 above -- listed separately only because the task asked to inventory every model file in src/model_prediction/rebuild/models/, and this file's internal model_version string differs from the production-facing one, which is itself worth flagging.
- **Recommendation:** **KEEP_PRIMARY**

#### tennis-surface-elo-v1

- **Sport:** TENNIS
- **Market capability:** moneyline (WTA + ATP; ESPN has no ITF scoreboard)
- **Model family:** surface_blended_elo (60% surface-specific / 40% overall Elo blend, K=32, optional logistic serve/return blend)
- **Artifact path:** none - code-parameterized only (src/model_prediction/rebuild/models/tennis.py backs the live registry model; src/model_prediction/models/tennis.py holds the production wrapper); no JSON artifact exists on main or the tennis-v1 branch
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_research_version, live via registry.get_model('tennis')
- **Historically callable:** yes
- **Feature names:** overall Elo rating, per-surface Elo rating, surface match count (dynamic blend weight), serve points won %, return points won % (when available)
- **Training / validation / holdout period:** validation.qualify_tennis_elo_model ran 2026-08-03: same chronological 60/20/20, learned-threshold methodology as soccer
- **Model status:** shadow_qualified via qualification_override=true (manual, not genuine promotion)
- **Qualification status:** qualified=true per config comment (not a persisted artifact): 65.5% hit rate on 4269 locked-holdout calls, +1070.7 units at -110, every qualifying month positive -- the strongest result and largest sample of any model in this inventory. No persisted artifact file backs this number, same gap pattern as soccer-poisson-dc-v1.
- **Calibration method + metrics:** none formalized beyond the hit-rate/units backtest cited above; no Brier/calibration artifact found
- **Save/load support:** no - in-memory Elo state only, no persisted trained-parameter artifact
- **PIT status:** rebuild/tennis/pit.py provides real PIT gating (eligible_matches_as_of, eligible_prior_matches_for_player) for the live TennisMyLife+ESPN data foundation
- **Train/serve parity notes:** two now-fixed defects documented in DEBUG.md (2026-07-28): ATP/WTA fetch-order tournament mistagging, and a FeatureStore abstraction mismatch that silently zeroed all tennis history -- both confirmed fixed on current main
- **Known defects:** No persisted, versioned qualification artifact exists despite shadow_qualified status (same gap as soccer). Known coverage gap: Polymarket US tennis is WTA/ITF only while ESPN tennis is ATP/WTA only, so the real overlap this model can act on is WTA only, even though it computes ATP predictions too.
- **Notes:** Named explicitly in this audit's incumbent-retention list; the strongest backtest sample of any model here, but should have its 4269-call qualification run re-materialized as a real artifact rather than living only in a config comment.
- **Recommendation:** **KEEP_PRIMARY**

### LOL/CS2/DOTA2/VALORANT/RAINBOW_SIX

#### esports-roster-v1 (rebuild/models/esports.py, orphaned)

- **Sport:** LOL/CS2/DOTA2/VALORANT/RAINBOW_SIX
- **Market capability:** single-game and series probability
- **Model family:** per-title roster-based, player-level, map/patch/draft-aware (org Elo retained only as a prior, not the whole model)
- **Artifact path:** none
- **Source branch:** present on main only; not part of any of the 6 audited historical branches
- **Currently callable from main:** no - _EsportsStubAdapter (sport_adapter.py) has no model wiring at all for esports in the rebuild-shadow pipeline
- **Historically callable:** no evidence of ever being wired
- **Feature names:** not evaluable, unwired (module docstring implies roster/player/map/patch/draft features that were never implemented in this file's current form)
- **Training / validation / holdout period:** none
- **Model status:** dead code / unwired
- **Qualification status:** none
- **Calibration method + metrics:** none
- **Save/load support:** no persistence path found
- **PIT status:** not evaluable, unwired
- **Train/serve parity notes:** not evaluable, unwired
- **Known defects:** A materially more ambitious design (per-player, per-map, draft-aware) than the production tiered-elo family, but never implemented beyond dataclass stubs -- no fit/predict logic exists
- **Notes:** Least-developed of the orphaned rebuild/models/*.py files -- essentially a design sketch, not a working model.
- **Recommendation:** **RETIRE**

### LOL

#### lol-neutral-series-elo-v1

- **Sport:** LOL
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/lol-neutral-series-elo-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (earliest esports capture)
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False (research state throughout)
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Earliest esports model generation, before the tiered-elo naming/schema was introduced.
- **Recommendation:** **RETIRE**

#### lol-neutral-series-elo-v2

- **Sport:** LOL
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/lol-neutral-series-elo-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Same generation as v1; identical matches_sha256, distinct confidence_threshold experiment.
- **Recommendation:** **RETIRE**

#### lol-tiered-elo-v3

- **Sport:** LOL
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/lol-tiered-elo-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** none - v3 uses schema esports-neutral-elo-v1 (pre-Platt)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### lol-tiered-elo-v4

- **Sport:** LOL
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/lol-tiered-elo-v4.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** Platt intercept/slope introduced in v4 (schema esports-neutral-elo-v2)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### lol-tiered-elo-v5

- **Sport:** LOL
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled)
- **Artifact path:** config/models/lol-tiered-elo-v5.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded by v6
- **Historically callable:** yes, was production/research before v6 (2026-08-04)
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (pre-2026-08-04)
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time; K selected by min Brier for the first time in v5 (replacing v4's flat-stake-P&L-optimized K which sat at the search grid's exact top for 4/5 titles -- a truncated-search/overfitting signal explicitly called out in config comments)
- **Calibration method + metrics:** Platt intercept/slope in artifact
- **Save/load support:** yes (artifact exists)
- **PIT status:** same ratings-dict pattern as v6
- **Train/serve parity notes:** n/a - not served
- **Known defects:** v4's K=96 override was flagged as likely overfit (top-of-grid for 4/5 titles); v5 fixed the selection methodology
- **Notes:** Immediate rollback target for v6 within the same family.
- **Recommendation:** **KEEP_ROLLBACK**

#### lol-tiered-elo-v5.previous

- **Sport:** LOL
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (pre-write backup snapshot)
- **Artifact path:** config/models/lol-tiered-elo-v5.previous.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** no - appears to be an automatic pre-write backup, not a distinct trained model
- **Feature names:** identical schema/ratings shape to v5
- **Training / validation / holdout period:** same trained_through_utc as v5
- **Model status:** backup snapshot
- **Qualification status:** n/a
- **Calibration method + metrics:** near-identical to v5 (same platt_intercept/slope, same training_observations)
- **Save/load support:** yes (artifact exists)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** Near-duplicate of v5 with a different source_manifest_sha256 -- an artifact-write safety copy, not a separate model worth independent tracking
- **Notes:** Same pattern observed for cs2/dota2/lol/rainbow_six/valorant -- every *-v5.previous.json is this kind of backup, not a unique model.
- **Recommendation:** **RETIRE**

#### lol-tiered-elo-v6

- **Sport:** LOL
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled), with 2026-08-04 inactivity-decay + thin-data confidence discount at prediction time
- **Artifact path:** config/models/lol-tiered-elo-v6.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version
- **Historically callable:** yes since 2026-08-04
- **Feature names:** point-in-time team Elo, games_played (K/confidence context), last_match_utc (inactivity decay input)
- **Training / validation / holdout period:** trained_through_utc 2026-08-04 (see artifact); qualification_override_reason states the standard walk-forward gate is not applicable to esports match-winner markets
- **Model status:** shadow_qualified
- **Qualification status:** qualification_override=true, reason: 'Esports v6 artifacts use Platt-scaled Elo; qualification gate is not applicable to esports match-winner markets.'
- **Calibration method + metrics:** Platt intercept/slope in artifact (title-specific); K chosen by minimum Brier (a proper scoring rule) as of v5, confidence_threshold chosen by units_at_minus_110 (found a genuine interior optimum ~0.03-0.05 per title in v5's re-validation)
- **Save/load support:** yes - JSON ratings dict, loaded and updated incrementally
- **PIT status:** ratings keyed by point-in-time team Elo; ratings/games_played/last_match_utc dicts preserve per-team history for the decay computation
- **Train/serve parity notes:** same NeutralElo.probability() code path for training re-validation and live serving
- **Known defects:** v6's own honest trade-off, disclosed in config: shrinking confidence on thin-data matchups cost some locked-test accuracy for LOL/DOTA2 (70.6%->69.2%, 68.1%->64.8% respectively) while CS2 improved slightly (65.8%->66.0%) -- not every title showed the same trade-off direction.
- **Notes:** research_confidence_gate values are title-specific and re-derived each version; RAINBOW_SIX is functionally identical in architecture to the four promoted titles but withheld from shadow_qualified pending a deliberate per-title review.
- **Recommendation:** **KEEP_PRIMARY**

### CS2

#### cs2-neutral-series-elo-v1

- **Sport:** CS2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/cs2-neutral-series-elo-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (earliest esports capture)
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False (research state throughout)
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Earliest esports model generation, before the tiered-elo naming/schema was introduced.
- **Recommendation:** **RETIRE**

#### cs2-neutral-series-elo-v2

- **Sport:** CS2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/cs2-neutral-series-elo-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Same generation as v1; identical matches_sha256, distinct confidence_threshold experiment.
- **Recommendation:** **RETIRE**

#### cs2-tiered-elo-v3

- **Sport:** CS2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/cs2-tiered-elo-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** none - v3 uses schema esports-neutral-elo-v1 (pre-Platt)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### cs2-tiered-elo-v4

- **Sport:** CS2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/cs2-tiered-elo-v4.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** Platt intercept/slope introduced in v4 (schema esports-neutral-elo-v2)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### cs2-tiered-elo-v5

- **Sport:** CS2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled)
- **Artifact path:** config/models/cs2-tiered-elo-v5.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded by v6
- **Historically callable:** yes, was production/research before v6 (2026-08-04)
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (pre-2026-08-04)
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time; K selected by min Brier for the first time in v5 (replacing v4's flat-stake-P&L-optimized K which sat at the search grid's exact top for 4/5 titles -- a truncated-search/overfitting signal explicitly called out in config comments)
- **Calibration method + metrics:** Platt intercept/slope in artifact
- **Save/load support:** yes (artifact exists)
- **PIT status:** same ratings-dict pattern as v6
- **Train/serve parity notes:** n/a - not served
- **Known defects:** v4's K=96 override was flagged as likely overfit (top-of-grid for 4/5 titles); v5 fixed the selection methodology
- **Notes:** Immediate rollback target for v6 within the same family.
- **Recommendation:** **KEEP_ROLLBACK**

#### cs2-tiered-elo-v5.previous

- **Sport:** CS2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (pre-write backup snapshot)
- **Artifact path:** config/models/cs2-tiered-elo-v5.previous.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** no - appears to be an automatic pre-write backup, not a distinct trained model
- **Feature names:** identical schema/ratings shape to v5
- **Training / validation / holdout period:** same trained_through_utc as v5
- **Model status:** backup snapshot
- **Qualification status:** n/a
- **Calibration method + metrics:** near-identical to v5 (same platt_intercept/slope, same training_observations)
- **Save/load support:** yes (artifact exists)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** Near-duplicate of v5 with a different source_manifest_sha256 -- an artifact-write safety copy, not a separate model worth independent tracking
- **Notes:** Same pattern observed for cs2/dota2/lol/rainbow_six/valorant -- every *-v5.previous.json is this kind of backup, not a unique model.
- **Recommendation:** **RETIRE**

#### cs2-tiered-elo-v6

- **Sport:** CS2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled), with 2026-08-04 inactivity-decay + thin-data confidence discount at prediction time
- **Artifact path:** config/models/cs2-tiered-elo-v6.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version
- **Historically callable:** yes since 2026-08-04
- **Feature names:** point-in-time team Elo, games_played (K/confidence context), last_match_utc (inactivity decay input)
- **Training / validation / holdout period:** trained_through_utc 2026-08-04 (see artifact); qualification_override_reason states the standard walk-forward gate is not applicable to esports match-winner markets
- **Model status:** shadow_qualified
- **Qualification status:** qualification_override=true, reason: 'Esports v6 artifacts use Platt-scaled Elo; qualification gate is not applicable to esports match-winner markets.'
- **Calibration method + metrics:** Platt intercept/slope in artifact (title-specific); K chosen by minimum Brier (a proper scoring rule) as of v5, confidence_threshold chosen by units_at_minus_110 (found a genuine interior optimum ~0.03-0.05 per title in v5's re-validation)
- **Save/load support:** yes - JSON ratings dict, loaded and updated incrementally
- **PIT status:** ratings keyed by point-in-time team Elo; ratings/games_played/last_match_utc dicts preserve per-team history for the decay computation
- **Train/serve parity notes:** same NeutralElo.probability() code path for training re-validation and live serving
- **Known defects:** v6's own honest trade-off, disclosed in config: shrinking confidence on thin-data matchups cost some locked-test accuracy for LOL/DOTA2 (70.6%->69.2%, 68.1%->64.8% respectively) while CS2 improved slightly (65.8%->66.0%) -- not every title showed the same trade-off direction.
- **Notes:** research_confidence_gate values are title-specific and re-derived each version; RAINBOW_SIX is functionally identical in architecture to the four promoted titles but withheld from shadow_qualified pending a deliberate per-title review.
- **Recommendation:** **KEEP_PRIMARY**

### DOTA2

#### dota2-neutral-series-elo-v1

- **Sport:** DOTA2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/dota2-neutral-series-elo-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (earliest esports capture)
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False (research state throughout)
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Earliest esports model generation, before the tiered-elo naming/schema was introduced.
- **Recommendation:** **RETIRE**

#### dota2-neutral-series-elo-v2

- **Sport:** DOTA2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/dota2-neutral-series-elo-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Same generation as v1; identical matches_sha256, distinct confidence_threshold experiment.
- **Recommendation:** **RETIRE**

#### dota2-tiered-elo-v3

- **Sport:** DOTA2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/dota2-tiered-elo-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** none - v3 uses schema esports-neutral-elo-v1 (pre-Platt)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### dota2-tiered-elo-v4

- **Sport:** DOTA2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/dota2-tiered-elo-v4.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** Platt intercept/slope introduced in v4 (schema esports-neutral-elo-v2)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### dota2-tiered-elo-v5

- **Sport:** DOTA2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled)
- **Artifact path:** config/models/dota2-tiered-elo-v5.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded by v6
- **Historically callable:** yes, was production/research before v6 (2026-08-04)
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (pre-2026-08-04)
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time; K selected by min Brier for the first time in v5 (replacing v4's flat-stake-P&L-optimized K which sat at the search grid's exact top for 4/5 titles -- a truncated-search/overfitting signal explicitly called out in config comments)
- **Calibration method + metrics:** Platt intercept/slope in artifact
- **Save/load support:** yes (artifact exists)
- **PIT status:** same ratings-dict pattern as v6
- **Train/serve parity notes:** n/a - not served
- **Known defects:** v4's K=96 override was flagged as likely overfit (top-of-grid for 4/5 titles); v5 fixed the selection methodology
- **Notes:** Immediate rollback target for v6 within the same family.
- **Recommendation:** **KEEP_ROLLBACK**

#### dota2-tiered-elo-v5.previous

- **Sport:** DOTA2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (pre-write backup snapshot)
- **Artifact path:** config/models/dota2-tiered-elo-v5.previous.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** no - appears to be an automatic pre-write backup, not a distinct trained model
- **Feature names:** identical schema/ratings shape to v5
- **Training / validation / holdout period:** same trained_through_utc as v5
- **Model status:** backup snapshot
- **Qualification status:** n/a
- **Calibration method + metrics:** near-identical to v5 (same platt_intercept/slope, same training_observations)
- **Save/load support:** yes (artifact exists)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** Near-duplicate of v5 with a different source_manifest_sha256 -- an artifact-write safety copy, not a separate model worth independent tracking
- **Notes:** Same pattern observed for cs2/dota2/lol/rainbow_six/valorant -- every *-v5.previous.json is this kind of backup, not a unique model.
- **Recommendation:** **RETIRE**

#### dota2-tiered-elo-v6

- **Sport:** DOTA2
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled), with 2026-08-04 inactivity-decay + thin-data confidence discount at prediction time
- **Artifact path:** config/models/dota2-tiered-elo-v6.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version
- **Historically callable:** yes since 2026-08-04
- **Feature names:** point-in-time team Elo, games_played (K/confidence context), last_match_utc (inactivity decay input)
- **Training / validation / holdout period:** trained_through_utc 2026-08-04 (see artifact); qualification_override_reason states the standard walk-forward gate is not applicable to esports match-winner markets
- **Model status:** shadow_qualified
- **Qualification status:** qualification_override=true, reason: 'Esports v6 artifacts use Platt-scaled Elo; qualification gate is not applicable to esports match-winner markets.'
- **Calibration method + metrics:** Platt intercept/slope in artifact (title-specific); K chosen by minimum Brier (a proper scoring rule) as of v5, confidence_threshold chosen by units_at_minus_110 (found a genuine interior optimum ~0.03-0.05 per title in v5's re-validation)
- **Save/load support:** yes - JSON ratings dict, loaded and updated incrementally
- **PIT status:** ratings keyed by point-in-time team Elo; ratings/games_played/last_match_utc dicts preserve per-team history for the decay computation
- **Train/serve parity notes:** same NeutralElo.probability() code path for training re-validation and live serving
- **Known defects:** v6's own honest trade-off, disclosed in config: shrinking confidence on thin-data matchups cost some locked-test accuracy for LOL/DOTA2 (70.6%->69.2%, 68.1%->64.8% respectively) while CS2 improved slightly (65.8%->66.0%) -- not every title showed the same trade-off direction.
- **Notes:** research_confidence_gate values are title-specific and re-derived each version; RAINBOW_SIX is functionally identical in architecture to the four promoted titles but withheld from shadow_qualified pending a deliberate per-title review.
- **Recommendation:** **KEEP_PRIMARY**

### VALORANT

#### valorant-neutral-series-elo-v1

- **Sport:** VALORANT
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/valorant-neutral-series-elo-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (earliest esports capture)
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False (research state throughout)
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Earliest esports model generation, before the tiered-elo naming/schema was introduced.
- **Recommendation:** **RETIRE**

#### valorant-neutral-series-elo-v2

- **Sport:** VALORANT
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/valorant-neutral-series-elo-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Same generation as v1; identical matches_sha256, distinct confidence_threshold experiment.
- **Recommendation:** **RETIRE**

#### valorant-tiered-elo-v3

- **Sport:** VALORANT
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/valorant-tiered-elo-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** none - v3 uses schema esports-neutral-elo-v1 (pre-Platt)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### valorant-tiered-elo-v4

- **Sport:** VALORANT
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/valorant-tiered-elo-v4.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** Platt intercept/slope introduced in v4 (schema esports-neutral-elo-v2)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### valorant-tiered-elo-v5

- **Sport:** VALORANT
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled)
- **Artifact path:** config/models/valorant-tiered-elo-v5.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded by v6
- **Historically callable:** yes, was production/research before v6 (2026-08-04)
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (pre-2026-08-04)
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time; K selected by min Brier for the first time in v5 (replacing v4's flat-stake-P&L-optimized K which sat at the search grid's exact top for 4/5 titles -- a truncated-search/overfitting signal explicitly called out in config comments)
- **Calibration method + metrics:** Platt intercept/slope in artifact
- **Save/load support:** yes (artifact exists)
- **PIT status:** same ratings-dict pattern as v6
- **Train/serve parity notes:** n/a - not served
- **Known defects:** v4's K=96 override was flagged as likely overfit (top-of-grid for 4/5 titles); v5 fixed the selection methodology
- **Notes:** Immediate rollback target for v6 within the same family.
- **Recommendation:** **KEEP_ROLLBACK**

#### valorant-tiered-elo-v5.previous

- **Sport:** VALORANT
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (pre-write backup snapshot)
- **Artifact path:** config/models/valorant-tiered-elo-v5.previous.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** no - appears to be an automatic pre-write backup, not a distinct trained model
- **Feature names:** identical schema/ratings shape to v5
- **Training / validation / holdout period:** same trained_through_utc as v5
- **Model status:** backup snapshot
- **Qualification status:** n/a
- **Calibration method + metrics:** near-identical to v5 (same platt_intercept/slope, same training_observations)
- **Save/load support:** yes (artifact exists)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** Near-duplicate of v5 with a different source_manifest_sha256 -- an artifact-write safety copy, not a separate model worth independent tracking
- **Notes:** Same pattern observed for cs2/dota2/lol/rainbow_six/valorant -- every *-v5.previous.json is this kind of backup, not a unique model.
- **Recommendation:** **RETIRE**

#### valorant-tiered-elo-v6

- **Sport:** VALORANT
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled), with 2026-08-04 inactivity-decay + thin-data confidence discount at prediction time
- **Artifact path:** config/models/valorant-tiered-elo-v6.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version
- **Historically callable:** yes since 2026-08-04
- **Feature names:** point-in-time team Elo, games_played (K/confidence context), last_match_utc (inactivity decay input)
- **Training / validation / holdout period:** trained_through_utc 2026-08-04 (see artifact); qualification_override_reason states the standard walk-forward gate is not applicable to esports match-winner markets
- **Model status:** shadow_qualified
- **Qualification status:** qualification_override=true, reason: 'Esports v6 artifacts use Platt-scaled Elo; qualification gate is not applicable to esports match-winner markets.'
- **Calibration method + metrics:** Platt intercept/slope in artifact (title-specific); K chosen by minimum Brier (a proper scoring rule) as of v5, confidence_threshold chosen by units_at_minus_110 (found a genuine interior optimum ~0.03-0.05 per title in v5's re-validation)
- **Save/load support:** yes - JSON ratings dict, loaded and updated incrementally
- **PIT status:** ratings keyed by point-in-time team Elo; ratings/games_played/last_match_utc dicts preserve per-team history for the decay computation
- **Train/serve parity notes:** same NeutralElo.probability() code path for training re-validation and live serving
- **Known defects:** v6's own honest trade-off, disclosed in config: shrinking confidence on thin-data matchups cost some locked-test accuracy for LOL/DOTA2 (70.6%->69.2%, 68.1%->64.8% respectively) while CS2 improved slightly (65.8%->66.0%) -- not every title showed the same trade-off direction.
- **Notes:** research_confidence_gate values are title-specific and re-derived each version; RAINBOW_SIX is functionally identical in architecture to the four promoted titles but withheld from shadow_qualified pending a deliberate per-title review.
- **Recommendation:** **KEEP_PRIMARY**

### RAINBOW_SIX

#### rainbow_six-neutral-series-elo-v1

- **Sport:** RAINBOW_SIX
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/rainbow_six-neutral-series-elo-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (earliest esports capture)
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False (research state throughout)
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Earliest esports model generation, before the tiered-elo naming/schema was introduced.
- **Recommendation:** **RETIRE**

#### rainbow_six-neutral-series-elo-v2

- **Sport:** RAINBOW_SIX
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (earliest, pre-tiered)
- **Artifact path:** config/models/archive/rainbow_six-neutral-series-elo-v2.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - archived
- **Historically callable:** yes, earliest esports Elo lineage
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** archived
- **Qualification status:** qualified_for_betting=False
- **Calibration method + metrics:** none - pre-Platt schema
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only
- **Train/serve parity notes:** n/a - archived
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Same generation as v1; identical matches_sha256, distinct confidence_threshold experiment.
- **Recommendation:** **RETIRE**

#### rainbow_six-tiered-elo-v3

- **Sport:** RAINBOW_SIX
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/rainbow_six-tiered-elo-v3.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** none - v3 uses schema esports-neutral-elo-v1 (pre-Platt)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### rainbow_six-tiered-elo-v4

- **Sport:** RAINBOW_SIX
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (tiered)
- **Artifact path:** config/models/rainbow_six-tiered-elo-v4.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded
- **Historically callable:** yes, earlier tiered-elo generation
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time
- **Calibration method + metrics:** Platt intercept/slope introduced in v4 (schema esports-neutral-elo-v2)
- **Save/load support:** yes (artifact exists)
- **PIT status:** ratings dict only, no games_played/last_match_utc tracking (added in v6)
- **Train/serve parity notes:** n/a - not served
- **Known defects:** none newly found beyond obsolescence
- **Notes:** Part of the tiered-elo lineage between the archived neutral-series-elo v1/v2 and the current v6.
- **Recommendation:** **RETIRE**

#### rainbow_six-tiered-elo-v5

- **Sport:** RAINBOW_SIX
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled)
- **Artifact path:** config/models/rainbow_six-tiered-elo-v5.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - superseded by v6
- **Historically callable:** yes, was production/research before v6 (2026-08-04)
- **Feature names:** point-in-time team Elo
- **Training / validation / holdout period:** trained_through_utc per artifact (pre-2026-08-04)
- **Model status:** superseded
- **Qualification status:** qualification_override applied at the time; K selected by min Brier for the first time in v5 (replacing v4's flat-stake-P&L-optimized K which sat at the search grid's exact top for 4/5 titles -- a truncated-search/overfitting signal explicitly called out in config comments)
- **Calibration method + metrics:** Platt intercept/slope in artifact
- **Save/load support:** yes (artifact exists)
- **PIT status:** same ratings-dict pattern as v6
- **Train/serve parity notes:** n/a - not served
- **Known defects:** v4's K=96 override was flagged as likely overfit (top-of-grid for 4/5 titles); v5 fixed the selection methodology
- **Notes:** Immediate rollback target for v6 within the same family.
- **Recommendation:** **KEEP_ROLLBACK**

#### rainbow_six-tiered-elo-v5.previous

- **Sport:** RAINBOW_SIX
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (pre-write backup snapshot)
- **Artifact path:** config/models/rainbow_six-tiered-elo-v5.previous.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** no - appears to be an automatic pre-write backup, not a distinct trained model
- **Feature names:** identical schema/ratings shape to v5
- **Training / validation / holdout period:** same trained_through_utc as v5
- **Model status:** backup snapshot
- **Qualification status:** n/a
- **Calibration method + metrics:** near-identical to v5 (same platt_intercept/slope, same training_observations)
- **Save/load support:** yes (artifact exists)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** Near-duplicate of v5 with a different source_manifest_sha256 -- an artifact-write safety copy, not a separate model worth independent tracking
- **Notes:** Same pattern observed for cs2/dota2/lol/rainbow_six/valorant -- every *-v5.previous.json is this kind of backup, not a unique model.
- **Recommendation:** **RETIRE**

#### rainbow_six-tiered-elo-v6

- **Sport:** RAINBOW_SIX
- **Market capability:** best-of match/series winner
- **Model family:** neutral_series_elo (schema esports-neutral-elo-v2, Platt-scaled), with 2026-08-04 inactivity-decay + thin-data confidence discount at prediction time
- **Artifact path:** config/models/rainbow_six-tiered-elo-v6.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_research_version, not yet promoted to shadow_qualified
- **Historically callable:** yes since 2026-08-04
- **Feature names:** point-in-time team Elo, games_played (K/confidence context), last_match_utc (inactivity decay input)
- **Training / validation / holdout period:** trained_through_utc 2026-08-04 (see artifact); qualification_override_reason states the standard walk-forward gate is not applicable to esports match-winner markets
- **Model status:** research
- **Qualification status:** qualification_override=true, reason: 'Esports v6 artifacts use Platt-scaled Elo; qualification gate is not applicable to esports match-winner markets.' Explicitly NOT yet promoted via qualification_override -- config note: 'deliberate per-title operator review this title hasn't had yet.'
- **Calibration method + metrics:** Platt intercept/slope in artifact (title-specific); K chosen by minimum Brier (a proper scoring rule) as of v5, confidence_threshold chosen by units_at_minus_110 (found a genuine interior optimum ~0.03-0.05 per title in v5's re-validation)
- **Save/load support:** yes - JSON ratings dict, loaded and updated incrementally
- **PIT status:** ratings keyed by point-in-time team Elo; ratings/games_played/last_match_utc dicts preserve per-team history for the decay computation
- **Train/serve parity notes:** same NeutralElo.probability() code path for training re-validation and live serving
- **Known defects:** v6's own honest trade-off, disclosed in config: shrinking confidence on thin-data matchups cost some locked-test accuracy for LOL/DOTA2 (70.6%->69.2%, 68.1%->64.8% respectively) while CS2 improved slightly (65.8%->66.0%) -- not every title showed the same trade-off direction.
- **Notes:** research_confidence_gate values are title-specific and re-derived each version; RAINBOW_SIX is functionally identical in architecture to the four promoted titles but withheld from shadow_qualified pending a deliberate per-title review.
- **Recommendation:** **KEEP_CHALLENGER**

### KBO/NPB

#### kbo-npb-run-dist-v1 (rebuild/models/kbo_npb.py, orphaned)

- **Sport:** KBO/NPB
- **Market capability:** moneyline with tie settlement, total
- **Model family:** league-specific starter/lineup/bullpen run distribution with tie probability derived from the score distribution (not a flat Elo-gap heuristic)
- **Artifact path:** none
- **Source branch:** origin/main (current)
- **Currently callable from main:** no - sport_adapter.build_adapter routes kbo/npb through _ResearchOnlyAdapter, which has no model wiring at all
- **Historically callable:** no evidence of ever being wired
- **Feature names:** not evaluable, unwired
- **Training / validation / holdout period:** none
- **Model status:** dead code / unwired
- **Qualification status:** none
- **Calibration method + metrics:** none
- **Save/load support:** no persistence path found
- **PIT status:** not evaluable, unwired
- **Train/serve parity notes:** not evaluable, unwired
- **Known defects:** A methodologically more sophisticated design (derives tie probability from the actual score distribution) than the production kbo/npb-tie-aware-elo-v2 (flat elo_gap heuristic), but never implemented beyond dataclass stubs
- **Notes:** Same orphaned-design-sketch pattern as esports-roster-v1 and nfl-drive-v2.
- **Recommendation:** **RETIRE**

### KBO

#### kbo-tie-aware-elo-v1.previous (backup)

- **Sport:** KBO
- **Market capability:** expected moneyline settlement, tie pays 0.50
- **Model family:** tie_aware_home_elo (pre-write backup snapshot)
- **Artifact path:** config/models/kbo-tie-aware-elo-v1.previous.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** no - appears to be an automatic pre-write backup
- **Feature names:** identical shape to the active artifact
- **Training / validation / holdout period:** same as active artifact
- **Model status:** backup snapshot
- **Qualification status:** n/a
- **Calibration method + metrics:** near-identical to the active artifact (same tie_probability, same training_observations, different source_manifest_sha256)
- **Save/load support:** yes (artifact exists)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** Backup copy, not a distinct model
- **Notes:** Same backup pattern as the esports *-v5.previous.json files.
- **Recommendation:** **RETIRE**

#### kbo-tie-aware-elo-v2 (file named kbo-tie-aware-elo-v1.json)

- **Sport:** KBO
- **Market capability:** expected moneyline settlement, tie pays 0.50
- **Model family:** tie_aware_home_elo (decisive-result Elo plus empirical tie probability via elo_gap)
- **Artifact path:** config/models/kbo-tie-aware-elo-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version and active_research_version both point here
- **Historically callable:** yes
- **Feature names:** point-in-time team Elo, home field (home_advantage=20.0), league tie rate (tie_probability), margin-weighted K
- **Training / validation / holdout period:** trained_through_date per artifact, 7059 training_observations; confidence-threshold sweep learned on a validation split
- **Model status:** research (research_outputs_zero_units=true -- never sizes real units even when shadow_qualified)
- **Qualification status:** Honest disclosure in config: confidence-threshold sweep learned 0.123889 on validation (65% hit rate there) but did NOT generalize to the locked holdout -- 55.4% hit rate on 65 calls, qualified=false.
- **Calibration method + metrics:** tie_probability=0.02464938 (elo_gap method)
- **Save/load support:** yes - JSON ratings/games_played dict
- **PIT status:** point-in-time team Elo updated match-by-match
- **Train/serve parity notes:** same Elo update code path for training and live serving
- **Known defects:** FILE NAMING DEFECT: the artifact file is named *-v1.json but its own model_version field inside says v2 -- a real versioning/naming mismatch that could mislead anyone matching config pointers to file names. The confidence threshold set in config/model.yaml is explicitly disclosed as NOT supported by locked-holdout evidence (set for 'structural consistency', not because the evidence supports it).
- **Notes:** qualification_override=true with an honest, self-disclosed non-generalizing threshold -- the clearest CALIBRATE candidate in the inventory since the family/architecture is fine but the current threshold is admittedly unproven.
- **Recommendation:** **CALIBRATE**

### NPB

#### npb-tie-aware-elo-v1.previous (backup)

- **Sport:** NPB
- **Market capability:** expected moneyline settlement, tie pays 0.50
- **Model family:** tie_aware_home_elo (pre-write backup snapshot)
- **Artifact path:** config/models/npb-tie-aware-elo-v1.previous.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** no
- **Historically callable:** no - appears to be an automatic pre-write backup
- **Feature names:** identical shape to the active artifact
- **Training / validation / holdout period:** same as active artifact
- **Model status:** backup snapshot
- **Qualification status:** n/a
- **Calibration method + metrics:** near-identical to the active artifact (same tie_probability, same training_observations, different source_manifest_sha256)
- **Save/load support:** yes (artifact exists)
- **PIT status:** n/a
- **Train/serve parity notes:** n/a
- **Known defects:** Backup copy, not a distinct model
- **Notes:** Same backup pattern as the esports *-v5.previous.json files.
- **Recommendation:** **RETIRE**

#### npb-tie-aware-elo-v2 (file named npb-tie-aware-elo-v1.json)

- **Sport:** NPB
- **Market capability:** expected moneyline settlement, tie pays 0.50
- **Model family:** tie_aware_home_elo (decisive-result Elo plus empirical tie probability via elo_gap)
- **Artifact path:** config/models/npb-tie-aware-elo-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - active_production_version and active_research_version both point here
- **Historically callable:** yes
- **Feature names:** point-in-time team Elo, home field (home_advantage=20.0), league tie rate (tie_probability), margin-weighted K
- **Training / validation / holdout period:** trained_through_date per artifact, 3943 training_observations; confidence-threshold sweep learned on a validation split
- **Model status:** research (research_outputs_zero_units=true -- never sizes real units even when shadow_qualified)
- **Qualification status:** Honest disclosure in config: confidence-threshold sweep learned 0.090624 on validation (65% hit rate there) but did NOT generalize to the locked holdout -- 55.7% hit rate on 176 calls, qualified=false, one non-positive month.
- **Calibration method + metrics:** tie_probability=0.02434694 (elo_gap method)
- **Save/load support:** yes - JSON ratings/games_played dict
- **PIT status:** point-in-time team Elo updated match-by-match
- **Train/serve parity notes:** same Elo update code path for training and live serving
- **Known defects:** FILE NAMING DEFECT: the artifact file is named *-v1.json but its own model_version field inside says v2 -- a real versioning/naming mismatch that could mislead anyone matching config pointers to file names. The confidence threshold set in config/model.yaml is explicitly disclosed as NOT supported by locked-holdout evidence (set for 'structural consistency', not because the evidence supports it).
- **Notes:** qualification_override=true with an honest, self-disclosed non-generalizing threshold -- the clearest CALIBRATE candidate in the inventory since the family/architecture is fine but the current threshold is admittedly unproven.
- **Recommendation:** **CALIBRATE**

### ALL (cross-sport)

#### market-residual-logistic-v1-identity-fallback

- **Sport:** ALL (cross-sport)
- **Market capability:** probability calibration layer combining model_p and market_p (the only place market prices may touch model output)
- **Model family:** logistic_regression on [logit(model_p), logit(market_p)], rolling 90-day window
- **Artifact path:** config/models/market-residual-v1.json
- **Source branch:** origin/main (current)
- **Currently callable from main:** yes - models/market_residual.py, config's market_residual key
- **Historically callable:** yes
- **Feature names:** logit(model_probability), logit(market_probability)
- **Training / validation / holdout period:** rolling 90-day window, minimum_sample=100 settled binary outcomes required to fit
- **Model status:** active, currently in identity fallback
- **Qualification status:** sample_size=51 in the current artifact, below the minimum_sample=100 threshold -- coefficients=None, so the model reports identity (pass-through) explicitly rather than guessing
- **Calibration method + metrics:** none yet - insufficient sample; will self-upgrade to a fit logistic model once 100+ settled outcomes accumulate
- **Save/load support:** yes - JSON with SHA-256 hash so a decision row can pin the exact residual version
- **PIT status:** n/a - trained on settled (fully resolved) outcomes only
- **Train/serve parity notes:** explicit fail-closed design: falls back to identity when sample is too small, reporting that status rather than silently using an unreliable fit
- **Known defects:** none - this is working as designed (fail-closed identity fallback), not a defect
- **Notes:** A cross-cutting layer, not sport-specific -- the ONLY place model probability and market probability may combine per this file's own docstring.
- **Recommendation:** **KEEP_BASELINE**
