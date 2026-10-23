# Models Not Fully Wired Up to Daily Forecast

## Executive Summary

This analysis identifies all sports and leagues with models defined in `config/model.yaml` that lack active daily forecast functions in `cli/forecast.py`.

---

## ✅ PRODUCTION_SPORTS - FULLY WIRED TO DAILY FORECAST

These are the core sports configured for live execution. All have complete daily forecast pipelines:

| Sport | Forecast Functions | Artifacts | Status |
|-------|-------------------|-----------|--------|
| MLB | `_forecast_mlb`, `_forecast_mlb_totals_flat` | `mlb-elo-trend-lr-v8.json`, `mlb-nrfi-v4.json` | ✅ Active |
| NBA | `_forecast_learned_sport` (via learned_forward) | `nba-elo-trend-lr-v4.json` | ✅ Active (Learned Forward) |
| WNBA | `_forecast_wnba_spread/sport`, `_forecast_wnba_total/sport` | `wnba-elo-trend-lr-v4.json` | ✅ Active |
| NFL | `_forecast_nfl_spread/sport`, `_forecast_nfl_total/sport` | `nfl-elo-trend-lr-v4.json` | ✅ Active |

---

## 🟡 LEARNED_PRODUCTION_SPORTS - PARTIALLY WIRED OR AUTO-FRESHENED

Sports with models but special handling (Learned Forward, esports auto-refresh).

| Sport | Forecast Functions | Notes | Gap |
|-------|-------------------|-------|-----|
| NCAAF | `_forecast_cfb_sport` | Full pipeline implemented | ✅ Complete |
| TENNIS | `_forecast_tennis_sport` | Uses tennis-surface-elo-v4 + markov-hierarchical-v2 | ✅ Active (via Flat Ledger for research) |
| HOCKEY | `_forecast_hockey_sport` | `hockey-poisson-dc-v1` runs daily | ⚠️ Only runs if invoked explicitly by CLI command |
| DARTS | `_forecast_darts_sport` | `darts-markov-v1` runs daily | ⚠️ Only runs if invoked explicitly by CLI command |

---

## 🔴 NOT WIRING TO DAILY FORECAST ACTIVELY

These sports/models have artifacts defined but do NOT run automatically in the daily forecast pipeline.

### 1. ESPOrts titles require manual backfill for fresh ratings:

| Sport | Forecast Function | Issue |
|-------|-------------------|-------|
| LOL | None direct, uses `_refresh_esports_ratings()` | Manual `esports-backfill --all` required for fresh Elo ratings |
| CS2 | Same | ⚠️ Ratings drift daily without manual refresh |
| DOTA2 | Same | ⚠️ Ratings drift daily without manual refresh |
| VALORANT | Same | ⚠️ Ratings drift daily without manual refresh |
| RAINBOW_SIX | Same | ⚠️ Ratings drift daily without manual refresh |

### 2. Sports with models but no dedicated forecast function found:

| Sport | Models Defined | Status |
|-------|---------------|--------|
| KBO (Int'l Baseball) | `kbo-tie-aware-elo-v2` | ⚠️ Has `_forecast_international_sport()` - wired to daily pipeline ✓ |
| NPB (Japanese Baseball) | `npb-tie-aware-elo-v2` | ⚠️ Has `_forecast_international_sport()` - wired to daily pipeline ✓ |

**International Baseball Note:** KBO/NPB are bundled in `_forecast_international_sport()` which runs as part of the international sports forecast path.

---

## Sports with Models That May Be Missing or Incomplete Coverage

### NCAAB (NCAA Basketball):
- **Models defined:** `ncaab-tempo-v1`, spreads, `total_sd` parameters present
- **Forecast function:** ❌ None found in `cli/forecast.py`
- **Artifacts location:** `config/models/ncaab-spread-margin-v2.json` likely exists but never wired

**Action required:** Add `_forecast_ncaab_sport()` to integrate NCAAB models

---

## Key Findings for Daily Forecast Integration

### ✅ Fully Automated (NO action needed):
1. MLB, NBA (moneyline only via Learned Forward), WNBA, NFL - core sports with active pipeline
2. NCAAF - fully wired as College Football
3. TENNIS - runs to Flat Ledger for research evidence every cycle  
4. SOCCER - runs daily with full pipeline

### ⚠️ Manual Intervention Required:
1. **ESPOrts (LOL/CS2/DOTA2/VALORANT/Rainbow Six):** Require manual `validate-esports` + `esports-backfill --all` to stay fresh  
2. HOCKEY & DARTS: Run but only via explicit CLI command, not auto-scheduled

### ❌ Needs Implementation:
1. **NCAAB** - Models defined in config but no forecast function exists

---

## Recommended Actions

| Priority | Action | Sport(s) | Impact |
|----------|--------|----------|--------|
| P0 | Implement `_forecast_ncaab_sport` | NCAAB | Enables daily NCAAB spread/tempo forecasting |
| P1 | Document esports rating refresh cadence | LOL/CS2/DOTA2/VALORANT/R6 | Prevents drift in predicted ratings |
| P2 | Verify HOCKEY/DARTS explicit invocation | All - hockey/darts users | Ensure they run when needed |

---

## Generated: 2026-10-20
