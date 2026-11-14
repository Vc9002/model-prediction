# Models Not Fully Wired Up to Daily Forecast - Complete Analysis

**Generated:** 2026-10-20  
**Scope:** All sports with models in `config/model.yaml` vs daily forecast functions in `cli/forecast.py`

---

## 📊 Executive Summary Table

| Category | Count | Sports |
|----------|-------|--------|
| PRODUCTION_SPORTS (core live execution) | 4 | MLB, NBA, WNBA, NFL |
| LEARNED_PRODUCTION_SPORTS (models for learning/analysis) | 12 | +NCAAF, SOCCER, LOL, CS2, DOTA2, VALORANT, RAINBOW_6 |
| ALL_SPORTS (complete universe) | 11 | +TENNIS, HOCKEY, DARTS, NCAAB, WNCAAB |

**Key Finding:** `ALL_SPORTS` defines sports with forecast functions for **5**, but models exist in config for **8 sports**. 

---

## ✅ FULLY WIRED TO DAILY FORECAST (Active Every Run)

| Sport | LEARNED_PRODUCTION? | Forecast Functions | Daily Pipeline Status |
|-------|---------------------|--------------------|----------------------|
| MLB | ✅ Yes | `_forecast_mlb`, `_forecast_mlb_totals_flat`, `_forecast_mlb_nrfi_flat` | ✓ Active |
| WNBA | ✅ Yes | `_forecast_wnba_spread/sport`, `_forecast_wnba_total/sport` | ✓ Active |
| NBA | ✅ Yes | `_forecast_learned_sport` (Learned Forward) | ✓ Active (Moneyline only) |
| NFL | ✅ Yes | `_forecast_nfl_spread/sport`, `_forecast_nfl_total/sport` | ✓ Active |
| NCAAF | ✅ Yes | `_forecast_cfb_sport` | ✓ Active |
| SOCCER | ✅ Yes | `_forecast_soccer_sport` | ✓ Active |
| TENNIS | ✅ Yes | `_forecast_tennis_sport` | ⚠️ Runs to Flat Ledger (research/gated), Gated Research ledger separate path |
| HOCKEY | ❌ No | `_forecast_hockey_sport` | ⚠️ Not auto-scheduled, requires explicit CLI invocation |
| DARTS | ❌ No | `_forecast_darts_sport` | ⚠️ Not auto-scheduled, requires explicit CLI invocation |

---

## 🟡 PARTIALLY WIRED / SPECIAL HANDLING REQUIRED

### ESPOrts - Manual Backfill Required for Fresh Ratings

These sports have models in `config/model.yaml` with artifacts checked into `config/models/`, but ratings drift daily without manual refresh:

| Sport | Forecast Function | Daily Pipeline Status | Artifacts (v6) |
|-------|-------------------|----------------------|-----------------|
| LOL | None → uses `_refresh_esports_ratings()` | ⚠️ Manual `validate-esports` + `esports-backfill --all` required | `lol-tiered-elo-v6.json` |
| CS2 | Same as above | ⚠️ Ratings drift daily | `cs2-tiered-elo-v6.json` |
| DOTA2 | Same | ⚠️ Ratings drift daily | `dota2-tiered-elo-v6.json` |
| VALORANT | Same | ⚠️ Ratings drift daily | `valorant-tiered-elo-v6.json` |
| RAINBOW_SIX | Same | ⚠️ Ratings drift daily | `rainbow_six-tiered-elo-v6.json` |

**How it works:** These use the same Elo model family as others, but ratings are frozen in artifacts. They're auto-instantiated during forecast if matches exist, but fresh ratings require manual backfill runs outside the daily pipeline.

### INTERNATIONAL BASEBALL - Wired Together

| Sport | Forecast Function | Note |
|-------|-------------------|------|
| KBO | `_forecast_international_sport()` | Runs bundled with NPB in `DAILY_INTERNATIONAL_BASEBALL_SPORTS` constant |
| NPB | Same function | ✅ Wired, runs daily alongside KBO |

**Both KBO and NPB are wired together via `_forecast_international_sport()`. No gaps here.**

