# ARCHIVE_RECOVERY_MAP.md

Per-file recovery recommendation for every source relevant to the model-feature
reconciliation audit: current main, the "clean-slate" MLB rebuild lineage, and the five
sport-specific "historical" rebuild branches plus the MLB v2 prospective-ops fix branch.
All refs pinned by the `archive/*` tags in the task background; nothing was checked out —
every comparison below was done with `git show`/`git log`/`git diff`/`git ls-tree` against
refs from this worktree.

Disposition enum used throughout: `ALREADY_ON_MAIN`, `RECOVER`, `AUDIT_ONLY`, `SUPERSEDED`,
`DO_NOT_RECOVER`. Per the task's scope instructions, runtime data, provider plumbing, and
generated snapshots are out of scope for recovery — current main's provider/runtime infra is
canonical and wins over any historical copy — so those are marked `DO_NOT_RECOVER` even where
a historical branch's copy differs, unless a branch fixes a real, still-open defect in that
plumbing (flagged explicitly where found).

**Headline finding across all six historical branches:** every one of them diverges from
current main at essentially the same shared merge-base (`4bc607b4` / `793a3a1`, both
2026-08-09/10) and contributes only a handful of unique commits (2–12), all now either fully
merged into main (often with additional fixes main made afterward) or superseded by main's
later, more general refactors. **No historical branch contains a file that is both (a) absent
from main and (b) worth recovering as-is**, with one partial exception: the WNBA branch's
research baseline-ensemble code (`baselines.py`/`features.py`/`horizon_builder.py`), which
main's own commit history shows was a *deliberate, documented* exclusion, not an oversight —
recorded as `RECOVER` below because the exclusion decision itself, not the code's quality, is
what's open.

---

## CURRENT MAIN

Scope: `src/model_prediction/models/`, `src/model_prediction/rebuild/` (all subpackages),
`config/model.yaml`, `config/models/**`, and the CLI entry points that reach them
(`model-prediction`, `rebuild-shadow`, `rebuild-data`, `rebuild-model` per `pyproject.toml`).

