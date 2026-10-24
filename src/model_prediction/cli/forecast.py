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
from ..feature_capture import contract_snapshot_json
from ..features.base import FeatureStore
from ..features.nfl_efficiency import NFLEfficiencyStore
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

STANDARD_LAY_ASK = round(implied_probability(-110), 6)


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
    return champion is not None and champion.available and champion.model_id == model_version


def _downgrade_unserved(eligibility: Any, reason_code: str = "PAPER_CALL_MODEL_UNVALIDATED") -> Any:
    return replace(
        eligibility,
        record_type=RecordType.RESEARCH_OBSERVATION,
        decision="CALL",
        reason_code=reason_code.replace("NO_CALL_", "PAPER_CALL_"),
    )


def _canonical_market_snapshot_lineage(row: dict[str, Any], archive_path: Path) -> dict[str, Any] | None:
    """Bind a parsed prospective quote to its exact archived JSON record."""
    row = row.get("_archive_record", row)
    if not isinstance(row, dict):
        return None
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
    from ..snapshot_archives import snapshot_paths

    sport = snapshot_path.parent.parent.name
    paths = [snapshot_path] if snapshot_path.is_file() else []
    if len(snapshot_path.parents) > 3 and snapshot_path.parents[2].name == "odds":
        paths = snapshot_paths(snapshot_path.parents[3], sport, snapshot_path.parent.name)
    for archive_path in paths:
        rows.extend(_read_single_snapshot_archive(archive_path, sport))
    return rows


