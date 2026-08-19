"""Misc command helpers.

Mechanical extraction from the former cli.py monolith (DD-6 split, stage
4). Polymarket slate capture, daily-summary machinery, ledger helpers,
chain verification, and team bans. The logger is defined HERE for the
same BLE001-exemption reason as the other cli modules.
"""

from __future__ import annotations

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ..backtester import walk_forward_backtest
from ..bans import TeamBanList
from ..champion_challenger import (
    FrozenProductionStore,
    ProductionRegistry,
    compare_champion_vs_challenger,
)
from ..config import (
    PROJECT_ROOT,
    audit_path,
    entity_registry_path,
    ledger_path,
    polymarket_snapshot_path,
    unit_policy,
)
from ..data_sources.espn import ESPNClient
from ..data_sources.espn_wnba_injuries import capture_espn_event_injuries
from ..data_sources.kalshi import DEFERRED_MESSAGE as KALSHI_DEFERRED_MESSAGE
from ..data_sources.polymarket_execute import ExecutionGateError, OrderTicket, PolymarketExecutor
from ..data_sources.polymarket_us import (
    POLYMARKET_SPORT_LEAGUES,
    PolymarketSnapshotStore,
    PolymarketUSClient,
    capture_slate_snapshots,
    probability_to_american,
    refresh_contract_snapshots,
)
from ..data_sources.wnba_injuries import capture_latest_report
from ..domain import (
    EASTERN,
    League,
    MarketType,
    ModelOrigin,
    ModelState,
    PickRequest,
    RecordType,
    parse_utc,
    utc_now,
)
from ..eligibility import evaluate_eligibility
from ..esports import TITLE_SPECS, backfill_esports, forecast_esports_slate, validate_all_esports_baselines
from ..features.base import FeatureStore
from ..ingest import Ingestor
from ..international_baseball import (
    LEAGUE_SPECS as INTERNATIONAL_BASEBALL_LEAGUE_SPECS,
)
from ..international_baseball import (
    backfill_international_baseball,
    forecast_international_baseball_slate,
    validate_all_international_baseball_baselines,
)
from ..ledger import LEDGER_SCHEMA_VERSION
from ..mlb_baseline_refresh import refresh_if_due
from ..models import MODEL_SPECS
from ..models.market_residual import MarketResidualModel, ResidualTrainingRow
from ..models.registry import model_spec
from ..research_ledgers import research_ledger
from ..runtime_paths import rolling_models_root
from ..total_score import validate_all_total_score_models
from ..units import edge_scaled_units
from ..validation import run_validation_audit, write_production_artifacts
from .state import ESPN_SPORTS, SPORTS

logger = logging.getLogger("model_prediction.cli")


def _polymarket_slate(args, config) -> dict:
    if args.provider == "kalshi":
        return {"provider": "kalshi", "status": "deferred", "note": KALSHI_DEFERRED_MESSAGE}
    client = PolymarketUSClient()
    game_date = date.fromisoformat(args.date)
    league_errors: dict[str, str] = {}
    if args.league:
        events = {args.league.upper(): client.slate(args.league, game_date, args.timezone)}
    elif args.all:
        events = {}
        # Every sport's leagues, flattened into one list and fetched through
        # ONE shared pool. Deliberately NOT "9 sports concurrently, each
        # calling sport_slate which itself fans its own leagues out
        # concurrently" -- that nests two pools sharing the same
        # PolymarketUSClient connection pool, and at peak (e.g. soccer's ~30
        # leagues alongside esports'/tennis'/etc.) pushes concurrent
        # in-flight requests well past what the gateway tolerates: measured
        # directly, request latency is flat around 0.6s per call up through
        # ~16 concurrent requests, then degrades to 3-5s per call (and
        # starts raising SSL EOF connection errors) at 24-32. One flat pool
        # capped at 16 keeps every fetch (30+ leagues) under that ceiling
        # instead of silently multiplying concurrency across nested pools.
        all_leagues = [(sport, league) for sport in SPORTS for league in POLYMARKET_SPORT_LEAGUES[sport]]

        def _fetch_league_slate(entry: tuple[str, str]) -> tuple[str, str, list[dict[str, Any]], str | None]:
            sport, league = entry
            try:
                return sport, league, client.slate(league, game_date, args.timezone), None
            except httpx.HTTPError as exc:
                return sport, league, [], str(exc)[:200]

        with ThreadPoolExecutor(max_workers=min(16, len(all_leagues))) as pool:
            for sport, league, league_events, error in pool.map(_fetch_league_slate, all_leagues):
                events[league] = league_events
                if error is not None:
                    league_errors[league] = error
    elif args.sport:
        result = client.sport_slate(args.sport, game_date, args.timezone)
        events = result.events
        league_errors = result.errors
    else:
        raise ValueError("provide --sport, --league, or --all")
    bbo_capture = (
        {"status": "disabled"}
        if getattr(args, "no_snapshot_bbo", False)
        else capture_slate_snapshots(
            client,
            events,
            Path(ledger_path(config)).parent,
            args.date,
        )
    )
    return {
        "provider": "polymarket_us",
        "game_date": args.date,
        "timezone": args.timezone,
        "events_by_league": events,
        "event_count": sum(len(items) for items in events.values()),
        "league_fetch_errors": league_errors,
        "prospective_bbo_capture": bbo_capture,
        "note": (
            "Public market discovery plus prospective executable-BBO storage. "
            "Event-list quotes remain indicative."
        ),
    }


