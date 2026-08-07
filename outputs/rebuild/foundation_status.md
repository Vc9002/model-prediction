# Foundation Inventory

Generated from code at commit `39f3f0c36d54fcd074e538c3d54cb7e833d91b49` on branch `rebuild/clean-slate-v1`.
Python: Python 3.14.5. Total tests passing: 992.

## Capability status

| Capability | Status |
|---|---|
| `raw_storage_content_addressed` | VERIFIED |
| `normalized_storage_idempotent` | VERIFIED |
| `point_in_time_join_utility` | VERIFIED |
| `mlb_feature_builders_own_pit_logic` | OPERATIONAL |
| `canonical_identity_registry` | VERIFIED |
| `mlb_starter_name_to_id_resolution` | OPERATIONAL |
| `mlb_player_canonical_identity` | VERIFIED |
| `horizon_orchestration` | PARTIAL |
| `mlb_market_event_isolation` | VERIFIED |
| `mlb_market_period_disambiguation_f5` | VERIFIED |
| `other_sport_market_matching` | PARTIAL |
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
| `multi_sport_shared_cli` | PARTIAL |
| `basic_multisport_elo_pipeline` | PARTIAL |
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

- Real order-book depth still doesn't exist as a data source — real_market_candidates() honestly sets depth_available=False, which makes every real market correctly fail INSUFFICIENT_DEPTH; it doesn't create the missing capability. Order-book walking (walk_asks) is also NOT_STARTED — nothing to walk without a real depth source. External blocker, not internal foundation debt.
- Canonical identity: resolve_espn_scoreboard_team_ids() is real, tested, and wired into all 5 real ESPN scoreboard collectors (MLB/NBA/NFL/Soccer/Tennis), live-verified against real network data -- including a real fix for a cross-sport ESPN team-id collision found live (WNBA/MLB shared numeric ids under one unnamespaced source_id). Market-contract matching now prefers canonical team IDs too (resolve_polymarket_team_id(), team_canonical_id column) with an honest name-matching fallback. MLB player identity is real (resolve_mlbam_player_id(), 12 real starters live-verified). Still unmigrated: NBA/NFL/Soccer/Tennis player identity (no stable name->id crosswalk exists for them the way MLB has pybaseball's player register), venue identity, and event identity.
- point_in_time_join() has one real caller (mlb_features.point_in_time_probable_starters, used by both mlb_shadow_run.py and horizon_builder.py) but the rolling-feature lookback windows (pitcher/bullpen) still implement their own day-granularity point-in-time filtering directly -- a genuinely different computational shape, not a drop-in fit for the shared utility as written.
- Horizon orchestration (PARTIAL): horizon_builder.py is real, tested, and live-verified for MLB across all 3 horizons (0/12, 2/12, 5/12 real coverage on the 2026-08-06 slate) -- but no other sport has a horizon builder, and MLB's rolling Statcast features are calendar-day granularity regardless of horizon (disclosed in the module's own docstring; the real available granularity given Statcast has no wall-clock pitch timestamp).
- Multi-sport shared CLI (PARTIAL): rebuild_shadow_cli.py + sport_adapter.py now run the REAL MLB pipeline end-to-end (predict/match_markets/decide via mlb_shadow_pipeline.py, live-verified byte-identical to scripts/mlb_shadow_run.py and idempotent on rerun). scripts/mlb_shadow_run.py is now a real thin wrapper (checked directly: no inlined build_forecast()/evaluate_game() loop of its own) around the same stage functions MLBAdapter calls -- the duplicate-orchestration drift risk is closed. Still open: every sport besides MLB is collect-only through this interface (collection itself now genuinely succeeds for all 5 -- the prior soccer/tennis ESPN-league-code and tennis groupings/athlete-shape bugs are fixed); esports is registered wrapping its honest stub collector; KBO/NPB are registered as an explicit research_only decision (no real collector or data source client exists for either); --resume-run-id continues ledger lineage only, not real in-memory stage state across processes.
- conservative_probability implements bootstrap_uncertainty only -- CLAUDE.md's full spec also requires calibration_uncertainty, lineup_uncertainty, missingness_penalty, and model_disagreement (the last requires multiple independently-trained model families, which don't exist yet -- only one model architecture is trained). Deliberately deferred to the model-development phase, not foundation work.
- Real bootstrap bounds are wide given only 126 real training games (e.g. a 0.49 point estimate with a real [0.27, 0.67] bound) -- this correctly makes almost every market fail the edge-after-costs gate, which is honest behavior given genuine data scarcity, not a bug, and reinforces backfill volume as the real bottleneck for the model-development phase.
- This script can't verify CI over the network (no gh CLI installed, generation must stay a pure code-derived check) -- CI was manually verified green via the public GitHub API 7 consecutive times this session (commits 184558c, 25f1924, 9e741f9, b6534f2, cd22964, 1a5c6dc, 07bd438), after finding and fixing: ci.yml's Ruff step running full-repo with no continue-on-error against ~190 pre-existing legacy findings (CI had never been green on any prior head either); and a real staging mistake (ruff --fix'd files never git added) that made local runs look clean while a genuinely fresh clone still failed. Also ran a fully genuine fresh-clone reproduction from origin (not a local copy) this session: fresh venv, pip install -e '.[dev]', import, rebuild-scoped ruff/mypy, full pytest (949 passed), and a real cold shared-CLI smoke run against an empty data_root -- all passed. ci_attached_to_current_head stays UNVERIFIED in this generated table on principle -- confirm manually for whatever HEAD is current when reading this.
- 8 sports (NBA/WNBA/NFL/soccer/tennis/esports/KBO/NPB) have zero foundation-gate items complete — correctly out of scope until MLB clears its own gate per CLAUDE.md's own sequencing. Real collection AND canonical identity resolution now work for all 5 of nba/wnba/nfl/soccer/tennis (live-verified against real ESPN data) but a foundation gate requires identity+features+predict+markets+decide+persistence+coverage together, not collection+identity alone. esports is registered in build_adapter()/SUPPORTED_SPORTS, wrapping its existing honest stub collector (collect() reports NOT_IMPLEMENTED, not a fabricated result) rather than newly building real BO3/OpenDota integration. KBO/NPB are now an explicit registered decision, not an unregistered gap: every stage honestly reports NOT_IMPLEMENTED with qualification_status=RESEARCH_ONLY (checked directly -- the only KBO/NPB reference anywhere in this codebase is Polymarket's own market league-code mapping, not a schedule/results source) per CLAUDE.md's own instruction to restrict rather than invent MLB-equivalent features when reliable inputs don't exist.
- MLB model held-out evaluation remains genuinely inconclusive on ~20-25 games — more real backfill days is the only way to resolve this, not further feature engineering. Deliberately not attempted this session per explicit instruction to finish the shared foundation first.