---

## ❌ MISSING FROM DAILY FORECAST PIPELINE (Needs Implementation)

### 1. NCAAB - Has Models, No Forecast Function

NCAAB has model artifacts in `config/models/`:
- `ncaab-tempo-v1`
- `ncaab-spread-margin-v2`

But **no `_forecast_ncaab_sport()` function exists** in `cli/forecast.py`.

**Action needed:** Add forecast function for NCAAB spread/tempo models (similar to WNBA/NFL structure).

### 2. WNCAAB - Not Defined in model.yaml

WNCAAB appears in `ALL_SPORTS` but **not in `PRODUCTION_SPORTS` or `LEARNED_PRODUCTION_SPORTS`**. No artifacts defined, no forecast needed.

---

## 🔍 Model Artifact Inventory (config/model.yaml)

| Sport | Models Defined ✓ | Forecast Wires → | Gap |
|-------|-----------------|------------------|-----|
| MLB | 4 artifacts (v8/v3/v2 + nrfi-v4) | ✅ Full | None |
| NBA | `nba-elo-trend-lr-v4`, v1/v3 protected | ✅ Moneyline only | ⚠️ Spread/total via learned_forward (uses shared normal approx) |
| WNBA | `wnba-elo-trend-lr-v4` + spread/total baselines | ✅ Full pipeline | None |
| NFL | `nfl-elo-trend-lr-v4`, structural v5 | ✅ Full pipeline | None |
| NCAAF | `cfb-structural-v2`, college-football-v1 | ✅ Full pipeline | None |
| SOCCER | `soccer-poisson-dc-v3`, legacy baselines | ✅ Full pipeline | None |
| TENNIS | `tennis-surface-elo-v4`, markov-hierarchical-v2 | ⚠️ Flat ledger only | 🟡 Gated/Research ledger needs clarification |
| HOCKEY | `hockey-poisson-dc-v1/v3` | ⚠️ Explicit CLI only | 🟡 Not auto-scheduled |
| DARTS | `darts-markov-v1` | ⚠️ Explicit CLI only | 🟡 Not auto-scheduled |
| LOL/CS2/DOTA2/VALORANT/R6 (Esports) | tiered-elo-v6 for each title | ❌ Auto-instantiated but ratings drift | 🔴 Manual backfill required daily |
| KBO/NPB (Intl Baseball) | `kbo-tie-aware-elo-v2`, `npb-tie-aware-elo-v2` | ✅ Wired together in `_forecast_international_sport()` | None |

---

## 🎯 Action Required Summary

| Priority | Sport(s) | Issue | Fix/Action |
|----------|----------|-------|------------|
| P0 | NCAAB | Models exist, no forecast function | Implement `_forecast_ncaab_sport()` to match NBA/NFL structure |
| P1 | ESL (LOL/CS2/DOTA2/VALORANT/R6) | Ratings drift daily without refresh | Document `validate-esports` + `esports-backfill` cadence requirement |
| P2 | TENNIS | Gated ledger path unclear | Clarify why `_forecast_tennis_sport` logs to Flat but not Main/Gated ledgers |
| P3 | HOCKEY/DARTS | Not auto-scheduled, explicit CLI only | Consider adding to daily pipeline or document invocation requirement |

---

## 📝 Summary: Sports/Leagues with Models But NOT Wire Up Every Day

**Answer:** The following sports/models are either **not scheduled for daily forecast** or require **manual intervention**:

1. **LOL/Espanol/LAN/CS2 (all tournaments):** ESL, LEC, LCS - ratings drift without manual backfill
2. **DOTA2 (TCL) / VALORANT / RAINBOW SIX:** Same as above
3. **NCAAB:** Models defined but no `_forecast_ncaab_sport()` function written
4. **HOCKEY & DARTS:** Have forecast functions (`_forecast_hockey_sport`, `_forecast_darts_sport`) but NOT auto-scheduled; require explicit CLI invocation

**These 6 sports/leagues' models have artifacts in `config/model.yaml` but don't run daily automatically through the forecast pipeline.**

---

## Generated: 2026-10-20