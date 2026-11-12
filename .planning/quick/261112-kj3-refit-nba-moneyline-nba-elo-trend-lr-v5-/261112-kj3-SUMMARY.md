---
quick_id: 261112-kj3
status: complete
date: 2026-11-12
commits:
  - 9bf925e4
  - docs commit (HEAD of quick task)
---

# Summary — refit NBA moneyline as `nba-elo-trend-lr-v5` (challenger, not promoted)

Produced through the project's own validation contract (`validate-models --sports nba
--write-artifacts` after bumping `LEARNED_ARTIFACT_VERSIONS["nba"]`), never by hand. Full
evidence: `docs/NBA_LR_V5_REFIT_2026-11-12.md`, `outputs/nba/nba_lr_v5_validation.json`.

| Item | Result |
|---|---|
| Artifact | `config/models/nba-elo-trend-lr-v5.json` (hash-verified; v4 untouched, writer overwrite-guarded) |
| Fit | 2024-01-08 → 2025-06-22 (2,104 rows); threshold on 2025-10-21 → 2026-02-22 (856); locked holdout 2026-02-23 → 2026-11-11 (636) |
| Holdout | 598 calls, 73.1% hit, Brier 0.1849 on calls, every qualifying month positive at −110 → qualified |
| Same rows, v4-as-fit | Brier 0.1892 vs v5 0.1889 — not a measurable improvement; coefficients within 1% |
| Threshold | 52.54% (v4: 54.44%) → ~5% more calls |
| Registry | new entry `nba-elo-trend-lr-v5` (shadow, historical_only, rollback v4); `challengers.NBA.moneyline` → v5 (old value `nba-structural-v5` had no entry); allowed_models/artifact_map mirrored |
| Champion | unchanged: `nba-elo-trend-lr-v4`. Promotion is an explicit `model_promotion promote` |

## Verification

- Registry loads; v5 `available: True`; champion/challenger resolve as intended.
- `test_production_registry` (entry count pin 87 → 88), `test_validation` (version pins v4 → v5),
  `test_champion_challenger` green. ruff clean.
- Canary only contract-checks shadow entries; no forecast path reads a shadow entry, so the
  daily cycle is unchanged.

## Not done / decisions left

- Promotion of v5 (operator). The refit shows the coefficients were never the defect; the
  served-model mismatch was fixed in quick-261112-11j.
- `elo_trend_defense` is the production variant by construction; this audit shows trend and
  defensive-trend terms add nothing over Elo alone for NBA (Brier 0.1889 vs 0.1886). Worth a
  separate decision on dropping them in a future version.
- HANDOFF item 3 and MODEL_REVIEW NBA addendum updated in the working tree (HANDOFF carries
  uncommitted operator edits, left for the operator's docs commit).
