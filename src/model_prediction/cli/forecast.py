"""Forecast pipeline command group.

Mechanical extraction from the former cli.py monolith (DD-6 split, stage
5). All per-sport forecast functions plus _append_secondary_ledger. This
module owns every test-patched forecast-domain name (utc_now, the
build_*_slate functions, MLBMarketOddsFeed, load_formula_spec,
_forecast_wnba_spread_slate), because monkeypatch.setattr only affects
lookups in the module where the call site lives. The logger is defined
HERE for the same BLE001-exemption reason as the other cli modules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT, config_path, ledger_path, market_odds_snapshot_path, unit_policy
from ..data_sources.espn import ESPNClient, ESPNMLBClient
from ..data_sources.mlb_market_odds import (
    MarketOddsSnapshotStore,
    MLBMarketOddsFeed,
    load_verified_mlb_market_snapshot,
)
from ..data_sources.polymarket_us import probability_to_american
from ..data_sources.the_odds_api import TheOddsAPIClient
from ..domain import (
    EASTERN,
    LEARNED_PRODUCTION_SPORTS,
    PRODUCTION_SPORTS,
    League,
    MarketType,
    ModelOrigin,
    ModelState,
    PickRequest,
    RecordType,
    iso_utc,
    parse_utc,
    utc_now,
)
from ..eligibility import evaluate_eligibility, evaluate_gated_research_eligibility
from ..entities import EntityResolutionError
from ..esports import (
    TITLE_SPECS,
    forecast_esports_slate,
    refresh_recent_matches,
    validate_all_esports_baselines,
)
from ..features.base import FeatureStore
from ..forward import build_mlb_slate
from ..international_baseball import (
    refresh_recent_international_baseball_matches,
    validate_all_international_baseball_baselines,
)
from ..learned_forward import build_learned_moneyline_slate, match_executable_quote
from ..ledger import DuplicatePickError
from ..main_ledgers import MAIN_LEDGER_SPORTS, MultiSportPickLedger
from ..market_blend import MarketBlendBlockedError, MarketBlendPolicy, canonical_config_logical_hash
from ..models.market_residual import MarketResidualModel
from ..models.mlb import canonical_mlb_artifact_hash, load_formula_spec
from ..pricing import implied_probability
from ..production_registry import ProductionModelRegistry, compute_artifact_hash
from ..research_ledgers import RESEARCH_LEDGER_SPORTS, research_ledger
from ..runtime_paths import RuntimePaths
from ..soccer_forward import build_soccer_total_slate
from ..tennis_forward import build_tennis_slate
from .commands import _clear_today_open, _research_models_dir
from .state import (
    _LEDGER_LOCK,
    DAILY_INTERNATIONAL_BASEBALL_SPORTS,
    DUAL_LEDGER_SPORTS,
    ESPORTS_TITLES,
    FLAT_LEDGER_SPORTS,
    SPORTS,
)

logger = logging.getLogger("model_prediction.cli")


def _load_exact_artifact_contract(model_version: str) -> tuple[dict[str, Any] | None, str | None]:
    """Load only the artifact whose own identity exactly matches the model.

    A similarly named artifact is not lineage.  Missing, mismatched, or
    hash-invalid files return a fail-closed reason and are never replaced by
    a placeholder hash.
    """
    artifact_path = PROJECT_ROOT / "config" / "models" / f"{model_version}.json"
    if not artifact_path.is_file():
        return None, f"exact serving artifact missing for {model_version}"
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return None, f"exact serving artifact unreadable for {model_version}: {exc}"
    if payload.get("model_version") != model_version:
        return None, f"artifact model_version does not match {model_version}"
    computed_hash = compute_artifact_hash(payload)
    if payload.get("artifact_hash") != computed_hash:
        return None, f"artifact_hash mismatch for {model_version}"
    return payload, None


def _is_registered_serving_model(model_version: str, sport: str, market: str) -> bool:
    """True only for the exact checked-in champion contract."""
    try:
        champion = ProductionModelRegistry.load(PROJECT_ROOT).champion(sport, market)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return False
    return champion is not None and champion.model_id == model_version


def _downgrade_unserved(eligibility: Any, reason_code: str = "PAPER_CALL_MODEL_UNVALIDATED") -> Any:
    return replace(
        eligibility,
        record_type=RecordType.RESEARCH_OBSERVATION,
        decision="CALL",
        reason_code=reason_code.replace("NO_CALL_", "PAPER_CALL_"),
    )


def _canonical_market_snapshot_lineage(row: dict[str, Any], archive_path: Path) -> dict[str, Any] | None:
    """Bind a parsed prospective quote to its exact archived JSON record."""
    observed_at = str(row.get("observed_at_utc") or "")
    source = str(row.get("provider") or "")
    reconstructed = row.get("reconstructed")
    prospective_marker = row.get("usage") == "prospective_executable_bbo"
    if (
        not archive_path.is_file()
        or not observed_at
        or source != "polymarket_us"
        or row.get("timestamp_valid") is not True
        or not (reconstructed is False or (reconstructed is None and prospective_marker))
    ):
        return None
    payload = {key: value for key, value in row.items() if not str(key).startswith("_")}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "market_quote_observed_at_utc": observed_at,
        "market_quote_timestamp_valid": True,
        "market_quote_source": source,
        "market_quote_provenance": "decision_time_executable_quote",
        "market_quote_reconstructed": False,
        "market_snapshot_hash": digest,
        "market_snapshot_archive_path": str(archive_path.resolve()),
        "market_snapshot_record_id": digest,
    }


def _read_polymarket_snapshot_rows(snapshot_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not snapshot_path.is_file():
        return rows
    with snapshot_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            lineage = _canonical_market_snapshot_lineage(row, snapshot_path)
            if lineage is not None:
                row["_lineage"] = lineage
            rows.append(row)
    return rows


def _forecast_mlb(args_date: str, log: bool, config, registry, bans, ledger, audit) -> dict:
    """Legacy Measured Edge research path retained as an explicit rollback."""
    spec = load_formula_spec(PROJECT_ROOT / "config/models/mlb-analyst-poisson-trend-v0.3.yaml")
    observed_at = utc_now()
    odds_api_key = os.getenv("THE_ODDS_API_KEY")
    odds_feed = MLBMarketOddsFeed(
        registry,
        MarketOddsSnapshotStore(market_odds_snapshot_path(config)),
        odds_api=TheOddsAPIClient(odds_api_key) if odds_api_key else None,
        observed_at=observed_at,
    )
    candidates, skipped, scheduled = build_mlb_slate(
        args_date,
        ESPNMLBClient(),
        spec,
        PROJECT_ROOT / "config/models/measured-edge-margin-v3.json",
        PROJECT_ROOT / "config/models/measured-edge-totals-v3.json",
        observed_at,
        odds_feed,
    )
    for item in skipped:
        if "NO_CALL_MARKET_UNAVAILABLE" in item["reason"]:
            audit.append(
                "forecast_no_call",
                item["event_id"],
                {
                    "reason_code": "NO_CALL_MARKET_UNAVAILABLE",
                    "detail": item["reason"],
                    "game_date": args_date,
                },
            )
    logged, duplicates = [], []
    if log:
        for candidate in candidates:
            request = PickRequest(
                event_start_utc=candidate.event_start_utc,
                event_id=candidate.event_id,
                league=League.MLB,
                away_team=candidate.away_team,
                home_team=candidate.home_team,
                market_type=candidate.market_type,
                selection=candidate.selection,
                line=candidate.line,
                sportsbook=candidate.sportsbook,
                american_odds=candidate.american_odds,
                model_probability=candidate.shrunk_probability,
                model_uncertainty=candidate.uncertainty,
                model_version=candidate.model_version,
                rationale=candidate.rationale,
                risks=candidate.risks,
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=ModelState.RESEARCH,
                observed_at_utc=candidate.observed_at_utc,
                model_artifact_hash=candidate.model_artifact_hash,
                calibration_method="flat_probability_shrinkage_toward_half",
                calibration_version=candidate.calibration_version,
                calibration_artifact_hash=candidate.model_artifact_hash,
                feature_schema_version=candidate.feature_schema_version,
                entity_map_version=registry.version,
                code_revision="measured-edge-paired-v1",
                decision_no_vig_probability=candidate.no_vig_probability,
            )
            request.validate(now=observed_at)
            away = registry.resolve(request.league, request.away_team, request.event_start_utc)
            home = registry.resolve(request.league, request.home_team, request.event_start_utc)
            eligibility = evaluate_eligibility(
                request,
                registry,
                bans,
                ledger.exposure(
                    request,
                    now=observed_at,
                    canonical_team_ids=(away.canonical_team_id, home.canonical_team_id),
                ),
                unit_policy(config),
                now=observed_at,
            )
            # Main holds only genuine qualified calls (see the same filter
            # in _forecast_learned_sport) -- this path's model_state is
            # hardcoded to RESEARCH, so it can never produce one anyway,
            # but a NO_CALL row here would still be pure noise in main.
            if eligibility.decision != "CALL":
                continue
            try:
                logged.append(ledger.append_evaluated(request, eligibility, now=observed_at))
            except DuplicatePickError as error:
                duplicates.append(error.pick_id)
    return {
        "sport": "mlb",
        "model_name": "Measured Edge Paired Models",
        "model_versions": ["measured-edge-margin-v3", "measured-edge-totals-v3"],
        "game_date": args_date,
        "scheduled_games": scheduled,
        "market_calls_created": len(candidates),
        "logged": len(logged),
        "duplicate_pick_ids": duplicates,
        "skipped": skipped,
        "candidates": [asdict(candidate) for candidate in candidates],
        "note": "All entries are zero-unit research; closing odds are attached only after start.",
    }


def _refresh_esports_ratings(data_root) -> dict:
    """Keep esports Elo ratings from going stale.

    forecast_esports_slate only ever reads frozen ratings out of each
    title's artifact -- nothing previously re-ran the backfill+validate
    cycle automatically, so ratings only updated when someone manually ran
    `esports-backfill --all` then `validate-esports --write-artifacts`.
    Without this, team strength silently drifts further out of date every
    day the daily pipeline runs (observed 7-9 days stale in practice).
    Uses refresh_recent_matches (a bounded, incremental merge), not
    backfill_esports (a full-history overwrite -- see its own docstring for
    why that would be unsafe to run on a schedule).
    """
    titles = tuple(TITLE_SPECS)
    backfill_results = {title: refresh_recent_matches(data_root, title) for title in titles}
    validation = validate_all_esports_baselines(data_root, titles, _research_models_dir())
    # Keep the dashboard's evidence-consistency report in sync with the
    # artifacts it describes -- otherwise it goes stale again the moment new
    # matches merge in, since it's read as a pinned snapshot elsewhere
    # (dashboard_server.production_evidence).
    report_path = PROJECT_ROOT / "outputs/latest/esports-baseline-validation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(validation, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return {"backfill": backfill_results, "validation": validation}


def _refresh_international_baseball_ratings(data_root) -> dict:
    """Keep KBO/NPB Elo ratings from going stale -- same problem, same fix
    shape as _refresh_esports_ratings above (found 2026-07-31: nothing
    equivalent existed for these two leagues; confirmed live artifacts were
    6 and 14 days stale respectively with no alert anywhere surfacing it)."""
    leagues = DAILY_INTERNATIONAL_BASEBALL_SPORTS
    backfill_results = {
        league: refresh_recent_international_baseball_matches(data_root, league) for league in leagues
    }
    validation = validate_all_international_baseball_baselines(data_root, leagues, _research_models_dir())
    report_path = PROJECT_ROOT / "outputs/latest/international-baseball-baseline-validation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(validation, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return {"backfill": backfill_results, "validation": validation}


def _forecast_mlb_totals_flat(
    args_date: str,
    log: bool,
    config,
    registry,
    bans,
    flat_ledger,
    audit,
    main_ledger=None,
    *,
    blend_policy_artifact_path: str | Path | None = None,
    blend_policy_report_path: str | Path | None = None,
    runtime_paths: RuntimePaths | None = None,
    preflight_only: bool = False,
) -> dict:
    """MLB total-runs and run-line picks (Measured Edge Monte-Carlo margin +
    totals models) into flat_picks.xlsx + main ledger. Flat logs every
    candidate (no edge gate); Main logs every candidate too (operator
    directive, 2026-08-03: MLB spread + total belong in Main alongside
    moneyline).

    Reuses build_mlb_slate's paired margin+totals output but keeps only the
    TOTAL and SPREAD candidates; MLB moneyline is already served live by
    learned_forward.py, so the moneyline third of this triple is discarded
    here rather than duplicated. The market line each candidate prices
    against is already the main/most-balanced line, not an alternate (see
    mlb_market_odds._select_full_game_market's `_market_balance`).
    """
    blend_policy: MarketBlendPolicy | None = None
    if blend_policy_artifact_path is None:
        if blend_policy_report_path is not None or runtime_paths is not None:
            raise MarketBlendBlockedError(
                "blend policy report/runtime paths require an explicit policy artifact"
            )
    else:
        if blend_policy_report_path is None:
            raise MarketBlendBlockedError("blend activation requires both policy artifact and gate report")
        runtime_paths = runtime_paths or RuntimePaths.resolve(
            repo_root=PROJECT_ROOT, require_external_runtime=True
        )
        blend_policy = MarketBlendPolicy.load(
            blend_policy_artifact_path,
            runtime_paths=runtime_paths,
            report_path=blend_policy_report_path,
        )

    spec = load_formula_spec(PROJECT_ROOT / "config/models/mlb-analyst-poisson-trend-v0.3.yaml")
    observed_at = utc_now()
    odds_api_key = os.getenv("THE_ODDS_API_KEY")
    odds_feed = MLBMarketOddsFeed(
        registry,
        MarketOddsSnapshotStore(market_odds_snapshot_path(config)),
        odds_api=TheOddsAPIClient(odds_api_key) if odds_api_key else None,
        observed_at=observed_at,
    )
    candidates, skipped, scheduled = build_mlb_slate(
        args_date,
        ESPNMLBClient(),
        spec,
        PROJECT_ROOT / "config/models/measured-edge-margin-v3.json",
        PROJECT_ROOT / "config/models/measured-edge-totals-v3.json",
        observed_at,
        odds_feed,
    )
    totals_candidates = [
        candidate
        for candidate in candidates
        if candidate.market_type in (MarketType.TOTAL, MarketType.SPREAD)
    ]
    stage1_config_path = config_path().resolve()
    try:
        stage1_config_bytes = stage1_config_path.read_bytes()
    except OSError:
        stage1_config_bytes = None
    stage1_config_byte_sha256 = (
        hashlib.sha256(stage1_config_bytes).hexdigest() if stage1_config_bytes is not None else None
    )
    stage1_config_hash = (
        canonical_config_logical_hash(stage1_config_bytes) if stage1_config_bytes is not None else None
    )
    logged, duplicates = [], []
    # DD-2 (deep debug audit, 2026-08-04): main_ledger's secondary write
    # below used to silently drop a duplicate with no trace at all --
    # `duplicates` above only ever tracked flat_ledger's own (the outer
    # except DuplicatePickError catches flat_ledger.append_evaluated, since
    # that call sits outside the suppress block).
    main_duplicates = []
    planned_rows: list[tuple[PickRequest, Any]] = []
    if log:
        for candidate in totals_candidates:
            model_artifact_path = (
                PROJECT_ROOT / "config/models/measured-edge-totals-v3.json"
                if candidate.market_type is MarketType.TOTAL
                else PROJECT_ROOT / "config/models/measured-edge-margin-v3.json"
            ).resolve()
            try:
                model_artifact_bytes = model_artifact_path.read_bytes()
            except OSError:
                model_artifact_bytes = None
            raw_model_probability = candidate.shrunk_probability
            market_probability = implied_probability(candidate.american_odds)
            if (
                candidate.market_snapshot_archive_path is not None
                and candidate.market_snapshot_record_id is not None
            ):
                try:
                    load_verified_mlb_market_snapshot(
                        archive_path=candidate.market_snapshot_archive_path,
                        record_id=candidate.market_snapshot_record_id,
                        approved_roots=(market_odds_snapshot_path(config).resolve().parent,),
                        expected_snapshot_hash=candidate.market_snapshot_hash,
                        event_id=candidate.event_id,
                        observed_at_utc=candidate.observed_at_utc,
                        provider=candidate.sportsbook,
                        market_type=candidate.market_type.value,
                        selection=candidate.selection,
                        line=candidate.line,
                        american_odds=candidate.american_odds,
                    )
                except ValueError as exc:
                    if blend_policy is not None:
                        raise MarketBlendBlockedError(
                            f"archived market snapshot verification failed: {exc}"
                        ) from exc
            serving_probability = raw_model_probability
            blend_weight = None
            blend_policy_hash = None
            blend_spec_hash = None
            if blend_policy is not None and candidate.market_type is MarketType.TOTAL:
                try:
                    snapshot_hash_valid = (
                        isinstance(candidate.market_snapshot_hash, str)
                        and len(candidate.market_snapshot_hash) == 64
                        and int(candidate.market_snapshot_hash, 16) >= 0
                    )
                except ValueError:
                    snapshot_hash_valid = False
                if not snapshot_hash_valid:
                    raise MarketBlendBlockedError(
                        "exact market snapshot SHA-256 is missing or invalid at the decision boundary"
                    )
                if (
                    candidate.market_quote_timestamp_valid is not True
                    or candidate.market_quote_source != "polymarket_us"
                    or candidate.market_quote_provenance != "decision_time_executable_quote"
                    or candidate.market_quote_reconstructed is not False
                ):
                    raise MarketBlendBlockedError(
                        "decision-time market quote provenance is not serving-qualified"
                    )
                if model_artifact_bytes is None:
                    raise MarketBlendBlockedError("measured-edge totals artifact bytes are unavailable")
                try:
                    model_artifact_raw = json.loads(model_artifact_bytes)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise MarketBlendBlockedError("measured-edge totals artifact is not valid JSON") from exc
                semantic_hash = canonical_mlb_artifact_hash(model_artifact_raw)
                if (
                    semantic_hash != candidate.model_artifact_hash
                    or model_artifact_raw.get("artifact_hash") != semantic_hash
                ):
                    raise MarketBlendBlockedError(
                        "candidate and measured-edge totals artifact identities differ"
                    )
                blend_audit = blend_policy.apply(
                    sport="mlb",
                    market="total",
                    model_probability=raw_model_probability,
                    market_probability=market_probability,
                    model_artifact_hash=semantic_hash,
                    config_hash=stage1_config_hash,
                )
                serving_probability = blend_audit.blended_probability
                blend_weight = blend_audit.weight
                blend_policy_hash = blend_audit.policy_artifact_hash
                blend_spec_hash = blend_audit.experiment_spec_hash
            request = PickRequest(
                event_start_utc=candidate.event_start_utc,
                event_id=candidate.event_id,
                league=League.MLB,
                away_team=candidate.away_team,
                home_team=candidate.home_team,
                market_type=candidate.market_type,
                selection=candidate.selection,
                line=candidate.line,
                sportsbook=candidate.sportsbook,
                american_odds=candidate.american_odds,
                model_probability=serving_probability,
                model_uncertainty=candidate.uncertainty,
                model_version=candidate.model_version,
                rationale=candidate.rationale,
                risks=candidate.risks,
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=ModelState.RESEARCH,
                observed_at_utc=candidate.observed_at_utc,
                model_artifact_hash=candidate.model_artifact_hash,
                calibration_method="flat_probability_shrinkage_toward_half",
                calibration_version=candidate.calibration_version,
                calibration_artifact_hash=candidate.model_artifact_hash,
                feature_schema_version=candidate.feature_schema_version,
                entity_map_version=registry.version,
                code_revision="measured-edge-paired-v1",
                decision_no_vig_probability=candidate.no_vig_probability,
                config_hash=stage1_config_hash,
                config_byte_sha256=stage1_config_byte_sha256,
                config_path=str(stage1_config_path),
                model_artifact_byte_sha256=(
                    hashlib.sha256(model_artifact_bytes).hexdigest()
                    if model_artifact_bytes is not None
                    else None
                ),
                model_artifact_path=str(model_artifact_path),
                market_quote_observed_at_utc=candidate.observed_at_utc,
                market_quote_timestamp_valid=candidate.market_quote_timestamp_valid,
                market_quote_source=candidate.market_quote_source,
                market_quote_provenance=candidate.market_quote_provenance,
                market_quote_reconstructed=candidate.market_quote_reconstructed,
                market_snapshot_hash=candidate.market_snapshot_hash,
                market_snapshot_archive_path=candidate.market_snapshot_archive_path,
                market_snapshot_record_id=candidate.market_snapshot_record_id,
                record_source="live_forecast",
                is_backfill=False,
                model_probability_raw=raw_model_probability,
                market_probability_at_decision=market_probability,
                serving_probability=serving_probability,
                blend_weight=blend_weight,
                blend_policy_artifact_hash=blend_policy_hash,
                blend_experiment_spec_hash=blend_spec_hash,
                blend_config_hash=(stage1_config_hash if blend_policy is not None else None),
            )
            try:
                request.validate(now=observed_at)
                away = registry.resolve(request.league, request.away_team, request.event_start_utc)
                home = registry.resolve(request.league, request.home_team, request.event_start_utc)
                # Preflight computes every decision before any ledger mutation.
                with _LEDGER_LOCK:
                    eligibility = evaluate_eligibility(
                        request,
                        registry,
                        bans,
                        flat_ledger.exposure(
                            request,
                            now=observed_at,
                            canonical_team_ids=(away.canonical_team_id, home.canonical_team_id),
                        ),
                        unit_policy(config),
                        now=observed_at,
                    )
                if not _is_registered_serving_model(
                    request.model_version,
                    request.league.value,
                    request.market_type.value,
                ):
                    eligibility = _downgrade_unserved(eligibility)
                planned_rows.append((request, eligibility))
            except (EntityResolutionError, ValueError) as error:
                if preflight_only or blend_policy is not None:
                    raise MarketBlendBlockedError(
                        f"MLB totals preflight failed for {candidate.event_id}: {error}"
                    ) from error
                skipped.append({"event_id": candidate.event_id, "reason": str(error)[:200]})
        if not preflight_only:
            for request, eligibility in planned_rows:
                try:
                    logged.append(flat_ledger.append_evaluated(request, eligibility, now=observed_at))
                    if main_ledger is not None and eligibility.decision == "CALL":
                        existing_pick_id = _append_secondary_ledger(
                            main_ledger, request, eligibility, observed_at, "mlb_totals:main_ledger"
                        )
                        if existing_pick_id is not None:
                            main_duplicates.append(existing_pick_id)
                except DuplicatePickError as error:
                    duplicates.append(error.pick_id)
    return {
        "sport": "mlb_totals",
        "model_name": "Measured Edge Totals + Spread",
        "model_versions": ["measured-edge-totals-v3", "measured-edge-margin-v3"],
        "game_date": args_date,
        "scheduled_games": scheduled,
        "market_candidates": len(totals_candidates),
        "total_candidates": sum(1 for c in totals_candidates if c.market_type is MarketType.TOTAL),
        "spread_candidates": sum(1 for c in totals_candidates if c.market_type is MarketType.SPREAD),
        "logged": len(logged),
        "logged_pick_ids": [row["pick_id"] for row in logged],
        "duplicate_pick_ids": duplicates,
        "main_ledger_duplicate_event_ids": main_duplicates,
        "skipped": skipped,
        "note": (
            "Spread/total go to Flat for research evidence. Main receives only "
            "the exact registered champion contract; unregistered workflows fail closed."
        ),
    }


def _forecast_mlb_nrfi_flat(
    args_date: str,
    log: bool,
    config,
    registry,
    bans,
    flat_ledger,
    audit,
    main_ledger=None,
    *,
    client=None,
) -> dict:
    """MLB NRFI / YRFI 1st-inning component run model into Flat and Main ledgers.

    Evaluates Poisson/Logit blended 1st-inning run probabilities for every
    scheduled MLB game on `args_date`. Generates PickRequests for NRFI (under 0.5 runs).
    Logs unconditionally to Flat Ledger, and to Main Ledger when eligible.
    """
    from ..data_sources.espn import ESPNClient
    from ..models.mlb_first_inning import FirstInningGameRow, MLBFirstInningModel
    from ..models.mlb_first_inning_live import live_first_inning_features
    from ..pricing import probability_to_american

    model_version = "mlb-nrfi-v1"
    artifact, artifact_error = _load_exact_artifact_contract(model_version)
    if artifact is None:
        return {
            "status": "blocked",
            "reason": artifact_error,
            "sport": "mlb_nrfi",
            "model_version": model_version,
            "game_date": args_date,
            "scheduled_events": 0,
            "nrfi_candidates": 0,
            "logged": 0,
            "duplicate_pick_ids": [],
            "main_duplicates": [],
        }

    espn_client = client or ESPNClient()
    try:
        scoreboard_data = espn_client.scoreboard("MLB", args_date)
        events = scoreboard_data.get("events", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch ESPN MLB scoreboard for NRFI on %s: %s", args_date, exc)
        events = []

    model = MLBFirstInningModel.from_dict(artifact)
    logged, duplicates, main_duplicates = [], [], []
    nrfi_candidates = 0
    decision_dt = utc_now()

    for event in events:
        comps = event.get("competitions", [{}])[0]
        competitors = comps.get("competitors", [])
        if len(competitors) < 2:
            continue
        home_comp = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away_comp = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home_comp or not away_comp:
            continue

        home_team = home_comp.get("team", {}).get("displayName", "")
        away_team = away_comp.get("team", {}).get("displayName", "")
        event_id = str(event.get("id", ""))
        event_start_utc = event.get("date", "")

        if not home_team or not away_team or not event_id:
            continue

        home_probables = home_comp.get("probables") or []
        away_probables = away_comp.get("probables") or []
        home_sp_name = home_probables[0].get("athlete", {}).get("displayName", "") if home_probables else ""
        away_sp_name = away_probables[0].get("athlete", {}).get("displayName", "") if away_probables else ""

        event_decision_dt = decision_dt
        if event_start_utc:
            try:
                from ..domain import parse_utc

                start_dt = parse_utc(event_start_utc)
                if event_decision_dt >= start_dt:
                    event_decision_dt = start_dt - timedelta(hours=2)
            except (ValueError, TypeError):
                pass

        venue_name = comps.get("venue", {}).get("fullName", "") or ""
        try:
            live_features = live_first_inning_features(
                home_team=home_team,
                away_team=away_team,
                venue_name=venue_name,
                home_starter_name=home_sp_name,
                away_starter_name=away_sp_name,
                decision=event_decision_dt,
            )
            row = FirstInningGameRow(
                game_pk=None,
                game_start_utc=event_decision_dt.isoformat(),
                home_team=home_team,
                away_team=away_team,
                venue_name=venue_name,
                features=live_features,
                nrfi=0,
                runs_1st_total=0.0,
            )
            p_nrfi = model.predict_p_nrfi(row)
            p_yrfi = round(1.0 - p_nrfi, 4)
            p_nrfi = round(p_nrfi, 4)
            fair_american_nrfi = probability_to_american(p_nrfi)
            fair_american_yrfi = probability_to_american(p_yrfi)
        except Exception as exc:  # noqa: BLE001
            logger.warning("NRFI prediction failed for %s @ %s: %s", away_team, home_team, exc)
            continue

        nrfi_candidates += 1

        if log:
            if p_yrfi >= 0.50:
                pick_selection = "yrfi"
                pick_prob = p_yrfi
                pick_odds = fair_american_yrfi
                pick_rationale = (
                    f"MLB 1st Inning YRFI: p={p_yrfi:.3f} (mlb-nrfi-v1, {model.fit_n_games} training games)"
                )
            else:
                pick_selection = "nrfi"
                pick_prob = p_nrfi
                pick_odds = fair_american_nrfi
                pick_rationale = (
                    f"MLB 1st Inning NRFI: p={p_nrfi:.3f} (mlb-nrfi-v1, {model.fit_n_games} training games)"
                )

            req_nrfi = PickRequest(
                event_start_utc=event_start_utc or (event_decision_dt + timedelta(hours=2)).isoformat(),
                event_id=event_id,
                league=League.MLB,
                away_team=away_team,
                home_team=home_team,
                market_type=MarketType.NRFI,
                selection=pick_selection,
                line=0.5,
                sportsbook="model_fair",
                american_odds=pick_odds,
                model_probability=pick_prob,
                model_uncertainty=0.04,
                model_version=model_version,
                rationale=pick_rationale,
                risks="1st inning variance, leadoff home run risk",
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=ModelState.SHADOW_QUALIFIED,
                observed_at_utc=event_decision_dt.isoformat(),
            )

            # Build EligibilityResult
            from ..eligibility import EligibilityResult, evaluate_eligibility
            from ..entities import CanonicalTeam
            from ..units import UnitPolicy

            away_team_obj = CanonicalTeam(
                canonical_team_id=away_team,
                league=League.MLB,
                canonical_name=away_team,
                abbreviation=away_team[:3].upper(),
                active=True,
                valid_from=None,
                valid_to=None,
                aliases=(),
            )
            home_team_obj = CanonicalTeam(
                canonical_team_id=home_team,
                league=League.MLB,
                canonical_name=home_team,
                abbreviation=home_team[:3].upper(),
                active=True,
                valid_from=None,
                valid_to=None,
                aliases=(),
            )

            exposure_source = flat_ledger or main_ledger
            if exposure_source is not None and registry is not None and bans is not None:
                try:
                    away_obj = registry.resolve(req_nrfi.league, req_nrfi.away_team, req_nrfi.event_start_utc)
                    home_obj = registry.resolve(req_nrfi.league, req_nrfi.home_team, req_nrfi.event_start_utc)
                    with _LEDGER_LOCK:
                        eligibility = evaluate_eligibility(
                            req_nrfi,
                            registry,
                            bans,
                            exposure_source.exposure(
                                req_nrfi,
                                now=event_decision_dt,
                                canonical_team_ids=(away_obj.canonical_team_id, home_obj.canonical_team_id),
                            ),
                            unit_policy(config)
                            if config
                            else UnitPolicy(min_pick_units=0.0, max_pick_units=2.0),
                            now=event_decision_dt,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Eligibility evaluation error for NRFI %s: %s", event_id, exc)
                    eligibility = EligibilityResult(
                        record_type=RecordType.RESEARCH_OBSERVATION,
                        decision="CALL",
                        reason_code="PAPER_CALL_MODEL_UNVALIDATED",
                        units=1.0,
                        confidence_score=50,
                        edge=0.05,
                        adjusted_edge=0.05,
                        away_team=away_team_obj,
                        home_team=home_team_obj,
                    )
            else:
                eligibility = EligibilityResult(
                    record_type=RecordType.RESEARCH_OBSERVATION,
                    decision="CALL",
                    reason_code="PAPER_CALL_MODEL_UNVALIDATED",
                    units=1.0,
                    confidence_score=50,
                    edge=0.05,
                    adjusted_edge=0.05,
                    away_team=away_team_obj,
                    home_team=home_team_obj,
                )

            if not _is_registered_serving_model(
                req_nrfi.model_version,
                req_nrfi.league.value,
                req_nrfi.market_type.value,
            ):
                eligibility = _downgrade_unserved(eligibility)

            # Log to Flat Ledger
            if flat_ledger is not None:
                with _LEDGER_LOCK:
                    if (
                        _append_secondary_ledger(
                            flat_ledger, req_nrfi, eligibility, event_decision_dt, "mlb_nrfi:flat_ledger"
                        )
                        is not None
                    ):
                        duplicates.append(event_id)
                    else:
                        logged.append(req_nrfi)

            # Log to Main Ledger
            if main_ledger is not None and eligibility.decision == "CALL":
                with _LEDGER_LOCK:
                    if (
                        _append_secondary_ledger(
                            main_ledger, req_nrfi, eligibility, event_decision_dt, "mlb_nrfi:main_ledger"
                        )
                        is not None
                    ):
                        main_duplicates.append(event_id)

    return {
        "status": "ok",
        "sport": "mlb_nrfi",
        "model_name": "MLB NRFI / YRFI Component Model",
        "model_version": model_version,
        "game_date": args_date,
        "scheduled_events": len(events),
        "nrfi_candidates": nrfi_candidates,
        "logged": len(logged),
        "duplicate_pick_ids": duplicates,
        "main_duplicates": main_duplicates,
    }


def _select_wnba_spread_market(rows: list[dict]) -> dict | None:
    """Among alternate WNBA spread lines for one event, the main line is the
    one whose long-side ask sits closest to a coin flip -- same "most
    balanced line wins" rule mlb_market_odds._market_balance uses for MLB's
    alternate lines, adapted to this snapshot format's embedded long/short
    asks instead of a two-sided quote list."""
    candidates = [
        row for row in rows if isinstance(row.get("long"), dict) and row["long"].get("ask") is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _forecast_wnba_spread_slate(data_root, args_date: str, client) -> dict:
    """Price WNBA spreads with BasketballModel's normal-CDF margin approach
    (wnba-spread-margin-v1), matched against the main (most-balanced)
    Polymarket alternate line per game.

    Snapshot rows are anchored to one team (``team``) with ``line`` always
    that team's own spread -- see PolymarketUSClient.snapshot's docstring
    comment for the exact convention this mirrors: ``long`` prices "team
    covers its own line", ``short`` prices the negation. ESPN's away team is
    matched to the snapshot's anchor team by name (verified empirically
    2026-08-14: Polymarket anchors WNBA spread rows to the away team), which
    makes the row's own ``line`` exactly BasketballModel's
    ``spread_away_line`` input with no sign transform needed.
    """
    from ..learned_forward import _team_matches, _teams
    from ..models.basketball import BasketballModel, UpcomingGame

    observed_at = utc_now()
    model_version = "wnba-spread-margin-v1"
    artifact, artifact_error = _load_exact_artifact_contract(model_version)
    if artifact is None:
        return {
            "status": "blocked",
            "reason": artifact_error,
            "sport": "wnba_spread",
            "model_version": model_version,
            "game_date": args_date,
            "scheduled_games": 0,
            "market_candidates": 0,
            "priced_contracts": [],
            "unmatched": [],
            "observed_at_utc": observed_at.isoformat(),
        }
    model_artifact_hash = str(artifact["artifact_hash"])
    model_qualified = artifact.get("qualification", {}).get("qualified") is True
    model = BasketballModel(sport="wnba", version=model_version, margin_sd=10.5, total_sd=15.0, league="WNBA")
    store = FeatureStore(data_root)
    history = store.games_before("wnba", args_date)

    scoreboard = client.scoreboard("WNBA", args_date)
    events = scoreboard.get("events", [])

    snapshot_path = Path(data_root) / "odds" / "wnba" / args_date / "polymarket_snapshots.jsonl"
    snapshot_rows = _read_polymarket_snapshot_rows(snapshot_path)
    spread_rows = [row for row in snapshot_rows if row.get("market_type") == "spread"]

    upcoming: list[UpcomingGame] = []
    market_by_event_id: dict[str, dict] = {}
    unmatched: list[dict[str, str]] = []
    for event in events:
        try:
            event_id = str(event["id"])
            start = parse_utc(str(event["date"]))
            if start <= observed_at:
                continue
            away_team, home_team = _teams(event)
        except (KeyError, TypeError, ValueError):
            continue
        # Matched by team name + start time, not Polymarket's own event_id
        # (a different id space than ESPN's -- these snapshot rows carry
        # Polymarket's event_id, which this loop never needs since matching
        # happens the same way every other sport in this project matches a
        # Polymarket quote to an ESPN event).
        candidates = [
            row
            for row in spread_rows
            if _team_matches(away_team, str(row.get("team") or ""))
            and str(row.get("event_start_utc") or "")[:16] == str(event["date"])[:16]
        ]
        market = _select_wnba_spread_market(candidates)
        if market is None:
            unmatched.append({"event_id": event_id, "reason": "no matched spread market"})
            continue
        upcoming.append(
            UpcomingGame(
                event_id=event_id,
                event_start_utc=str(event["date"]),
                away_team=away_team,
                home_team=home_team,
                spread_away_line=float(market["line"]),
            )
        )
        market_by_event_id[event_id] = market

    predictions = [p for p in model.predict_games(history, upcoming) if p.market_type == "spread"]

    priced_contracts = []
    for prediction in predictions:
        market = market_by_event_id[prediction.event_id]
        market_lineage = market.get("_lineage")
        # Model's own pick: the side with higher probability (same
        # convention as every other sport in this project).
        away_prob = prediction.probability("away")
        if away_prob >= 0.5:
            selection, model_probability = "away", away_prob
            ask = market["long"]["ask"]
            line = float(market["line"])
        else:
            selection, model_probability = "home", 1 - away_prob
            ask = market["short"]["ask"]
            line = -float(market["line"])
        if ask is None or not 0 < float(ask) < 1:
            continue
        priced_contracts.append(
            {
                "event_id": prediction.event_id,
                "event_start_utc": prediction.event_start_utc,
                "away_team": prediction.away_team,
                "home_team": prediction.home_team,
                "market_type": "spread",
                "selection": selection,
                "line": line,
                "executable_ask": float(ask),
                "model_probability": round(model_probability, 6),
                "model_uncertainty": prediction.uncertainty,
                "model_version": model_version,
                "model_artifact_hash": model_artifact_hash,
                "model_qualified": model_qualified,
                "rationale": prediction.rationale,
                "market_slug": market.get("market_slug"),
                "observed_at_utc": (
                    market_lineage["market_quote_observed_at_utc"]
                    if market_lineage is not None
                    else observed_at.isoformat()
                ),
                "market_lineage": market_lineage,
            }
        )

    return {
        "sport": "wnba_spread",
        "model_name": "WNBA Spread (margin normal CDF)",
        "model_version": model_version,
        "model_qualified": model_qualified,
        "game_date": args_date,
        "scheduled_games": len(events),
        "market_candidates": len(priced_contracts),
        "priced_contracts": priced_contracts,
        "unmatched": unmatched,
        "observed_at_utc": observed_at.isoformat(),
    }


def _forecast_wnba_spread_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    registry,
    bans,
    main_ledger=None,
    flat_ledger=None,
) -> dict:
    """Log WNBA spread picks to Main (CALL only) + Flat (every candidate) --
    same routing MLB spread/total uses (operator directive, 2026-08-03: MLB
    spread + total belong in Main alongside moneyline; WNBA is a Main-ledger
    sport under the same "show everything, human decides" philosophy).

    Trust-boundary-only eligibility (evaluate_eligibility), not the
    curated min-edge gate esports/soccer/tennis Gated Research uses -- this
    mirrors _forecast_mlb_totals_flat exactly, not _forecast_soccer_sport.
    ``registry``/``bans`` are the same instances the daily dispatch already
    builds once for WNBA moneyline -- not reconstructed here.
    """
    from ..data_sources.polymarket_us import probability_to_american

    forecast = _forecast_wnba_spread_slate(data_root, args_date, ESPNClient())
    exposure_source = flat_ledger or main_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    observed_now = utc_now()
    logged: list[dict] = []
    duplicates: list[str] = []
    main_duplicates: list[str] = []
    skipped: list[dict] = []
    for contract in forecast["priced_contracts"]:
        ask = contract["executable_ask"]
        request = PickRequest(
            event_start_utc=contract["event_start_utc"],
            event_id=contract["event_id"],
            league=League.WNBA,
            away_team=contract["away_team"],
            home_team=contract["home_team"],
            market_type=MarketType.SPREAD,
            selection=contract["selection"],
            line=contract["line"],
            sportsbook="polymarket_us",
            american_odds=probability_to_american(ask),
            model_probability=contract["model_probability"],
            model_uncertainty=contract["model_uncertainty"],
            model_version=contract["model_version"],
            rationale=(f"{contract['rationale']} Executable ask {ask:.4f} ({contract['market_slug']})."),
            risks="Research-baseline margin-normal spread model; not yet locked-holdout qualified.",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState.RESEARCH,
            observed_at_utc=contract["observed_at_utc"],
            model_artifact_hash=contract["model_artifact_hash"],
            calibration_method="margin_normal",
            calibration_version=contract["model_version"],
            calibration_artifact_hash=contract["model_artifact_hash"],
            feature_schema_version="wnba-spread-margin-v1",
            code_revision=contract["model_artifact_hash"],
            **(contract.get("market_lineage") or {}),
        )
        try:
            request.validate(now=observed_now)
            away = registry.resolve(request.league, request.away_team, request.event_start_utc)
            home = registry.resolve(request.league, request.home_team, request.event_start_utc)
            with _LEDGER_LOCK:
                eligibility = evaluate_eligibility(
                    request,
                    registry,
                    bans,
                    exposure_source.exposure(
                        request,
                        now=observed_now,
                        canonical_team_ids=(away.canonical_team_id, home.canonical_team_id),
                    ),
                    unit_policy(config),
                    now=observed_now,
                )
                if (
                    contract.get("model_qualified") is not True
                    or contract.get("market_lineage") is None
                    or not _is_registered_serving_model(
                        request.model_version,
                        request.league.value,
                        request.market_type.value,
                    )
                ):
                    eligibility = _downgrade_unserved(eligibility)
                if (
                    flat_ledger is not None
                    and _append_secondary_ledger(
                        flat_ledger, request, eligibility, observed_now, "wnba_spread:flat_ledger"
                    )
                    is not None
                ):
                    duplicates.append(contract["event_id"])
                if (
                    main_ledger is not None
                    and eligibility.decision == "CALL"
                    and _append_secondary_ledger(
                        main_ledger, request, eligibility, observed_now, "wnba_spread:main_ledger"
                    )
                    is not None
                ):
                    main_duplicates.append(contract["event_id"])
            logged.append(contract["event_id"])
        except DuplicatePickError as error:
            duplicates.append(error.pick_id)
        except (EntityResolutionError, ValueError) as error:
            skipped.append({"event_id": contract["event_id"], "reason": str(error)[:200]})

    forecast["logged"] = len(logged)
    forecast["logged_event_ids"] = logged
    forecast["duplicate_pick_ids"] = duplicates
    forecast["main_ledger_duplicate_event_ids"] = main_duplicates
    forecast["skipped"] = forecast.get("unmatched", []) + skipped
    return forecast


def _select_wnba_total_market(rows: list[dict]) -> dict | None:
    """Select the most balanced total market line for a WNBA game."""
    candidates = [
        row
        for row in rows
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _forecast_wnba_total_slate(data_root, args_date: str, client) -> dict:
    """Price WNBA totals with BasketballModel's normal-CDF score approach
    (wnba-total-margin-v1), matched against the main (most-balanced)
    Polymarket alternate line per game.
    """
    from ..learned_forward import _team_matches, _teams
    from ..models.basketball import BasketballModel, UpcomingGame

    observed_at = utc_now()
    model_version = "wnba-total-margin-v1"
    artifact, artifact_error = _load_exact_artifact_contract(model_version)
    if artifact is None:
        return {
            "status": "blocked",
            "reason": artifact_error,
            "sport": "wnba_total",
            "model_version": model_version,
            "game_date": args_date,
            "scheduled_games": 0,
            "market_candidates": 0,
            "priced_contracts": [],
            "unmatched": [],
            "observed_at_utc": observed_at.isoformat(),
        }
    model_artifact_hash = str(artifact["artifact_hash"])
    model_qualified = artifact.get("qualification", {}).get("qualified") is True

    model = BasketballModel(sport="wnba", version=model_version, margin_sd=10.5, total_sd=15.0, league="WNBA")
    store = FeatureStore(data_root)
    history = store.games_before("wnba", args_date)

    scoreboard = client.scoreboard("WNBA", args_date)
    events = scoreboard.get("events", [])

    snapshot_path = Path(data_root) / "odds" / "wnba" / args_date / "polymarket_snapshots.jsonl"
    snapshot_rows = _read_polymarket_snapshot_rows(snapshot_path)
    total_rows = [
        row
        for row in snapshot_rows
        if row.get("market_type") in ("total", "over_under")
        or "total" in str(row.get("market_slug", "")).lower()
        or "over" in str(row.get("market_slug", "")).lower()
    ]

    upcoming: list[UpcomingGame] = []
    market_by_event_id: dict[str, dict] = {}
    unmatched: list[dict[str, str]] = []
    for event in events:
        try:
            event_id = str(event["id"])
            start = parse_utc(str(event["date"]))
            if start <= observed_at:
                continue
            away_team, home_team = _teams(event)
        except (KeyError, TypeError, ValueError):
            continue

        candidates = [
            row
            for row in total_rows
            if (
                _team_matches(away_team, str(row.get("team") or ""))
                or _team_matches(home_team, str(row.get("team") or ""))
                or (
                    any(
                        w in str(row.get("event_title") or "").casefold()
                        or w in str(row.get("event_slug") or "").casefold()
                        or w in str(row.get("market_slug") or "").casefold()
                        for w in [w for w in away_team.casefold().split() if len(w) > 2]
                    )
                    and any(
                        w in str(row.get("event_title") or "").casefold()
                        or w in str(row.get("event_slug") or "").casefold()
                        or w in str(row.get("market_slug") or "").casefold()
                        for w in [w for w in home_team.casefold().split() if len(w) > 2]
                    )
                )
            )
            and str(row.get("event_start_utc") or "")[:16] == str(event["date"])[:16]
        ]
        market = _select_wnba_total_market(candidates)
        if market is None:
            unmatched.append({"event_id": event_id, "reason": "no matched total market"})
            continue
        try:
            t_line = float(market["line"])
        except (KeyError, TypeError, ValueError):
            continue
        upcoming.append(
            UpcomingGame(
                event_id=event_id,
                event_start_utc=str(event["date"]),
                away_team=away_team,
                home_team=home_team,
                total_line=t_line,
            )
        )
        market_by_event_id[event_id] = market

    predictions = [p for p in model.predict_games(history, upcoming) if p.market_type == "total"]

    priced_contracts = []
    for prediction in predictions:
        market = market_by_event_id[prediction.event_id]
        market_lineage = market.get("_lineage")
        over_prob = prediction.probability("over")
        if over_prob >= 0.5:
            selection, model_probability = "over", over_prob
            ask = market["long"]["ask"]
            line = float(market["line"])
        else:
            selection, model_probability = "under", 1 - over_prob
            ask = market["short"]["ask"]
            line = float(market["line"])
        if ask is None or not 0 < float(ask) < 1:
            continue
        priced_contracts.append(
            {
                "event_id": prediction.event_id,
                "event_start_utc": prediction.event_start_utc,
                "away_team": prediction.away_team,
                "home_team": prediction.home_team,
                "market_type": "total",
                "selection": selection,
                "line": line,
                "executable_ask": float(ask),
                "model_probability": round(model_probability, 6),
                "model_uncertainty": prediction.uncertainty,
                "model_version": model_version,
                "model_artifact_hash": model_artifact_hash,
                "model_qualified": model_qualified,
                "rationale": prediction.rationale,
                "market_slug": market.get("market_slug"),
                "observed_at_utc": (
                    market_lineage["market_quote_observed_at_utc"]
                    if market_lineage is not None
                    else observed_at.isoformat()
                ),
                "market_lineage": market_lineage,
            }
        )

    return {
        "sport": "wnba_total",
        "model_name": "WNBA Total (trend normal CDF)",
        "model_version": model_version,
        "model_qualified": model_qualified,
        "game_date": args_date,
        "scheduled_games": len(events),
        "market_candidates": len(priced_contracts),
        "priced_contracts": priced_contracts,
        "unmatched": unmatched,
        "observed_at_utc": observed_at.isoformat(),
    }


def _forecast_wnba_total_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    registry,
    bans,
    main_ledger=None,
    flat_ledger=None,
) -> dict:
    """Log WNBA total picks to Main (CALL only) + Flat (every candidate)."""
    from ..data_sources.polymarket_us import probability_to_american

    forecast = _forecast_wnba_total_slate(data_root, args_date, ESPNClient())
    exposure_source = flat_ledger or main_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    observed_now = utc_now()
    logged: list[dict] = []
    duplicates: list[str] = []
    main_duplicates: list[str] = []
    skipped: list[dict] = []
    for contract in forecast["priced_contracts"]:
        ask = contract["executable_ask"]
        request = PickRequest(
            event_start_utc=contract["event_start_utc"],
            event_id=contract["event_id"],
            league=League.WNBA,
            away_team=contract["away_team"],
            home_team=contract["home_team"],
            market_type=MarketType.TOTAL,
            selection=contract["selection"],
            line=contract["line"],
            sportsbook="polymarket_us",
            american_odds=probability_to_american(ask),
            model_probability=contract["model_probability"],
            model_uncertainty=contract["model_uncertainty"],
            model_version=contract["model_version"],
            rationale=(f"{contract['rationale']} Executable ask {ask:.4f} ({contract['market_slug']})."),
            risks="Research-baseline total model; not yet locked-holdout qualified.",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState.RESEARCH,
            observed_at_utc=contract["observed_at_utc"],
            model_artifact_hash=contract["model_artifact_hash"],
            calibration_method="total_normal",
            calibration_version=contract["model_version"],
            calibration_artifact_hash=contract["model_artifact_hash"],
            feature_schema_version="wnba-total-margin-v1",
            code_revision=contract["model_artifact_hash"],
            **(contract.get("market_lineage") or {}),
        )
        try:
            request.validate(now=observed_now)
            away = registry.resolve(request.league, request.away_team, request.event_start_utc)
            home = registry.resolve(request.league, request.home_team, request.event_start_utc)
            with _LEDGER_LOCK:
                eligibility = evaluate_eligibility(
                    request,
                    registry,
                    bans,
                    exposure_source.exposure(
                        request,
                        now=observed_now,
                        canonical_team_ids=(away.canonical_team_id, home.canonical_team_id),
                    ),
                    unit_policy(config),
                    now=observed_now,
                )
                if (
                    contract.get("model_qualified") is not True
                    or contract.get("market_lineage") is None
                    or not _is_registered_serving_model(
                        request.model_version,
                        request.league.value,
                        request.market_type.value,
                    )
                ):
                    eligibility = _downgrade_unserved(eligibility)
                if (
                    flat_ledger is not None
                    and _append_secondary_ledger(
                        flat_ledger, request, eligibility, observed_now, "wnba_total:flat_ledger"
                    )
                    is not None
                ):
                    duplicates.append(contract["event_id"])
                if (
                    main_ledger is not None
                    and eligibility.decision == "CALL"
                    and _append_secondary_ledger(
                        main_ledger, request, eligibility, observed_now, "wnba_total:main_ledger"
                    )
                    is not None
                ):
                    main_duplicates.append(contract["event_id"])
            logged.append(contract["event_id"])
        except DuplicatePickError as error:
            duplicates.append(error.pick_id)
        except (EntityResolutionError, ValueError) as error:
            skipped.append({"event_id": contract["event_id"], "reason": str(error)[:200]})

    forecast["logged"] = len(logged)
    forecast["logged_event_ids"] = logged
    forecast["duplicate_pick_ids"] = duplicates
    forecast["main_ledger_duplicate_event_ids"] = main_duplicates
    forecast["skipped"] = forecast.get("unmatched", []) + skipped
    return forecast


def _load_market_residual_model(config) -> MarketResidualModel | None:
    """Fail-soft load of the market-residual artifact (P0-4), diagnostic use only.

    A missing config block, missing file, or hash mismatch all fall back to
    None (no market_residual_probability recorded on the row) rather than
    raising into the primary forecast path -- this layer must never be able
    to block a real pick from being logged.
    """
    artifact_value = (config.get("models", {}).get("market_residual") or {}).get("artifact")
    if not artifact_value:
        return None
    artifact_path = Path(artifact_value)
    if not artifact_path.is_absolute():
        artifact_path = PROJECT_ROOT / artifact_path
    try:
        return MarketResidualModel.load(artifact_path)
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _forecast_learned_sport(
    sport: str,
    args_date: str,
    log: bool,
    config,
    registry=None,
    bans=None,
    ledger=None,
    *,
    maximum_data_age_hours: float | None = None,
    maximum_unreviewed_disagreement: float | None = None,
    flat_mode: bool = False,
    force: bool = False,
    research_ledger=None,
    gated_ledger=None,
    exposure_ledger=None,
    observed_at: datetime | None = None,
) -> dict:
    """Default production forecast path for audited learned moneyline models.

    exposure_ledger: which ledger's existing rows count toward exposure caps
    when sizing a pick — always the MAIN ledger (picks.xlsx) regardless of
    which ledger this candidate ends up WRITTEN to. Without this, flat mode
    computed exposure against flat_picks.xlsx's own much denser history
    (every game, not just qualified ones), so the same real-world game could
    size differently in the main vs. flat view of the identical decision —
    confusing, since main's rows are always a subset of flat's candidates.
    """
    decision_observed_at = observed_at or (
        utc_now() if not force else datetime.strptime(args_date, "%Y-%m-%d").replace(tzinfo=UTC)
    )
    model_config = config["models"][sport.upper()]
    residual_model = _load_market_residual_model(config)
    artifact_value = model_config.get("production_artifact")
    if not artifact_value:
        return {
            "sport": sport,
            "status": "no_production_artifact",
            "logged": 0,
            "candidates": [],
            "note": "Fail closed: no hash-verified learned artifact is configured.",
        }
    artifact_path = Path(artifact_value)
    if not artifact_path.is_absolute():
        artifact_path = PROJECT_ROOT / artifact_path
    try:
        # Soccer spans multiple ESPN leagues (EPL, LA_LIGA, ...); every other
        # learned sport maps 1:1 to a single ESPN league equal to its name and
        # passes leagues=None. config/model.yaml's models.SOCCER.leagues is
        # the single source of truth for which competitions are in scope.
        configured_leagues = model_config.get("leagues")
        candidates, skipped, scheduled = build_learned_moneyline_slate(
            sport=sport,
            game_date=args_date,
            store=FeatureStore(Path(ledger_path(config)).parent),
            client=ESPNClient(),
            artifact_path=artifact_path,
            observed_at=decision_observed_at,
            leagues=tuple(configured_leagues) if configured_leagues else None,
        )
    except ValueError as error:
        return {
            "sport": sport,
            "status": "forecast_unavailable",
            "reason": str(error),
            "logged": 0,
            "candidates": [],
        }
    calls = [candidate for candidate in candidates if candidate.call]
    qualified_calls = [candidate for candidate in calls if candidate.model_qualified]
    research_calls = [candidate for candidate in calls if not candidate.model_qualified]
    # Flat mode: log every game regardless of confidence threshold.
    to_log = candidates if flat_mode else calls
    logged: list[dict] = []
    duplicates: list[str] = []
    # DD-2 (deep debug audit, 2026-08-04): gated_ledger's secondary write
    # below used to silently drop a duplicate with no trace at all --
    # `duplicates` above only ever tracked the primary `ledger`'s own.
    gated_duplicates: list[str] = []
    unmatched: list[dict] = []
    edge_blocked: list[dict] = []
    if log and to_log and registry is not None and bans is not None and ledger is not None:
        data_root = Path(ledger_path(config)).parent
        # --force is meant for backfilling a past date's picks using genuinely
        # point-in-time data — request.validate()'s "cannot create a call
        # after the event has started" check needs a frozen (non-wall-clock)
        # timestamp, or every game that has since started gets rejected
        # regardless of --force. A single global freeze for the whole date
        # (e.g. midnight UTC) doesn't work: real Polymarket quote captures
        # for a date don't start until hours after midnight, so no captured
        # quote can ever be "as of midnight" and every one gets rejected by
        # the same validate() call as "in the future" relative to that
        # frozen instant. Each game gets its own effective decision time
        # instead (just before ITS OWN first pitch) — see effective_now below.
        # Main ledger: ONLY production sports (MLB, WNBA) — everything else goes to flat/research.
        # Flat ledger: every game gets diagnostic edge-scaled units.
        research_routed = False
        if not flat_mode and sport not in PRODUCTION_SPORTS:
            if research_ledger is not None:
                ledger = research_ledger
                research_routed = True
            else:
                return {
                    "sport": sport,
                    "status": "skipped_non_production_sport",
                    "logged": 0,
                    "candidates": candidates,
                    "note": f"{sport} is research-only — not logged to main ledger",
                }
        configured_state = str(model_config.get("status", "research"))
        for candidate in to_log:
            if force:
                try:
                    effective_now = parse_utc(candidate.event_start_utc) - timedelta(seconds=1)
                except ValueError:
                    effective_now = decision_observed_at
            else:
                effective_now = decision_observed_at
            quote = match_executable_quote(data_root, sport, args_date, candidate)
            quote_warning: str | None = None
            quote_lineage: dict[str, Any] | None = None
            if quote is None:
                if flat_mode:
                    # Flat mode: log every game even without a Polymarket quote.
                    # Use -110 as a neutral default; rationale records the gap.
                    quote = None  # signal downstream
                elif sport in PRODUCTION_SPORTS:
                    quote_warning = "executable_quote_missing_or_unmatched"
                    unmatched.append(
                        {
                            "event_id": candidate.event_id,
                            "reason": (
                                "model call retained for Today visibility; "
                                "execution blocked because no exact executable quote matched"
                            ),
                        }
                    )
                else:
                    unmatched.append(
                        {
                            "event_id": candidate.event_id,
                            "reason": "no stored executable moneyline BBO matched this matchup",
                        }
                    )
                    continue
            elif not bool(quote.get("timestamp_valid", False)):
                if flat_mode:
                    quote = None
                elif sport in PRODUCTION_SPORTS:
                    quote_warning = "executable_quote_timestamp_invalid"
                    unmatched.append(
                        {
                            "event_id": candidate.event_id,
                            "reason": (
                                "model call retained for Today visibility; "
                                "execution blocked because quote timestamp is invalid"
                            ),
                        }
                    )
                    quote = None
                else:
                    unmatched.append(
                        {
                            "event_id": candidate.event_id,
                            "reason": "quote timestamp is invalid",
                        }
                    )
                    continue
            if quote is not None:
                quote_lineage = _canonical_market_snapshot_lineage(
                    quote,
                    data_root / "odds" / sport / args_date / "polymarket_snapshots.jsonl",
                )
                if quote_lineage is None:
                    quote_warning = "executable_quote_lineage_unverifiable"
                    unmatched.append(
                        {
                            "event_id": candidate.event_id,
                            "reason": (
                                "model call retained for visibility; execution blocked because "
                                "the matched quote lacks verifiable archive lineage"
                            ),
                        }
                    )
                    if flat_mode:
                        # Flat is a paper benchmark, not an execution claim.
                        # An unverifiable quote cannot be stored as its price,
                        # but the row can still be scored on the explicit
                        # standard -110 fallback used when no quote matched.
                        quote = None
            # Operator directive (2026-07-30): the minimum-edge-vs-executable-
            # ask check no longer hides a candidate from the ledger. Sizing
            # (edge_scaled_units, applied downstream in evaluate_eligibility)
            # is driven by the model's own confidence distance from 50/50,
            # not by this vs-market number, so removing this gate does not
            # risk generously sizing a trade the model itself considers bad
            # -- it only stops hiding the row before a human ever sees it.
            # model_edge is still computed and recorded below (rationale)
            # purely as a reference: how much the model currently disagrees
            # with the executable market price. Flat mode already bypassed
            # this; now every mode does.
            model_edge = None
            if quote is not None:
                min_edge = float(model_config.get("min_edge", 0.02))
                model_edge = candidate.model_probability - quote["executable_ask"]
                if model_edge < min_edge:
                    edge_pct = f"{min_edge * 100:.0f}%"
                    edge_blocked.append(
                        {
                            "event_id": candidate.event_id,
                            "reason": f"model edge {model_edge:.4f} below {edge_pct} minimum over executable ask {quote['executable_ask']:.4f} — logged anyway, operator review",
                        }
                    )
            # Convert UTC event time to Eastern for consistent ledger display
            try:
                event_et = (
                    datetime.fromisoformat(candidate.event_start_utc)
                    .astimezone(EASTERN)
                    .strftime("%Y-%m-%dT%H:%M:%S%z")
                )
            except (ValueError, TypeError):
                event_et = candidate.event_start_utc
            observed_at_utc: str | None = None
            if quote is not None:
                american_odds = probability_to_american(quote["executable_ask"])
                sportsbook = "polymarket_us"
                observed_at_utc = str(quote.get("observed_at_utc") or "")
                decision_no_vig = quote.get("no_vig_probability")
                rationale = (
                    f"Learned LR call at threshold {candidate.confidence_threshold:.4f}; "
                    f"executable ask {quote['executable_ask']:.4f} "
                    f"({quote['market_slug']}); model edge vs ask {model_edge:+.4f}."
                )
            else:
                american_odds = -110
                sportsbook = "model_opinion_no_executable_quote" if quote_warning else "espn"
                observed_at_utc = iso_utc(effective_now) if quote_warning else None
                decision_no_vig = None
                rationale = (
                    f"Learned LR call at threshold {candidate.confidence_threshold:.4f}; "
                    f"no Polymarket quote available — using -110 default odds."
                )
                if quote_warning:
                    rationale += (
                        f" WARNING: {quote_warning}; model opinion remains visible, "
                        "but this row is not executable or price-qualified."
                    )
            row_unavailable_features = tuple(candidate.unavailable_features)
            if quote_warning:
                row_unavailable_features = tuple(dict.fromkeys((*row_unavailable_features, quote_warning)))
            if row_unavailable_features:
                # Never a reason to drop the game — just a visible note that
                # one input defaulted to neutral instead of using its real
                # value (e.g. ESPN hasn't posted both starters yet).
                rationale += (
                    f" NOTE: {', '.join(row_unavailable_features)} unavailable for "
                    f"this game — defaulted to neutral, other features used normally."
                )
            market_residual_probability = (
                residual_model.calibrated_probability(candidate.model_probability, decision_no_vig)
                if residual_model is not None and decision_no_vig is not None
                else None
            )
            request = PickRequest(
                event_start_utc=event_et,
                event_id=candidate.event_id,
                league=League(sport.upper()),
                away_team=candidate.away_team,
                home_team=candidate.home_team,
                market_type=MarketType.MONEYLINE,
                selection=candidate.selection,
                line=None,
                sportsbook=sportsbook,
                american_odds=american_odds,
                model_probability=candidate.model_probability,
                model_uncertainty=None,
                model_version=candidate.model_version,
                rationale=rationale,
                risks="Learned model; shadow-qualified via operator override.",
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=ModelState(configured_state),
                observed_at_utc=observed_at_utc,
                model_artifact_hash=candidate.model_artifact_hash,
                calibration_method="learned_lr",
                calibration_version=candidate.model_version,
                calibration_artifact_hash=candidate.model_artifact_hash,
                feature_schema_version=candidate.feature_snapshot_hash[:16],
                entity_map_version=registry.version,
                code_revision=candidate.model_version,
                decision_no_vig_probability=decision_no_vig,
                elo_probability=candidate.feature_basis.get("elo_probability"),
                trend_gap=candidate.feature_basis.get("trend_gap"),
                defensive_trend_gap=candidate.feature_basis.get("defensive_trend_gap"),
                park_factor=candidate.feature_basis.get("park_factor"),
                weather_factor=candidate.feature_basis.get("weather_factor"),
                pitcher_era_gap=candidate.feature_basis.get("pitcher_era_gap"),
                probable_starter_era_gap=candidate.feature_basis.get("probable_starter_era_gap"),
                bullpen_weakness_gap=candidate.feature_basis.get("bullpen_weakness_gap"),
                starter_era_gap=candidate.feature_basis.get("starter_era_gap"),
                market_residual_probability=market_residual_probability,
                unavailable_features=(
                    ",".join(row_unavailable_features) if row_unavailable_features else None
                ),
                **(quote_lineage or {}),
            )
            try:
                request.validate(now=effective_now)
                away = registry.resolve(request.league, request.away_team, request.event_start_utc)
                home = registry.resolve(request.league, request.home_team, request.event_start_utc)
                eligibility_kwargs: dict = {"now": effective_now}
                if maximum_data_age_hours is not None:
                    eligibility_kwargs["maximum_age_hours"] = maximum_data_age_hours
                if maximum_unreviewed_disagreement is not None:
                    eligibility_kwargs["maximum_unreviewed_disagreement"] = maximum_unreviewed_disagreement
                # Exposure check and append happen inside one held lock -- see
                # the matching comment in _log_esports_forecast.
                with _LEDGER_LOCK:
                    eligibility = evaluate_eligibility(
                        request,
                        registry,
                        bans,
                        (exposure_ledger or ledger).exposure(
                            request,
                            now=effective_now,
                            canonical_team_ids=(away.canonical_team_id, home.canonical_team_id),
                        ),
                        unit_policy(config),
                        **eligibility_kwargs,
                    )
                    if quote_warning and eligibility.decision == "CALL":
                        eligibility = replace(
                            eligibility,
                            record_type=RecordType.RESEARCH_OBSERVATION,
                            decision="CALL",
                            reason_code="PAPER_CALL_MARKET_UNAVAILABLE",
                            units=eligibility.units,
                        )
                    # evaluate_eligibility itself no longer gates on
                    # disagreement, exposure, or edge (operator directive,
                    # 2026-07-26; see eligibility._call_result) -- those
                    # remain deliberately removed. Confidence is restored
                    # here as an explicit, separate gate (operator
                    # directive, reversing F-34/F-35): candidate.
                    # confidence_threshold is each sport's own real,
                    # walk-forward-learned value (MLB v7: 0.62419, learned
                    # on validation at a 65% target hit rate; WNBA v4:
                    # 0.50013, i.e. already-effectively-ungated because the
                    # model clears almost every game) -- not a fabricated
                    # number, the same one already computed and shown as a
                    # reference/label on every candidate.
                    if (
                        eligibility.decision == "CALL"
                        and candidate.model_probability < candidate.confidence_threshold
                    ):
                        eligibility = replace(
                            eligibility,
                            record_type=RecordType.RESEARCH_OBSERVATION,
                            decision="CALL",
                            reason_code="PAPER_CALL_BELOW_LEARNED_CONFIDENCE",
                        )
                    if eligibility.decision == "CALL" and not _is_registered_serving_model(
                        request.model_version,
                        request.league.value,
                        request.market_type.value,
                    ):
                        eligibility = _downgrade_unserved(eligibility)
                    # What's still NO_CALL here is always a hard trust-
                    # boundary reason or the confidence gate just above.
                    genuinely_eligible = eligibility.decision == "CALL"
                    # Main ledger (MLB/WNBA, non-flat, non-research-routed) holds
                    # ONLY genuine qualified calls -- any remaining NO_CALL is a
                    # structurally-untrustworthy reason, real diagnostic
                    # information that still belongs in flat_picks.xlsx (which
                    # already logs every game every day) rather than muddying main.
                    skip_main_no_call = (
                        not flat_mode and not research_routed and eligibility.decision != "CALL"
                    )
                    if not skip_main_no_call:
                        logged.append(
                            ledger.append_evaluated(
                                request,
                                eligibility,
                                now=effective_now,
                            )
                        )
                    # gated_ledger mirrors research_ledger but only for rows
                    # evaluate_eligibility genuinely approved as a real call —
                    # a curated subset ledger, same relationship
                    # flat_picks.xlsx has to picks.xlsx, but for research-only
                    # sports.
                    if gated_ledger is not None and research_routed and genuinely_eligible:
                        existing_pick_id = _append_secondary_ledger(
                            gated_ledger, request, eligibility, effective_now, "learned:gated_ledger"
                        )
                        if existing_pick_id is not None:
                            gated_duplicates.append(existing_pick_id)
            except DuplicatePickError as error:
                duplicates.append(error.pick_id)
            except (EntityResolutionError, ValueError) as error:
                unmatched.append({"event_id": candidate.event_id, "reason": str(error)[:200]})
    if not log:
        logging_note = "Logging not requested."
    elif flat_mode:
        logging_note = (
            f"Flat mode: logged {len(logged)} of {len(candidates)} games "
            f"({len(calls)} above threshold); "
            f"{len(duplicates)} duplicates, {len(unmatched)} without a matched quote, "
            f"{len(edge_blocked)} below min-edge (logged anyway, operator review)."
        )
    elif not calls:
        logging_note = "No calls above the learned confidence threshold."
    else:
        logging_note = (
            f"Logged {len(logged)} of {len(calls)} calls against stored executable asks; "
            f"{len(duplicates)} duplicates, {len(unmatched)} without a matched quote, "
            f"{len(edge_blocked)} below min-edge (logged anyway, operator review)."
        )
    return {
        "sport": sport,
        "status": "learned_forecast_complete",
        "model_version": candidates[0].model_version
        if candidates
        else model_config.get("active_production_version", "unknown"),
        "artifact": str(artifact_path),
        "game_date": args_date,
        "scheduled_games": scheduled,
        "calls": len(calls),
        "qualified_shadow_calls": len(qualified_calls),
        "zero_unit_research_calls": len(research_calls),
        "logged": len(logged),
        "logged_pick_ids": [row["pick_id"] for row in logged],
        "duplicate_pick_ids": duplicates,
        "gated_ledger_duplicate_pick_ids": gated_duplicates,
        "unmatched_quotes": unmatched,
        "edge_blocked": edge_blocked,
        "skipped": skipped,
        "candidates": [candidate.to_dict() for candidate in candidates],
        "note": logging_note,
    }


def _log_esports_forecast(
    forecast: dict,
    config: dict,
    ledger: Any,
    flat_mode: bool = False,
    gated_ledger=None,
    flat_ledger=None,
) -> int:
    """Log esports contracts through the real eligibility gates.

    Esports promotion to shadow_qualified units is a DELIBERATE config
    decision (models.<TITLE>.status). This path enforces the same gates as
    every other sport — staleness, model/market disagreement, exposure caps,
    and unit-engine sizing — via ``evaluate_esports_eligibility``. Entities
    are name-based because esports teams are not in the canonical registry
    -- unlike MLB/WNBA/NBA/NFL, there is no team-ban check here at all (see
    ``evaluate_esports_eligibility``'s own docstring for why). Returns the
    count of logged rows.
    """
    from ..data_sources.polymarket_us import probability_to_american

    logged = 0
    errors: list[dict] = []
    # DD-2 (deep debug audit, 2026-08-04): see _append_secondary_ledger's
    # docstring -- these count duplicates the primary/flat/gated writes
    # below used to silently drop with no trace at all.
    primary_duplicates = 0
    flat_duplicates = 0
    gated_duplicates = 0
    if flat_mode and flat_ledger is None:
        # Research-only sports only write to Flat when a flat_ledger is
        # explicitly provided (Daily dispatches one). Direct
        # `flat-forecast --sport <esport>` calls without --all routing
        # still skip Flat for esports/KBO/NPB.
        return 0
    model_config = config["models"].get(forecast["title"].upper(), {})
    min_edge = float(model_config.get("min_edge", 0.02))
    configured_state = str(model_config.get("status", "research"))
    title = forecast["title"].upper()
    league = League(title)
    observed_now = utc_now()

    for contract in forecast.get("priced_contracts", []):
        # Only log the model's pick: the side with higher model probability.
        # Consistent with every other sport (e.g. learned_forward.py's MLB
        # moneyline: `selection = "home" if home_probability >= 0.5 else
        # "away"`) -- the model's job is to call the winner; the min_edge
        # gate downstream decides whether that call is also good enough
        # value to become a real pick vs. a zero-unit research observation.
        sides = contract.get("sides", [])
        if len(sides) != 2:
            continue
        best_side = max(sides, key=lambda s: float(s["model_probability"]))
        model_prob = float(best_side["model_probability"])
        # esports.py builds the two sides as complementary (p, 1-p), so the
        # max of the two must be >= 0.5. If it isn't, something upstream
        # produced a NaN or otherwise corrupted probability — fail closed
        # rather than log a "pick" the model doesn't actually favor.
        if model_prob < 0.5:
            continue
        ask = float(best_side["executable_ask"])

        # Research preserves every safely priced candidate, including a
        # synthetic-1500-prior candidate for an unvalidated/new team --
        # evaluate_gated_research_eligibility downgrades those to a
        # RESEARCH_OBSERVATION/NO_CALL row below rather than dropping them.
        # Gated research is the curated subset: positive executable edge, a
        # real model opinion, and both teams resolved to ratings learned by
        # this exact artifact. Exposure and model/market disagreement
        # deliberately remain relaxed for shadow research; provenance and
        # input validity do not.
        research_confidence_gate = float(model_config.get("research_confidence_gate", 0.05))
        model_inputs_valid = bool(contract.get("gated_research_eligible", False))

        selected_team = str(best_side["team"])
        # Polymarket side ordering is arbitrary for venue-neutral esports;
        # teams[0]/teams[1] map to ledger home/away consistently with
        # settlement, which reconstructs the selected team the same way.
        teams = list(contract["teams"])
        home_team = teams[0]
        away_team = teams[1]
        pick_is_home = selected_team == home_team

        american_odds = probability_to_american(ask)
        request = PickRequest(
            event_start_utc=str(contract["event_start_utc"]),
            event_id=str(contract["event_id"]),
            league=league,
            away_team=away_team,
            home_team=home_team,
            market_type=MarketType.MONEYLINE,
            selection="home" if pick_is_home else "away",
            line=None,
            sportsbook="polymarket_us",
            american_odds=american_odds,
            model_probability=round(model_prob, 6),
            model_uncertainty=None,
            model_version=str(forecast["model_version"]),
            rationale=(
                f"Neutral Elo baseline; executable ask {ask:.4f} (market_slug={contract['market_slug']})."
            ),
            risks="Config-promoted esports baseline; gates enforced at log time.",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState(configured_state),
            observed_at_utc=str(contract.get("observed_at_utc") or "") or None,
            model_artifact_hash=str(contract.get("artifact_hash", "")),
            calibration_method="neutral_elo",
            calibration_version=str(forecast["model_version"]),
            calibration_artifact_hash=str(contract.get("artifact_hash", "")),
            code_revision=str(forecast["model_version"]),
        )
        try:
            request.validate(now=observed_now)
            # Exposure is checked and the row appended inside one held lock,
            # not as two separately-lockable steps -- otherwise two concurrent
            # forecast threads could both read the same stale exposure before
            # either writes (in-process TOCTOU). This does not make the check
            # cross-process-atomic; that needs a lock spanning both ledgers.
            with _LEDGER_LOCK:
                exposure = ledger.exposure(request, now=observed_now)
                eligibility = evaluate_gated_research_eligibility(
                    request,
                    exposure,
                    unit_policy(config),
                    model_inputs_valid=model_inputs_valid,
                    minimum_edge=min_edge,
                    minimum_confidence=research_confidence_gate,
                    now=observed_now,
                    maximum_age_hours=float(config["project"].get("maximum_data_age_hours", 12)),
                    maximum_unreviewed_disagreement=float(
                        config["project"].get("maximum_unreviewed_market_disagreement", 0.10)
                    ),
                )
                genuinely_eligible = eligibility.decision == "CALL"
                ledger.append_evaluated(request, eligibility, now=observed_now)
                # Flat: every candidate, no edge gate (operator directive 2026-08-03).
                if (
                    flat_ledger is not None
                    and _append_secondary_ledger(
                        flat_ledger, request, eligibility, observed_now, f"{title}:flat_ledger"
                    )
                    is not None
                ):
                    flat_duplicates += 1
                # gated_ledger: curated subset of rows evaluate_esports_eligibility
                # genuinely approved as a real call. Same relationship
                # flat_picks.xlsx has to picks.xlsx, for research-only sports.
                if (
                    gated_ledger is not None
                    and genuinely_eligible
                    and _append_secondary_ledger(
                        gated_ledger, request, eligibility, observed_now, f"{title}:gated_ledger"
                    )
                    is not None
                ):
                    gated_duplicates += 1
            logged += 1
        except DuplicatePickError as error:
            # Primary ledger write only -- flat/gated are handled above via
            # _append_secondary_ledger, which never raises. DD-2: this used
            # to be a bare `continue` with no trace of which pick already
            # existed.
            primary_duplicates += 1
            logger.debug(
                "%s: duplicate suppressed for existing pick %s (primary ledger)",
                title,
                error.pick_id,
            )
            continue
        except (ValueError, KeyError) as error:
            # Record the failure instead of silently discarding it -- a bare
            # `continue` here previously left an entire league able to log
            # zero real predictions for a day with nothing surfaced anywhere
            # (see the KBO/NPB timestamp-ordering incident in DEBUG.md for
            # how bad a silent per-contract swallow can get).
            errors.append(
                {
                    "event_id": contract.get("event_id"),
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
            logger.warning(
                "esports forecast logging failed for event %s (%s): %s",
                contract.get("event_id"),
                title,
                error,
            )
            continue

    forecast["errors"] = errors
    forecast["duplicates"] = {
        "primary_ledger": primary_duplicates,
        "flat_ledger": flat_duplicates,
        "gated_ledger": gated_duplicates,
    }
    return logged


def _forecast_international_sport(
    data_root,
    artifact_dir,
    league: str,
    args_date: str,
    config: dict,
    research_ledger,
    gated_ledger=None,
    flat_ledger=None,
) -> dict:
    """Forecast KBO/NPB slate and log to research/gated/flat ledgers.

    Uses the centralized research gate because KBO/NPB teams are not yet in the
    canonical registry. Exact-input priced contracts go to the sport's Research
    workbook; only calls clearing the configured executable-edge and confidence
    floors also go to its Gated Research workbook.
    """
    from ..data_sources.polymarket_us import probability_to_american
    from ..international_baseball import forecast_international_baseball_slate

    league_upper = league.upper()
    model_config = config["models"].get(league_upper, {})
    min_edge = float(model_config.get("min_edge", 0.02))
    # Both teams must have real, observed history beyond the bare minimum
    # forecast_international_baseball_slate already hard-requires (it
    # NO_CALLs entirely if either team_id is missing from the artifact's
    # ratings -- see NO_CALL_MODEL_UNVALIDATED_NEW_TEAM there -- but one
    # game away from cold-start is still a thin, noisy rating).
    # MINIMUM_TEAM_GAMES matches this project's existing "enough to say
    # something" convention (validation.MINIMUM_MONTHLY_CALLS = 10), same
    # reasoning as soccer/tennis.
    MINIMUM_TEAM_GAMES = 10
    configured_state = str(model_config.get("status", "research"))
    forecast = forecast_international_baseball_slate(
        data_root,
        artifact_dir,
        league,
        args_date,
    )
    # Captured AFTER the slate builder, not before: forecast_international_
    # baseball_slate stamps each contract's own observed_at_utc with ITS OWN
    # internal utc_now() call, which -- since real fetch/compute time passes
    # inside that call -- always lands strictly after any observed_now
    # captured before calling it. request.validate(now=observed_now) then
    # ALWAYS saw an observation timestamp "in the future" and rejected every
    # single contract, unconditionally: real events=5/6 daily, logged=0
    # every single day this ran. Same ordering _forecast_soccer_sport/
    # _forecast_tennis_sport already use correctly.
    observed_now = utc_now()
    if research_ledger is None:
        forecast["logged"] = 0
        forecast["logging_note"] = "Preview only; no ledger was supplied and no rows were written."
        return forecast

    logged = 0
    errors: list[dict] = []
    # DD-2 (deep debug audit, 2026-08-04): see _append_secondary_ledger's
    # docstring -- these count duplicates the research/flat/gated writes
    # below used to silently drop with no trace at all.
    research_duplicates = 0
    flat_duplicates = 0
    gated_duplicates = 0
    for contract in forecast.get("priced_contracts", []):
        sides = contract.get("sides", [])
        if len(sides) != 2:
            continue
        # Pick the side the model actually favors: highest model_fair_settlement_value
        best_side = max(sides, key=lambda s: float(s["model_fair_settlement_value"]))
        model_prob = float(best_side["model_fair_settlement_value"])
        if model_prob <= 0.5:
            continue
        ask = float(best_side["executable_ask"])
        research_confidence_gate = float(model_config.get("research_confidence_gate", 0.0))
        model_inputs_valid = float(contract.get("min_team_games", 0)) >= MINIMUM_TEAM_GAMES
        selected_team = str(best_side["team"])
        # home_team/away_team are resolved tag-safely inside
        # forecast_international_baseball_slate (via each side's own
        # "selection" tag, not array position) -- market["sides"] has no
        # ordering guarantee, so trusting position here would risk a silent
        # home/away swap. Fall back to the (rare) old positional guess only
        # if an older contract predates this field.
        if contract.get("home_team") and contract.get("away_team"):
            home_team = str(contract["home_team"])
            away_team = str(contract["away_team"])
        else:
            teams = list(contract["teams"])
            if len(teams) == 2:
                home_team = teams[1]
                away_team = teams[0]
            else:
                home_team = teams[0] if len(teams) > 0 else selected_team
                away_team = selected_team if selected_team != home_team else ""
        pick_is_home = selected_team == home_team
        american_odds = probability_to_american(ask)
        request = PickRequest(
            event_start_utc=str(contract["event_start_utc"]),
            event_id=str(contract["event_id"]),
            league=League(league_upper),
            away_team=away_team,
            home_team=home_team,
            market_type=MarketType.MONEYLINE,
            selection="home" if pick_is_home else "away",
            line=None,
            sportsbook="polymarket_us",
            american_odds=american_odds,
            model_probability=round(model_prob, 6),
            model_uncertainty=None,
            model_version=str(forecast["model_version"]),
            rationale=(
                f"Tie-aware Elo baseline; executable ask {ask:.4f} "
                f"(market_slug={contract['market_slug']}). "
                f"Tie probability={best_side.get('tie_probability', 0):.4f}."
            ),
            risks="Config-promoted international baseball baseline; gates enforced at log time.",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState(configured_state),
            observed_at_utc=str(contract.get("observed_at_utc") or "") or None,
            model_artifact_hash=str(contract.get("artifact_hash", "")),
            calibration_method="tie_aware_elo",
            calibration_version=str(forecast["model_version"]),
            calibration_artifact_hash=str(contract.get("artifact_hash", "")),
            code_revision=str(forecast["model_version"]),
        )
        try:
            request.validate(now=observed_now)
            # Exposure check and append happen inside one held lock -- see
            # the matching comment in _log_esports_forecast.
            with _LEDGER_LOCK:
                exposure = research_ledger.exposure(request, now=observed_now)
                eligibility = evaluate_gated_research_eligibility(
                    request,
                    exposure,
                    unit_policy(config),
                    model_inputs_valid=model_inputs_valid,
                    minimum_edge=min_edge,
                    minimum_confidence=research_confidence_gate,
                    now=observed_now,
                    maximum_age_hours=float(config["project"].get("maximum_data_age_hours", 12)),
                    maximum_unreviewed_disagreement=float(
                        config["project"].get("maximum_unreviewed_market_disagreement", 0.10)
                    ),
                )
                genuinely_eligible = eligibility.decision == "CALL"
                research_ledger.append_evaluated(request, eligibility, now=observed_now)
                # Flat: every candidate, no edge gate (operator directive 2026-08-03).
                if (
                    flat_ledger is not None
                    and _append_secondary_ledger(
                        flat_ledger, request, eligibility, observed_now, f"{league_upper}:flat_ledger"
                    )
                    is not None
                ):
                    flat_duplicates += 1
                if (
                    gated_ledger is not None
                    and genuinely_eligible
                    and _append_secondary_ledger(
                        gated_ledger, request, eligibility, observed_now, f"{league_upper}:gated_ledger"
                    )
                    is not None
                ):
                    gated_duplicates += 1
            logged += 1
        except DuplicatePickError as error:
            # Primary (research_ledger) write only -- flat/gated are handled
            # above via _append_secondary_ledger, which never raises. DD-2:
            # this used to be a bare `continue` with no trace of which pick
            # already existed.
            research_duplicates += 1
            logger.debug(
                "%s: duplicate suppressed for existing pick %s (research_ledger)",
                league_upper,
                error.pick_id,
            )
            continue
        except (ValueError, KeyError) as error:
            # Record the failure instead of silently discarding it -- see
            # the matching comment in _log_esports_forecast.
            errors.append(
                {
                    "event_id": contract.get("event_id"),
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
            logger.warning(
                "international baseball forecast logging failed for event %s (%s): %s",
                contract.get("event_id"),
                league_upper,
                error,
            )
            continue
    forecast["logged"] = logged
    forecast["errors"] = errors
    forecast["duplicates"] = {
        "research_ledger": research_duplicates,
        "flat_ledger": flat_duplicates,
        "gated_ledger": gated_duplicates,
    }
    forecast["logging_note"] = (
        "Every model-favored priced contract was evaluated for the research ledger; "
        "only trust-valid contracts clearing edge and confidence gates were mirrored "
        "to the gated ledger."
    )
    return forecast


def _forecast_soccer_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    research_ledger=None,
    gated_ledger=None,
    flat_ledger=None,
    main_ledger=None,
) -> dict:
    """Price the draw-aware soccer score model: full-game 2.5 totals, plus
    moneyline whenever Polymarket lists a matching two-sided win market.

    flat_ledger: every priced contract, no edge/confidence gate -- same
    "log everything" semantics flat mode uses for the learned sports.
    main_ledger: appended only when genuinely eligible (same
    evaluate_gated_research_eligibility check gated_ledger would use). As of
    the 2026-08-03 Main+Flat-only operator directive, no real caller passes
    gated_ledger/research_ledger for soccer anymore (RESEARCH_LEDGER_SPORTS
    no longer includes "soccer") -- those two parameters stay for tests /
    API symmetry with the other _forecast_*_sport functions but are
    production-dead here. Soccer's config was set to status: shadow_qualified
    via an explicit manual qualification_override (operator directive,
    2026-08-02) rather than a genuine walk-forward/locked-holdout pass --
    see config/model.yaml's SOCCER.qualification_override_reason for the
    honest disclosure. Real Main-ledger rows now get produced whenever a
    contract clears min_edge; _row_artifact_qualified (cli.py) still fails
    closed for real execution since no genuinely-qualified soccer artifact
    exists, so PolymarketExecutor.execute requires --manual-research-order
    for any actual order on these rows.
    """
    from ..data_sources.polymarket_us import probability_to_american

    model_config = config["models"].get("SOCCER", {})
    forecast = build_soccer_total_slate(
        data_root=data_root,
        game_date=args_date,
        client=ESPNClient(),
        leagues=tuple(model_config.get("leagues") or ()),
        observed_at=utc_now(),
    )
    # research_ledger is the usual exposure/eligibility context; a flat-only
    # call (research_ledger=None, flat_ledger set) still needs somewhere to
    # compute exposure against, so fall back to flat_ledger in that case.
    exposure_source = research_ledger or flat_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    min_edge = float(model_config.get("min_edge", 0.05))
    # Real edge/confidence validation for soccer's primary market lives in
    # validation.qualify_soccer_total_model (chronological 60/20/20 split,
    # learned confidence threshold, locked-holdout units_at_minus_110 +
    # monthly-consistency check -- same rigor as every other model in this
    # project). Read from config, same mechanism as esports/KBO/NPB, so a
    # validated value can be set here without another code change; defaults
    # to 0.0 (no gate) until that value is set.
    research_confidence_gate = float(model_config.get("research_confidence_gate", 0.0))
    # Both teams must have real, observed history (not the neutral
    # attack=defense=1.0 cold-start default SoccerModel._strengths falls
    # back to for a team it's never seen) before a call counts as resting
    # on a genuine model opinion. Mirrors esports' gated_research_eligible
    # check; MINIMUM_TEAM_GAMES matches this project's existing "enough to
    # say something" convention (validation.MINIMUM_MONTHLY_CALLS = 10).
    MINIMUM_TEAM_GAMES = 10
    configured_state = str(model_config.get("status", "research"))
    # build_soccer_total_slate hardcodes "status": "research" in its return
    # dict (it has no config access) -- overwrite with the real configured
    # promotion tier so the dashboard/diagnostic output doesn't show a stale
    # "research" label when config actually has soccer at shadow_qualified.
    forecast["status"] = configured_state
    observed_now = utc_now()
    logged = 0
    gated = 0
    flat_logged = 0
    main_logged = 0
    # DD-2 (deep debug audit, 2026-08-04): these count duplicates the same
    # four ledger writes below silently dropped before this fix (each was a
    # bare suppress(DuplicatePickError), with no way to tell "this exact
    # market was already logged" apart from "the model produced nothing
    # here"). See _append_secondary_ledger's docstring for the full context.
    research_duplicates = 0
    gated_duplicates = 0
    flat_duplicates = 0
    main_duplicates = 0
    errors: list[dict] = []
    for contract in forecast.get("priced_contracts", []):
        ask = float(contract["executable_ask"])
        min_team_games = float((contract.get("feature_basis") or {}).get("min_team_games", 0.0))
        model_inputs_valid = min_team_games >= MINIMUM_TEAM_GAMES
        request = PickRequest(
            event_start_utc=str(contract["event_start_utc"]),
            event_id=str(contract["event_id"]),
            league=League.SOCCER,
            away_team=str(contract["away_team"]),
            home_team=str(contract["home_team"]),
            market_type=MarketType(str(contract["market_type"])),
            selection=str(contract["selection"]),
            line=None if contract["line"] is None else float(contract["line"]),
            sportsbook="polymarket_us",
            american_odds=probability_to_american(ask),
            model_probability=float(contract["model_probability"]),
            model_uncertainty=float(contract["model_uncertainty"]),
            model_version=str(contract["model_version"]),
            rationale=(f"{contract['rationale']} Executable ask {ask:.4f} ({contract['market_slug']})."),
            risks=("Research-only soccer score model; draw-aware, but not yet locked-holdout qualified."),
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState(configured_state),
            observed_at_utc=str(contract["observed_at_utc"]),
            model_artifact_hash=str(forecast["model_code_hash"]),
            calibration_method="poisson_dixon_coles",
            calibration_version=str(contract["model_version"]),
            calibration_artifact_hash=str(forecast["model_code_hash"]),
            feature_schema_version="soccer-poisson-dc-v1",
            code_revision=str(forecast["model_code_hash"]),
        )
        try:
            request.validate(now=observed_now)
            # Exposure check and append happen inside one held lock -- see
            # the matching comment in _log_esports_forecast.
            with _LEDGER_LOCK:
                eligibility = evaluate_gated_research_eligibility(
                    request,
                    exposure_source.exposure(request, now=observed_now),
                    unit_policy(config),
                    model_inputs_valid=model_inputs_valid,
                    minimum_edge=min_edge,
                    minimum_confidence=research_confidence_gate,
                    now=observed_now,
                    maximum_age_hours=float(config["project"].get("maximum_data_age_hours", 12)),
                    maximum_unreviewed_disagreement=float(
                        config["project"].get(
                            "maximum_unreviewed_market_disagreement",
                            0.10,
                        )
                    ),
                )
                genuinely_eligible = eligibility.decision == "CALL"
                if (
                    research_ledger is not None
                    and _append_secondary_ledger(
                        research_ledger, request, eligibility, observed_now, "soccer:research_ledger"
                    )
                    is not None
                ):
                    research_duplicates += 1
                if gated_ledger is not None and genuinely_eligible:
                    if (
                        _append_secondary_ledger(
                            gated_ledger, request, eligibility, observed_now, "soccer:gated_ledger"
                        )
                        is None
                    ):
                        gated += 1
                    else:
                        gated_duplicates += 1
                if flat_ledger is not None:
                    # Flat: log every priced contract regardless of
                    # eligibility, same "show everything" semantics flat
                    # mode uses for every other sport.
                    if (
                        _append_secondary_ledger(
                            flat_ledger, request, eligibility, observed_now, "soccer:flat_ledger"
                        )
                        is None
                    ):
                        flat_logged += 1
                    else:
                        flat_duplicates += 1
                if main_ledger is not None and genuinely_eligible:
                    # Mirrors gated_ledger exactly -- same eligibility
                    # result, same "only when genuinely eligible" gate. See
                    # this function's docstring: inert until soccer is
                    # promoted past status: research in config/model.yaml.
                    if (
                        _append_secondary_ledger(
                            main_ledger, request, eligibility, observed_now, "soccer:main_ledger"
                        )
                        is None
                    ):
                        main_logged += 1
                    else:
                        main_duplicates += 1
            logged += 1
        except DuplicatePickError:
            continue
        except (KeyError, ValueError) as error:
            # Record the failure instead of silently discarding it -- see
            # the matching comment in _log_esports_forecast.
            errors.append(
                {
                    "event_id": contract.get("event_id"),
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
            logger.warning(
                "soccer forecast logging failed for event %s: %s",
                contract.get("event_id"),
                error,
            )
            continue
    forecast["logged"] = logged
    forecast["gated_logged"] = gated
    forecast["flat_logged"] = flat_logged
    forecast["main_logged"] = main_logged
    forecast["duplicates"] = {
        "research_ledger": research_duplicates,
        "gated_ledger": gated_duplicates,
        "flat_ledger": flat_duplicates,
        "main_ledger": main_duplicates,
    }
    forecast["errors"] = errors
    return forecast


def _forecast_tennis_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    research_ledger=None,
    gated_ledger=None,
    flat_ledger=None,
    main_ledger=None,
) -> dict:
    """Price the surface-blended Elo model against WTA and ATP moneyline
    markets -- see tennis_forward.py for why ITF can never be matched here.

    main_ledger: appended only when genuinely eligible, same as
    _forecast_soccer_sport -- and like soccer, no real caller passes
    gated_ledger/research_ledger here either (Main+Flat-only directive,
    2026-08-03); those params are test-only at this point. TENNIS's config
    was set to status: shadow_qualified via an explicit manual qualification_override
    (operator directive, 2026-08-03) rather than a genuine walk-forward/
    locked-holdout pass -- see config/model.yaml's TENNIS.
    qualification_override_reason. Real Main-ledger rows now get produced
    whenever a contract clears min_edge; _row_artifact_qualified (cli.py)
    still fails closed for real execution since no genuinely-qualified
    tennis artifact exists, so PolymarketExecutor.execute requires
    --manual-research-order for any actual order on these rows.
    """
    from ..data_sources.polymarket_us import probability_to_american

    model_config = config["models"].get("TENNIS", {})
    forecast = build_tennis_slate(
        data_root=data_root,
        game_date=args_date,
        client=ESPNClient(),
        observed_at=utc_now(),
    )
    exposure_source = research_ledger or flat_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    min_edge = float(model_config.get("min_edge", 0.05))
    # Real edge/confidence validation lives in validation.qualify_tennis_elo_model
    # (chronological 60/20/20 split, learned confidence threshold, locked-
    # holdout units_at_minus_110 + monthly-consistency check -- same rigor
    # as every other model in this project). Read from config, same
    # mechanism as esports/KBO/NPB/soccer, so a validated value can be set
    # here without another code change; defaults to 0.0 (no gate) until set.
    research_confidence_gate = float(model_config.get("research_confidence_gate", 0.0))
    # Both players must have real, observed match history beyond the bare
    # minimum TennisModel.predict_games already hard-requires (it skips a
    # match entirely if either player has zero history at all -- see that
    # function's own comment -- but one win/loss is still a thin, noisy
    # rating). MINIMUM_PLAYER_MATCHES matches this project's existing
    # "enough to say something" convention (validation.MINIMUM_MONTHLY_CALLS
    # = 10), same reasoning as soccer's MINIMUM_TEAM_GAMES.
    MINIMUM_PLAYER_MATCHES = 10
    configured_state = str(model_config.get("status", "research"))
    observed_now = utc_now()
    logged = 0
    gated = 0
    flat_logged = 0
    main_logged = 0
    # DD-2 (deep debug audit, 2026-08-04): see _append_secondary_ledger's
    # docstring -- these count duplicates the four ledger writes below used
    # to silently drop.
    research_duplicates = 0
    gated_duplicates = 0
    flat_duplicates = 0
    main_duplicates = 0
    errors: list[dict] = []
    for contract in forecast.get("priced_contracts", []):
        ask = float(contract["executable_ask"])
        min_player_matches = float((contract.get("feature_basis") or {}).get("min_player_matches", 0.0))
        model_inputs_valid = min_player_matches >= MINIMUM_PLAYER_MATCHES
        request = PickRequest(
            event_start_utc=str(contract["event_start_utc"]),
            event_id=str(contract["event_id"]),
            league=League.TENNIS,
            away_team=str(contract["away_team"]),
            home_team=str(contract["home_team"]),
            market_type=MarketType(str(contract["market_type"])),
            selection=str(contract["selection"]),
            line=None if contract["line"] is None else float(contract["line"]),
            sportsbook="polymarket_us",
            american_odds=probability_to_american(ask),
            model_probability=float(contract["model_probability"]),
            model_uncertainty=float(contract["model_uncertainty"]),
            model_version=str(contract["model_version"]),
            rationale=(f"{contract['rationale']} Executable ask {ask:.4f} ({contract['market_slug']})."),
            risks=(
                "Surface-blended Elo model; singles only, WTA+ATP market "
                "coverage, not yet locked-holdout qualified -- promoted by "
                "explicit operator directive, not genuine walk-forward validation."
            ),
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState(configured_state),
            observed_at_utc=str(contract["observed_at_utc"]),
            model_artifact_hash=str(forecast["model_code_hash"]),
            calibration_method="surface_blended_elo",
            calibration_version=str(contract["model_version"]),
            calibration_artifact_hash=str(forecast["model_code_hash"]),
            feature_schema_version="tennis-surface-elo-v1",
            code_revision=str(forecast["model_code_hash"]),
            market_quote_observed_at_utc=contract.get("market_quote_observed_at_utc"),
            market_quote_timestamp_valid=contract.get("market_quote_timestamp_valid"),
            market_quote_source=contract.get("market_quote_source"),
            market_quote_provenance=contract.get("market_quote_provenance"),
            market_quote_reconstructed=contract.get("market_quote_reconstructed"),
            market_snapshot_hash=contract.get("market_snapshot_hash"),
            market_snapshot_archive_path=contract.get("market_snapshot_archive_path"),
            market_snapshot_record_id=contract.get("market_snapshot_record_id"),
        )
        try:
            request.validate(now=observed_now)
            # Exposure check and append happen inside one held lock -- see
            # the matching comment in _log_esports_forecast.
            with _LEDGER_LOCK:
                eligibility = evaluate_gated_research_eligibility(
                    request,
                    exposure_source.exposure(request, now=observed_now),
                    unit_policy(config),
                    model_inputs_valid=model_inputs_valid,
                    minimum_edge=min_edge,
                    minimum_confidence=research_confidence_gate,
                    now=observed_now,
                    maximum_age_hours=float(config["project"].get("maximum_data_age_hours", 12)),
                    maximum_unreviewed_disagreement=float(
                        config["project"].get(
                            "maximum_unreviewed_market_disagreement",
                            0.10,
                        )
                    ),
                )
                if not all(
                    (
                        request.market_snapshot_hash,
                        request.market_snapshot_archive_path,
                        request.market_snapshot_record_id,
                    )
                ):
                    eligibility = _downgrade_unserved(
                        eligibility,
                        reason_code="PAPER_CALL_MARKET_UNAVAILABLE",
                    )
                genuinely_eligible = eligibility.decision == "CALL"
                if (
                    research_ledger is not None
                    and _append_secondary_ledger(
                        research_ledger, request, eligibility, observed_now, "tennis:research_ledger"
                    )
                    is not None
                ):
                    research_duplicates += 1
                if gated_ledger is not None and genuinely_eligible:
                    if (
                        _append_secondary_ledger(
                            gated_ledger, request, eligibility, observed_now, "tennis:gated_ledger"
                        )
                        is None
                    ):
                        gated += 1
                    else:
                        gated_duplicates += 1
                if flat_ledger is not None:
                    # Flat: log every priced contract regardless of
                    # eligibility, same "show everything" semantics flat
                    # mode uses for every other sport.
                    if (
                        _append_secondary_ledger(
                            flat_ledger, request, eligibility, observed_now, "tennis:flat_ledger"
                        )
                        is None
                    ):
                        flat_logged += 1
                    else:
                        flat_duplicates += 1
                if main_ledger is not None and genuinely_eligible:
                    if (
                        _append_secondary_ledger(
                            main_ledger, request, eligibility, observed_now, "tennis:main_ledger"
                        )
                        is None
                    ):
                        main_logged += 1
                    else:
                        main_duplicates += 1
            logged += 1
        except DuplicatePickError:
            continue
        except (KeyError, ValueError) as error:
            # Record the failure instead of silently discarding it -- see
            # the matching comment in _log_esports_forecast.
            errors.append(
                {
                    "event_id": contract.get("event_id"),
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
            logger.warning(
                "tennis forecast logging failed for event %s: %s",
                contract.get("event_id"),
                error,
            )
            continue
    forecast["logged"] = logged
    forecast["gated_logged"] = gated
    forecast["flat_logged"] = flat_logged
    forecast["main_logged"] = main_logged
    forecast["duplicates"] = {
        "research_ledger": research_duplicates,
        "gated_ledger": gated_duplicates,
        "flat_ledger": flat_duplicates,
        "main_ledger": main_duplicates,
    }
    forecast["errors"] = errors
    return forecast


def _forecast_cfb_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    research_ledger=None,
    gated_ledger=None,
    flat_ledger=None,
    main_ledger=None,
    force: bool = False,
) -> dict:
    """Forecast and log College Football slate (Moneyline, Spread, Total).

    flat_ledger: unconditionally logs all candidate forecasts across all 3 markets.
    main_ledger: logs qualified calls meeting min_edge and uncertainty thresholds with edge-scaled sizing.
    """
    from ..data_sources.polymarket_us import probability_to_american
    from ..models.college_football import build_cfb_slate

    model_config = config.get("models", {}).get("NCAAF", {})
    forecast = build_cfb_slate(
        data_root=data_root,
        game_date=args_date,
        observed_at=utc_now(),
    )
    exposure_source = main_ledger or flat_ledger or research_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    min_edge = float(model_config.get("min_edge", 0.03))
    configured_state = str(model_config.get("status", "shadow_qualified"))
    forecast["status"] = configured_state
    observed_now = utc_now()
    logged = gated = flat_logged = main_logged = 0
    research_duplicates = gated_duplicates = flat_duplicates = main_duplicates = 0
    errors: list[dict] = []

    for contract in forecast.get("priced_contracts", []):
        ask = float(contract["executable_ask"])
        mtype = MarketType(str(contract["market_type"]))
        if mtype == MarketType.SPREAD:
            m_version = "cfb-spread-v1"
        elif mtype == MarketType.TOTAL:
            m_version = "cfb-total-v1"
        else:
            m_version = "college-football-v1"

        try:
            start_dt = parse_utc(str(contract["event_start_utc"]))
            effective_now = (
                (start_dt - timedelta(minutes=15)) if (force or observed_now >= start_dt) else observed_now
            )
            request = PickRequest(
                event_start_utc=str(contract["event_start_utc"]),
                event_id=str(contract["event_id"]),
                league=League.NCAAF,
                away_team=str(contract["away_team"]),
                home_team=str(contract["home_team"]),
                market_type=mtype,
                selection=str(contract["selection"]),
                line=None if contract["line"] is None else float(contract["line"]),
                sportsbook="espn_consensus",
                american_odds=probability_to_american(ask),
                model_probability=float(contract["model_probability"]),
                model_uncertainty=float(contract["model_uncertainty"]),
                model_version=m_version,
                rationale=str(contract["rationale"]),
                risks="College football model; multi-market (moneyline, spread, total).",
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=ModelState(configured_state),
                observed_at_utc=effective_now.isoformat(),
                model_artifact_hash=str(forecast["model_code_hash"]),
                calibration_method="cfb_key_number_engine",
                calibration_version=m_version,
                calibration_artifact_hash=str(forecast["model_code_hash"]),
                feature_schema_version="cfb-v1",
                code_revision=str(forecast["model_code_hash"]),
            )
            request.validate(now=effective_now)
            with _LEDGER_LOCK:
                eligibility = evaluate_gated_research_eligibility(
                    request,
                    exposure_source.exposure(request, now=effective_now),
                    unit_policy(config),
                    model_inputs_valid=True,
                    minimum_edge=min_edge,
                    minimum_confidence=0.0,
                    now=effective_now,
                    maximum_age_hours=float(config.get("project", {}).get("maximum_data_age_hours", 12)),
                    maximum_unreviewed_disagreement=float(
                        config.get("project", {}).get(
                            "maximum_unreviewed_market_disagreement",
                            0.10,
                        )
                    ),
                )
                genuinely_eligible = eligibility.decision == "CALL"
                if (
                    research_ledger is not None
                    and _append_secondary_ledger(
                        research_ledger, request, eligibility, effective_now, "ncaaf:research_ledger"
                    )
                    is not None
                ):
                    research_duplicates += 1
                if gated_ledger is not None and genuinely_eligible:
                    if (
                        _append_secondary_ledger(
                            gated_ledger, request, eligibility, effective_now, "ncaaf:gated_ledger"
                        )
                        is None
                    ):
                        gated += 1
                    else:
                        gated_duplicates += 1
                if flat_ledger is not None:
                    if (
                        _append_secondary_ledger(
                            flat_ledger, request, eligibility, effective_now, "ncaaf:flat_ledger"
                        )
                        is None
                    ):
                        flat_logged += 1
                    else:
                        flat_duplicates += 1
                # NCAAF is research-only with synthetic baseline; never routes to Main Ledger
                is_market_qualified = False
                if main_ledger is not None and genuinely_eligible and is_market_qualified:
                    if (
                        _append_secondary_ledger(
                            main_ledger, request, eligibility, effective_now, "ncaaf:main_ledger"
                        )
                        is None
                    ):
                        main_logged += 1
                    else:
                        main_duplicates += 1
            logged += 1
        except DuplicatePickError:
            continue
        except (KeyError, ValueError) as error:
            errors.append(
                {
                    "event_id": contract.get("event_id"),
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
            logger.warning("ncaaf forecast logging failed for event %s: %s", contract.get("event_id"), error)
            continue

    forecast["logged"] = logged
    forecast["gated_logged"] = gated
    forecast["flat_logged"] = flat_logged
    forecast["main_logged"] = main_logged
    forecast["duplicates"] = {
        "research_ledger": research_duplicates,
        "gated_ledger": gated_duplicates,
        "flat_ledger": flat_duplicates,
        "main_ledger": main_duplicates,
    }
    forecast["errors"] = errors
    return forecast


def _forecast_research_sport(sport: str, args_date: str, config) -> dict:
    """Research-only preview for non-MLB sports from cached data. Never logs."""
    store = FeatureStore(Path(ledger_path(config)).parent)
    games = store.games_before(sport, args_date)
    if len(games) < 50:
        return {
            "sport": sport,
            "status": "insufficient_local_history",
            "cached_games_before_date": len(games),
            "note": (
                f"Run `model-prediction bootstrap --sport {sport} --from <season-start>` to build "
                "the local dataset. Research models never log qualified calls until they reach "
                "60% hit rate on 50+ locked-holdout calls with every called month positive."
            ),
        }
    from ..models.registry import get_model

    model = get_model(sport)
    return {
        "sport": sport,
        "status": "research_preview_available",
        "model_version": model.version,
        "cached_games_before_date": len(games),
        "note": (
            "Model is RESEARCH state: predictions available programmatically; no ledger writes "
            "until backtest validation and lifecycle promotion."
        ),
    }


def _append_secondary_ledger(
    ledger: Any, request: PickRequest, eligibility: Any, now: datetime, ledger_name: str
) -> str | None:
    """Append a request/eligibility pair to a secondary ledger (Flat/Gated
    Research/Main mirroring a primary write already logged elsewhere in the
    same forecast loop). Returns None on a genuinely new row, or the
    already-logged pick's own pick_id on a duplicate -- matching this
    codebase's existing `duplicates.append(error.pick_id)` convention for
    primary-ledger duplicates, so a secondary duplicate is now traceable
    back to the exact row that blocked it, not just a count.

    DD-2 (deep debug audit, 2026-08-04): every one of this project's
    per-sport forecast functions writes to more than one ledger per
    candidate, and previously guarded every secondary write with a bare
    `with suppress(DuplicatePickError):` -- a genuine "this exact market
    was already logged to <ledger>" event was completely invisible, with no
    way for an operator to tell it apart from "the model just didn't
    produce a candidate here."
    """
    try:
        ledger.append_evaluated(request, eligibility, now=now)
        return None
    except DuplicatePickError as error:
        logger.debug("%s: duplicate suppressed for existing pick %s", ledger_name, error.pick_id)
        return error.pick_id


def run_forecast(args, config, registry, bans, ledger, audit, data_root) -> dict:
    log = args.command == "log" or getattr(args, "log", False) or args.command == "flat-forecast"
    replace_today = getattr(args, "replace_today", False) or args.command == "flat-forecast"
    is_flat = args.command == "flat-forecast"
    sports = (
        [*FLAT_LEDGER_SPORTS, "soccer", "tennis"]
        if is_flat and getattr(args, "all", False)
        else (list(SPORTS) + list(ESPORTS_TITLES) if getattr(args, "all", False) else [args.sport or "mlb"])
    )
    # Constructed unconditionally (not just when is_flat) so sports whose
    # main/flat ledgers form a pair -- soccer, matching how its research/
    # gated ledgers already pair -- can log to both from either command.
    flat_ledger = MultiSportPickLedger(data_root, flat=True)
    # Scopes clearing to only the sport(s) this invocation is about to
    # regenerate -- flat_ledger/ledger both span every Main-ledger
    # sport (mlb/wnba/soccer/tennis), so an unscoped clear on a
    # single-sport run (e.g. `flat-forecast --sport tennis --log`)
    # would silently wipe every OTHER sport's still-open today rows
    # too, with nothing in this run to regenerate them. See
    # _clear_today_open's docstring for the 2026-08-03 incident.
    main_ledger_sport_scope = {s.casefold() for s in sports} & set(MAIN_LEDGER_SPORTS)
    if is_flat:
        if replace_today and log:
            _clear_today_open(flat_ledger, args.date, by_event_date=True, leagues=main_ledger_sport_scope)
    elif replace_today and log:
        _clear_today_open(ledger, args.date, by_event_date=True, leagues=main_ledger_sport_scope)
    data_directory = Path(ledger_path(config)).parent
    # Soccer and tennis are the two sports whose forecast functions
    # write BOTH main_ledger and flat_ledger unconditionally whenever
    # `log` is true, regardless of which command ran (see
    # _forecast_soccer_sport/_forecast_tennis_sport call sites below:
    # `main_ledger=(ledger if log else None), flat_ledger=(flat_ledger
    # if log else None)`) -- every other sport only ever writes the
    # one ledger matching is_flat. The is_flat/not-is_flat branches
    # above only clear the ledger matching the command that ran, so
    # without this, a second same-day run of the *other* command
    # (`forecast --sport soccer --log` after an earlier `flat-forecast`,
    # or vice versa) duplicates that sport's rows in the ledger this
    # run doesn't otherwise touch. Originally patched for soccer only
    # (2026-08-03) after it was caught duplicating Main rows; tennis
    # was added to Main+Flat the same day but missed this fix, and the
    # symmetric non-flat-run-duplicates-Flat gap was never covered for
    # either sport.
    dual_ledger_sports = {s.casefold() for s in sports} & DUAL_LEDGER_SPORTS
    if replace_today and log and not is_flat:
        selected_research_sports = (
            RESEARCH_LEDGER_SPORTS
            if getattr(args, "all", False)
            else tuple(sport for sport in sports if sport.casefold() in RESEARCH_LEDGER_SPORTS)
        )
        for research_sport in selected_research_sports:
            _clear_today_open(
                research_ledger(data_directory, research_sport),
                args.date,
                by_event_date=True,
            )
            _clear_today_open(
                research_ledger(data_directory, research_sport, gated=True),
                args.date,
                by_event_date=True,
            )
        if dual_ledger_sports:
            _clear_today_open(flat_ledger, args.date, by_event_date=True, leagues=dual_ledger_sports)
    elif replace_today and log and is_flat and dual_ledger_sports:
        # NOTE: soccer/tennis's research/gated ledgers stopped being
        # written to entirely as of the 2026-08-03 Main+Flat-only
        # directive (RESEARCH_LEDGER_SPORTS no longer includes either)
        # -- this used to also clear those files, but research_ledger()
        # now raises ValueError for a sport outside RESEARCH_LEDGER_SPORTS,
        # so clearing them here would crash rather than no-op. Removed.
        _clear_today_open(ledger, args.date, by_event_date=True, leagues=dual_ledger_sports)
    results = {}
    for sport in sports:
        if sport == "esports":
            continue  # handled individually as lol/cs2
        selected_model = getattr(args, "model", "learned")
        if selected_model == "legacy-measured-edge":
            if sport != "mlb":
                raise ValueError("legacy-measured-edge is available only for MLB")
            results[sport] = _forecast_mlb(args.date, log, config, registry, bans, ledger, audit)
        elif sport in ESPORTS_TITLES:
            sport_research = research_ledger(data_directory, sport)
            sport_gated = research_ledger(data_directory, sport, gated=True)
            results[sport] = forecast_esports_slate(
                data_root=data_directory,
                artifact_dir=_research_models_dir(),
                title=sport,
                game_date=args.date,
            )
            if log and ledger is not None:
                # Esports never reaches Main, so no Flat row either
                # (operator directive, 2026-08-03) -- Research is
                # already its "every candidate" companion.
                _log_esports_forecast(
                    results[sport],
                    config,
                    sport_research,
                    flat_mode=is_flat,
                    gated_ledger=sport_gated,
                )
        elif sport in ("kbo", "npb"):
            # KBO/NPB never reach Main, so no Flat row either
            # (operator directive, 2026-08-03) -- same reasoning as
            # esports above.
            results[sport] = _forecast_international_sport(
                data_root=data_directory,
                artifact_dir=_research_models_dir(),
                league=sport,
                args_date=args.date,
                config=config,
                research_ledger=(research_ledger(data_directory, sport) if log and not is_flat else None),
                gated_ledger=(
                    research_ledger(data_directory, sport, gated=True) if log and not is_flat else None
                ),
            )
        elif sport == "soccer":
            # Main+Flat only (operator directive 2026-08-03).
            results[sport] = _forecast_soccer_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=(ledger if log else None),
                flat_ledger=(flat_ledger if log else None),
            )
        elif sport == "tennis":
            # Main+Flat only (operator directive 2026-08-03).
            results[sport] = _forecast_tennis_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=(ledger if log else None),
                flat_ledger=(flat_ledger if log else None),
            )
        elif sport in ("ncaaf", "cfb"):
            results[sport] = _forecast_cfb_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=(ledger if log else None),
                flat_ledger=(flat_ledger if log else None),
                force=getattr(args, "force", False),
            )
        elif sport in LEARNED_PRODUCTION_SPORTS:
            use_ledger = flat_ledger if is_flat else ledger
            results[sport] = _forecast_learned_sport(
                sport,
                args.date,
                log,
                config,
                registry,
                bans,
                use_ledger,
                maximum_data_age_hours=float(config["project"].get("maximum_data_age_hours", 12)),
                maximum_unreviewed_disagreement=float(
                    config["project"].get("maximum_unreviewed_market_disagreement", 0.10)
                ),
                flat_mode=is_flat,
                force=getattr(args, "force", False),
                research_ledger=None,
                gated_ledger=None,
                exposure_ledger=ledger,
            )
            if is_flat and sport == "mlb":
                results["mlb_totals"] = _forecast_mlb_totals_flat(
                    args.date, log, config, registry, bans, flat_ledger, audit
                )
        else:
            results[sport] = _forecast_research_sport(sport, args.date, config)
    output = results[sports[0]] if len(sports) == 1 else results
    return output
