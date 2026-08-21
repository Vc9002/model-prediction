# Feature + model deep audit — 2026-08-16

Tooling: `scripts/feature_model_audit.py` (committed on main; run from
the repo root with the runtime env). Scope: every feature (32 row
fields / 21 variant features) and all 74 JSON artifacts under
config/models/ (production, challengers, archive).

## Feature layer — final state: all 32 features covered

| Check | Result |
|---|---|
| Variant features missing from ValidationRow | 0 |
| Variant features with no serving path | 0 (was 4 — fixed) |
| Features with zero direct test coverage | 0 (was 4 — fixed) |

### Fixed during the audit

1. **`elo_neutral_probability` + `trailing_home_win_rate_30d`**
   (`elo_trend_adaptive_hfa`) had NO serving path — the variant could
   never serve. Both now compute in `learned_forward._compute_features`
   with the exact training-side definitions (same 30-day home-team
   rate as `residual_trend_gap`; raw value — the variant's
   coefficients own the gating). Parity test added
   (`test_adaptive_hfa_features_now_serve_and_match_training_definitions`).
2. **`starter_fip_gap` / `starter_kbb_gap`** had zero direct tests.
   Sign convention (home − away) now pinned with hand-computed values
   against a synthetic snapshot store
   (`test_starter_fip_and_kbb_gaps_home_minus_away_sign_convention`).
3. Schedule features (`rest_disparity`, `back_to_back_gap`,
   `games_last_7_gap`) confirmed served via the dynamic
   `matchup_schedule_load` update — the initial scan was a parser
   blind spot, not a wiring gap.

## Model layer — 74 artifacts

| Category | Result |
|---|---|
| Production models (13) | All hash-valid, wired, serving-covered (audited 08-15) |
| Challengers | 6 re-signed to the registry's canonical hash convention (were written under three different writer conventions — rebuild-LR, soccer-poisson, mlb-two-head); 8 calibrator files carry no self-hash field (different schema — documented) |
| Rebuild-family features | `elo_probability_player_one` (tennis rebuild) confirmed served by the rebuild predictor (`rebuild/tennis/rebuild_v1_predictor.py`), not learned_forward — false-positive class documented |
| Rollback pointers | `mlb-elo-trend-lr-v7` is a live `rollback_model` reference in production.yaml — kept |
| Superseded unwired artifacts | measured-edge v2 ×2, mlb-elo-trend-lr-v5, mlb-total-score-ridge-v1, mlb-v0.2-platt, production-feature-ablation report, cs2/lol v4 + lol v5 → moved to `config/models/archive/` |
| Archive integrity | `archive/wnba-spread-baseline-v1.json` hash mismatch is DELIBERATE — archived rollback evidence is never re-signed (TODO.md's own rule) |

## Final gap list

Exactly one flag remains, and it is a documented non-issue:
`config/models/archive/wnba-spread-baseline-v1.json` — broken-at-
archive-time evidence kept for rollback provenance; re-signing archived
evidence is prohibited.

## Standing notes

- Challenger artifacts now self-verify under the promotion convention
  (`production_registry.compute_artifact_hash`), so freeze/promotion
  never surprises on a convention mismatch.
- The audit script is idempotent and cheap; re-run it after any
  feature/variant addition.
