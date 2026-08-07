# Foundation Inventory

Generated from code at commit `001228c2b3ab527630f152f4743819127ec6f9b6` on branch `rebuild/clean-slate-v1`.
Python: Python 3.14.5. Total tests passing: 870.

## Capability status

| Capability | Status |
|---|---|
| `raw_storage_content_addressed` | VERIFIED |
| `normalized_storage_idempotent` | NOT_STARTED |
| `point_in_time_join_utility` | PARTIAL |
| `mlb_feature_builders_own_pit_logic` | OPERATIONAL |
| `canonical_identity_registry` | INTERFACE_ONLY |
| `mlb_starter_name_to_id_resolution` | OPERATIONAL |
| `horizon_orchestration` | INTERFACE_ONLY |
| `mlb_market_event_isolation` | VERIFIED |
| `mlb_market_period_disambiguation_f5` | VERIFIED |
| `other_sport_market_matching` | NOT_STARTED |
| `winner_first_decision_engine` | VERIFIED |
| `spread_cover_probability` | VERIFIED |
| `evaluated_market_audit_trail` | VERIFIED |
| `conservative_probability_bootstrap_uncertainty` | VERIFIED |
| `real_quote_depth` | NOT_STARTED |
| `real_quote_age` | VERIFIED |
| `order_book_walking` | NOT_STARTED |
| `mlb_two_head_model_real_features` | OPERATIONAL |
| `train_calib_test_split_independence` | VERIFIED |
| `mlb_predictive_qualification` | NOT_STARTED |
| `sqlite_shadow_ledger` | VERIFIED |
| `rerun_idempotency` | VERIFIED |
| `one_command_mlb_shadow_run` | OPERATIONAL |
| `multi_sport_shared_cli` | NOT_STARTED |
| `nba_foundation_gate` | NOT_STARTED |
| `wnba_foundation_gate` | NOT_STARTED |
| `nfl_foundation_gate` | NOT_STARTED |
| `soccer_foundation_gate` | NOT_STARTED |
| `tennis_foundation_gate` | NOT_STARTED |
| `esports_foundation_gate` | NOT_STARTED |
| `kbo_foundation_gate` | NOT_STARTED |
| `npb_foundation_gate` | NOT_STARTED |
| `ci_runtime_matches_pyproject` | VERIFIED |
| `ci_attached_to_current_head` | UNVERIFIED |

## Known blockers

- NormalizedStore.write() still unconditionally concatenates — no real primary-key idempotency at the storage layer (consumer-side dedupe_scoreboard() is a workaround for MLB only)
- point_in_time_join() is now correct and tested but is dead code — the real MLB pipeline uses its own point-in-time filtering in mlb_features.py instead of this shared utility
- Real order-book depth is unavailable from the current Polymarket source — available_depth remains a fabricated 999.0 placeholder in mlb_market_matching.py (quote_age_seconds itself is real)
- 8 of the shadow ledger's 16 required tables (raw_snapshots, normalized_observations, feature_snapshots, dataset_manifests, model_artifacts, calibration_artifacts, closing_prices, reviews) are schema-only — no insert/query methods, nothing writes to them yet
- conservative_probability implements bootstrap_uncertainty only -- CLAUDE.md's full spec also requires calibration_uncertainty, lineup_uncertainty, missingness_penalty, and model_disagreement (the last requires multiple independently-trained model families, which don't exist yet -- only one model architecture is trained)
- Real bootstrap bounds are wide given only 126 real training games (e.g. a 0.49 point estimate with a real [0.27, 0.67] bound) -- this correctly makes almost every market fail the edge-after-costs gate, which is honest behavior given genuine data scarcity, not a bug, and reinforces backfill volume as the real bottleneck
- No CI run status verified for the current head — no gh CLI auth in this session
- 8 sports (NBA/WNBA/NFL/soccer/tennis/esports/KBO/NPB) have zero foundation-gate items complete — correctly out of scope until MLB clears its own gate per CLAUDE.md's own sequencing
- MLB model held-out evaluation remains genuinely inconclusive on ~20-25 games — more real backfill days is the only way to resolve this, not further feature engineering
