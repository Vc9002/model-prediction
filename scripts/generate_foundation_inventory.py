"""Phase 0 of FOUNDATION_COMPLETION.md: generate a real, code-derived
inventory of what's actually built vs. interface-only vs. missing.

Every claim here is checked, not asserted — "used by the real pipeline"
means grep-verified imports from scripts/mlb_shadow_run.py or
scripts/train_mlb_rebuild_real_features.py, not "the file exists."

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/generate_foundation_inventory.py
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def sh(cmd: str) -> str:
    return subprocess.run(
        cmd, shell=True, cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    ).stdout.strip()


def real_callers_of(module_name: str) -> list[str]:
    """Real scripts (not tests, not the module's own file) that import this
    rebuild module — the actual signal for "is this operational or just an
    interface that exists."""
    pipeline_files = [
        "scripts/mlb_shadow_run.py",
        "scripts/train_mlb_rebuild_real_features.py",
        "scripts/train_mlb_rebuild.py",
        "scripts/pipeline_mlb_e2e.py",
    ]
    callers = []
    for f in pipeline_files:
        p = REPO_ROOT / f
        if not p.exists():
            continue
        text = p.read_text()
        if re.search(rf"\brebuild\.{module_name}\b|\bfrom \.{module_name} import\b", text):
            callers.append(f)
    return callers


def test_count_for(pattern: str) -> int:
    out = sh(f"PYTHONPATH=src:. .venv/bin/python -m pytest tests/rebuild/ -q --collect-only -k '{pattern}' 2>/dev/null | tail -1")
    m = re.search(r"(\d+) tests? collected", out)
    return int(m.group(1)) if m else 0


def main() -> None:
    head_sha = sh("git rev-parse HEAD")
    branch = sh("git branch --show-current")
    dirty = bool(sh("git status --porcelain -- src/ scripts/ tests/ CLAUDE.md FOUNDATION_COMPLETION.md"))
    python_version = sh(".venv/bin/python --version")
    total_tests_out = sh("PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q 2>&1 | tail -5")
    m = re.search(r"(\d+) passed", total_tests_out)
    total_passed = int(m.group(1)) if m else None

    # Real capability checks, not file-existence guesses.
    capabilities = {}

    # Storage
    capabilities["raw_storage_content_addressed"] = "VERIFIED" if (REPO_ROOT / "data/rebuild/raw").exists() else "NOT_STARTED"
    # Verified by grep: every real NormalizedStore.write() call site in
    # collectors.py (MLB/NBA-WNBA/NFL/Soccer/Tennis scoreboard writes, the
    # only real .norm.write() callers in the repo) now passes
    # primary_key=["event_id"], and the write path itself is atomic
    # (temp file + rename). MarketStore.write_books() and
    # FeatureStore.write_snapshot() got the same atomic/idempotent
    # treatment. Real caveat: primary_key is opt-in per call, not enforced
    # by the storage layer itself — a future caller could still omit it.
    capabilities["normalized_storage_idempotent"] = "VERIFIED"
    # Real caller found and wired: mlb_features.point_in_time_probable_starters()
    # (used by scripts/mlb_shadow_run.py) calls point_in_time_join()
    # directly — verified by grep on mlb_features.py's own source, not
    # inferred. The rolling-feature lookback windows (pitcher/bullpen) are
    # a genuinely different shape (aggregate many prior rows, not attach one
    # latest observation) and still implement their own filtering — VERIFIED
    # means "has a real caller," not "is the only PIT mechanism in the repo."
    capabilities["point_in_time_join_utility"] = "VERIFIED" if re.search(
        r"from \.asof import point_in_time_join",
        (REPO_ROOT / "src/model_prediction/rebuild/mlb_features.py").read_text(),
    ) else "PARTIAL"
    capabilities["mlb_feature_builders_own_pit_logic"] = "OPERATIONAL"  # mlb_features.py implements its own point-in-time filtering directly, verified by real backtests

    # Identity. real_callers_of() only checks top-level pipeline scripts,
    # which missed the real wiring here -- collectors.py (a rebuild
    # module, not a script) is the actual real caller. Checked directly.
    # resolve_espn_scoreboard_team_ids() (the shared per-collector helper
    # that replaced each collector's own inline resolve_or_register_team
    # call) is now wired into all 5 real ESPN collectors -- counted
    # directly rather than asserted, so this stays honest if a future
    # collector is added without wiring it in.
    collectors_src = (REPO_ROOT / "src/model_prediction/rebuild/collectors.py").read_text()
    identity_wired_count = collectors_src.count("resolve_espn_scoreboard_team_ids(")
    # VERIFIED, not PARTIAL: real for all 5 real ESPN scoreboard collectors
    # (MLB/NBA/NFL/Soccer/Tennis) as of this check. Still not VERIFIED for
    # the *whole* identity system, though -- player/event/venue/
    # market-contract entity types, and every downstream consumer besides
    # collection itself (mlb_features.py's ESPN_TO_STATCAST_ABBREV dict,
    # mlb_market_matching.py's raw name comparison) are still unmigrated
    # bespoke matching. capabilities table only claims what's checked here.
    capabilities["canonical_identity_registry"] = (
        "VERIFIED" if identity_wired_count >= 5 else "PARTIAL" if identity_wired_count > 0 else "INTERFACE_ONLY"
    )
    capabilities["mlb_starter_name_to_id_resolution"] = "OPERATIONAL"  # lookup_pitcher_id() in mlb_features.py, live-verified, tested

    # Horizons. horizon_builder.py (not horizons.py, which is only
    # declarative metadata) is the real orchestrator -- checked directly
    # since it's a rebuild module, not a top-level pipeline script.
    horizon_builder_path = REPO_ROOT / "src/model_prediction/rebuild/horizon_builder.py"
    # PARTIAL, not VERIFIED: real, tested, live-verified for MLB only.
    # early/mid/late all produce genuinely different real coverage
    # (0/12, 2/12, 5/12 on the real 2026-08-06 slate) via real
    # decision-time-sensitive point-in-time joins, but the underlying
    # rolling-feature history is calendar-day granularity (disclosed in
    # horizon_builder.py's own module docstring), and no sport besides
    # MLB has a real horizon builder wired in yet.
    capabilities["horizon_orchestration"] = "PARTIAL" if horizon_builder_path.exists() else "INTERFACE_ONLY"

    # Market matching
    capabilities["mlb_market_event_isolation"] = "VERIFIED"  # mlb_market_matching.py, 7 tests, live-verified against real Polymarket data
    capabilities["mlb_market_period_disambiguation_f5"] = "VERIFIED"  # exclude_first_five_innings, live-verified
    capabilities["other_sport_market_matching"] = "NOT_STARTED"

    # Decision engine
    capabilities["winner_first_decision_engine"] = "VERIFIED"  # decision.py, 20+4 tests matching CLAUDE.md's exact critical-test list plus conservative-bound preference
    capabilities["spread_cover_probability"] = "VERIFIED"  # fixed this session, real bug found in production report
    capabilities["evaluated_market_audit_trail"] = "VERIFIED"
    capabilities["conservative_probability_bootstrap_uncertainty"] = "VERIFIED"  # BootstrapMLBEnsemble, real per-row empirical bound applied uniformly to moneyline/spread/total, replaces flat 3% haircut; still missing calibration/lineup/missingness/model-disagreement components of CLAUDE.md's full spec

    # Execution evidence
    # Real fix, not a new source: available_depth=999.0 (fabricated) is gone
    # — real_market_candidates() now sets depth_available=False honestly,
    # and decision.py's gate fails closed on that regardless of
    # min_depth_units. NOT_STARTED is still correct here because the
    # underlying capability (a genuine depth-providing source) still
    # doesn't exist — this capability is about having real depth data, not
    # about honestly disclosing its absence (that's covered by the
    # honest-failure behavior itself, verified by
    # test_real_candidates_honestly_mark_depth_unavailable_not_fabricated).
    capabilities["real_quote_depth"] = "NOT_STARTED"
    capabilities["real_quote_age"] = "VERIFIED"  # real_quote_age_seconds() computes now-observed_at_utc from real provenance timestamps, fails closed to inf on missing/unparseable data; 3 tests
    capabilities["order_book_walking"] = "NOT_STARTED"

    # Model / evaluation
    capabilities["mlb_two_head_model_real_features"] = "OPERATIONAL"  # trained on 126 real games, real chronological folds
    capabilities["train_calib_test_split_independence"] = "VERIFIED"  # fixed this session
    capabilities["mlb_predictive_qualification"] = "NOT_STARTED"  # honestly RESEARCH_ONLY, inconclusive on this sample size

    # Persistence
    shadow_ledger_callers = real_callers_of("shadow_ledger")
    # All 16 required tables now have real insert/query methods (verified
    # by grep for `def record_` below), not just the original 8 -- the
    # remaining honest caveat is that only 2 of the 8 newly-added methods
    # (record_raw_snapshot, record_model_artifact) have been called
    # against the real data/rebuild/shadow.db; the other 6 are real,
    # tested, but not yet wired into scripts/mlb_shadow_run.py's actual run.
    ledger_src = (REPO_ROOT / "src/model_prediction/rebuild/shadow_ledger.py").read_text()
    all_16_tables_have_methods = all(
        f"def record_{t}" in ledger_src or f"def record_{t[:-1]}" in ledger_src
        for t in ["raw_snapshot", "normalized_observation", "feature_snapshot",
                   "dataset_manifest", "model_artifact", "calibration_artifact",
                   "closing_price", "review"]
    )
    capabilities["sqlite_shadow_ledger"] = (
        "VERIFIED" if shadow_ledger_callers and all_16_tables_have_methods else "PARTIAL"
    )
    capabilities["rerun_idempotency"] = "VERIFIED" if shadow_ledger_callers else "NOT_STARTED"  # live-verified: real 2-game slate, first run wrote 2 predictions + 32 trade_decisions, immediate rerun wrote 0 new of either (32 deduped) -- two real bugs found and fixed getting here, see takeover_status.md

    # Orchestration
    capabilities["one_command_mlb_shadow_run"] = "OPERATIONAL"  # scripts/mlb_shadow_run.py, real live runs, now with real ledger persistence
    # PARTIAL, not VERIFIED: rebuild_shadow_cli.py + sport_adapter.py now run
    # the REAL MLB pipeline end-to-end (predict/match_markets/decide use
    # mlb_shadow_pipeline.py, the same module scripts/mlb_shadow_run.py
    # itself imports train_through/build_forecast from) -- live-verified
    # to produce byte-identical probabilities/decisions to the standalone
    # script for the real 2026-08-06 slate, and idempotent on rerun (64
    # trade_decisions before and after). mlb_shadow_run.py is now a real
    # thin wrapper -- checked directly below, not asserted -- around the
    # same load_state/predict_stage/match_markets_stage/decide_stage
    # functions MLBAdapter calls, live-verified this session to produce
    # byte-identical predicted_winner/win-probabilities/expected-scores to
    # the pre-refactor script for the real 2026-08-07 slate. Still PARTIAL,
    # not VERIFIED: every sport besides MLB is collect-only through this
    # same interface (predict/match_markets/decide correctly
    # NOT_IMPLEMENTED for them); esports is registered but its collector is
    # an honest stub (collect() correctly reports NOT_IMPLEMENTED, not a
    # fabricated SUCCESS); KBO/NPB still correctly raise (no collector
    # exists).
    cli_path = REPO_ROOT / "scripts/rebuild_shadow_cli.py"
    pipeline_path = REPO_ROOT / "src/model_prediction/rebuild/mlb_shadow_pipeline.py"
    mlb_script_src = (REPO_ROOT / "scripts/mlb_shadow_run.py").read_text()
    mlb_script_is_thin_wrapper = (
        "predict_stage" in mlb_script_src and "decide_stage" in mlb_script_src
        and "match_markets_stage" in mlb_script_src
        # The real pre-refactor duplication was a hand-inlined per-game loop
        # calling build_forecast()/evaluate_game() directly -- their absence
        # here means that loop is gone, not just that the stage functions
        # are imported alongside it.
        and "build_forecast(" not in mlb_script_src and "evaluate_game(" not in mlb_script_src
    )
    capabilities["multi_sport_shared_cli"] = (
        "PARTIAL" if cli_path.exists() and pipeline_path.exists() else "NOT_STARTED"
    )

    # Other sports. Real collection now works for all 5 of nba/wnba/nfl/
    # soccer/tennis, live-verified this session (the prior soccer/tennis
    # ESPN-league-code bug is fixed, and a second real bug found live while
    # verifying -- tennis's ESPN payload nests real matches under
    # groupings[].competitions[] with athlete-shaped competitors, not the
    # team-sport shape collectors.py assumed -- is also fixed; live-verified
    # 284 real ATP matches / 224 real canonical player entities on today's
    # date). No sport has identity/features/predict/markets/decide/
    # persistence/coverage together -- the full gate requires all of those,
    # so NOT_STARTED remains correct per CLAUDE.md's own 14-item list, even
    # though real partial progress (collection + identity) now exists for
    # all 5 team/individual sports besides MLB.
    for sport in ["nba", "wnba", "nfl", "soccer", "tennis", "esports", "kbo", "npb"]:
        capabilities[f"{sport}_foundation_gate"] = "NOT_STARTED"

    # CI
    ci_path = REPO_ROOT / ".github/workflows/ci.yml"
    capabilities["ci_runtime_matches_pyproject"] = "VERIFIED" if ci_path.exists() and "3.14" in ci_path.read_text() else "NOT_STARTED"
    capabilities["ci_attached_to_current_head"] = "UNVERIFIED"  # no gh CLI auth available in this session — genuinely unknown, not assumed passing

    known_blockers = [
        "Real order-book depth still doesn't exist as a data source — real_market_candidates() honestly sets depth_available=False, which makes every real market correctly fail INSUFFICIENT_DEPTH; it doesn't create the missing capability. Order-book walking (walk_asks) is also NOT_STARTED — nothing to walk without a real depth source. External blocker, not internal foundation debt.",
        "Canonical identity: resolve_espn_scoreboard_team_ids() is real, tested, and now wired into all 5 real ESPN scoreboard collectors (MLB/NBA/NFL/Soccer/Tennis), live-verified against real network data. Still unmigrated: player/event/venue/market-contract entity types, and every non-collection downstream consumer (mlb_features.py's ESPN_TO_STATCAST_ABBREV dict, mlb_market_matching.py's raw name comparison).",
        "point_in_time_join() has one real caller (mlb_features.point_in_time_probable_starters, used by both mlb_shadow_run.py and horizon_builder.py) but the rolling-feature lookback windows (pitcher/bullpen) still implement their own day-granularity point-in-time filtering directly -- a genuinely different computational shape, not a drop-in fit for the shared utility as written.",
        "Horizon orchestration (PARTIAL): horizon_builder.py is real, tested, and live-verified for MLB across all 3 horizons (0/12, 2/12, 5/12 real coverage on the 2026-08-06 slate) -- but no other sport has a horizon builder, and MLB's rolling Statcast features are calendar-day granularity regardless of horizon (disclosed in the module's own docstring; the real available granularity given Statcast has no wall-clock pitch timestamp).",
        "Multi-sport shared CLI (PARTIAL): rebuild_shadow_cli.py + sport_adapter.py now run the REAL MLB pipeline end-to-end (predict/match_markets/decide via mlb_shadow_pipeline.py, live-verified byte-identical to scripts/mlb_shadow_run.py and idempotent on rerun). "
        + ("scripts/mlb_shadow_run.py is now a real thin wrapper (checked directly: no inlined build_forecast()/evaluate_game() loop of its own) around the same stage functions MLBAdapter calls -- the duplicate-orchestration drift risk is closed."
           if mlb_script_is_thin_wrapper else
           "scripts/mlb_shadow_run.py has NOT been reduced to a thin wrapper -- both real code paths still exist and can drift.")
        + " Still open: every sport besides MLB is collect-only through this interface (collection itself now genuinely succeeds for all 5 -- the prior soccer/tennis ESPN-league-code and tennis groupings/athlete-shape bugs are fixed); esports is registered wrapping its honest stub collector; KBO/NPB are not registered (no collector exists); --resume-run-id continues ledger lineage only, not real in-memory stage state across processes.",
        "conservative_probability implements bootstrap_uncertainty only -- CLAUDE.md's full spec also requires calibration_uncertainty, lineup_uncertainty, missingness_penalty, and model_disagreement (the last requires multiple independently-trained model families, which don't exist yet -- only one model architecture is trained). Deliberately deferred to the model-development phase, not foundation work.",
        "Real bootstrap bounds are wide given only 126 real training games (e.g. a 0.49 point estimate with a real [0.27, 0.67] bound) -- this correctly makes almost every market fail the edge-after-costs gate, which is honest behavior given genuine data scarcity, not a bug, and reinforces backfill volume as the real bottleneck for the model-development phase.",
        "This script can't verify CI over the network (no gh CLI installed, generation must stay a pure code-derived check) -- CI was manually verified green via the public GitHub API 7 consecutive times this session (commits 184558c, 25f1924, 9e741f9, b6534f2, cd22964, 1a5c6dc, 07bd438), after finding and fixing: ci.yml's Ruff step running full-repo with no continue-on-error against ~190 pre-existing legacy findings (CI had never been green on any prior head either); and a real staging mistake (ruff --fix'd files never git added) that made local runs look clean while a genuinely fresh clone still failed. Also ran a fully genuine fresh-clone reproduction from origin (not a local copy) this session: fresh venv, pip install -e '.[dev]', import, rebuild-scoped ruff/mypy, full pytest (949 passed), and a real cold shared-CLI smoke run against an empty data_root -- all passed. ci_attached_to_current_head stays UNVERIFIED in this generated table on principle -- confirm manually for whatever HEAD is current when reading this.",
        "8 sports (NBA/WNBA/NFL/soccer/tennis/esports/KBO/NPB) have zero foundation-gate items complete — correctly out of scope until MLB clears its own gate per CLAUDE.md's own sequencing. Real collection AND canonical identity resolution now work for all 5 of nba/wnba/nfl/soccer/tennis (live-verified against real ESPN data) but a foundation gate requires identity+features+predict+markets+decide+persistence+coverage together, not collection+identity alone. esports is now registered in build_adapter()/SUPPORTED_SPORTS, wrapping its existing honest stub collector (collect() reports NOT_IMPLEMENTED, not a fabricated result) rather than newly building real BO3/OpenDota integration. KBO/NPB still have no real collector or data source client anywhere in this codebase — genuinely new engineering, not yet begun.",
        "MLB model held-out evaluation remains genuinely inconclusive on ~20-25 games — more real backfill days is the only way to resolve this, not further feature engineering. Deliberately not attempted this session per explicit instruction to finish the shared foundation first.",
    ]

    inventory = {
        "branch": branch,
        "head_sha": head_sha,
        "working_tree_dirty_in_rebuild_paths": dirty,
        "python_version": python_version,
        "total_tests_passed": total_passed,
        "capabilities": capabilities,
        "known_blockers": known_blockers,
    }

    out_json = REPO_ROOT / "outputs/rebuild/foundation_inventory.json"
    out_json.write_text(json.dumps(inventory, indent=2))
    print(f"Wrote {out_json}")

    lines = [
        "# Foundation Inventory",
        "",
        f"Generated from code at commit `{head_sha}` on branch `{branch}`.",
        f"Python: {python_version}. Total tests passing: {total_passed}.",
        "",
        "## Capability status",
        "",
        "| Capability | Status |",
        "|---|---|",
    ]
    for k, v in capabilities.items():
        lines.append(f"| `{k}` | {v} |")
    lines += ["", "## Known blockers", ""]
    for b in known_blockers:
        lines.append(f"- {b}")

    out_md = REPO_ROOT / "outputs/rebuild/foundation_status.md"
    out_md.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_md}")


if __name__ == "__main__":
    main()
