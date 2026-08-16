# Rollback Plan — Rebuild Platform

**Last updated**: 2026-08-05

## Principle

Every model version is preserved alongside its predecessor. No artifact is ever
overwritten or deleted. All rollbacks are to a known, hash-verified artifact.

## Current Rollback Targets

| Sport | Production | Rollback | Rollback Hash | Reason Available |
|---|---|---|---|---|
| MLB moneyline | mlb-two-head-v1 (rebuild) | mlb-elo-trend-lr-v8 (legacy, qualified=false) | See config/models/ | Legacy benchmark |
| MLB moneyline | mlb-elo-trend-lr-v8 | mlb-elo-trend-lr-v7 | See config/models/ | v7 was previous production |
| All rebuild models | (untrained) | (not applicable) | — | No trained artifacts exist yet |

## Rollback Triggers

| Trigger | Action |
|---|---|
| Negative CLV for 30+ trades | Roll back to previous calibrator |
| Brier regression > 0.01 vs baseline | Roll back model weights to previous version |
| ECE > 0.10 on recent window | Re-fit calibrator on recent data |
| Source health: 2+ critical sources down | Freeze predictions, use last known-good model |
| Contract match failure rate > 5% | Halt paper trading, investigate gateway changes |
| Stress test failure (any scenario) | Do not promote beyond RESEARCH_ONLY |

## Rollback Procedure

1. Identify the failing condition from triggers above
2. Locate the previous artifact in config/models/challengers/ or config/models/
3. Verify artifact hash matches stored hash in metadata.db
4. Update config/model.yaml to point to rollback artifact
5. Re-run validation on the rollback artifact against recent data
6. Confirm metrics are stable before resuming shadow operations
7. Document the rollback in MASTER.md with:
   - Date, reason, triggering condition, rollback target, verification results

## Artifact Preservation

- Every artifact in config/models/ and config/models/challengers/ is immutable
- New versions are created alongside, never replacing old ones
- metadata.db tracks every model version with hash verification
- Legacy freeze: all existing artifacts on main branch are never modified

## Current State

All rebuild models are RESEARCH_ONLY. No rollback targets exist for the rebuild
platform because no models have been promoted beyond research. The legacy system
on main provides the only existing rollback targets.
