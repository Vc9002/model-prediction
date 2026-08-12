# Current System Audit — `rebuild/clean-slate-v1`

**Date:** 2026-08-05
**Branch:** `rebuild/clean-slate-v1` (`bea7e8c`, 29 commits behind `main`)
**Tests:** 755 passed, 1 skipped

---

## 1. What actually runs in `daily`

The `daily` command runs 16 stages sequentially, with parallel I/O for independent data collection:

| Step | Stage | Status | Notes |
|------|-------|--------|-------|
| 1 | MLB baseline refresh | Active | Park factors, league run rates — self-throttled to weekly |
| 2 | Polymarket slate capture | Active | BBO snapshots for all sports |
| 3 | WNBA availability capture | Active | ESPN injury reports per event |
| 4 | WNBA priors build | Active | Player availability priors |
| 5 | Soccer score collection | Active | 3-day window |
| 6 | MLB probables capture | Active | Probable starting pitchers |
| 7 | MLB availability capture | Shadow | Roster/transaction data |
| 8 | Esports ratings refresh | Active | Incremental Elo ratings |
| 9–14 | Forecast + log | Active | All sports, learned + legacy paths |
| 15 | Settle all unsettled | Active | Against ESPN results |
| 16 | Summary | Active | Daily P&L and metrics |

All stages are wrapped in try/except — a single failure does not halt the pipeline.

## 2. Dead code and shadow-only paths

### Confirmed dead code
- **`KALSHI_DEFERRED_MESSAGE`**: Imported but Kalshi integration is deferred (US residency required)
- **`sportsdataio`**: Listed as optional upgrade, never wired into active collection
- **`_forecast_mlb_totals_flat`** (line 888): Referenced by `flat-forecast` command, which is a diagnostic path

### Shadow-only (research, no real money)
- **MLB player availability features**: Captured but marked "shadow feature"
- **All esports models**: Research-only, never promoted to production
- **KBO/NPB models**: Research-only
- **Soccer/Tennis models**: Research-only
- **Rebuild MLB challenger**: `RESEARCH_ONLY` status, no ledger writes

## 3. Features calculated but not consumed

| Feature | Computed by | Consumed by | Gap |
|---------|-------------|-------------|-----|
| `bullpen_weakness_gap` | `features/bullpen.py` | MLB v8 learned LR | Was silently blank in audit trail until 2026-08-04 |
| `defensive_trend_gap` | `features/trends.py` | NBA/WNBA v4 | Was silently blank in audit trail until 2026-08-04 |
| `pitcher_era_gap` | `features/team_runs.py` | MLB v8 | Was silently blank until 2026-07-25 |
| `starter_era_gap` | `features/starter_history.py` | MLB v8 | Same recurrence, caught 2026-08-04 |
| `availability_points_gap` | `features/player_availability.py` | WNBA v4 | Populated but uncertain coverage |
| `mlb_player_availability` | `features/mlb_player_availability.py` | MLB v8 | Shadow feature, not fully wired |

**Pattern**: Multiple features were added to the codebase and artifact coefficients but never serialized to the audit trail. Model probability was unaffected (scored from features dict directly), but the audit trail was blind.

## 4. Artifact mapping per sport

| Sport | Moneyline artifact | Spread/total artifact |
|-------|-------------------|----------------------|
| MLB | `mlb-v8-learned.json` | `measured-edge-margin-v3.json` + `measured-edge-totals-v3.json` |
| NBA | `nba-v4-learned.json` | — (moneyline only) |
| WNBA | `wnba-v4-learned.json` | — (moneyline only) |
| NFL | `nfl-v1-learned.json` | — (moneyline only) |
| Soccer | `soccer-poisson-dc-v1.json` | — (moneyline + BTTS) |
| Tennis | `tennis-surface-elo-v1.json` | — (moneyline only) |
| Esports | `{title}-tiered-elo-v6.json` | — (moneyline only) |
| KBO/NPB | `{league}-tie-aware-elo-v1.json` | — (moneyline only) |

**Key finding**: Only MLB has multi-market coverage (moneyline + spread + total). Every other sport is moneyline-only.

## 5. Train-serving differences

| Area | Training | Serving | Risk |
|------|----------|---------|------|
| MLB features | Artifact feature list | `_compute_features()` in `learned_forward.py` | Missing features default to 0.0 silently |
| Elo ratings | Built from historical backfill | Incrementally updated | Train-serving gap if backfill is stale |
| Calibration | Fitted on training predictions | Applied at inference | No out-of-fold calibration; in-sample optimistic |
| WNBA availability | `build_and_save_priors()` daily | `adjust_home_probability()` in forward | Priors may be stale by game time |
| Esports ratings | `refresh_recent_matches()` daily | Frozen artifact read | Ratings silently drift if refresh fails |

## 6. Neutral-value imputation disguising missing data