def _research_models_dir() -> Path:
    """Rolling-first artifact directory for research retraining/forecast.

    The scheduled cycle retrains esports/KBO/NPB ratings artifacts every
    run; those live under the runtime root's models/ so the checked-in
    config/models copies stay frozen promoted artifacts. Readers fall
    back to the frozen copies until the first rolling copy exists.
    """
    rolling = rolling_models_root(PROJECT_ROOT)
    if rolling.is_dir() and any(rolling.iterdir()):
        return rolling
    return PROJECT_ROOT / "config" / "models"


def _clear_today_open(
    ledger, date_str: str, by_event_date: bool = False, leagues: set[str] | None = None
) -> list[str]:
    """Remove open picks for a date before re-forecasting, via the audited path.

    When by_event_date is True, also removes open picks whose event_start_utc
    matches date_str — used for flat ledger to prevent duplicate forecast runs.
    All removals go through ``PickLedger.remove_open_rows`` so they hold the
    ledger lock and append ``pick_removed`` audit events.

    leagues: when given (casefolded league names), only rows in those leagues
    are considered for removal. ``ledger`` here is frequently a
    MultiSportPickLedger spanning every sport that shares one Main/Flat file
    pair -- without this filter, re-forecasting a single sport (e.g.
    `forecast --sport tennis --log`) clears every OTHER sport's still-open
    today rows too, since they all live in the same underlying ledger, and
    only the requested sport gets regenerated afterward. Found 2026-08-03
    when a single-sport flat-forecast run silently wiped that day's real
    open MLB/WNBA/tennis Main picks.

    Only removes picks for games that HAVEN'T STARTED YET. Re-running the
    pipeline intraday is meant to refresh not-yet-started candidates with
    fresher data — a game that started between two runs is not a stale
    candidate to replace, it's a live/settling position. Without this guard,
    a later same-day run would clear its still-open row and never recreate
    it (the new forecast pass correctly excludes started games), silently
    losing the record of a pick that was made legitimately pregame.

    Passes allow_staked_removal=True on every call: this project is
    shadow-only (no real orders are ever placed from a forecast run — see
    POLICY.md), so a "QUALIFIED_SHADOW_CALL, units > 0" row is never actual
    staked money on EITHER ledger. Without this, an MLB pick strong enough to
    independently clear real eligibility (not just the flat-mode diagnostic
    override) would get silently frozen forever — never replaced by any
    later re-forecast, on the main ledger exactly as much as on flat.
    """
    now = utc_now()
    to_remove = []
    for row in ledger.rows():
        if row.get("status") != "open":
            continue
        if leagues is not None and str(row.get("league", "")).casefold() not in leagues:
            continue
        created = str(row.get("created_at_utc", "") or "")
        event = str(row.get("event_start_utc", "") or "")
        if not (created.startswith(date_str) or (by_event_date and event.startswith(date_str))):
            continue
        try:
            started = parse_utc(event) <= now
        except ValueError:
            started = False  # can't verify timing — don't risk deleting it
        if started:
            continue
        to_remove.append(row["pick_id"])
    if not to_remove:
        return []
    return ledger.remove_open_rows(
        to_remove, reason=f"re-forecast replacement for {date_str}", allow_staked_removal=True
    )


def _drift_check(settled_qualified: list, config: dict) -> dict:
    """Compare live settled hit rate against model holdout for each sport."""
    import json
    import math
    from collections import defaultdict

    by_sport = defaultdict(lambda: {"wins": 0, "losses": 0})
    for row in settled_qualified:
        sport = str(row.get("league") or row.get("sport") or "?").upper()
        if row.get("result") == "win":
            by_sport[sport]["wins"] += 1
        elif row.get("result") == "loss":
            by_sport[sport]["losses"] += 1

    drift = {}
    for sport_name, counts in by_sport.items():
        n = counts["wins"] + counts["losses"]
        if n < 10:
            drift[sport_name] = {"status": "insufficient_sample", "n": n}
            continue

        live_hr = counts["wins"] / n
        holdout_hr = None
        model_config = (config.get("models") or {}).get(sport_name, {})
        artifact_rel = model_config.get("production_artifact", "")
        if artifact_rel:
            model_path = PROJECT_ROOT / artifact_rel
            if model_path.exists():
                try:
                    artifact = json.loads(model_path.read_text())
                    holdout_hr = artifact.get("qualification", {}).get("hit_rate")
                except (json.JSONDecodeError, KeyError):
                    pass

        if holdout_hr is None:
            drift[sport_name] = {"status": "no_holdout_reference", "live_hr": round(live_hr, 4), "n": n}
            continue

        # 2-sigma check
        se = math.sqrt(holdout_hr * (1 - holdout_hr) / n)
        z = (live_hr - holdout_hr) / se if se > 0 else 0
        status = "drifting" if z < -2.0 else ("excelling" if z > 2.0 else "on_track")

        drift[sport_name] = {
            "status": status,
            "live_hr": round(live_hr, 4),
            "holdout_hr": round(holdout_hr, 4),
            "n": n,
            "z_score": round(z, 2),
        }

    return drift


