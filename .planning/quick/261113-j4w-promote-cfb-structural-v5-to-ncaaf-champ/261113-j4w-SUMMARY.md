---
status: complete
quick_id: 261113-j4w
---

# Promote cfb-structural-v5 to NCAAF champion (Main + Flat)

Operator directive 2026-11-13, executed under the Direct Model Wiring policy (ROADMAP 2C) with
`python -m model_prediction.model_promotion promote` for NCAAF moneyline, spread and total
(three active records in the runtime `promotions` table, evidence `outputs/ncaaf/cfb_v5_research.md`).
Code commit `02f45736`.

## What changed

- `cfb_model()` follows `champions.NCAAF` in `config/production.yaml` and builds the champion from
  its own hash-verified artifact (`cfb_model_for_artifact` dispatch: v4 / v5). `_forecast_cfb_sport`
  prices with the exact serving artifact it stamps on rows.
- v5 accepted by settlement replay (`cfb_replay.VERSIONS`), the Main version list, the naming
  catalog and the auto-buyer whitelist (v4 removed from the whitelist, not blacklisted).
- `production.yaml`: champions → v5, v5 entry production/historical_only with rollback v4,
  v4 → rollback. `model.yaml` NCAAF active version → v5. Artifact qualification
  `PRODUCTION_ACTIVE, qualified: false` with the evidence in its note (hash recomputed by code).

## Verification

- Tests: NCAAF, registry, naming, replay, auto-buyer, NFL v7 files green except two pre-existing
  failures unrelated to this change (`test_model_naming::test_catalog_covers_production_registry`
  missing `mlb-moneyline-v9-residual`; `test_ci_contracts` pins a stale entry count of 78).
- Live: `cfb_model()` → `CFBStructuralV5Model`; 2026-11-14 ESPN slate 167 contracts all v5;
  real `_forecast_cfb_sport` into isolated ledgers wrote 167 Flat / 19 Main rows (11 spread,
  8 moneyline; totals rejected: weather unverified for a not-yet-captured date + config gates),
  every snapshot replaying on append.

## Notes

- A concurrent session's commit `ec3210a2` committed my uncommitted `production.yaml` registry
  entry four minutes before my promote (path-scoped `git add` on a shared file). Harmless; recorded.
- Rollback: `model_promotion rollback --sport NCAAF --market <m>` ×3; serving follows the map.
- Open: Main NCAAF totals decision (both models lose at consensus asks); first live v5 settlements.