- **`learned_forward.py:285`**: Missing features default to `0.0` with no flag — caller cannot distinguish "feature value is genuinely zero" from "feature was unavailable"
- **`features/base.py`**: `FeatureStore.get()` returns `None` for missing features, but callers often apply `or 0.0` without recording the missingness
- **Bullpen features**: Default to neutral (1.0 multiplier) when unavailable — this is documented but the fact of unavailability is not surfaced to the decision layer

## 7. Hardcoded thresholds and parameters

| Location | Parameter | Value | Should be |
|----------|-----------|-------|-----------|
| `units.py:20` | `min_edge` | 0.02 | Source of truth in `config/model.yaml` but hardcoded as default |
| `units.py:24` | `min_pick_units` | 1.0 | Config-driven but hardcoded as default |
| `models/soccer.py` | Home multiplier | 1.15 | Should be learned per league |
| `models/soccer.py` | Dixon-Coles rho | -0.10 | Should be learned dynamically |
| `models/tennis.py` | Surface/overall blend | 60/40 | Should be dynamically optimized |
| `eligibility.py:35` | `maximum_age_hours` | 12 | Config-driven but hardcoded default |
| `cli.py:1019` | `maximum_data_age_hours` | 12 | Config-driven but hardcoded default |

## 8. Retrospective leakage risks

- **Weather**: Current path uses realized weather, not archived forecasts. Open-Meteo archived forecast data is available but not yet wired.
- **Lineup confirmation**: Batting orders are captured but confirmation timing vs model prediction time is not enforced.
- **Closing prices**: `polymarket-clv` command captures closing prices for CLV — these are post-game and must never enter the pre-game feature pipeline.
- **WNBA priors**: `build_and_save_priors()` runs daily — the prior for today's games includes data from today, which could leak if today's games are predicted after priors are built.

## 9. Competing implementations

| Concept | Implementation 1 | Implementation 2 | Conflict |
|---------|-----------------|-------------------|----------|
| Moneyline forecast | `learned_forward.py` (learned LR) | `forward.py` (measured-edge paired models) | Both active for MLB, produce different picks |
| Edge calculation | `eligibility.py:_call_result()` (value gate) | `cli.py:_forecast_learned_sport` (value gate) | Both compute `cost_adjusted_edge` independently |
| Unit sizing | `units.py:edge_scaled_units()` | `units.py:recommend_units()` | `_call_result` uses edge-scaled; `_research` uses recommend |
| Elo ratings | `features/elo_ratings.py` (learned path) | `esports.py:NeutralElo` (esports) | Different implementations, different sports |
| Settlement | `cli.py:_settle_all_unsettled()` | `cli.py:_settle_esports_pick()` | Different settlement logic per sport |

## 10. Documentation vs code disagreements

- **`MASTER.md §DD-6`**: States CLI should be split into `cli/` package — still a 4,470-line monolith
- **`MASTER.md §DD-8`**: States thresholds should move to single config source — `UnitPolicy` still hardcodes defaults
- **Rebuild spec Part 3-H**: States "zero is the default valid size, remove mandatory minimum of 1 unit" — `min_pick_units` is still 1.0
- **`README.md`**: References models and workflows that may not reflect the current rebuild branch state

## 11. Market prices mixed with sports probabilities

- **`evaluate_gated_research_eligibility()`** (line 195): Computes `executable_edge = model_probability - implied_probability(american_odds)` — this mixes market price into the eligibility decision, but does so AFTER the sports-only model has already produced its probability. This is the gated research edge check, which is separate from the value gate.

## 12. Hypothetical vs executable outputs

| Output | Type | Issue |
|--------|------|-------|
| Research ledger picks | Paper | Sized with `edge_scaled_units()` — real size, no real money |
| Flat picks | Paper | Every game logged, no edge gate |
| Gated research | Paper | Curated subset of research |
| Main ledger picks | Paper | Only production sports, only qualified calls |
| `execute` command | Real | Hard-gated behind explicit flags + env vars + confirmation |
| `polymarket-clv` | Paper | Uses closing prices from stored snapshots |
| `-110` default odds | Hypothetical | Used when no Polymarket quote matches — explicitly marked as non-executable |

---

## Summary

The system has strong infrastructure (point-in-time checks, immutable hashes, exact market matching, audit trail) but weak forecasting models (small Elo-based feature sets, no separate market-residual model, economic qualification relies on directional accuracy and hypothetical -110 scoring).

**Most critical gaps:**
1. Rebuild collectors for non-MLB sports are stubs
2. MLB rebuild model uses only rolling scoreboard averages — needs Statcast/weather/lineup/pitcher features
3. No out-of-fold calibration — current Brier/ECE are in-sample optimistic
4. No executable order-book walks — economic evaluation is hypothetical
5. Branch is 29 commits behind main — merge conflict risk
6. Two-gate system is now enforced at all levels (eligibility, units, cli) — this was the most recent fix