| Path | Disposition | Reason |
|---|---|---|
| `src/model_prediction/models/mlb.py` (Trend Engine + Measured Edge heads) | ALREADY_ON_MAIN | Live production spread/total engine for MLB (`mlb-analyst-poisson-trend-v0.3`); canonical, no recovery action |
| `src/model_prediction/models/{nba,wnba,nfl}.py` (thin re-export wrappers) | ALREADY_ON_MAIN | Trivial wrappers around `learned_forward.py`'s shared moneyline path |
| `src/model_prediction/models/soccer.py` (`soccer-poisson-dc-v1`) | ALREADY_ON_MAIN | Live production soccer model; see `MODEL_INVENTORY.md` for its one real gap (no persisted qualification artifact) |
| `src/model_prediction/models/tennis.py` (`tennis-surface-elo-v1` wrapper) | ALREADY_ON_MAIN | Live production tennis model; same missing-persisted-artifact gap as soccer |
| `src/model_prediction/models/market_residual.py` | ALREADY_ON_MAIN | Cross-sport market-residual layer, working as designed (identity fallback below `minimum_sample`) |
| `src/model_prediction/models/learned_market.py`, `base.py`, `registry.py` | ALREADY_ON_MAIN | Shared infrastructure (hash-verified artifact loading, gated market decision, sport model registry) |
| `src/model_prediction/learned_forward.py` | ALREADY_ON_MAIN | The actual production moneyline serving path for MLB/NBA/WNBA/NFL — loads `config/models/*.json` LR coefficients, computes point-in-time features live |
| `config/model.yaml` | ALREADY_ON_MAIN | Canonical config; several self-documented honesty notes (KBO/NPB non-generalizing thresholds, soccer/tennis missing qualification artifacts, MLB v8's validation-Brier-regression override) already flag their own gaps — see `MODEL_INVENTORY.md` for each |
| `config/model.yaml`'s NBA/WNBA `protected_versions` lists | AUDIT_ONLY | References `nba-elo-trend-v1`/`wnba-elo-trend-v1` (no `-lr-`) which do not match any file under `config/models/` or `config/models/archive/` — a stale or typo'd reference worth a follow-up fix, not a recovery action |
| `config/models/mlb-elo-trend-lr-v8.json` (current primary) | ALREADY_ON_MAIN | Live; carries a self-disclosed validation-Brier regression accepted via explicit operator override — flagged in `MODEL_INVENTORY.md`, not a recovery item |
| `config/models/mlb-spread-baseline-v1.json` | AUDIT_ONLY | Orphaned per config's own P0-5 comment ("no code in src/model_prediction ever reads for MLB"); harmless dead file, not worth deleting or recovering, just worth knowing it's inert |
| `src/model_prediction/rebuild/mlb_v3/*` (9 files) | ALREADY_ON_MAIN | Confirmed byte-identical to `origin/rebuild/mlb-v3-research` for 8/9 files; `boundary.py` and the providers it depends on are main-side *fixes* over the branch (see MLB V3 section below) |
| `src/model_prediction/rebuild/mlb_v2_artifact.py`, `mlb_shadow_pipeline.py`, `xgboost_stress.py`, `models/__init__.py` (two-head/XGB-NB stack) | ALREADY_ON_MAIN | The live `mlb_moneyline_v2_frozen_v1` challenger stack — code-complete, correctly fail-closed pending manual seal; see `MODEL_INVENTORY.md` |
| `src/model_prediction/rebuild/wnba/*` (7 files: contracts/foundation/normalize/store/audit/pit/`__init__`) | ALREADY_ON_MAIN | Byte-identical to `origin/rebuild/wnba-v1`'s data-foundation layer; `wnba/cli_adapter.py` is a main-only addition wiring it into `rebuild-data` |
| `src/model_prediction/rebuild/nfl/*`, `soccer/*`, `tennis/*` (data-foundation packages) | ALREADY_ON_MAIN | Same pattern as WNBA — main has these branches' data-ingestion layer plus its own later CLI wiring and provider consolidation |
| `src/model_prediction/rebuild/providers/*` (consolidated) | ALREADY_ON_MAIN | Main's `providers/base.py` docstring explicitly states it was "consolidated from five independently-evolved per-sport copies of this same file (mlb-v3, wnba-v1, nfl-v1, soccer-v1, tennis-v1 rebuild worktrees)" — this consolidation is itself the recovery event; nothing further to pull from any branch |
| `src/model_prediction/rebuild/sport_adapter.py` (`_BasicEloAdapter`) | AUDIT_ONLY | **Live, reachable gap**: routes soccer through a binary-outcome Elo baseline in the `rebuild-shadow` CLI despite soccer being a genuine 3-way (draw-possible) sport; a fix (`_SoccerCollectionOnlyAdapter`, disabling the unsafe path) exists on `origin/rebuild/soccer-v1` but was never merged — see SOCCER HISTORICAL BRANCH section |
| `src/model_prediction/rebuild/models/{basketball,soccer,tennis,kbo_npb,esports,nfl}.py` | AUDIT_ONLY | Five/six orphaned model-definition files, none wired into `sport_adapter.build_adapter()` or `model_lifecycle.py`'s `rebuild-model` CLI (which reports `NOT_IMPLEMENTED` for every sport). `soccer.py`'s Dixon-Coles class is flagged `REPAIR_SERVING` in `MODEL_INVENTORY.md` as the one exception worth wiring up (draw-aware, data-fit constants, matches the sport's own docs' stated open task); the rest (`basketball.py`, `tennis.py`'s duplicate wrapper, `kbo_npb.py`, `esports.py`, `nfl.py`) are recommended `RETIRE` in the model inventory as unfinished design sketches |
| `src/model_prediction/rebuild/model_lifecycle.py`, `model_cli.py` | ALREADY_ON_MAIN | `rebuild-model` CLI stub — explicitly documents itself as "every sport currently reports NOT_IMPLEMENTED"; real training happens via `rebuild-shadow`'s per-sport adapters instead |
| `docs/rebuild/{ARCHITECTURE,MARKETS,OPERATIONS,VALIDATION,README,MLB_V3_DATA}.md` | ALREADY_ON_MAIN | Current, live rebuild-contract documentation; superset of every historical branch's own docs of the same name |
| `docs/model_audit/prior_evidence/*` (this audit's own prior evidence) | AUDIT_ONLY | Gathered 2026-08-05..08-10 against `rebuild/clean-slate-v1` and earlier main SHAs; spot-verified against current code in this pass (e.g. the MLB two-head model card's numbers match `config/models/challengers/mlb-two-head-real-features-v1.json` on current main byte-for-byte) — keep as supporting evidence, do not treat as more current than this document |
| `config/models/archive/*`, `config/models/challengers/*` | ALREADY_ON_MAIN | Both directories already exist on main with the exact content described in `MODEL_INVENTORY.md`; no recovery action for either |

---

## CLEAN SLATE (`origin/rebuild/clean-slate-v1`, tag `archive/model-source-clean-slate-70250b1`)

144 unique commits vs. current main (merge-base `793a3a1`, 2026-08-09). This is the branch
that produced the MLB XGBoost two-head/negative-binomial challenger stack. Verified directly
(not delegated): `git ls-tree` shows **zero files exist under `src/model_prediction/rebuild/`
on this branch that are absent from main** — the branch predates main's later WNBA/NFL/
soccer/tennis/`mlb_v3` expansion, so the branch-vs-main diff is almost entirely main having
more, not the branch having anything unique. The MLB-specific files that do differ
(`decision.py`, `mlb_features.py`, `mlb_shadow_pipeline.py`, `models/__init__.py`,
`shadow_ledger.py`, `sport_adapter.py`) were diffed function-by-function
(`grep -E "^def |^class "`): every function/class on the branch exists on main; main has
several the branch lacks (`_apply_frozen_bundle`, `_is_post_start`, an evolved
`load_resume_state` signature) — main is a strict superset, later refined by the
`fix/mlb-v2-prospective-ops` PR (see that section below).

| Path | Disposition | Reason |
|---|---|---|
| `config/models/challengers/*.json` (all 6 files) | ALREADY_ON_MAIN | Confirmed byte-identical via `git diff --quiet` for every file — the full MLB two-head/XGB-NB calibrator stack is already on main |
| `src/model_prediction/rebuild/models/__init__.py` (two-head model classes) | ALREADY_ON_MAIN | Same class set (`MLBTwoHeadModel`, `XGBoostTwoHeadModel`, `BootstrapMLBEnsemble`), no branch-unique classes found |
| `src/model_prediction/rebuild/mlb_shadow_pipeline.py`, `mlb_v2_artifact.py`, `decision.py`, `shadow_ledger.py`, `sport_adapter.py` | ALREADY_ON_MAIN | Main is a strict superset (see function-diff above); nothing to recover |
| `docs/REBUILD_PLAN.md` | AUDIT_ONLY | The founding rationale document for the whole rebuild effort ("none of the existing model structures should be treated as correct by default... frozen as benchmark controls while a separate system is rebuilt from first principles") — genuinely useful historical context for *why* this audit exists, but operationally superseded by `docs/rebuild/ARCHITECTURE.md`; recommend reading, not merging |
| `docs/AI_REBUILD_GUIDE.md` | SUPERSEDED | References stale test counts ("Should show: 699 passed") and an old absolute path; superseded by `CLAUDE.md` + `docs/rebuild/README.md` |
| `docs/CLI_TOKEN_USAGE.md` | SUPERSEDED | Early-state operational doc; its one substantive claim (CLI is entirely local, no LLM calls) is still true but better covered by current docs |
| `FOUNDATION_COMPLETION.md` | SUPERSEDED | Planning doc for foundation work that is now largely complete; superseded by `docs/rebuild/ARCHITECTURE.md` and the actual shipped per-sport foundations |
| `requirements.lock` | DO_NOT_RECOVER | Generated lockfile, runtime plumbing, out of scope |
| `scripts/train_mlb_rebuild.py`, `train_nba_rebuild.py`, `train_nfl_rebuild.py`, `train_soccer_rebuild.py`, `train_tennis_rebuild.py`, `pipeline_mlb_e2e.py` | DO_NOT_RECOVER | Early single-shot training/pipeline scripts, absent from main entirely; superseded by `scripts/train_mlb_rebuild_real_features.py`, `train_mlb_xgboost_ensemble.py`, `train_mlb_feature_ablation.py`, `train_mlb_uncertainty_demo.py` and the `rebuild-shadow` CLI |
| `scripts/generate_baseline_parquet.py` | DO_NOT_RECOVER | Absent from main; generated-data helper, out of scope |
| `scripts/generate_foundation_inventory.py` | AUDIT_ONLY | Absent from main, but its output (a code-derived, grep-verified inventory of what's actually built vs. stubbed) is exactly this audit's own methodology — the script itself isn't needed, but its "verify against real code, not doc claims" approach is worth preserving as a pattern, already reflected in `docs/model_audit/prior_evidence/foundation_inventory.json` |
| `tests/test_rebuild.py` (922 lines, monolithic) | SUPERSEDED | Absent from main; superseded by main's `tests/rebuild/` directory (67 files, per-module coverage) |
| `config/models/{cs2,dota2,lol,rainbow_six,valorant}-tiered-elo-v6.previous.json` | DO_NOT_RECOVER | One version behind main's own `.previous.json` backups for the same titles; generated snapshot, out of scope |

---

## WNBA HISTORICAL BRANCH (`origin/rebuild/wnba-v1`, tag `archive/model-source-rebuild-wnba-v1-95c7dcc2`)

12 commits vs. merge-base `4bc607b`. Partially ported to main via PR #10
(`5aea31e`, "wire WNBA data foundation into rebuild-data") — that port explicitly and
knowingly took only the data-ingestion layer and left out `baselines.py`/`features.py`/
`horizon_builder.py`. Main's own `wnba/__init__.py` docstring and `docs/rebuild/README.md`
state this exclusion was a conscious, documented decision ("feature-engineering/model-
baseline work is a separate, not-yet-made decision"), not an oversight.

| Path | Disposition | Reason |
|---|---|---|
| `src/model_prediction/rebuild/wnba/{audit,contracts,foundation,normalize,pit,store,time}.py` | ALREADY_ON_MAIN | Byte-identical to branch (PR #10) |
| `src/model_prediction/rebuild/wnba/__init__.py` | ALREADY_ON_MAIN | Main's version is the intentionally-trimmed superset; docstring documents the exclusion verbatim |
| `src/model_prediction/rebuild/wnba/cli_adapter.py` | ALREADY_ON_MAIN | Main-only addition (76 lines, PR #10); no branch equivalent, main is a superset here |
| `src/model_prediction/rebuild/wnba/baselines.py` | AUDIT_ONLY | Research-only, rights-blocked (SportsDataverse/ESPN commercial terms unresolved) box-score-form ensemble; never produced a deployable artifact — valuable as evidence of an explored-but-abandoned model family, not a recovery candidate until the rights question is resolved. Modeled in `MODEL_INVENTORY.md` as `wnba-research-baseline-ensemble (unmerged)`, recommendation `REPLACE_ONLY_IF_AUDIT_FAILS` |
| `src/model_prediction/rebuild/wnba/features.py` | RECOVER | Well-tested, PIT-safe, train/serve-parity-by-construction feature builder (`build_team_form_snapshot`); main explicitly *deferred* this decision rather than rejecting it — worth a real promotion decision, not a silent drop |
| `src/model_prediction/rebuild/wnba/horizon_builder.py` | RECOVER | Single replay/live feature-build path with fail-closed provenance and decision-cutoff stabilization; needed if `features.py` is recovered (same recovery unit) |
| `src/model_prediction/rebuild/sport_adapter.py` (`_WNBAAdapter` class) | RECOVER (partial, hand-port only) | Main routes WNBA through the generic `_BasicEloAdapter` with no `build_features` path at all for the research ensemble; the rest of `sport_adapter.py` has diverged independently on MLB resume-state signatures since the fork, so only the WNBA-specific class should be hand-ported, not the whole file |
| `tests/rebuild/test_wnba_baselines.py` | AUDIT_ONLY | Companion tests for the rights-blocked module; recover only if `baselines.py` itself is recovered |
| `tests/rebuild/test_wnba_features.py` | RECOVER | Direct unit coverage for `build_team_form_snapshot`; pairs with `features.py` |
| `tests/rebuild/test_wnba_research_guards.py` | RECOVER | Covers fail-closed provenance and a late-Eastern/UTC-midnight date edge case; pairs with `horizon_builder.py` |
| `tests/rebuild/test_sport_adapter.py` (WNBA-specific test methods, +202 lines vs. main) | RECOVER (partial) | Only the WNBA-specific methods are relevant; the file has diverged on MLB coverage since the fork, so tests must be merged individually |
| `docs/rebuild/DATA_SOURCES.md` | AUDIT_ONLY | Free/open-data licensing-gate policy doc; no equivalent exists on main (main only has the MLB-v3-scoped `MLB_V3_DATA.md`), and its content generalizes beyond WNBA — worth a documentation-team decision on whether to generalize it, not a direct code recovery |
| `docs/rebuild/README.md`, `OPERATIONS.md` (branch versions) | SUPERSEDED | Predate the multi-sport `rebuild-data` rollout and MLB v3 lane; main's versions are strictly more current |
| `docs/leagues/WNBA.md` | ALREADY_ON_MAIN | Byte-identical |
| `config/models/wnba-*.json`, `archive/wnba-elo-trend-lr-v{1,2,3}.json` | ALREADY_ON_MAIN | Byte-identical; belong to the incumbent production family, unrelated to the branch's unmerged research ensemble (which never emitted an artifact) |
| `src/model_prediction/rebuild/models/basketball.py` | ALREADY_ON_MAIN | Byte-identical; a third, separate NBA/WNBA model definition, unwired on both refs — see CURRENT MAIN section |
| `src/model_prediction/rebuild/providers/sportsdataverse.py`, `providers/base.py`, `providers/cache.py`, `data_cli.py`, `mlb_shadow_pipeline.py`, `xgboost_stress.py`, `safety.py`, `shadow_ledger.py`, `config.py` | DO_NOT_RECOVER | Generic/provider/runtime infra, out of scope; main's versions are canonical and have evolved independently since the fork |
| `pyproject.toml` (branch adds `sportsdataverse==0.0.72`) | AUDIT_ONLY | Main has `providers/sportsdataverse.py` in-tree but currently lacks this dependency declaration — a possible packaging gap worth a separate ticket, tangential to WNBA specifically |

---

## NFL HISTORICAL BRANCH (`origin/rebuild/nfl-v1`, tag `archive/model-source-rebuild-nfl-v1-ab624837`)

5 commits vs. merge-base `4bc607b`, all dated 2026-08-09. **Every substantive NFL file is
byte-identical to current main**; main is strictly ahead (it also has `nfl/cli_adapter.py`,
which the branch never got). Nothing needs recovery for NFL purposes.

| Path | Disposition | Reason |
|---|---|---|
| `src/model_prediction/rebuild/nfl/{__init__,audit,contracts,foundation,normalize,pit,store}.py` | ALREADY_ON_MAIN | Byte-identical to branch |
| `src/model_prediction/rebuild/nfl/cli_adapter.py` | ALREADY_ON_MAIN | Main-only; branch predates it, nothing to recover |
| `src/model_prediction/rebuild/models/nfl.py` (`nfl-drive-v2`) | ALREADY_ON_MAIN | Byte-identical; orphaned/unwired drive-based Monte Carlo model — flagged for audit awareness in `MODEL_INVENTORY.md` (recommendation `RETIRE`), not a recovery target |
| `src/model_prediction/rebuild/providers/{nflverse,base,cache,config}.py`, `providers/__init__.py`, `http.py` | SUPERSEDED | Part of main's 2026-08-10 five-branch provider consolidation (see CURRENT MAIN section); nothing left to recover |
| `config/models/nfl-elo-trend-lr-v4.json`, `nfl-spread-baseline-v1.json`, `nfl-total-score-ridge-v1.json`, `archive/nfl-elo-trend-lr-v{1,2,3}.json` | ALREADY_ON_MAIN | Byte-identical |
| `config/model.yaml` (NFL block), `docs/leagues/NFL.md` | ALREADY_ON_MAIN | Byte-identical |
| `tests/rebuild/test_nfl_data_foundation.py`, `test_nflverse_provider.py`, `test_nfl_collector.py`, fixtures | ALREADY_ON_MAIN | Byte-identical |
| `tests/rebuild/test_provider_http_cache.py` | SUPERSEDED | Not present under this name on main; every test in it (rights-gate, retry/403, cache dedup) exists by identical test function name inside main's consolidated `tests/rebuild/test_provider_shared.py`, which has more coverage (429 handling, frame-level rights, `SourceRightsProfile`) |
| `data/historical/nfl_games_all.jsonl` | DO_NOT_RECOVER | Runtime/generated data, out of scope; byte-identical anyway |

---

## SOCCER HISTORICAL BRANCH (`origin/rebuild/soccer-v1`, tag `archive/model-source-rebuild-soccer-v1-c53acaf1`)

3 unique commits vs. merge-base `4bc607b` (one WNBA-scope, two soccer-scope). Most of the
branch's soccer data-foundation code is already merged; main then refactored the rights-
profile plumbing into a shared cross-sport module. **Two things are genuinely unique to the
branch and absent from main**, one of which is a real, currently-reachable defect.

| Path | Disposition | Reason |
|---|---|---|
| `src/model_prediction/rebuild/soccer/{contracts,normalize,pit,rights,store,__init__}.py` | ALREADY_ON_MAIN | Byte-identical |
| `src/model_prediction/rebuild/models/soccer.py` (`soccer-dc-v2`, Dixon-Coles) | ALREADY_ON_MAIN | Byte-identical; draw-aware, data-fit Dixon-Coles class, unwired on both refs — see `MODEL_INVENTORY.md`, recommendation `REPAIR_SERVING` (the one orphaned rebuild model worth wiring up rather than retiring) |
| `src/model_prediction/rebuild/soccer/audit.py`, `foundation.py` | SUPERSEDED | Main replaced branch's per-provider rights reassembly with the consolidated `providers/rights.py` pattern; same logic, cleaner structure |
| `src/model_prediction/rebuild/providers/soccer_espn.py`, `football_data.py`, `soccer_rights.py`, `statsbomb_open.py` | SUPERSEDED | Content promoted verbatim into main's shared `providers/rights.py` (that file's own docstring: "Promoted from soccer-v1's `soccer_rights.py`") and renamed (`statsbomb_open.py` → `statsbomb.py`) |
| `docs/rebuild/SOCCER_DATA.md` | RECOVER | Rights/policy doc for soccer sources plus the explicit "soccer model stages disabled until a draw-aware 1X2 model + replay-safe PIT feature set exist" scope statement — not carried to main's `docs/rebuild/`, which has no soccer-specific data doc at all |
| `src/model_prediction/rebuild/sport_adapter.py` (`_SoccerCollectionOnlyAdapter`) | **RECOVER** | **Live, reachable defect on main**: main's `build_adapter("soccer", ...)` still returns the generic `_BasicEloAdapter`, forcing a binary-outcome Elo baseline onto a 3-way (draw-possible) sport in the `rebuild-shadow` CLI. The branch's fix makes soccer's `build_features`/`predict`/`match_markets`/`decide` stages explicitly return `STAGE_NOT_IMPLEMENTED` with reason "a draw-aware three-way model is required; binary Elo is unsafe" instead of silently producing a wrong-shaped forecast. Reachable today via `rebuild-shadow --sport soccer`. |
| `tests/rebuild/test_sport_adapter.py::test_soccer_binary_elo_path_is_disabled_for_three_way_outcomes` | RECOVER | Regression test for the fix above; would fail against main's current `sport_adapter.py` |
| `tests/rebuild/test_soccer_data_foundation.py`, `test_soccer_providers.py` | ALREADY_ON_MAIN | Only import-path renames from the consolidation refactor; same coverage exists on main |
| `src/model_prediction/models/soccer.py` (legacy, non-rebuild), `validation.py::qualify_soccer_total_model` | ALREADY_ON_MAIN | Byte-identical; the live production model, untouched by this branch |
| `config/models/soccer-elo-trend-lr-v2.json` | ALREADY_ON_MAIN | Byte-identical legacy reference artifact |
| `soccer-poisson-dc-v1` model/artifact | DO_NOT_RECOVER (nothing to recover) | Confirmed absent on both main and this branch — the branch does not close the "no qualified artifact" gap `config/model.yaml`'s own comment describes; see `MODEL_INVENTORY.md`'s known_defects for `soccer-poisson-dc-v1` |
| `src/model_prediction/rebuild/providers/base.py`, `cache.py`, `__init__.py` | DO_NOT_RECOVER | Generic plumbing, out of scope; main's versions are later/canonical |

---

## TENNIS HISTORICAL BRANCH (`origin/rebuild/tennis-v1`, tag `archive/model-source-rebuild-tennis-v1-897db058`)

3 unique commits vs. merge-base `4bc607b`. Architecturally unrelated to main's tennis work:
the branch is a fail-closed rights-policy stub for the permanently-dead Jeff Sackmann ATP/WTA
GitHub CSV repos (CC BY-NC-SA 4.0, noncommercial, never had a downloader — only synthetic-
fixture tests). Main's real `tennis/` package (TennisMyLife + ESPN ingestion) predates this
branch and is explicitly, per its own docstring, new authorship rather than a port of it. The
actual model source (`rebuild/models/tennis.py`, `surface_blended_elo`) is byte-identical on
both — nothing model-related is missing from main.

| Path | Disposition | Reason |
|---|---|---|
| `src/model_prediction/rebuild/models/tennis.py` | ALREADY_ON_MAIN | Byte-identical; this is the active `surface_blended_elo` model source |
| `src/model_prediction/rebuild/tennis/{foundation,store,cli_adapter}.py`, `providers/tennis_espn.py`, `providers/tennis_mylife.py` | ALREADY_ON_MAIN (main-only superset) | Real TennisMyLife+ESPN ingestion; no branch equivalent at all |
| `src/model_prediction/rebuild/tennis/{__init__,audit,contracts,normalize}.py` (branch versions) | SUPERSEDED | Branch versions target the dead Sackmann CSV schema; main's versions target the real, live ESPN/TennisMyLife schema — different purpose, main's is live |
| `src/model_prediction/rebuild/tennis/pit.py` (branch version) | AUDIT_ONLY | Targets a rankings table main never built (a rankings-based PIT gate was planned but the schema doesn't match anything on main) — conceptually interesting, not directly recoverable |
| `src/model_prediction/rebuild/tennis/policy.py` (`HistoricalSourcePolicy` fail-closed rights gate) | AUDIT_ONLY | No equivalent rights-policy gate exists anywhere on main. `src/model_prediction/data_sources/tennis_sackmann.py` sits unwired and **ungated** on main — exactly the risk this policy module was built to prevent, but the protection pattern was never applied there. Worth reviewing for adoption (as a pattern, not a direct code recovery given the schema mismatch) |
| `src/model_prediction/rebuild/tennis/live.py`, `snapshot.py` | DO_NOT_RECOVER | Branch-only, built for the never-approved Sackmann/generic-provider path; main's `foundation.py`/`cli_adapter.py` already handle live ESPN capture directly |
| `src/model_prediction/rebuild/sport_adapter.py` (`_TennisFoundationUnavailableAdapter`) | AUDIT_ONLY | Main never adopted this explicit fail-closed gate for tennis (main still routes tennis through `_BasicEloAdapter`+`TennisCollector`); confirmed no live violation today (`TennisCollector` only calls ESPN+Polymarket, not Sackmann), so this is a documentation/defense-in-depth gap, not an active bug — lower priority than soccer's equivalent gap since there's no live data-shape violation, just a missing explicit guard |
| `data_sources/tennis_sackmann.py` (main) | AUDIT_ONLY | Confirmed dead/orphaned on main (no import outside its own test) — a CSV loader for a source with no real data and no rights gate; candidate for either deletion or adopting `tennis/policy.py`'s pattern |
| `tests/rebuild/test_tennis_data_foundation.py` (branch version), `tests/rebuild/fixtures/tennis/synthetic_*.json` | DO_NOT_RECOVER | Tests the rights-policy/fail-closed mechanics for a source that's not portable to main's real-data schema |
| `src/model_prediction/models/tennis.py`, `features/tennis_surface.py`, `tennis_forward.py`, `docs/leagues/TENNIS.md` | ALREADY_ON_MAIN | Byte-identical (modulo one main-only comment); predate branch divergence |
| `config/model.yaml` (TENNIS section) | ALREADY_ON_MAIN | Byte-identical; the qualification-override/research_confidence_gate provenance text is unrelated to this branch |
| `pyproject.toml` (branch adds `sportsdataverse==0.0.72`) | AUDIT_ONLY | Same packaging-gap note as the WNBA branch section — main has `providers/sportsdataverse.py` in-tree without the dependency declared |
| `providers/{base,cache,config,http,sportsdataverse}.py` | DO_NOT_RECOVER | Branch versions are strict subsets of main's; out of scope regardless |

---

## MLB V3 HISTORICAL BRANCH (`origin/rebuild/mlb-v3-research`, tag `archive/model-source-rebuild-mlb-v3-research-afe14fa5`)

6 unique commits vs. merge-base `4bc607b` (one WNBA-scope, five MLB-v3-scope). This is a
**data-ingestion-only research lane — it contains no model, calibration, or training code at
all**, confirmed both by `git show --name-only` on every commit and by the branch's own
`docs/rebuild/MLB_V3_DATA.md`: "the first milestone contains data providers and PIT contracts
only; it contains no model... Deliberately not included: no MLB v3 model, calibration,
candidate, or prospective test." It is a **sibling of `clean-slate-v1`, not a sequential
phase of it** — both fork from the same base but never merge into each other; `clean-slate-v1`
(the actual XGBoost two-head/NB model work) does not contain the `mlb_v3/` subpackage at all.

| Path | Disposition | Reason |
|---|---|---|
| `src/model_prediction/rebuild/mlb_v3/{__init__,audit,contracts,coverage,foundation,normalize,pit,store}.py` (8/9 files) | ALREADY_ON_MAIN | Byte-identical |
| `src/model_prediction/rebuild/mlb_v3/boundary.py` | SUPERSEDED | Main resolves paths via `RuntimePaths.resolve()` (needed for `MODEL_PREDICTION_RUNTIME_ROOT` deployments); branch hardcodes a `repo_root/data/rebuild` join — branch version is a regression, do not recover |
| `src/model_prediction/rebuild/mlb_v3/cli_adapter.py` | ALREADY_ON_MAIN | Main-only; doesn't exist on branch |
| `src/model_prediction/rebuild/providers/mlb_stats.py` | SUPERSEDED | Branch lacks an explicit `_SCHEDULE_SCHEMA`, which is exactly the schema-drift crash risk implied by the (now-resolved) `fix/mlb-v3-schedule-schema-drift` branch name — `pl.DataFrame(rows)` with no schema crashes when an all-null-prefix column (`resumeDate`/`rescheduleDate`) later contains a real string. Main's fixed version is authoritative. |
| `src/model_prediction/rebuild/providers/statcast.py` | SUPERSEDED | Same rights.py/cache-API refactor as `mlb_stats.py` |
| `src/model_prediction/rebuild/providers/open_meteo.py` | SUPERSEDED | Main generalized this MLB-only provider to sport-neutral (typed `game_pk`, `sport` param) for NFL/soccer reuse; main's own docstring credits this branch as the origin |
| `src/model_prediction/features/starter_history.py` | DO_NOT_RECOVER | Predates this branch's fork point (inherited, unmodified by the branch); main later extended it with a FIP rolling-window feature the branch's copy lacks — branch copy is strictly older |
| `config/tested_features.json` | ALREADY_ON_MAIN | Main has newer entries (market_signals, guaranteed_signal exclusions, a 2026-08-10 `starter_era_gap_live` addendum) than the branch's stale copy — **note: this file is explicitly out of scope for edits per this audit's own instructions, listed here only for completeness of the comparison** |
| `scripts/check_rebuild_isolation.py` | ALREADY_ON_MAIN | Main is a superset of the branch's `MLB_V3_DENIED_IMPORTS`/`MLB_V3_SEALED_PATH_MARKERS` additions |
| `docs/rebuild/MLB_V3_DATA.md`, `ARCHITECTURE.md`, `OPERATIONS.md`, `README.md` | ALREADY_ON_MAIN | Byte-identical or main has strictly more detail (main's `OPERATIONS.md` documents the exact "curated, individually-reviewed transplant from archived `-research` branch" methodology that applies to this branch) |
| `tests/rebuild/test_mlb_v3_data_foundation.py` | SUPERSEDED | Main's version updated to match the refactored provider API; branch version tests pre-refactor signatures |
| `tests/rebuild/fixtures/mlb_v3/*` | ALREADY_ON_MAIN | Identical fixtures |

---

## MLB V2 PROSPECTIVE OPS (`origin/fix/mlb-v2-prospective-ops`, tag `archive/model-source-fix-mlb-v2-prospective-ops-308601f4`)

2 unique commits vs. merge-base `4bc607b`. Hardens the provenance/gating chain for
`mlb_moneyline_v2_frozen_v1` (the XGBoost two-head + negative-binomial challenger from the
`clean-slate-v1` lineage): (1) binds the temperature calibrator to the actual fitted booster's
**byte content hash**, not just schema metadata (closing a real risk of silently pairing a
calibrator with the wrong fitted model), (2) fails closed unless the external registry's
`frozen_artifact_anchor.status == "sealed"`, (3) adds a second readiness gate requiring 100
real completed games (not just committed predictions), (4) makes shadow-run resume state
self-verifying/atomic. **Both commits were squash-merged into main as `77e612d` (PR #8)**,
which additionally resolved mypy findings the raw branch commits introduced — main is a strict
superset, not just a match.

| Path | Disposition | Reason |
|---|---|---|
| `src/model_prediction/rebuild/mlb_v2_artifact.py`, `mlb_shadow_pipeline.py`, `xgboost_stress.py` | ALREADY_ON_MAIN | Squash-merged via PR #8; main adds an explicit `isinstance` guard and a `joblib` type-ignore comment beyond the raw branch commits |
| `src/model_prediction/rebuild/shadow_ledger.py`, `sport_adapter.py`, `mlb_market_matching.py`, `mlb_model_comparison.py`, `storage.py`, `asof.py`, `qualification.py`, `validation.py`, `calibration.py`, `mlb_features.py`, `models/__init__.py` | ALREADY_ON_MAIN | Byte-identical to branch |
| `scripts/check_mlb_v2_readiness.py`, `scripts/mlb_shadow_run.py` | ALREADY_ON_MAIN | Byte-identical |
| `outputs/rebuild/test_consumption_registry.json` | ALREADY_ON_MAIN | Byte-identical; current live state confirms `mlb_moneyline_v2` is still `sealing_required`, 0/100 real games — the seal is a deliberate, not-yet-performed manual operator action |
| `tests/rebuild/mlb_v2_helpers.py`, `test_mlb_shadow_pipeline.py`, `test_mlb_v2_artifact.py`, `test_mlb_v2_readiness.py`, `test_shadow_ledger.py` | ALREADY_ON_MAIN | Byte-identical |
| `config/models/challengers/mlb-xgb_two_head_negative_binomial-calibrator-v1.json` | ALREADY_ON_MAIN | Byte-identical; the exact calibrator the content-hash binding fix protects |
| `config/models/mlb-elo-trend-lr-v7.json`, `v8.json`, `src/model_prediction/models/mlb.py` | ALREADY_ON_MAIN | Byte-identical; the production serving path was never touched by this branch at all |
| `docs/mlb_trend_score_v2/MODEL_CARD.md` | ALREADY_ON_MAIN | Byte-identical; an unrelated, already-rejected "v2" naming collision — see `MODEL_INVENTORY.md`'s `mlb_trend_score_v2` record |
| `src/model_prediction/rebuild/config.py`, `safety.py` (branch versions) | DO_NOT_RECOVER | Branch versions predate `RuntimePaths`/`MODEL_PREDICTION_RUNTIME_ROOT` externalization added by a later, unrelated main-line PR (#7) — main's versions are strictly newer, not a regression to fix |
| `src/model_prediction/rebuild/data_cli.py`, `model_cli.py`, `model_lifecycle.py`, `data_foundation.py`, `mlb_v3/**` | SUPERSEDED / n/a | Don't exist on this branch at all; added later on main by unrelated PRs (#7 CLI scaffolding, #9 MLB v3 research track) — not something this branch is missing a fix for |

---

## Cross-cutting observations for whoever acts on this map

1. **Two live, reachable gaps found, both in the `rebuild-shadow` CLI's sport dispatch, not
   in any production/Main-ledger path**: soccer's binary-Elo-on-a-3-way-sport issue (fix
   exists on `origin/rebuild/soccer-v1`, unmerged) and tennis's missing explicit rights gate
   (fix exists on `origin/rebuild/tennis-v1`, unmerged, lower priority since no live data-shape
   violation was found). Both are `RECOVER`/`AUDIT_ONLY` items above, not `DO_NOT_RECOVER`,
   because they're behavioral fixes to reachable code, not just provider plumbing.
2. **The provider-plumbing consolidation is already done.** Main's `providers/base.py`
   explicitly documents having absorbed the best parts of all five sport branches' independent
   copies (mlb-v3, wnba-v1, nfl-v1, soccer-v1, tennis-v1) into one shared module — this is
   exactly the "current main's provider/runtime infra is canonical" principle the task
   instructions describe, already executed, not still pending.
3. **The WNBA research-baseline exclusion (`baselines.py`/`features.py`/`horizon_builder.py`)
   is the one genuinely open decision** among all six branches — not a bug, not stale code, but
   a real "should this rights-blocked-for-now, architecturally-different model family be
   promoted to a real artifact once SportsDataverse/ESPN rights are resolved" question that
   main's own commit history shows was deliberately deferred rather than answered.
4. Every `.previous.json` file encountered (esports v5, KBO/NPB v1) is a pre-write backup
   snapshot with a different `source_manifest_sha256` but otherwise near-identical content to
   its paired current file — not a distinct model worth independent tracking, consistently
   marked `RETIRE` in `MODEL_INVENTORY.md` rather than treated as a rollback target.