def _summary(config, ledger) -> dict:
    rows = ledger.rows()
    today = utc_now().astimezone(EASTERN).date().isoformat()

    def _et_day(value: str) -> str:
        try:
            return parse_utc(value).astimezone(EASTERN).date().isoformat()
        except (KeyError, ValueError):
            return ""

    created_today = [row for row in rows if _et_day(row["created_at_utc"]) == today]
    settled_today = [row for row in rows if row["settled_at_utc"] and _et_day(row["settled_at_utc"]) == today]
    open_rows = [row for row in rows if row["status"] == "open"]
    settled_qualified = [
        row for row in rows if row["status"] == "settled" and row["record_type"] == "QUALIFIED_SHADOW_CALL"
    ]
    pnl = sum(float(row["pnl_units"] or 0) for row in settled_qualified)
    clv_values = [float(row["probability_clv"]) for row in rows if row["probability_clv"]]
    measured_edge_rows = [row for row in rows if row["model_version"].startswith("measured-edge")]
    measured_edge_settled = [row for row in measured_edge_rows if row["status"] == "settled"]
    return {
        "date_et": today,
        "picks_logged_today": len(created_today),
        "picks_settled_today": len(settled_today),
        "open_picks": len(open_rows),
        "open_units": round(
            sum(
                float(row["units"] or 0) for row in open_rows if row["record_type"] == "QUALIFIED_SHADOW_CALL"
            ),
            2,
        ),
        "qualified_pnl_units_all_time": round(pnl, 4),
        "mean_probability_clv": round(sum(clv_values) / len(clv_values), 6) if clv_values else None,
        "bankroll_reference_units": config["bankroll"]["reference_units"],
        "research_progress": {
            "measured_edge_forward_picks": len(measured_edge_rows),
            "measured_edge_settled": len(measured_edge_settled),
            "iteration_policy": "continuous",
            "promotion_requires": "versioned walk-forward ablation and locked holdout",
        },
        "model_drift": _drift_check(settled_qualified, config),
        "note": "Shadow research accounting only; no real-money authorization.",
    }


def _row_artifact_qualified(row: dict[str, str], config: dict) -> bool:
    """Whether a ledger row's backing model ARTIFACT is genuinely qualified,
    not just config-declared.

    ``config/model.yaml`` can set ``status: shadow_qualified`` with an
    explicit ``qualification_override`` for a league whose artifact itself
    never cleared holdout validation (e.g. MLB v6, 2026-07:
    ``qualification.meets_primary_holdout_metrics`` is false --
    "running live for observation" ahead of any real promotion decision).
    That's a legitimate, documented choice for shadow logging, but
    real-money execution (PolymarketExecutor.execute's ``artifact_qualified``
    gate) must not treat an override-only qualification as equivalent to a
    genuinely validated one.

    Two artifact schemas exist in this project (verified against real
    artifact files, not assumed): learned MLB/NBA/WNBA/NFL artifacts record
    ``qualification.meets_primary_holdout_metrics``; Elo-baseline artifacts
    (esports, KBO, NPB -- identifiable by ``k``+``ratings`` fields, the same
    shape check dashboard_server.py's ``_ml_cell`` uses) record a top-level
    ``qualified_for_betting`` instead. Neither schema has a top-level
    ``qualified`` field -- checking one that doesn't exist would silently
    return False for every artifact, including genuinely qualified ones.

    Fails closed (returns False) whenever the artifact can't be loaded or
    matched to the exact version this row was computed from -- an
    unverifiable claim of qualification is not qualification.
    """
    model_config = config.get("models", {}).get(str(row.get("league", "")).upper(), {})
    artifact_value = model_config.get("production_artifact")
    if not artifact_value:
        return False
    artifact_path = Path(artifact_value)
    if not artifact_path.is_absolute():
        artifact_path = PROJECT_ROOT / artifact_path
    if not artifact_path.exists():
        return False
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    row_hash = str(row.get("model_artifact_hash") or "")
    if row_hash and row_hash != str(artifact.get("artifact_hash", "")):
        # The config's current production artifact isn't the exact version
        # this row was priced from (e.g. a newer artifact has since been
        # promoted) -- can't verify qualification against a version that no
        # longer matches, so refuse to claim it.
        return False
    if "k" in artifact and "ratings" in artifact:
        return bool(artifact.get("qualified_for_betting", False))
    return bool(artifact.get("qualification", {}).get("meets_primary_holdout_metrics", False))


