---
quick_id: 261114-hlt
status: complete
date: 2026-11-14
commit: 311b4812
---

# Summary: system_health is HEALTHY again, and means it

## What changed

- `311b4812` `src/model_prediction/system_health.py`: `_inactive_sports()` (model.yaml via
  `is_sport_daily_enabled`); `_flag_capture_staleness` / `_flag_market_staleness` take the
  inactive set and record `inactive: true, stale: false` instead of degrading; the production
  prediction-age check becomes a note when the primary's sport is inactive. Report gains
  `checks.inactive_sports` and `checks.predictions.primary_sport_inactive`.
- `tests/test_system_health.py`: `_mark_sport_off` helper plus two tests.

## Evidence

- `tests/test_system_health.py`: 22 passed.
- Live (`MODEL_PREDICTION_RUNTIME_ROOT=E:\model-prediction-runtime`): before, DEGRADED with
  7 reasons (production prediction 20034 min old; mlb/wnba game capture stale; kbo/mlb/npb/wnba
  snapshots stale). After, HEALTHY, `reasons: []`, `inactive_sports` = the 13 switched-off
  sports plus MLB and WNBA.
- The 14:00 ET daily run (first after the registry cut, commit 13:40) exited 0 on every step
  and wrote NBA 15, NCAAB 119, NCAAF 139 rows; zero out-of-scope rows today.