def _read_single_snapshot_archive(snapshot_path: Path, sport: str) -> list[dict[str, Any]]:
    rows = []
    with snapshot_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                snapshot_path.parent.parent.name == "basketball"
                and str(row.get("league", "")).casefold() != sport.casefold()
            ):
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
    odds_feed = MLBMarketOddsFeed(
        registry,
        MarketOddsSnapshotStore(market_odds_snapshot_path(config)),
        espn=ESPNClient(),
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
                model_input_snapshot_json=(
                    json.dumps(candidate.model_input_snapshot, sort_keys=True, allow_nan=False)
                    if candidate.model_input_snapshot
                    else None
                ),
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
    odds_feed = MLBMarketOddsFeed(
        registry,
        MarketOddsSnapshotStore(market_odds_snapshot_path(config)),
        espn=ESPNClient(),
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
    if blend_policy is None:
        from ..mlb_champion_forward import champion_candidates

        totals_candidates, structural_skipped = champion_candidates(
            totals_candidates, Path(ledger_path(config)).parent, PROJECT_ROOT, observed_at
        )
        skipped.extend(structural_skipped)
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
                model_input_snapshot_json=(
                    json.dumps(candidate.model_input_snapshot, sort_keys=True, allow_nan=False)
                    if candidate.model_input_snapshot
                    else None
                ),
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


def _select_mlb_nrfi_market(rows: list[dict]) -> dict | None:
    """Select the latest prospective executable Polymarket NRFI market."""
    valid = [
        row
        for row in rows
        if isinstance(row.get("short"), dict)
        and row["short"].get("ask") is not None
        and isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and 0 < float(row["short"]["ask"]) < 1
        and 0 < float(row["long"]["ask"]) < 1
    ]
    if not valid:
        return None
    return max(
        valid,
        key=lambda row: str(row.get("observed_at_utc") or row.get("transact_time_utc") or ""),
    )


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
    data_root=None,
) -> dict:
    """MLB NRFI / YRFI 1st-inning component run model into Flat and Main ledgers.

    Evaluates decomposed 1st-inning run probabilities for every scheduled MLB game
    on `args_date`. Generates PickRequests for NRFI (under 0.5 runs) or YRFI, priced
    against prospective executable quotes from Polymarket US where available.
    Logs unconditionally to Flat Ledger, and to Main Ledger when eligible.
    """
    from ..data_sources.espn import ESPNClient
    from ..data_sources.polymarket_us import probability_to_american
    from ..learned_forward import _team_matches
    from ..models.mlb_first_inning import (
        FirstInningGameRow,
        MLBFirstInningModel,
        MLBHalfInningInteractionModel,
        MLBHalfInningModel,
    )
    from ..models.mlb_first_inning_live import live_first_inning_features

    champion = None
    try:
        champion = ProductionModelRegistry.load(PROJECT_ROOT).champion("MLB", "nrfi")
    except (KeyError, ValueError, OSError):
        pass
    model_version = champion.model_id if champion is not None else "mlb-nrfi-v4"
    artifact, artifact_error = _load_exact_artifact_contract(model_version)
    if artifact is None:
        artifact, artifact_error = _load_exact_artifact_contract("mlb-nrfi-v3")
        if artifact is None:
            artifact, artifact_error = _load_exact_artifact_contract("mlb-nrfi-v2")
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
            model_version = "mlb-nrfi-v2"
        else:
            model_version = "mlb-nrfi-v3"

    espn_client = client or ESPNClient()
    try:
        scoreboard_data = espn_client.scoreboard("MLB", args_date)
        events = scoreboard_data.get("events", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch ESPN MLB scoreboard for NRFI on %s: %s", args_date, exc)
        events = []

    model: Any
    if artifact.get("method") == "half_inning_interaction":
        model = MLBHalfInningInteractionModel.from_dict(artifact)
    elif artifact.get("method") == "half_inning_logistic":
        model = MLBHalfInningModel.from_dict(artifact)
    else:
        model = MLBFirstInningModel.from_dict(artifact)

    try:
        data_dir = (
            Path(data_root)
            if data_root is not None
            else (Path(ledger_path(config)).parent if config else PROJECT_ROOT / "data")
        )
    except (KeyError, TypeError, ValueError):
        data_dir = PROJECT_ROOT / "data"
    snapshot_path = data_dir / "odds" / "mlb" / args_date / "polymarket_snapshots.jsonl"
    snapshot_rows = _read_polymarket_snapshot_rows(snapshot_path)
    nrfi_snapshot_rows = [row for row in snapshot_rows if row.get("market_type") == "nrfi"]

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
        try:
            from ..domain import parse_utc

            if not event_start_utc or event_decision_dt >= parse_utc(event_start_utc):
                logger.warning("Skipping NRFI event %s: missing start or already started", event_id)
                continue
        except (ValueError, TypeError):
            logger.warning("Skipping NRFI event %s: invalid event start", event_id)
            continue

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
        except Exception as exc:  # noqa: BLE001
            logger.warning("NRFI prediction failed for %s @ %s: %s", away_team, home_team, exc)
            continue

        nrfi_candidates += 1

        if log:
            market_candidates = [
                row
                for row in nrfi_snapshot_rows
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
                and str(row.get("event_start_utc") or "")[:10] == str(event_start_utc)[:10]
            ]
            market = _select_mlb_nrfi_market(market_candidates)
            market_lineage = market.get("_lineage") if market else None

            if market is not None:
                short_ask = float(market["short"]["ask"])
                long_ask = float(market["long"]["ask"])
                edge_nrfi = p_nrfi - short_ask
                edge_yrfi = p_yrfi - long_ask
                if edge_nrfi >= edge_yrfi:
                    pick_selection = "nrfi"
                    pick_prob = p_nrfi
                    ask = short_ask
                    pick_edge = edge_nrfi
                else:
                    pick_selection = "yrfi"
                    pick_prob = p_yrfi
                    ask = long_ask
                    pick_edge = edge_yrfi

                pick_odds = probability_to_american(ask)
                sportsbook = "polymarket_us"
                observed_at_str = (
                    market_lineage["market_quote_observed_at_utc"]
                    if market_lineage is not None
                    else str(market.get("observed_at_utc") or event_decision_dt.isoformat())
                )
                pick_rationale = (
                    f"MLB 1st Inning {pick_selection.upper()}: p={pick_prob:.3f} "
                    f"vs ask {ask:.3f} ({market.get('market_slug')}; edge {pick_edge:+.4f}) "
                    f"({model_version}, {getattr(model, 'fit_n_games', 0)} training games)"
                )
            else:
                if p_yrfi >= 0.50:
                    pick_selection = "yrfi"
                    pick_prob = p_yrfi
                else:
                    pick_selection = "nrfi"
                    pick_prob = p_nrfi
                pick_odds = -110
                sportsbook = "espn"
                observed_at_str = event_decision_dt.isoformat()
                pick_rationale = (
                    f"MLB 1st Inning {pick_selection.upper()}: p={pick_prob:.3f}; "
                    f"no Polymarket quote available — using -110 default odds ({model_version})"
                )

            from ..scalar_artifact_replay import build_snapshot as build_scalar_snapshot

            nrfi_snapshot = build_scalar_snapshot(
                artifact,
                live_features,
                {},
                event_id=event_id,
                event_start_utc=event_start_utc,
                observed_at_utc=event_decision_dt.isoformat(),
            )
            req_nrfi = PickRequest(
                event_start_utc=event_start_utc,
                event_id=event_id,
                league=League.MLB,
                away_team=away_team,
                home_team=home_team,
                market_type=MarketType.NRFI,
                selection=pick_selection,
                line=0.5,
                sportsbook=sportsbook,
                american_odds=pick_odds,
                model_probability=pick_prob,
                model_uncertainty=0.04,
                model_version=model_version,
                rationale=pick_rationale,
                risks="1st inning variance, leadoff home run risk",
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=ModelState.SHADOW_QUALIFIED,
                observed_at_utc=observed_at_str,
                model_artifact_hash=artifact["artifact_hash"],
                calibration_method=artifact.get("method", "first_inning_logistic"),
                calibration_version=model_version,
                calibration_artifact_hash=artifact["artifact_hash"],
                code_revision="scalar-artifact-replay-v1",
                model_input_snapshot_json=json.dumps(nrfi_snapshot, sort_keys=True, allow_nan=False),
                **(market_lineage or {}),
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

            if (
                market is None
                or market_lineage is None
                or not _is_registered_serving_model(
                    req_nrfi.model_version,
                    req_nrfi.league.value,
                    req_nrfi.market_type.value,
                )
            ):
                eligibility = _downgrade_unserved(eligibility)

            # Conviction gating: require max(p_nrfi, p_yrfi) >= 0.555 (|p - 0.50| >= 0.055)
            # and pick_edge >= 0.020 when executable quotes exist.
            is_conviction = (max(p_nrfi, p_yrfi) >= 0.555) and (
                pick_edge >= 0.020 if market is not None else True
            )

            # Log to Flat Ledger: records every candidate for research/flat evidence
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
            is_main_eligible = eligibility.decision == "CALL" and (
                (market is not None and is_conviction)
                or (market is None and eligibility.reason_code == "PAPER_CALL_MODEL_UNVALIDATED")
            )
            if main_ledger is not None and is_main_eligible:
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
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-")
    candidates = [
        row
        for row in rows
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
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
    model_version = "wnba-spread-margin-v2"
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
    margin_sd = float(artifact.get("margin_sd", 13.37))
    elo_weight = float(artifact.get("elo_weight", 0.52))
    trend_weight = float(artifact.get("trend_weight", 0.44))
    rest_weight = float(artifact.get("rest_weight", 0.20))
    home_court_points = float(artifact.get("home_court_points", 2.26))
    model = BasketballModel(
        sport="wnba",
        version=model_version,
        margin_sd=margin_sd,
        total_sd=15.0,
        league="WNBA",
        elo_weight=elo_weight,
        trend_weight=trend_weight,
        rest_weight=rest_weight,
        home_court_points=home_court_points,
    )
    store = FeatureStore(data_root)
    history = store.games_before("wnba", args_date)

    scoreboard = client.scoreboard("WNBA", args_date)
    events = scoreboard.get("events", [])

    snapshot_path = Path(data_root) / "odds" / "wnba" / args_date / "polymarket_snapshots.jsonl"
    snapshot_rows = _read_polymarket_snapshot_rows(snapshot_path)
    spread_rows = [row for row in snapshot_rows if row.get("market_type") == "spread"]

    prior_file = Path(data_root) / "player_priors" / "wnba" / f"{args_date}.json"
    if not prior_file.exists():
        try:
            from ..wnba_availability_evaluation import build_and_save_priors

            build_and_save_priors(
                store=store,
                client=client,
                game_date=args_date,
                data_root=data_root,
                observed_at=observed_at,
            )
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            pass

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

        avail_points_gap = 0.0
        try:
            from ..features.player_availability import matchup_player_availability

            avail = matchup_player_availability(
                data_root=data_root,
                home_team=home_team,
                away_team=away_team,
                game_date=args_date,
                observed_at=observed_at,
                event_start=start,
                event_id=event_id,
            )
            avail_points_gap = float(avail.get("availability_points_gap", 0.0))
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            pass

        upcoming.append(
            UpcomingGame(
                event_id=event_id,
                event_start_utc=str(event["date"]),
                away_team=away_team,
                home_team=home_team,
                spread_away_line=float(market["line"]),
                availability_points_gap=avail_points_gap,
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
        from ..scalar_artifact_replay import build_snapshot as build_scalar_snapshot

        model_snapshot = (
            build_scalar_snapshot(
                artifact,
                prediction.feature_basis,
                prediction.inference_inputs,
                event_id=prediction.event_id,
                event_start_utc=prediction.event_start_utc,
                observed_at_utc=observed_at.isoformat(),
            )
            if prediction.inference_inputs is not None
            else None
        )
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
                "model_input_snapshot": model_snapshot,
            }
        )

    return {
        "sport": "wnba_spread",
        "model_name": "WNBA Spread (composite margin normal CDF)",
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
            risks="Point-in-time composite Elo + trend + rest margin-normal spread model.",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState.SHADOW_QUALIFIED
            if contract.get("model_qualified")
            else ModelState.RESEARCH,
            observed_at_utc=contract["observed_at_utc"],
            model_artifact_hash=contract["model_artifact_hash"],
            calibration_method="margin_normal",
            calibration_version=contract["model_version"],
            calibration_artifact_hash=contract["model_artifact_hash"],
            feature_schema_version=contract["model_version"],
            code_revision=contract["model_artifact_hash"],
            model_input_snapshot_json=json.dumps(
                contract["model_input_snapshot"], sort_keys=True, allow_nan=False
            )
            if contract.get("model_input_snapshot") is not None
            else None,
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
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-tt-", "-tt1h-", "-tt2h-")
    candidates = [
        row
        for row in rows
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
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
    model_version = "wnba-total-margin-v2"
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

    model = BasketballModel(
        sport="wnba",
        version=model_version,
        margin_sd=float(artifact.get("margin_sd", 13.37)),
        total_sd=float(artifact.get("total_sd", 16.0)),
        league="WNBA",
        trend_total_weight=float(artifact.get("trend_total_weight", 0.60)),
        team_total_weight=float(artifact.get("team_total_weight", 0.40)),
    )
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

    prior_file = Path(data_root) / "player_priors" / "wnba" / f"{args_date}.json"
    if not prior_file.exists():
        try:
            from ..wnba_availability_evaluation import build_and_save_priors

            build_and_save_priors(
                store=store,
                client=client,
                game_date=args_date,
                data_root=data_root,
                observed_at=observed_at,
            )
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            pass

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

        avail_total_loss = 0.0
        try:
            from ..features.player_availability import matchup_player_availability

            avail = matchup_player_availability(
                data_root=data_root,
                home_team=home_team,
                away_team=away_team,
                game_date=args_date,
                observed_at=observed_at,
                event_start=start,
                event_id=event_id,
            )
            home_loss = float(avail.get("home_expected_loss_points", 0.0))
            away_loss = float(avail.get("away_expected_loss_points", 0.0))
            avail_total_loss = home_loss + away_loss
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            pass

        upcoming.append(
            UpcomingGame(
                event_id=event_id,
                event_start_utc=str(event["date"]),
                away_team=away_team,
                home_team=home_team,
                total_line=t_line,
                availability_total_loss=avail_total_loss,
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
        from ..scalar_artifact_replay import build_snapshot as build_scalar_snapshot

        model_snapshot = (
            build_scalar_snapshot(
                artifact,
                prediction.feature_basis,
                prediction.inference_inputs,
                event_id=prediction.event_id,
                event_start_utc=prediction.event_start_utc,
                observed_at_utc=observed_at.isoformat(),
            )
            if prediction.inference_inputs is not None
            else None
        )
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
                "model_input_snapshot": model_snapshot,
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
            feature_schema_version="wnba-total-margin-v2",
            code_revision=contract["model_artifact_hash"],
            model_input_snapshot_json=json.dumps(
                contract["model_input_snapshot"], sort_keys=True, allow_nan=False
            )
            if contract.get("model_input_snapshot") is not None
            else None,
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


def _quotes_known_at(rows: list[dict], cutoff: datetime) -> list[dict]:
    """Keep only quotes observed at or before ``cutoff``.

    The day's snapshot file keeps growing after a run starts, so rows stamped
    later than the run would otherwise be priced and then rejected as future.
    """
    known = []
    for row in rows:
        try:
            observed = parse_utc(str(row.get("observed_at_utc") or ""))
        except ValueError:
            continue
        if observed <= cutoff:
            known.append(row)
    return known


def _select_nba_spread_market(rows: list[dict]) -> dict | None:
    """Among alternate NBA spread lines for one event, the main line is the
    one whose long-side ask sits closest to a coin flip -- most balanced line wins."""
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-")
    candidates = [
        row
        for row in rows
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _forecast_nba_spread_slate(data_root, args_date: str, client) -> dict:
    """Price NBA spreads with BasketballModel's normal-CDF margin approach
    (nba-spread-margin-v1), matched against the main (most-balanced)
    Polymarket alternate line per game.
    """
    from ..learned_forward import _team_matches, _teams
    from ..models.basketball import BasketballModel, UpcomingGame

    observed_at = utc_now()
    model_version = "nba-spread-margin-v2"
    artifact, artifact_error = _load_exact_artifact_contract(model_version)
    if artifact is None:
        return {
            "status": "blocked",
            "reason": artifact_error,
            "sport": "nba_spread",
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
    margin_sd = float(artifact.get("margin_sd", 12.0))
    elo_weight = float(artifact.get("elo_weight", 0.35))
    trend_weight = float(artifact.get("trend_weight", 0.65))
    rest_weight = float(artifact.get("rest_weight", 0.20))
    home_court_points = float(artifact.get("home_court_points", 2.30))
    model = BasketballModel(
        sport="nba",
        version=model_version,
        margin_sd=margin_sd,
        total_sd=18.5,
        league="NBA",
        elo_weight=elo_weight,
        trend_weight=trend_weight,
        rest_weight=rest_weight,
        home_court_points=home_court_points,
    )
    store = FeatureStore(data_root)
    history = store.games_before("nba", args_date)

    scoreboard = client.scoreboard("NBA", args_date)
    events = scoreboard.get("events", [])

    snapshot_path = Path(data_root) / "odds" / "nba" / args_date / "polymarket_snapshots.jsonl"
    snapshot_rows = _read_polymarket_snapshot_rows(snapshot_path)
    snapshot_rows = _quotes_known_at(snapshot_rows, observed_at)
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
        candidates = [
            row
            for row in spread_rows
            if _team_matches(away_team, str(row.get("team") or ""))
            and str(row.get("event_start_utc") or "")[:16] == str(event["date"])[:16]
        ]
        market = _select_nba_spread_market(candidates)
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
                league="NBA",
            )
        )
        market_by_event_id[event_id] = market

    predictions = [p for p in model.predict_games(history, upcoming) if p.market_type == "spread"]

    priced_contracts = []
    for prediction in predictions:
        market = market_by_event_id[prediction.event_id]
        market_lineage = market.get("_lineage")
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
        from ..scalar_artifact_replay import build_snapshot as build_scalar_snapshot

        model_snapshot = (
            build_scalar_snapshot(
                artifact,
                prediction.feature_basis,
                prediction.inference_inputs,
                event_id=prediction.event_id,
                event_start_utc=prediction.event_start_utc,
                observed_at_utc=observed_at.isoformat(),
            )
            if prediction.inference_inputs is not None
            else None
        )
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
                "model_input_snapshot": model_snapshot,
            }
        )

    return {
        "sport": "nba_spread",
        "model_name": "NBA Spread (composite margin normal CDF)",
        "model_version": model_version,
        "model_qualified": model_qualified,
        "game_date": args_date,
        "scheduled_games": len(events),
        "market_candidates": len(priced_contracts),
        "priced_contracts": priced_contracts,
        "unmatched": unmatched,
        "observed_at_utc": observed_at.isoformat(),
    }


def _forecast_nba_spread_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    registry,
    bans,
    main_ledger=None,
    flat_ledger=None,
) -> dict:
    """Log NBA spread picks to Main (CALL only) + Flat (every candidate)."""
    from ..data_sources.polymarket_us import probability_to_american

    forecast = _forecast_nba_spread_slate(data_root, args_date, ESPNClient())
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
            league=League.NBA,
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
            risks="Point-in-time composite Elo + trend + rest margin-normal spread model.",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState.SHADOW_QUALIFIED
            if contract.get("model_qualified")
            else ModelState.RESEARCH,
            observed_at_utc=contract["observed_at_utc"],
            model_artifact_hash=contract["model_artifact_hash"],
            calibration_method="margin_normal",
            calibration_version=contract["model_version"],
            calibration_artifact_hash=contract["model_artifact_hash"],
            feature_schema_version=contract["model_version"],
            code_revision=contract["model_artifact_hash"],
            model_input_snapshot_json=json.dumps(
                contract["model_input_snapshot"], sort_keys=True, allow_nan=False
            )
            if contract.get("model_input_snapshot") is not None
            else None,
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
                        flat_ledger, request, eligibility, observed_now, "nba_spread:flat_ledger"
                    )
                    is not None
                ):
                    duplicates.append(contract["event_id"])
                if (
                    main_ledger is not None
                    and eligibility.decision == "CALL"
                    and _append_secondary_ledger(
                        main_ledger, request, eligibility, observed_now, "nba_spread:main_ledger"
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


def _select_nba_total_market(rows: list[dict]) -> dict | None:
    """Select the most balanced total market line for an NBA game."""
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-tt-", "-tt1h-", "-tt2h-")
    candidates = [
        row
        for row in rows
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _forecast_nba_total_slate(data_root, args_date: str, client) -> dict:
    """Price NBA totals with BasketballModel's normal-CDF score approach
    (nba-total-margin-v2), matched against the main (most-balanced)
    Polymarket alternate line per game.
    """
    from ..learned_forward import _team_matches, _teams
    from ..models.basketball import BasketballModel, UpcomingGame

    observed_at = utc_now()
    model_version = "nba-total-margin-v2"
    artifact, artifact_error = _load_exact_artifact_contract(model_version)
    if artifact is None:
        return {
            "status": "blocked",
            "reason": artifact_error,
            "sport": "nba_total",
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

    model = BasketballModel(
        sport="nba",
        version=model_version,
        margin_sd=float(artifact.get("margin_sd", 12.0)),
        total_sd=float(artifact.get("total_sd", 19.2)),
        league="NBA",
        trend_total_weight=float(artifact.get("trend_total_weight", 0.85)),
        team_total_weight=float(artifact.get("team_total_weight", 0.15)),
    )
    store = FeatureStore(data_root)
    history = store.games_before("nba", args_date)

    scoreboard = client.scoreboard("NBA", args_date)
    events = scoreboard.get("events", [])

    snapshot_path = Path(data_root) / "odds" / "nba" / args_date / "polymarket_snapshots.jsonl"
    snapshot_rows = _read_polymarket_snapshot_rows(snapshot_path)
    snapshot_rows = _quotes_known_at(snapshot_rows, observed_at)
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
        market = _select_nba_total_market(candidates)
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
                league="NBA",
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
        from ..scalar_artifact_replay import build_snapshot as build_scalar_snapshot

        model_snapshot = (
            build_scalar_snapshot(
                artifact,
                prediction.feature_basis,
                prediction.inference_inputs,
                event_id=prediction.event_id,
                event_start_utc=prediction.event_start_utc,
                observed_at_utc=observed_at.isoformat(),
            )
            if prediction.inference_inputs is not None
            else None
        )
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
                "model_input_snapshot": model_snapshot,
            }
        )

    return {
        "sport": "nba_total",
        "model_name": "NBA Total (composite trend/team normal CDF)",
        "model_version": model_version,
        "model_qualified": model_qualified,
        "game_date": args_date,
        "scheduled_games": len(events),
        "market_candidates": len(priced_contracts),
        "priced_contracts": priced_contracts,
        "unmatched": unmatched,
        "observed_at_utc": observed_at.isoformat(),
    }


def _forecast_nba_total_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    registry,
    bans,
    main_ledger=None,
    flat_ledger=None,
) -> dict:
    """Log NBA total picks to Main (CALL only) + Flat (every candidate)."""
    from ..data_sources.polymarket_us import probability_to_american

    forecast = _forecast_nba_total_slate(data_root, args_date, ESPNClient())
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
            league=League.NBA,
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
            risks="Point-in-time composite trend + team rolling total-normal model.",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState.SHADOW_QUALIFIED
            if contract.get("model_qualified")
            else ModelState.RESEARCH,
            observed_at_utc=contract["observed_at_utc"],
            model_artifact_hash=contract["model_artifact_hash"],
            calibration_method="total_normal",
            calibration_version=contract["model_version"],
            calibration_artifact_hash=contract["model_artifact_hash"],
            feature_schema_version=contract["model_version"],
            code_revision=contract["model_artifact_hash"],
            model_input_snapshot_json=json.dumps(
                contract["model_input_snapshot"], sort_keys=True, allow_nan=False
            )
            if contract.get("model_input_snapshot") is not None
            else None,
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
                        flat_ledger, request, eligibility, observed_now, "nba_total:flat_ledger"
                    )
                    is not None
                ):
                    duplicates.append(contract["event_id"])
                if (
                    main_ledger is not None
                    and eligibility.decision == "CALL"
                    and _append_secondary_ledger(
                        main_ledger, request, eligibility, observed_now, "nba_total:main_ledger"
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


def _select_nfl_spread_market(candidates: list[dict]) -> dict | None:
    """Select the most balanced spread market line for an NFL game."""
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-tt-", "-tt1h-", "-tt2h-")
    valid = [
        row
        for row in candidates
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
    ]
    if not valid:
        return None
    return min(valid, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _nfl_is_neutral_site(event: dict) -> bool:
    """Determine whether an NFL game is played at a neutral site (international series, etc.)."""
    comp = (event.get("competitions") or [{}])[0]
    is_neutral_site = bool(comp.get("neutralSite", False))
    if not is_neutral_site:
        venue = comp.get("venue") or {}
        address = venue.get("address") or {}
        country = str(address.get("country") or "").strip().upper()
        city = str(address.get("city") or "").strip().lower()
        venue_name = str(venue.get("fullName") or "").strip().lower()
        if (
            country
            and country not in ("USA", "US", "UNITED STATES")
            or any(
                kw in city
                for kw in ("london", "munich", "frankfurt", "são paulo", "sao paulo", "mexico city")
            )
            or any(
                kw in venue_name
                for kw in (
                    "tottenham",
                    "wembley",
                    "twickenham",
                    "allianz arena",
                    "deutsche bank park",
                    "arena corinthians",
                    "estadio azteca",
                )
            )
        ):
            is_neutral_site = True
    return is_neutral_site


def _nfl_situational_features(
    history: list,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Extract point-in-time last game dates and starting QBs for NFL teams."""
    team_last_date: dict[str, Any] = {}
    team_starters: dict[str, str] = {}
    for g in history:
        try:
            g_dt = parse_utc(str(g.event_start_utc)).date()
            team_last_date[g.home_team] = g_dt
            team_last_date[g.away_team] = g_dt
            if getattr(g, "home_starter_name", None) and g.home_starter_name != "Unknown":
                team_starters[g.home_team] = g.home_starter_name
            if getattr(g, "away_starter_name", None) and g.away_starter_name != "Unknown":
                team_starters[g.away_team] = g.away_starter_name
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return team_last_date, team_starters


def _nfl_venue_weather(event: dict) -> tuple[bool, float, float]:
    """Extract dome status, wind speed, and temperature from NFL event."""
    comp = (event.get("competitions") or [{}])[0]
    venue = comp.get("venue") or {}
    is_dome = bool(venue.get("indoor", False)) or any(
        d in str(venue.get("fullName") or "").lower()
        for d in (
            "dome",
            "mercedes-benz",
            "ford field",
            "allegiant",
            "at&t stadium",
            "lucas oil",
            "nrg stadium",
            "u.s. bank",
            "state farm",
            "sofi",
        )
    )
    weather = comp.get("weather") or {}
    wind_mph = float(weather.get("windSpeed") or (0.0 if is_dome else 5.0))
    temperature_f = float(weather.get("temperature") or (70.0 if is_dome else 65.0))
    return is_dome, wind_mph, temperature_f


NFL_DIVISIONS: dict[str, str] = {
    "Buffalo Bills": "AFC_East",
    "Miami Dolphins": "AFC_East",
    "New England Patriots": "AFC_East",
    "New York Jets": "AFC_East",
    "Baltimore Ravens": "AFC_North",
    "Cincinnati Bengals": "AFC_North",
    "Cleveland Browns": "AFC_North",
    "Pittsburgh Steelers": "AFC_North",
    "Houston Texans": "AFC_South",
    "Indianapolis Colts": "AFC_South",
    "Jacksonville Jaguars": "AFC_South",
    "Tennessee Titans": "AFC_South",
    "Denver Broncos": "AFC_West",
    "Kansas City Chiefs": "AFC_West",
    "Las Vegas Raiders": "AFC_West",
    "Los Angeles Chargers": "AFC_West",
    "Dallas Cowboys": "NFC_East",
    "New York Giants": "NFC_East",
    "Philadelphia Eagles": "NFC_East",
    "Washington Commanders": "NFC_East",
    "Chicago Bears": "NFC_North",
    "Detroit Lions": "NFC_North",
    "Green Bay Packers": "NFC_North",
    "Minnesota Vikings": "NFC_North",
    "Atlanta Falcons": "NFC_South",
    "Carolina Panthers": "NFC_South",
    "New Orleans Saints": "NFC_South",
    "Tampa Bay Buccaneers": "NFC_South",
    "Arizona Cardinals": "NFC_West",
    "Los Angeles Rams": "NFC_West",
    "San Francisco 49ers": "NFC_West",
    "Seattle Seahawks": "NFC_West",
}


def _nfl_is_division_game(home_team: str, away_team: str) -> bool:
    """Return True if both teams belong to the same NFL division."""
    h_div = NFL_DIVISIONS.get(home_team)
    a_div = NFL_DIVISIONS.get(away_team)
    return h_div is not None and h_div == a_div


def _load_nfl_injuries_known_at(data_root: str | Path, observed_at: datetime) -> list[dict[str, Any]]:
    """Load latest timestamped injury entries from espn_injury_snapshots strictly <= observed_at."""
    snap_dir = Path(data_root) / "availability" / "nfl" / "espn_injury_snapshots"
    if not snap_dir.exists():
        return []
    candidates = []
    for p in snap_dir.glob("*/*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            obs = parse_utc(data.get("observed_at_utc", ""))
            if obs <= observed_at:
                candidates.append((obs, data.get("entries", [])))
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            continue
    if not candidates:
        return []
    candidates.sort(key=lambda c: c[0])
    return candidates[-1][1]


def _forecast_nfl_spread_slate(
    data_root, args_date: str, client, *, observed_at: datetime | None = None
) -> dict:
    """Price NFL spreads using NFLStructuralV5Engine (nfl-spread-structural-v5)."""
    from ..features.base import FeatureStore
    from ..features.nfl_qb_oline import check_nfl_qb_injury_status
    from ..learned_forward import _team_matches, _teams
    from ..models.nfl_structural_v5 import NFLStructuralV5Engine
    from ..scalar_artifact_replay import build_snapshot as build_scalar_snapshot

    observed_at = observed_at or utc_now()
    injury_entries = _load_nfl_injuries_known_at(data_root, observed_at)
    model_version = "nfl-spread-structural-v5"
    artifact, artifact_error = _load_exact_artifact_contract(model_version)
    if artifact is None:
        return {
            "status": "blocked",
            "reason": artifact_error,
            "sport": "nfl_spread",
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
    margin_sd = float(artifact.get("margin_sd", 13.8))
    engine = NFLStructuralV5Engine(spread_sd=margin_sd)

    store = FeatureStore(data_root)
    history = store.games_before("nfl", args_date)
    team_last_date, team_starters = _nfl_situational_features(history)

    scoreboard = client.scoreboard("NFL", args_date)
    events = scoreboard.get("events", [])

    snapshot_path = Path(data_root) / "odds" / "nfl" / args_date / "polymarket_snapshots.jsonl"
    snapshot_rows = _read_polymarket_snapshot_rows(snapshot_path)
    spread_rows = _quotes_known_at(
        [
            row
            for row in snapshot_rows
            if row.get("market_type") == "spread" or "spread" in str(row.get("market_slug", "")).lower()
        ],
        observed_at,
    )

    priced_contracts = []
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
            for row in spread_rows
            if (
                _team_matches(away_team, str(row.get("team") or ""))
                or _team_matches(home_team, str(row.get("team") or ""))
            )
            and str(row.get("event_start_utc") or "")[:16] == str(event["date"])[:16]
        ]
        market = _select_nfl_spread_market(candidates)
        first_odds: dict[str, Any] = {}
        if market is None:
            comp = (event.get("competitions") or [{}])[0]
            odds_list = comp.get("odds") or []
            first_odds = odds_list[0] if odds_list else {}
            espn_spread = first_odds.get("spread")
            if espn_spread is None:
                unmatched.append({"event_id": event_id, "reason": "no matched spread market"})
                continue
            try:
                spread_home_line = float(espn_spread)
            except (ValueError, TypeError):
                unmatched.append({"event_id": event_id, "reason": "invalid espn spread line"})
                continue
            anchor_team = home_team
            anchor_line = spread_home_line
            anchor_is_away = False
            market_lineage = None
            sportsbook = "espn_consensus"
            market_slug = f"nfl-spread-{event_id}"
        else:
            market_lineage = market.get("_lineage")
            anchor_team = str(market.get("team") or "")
            anchor_line = float(market["line"])
            anchor_is_away = _team_matches(away_team, anchor_team)
            spread_home_line = -anchor_line if anchor_is_away else anchor_line
            sportsbook = "polymarket_us"
            market_slug = market.get("market_slug")

        is_neutral_site = _nfl_is_neutral_site(event)
        is_dome, wind_mph, temperature_f = _nfl_venue_weather(event)
        is_division_game = _nfl_is_division_game(home_team, away_team)

        event_date = start.date()
        h_last = team_last_date.get(home_team)
        a_last = team_last_date.get(away_team)
        home_rest_days = max(4, min(14, (event_date - h_last).days)) if h_last else 7
        away_rest_days = max(4, min(14, (event_date - a_last).days)) if a_last else 7

        h_qb = team_starters.get(home_team)
        a_qb = team_starters.get(away_team)

        _, _h_stat, h_qb_pen = check_nfl_qb_injury_status(home_team, h_qb or "", injury_entries)
        _, _a_stat, a_qb_pen = check_nfl_qb_injury_status(away_team, a_qb or "", injury_entries)

        eff = NFLEfficiencyStore.get_default(data_root).get_matchup_efficiency(
            home_team=home_team,
            away_team=away_team,
            as_of_date=start,
            home_qb=h_qb,
            away_qb=a_qb,
        )

        h_qb_tier_adj = eff.home_qb_tier_adj - h_qb_pen
        a_qb_tier_adj = eff.away_qb_tier_adj - a_qb_pen

        fc = engine.forecast_game(
            home_team=home_team,
            away_team=away_team,
            home_off_epa=eff.home_off_epa,
            home_def_epa=eff.home_def_epa,
            away_off_epa=eff.away_off_epa,
            away_def_epa=eff.away_def_epa,
            home_qb_tier_adj=h_qb_tier_adj,
            away_qb_tier_adj=a_qb_tier_adj,
            wind_mph=wind_mph,
            temperature_f=temperature_f,
            is_dome=is_dome,
            home_rest_days=home_rest_days,
            away_rest_days=away_rest_days,
            spread_home_line=spread_home_line,
            is_neutral_site=is_neutral_site,
        )

        cover_prob = fc.prob_away_cover if anchor_is_away else fc.prob_home_cover
        if cover_prob >= 0.5:
            selection = "away" if anchor_is_away else "home"
            model_probability = cover_prob
            if market is not None:
                ask = market["long"]["ask"]
            else:
                ps_obj = first_odds.get("pointSpread") or {}
                sel_ps = ps_obj.get("away") if selection == "away" else ps_obj.get("home")
                close_odds = (sel_ps or {}).get("close", {}).get("odds")
                try:
                    ask = (
                        round(implied_probability(int(close_odds)), 6)
                        if close_odds and str(close_odds).lstrip("-+").isdigit()
                        else STANDARD_LAY_ASK
                    )
                except (ValueError, TypeError):
                    ask = STANDARD_LAY_ASK
            line = anchor_line
        else:
            selection = "home" if anchor_is_away else "away"
            model_probability = 1.0 - cover_prob
            if market is not None:
                ask = market["short"]["ask"]
            else:
                ps_obj = first_odds.get("pointSpread") or {}
                sel_ps = ps_obj.get("home") if selection == "home" else ps_obj.get("away")
                close_odds = (sel_ps or {}).get("close", {}).get("odds")
                try:
                    ask = (
                        round(implied_probability(int(close_odds)), 6)
                        if close_odds and str(close_odds).lstrip("-+").isdigit()
                        else STANDARD_LAY_ASK
                    )
                except (ValueError, TypeError):
                    ask = STANDARD_LAY_ASK
            line = -anchor_line

        if ask is None or not 0 < float(ask) < 1:
            continue

        inference_inputs = {
            "mean": fc.projected_margin_home,
            "sd": margin_sd,
            "line": -spread_home_line,
        }
        feature_basis = {
            "home_team": home_team,
            "away_team": away_team,
            "spread_home_line": spread_home_line,
            "home_qb": h_qb or "Unknown",
            "away_qb": a_qb or "Unknown",
            "home_rest_days": home_rest_days,
            "away_rest_days": away_rest_days,
            "is_dome": is_dome,
            "is_division_game": is_division_game,
        }
        model_snapshot = build_scalar_snapshot(
            artifact,
            feature_basis,
            inference_inputs,
            event_id=event_id,
            event_start_utc=str(event["date"]),
            observed_at_utc=observed_at.isoformat(),
        )

        priced_contracts.append(
            {
                "event_id": event_id,
                "event_start_utc": str(event["date"]),
                "away_team": away_team,
                "home_team": home_team,
                "market_type": "spread",
                "selection": selection,
                "line": line,
                "executable_ask": float(ask),
                "sportsbook": sportsbook,
                "model_probability": round(model_probability, 6),
                "model_uncertainty": 0.08,
                "model_version": model_version,
                "model_artifact_hash": model_artifact_hash,
                "model_qualified": model_qualified,
                "rationale": (
                    f"NFL Structural v5 Spread projection. Projected margin {fc.projected_margin_home:+.1f} "
                    f"(Rest: {home_rest_days}v{away_rest_days}d, Dome: {is_dome})."
                ),
                "market_slug": market_slug,
                "observed_at_utc": (
                    market_lineage["market_quote_observed_at_utc"]
                    if market_lineage is not None
                    else observed_at.isoformat()
                ),
                "market_lineage": market_lineage,
                "model_input_snapshot": model_snapshot,
            }
        )

    return {
        "sport": "nfl_spread",
        "model_name": "NFL Spread (Structural v5)",
        "model_version": model_version,
        "model_qualified": model_qualified,
        "game_date": args_date,
        "scheduled_games": len(events),
        "market_candidates": len(priced_contracts),
        "priced_contracts": priced_contracts,
        "unmatched": unmatched,
        "observed_at_utc": observed_at.isoformat(),
    }


def _forecast_nfl_spread_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    registry,
    bans,
    main_ledger=None,
    flat_ledger=None,
    force: bool = False,
    observed_at: datetime | None = None,
) -> dict:
    """Log NFL spread picks to Main (CALL only) + Flat (every candidate)."""
    decision_observed_at = observed_at or (
        utc_now() if not force else datetime.strptime(args_date, "%Y-%m-%d").replace(tzinfo=UTC)
    )
    forecast = _forecast_nfl_spread_slate(
        data_root, args_date, ESPNClient(), observed_at=decision_observed_at
    )
    exposure_source = flat_ledger or main_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    observed_now = decision_observed_at
    logged: list[dict] = []
    duplicates: list[str] = []
    main_duplicates: list[str] = []
    skipped: list[dict] = []

    for contract in forecast["priced_contracts"]:
        ask = contract["executable_ask"]
        contract_sportsbook = str(contract.get("sportsbook") or "polymarket_us")
        request = PickRequest(
            event_start_utc=contract["event_start_utc"],
            event_id=contract["event_id"],
            league=League.NFL,
            away_team=contract["away_team"],
            home_team=contract["home_team"],
            market_type=MarketType.SPREAD,
            selection=contract["selection"],
            line=contract["line"],
            sportsbook=contract_sportsbook,
            american_odds=probability_to_american(ask),
            model_probability=contract["model_probability"],
            model_uncertainty=contract["model_uncertainty"],
            model_version=contract["model_version"],
            rationale=(f"{contract['rationale']} Executable ask {ask:.4f} ({contract['market_slug']})."),
            risks="NFL Structural v5 joint distribution spread model.",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState.SHADOW_QUALIFIED
            if contract.get("model_qualified")
            else ModelState.RESEARCH,
            observed_at_utc=contract["observed_at_utc"],
            model_artifact_hash=contract["model_artifact_hash"],
            calibration_method="margin_normal",
            calibration_version=contract["model_version"],
            calibration_artifact_hash=contract["model_artifact_hash"],
            feature_schema_version="2",
            code_revision=contract["model_artifact_hash"],
            model_input_snapshot_json=json.dumps(
                contract["model_input_snapshot"], sort_keys=True, allow_nan=False
            )
            if contract.get("model_input_snapshot") is not None
            else None,
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
                    or (contract_sportsbook == "polymarket_us" and contract.get("market_lineage") is None)
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
                        flat_ledger, request, eligibility, observed_now, "nfl_spread:flat_ledger"
                    )
                    is not None
                ):
                    duplicates.append(contract["event_id"])
                contract_edge = float(contract["model_probability"]) - float(contract["executable_ask"])
                if (
                    main_ledger is not None
                    and eligibility.decision == "CALL"
                    and 0.02 <= contract_edge <= 0.08
                    and _append_secondary_ledger(
                        main_ledger, request, eligibility, observed_now, "nfl_spread:main_ledger"
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


def _select_nfl_total_market(candidates: list[dict]) -> dict | None:
    """Select the most balanced total market line for an NFL game."""
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-tt-", "-tt1h-", "-tt2h-")
    valid = [
        row
        for row in candidates
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
    ]
    if not valid:
        return None
    return min(valid, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _forecast_nfl_total_slate(
    data_root, args_date: str, client, *, observed_at: datetime | None = None
) -> dict:
    """Price NFL totals using NFLStructuralV5Engine (nfl-total-structural-v5)."""

    from ..features.base import FeatureStore
    from ..features.nfl_qb_oline import check_nfl_qb_injury_status
    from ..learned_forward import _team_matches, _teams
    from ..models.nfl_structural_v5 import NFLStructuralV5Engine
    from ..scalar_artifact_replay import build_snapshot as build_scalar_snapshot

    observed_at = observed_at or utc_now()
    injury_entries = _load_nfl_injuries_known_at(data_root, observed_at)
    model_version = "nfl-total-structural-v5"
    artifact, artifact_error = _load_exact_artifact_contract(model_version)
    if artifact is None:
        return {
            "status": "blocked",
            "reason": artifact_error,
            "sport": "nfl_total",
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
    total_sd = float(artifact.get("total_sd", 14.2))
    league_avg_total = float(artifact.get("league_avg_total", 45.5))
    engine = NFLStructuralV5Engine(total_sd=total_sd, league_avg_total=league_avg_total)

    store = FeatureStore(data_root)
    history = store.games_before("nfl", args_date)
    team_last_date, team_starters = _nfl_situational_features(history)

    scoreboard = client.scoreboard("NFL", args_date)
    events = scoreboard.get("events", [])

    snapshot_path = Path(data_root) / "odds" / "nfl" / args_date / "polymarket_snapshots.jsonl"
    snapshot_rows = _read_polymarket_snapshot_rows(snapshot_path)
    total_rows = _quotes_known_at(
        [
            row
            for row in snapshot_rows
            if row.get("market_type") in ("total", "over_under")
            or "total" in str(row.get("market_slug", "")).lower()
            or "over" in str(row.get("market_slug", "")).lower()
        ],
        observed_at,
    )

    priced_contracts = []
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
                or any(
                    w in str(row.get("event_title") or "").casefold()
                    or w in str(row.get("event_slug") or "").casefold()
                    or w in str(row.get("market_slug") or "").casefold()
                    for w in [w for w in away_team.casefold().split() if len(w) > 2]
                )
            )
            and str(row.get("event_start_utc") or "")[:16] == str(event["date"])[:16]
        ]
        market = _select_nfl_total_market(candidates)
        first_odds: dict[str, Any] = {}
        if market is None:
            comp = (event.get("competitions") or [{}])[0]
            odds_list = comp.get("odds") or []
            first_odds = odds_list[0] if odds_list else {}
            espn_total = first_odds.get("overUnder")
            if espn_total is None:
                unmatched.append({"event_id": event_id, "reason": "no matched total market"})
                continue
            try:
                t_line = float(espn_total)
            except (ValueError, TypeError):
                unmatched.append({"event_id": event_id, "reason": "invalid espn overUnder"})
                continue
            market_lineage = None
            sportsbook = "espn_consensus"
            market_slug = f"nfl-total-{event_id}"
        else:
            try:
                t_line = float(market["line"])
            except (KeyError, TypeError, ValueError):
                continue
            market_lineage = market.get("_lineage")
            sportsbook = "polymarket_us"
            market_slug = market.get("market_slug")

        is_neutral_site = _nfl_is_neutral_site(event)
        is_dome, wind_mph, temperature_f = _nfl_venue_weather(event)
        is_division_game = _nfl_is_division_game(home_team, away_team)

        event_date = start.date()
        h_last = team_last_date.get(home_team)
        a_last = team_last_date.get(away_team)
        home_rest_days = max(4, min(14, (event_date - h_last).days)) if h_last else 7
        away_rest_days = max(4, min(14, (event_date - a_last).days)) if a_last else 7

        h_qb = team_starters.get(home_team)
        a_qb = team_starters.get(away_team)

        _, _h_stat, h_qb_pen = check_nfl_qb_injury_status(home_team, h_qb or "", injury_entries)
        _, _a_stat, a_qb_pen = check_nfl_qb_injury_status(away_team, a_qb or "", injury_entries)

        eff = NFLEfficiencyStore.get_default(data_root).get_matchup_efficiency(
            home_team=home_team,
            away_team=away_team,
            as_of_date=start,
            home_qb=h_qb,
            away_qb=a_qb,
        )

        h_qb_tier_adj = eff.home_qb_tier_adj - h_qb_pen
        a_qb_tier_adj = eff.away_qb_tier_adj - a_qb_pen

        fc = engine.forecast_game(
            home_team=home_team,
            away_team=away_team,
            home_off_epa=eff.home_off_epa,
            home_def_epa=eff.home_def_epa,
            away_off_epa=eff.away_off_epa,
            away_def_epa=eff.away_def_epa,
            home_qb_tier_adj=h_qb_tier_adj,
            away_qb_tier_adj=a_qb_tier_adj,
            wind_mph=wind_mph,
            temperature_f=temperature_f,
            is_dome=is_dome,
            home_rest_days=home_rest_days,
            away_rest_days=away_rest_days,
            total_line=t_line,
            is_neutral_site=is_neutral_site,
        )

        over_prob = fc.prob_over
        if over_prob >= 0.5:
            selection = "over"
            model_probability = over_prob
            if market is not None:
                ask = market["long"]["ask"]
            else:
                tot_obj = first_odds.get("total") or {}
                close_odds = (tot_obj.get("over") or {}).get("close", {}).get("odds")
                try:
                    ask = (
                        round(implied_probability(int(close_odds)), 6)
                        if close_odds and str(close_odds).lstrip("-+").isdigit()
                        else STANDARD_LAY_ASK
                    )
                except (ValueError, TypeError):
                    ask = STANDARD_LAY_ASK
            line = t_line
        else:
            selection = "under"
            model_probability = 1.0 - over_prob
            if market is not None:
                ask = market["short"]["ask"]
            else:
                tot_obj = first_odds.get("total") or {}
                close_odds = (tot_obj.get("under") or {}).get("close", {}).get("odds")
                try:
                    ask = (
                        round(implied_probability(int(close_odds)), 6)
                        if close_odds and str(close_odds).lstrip("-+").isdigit()
                        else STANDARD_LAY_ASK
                    )
                except (ValueError, TypeError):
                    ask = STANDARD_LAY_ASK
            line = t_line

        if ask is None or not 0 < float(ask) < 1:
            continue

        inference_inputs = {
            "mean": fc.projected_total,
            "sd": total_sd,
            "line": t_line,
        }
        feature_basis = {
            "home_team": home_team,
            "away_team": away_team,
            "total_line": t_line,
            "home_qb": h_qb or "Unknown",
            "away_qb": a_qb or "Unknown",
            "home_rest_days": home_rest_days,
            "away_rest_days": away_rest_days,
            "is_dome": is_dome,
            "is_division_game": is_division_game,
            "short_week_penalty": fc.short_week_penalty,
        }
        model_snapshot = build_scalar_snapshot(
            artifact,
            feature_basis,
            inference_inputs,
            event_id=event_id,
            event_start_utc=str(event["date"]),
            observed_at_utc=observed_at.isoformat(),
        )

        tnf_note = (
            f" TNF short-week drag: -{fc.short_week_penalty:.1f} pts ({home_rest_days}d/{away_rest_days}d rest)."
            if fc.short_week_penalty > 0
            else ""
        )
        priced_contracts.append(
            {
                "event_id": event_id,
                "event_start_utc": str(event["date"]),
                "away_team": away_team,
                "home_team": home_team,
                "market_type": "total",
                "selection": selection,
                "line": line,
                "executable_ask": float(ask),
                "sportsbook": sportsbook,
                "model_probability": round(model_probability, 6),
                "model_uncertainty": 0.08,
                "model_version": model_version,
                "model_artifact_hash": model_artifact_hash,
                "model_qualified": model_qualified,
                "rationale": (
                    f"NFL Structural v5 Total projection. Projected total {fc.projected_total:.1f} "
                    f"(Dome: {is_dome}, Weather: {wind_mph:.0f}mph/{temperature_f:.0f}F).{tnf_note}"
                ),
                "market_slug": market_slug,
                "observed_at_utc": (
                    market_lineage["market_quote_observed_at_utc"]
                    if market_lineage is not None
                    else observed_at.isoformat()
                ),
                "market_lineage": market_lineage,
                "model_input_snapshot": model_snapshot,
            }
        )

    return {
        "sport": "nfl_total",
        "model_name": "NFL Total (Structural v5)",
        "model_version": model_version,
        "model_qualified": model_qualified,
        "game_date": args_date,
        "scheduled_games": len(events),
        "market_candidates": len(priced_contracts),
        "priced_contracts": priced_contracts,
        "unmatched": unmatched,
        "observed_at_utc": observed_at.isoformat(),
    }


def _forecast_nfl_total_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    registry,
    bans,
    main_ledger=None,
    flat_ledger=None,
    force: bool = False,
    observed_at: datetime | None = None,
) -> dict:
    """Log NFL total picks to Main (CALL only) + Flat (every candidate)."""
    decision_observed_at = observed_at or (
        utc_now() if not force else datetime.strptime(args_date, "%Y-%m-%d").replace(tzinfo=UTC)
    )
    forecast = _forecast_nfl_total_slate(data_root, args_date, ESPNClient(), observed_at=decision_observed_at)
    exposure_source = flat_ledger or main_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    observed_now = decision_observed_at
    logged: list[dict] = []
    duplicates: list[str] = []
    main_duplicates: list[str] = []
    skipped: list[dict] = []

    for contract in forecast["priced_contracts"]:
        ask = contract["executable_ask"]
        contract_sportsbook = str(contract.get("sportsbook") or "polymarket_us")
        request = PickRequest(
            event_start_utc=contract["event_start_utc"],
            event_id=contract["event_id"],
            league=League.NFL,
            away_team=contract["away_team"],
            home_team=contract["home_team"],
            market_type=MarketType.TOTAL,
            selection=contract["selection"],
            line=contract["line"],
            sportsbook=contract_sportsbook,
            american_odds=probability_to_american(ask),
            model_probability=contract["model_probability"],
            model_uncertainty=contract["model_uncertainty"],
            model_version=contract["model_version"],
            rationale=(f"{contract['rationale']} Executable ask {ask:.4f} ({contract['market_slug']})."),
            risks="NFL Structural v5 joint distribution total model.",
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState.SHADOW_QUALIFIED
            if contract.get("model_qualified")
            else ModelState.RESEARCH,
            observed_at_utc=contract["observed_at_utc"],
            model_artifact_hash=contract["model_artifact_hash"],
            calibration_method="total_normal",
            calibration_version=contract["model_version"],
            calibration_artifact_hash=contract["model_artifact_hash"],
            feature_schema_version="2",
            code_revision=contract["model_artifact_hash"],
            model_input_snapshot_json=json.dumps(
                contract["model_input_snapshot"], sort_keys=True, allow_nan=False
            )
            if contract.get("model_input_snapshot") is not None
            else None,
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
                    or (contract_sportsbook == "polymarket_us" and contract.get("market_lineage") is None)
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
                        flat_ledger, request, eligibility, observed_now, "nfl_total:flat_ledger"
                    )
                    is not None
                ):
                    duplicates.append(contract["event_id"])
                contract_edge = float(contract["model_probability"]) - float(contract["executable_ask"])
                if (
                    main_ledger is not None
                    and eligibility.decision == "CALL"
                    and 0.02 <= contract_edge <= 0.08
                    and _append_secondary_ledger(
                        main_ledger, request, eligibility, observed_now, "nfl_total:main_ledger"
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
        # Main ledger: ONLY production sports (MLB, WNBA, NFL) — everything else goes to flat/research.
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
                    Path(
                        quote.get("_archive_path")
                        or data_root / "odds" / sport / args_date / "polymarket_snapshots.jsonl"
                    ),
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
            min_edge = float(model_config.get("min_edge", 0.02))
            max_edge = float(model_config.get("max_edge", 0.08))
            model_edge = None
            if quote is not None:
                model_edge = candidate.model_probability - quote["executable_ask"]
                if model_edge < min_edge:
                    edge_pct = f"{min_edge * 100:.0f}%"
                    edge_blocked.append(
                        {
                            "event_id": candidate.event_id,
                            "reason": f"model edge {model_edge:.4f} below {edge_pct} minimum over executable ask {quote['executable_ask']:.4f} — logged anyway, operator review",
                        }
                    )
                elif model_edge > max_edge:
                    edge_blocked.append(
                        {
                            "event_id": candidate.event_id,
                            "reason": f"model edge {model_edge:.4f} above {max_edge * 100:.0f}% maximum over executable ask {quote['executable_ask']:.4f} — extreme edge anomaly protection",
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
                model_input_snapshot_json=(
                    json.dumps(candidate.model_input_snapshot, sort_keys=True, allow_nan=False)
                    if getattr(candidate, "model_input_snapshot", None) is not None
                    else None
                ),
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
                    if (
                        eligibility.decision == "CALL"
                        and quote is not None
                        and model_edge is not None
                        and not (min_edge <= model_edge <= max_edge)
                    ):
                        eligibility = replace(
                            eligibility,
                            record_type=RecordType.RESEARCH_OBSERVATION,
                            decision="NO_CALL" if not flat_mode else "CALL",
                            reason_code="PAPER_CALL_EDGE_OUT_OF_BOUNDS",
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

        from ..research_io import identity_key as _identity_key

        selected_team = str(best_side["team"])
        # Polymarket side ordering is arbitrary for venue-neutral esports;
        # teams[0]/teams[1] map to ledger home/away consistently with
        # settlement, which reconstructs the selected team the same way.
        teams = list(contract["teams"])
        home_team = teams[0]
        away_team = teams[1]
        m_type_str = str(contract.get("market_type") or "moneyline")
        m_type = MarketType(m_type_str)

        # Persist the selected contract exactly; never fabricate a default line
        # or recover identity from an ambiguous team-name substring.
        try:
            if m_type_str == "total":
                label = selected_team.strip().lower()
                if label not in {"over", "under"}:
                    raise ValueError("unresolved total selection")
                selection = label
                m_line = float(contract["line"])
                if m_line not in {2.5, 3.5, 4.5}:
                    raise ValueError("unsupported exact total line")
            elif m_type_str == "spread":
                import re

                label = re.fullmatch(r"(.+?)\s+([+-](?:1\.5|2\.5))", selected_team.strip())
                if label is None:
                    raise ValueError("unresolved signed spread selection")
                selected_key = _identity_key(label.group(1))
                team_keys = [_identity_key(home_team), _identity_key(away_team)]
                if team_keys.count(selected_key) != 1:
                    raise ValueError("unresolved spread team")
                selection = "home" if selected_key == team_keys[0] else "away"
                m_line = float(label.group(2))
                if abs(float(contract["line"])) != abs(m_line):
                    raise ValueError("spread selection/contract line mismatch")
            else:
                team_keys = [_identity_key(home_team), _identity_key(away_team)]
                selected_key = _identity_key(selected_team)
                if team_keys.count(selected_key) != 1:
                    raise ValueError("unresolved moneyline team")
                selection = "home" if selected_key == team_keys[0] else "away"
                m_line = None
        except (KeyError, TypeError, ValueError) as error:
            errors.append(
                {"event_id": contract.get("event_id"), "reason": f"Invalid exact contract: {error}"}
            )
            continue

        american_odds = probability_to_american(ask)
        from ..esports_replay import build_snapshot as build_esports_snapshot

        try:
            esports_snapshot_json = (
                json.dumps(build_esports_snapshot(contract), sort_keys=True, allow_nan=False)
                if contract.get("inference_inputs") is not None and m_type_str == "moneyline"
                else None
            )
        except (ValueError, KeyError, TypeError) as error:
            errors.append({"event_id": contract.get("event_id"), "reason": f"Invalid replay inputs: {error}"})
            continue
        contract_model_version = str(contract.get("model_version") or forecast["model_version"])
        if esports_snapshot_json is None and contract.get("observed_at_utc"):
            esports_snapshot_json = contract_snapshot_json(
                {**contract, "market_type": m_type.value, "model_version": contract_model_version},
                sport=league.value,
                selection=selection,
                line=m_line,
                probability=round(model_prob, 6),
                observed_at_utc=str(contract["observed_at_utc"]),
                artifact_hash=str(contract.get("artifact_hash", "")),
            )
        request = PickRequest(
            event_start_utc=str(contract["event_start_utc"]),
            event_id=str(contract["event_id"]),
            league=league,
            away_team=away_team,
            home_team=home_team,
            market_type=m_type,
            selection=selection,
            line=m_line,
            sportsbook="polymarket_us",
            american_odds=american_odds,
            model_probability=round(model_prob, 6),
            model_uncertainty=None,
            model_version=contract_model_version,
            rationale=(
                f"Neutral Elo baseline; executable ask {ask:.4f} (market_slug={contract['market_slug']})."
            ),
            risks=(
                "Paper observation; "
                f"actionable_inputs_valid={model_inputs_valid}; "
                f"input_ineligibility={contract.get('gated_research_ineligibility_reason') or 'none'}; "
                f"series_format_source={contract.get('series_format_source') or 'not_recorded'}; "
                f"team_resolution_methods={contract.get('team_resolution_methods') or 'not_recorded'}."
            ),
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_state=ModelState(configured_state),
            observed_at_utc=str(contract.get("observed_at_utc") or "") or None,
            model_artifact_hash=str(contract.get("artifact_hash", "")),
            calibration_method="neutral_elo",
            model_input_snapshot_json=esports_snapshot_json,
            calibration_version=contract_model_version,
            calibration_artifact_hash=str(contract.get("artifact_hash", "")),
            code_revision=contract_model_version,
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
        m_type_str = str(contract.get("market_type") or "moneyline")
        m_type = MarketType(m_type_str)
        m_line = None if contract.get("line") is None else float(contract["line"])

        selection = "home" if pick_is_home else "away"
        if m_type_str == "total":
            selection = "over" if "over" in selected_team.lower() else "under"
        elif m_type_str == "spread":
            selection = "home" if home_team in selected_team else "away"

        american_odds = probability_to_american(ask)
        from ..international_replay import build_snapshot as build_international_snapshot

        try:
            international_snapshot_json = (
                json.dumps(
                    build_international_snapshot(contract, contract["model_inputs_observed_at_utc"]),
                    sort_keys=True,
                    allow_nan=False,
                )
                if contract.get("inference_inputs") is not None and m_type_str == "moneyline"
                else None
            )
        except (ValueError, KeyError, TypeError) as error:
            errors.append({"event_id": contract.get("event_id"), "reason": f"Invalid replay inputs: {error}"})
            continue
        contract_model_version = str(contract.get("model_version") or forecast["model_version"])
        request = PickRequest(
            event_start_utc=str(contract["event_start_utc"]),
            event_id=str(contract["event_id"]),
            league=League(league_upper),
            away_team=away_team,
            home_team=home_team,
            market_type=m_type,
            selection=selection,
            line=m_line,
            sportsbook="polymarket_us",
            american_odds=american_odds,
            model_probability=round(model_prob, 6),
            model_uncertainty=None,
            model_version=contract_model_version,
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
            model_input_snapshot_json=international_snapshot_json,
            calibration_version=contract_model_version,
            calibration_artifact_hash=str(contract.get("artifact_hash", "")),
            code_revision=contract_model_version,
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
    configured_markets = tuple(model_config.get("markets") or ())
    from ..models.soccer_v3 import soccer_model_for_version

    model_ver = str(
        model_config.get("active_production_version")
        or model_config.get("active_research_version")
        or "soccer-poisson-dc-v3"
    )
    active_model = soccer_model_for_version(model_ver)
    forecast = build_soccer_total_slate(
        data_root=data_root,
        game_date=args_date,
        client=ESPNClient(),
        leagues=tuple(model_config.get("leagues") or ()),
        observed_at=utc_now(),
        include_draw="draw" in configured_markets,
        include_double_chance="double_chance" in configured_markets,
        model=active_model,
        min_team_games=int(model_config.get("min_team_games", 5)),
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
    configured_markets = tuple(model_config.get("markets") or ())
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
        coded_snapshot_json = None
        if contract.get("inference_inputs") and contract["market_type"] == "moneyline":
            from ..coded_market_replay import build_snapshot as build_coded_snapshot

            try:
                coded_snapshot_json = json.dumps(
                    build_coded_snapshot(
                        contract,
                        str(forecast["model_code_hash"]),
                        contract["model_inputs_observed_at_utc"],
                    ),
                    sort_keys=True,
                    allow_nan=False,
                )
            except (KeyError, ValueError, TypeError) as exc:
                errors.append({"event_id": contract["event_id"], "error": str(exc)})
                continue
        if coded_snapshot_json is None:
            coded_snapshot_json = contract_snapshot_json(
                contract,
                sport="SOCCER",
                selection=str(contract["selection"]),
                line=None if contract["line"] is None else float(contract["line"]),
                probability=float(contract["model_probability"]),
                observed_at_utc=str(contract["observed_at_utc"]),
                artifact_hash=str(forecast["model_code_hash"]),
            )
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
            feature_schema_version=str(contract["model_version"]),
            code_revision=str(forecast["model_code_hash"]),
            model_input_snapshot_json=coded_snapshot_json,
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
                contract_mtype = str(contract.get("market_type", ""))
                market_allowed = True
                if configured_markets:
                    is_allowed_total = contract_mtype == "total" and any(
                        m in ("full_game_total_2_5", "total") for m in configured_markets
                    )
                    is_allowed_ml = contract_mtype == "moneyline" and (
                        ("moneyline" in configured_markets and contract.get("selection") in ("home", "away"))
                        or (
                            ("draw" in configured_markets or "moneyline" in configured_markets)
                            and contract.get("selection") == "draw"
                        )
                    )
                    is_allowed_btts = contract_mtype == "btts" and "btts" in configured_markets
                    market_allowed = is_allowed_total or is_allowed_ml or is_allowed_btts
                genuinely_eligible = eligibility.decision == "CALL" and market_allowed
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

    min_edge = float(model_config.get("min_edge", 0.02))
    configured_markets = tuple(model_config.get("markets") or ("moneyline",))
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
        coded_snapshot_json = None
        if contract.get("inference_inputs") and contract["market_type"] == "moneyline":
            from ..coded_market_replay import build_snapshot as build_coded_snapshot

            try:
                coded_snapshot_json = json.dumps(
                    build_coded_snapshot(
                        contract,
                        str(forecast["model_code_hash"]),
                        contract["model_inputs_observed_at_utc"],
                    ),
                    sort_keys=True,
                    allow_nan=False,
                )
            except (KeyError, ValueError, TypeError) as exc:
                errors.append({"event_id": contract["event_id"], "error": str(exc)})
                continue
        if coded_snapshot_json is None:
            coded_snapshot_json = contract_snapshot_json(
                contract,
                sport="TENNIS",
                selection=str(contract["selection"]),
                line=None if contract["line"] is None else float(contract["line"]),
                probability=float(contract["model_probability"]),
                observed_at_utc=str(contract["observed_at_utc"]),
                artifact_hash=str(forecast["model_code_hash"]),
            )
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
            calibration_method=(
                "learned_v3"
                if contract["model_version"] == "tennis-learned-v3"
                else (
                    "markov_hierarchical"
                    if contract["model_version"]
                    in (
                        "tennis-markov-hierarchical-v2",
                        "tennis-games-markov-v1",
                        "tennis-games-markov-v2",
                        "tennis-games-markov-v3",
                    )
                    else "surface_blended_elo"
                )
            ),
            calibration_version=str(contract["model_version"]),
            calibration_artifact_hash=str(forecast["model_code_hash"]),
            feature_schema_version=str(contract.get("feature_schema_version") or contract["model_version"]),
            code_revision=str(forecast["model_code_hash"]),
            model_input_snapshot_json=coded_snapshot_json,
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
                contract_edge = float(contract.get("edge_vs_executable_ask", 0.0))
                mtype_str = str(contract.get("market_type", "moneyline"))
                market_allowed = mtype_str in configured_markets
                genuinely_eligible = (
                    eligibility.decision == "CALL"
                    and not eligibility.reason_code.startswith("PAPER_CALL_")
                    and market_allowed
                    and (min_edge <= contract_edge <= 0.08)
                    and float(contract.get("model_probability", 0.0)) >= 0.25
                )
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
                    # Flat: call every priced contract on the slate for 100% full coverage (flat 1.0U, real qualified call)
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
    exposure_source = flat_ledger or research_ledger or main_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    min_edge = float(model_config.get("min_edge", 0.03))
    configured_state = str(model_config.get("status", "research"))
    forecast["status"] = configured_state
    observed_now = utc_now()
    logged = gated = flat_logged = main_logged = 0
    research_duplicates = gated_duplicates = flat_duplicates = main_duplicates = 0
    errors: list[dict] = []

    for contract in forecast.get("priced_contracts", []):
        ask = float(contract["executable_ask"])
        mtype = MarketType(str(contract["market_type"]))
        m_version = str(
            contract.get("model_version")
            or model_config.get("active_production_version", "cfb-structural-v3")
        )

        try:
            # A forced refresh cannot manufacture a pregame decision time.
            # validate() below rejects started events using the actual clock.
            effective_now = observed_now
            from ..cfb_replay import build_snapshot as build_cfb_snapshot

            cfb_snapshot_json = (
                json.dumps(
                    build_cfb_snapshot(
                        contract,
                        model_hash=str(forecast["model_code_hash"]),
                        observed_at_utc=effective_now.isoformat(),
                    ),
                    sort_keys=True,
                    allow_nan=False,
                )
                if contract.get("inference_inputs") is not None
                else None
            )
            contract_sportsbook = str(contract.get("sportsbook") or "espn_consensus")
            contract_slug = str(contract.get("market_slug") or "")
            contract_obs = str(contract.get("observed_at_utc") or effective_now.isoformat())
            rationale_text = str(contract.get("rationale") or "")
            if contract_sportsbook == "polymarket_us" and contract_slug:
                rationale_text = f"{rationale_text} Executable ask {ask:.4f} ({contract_slug})."

            request = PickRequest(
                event_start_utc=str(contract["event_start_utc"]),
                event_id=str(contract["event_id"]),
                league=League.NCAAF,
                away_team=str(contract["away_team"]),
                home_team=str(contract["home_team"]),
                market_type=mtype,
                selection=str(contract["selection"]),
                line=None if contract["line"] is None else float(contract["line"]),
                sportsbook=contract_sportsbook,
                american_odds=probability_to_american(ask),
                model_probability=float(contract["model_probability"]),
                model_uncertainty=float(contract["model_uncertainty"]),
                model_version=m_version,
                rationale=rationale_text,
                risks="College football model; multi-market (moneyline, spread, total).",
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=ModelState(configured_state),
                observed_at_utc=contract_obs,
                model_artifact_hash=str(forecast["model_code_hash"]),
                calibration_method="cfb_key_number_engine",
                calibration_version=m_version,
                calibration_artifact_hash=str(forecast["model_code_hash"]),
                feature_schema_version="cfb-v1",
                code_revision=str(forecast["model_code_hash"]),
                model_input_snapshot_json=cfb_snapshot_json,
                **(contract.get("market_lineage") or {}),
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
                contract_edge = float(contract.get("edge_vs_executable_ask", 0.0))
                genuinely_eligible = (
                    eligibility.decision == "CALL"
                    and eligibility.record_type == RecordType.QUALIFIED_SHADOW_CALL
                    and contract_edge >= min_edge
                )
                if (
                    genuinely_eligible
                    and mtype == MarketType.TOTAL
                    and (
                        contract_edge > 0.10
                        or float(contract.get("model_probability", 0.0)) > 0.62
                        or (
                            str(contract.get("selection", "")).lower() == "under"
                            and contract.get("line") is not None
                            and float(contract["line"]) >= 56.5
                        )
                    )
                ):
                    genuinely_eligible = False
                # Optional, off-by-default gates: each needs a config value, which in turn needs
                # walk-forward evidence (ARCHITECTURE.md validation contract) before being set.
                _ml_max_odds = model_config.get("max_moneyline_american_odds")
                if (
                    genuinely_eligible
                    and _ml_max_odds is not None
                    and mtype == MarketType.MONEYLINE
                    and request.american_odds is not None
                    and request.american_odds > float(_ml_max_odds)
                ):
                    genuinely_eligible = False
                _blowout_cap = model_config.get("blowout_spread_edge_cap")
                if (
                    genuinely_eligible
                    and _blowout_cap is not None
                    and mtype == MarketType.SPREAD
                    and contract.get("line") is not None
                    and abs(float(contract["line"])) > 21.5
                    and contract_edge > float(_blowout_cap)
                ):
                    genuinely_eligible = False
                # CFB spread (cfb-spread-v1) and total (cfb-total-v1) artifacts explicitly declare
                # "status": "FLAT_LEDGER_ONLY" and "qualified": false. Unified cfb-structural-v3
                # produces calibrated joint probabilities and is qualified across moneyline,
                # spread, and total for main_ledger.
                is_qualified_call = (
                    genuinely_eligible
                    and not eligibility.reason_code.startswith("PAPER_CALL_")
                    and m_version in ("cfb-structural-v3", "cfb-structural-v2")
                )
                if (
                    research_ledger is not None
                    and _append_secondary_ledger(
                        research_ledger, request, eligibility, effective_now, "ncaaf:research_ledger"
                    )
                    is not None
                ):
                    research_duplicates += 1
                if gated_ledger is not None and genuinely_eligible and is_qualified_call:
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
                if main_ledger is not None and is_qualified_call:
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


def _forecast_hockey_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    research_ledger=None,
    gated_ledger=None,
    flat_ledger=None,
    main_ledger=None,
) -> dict:
    """Forecast and price hockey slates across all Polymarket leagues."""
    from ..data_sources.polymarket_us import probability_to_american
    from ..hockey_forward import build_hockey_slate
    from ..models.hockey import hockey_model

    model = hockey_model()
    if not _is_registered_serving_model(model.version, "HOCKEY", "moneyline"):
        raise ValueError(f"HOCKEY moneyline champion does not match executable model {model.version}")
    model_config = config.get("models", {}).get("HOCKEY", {})
    leagues = tuple(model_config.get("leagues") or ())
    min_edge = float(model_config.get("min_edge", 0.02))
    configured_state = model_config.get("status", "shadow_qualified")
    try:
        model_state_val = ModelState(configured_state)
    except (ValueError, KeyError):
        model_state_val = ModelState.SHADOW_QUALIFIED

    forecast = build_hockey_slate(
        data_root=data_root,
        game_date=args_date,
        leagues=leagues,
        observed_at=utc_now(),
        model=model,
        min_team_games=int(model_config.get("min_team_games", 0)),
    )

    exposure_source = research_ledger or flat_ledger or main_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    effective_now = utc_now()
    logged = 0
    flat_logged = 0
    main_logged = 0
    research_duplicates = 0
    flat_duplicates = 0
    main_duplicates = 0
    errors: list[dict[str, Any]] = []

    for contract in forecast.get("priced", []):
        try:
            if parse_utc(str(contract["event_start_utc"])) <= effective_now:
                forecast.setdefault("skipped", []).append(
                    {"event_id": contract.get("event_id"), "reason": "event_started"}
                )
                continue
            m_type = str(contract.get("market_type") or "moneyline")
            if not _is_registered_serving_model(model.version, "HOCKEY", m_type):
                forecast.setdefault("skipped", []).append(
                    {
                        "event_id": contract.get("event_id"),
                        "reason": "market_has_no_serving_champion",
                        "market": m_type,
                    }
                )
                continue
            market_type_enum = MarketType(m_type)
            sel_str = str(contract.get("selection") or "home").lower()
            line_val = None if contract.get("line") is None else float(contract["line"])
            prob_val = float(contract["model_probability"])
            ask_val = float(contract.get("executable_ask") or 0.5)
            contract_edge = float(contract.get("edge_vs_executable_ask") or (prob_val - ask_val))

            hockey_observed_at = str(contract.get("observed_at_utc") or effective_now.isoformat())
            hockey_model_version = str(contract.get("model_version") or "hockey-poisson-dc-v2")
            request = PickRequest(
                event_start_utc=str(contract["event_start_utc"]),
                event_id=str(contract["event_id"]),
                league=League.HOCKEY,
                away_team=str(contract["away_team"]),
                home_team=str(contract["home_team"]),
                market_type=market_type_enum,
                selection=sel_str,
                line=line_val,
                sportsbook="model_opinion_indicative_market",
                american_odds=probability_to_american(ask_val),
                model_probability=prob_val,
                model_uncertainty=float(contract.get("model_uncertainty") or 0.1),
                model_version=str(contract.get("model_version") or "hockey-poisson-dc-v2"),
                rationale=(
                    f"{contract.get('rationale', '')} Indicative market price {ask_val:.4f} "
                    f"({contract.get('market_slug', '')}); executable BBO unverified."
                ),
                risks=("Paper-mode hockey score model; bivariate Poisson with OT resolution."),
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=model_state_val,
                observed_at_utc=hockey_observed_at,
                calibration_method="poisson_dixon_coles",
                calibration_version=hockey_model_version,
                model_input_snapshot_json=contract_snapshot_json(
                    {**contract, "market_type": m_type, "model_version": hockey_model_version},
                    sport="HOCKEY",
                    selection=sel_str,
                    line=line_val,
                    probability=prob_val,
                    observed_at_utc=hockey_observed_at,
                ),
            )

            from ..eligibility import EligibilityResult
            from ..entities import CanonicalTeam

            away_team_obj = CanonicalTeam(
                canonical_team_id=str(contract["away_team"]),
                league=League.HOCKEY,
                canonical_name=str(contract["away_team"]),
                abbreviation=str(contract["away_team"])[:3].upper(),
                active=True,
                valid_from=None,
                valid_to=None,
                aliases=(),
            )
            home_team_obj = CanonicalTeam(
                canonical_team_id=str(contract["home_team"]),
                league=League.HOCKEY,
                canonical_name=str(contract["home_team"]),
                abbreviation=str(contract["home_team"])[:3].upper(),
                active=True,
                valid_from=None,
                valid_to=None,
                aliases=(),
            )
            from ..models.hockey import HockeyModel

            dyn_units = (
                HockeyModel.compute_dynamic_units(contract_edge, min_edge=min_edge, max_edge=0.08)
                if contract_edge >= min_edge
                else 0.0
            )
            eligibility = EligibilityResult(
                record_type=RecordType.RESEARCH_OBSERVATION,
                decision="CALL" if contract_edge >= min_edge else "NO_CALL",
                reason_code="PAPER_CALL_MODEL_QUALIFIED" if contract_edge >= min_edge else "NO_CALL_LOW_EDGE",
                units=dyn_units,
                confidence_score=round(prob_val * 100),
                edge=round(contract_edge, 6),
                adjusted_edge=round(contract_edge, 6),
                away_team=away_team_obj,
                home_team=home_team_obj,
            )

            with _LEDGER_LOCK:
                if flat_ledger is not None:
                    if (
                        _append_secondary_ledger(
                            flat_ledger, request, eligibility, effective_now, "hockey:flat_ledger"
                        )
                        is None
                    ):
                        flat_logged += 1
                    else:
                        flat_duplicates += 1
                if research_ledger is not None:
                    if (
                        _append_secondary_ledger(
                            research_ledger, request, eligibility, effective_now, "hockey:research_ledger"
                        )
                        is None
                    ):
                        logged += 1
                    else:
                        research_duplicates += 1
                # Spread/total are unverified (no settled-pick record): flat/research only.
                if (
                    main_ledger is not None
                    and m_type == "moneyline"
                    and contract_edge >= min_edge
                    and eligibility.decision == "CALL"
                ):
                    if (
                        _append_secondary_ledger(
                            main_ledger, request, eligibility, effective_now, "hockey:main_ledger"
                        )
                        is None
                    ):
                        main_logged += 1
                    else:
                        main_duplicates += 1
        except DuplicatePickError:
            continue
        except Exception as error:  # noqa: BLE001
            errors.append({"event_id": contract.get("event_id"), "reason": str(error)})
            continue

    forecast["logged"] = logged or flat_logged
    forecast["flat_logged"] = flat_logged
    forecast["main_logged"] = main_logged
    forecast["duplicates"] = {
        "research_ledger": research_duplicates,
        "flat_ledger": flat_duplicates,
        "main_ledger": main_duplicates,
    }
    forecast["errors"] = errors
    return forecast


def _forecast_darts_sport(
    *,
    data_root,
    args_date: str,
    config: dict,
    research_ledger=None,
    gated_ledger=None,
    flat_ledger=None,
    main_ledger=None,
) -> dict:
    """Forecast and price darts slates across PDC and MODUS leagues."""
    from ..darts_forward import build_darts_slate
    from ..data_sources.polymarket_us import probability_to_american
    from ..models.darts import darts_model

    model = darts_model()
    if not _is_registered_serving_model(model.version, "DARTS", "moneyline"):
        raise ValueError(f"DARTS moneyline champion does not match executable model {model.version}")
    model_config = config.get("models", {}).get("DARTS", {})
    leagues = tuple(model_config.get("leagues") or ())
    min_edge = float(model_config.get("min_edge", 0.02))
    configured_state = model_config.get("status", "shadow_qualified")
    try:
        model_state_val = ModelState(configured_state)
    except (ValueError, KeyError):
        model_state_val = ModelState.SHADOW_QUALIFIED

    forecast = build_darts_slate(
        data_root=data_root,
        game_date=args_date,
        leagues=leagues,
        observed_at=utc_now(),
        model=model,
        min_team_matches=int(model_config.get("min_team_matches", 5)),
        longshot_ask_floor=model_config.get("longshot_ask_floor"),
        longshot_min_sample=int(model_config.get("longshot_min_sample", 5)),
        max_claimed_edge=model_config.get("max_claimed_edge"),
    )
    # Darts stays flat-ledger-only until the model is promoted: main_ledger_enabled opts in.
    baseline_only = not bool(model.match_counts)
    forecast["training_status"] = (
        "untrained_equal_rating_baseline" if baseline_only else "observed_match_history"
    )

    exposure_source = research_ledger or flat_ledger or main_ledger
    if exposure_source is None:
        forecast["logged"] = 0
        return forecast

    effective_now = utc_now()
    logged = 0
    flat_logged = 0
    main_logged = 0
    research_duplicates = 0
    flat_duplicates = 0
    main_duplicates = 0
    errors: list[dict[str, Any]] = []

    for contract in forecast.get("priced", []):
        try:
            if parse_utc(str(contract["event_start_utc"])) <= effective_now:
                forecast.setdefault("skipped", []).append(
                    {"event_id": contract.get("event_id"), "reason": "event_started"}
                )
                continue
            m_type = str(contract.get("market_type") or "moneyline")
            if not _is_registered_serving_model(model.version, "DARTS", m_type):
                forecast.setdefault("skipped", []).append(
                    {
                        "event_id": contract.get("event_id"),
                        "reason": "market_has_no_serving_champion",
                        "market": m_type,
                    }
                )
                continue
            market_type_enum = MarketType(m_type)
            sel_str = str(contract.get("selection") or "home").lower()
            line_val = None if contract.get("line") is None else float(contract["line"])
            prob_val = float(contract["model_probability"])
            ask_val = float(contract.get("executable_ask") or 0.5)
            contract_edge = float(contract.get("edge_vs_executable_ask") or (prob_val - ask_val))

            request = PickRequest(
                event_start_utc=str(contract["event_start_utc"]),
                event_id=str(contract["event_id"]),
                league=League.DARTS,
                away_team=str(contract["away_team"]),
                home_team=str(contract["home_team"]),
                market_type=market_type_enum,
                selection=sel_str,
                line=line_val,
                sportsbook="model_opinion_indicative_market",
                american_odds=probability_to_american(ask_val),
                model_probability=prob_val,
                model_uncertainty=float(contract.get("model_uncertainty") or 0.1),
                model_version=str(contract.get("model_version") or "darts-markov-v1"),
                rationale=(
                    f"{contract.get('rationale', '')} Indicative market price {ask_val:.4f} "
                    f"({contract.get('market_slug', '')}); executable BBO unverified."
                ),
                risks=("Paper-mode darts score model; exact Markov leg-progression distribution."),
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=model_state_val,
                observed_at_utc=str(contract.get("observed_at_utc") or effective_now.isoformat()),
                calibration_method="markov_leg_progression",
                calibration_version=str(contract.get("model_version") or "darts-markov-v1"),
            )

            from ..eligibility import EligibilityResult
            from ..entities import CanonicalTeam

            away_team_obj = CanonicalTeam(
                canonical_team_id=str(contract["away_team"]),
                league=League.DARTS,
                canonical_name=str(contract["away_team"]),
                abbreviation=str(contract["away_team"])[:3].upper(),
                active=True,
                valid_from=None,
                valid_to=None,
                aliases=(),
            )
            home_team_obj = CanonicalTeam(
                canonical_team_id=str(contract["home_team"]),
                league=League.DARTS,
                canonical_name=str(contract["home_team"]),
                abbreviation=str(contract["home_team"])[:3].upper(),
                active=True,
                valid_from=None,
                valid_to=None,
                aliases=(),
            )
            from ..models.darts import DartsModel

            dyn_units = (
                DartsModel.compute_dynamic_units(contract_edge, min_edge=min_edge, max_edge=0.08)
                if not baseline_only and contract_edge >= min_edge
                else 0.0
            )
            eligibility = EligibilityResult(
                record_type=RecordType.RESEARCH_OBSERVATION,
                decision="CALL" if not baseline_only and contract_edge >= min_edge else "NO_CALL",
                reason_code=(
                    "NO_CALL_UNTRAINED_MODEL"
                    if baseline_only
                    else "PAPER_CALL_MODEL_QUALIFIED"
                    if contract_edge >= min_edge
                    else "NO_CALL_LOW_EDGE"
                ),
                units=dyn_units,
                confidence_score=round(prob_val * 100),
                edge=round(contract_edge, 6),
                adjusted_edge=round(contract_edge, 6),
                away_team=away_team_obj,
                home_team=home_team_obj,
            )

            with _LEDGER_LOCK:
                if flat_ledger is not None:
                    if (
                        _append_secondary_ledger(
                            flat_ledger, request, eligibility, effective_now, "darts:flat_ledger"
                        )
                        is None
                    ):
                        flat_logged += 1
                    else:
                        flat_duplicates += 1
                if research_ledger is not None:
                    if (
                        _append_secondary_ledger(
                            research_ledger, request, eligibility, effective_now, "darts:research_ledger"
                        )
                        is None
                    ):
                        logged += 1
                    else:
                        research_duplicates += 1
                if (
                    main_ledger is not None
                    and model_config.get("main_ledger_enabled", False)
                    and contract_edge >= min_edge
                    and eligibility.decision == "CALL"
                ):
                    if (
                        _append_secondary_ledger(
                            main_ledger, request, eligibility, effective_now, "darts:main_ledger"
                        )
                        is None
                    ):
                        main_logged += 1
                    else:
                        main_duplicates += 1
        except DuplicatePickError:
            continue
        except Exception as error:  # noqa: BLE001
            errors.append({"event_id": contract.get("event_id"), "reason": str(error)})
            continue

    forecast["logged"] = logged or flat_logged
    forecast["flat_logged"] = flat_logged
    forecast["main_logged"] = main_logged
    forecast["duplicates"] = {
        "research_ledger": research_duplicates,
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
    except ValueError as error:
        # None means a new durable row to every caller's counter. Returning
        # it on rejection falsely reported successful writes for whole sports.
        logger.warning("%s: ledger rejected pick (%s)", ledger_name, error)
        raise


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
        # Esports/KBO/NPB are dual-ledger sports since the 2026-10-22 merge: Main was
        # cleared above and Flat is cleared here.
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
            results[sport] = forecast_esports_slate(
                data_root=data_directory,
                artifact_dir=_research_models_dir(),
                title=sport,
                game_date=args.date,
            )
            if log and ledger is not None:
                # Merged 2026-10-22: Flat gets every candidate, Main the gated CALLs. Both
                # ledgers are written whichever command ran (a dual-ledger sport), so a
                # same-day re-run of either command regenerates what it clears.
                _log_esports_forecast(
                    results[sport],
                    config,
                    flat_ledger,
                    flat_mode=False,
                    gated_ledger=ledger,
                )
        elif sport in ("kbo", "npb"):
            # Merged 2026-10-22: same routing as esports above.
            results[sport] = _forecast_international_sport(
                data_root=data_directory,
                artifact_dir=_research_models_dir(),
                league=sport,
                args_date=args.date,
                config=config,
                research_ledger=(flat_ledger if log else None),
                gated_ledger=(ledger if log else None),
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
        elif sport in ("hockey", "nhl"):
            results[sport] = _forecast_hockey_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=(ledger if log else None),
                flat_ledger=(flat_ledger if log else None),
            )
        elif sport in ("darts", "pdcdarts", "modus"):
            results[sport] = _forecast_darts_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=(ledger if log else None),
                flat_ledger=(flat_ledger if log else None),
            )
        elif sport == "ncaab":
            from ..college_basketball_forward import forecast_ncaab_sport

            results[sport] = forecast_ncaab_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=ledger if log else None,
                flat_ledger=flat_ledger if log else None,
            )
        elif sport == "nbl":
            from ..nbl_forward import forecast_nbl_sport

            results[sport] = forecast_nbl_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=ledger if log else None,
                flat_ledger=flat_ledger if log else None,
            )
        elif sport == "euroleague":
            from ..euroleague_forward import forecast_euroleague_sport

            results[sport] = forecast_euroleague_sport(
                data_root=data_directory,
                args_date=args.date,
                config=config,
                main_ledger=ledger if log else None,
                flat_ledger=flat_ledger if log else None,
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
            if sport == "mlb":
                results["mlb_totals"] = _forecast_mlb_totals_flat(
                    args.date,
                    log,
                    config,
                    registry,
                    bans,
                    flat_ledger,
                    audit,
                    main_ledger=(ledger if log and not is_flat else None),
                )
                results["mlb_nrfi"] = _forecast_mlb_nrfi_flat(
                    args.date,
                    log,
                    config,
                    registry,
                    bans,
                    flat_ledger,
                    audit,
                    main_ledger=(ledger if log and not is_flat else None),
                    data_root=data_directory,
                )
            elif sport == "wnba":
                results["wnba_spread"] = _forecast_wnba_spread_sport(
                    data_root=data_directory,
                    args_date=args.date,
                    config=config,
                    registry=registry,
                    bans=bans,
                    main_ledger=(ledger if log and not is_flat else None),
                    flat_ledger=(flat_ledger if log else None),
                )
                results["wnba_total"] = _forecast_wnba_total_sport(
                    data_root=data_directory,
                    args_date=args.date,
                    config=config,
                    registry=registry,
                    bans=bans,
                    main_ledger=(ledger if log and not is_flat else None),
                    flat_ledger=(flat_ledger if log else None),
                )
            elif sport == "nba":
                results["nba_spread"] = _forecast_nba_spread_sport(
                    data_root=data_directory,
                    args_date=args.date,
                    config=config,
                    registry=registry,
                    bans=bans,
                    main_ledger=(ledger if log and not is_flat else None),
                    flat_ledger=(flat_ledger if log else None),
                )
                results["nba_total"] = _forecast_nba_total_sport(
                    data_root=data_directory,
                    args_date=args.date,
                    config=config,
                    registry=registry,
                    bans=bans,
                    main_ledger=(ledger if log and not is_flat else None),
                    flat_ledger=(flat_ledger if log else None),
                )
            elif sport == "nfl":
                results["nfl_spread"] = _forecast_nfl_spread_sport(
                    data_root=data_directory,
                    args_date=args.date,
                    config=config,
                    registry=registry,
                    bans=bans,
                    main_ledger=(ledger if log and not is_flat else None),
                    flat_ledger=(flat_ledger if log else None),
                    force=getattr(args, "force", False),
                )
                results["nfl_total"] = _forecast_nfl_total_sport(
                    data_root=data_directory,
                    args_date=args.date,
                    config=config,
                    registry=registry,
                    bans=bans,
                    main_ledger=(ledger if log and not is_flat else None),
                    flat_ledger=(flat_ledger if log else None),
                    force=getattr(args, "force", False),
                )
        else:
            results[sport] = _forecast_research_sport(sport, args.date, config)
    output = results[sports[0]] if len(sports) == 1 and len(results) == 1 else results
    return output