def _verify_chain(events_path, ledger) -> dict:
    """Audit-chain link/hash verification plus ledger<->audit reconciliation."""
    import hashlib

    previous = "0" * 64
    lines = 0
    breaks: list[dict] = []
    created: set[str] = set()
    removed: set[str] = set()
    path = Path(events_path)
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                lines += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    breaks.append({"line": number, "kind": "unparseable"})
                    continue
                if event.get("previous_hash") != previous:
                    breaks.append({"line": number, "kind": "broken_link"})
                canonical = {key: value for key, value in event.items() if key != "event_hash"}
                digest = hashlib.sha256(
                    json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                ).hexdigest()
                if digest != event.get("event_hash"):
                    breaks.append({"line": number, "kind": "hash_mismatch"})
                previous = event.get("event_hash", previous)
                event_type = event.get("event_type")
                if event_type in ("pick_created", "research_observation_created"):
                    created.add(str(event.get("subject_id")))
                elif event_type == "pick_removed":
                    removed.add(str(event.get("subject_id")))
    ledger_ids = {row["pick_id"] for row in ledger.rows()}
    deleted_unaudited = created - removed - ledger_ids
    return {
        "audit_lines": lines,
        "breaks": breaks,
        "break_count": len(breaks),
        "ledger_rows": len(ledger_ids),
        "audited_created": len(created),
        "audited_removed": len(removed),
        "rows_missing_creation_event": sorted(ledger_ids - created),
        "created_but_absent_without_removal_event": len(deleted_unaudited),
        "chain_intact": not breaks,
        "reconciled": not deleted_unaudited and not (ledger_ids - created),
        "note": (
            "created_but_absent_without_removal_event counts historical deletions "
            "made before the audited remove_open_rows path existed."
        ),
    }


def _handle_ban(args: argparse.Namespace, bans: TeamBanList) -> object:
    if args.ban_command == "list":
        return [asdict(entry) for entry in bans.list()]
    team, banned = bans.check(args.league, args.team)
    if args.ban_command == "check":
        return {
            "league": team.league.value,
            "canonical_team_id": team.canonical_team_id,
            "canonical_name": team.canonical_name,
            "banned": banned,
        }
    entry, changed = (
        bans.add(args.league, args.team, args.reason, args.review_after)
        if args.ban_command == "add"
        else bans.remove(args.league, args.team)
    )
    return {**asdict(entry), "changed": changed, "banned": args.ban_command == "add"}


def cmd_init_ledger(args, config, registry, bans, ledger, audit, data_root) -> dict:
    ledger.initialize()
    output = {"ledger": str(ledger.path), "status": "ready", "schema_version": LEDGER_SCHEMA_VERSION}
    return output


def cmd_report(args, config, registry, bans, ledger, audit, data_root) -> dict:
    filters = {
        key: value
        for key, value in {
            "record_type": args.record_type,
            "origin": args.origin,
            "model_version": args.model_version,
            "calibration_version": args.calibration_version,
            "league": args.league,
            "market": args.market,
        }.items()
        if value is not None
    }
    output = ledger.report(filters, by_odds_range=args.by_odds_range)
    return output


def cmd_models(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = {league.value: asdict(model_spec(league)) for league in MODEL_SPECS}
    return output


def cmd_summary(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = _summary(config, ledger)
    return output


def cmd_live_portfolio(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = PolymarketExecutor(audit).portfolio_snapshot()
    return output


def cmd_order_status(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = PolymarketExecutor(audit).order_snapshots(args.order_id)
    return output


def cmd_polymarket_slate(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = _polymarket_slate(args, config)
    return output


def cmd_polymarket_snapshot(args, config, registry, bans, ledger, audit, data_root) -> dict:
    snapshot = PolymarketUSClient().snapshot(args.slug)
    if args.sport:
        event_day = utc_now().astimezone(EASTERN).date().isoformat()
        store = PolymarketSnapshotStore.for_sport_date(data_root, args.sport, event_day)
    else:
        store = PolymarketSnapshotStore(polymarket_snapshot_path(config))
    store.append(snapshot)
    output = snapshot
    return output


def cmd_polymarket_ledger_prices(args, config, registry, bans, ledger, audit, data_root) -> dict:
    contracts = []
    for value in args.contract:
        sport_day, separator, slug = value.partition("=")
        sport, day_separator, contract_day = sport_day.partition("@")
        if not separator or not sport or not slug:
            raise ValueError("contract must use SPORT[@GAME_DATE]=MARKET_SLUG")
        if sport not in SPORTS:
            raise ValueError(f"unsupported contract sport: {sport}")
        if day_separator:
            date.fromisoformat(contract_day)
        contracts.append(
            {
                "sport": sport,
                "market_slug": slug,
                "game_date": contract_day if day_separator else args.date,
            }
        )
    output = refresh_contract_snapshots(PolymarketUSClient(), contracts, data_root, args.date)
    return output


def cmd_polymarket_clv(args, config, registry, bans, ledger, audit, data_root) -> dict:
    if not 0 < args.decision_price < 1:
        raise ValueError("decision price must be between 0 and 1")
    market = PolymarketUSClient().market(args.slug)
    if utc_now() < parse_utc(market["gameStartTime"]):
        raise ValueError("event has not started; a final pregame closing snapshot does not exist yet")
    if args.sport and args.date:
        store = PolymarketSnapshotStore.for_sport_date(data_root, args.sport, args.date)
    else:
        store = PolymarketSnapshotStore(polymarket_snapshot_path(config))
    closing = store.closing_snapshot(args.slug, market["gameStartTime"])
    if closing is None:
        raise ValueError("no stored pregame snapshot exists for this market")
    closing_price = closing[args.side]["price"]
    if closing_price is None:
        raise ValueError("stored pregame snapshot has no executable price for this side")
    output = {
        "market_slug": args.slug,
        "side": args.side,
        "decision_price_probability": args.decision_price,
        "decision_american_odds": probability_to_american(args.decision_price),
        "closing_price_probability": closing_price,
        "closing_american_odds": probability_to_american(closing_price),
        "probability_clv": round(closing_price - args.decision_price, 6),
        "closing_snapshot_observed_at_utc": closing["observed_at_utc"],
        "definition": "closing executable probability minus decision executable probability",
    }
    return output


def cmd_ingest(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = Ingestor(data_root, audit=audit).ingest_scores(args.sport, args.date)
    return output


def cmd_wnba_availability_capture(args, config, registry, bans, ledger, audit, data_root) -> dict:
    availability_observed_at = parse_utc(args.observed_at) if args.observed_at else utc_now()
    output = {
        "official_report": capture_latest_report(
            data_root,
            observed_at=availability_observed_at,
        ),
        "espn_events": [
            capture_espn_event_injuries(
                data_root,
                event_id=event_id,
                client=ESPNClient(),
                observed_at=availability_observed_at,
            )
            for event_id in args.event_id
        ],
    }
    return output


def cmd_bootstrap(args, config, registry, bans, ledger, audit, data_root) -> dict:
    ingestor = Ingestor(data_root, audit=audit)
    if args.all:
        output = {sport: ingestor.bootstrap(sport, args.from_date, args.to_date) for sport in ESPN_SPORTS}
    elif args.sport:
        output = ingestor.bootstrap(args.sport, args.from_date, args.to_date)
    else:
        raise ValueError("provide --sport or --all")
    return output


def cmd_esports_backfill(args, config, registry, bans, ledger, audit, data_root) -> dict:
    if args.all:
        titles = tuple(TITLE_SPECS)
    elif args.title:
        titles = (args.title,)
    else:
        raise ValueError("provide --title or --all")
    output = {title: backfill_esports(data_root, title, args.from_date, args.to_date) for title in titles}
    return output


def cmd_validate_esports(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = validate_all_esports_baselines(
        data_root,
        args.titles,
        _research_models_dir() if args.write_artifacts else None,
    )
    destination = PROJECT_ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    output["report_path"] = str(destination)
    return output


def cmd_esports_forecast(args, config, registry, bans, ledger, audit, data_root) -> dict:
    from .forecast import _log_esports_forecast  # local: breaks commands<->forecast cycle

    # Manual/debugging use only. run_daily.sh no longer calls this —
    # its --all invocation duplicated the esports logging that
    # `forecast --all` (Step 4) already does per title, producing
    # near-simultaneous duplicate research rows for the same contract.
    titles = list(TITLE_SPECS) if getattr(args, "all", False) else [args.title]
    output = {}
    for title in titles:
        forecast = forecast_esports_slate(
            data_root,
            _research_models_dir(),
            title,
            args.date,
            args.timezone,
        )
        if getattr(args, "log", False):
            logged = _log_esports_forecast(
                forecast,
                config,
                research_ledger(data_root, title),
                flat_mode=False,
                gated_ledger=research_ledger(data_root, title, gated=True),
            )
            forecast["logged"] = logged
        output[title] = forecast
    return output


def cmd_international_baseball_backfill(args, config, registry, bans, ledger, audit, data_root) -> dict:
    if args.all:
        leagues = tuple(INTERNATIONAL_BASEBALL_LEAGUE_SPECS)
    elif args.league:
        leagues = (args.league,)
    else:
        raise ValueError("provide --league or --all")
    output = {
        league: backfill_international_baseball(data_root, league, args.from_date, args.to_date)
        for league in leagues
    }
    return output


def cmd_validate_international_baseball(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = validate_all_international_baseball_baselines(
        data_root,
        args.leagues,
        _research_models_dir() if args.write_artifacts else None,
    )
    destination = PROJECT_ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    output["report_path"] = str(destination)
    return output


def cmd_international_baseball_forecast(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = forecast_international_baseball_slate(
        data_root,
        _research_models_dir(),
        args.league,
        args.date,
        args.timezone,
    )
    return output


def cmd_bootstrap_entities(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = Ingestor(data_root, audit=audit).bootstrap_entities(args.league, entity_registry_path(config))
    return output


def cmd_features(args, config, registry, bans, ledger, audit, data_root) -> dict:
    store = FeatureStore(data_root)
    snapshots = store.compute_all(args.sport, args.date)
    output = {
        name: {"computation_hash": snap["computation_hash"], "input_games": snap["input_games"]}
        for name, snap in snapshots.items()
    }
    return output


def cmd_backtest(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = walk_forward_backtest(
        FeatureStore(data_root),
        args.sport,
        args.start,
        args.end,
        market_lines_path=args.market_lines,
        confidence_threshold=args.confidence_threshold,
        locked_holdout=args.locked_holdout,
    )
    if args.output:
        destination = PROJECT_ROOT / args.output
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        output["report_path"] = str(destination)
    return output


def cmd_validate_models(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = run_validation_audit(
        FeatureStore(data_root),
        args.sports,
        PROJECT_ROOT / "data/historical/mlb_market_lines_reconstructed.jsonl",
    )
    destination = PROJECT_ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.write_artifacts:
        output["production_artifacts"] = write_production_artifacts(output, PROJECT_ROOT / "config/models")
    destination.write_text(
        json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    output["report_path"] = str(destination)
    return output


def cmd_validate_totals(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = validate_all_total_score_models(
        FeatureStore(data_root),
        args.sports,
        _research_models_dir() if args.write_artifacts else None,
    )
    destination = PROJECT_ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    output["report_path"] = str(destination)
    return output


def cmd_reconstruct_mlb_markets(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = Ingestor(data_root, audit=audit).reconstruct_mlb_markets(
        args.start,
        args.end,
        PROJECT_ROOT / args.output,
    )
    return output


def cmd_refresh_mlb_baselines(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = refresh_if_due(data_root, PROJECT_ROOT, min_days=args.min_days, force=args.force)
    return output


def cmd_train_residual(args, config, registry, bans, ledger, audit, data_root) -> dict:
    rows = [
        ResidualTrainingRow(
            settled_at_utc=row["settled_at_utc"],
            model_probability=float(row["model_probability"]),
            market_probability=float(
                row["decision_no_vig_probability"]
                or row["decision_raw_implied_probability"]
                or row["market_implied_probability"]
            ),
            outcome=1 if row["result"] == "win" else 0,
        )
        for row in ledger.rows()
        if row["status"] == "settled"
        and row["result"] in {"win", "loss"}
        and row["settled_at_utc"]
        and row["model_probability"]
    ]
    model = MarketResidualModel.train(rows)
    destination = PROJECT_ROOT / args.output
    model.save(destination)
    output = {
        "model_version": model.version,
        "sample_size": model.sample_size,
        "identity_fallback": model.is_identity,
        "artifact": str(destination),
    }
    return output


def cmd_execute(args, config, registry, bans, ledger, audit, data_root) -> dict:
    row = next((r for r in ledger.rows() if r["pick_id"] == args.pick_id), None)
    if row is None:
        raise KeyError(f"unknown pick id: {args.pick_id}")
    manual = bool(args.manual_research_order)
    execution_config = config.get("execution", {})
    if manual:
        if not execution_config.get("allow_manual_research_orders", False):
            raise ExecutionGateError("REFUSED: manual research orders are disabled in config.")
        league = League(row["league"])
        active_version = (config.get("models", {}).get(league.value, {}) or {}).get(
            "active_production_version"
        )
        if (
            execution_config.get("manual_research_require_active_model", True)
            and row.get("model_version") != active_version
        ):
            raise ExecutionGateError(
                "REFUSED: manual order row was not produced by the active model version."
            )
        model_probability = float(row.get("model_probability") or 0)
        market_probability = float(row.get("market_implied_probability") or 0)
        edge = model_probability - market_probability
        if execution_config.get("manual_research_require_positive_edge", True) and edge <= 0:
            raise ExecutionGateError("REFUSED: manual research order has no positive edge.")
        # The "limit below model probability" rule is a BUY guard (do not
        # pay more than fair value). A SELL is an exit at a target price
        # and is exempt.
        if (
            args.action == "buy"
            and execution_config.get("manual_research_require_positive_edge", True)
            and args.price >= model_probability
        ):
            raise ExecutionGateError("REFUSED: manual buy limit must remain below the model probability.")
        for team_key in ("away_team", "home_team"):
            _, banned = bans.check(league, row[team_key])
            if banned:
                raise ExecutionGateError(f"REFUSED: {row[team_key]} is on the permanent team ban list.")
    policy = unit_policy(config)
    if manual:
        maximum_units = edge_scaled_units(
            float(row["model_probability"]),
            float(row.get("model_uncertainty") or 0),
            int(row["american_odds"]),
            policy,
        )
    else:
        maximum_units = float(row.get("units") or 0)
    # A SELL is an exit — it returns capital, so no dollar cost cap
    # applies. Buys keep the unit cap.
    maximum_cost = None if args.action == "sell" else round(maximum_units * policy.unit_value_usd, 2)
    ticket = OrderTicket(
        market_slug=args.market_slug,
        token_side=args.side,
        action=args.action,
        order_type=args.order_type,
        price=args.price,
        size_shares=args.size_shares,
        pick_id=args.pick_id,
        estimated_cost_usd=round(args.price * args.size_shares, 2),
        maximum_cost_usd=maximum_cost,
        authorization_type=("manual_research_override" if manual else "qualified_model"),
    )
    executor = PolymarketExecutor(audit)
    output = executor.execute(
        ticket,
        row,
        execute_flag=args.execute_flag,
        user_command=True,
        manual_research_order=manual,
        artifact_qualified=_row_artifact_qualified(row, config),
    )
    return output


def cmd_sell_position(args, config, registry, bans, ledger, audit, data_root) -> dict:
    # Close a live exchange position you already hold. There is no
    # model pick and no unit cap (a sell returns capital); the hard
    # gates (credentials, post-only, confirmation, audit) still apply.
    ticket = OrderTicket(
        market_slug=args.market_slug,
        token_side=args.side,
        action="sell",
        order_type="limit_gtc",
        price=args.price,
        size_shares=args.size_shares,
        pick_id=f"live-position:{args.market_slug}:{args.side}",
        estimated_cost_usd=round(args.price * args.size_shares, 2),
        maximum_cost_usd=None,
        authorization_type="live_position_exit",
    )
    synthetic_row = {
        "record_type": "LIVE_POSITION_EXIT",
        "status": "open",
        "reason_code": "LIVE_POSITION_EXIT",
    }
    output = PolymarketExecutor(audit).execute(
        ticket,
        synthetic_row,
        execute_flag=args.execute_flag,
        user_command=True,
        manual_research_order=True,
    )
    return output


def cmd_exposure(args, config, registry, bans, ledger, audit, data_root) -> dict:
    rows = ledger.rows()
    qualified_open = [
        row for row in rows if row["record_type"] == "QUALIFIED_SHADOW_CALL" and row["status"] == "open"
    ]
    output = {
        "open_units": round(sum(float(row["units"] or 0) for row in qualified_open), 2),
        "open_qualified_calls": len(qualified_open),
        "zero_unit_research_observations": sum(row["record_type"] == "RESEARCH_OBSERVATION" for row in rows),
        "starting_bankroll_units": config["bankroll"]["reference_units"],
        "note": "Shadow research accounting only; no real-money authorization",
    }
    return output


def cmd_ban_team(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = _handle_ban(args, bans)
    return output


def cmd_collect_scores(args, config, registry, bans, ledger, audit, data_root) -> dict:
    from ..data_sources.odds_soccer_scores import collect_soccer_scores

    output = collect_soccer_scores(days_from=args.days)
    return output


def cmd_verify_checklist(args, config, registry, bans, ledger, audit, data_root) -> dict:
    from ..source_policy import DEFAULT_SOURCES
    from ..verification_checklist import format_checklist, run_checklist

    sport = args.sport.lower()
    source_keys = [key for key, spec in DEFAULT_SOURCES.items() if sport in spec.leagues]
    artifact_hashes: list[str] = []
    model_config = config["models"].get(sport.upper())
    if model_config and model_config.get("production_artifact"):
        artifact_path = PROJECT_ROOT / model_config["production_artifact"]
        if artifact_path.exists():
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            if artifact.get("artifact_hash"):
                artifact_hashes.append(artifact["artifact_hash"])
    research_units = [
        float(row["units"] or 0)
        for row in ledger.rows()
        if row["league"].lower() == sport and row["record_type"] == RecordType.RESEARCH_OBSERVATION.value
    ]
    results = run_checklist(
        source_keys=source_keys or None,
        artifact_hashes=artifact_hashes or None,
        research_units=research_units or None,
    )
    output = {
        "sport": sport,
        "results": [vars(result) for result in results],
        "formatted": format_checklist(results),
    }
    return output


def cmd_verify_chain(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = _verify_chain(audit_path(config), ledger)
    return output


def cmd_score_research(args, config, registry, bans, ledger, audit, data_root) -> dict:
    if bool(args.pick_ids) == bool(args.all_research):
        raise ValueError("provide either --pick-id or --all-research")
    pick_ids = args.pick_ids or [
        row["pick_id"]
        for row in ledger.rows()
        if row["record_type"] == "RESEARCH_OBSERVATION" and row["status"] == "settled"
    ]
    rows = ledger.score_research(pick_ids, args.units, args.note)
    output = {
        "scored_records": len(rows),
        "research_score_units_each": args.units,
        "research_pnl_units": round(sum(float(row["research_pnl_units"]) for row in rows), 6),
        "note": args.note,
    }
    return output


def cmd_call(args, config, registry, bans, ledger, audit, data_root) -> dict:
    request = PickRequest(
        event_start_utc=args.start,
        event_id=args.event_id,
        league=League(args.league),
        away_team=args.away,
        home_team=args.home,
        market_type=MarketType(args.market),
        selection=args.selection,
        line=args.line,
        sportsbook=args.sportsbook,
        american_odds=args.american_odds,
        model_probability=args.probability,
        model_uncertainty=args.model_uncertainty,
        model_version=args.model_version,
        rationale=args.rationale,
        risks=args.risks,
        model_origin=ModelOrigin(args.origin),
        model_state=ModelState(args.model_state),
        baseline_identifier=args.baseline_id,
        observed_at_utc=args.observed_at,
        model_artifact_hash=args.model_artifact_hash,
        calibration_method=args.calibration_method,
        calibration_version=args.calibration_version,
        calibration_artifact_hash=args.calibration_artifact_hash,
        feature_schema_version=args.feature_schema_version,
        entity_map_version=registry.version,
        code_revision=args.code_revision,
        decision_no_vig_probability=args.decision_no_vig_probability,
        decision_consensus_probability=args.decision_consensus_probability,
        decision_consensus_line=args.decision_consensus_line,
    )
    request.validate()
    away = registry.resolve(request.league, request.away_team, request.event_start_utc)
    home = registry.resolve(request.league, request.home_team, request.event_start_utc)
    exposure = ledger.exposure(request, canonical_team_ids=(away.canonical_team_id, home.canonical_team_id))
    eligibility = evaluate_eligibility(
        request,
        registry,
        bans,
        exposure,
        unit_policy(config),
        maximum_age_hours=float(config["project"].get("maximum_data_age_hours", 12)),
        maximum_unreviewed_disagreement=float(
            config["project"].get("maximum_unreviewed_market_disagreement", 0.10)
        ),
    )
    row = ledger.append_evaluated(request, eligibility)
    output = {
        "decision": eligibility.decision,
        "record_type": eligibility.record_type.value,
        "reason_code": eligibility.reason_code,
        "units": eligibility.units,
        "pick": row,
    }
    return output


def cmd_update_closing(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = ledger.update_closing(
        args.pick_id,
        args.closing_line,
        args.closing_american_odds,
        closing_no_vig_probability=args.closing_no_vig_probability,
        closing_consensus_probability=args.closing_consensus_probability,
        closing_consensus_line=args.closing_consensus_line,
    )
    return output


def cmd_void(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = ledger.void(args.pick_id, args.reason)
    return output


def cmd_review_loss(args, config, registry, bans, ledger, audit, data_root) -> dict:
    output = ledger.review_loss(args.pick_id, args.classification, args.cause, args.action)
    return output


def cmd_freeze_production(args, config, registry, bans, ledger, audit, data_root) -> dict:
    registry = ProductionRegistry()
    snapshots = registry.freeze()
    store = FrozenProductionStore()
    store.write(registry)
    output = {
        "status": "frozen",
        "frozen_at_utc": snapshots[0].frozen_at_utc if snapshots else "",
        "champions": [s.to_dict() for s in snapshots],
    }
    return output


def cmd_compare_champion(args, config, registry, bans, ledger, audit, data_root) -> dict:
    import json as _json

    def _load_predictions(pth):
        raw = Path(pth).read_text(encoding="utf-8")
        if raw.strip().startswith("["):
            return _json.loads(raw)
        return [_json.loads(line) for line in raw.strip().splitlines() if line.strip()]

    champion_preds = _load_predictions(args.champion_predictions)
    challenger_preds = _load_predictions(args.challenger_predictions)
    verdict = compare_champion_vs_challenger(
        challenger_predictions=challenger_preds,
        champion_predictions=champion_preds,
        sport=args.sport,
        market=args.market,
    )
    output = {
        "status": verdict.status,
        "paired_metrics": verdict.paired_metrics,
        # Pass the CI dicts through as-is: each value is already a
        # JSON-serializable dict (status/dates/point_estimate/
        # ci_low/ci_high). The previous version indexed values with
        # [0]/[1] -- that raised KeyError on dict values, which the
        # outer except swallowed and reported as
        # NO_CALL_INVALID_MARKET for a perfectly healthy comparison.
        "bootstrap_ci": verdict.bootstrap_ci,
        "failures": verdict.failures,
        "recommendation": verdict.recommendation,
    }
    return output
