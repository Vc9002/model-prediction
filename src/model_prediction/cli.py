"""model-prediction CLI.

Daily loop: polymarket-slate -> forecast --log -> settle --all-unsettled ->
summary (or `daily` for all of it). Everything is shadow/paper by default;
the only real-money path is the `execute` subcommand behind the hard gate in
``data_sources/polymarket_execute.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from .audit import AuditLog
from .backtester import walk_forward_backtest
from .bans import TeamBanList
from .config import (
    PROJECT_ROOT,
    audit_path,
    config_path,
    entity_registry_path,
    ledger_path,
    load_config,
    market_odds_snapshot_path,
    polymarket_snapshot_path,
    unit_policy,
)
from .data_sources.espn import ESPNClient, ESPNMLBClient
from .data_sources.kalshi import DEFERRED_MESSAGE as KALSHI_DEFERRED_MESSAGE
from .data_sources.mlb_market_odds import MarketOddsSnapshotStore, MLBMarketOddsFeed
from .data_sources.polymarket_execute import (
    ExecutionGateError,
    OrderTicket,
    PolymarketExecutor,
)
from .data_sources.polymarket_us import (
    POLYMARKET_SPORT_LEAGUES,
    PolymarketSnapshotStore,
    PolymarketUSClient,
    capture_slate_snapshots,
    probability_to_american,
    refresh_contract_snapshots,
)
from .data_sources.the_odds_api import TheOddsAPIClient
from .domain import (
    LOSS_CLASSIFICATIONS,
    League,
    MarketType,
    ModelOrigin,
    ModelState,
    PickRequest,
    parse_utc,
    utc_now,
)
from .eligibility import evaluate_eligibility
from .entities import EntityRegistry, EntityResolutionError
from .features.base import FeatureStore
from .forward import build_mlb_slate
from .ingest import Ingestor
from .ledger import LEDGER_SCHEMA_VERSION, DuplicatePickError, PickLedger
from .learned_forward import build_learned_moneyline_slate, match_executable_quote
from .models import MODEL_SPECS
from .models.market_residual import MarketResidualModel, ResidualTrainingRow
from .models.mlb import load_formula_spec
from .total_score import validate_all_total_score_models
from .units import edge_scaled_units
from .validation import run_validation_audit, write_production_artifacts


SPORTS = tuple(POLYMARKET_SPORT_LEAGUES)
LEARNED_PRODUCTION_SPORTS = ("mlb", "nba", "wnba", "nfl", "soccer")
EASTERN = ZoneInfo("America/New_York")

# League value on a ledger row -> ESPN league key(s) to search for results.
_LEDGER_LEAGUE_TO_ESPN = {
    "MLB": ("MLB",),
    "NBA": ("NBA",),
    "WNBA": ("WNBA",),
    "NFL": ("NFL",),
    "SOCCER": ("EPL", "LA_LIGA", "BUNDESLIGA", "SERIE_A", "MLS", "UCL", "WORLD_CUP"),
    "WORLD_CUP": ("WORLD_CUP",),
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="model-prediction")
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("init-ledger")

    report = commands.add_parser("report")
    report.add_argument(
        "--record-type", choices=["qualified", "research", "QUALIFIED_SHADOW_CALL", "RESEARCH_OBSERVATION"]
    )
    report.add_argument("--origin", choices=[item.value for item in ModelOrigin])
    report.add_argument("--model-version")
    report.add_argument("--calibration-version")
    report.add_argument("--league", choices=[item.value for item in League])
    report.add_argument("--market", choices=[item.value for item in MarketType])
    report.add_argument("--by-odds-range", action="store_true")

    commands.add_parser("exposure")
    commands.add_parser("models")
    commands.add_parser("summary")
    commands.add_parser(
        "live-portfolio",
        help="read authenticated exchange positions, activities, and balances",
    )

    slate = commands.add_parser(
        "polymarket-slate", help="read dated sports slates from the public Polymarket US API"
    )
    slate.add_argument("--sport", choices=SPORTS)
    slate.add_argument("--league", help="single gateway league key (e.g. MLB, EPL, WTA)")
    slate.add_argument("--all", action="store_true", help="every supported sport")
    slate.add_argument("--date", required=True)
    slate.add_argument("--timezone", default="America/New_York")
    slate.add_argument("--provider", default="polymarket", choices=["polymarket", "kalshi"])
    slate.add_argument(
        "--no-snapshot-bbo",
        action="store_true",
        help="disable the default prospective BBO capture for discovered contracts",
    )

    snapshot = commands.add_parser(
        "polymarket-snapshot", help="freeze a read-only BBO snapshot for later pregame CLV"
    )
    snapshot.add_argument("--slug", required=True)
    snapshot.add_argument("--sport", help="store under data/odds/{sport}/{date}/ instead of the flat file")

    ledger_prices = commands.add_parser(
        "polymarket-ledger-prices",
        help="refresh BBOs only for exact contracts selected from today's visible ledger",
    )
    ledger_prices.add_argument("--date", required=True)
    ledger_prices.add_argument(
        "--contract",
        action="append",
        default=[],
        metavar="SPORT=MARKET_SLUG",
        help="repeat for each unique ledger contract",
    )

    clv = commands.add_parser("polymarket-clv", help="probability CLV from the final stored pregame snapshot")
    clv.add_argument("--slug", required=True)
    clv.add_argument("--side", required=True, choices=["long", "short"])
    clv.add_argument("--decision-price", required=True, type=float)
    clv.add_argument("--sport")
    clv.add_argument("--date")

    forecast = commands.add_parser(
        "forecast", help="pregame learned LR + confidence-gate moneyline slate"
    )
    forecast.add_argument("--sport", choices=SPORTS)
    forecast.add_argument("--all", action="store_true")
    forecast.add_argument("--date", required=True)
    forecast.add_argument("--log", action="store_true", help="log only rows with exact executable prices")
    forecast.add_argument(
        "--model",
        choices=("learned", "legacy-measured-edge"),
        default="learned",
        help="default learned production path; legacy option is MLB-only research rollback",
    )

    log_cmd = commands.add_parser("log", help="alias for forecast --log")
    log_cmd.add_argument("--sport", choices=SPORTS, default="mlb")
    log_cmd.add_argument("--date", required=True)
    log_cmd.add_argument(
        "--model", choices=("learned", "legacy-measured-edge"), default="learned"
    )

    settle = commands.add_parser("settle")
    settle.add_argument("--pick-id")
    settle.add_argument("--away-score", type=int)
    settle.add_argument("--home-score", type=int)
    settle.add_argument(
        "--all-unsettled", action="store_true", help="grade every started open pick from ESPN"
    )
    settle.add_argument("--void-postponed", action="store_true")
    settle.add_argument("--closing-line", type=float)
    settle.add_argument("--closing-american-odds", type=int)
    settle.add_argument("--closing-no-vig-probability", type=float)
    settle.add_argument("--closing-consensus-probability", type=float)
    settle.add_argument("--closing-consensus-line", type=float)

    daily = commands.add_parser("daily", help="slate + forecast + log + settle + summary in one run")
    daily.add_argument("--date", required=True)

    ingest = commands.add_parser("ingest", help="cache one date of ESPN scores locally")
    ingest.add_argument("--sport", required=True, choices=SPORTS)
    ingest.add_argument("--date", required=True)

    bootstrap = commands.add_parser("bootstrap", help="idempotent historical backfill from ESPN")
    bootstrap.add_argument("--sport", choices=SPORTS)
    bootstrap.add_argument("--all", action="store_true")
    bootstrap.add_argument("--from", dest="from_date", required=True)
    bootstrap.add_argument("--to", dest="to_date")

    entities = commands.add_parser("bootstrap-entities", help="merge ESPN team lists into the registry")
    entities.add_argument("--league", required=True)

    features = commands.add_parser("features", help="compute point-in-time feature snapshots")
    features.add_argument("--sport", required=True, choices=SPORTS)
    features.add_argument("--date", required=True)
    features.add_argument("--refresh", action="store_true")

    backtest = commands.add_parser("backtest", help="walk-forward chronological backtest")
    backtest.add_argument("--sport", required=True, choices=SPORTS)
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument(
        "--market-lines",
        help="optional historical two-sided quote JSONL; absent quotes disable ROI/CLV claims",
    )
    backtest.add_argument(
        "--confidence-threshold",
        type=float,
        help="selective call threshold; required to evaluate model qualification",
    )
    backtest.add_argument(
        "--locked-holdout",
        action="store_true",
        help="affirm this untouched evaluation window was locked before model selection",
    )
    backtest.add_argument("--output", help="optional JSON report destination under the project root")

    validate = commands.add_parser(
        "validate-models",
        help="learn thresholds on chronological validation data and grade a locked holdout",
    )
    validate.add_argument(
        "--sports",
        nargs="+",
        choices=("mlb", "nba", "wnba", "nfl", "soccer"),
        default=("mlb", "nba", "wnba", "nfl", "soccer"),
    )
    validate.add_argument(
        "--output",
        default="outputs/latest/learned-model-validation.json",
        help="JSON report destination under the project root",
    )
    validate.add_argument(
        "--write-artifacts",
        action="store_true",
        help="write hash-verified learned artifacts under config/models",
    )

    total_validate = commands.add_parser(
        "validate-totals",
        help="validate research-only point-in-time combined-score models",
    )
    total_validate.add_argument(
        "--sports",
        nargs="+",
        choices=("mlb", "nba", "wnba", "nfl"),
        default=("mlb", "nba", "wnba", "nfl"),
    )
    total_validate.add_argument(
        "--output",
        default="outputs/latest/total-score-validation.json",
    )
    total_validate.add_argument("--write-artifacts", action="store_true")

    reconstruct = commands.add_parser(
        "reconstruct-mlb-markets",
        help="cache postgame ESPN summaries for diagnostic-only historical market testing",
    )
    reconstruct.add_argument("--start", required=True)
    reconstruct.add_argument("--end", required=True)
    reconstruct.add_argument("--output", default="data/historical/mlb_market_lines_reconstructed.jsonl")

    residual = commands.add_parser(
        "train-residual", help="train the market-residual layer on the rolling settled window"
    )
    residual.add_argument("--output", default="config/models/market-residual-v1.json")

    execute = commands.add_parser(
        "execute",
        help="REAL MONEY: place an order for a qualified pick (hard gate; requires --execute)",
    )
    execute.add_argument("--pick-id", required=True)
    execute.add_argument("--size-shares", type=float, required=True)
    execute.add_argument("--price", type=float, required=True, help="executable ask/bid, 0-1")
    execute.add_argument("--side", default="long", choices=["long", "short"])
    execute.add_argument("--action", default="buy", choices=["buy", "sell"])
    execute.add_argument("--order-type", default="limit_gtc", choices=["limit_gtc", "market"])
    execute.add_argument("--market-slug", required=True)
    execute.add_argument(
        "--execute",
        dest="execute_flag",
        action="store_true",
        help="actually place the order; without it this is a dry-run preview",
    )
    execute.add_argument(
        "--manual-research-order",
        action="store_true",
        help="explicitly authorize an active-model positive-edge research row as a manual order",
    )

    sell_position = commands.add_parser(
        "sell-position",
        help="place a resting SELL limit against a live exchange position (no model pick)",
    )
    sell_position.add_argument("--market-slug", required=True)
    sell_position.add_argument("--side", required=True, choices=["long", "short"])
    sell_position.add_argument("--price", type=float, required=True, help="limit price 0-1")
    sell_position.add_argument("--size-shares", type=float, required=True)
    sell_position.add_argument("--execute", dest="execute_flag", action="store_true")

    research_score = commands.add_parser("score-research")
    research_score.add_argument("--pick-id", action="append", dest="pick_ids")
    research_score.add_argument("--all-research", action="store_true")
    research_score.add_argument("--units", type=float, required=True)
    research_score.add_argument("--note", default="retrospective fixed-stake research scoring")

    call = commands.add_parser("call", help="freeze one pre-game prediction manually")
    call.add_argument("--league", required=True, choices=[item.value for item in League])
    call.add_argument("--event-id", required=True)
    call.add_argument("--start", required=True)
    call.add_argument("--away", required=True)
    call.add_argument("--home", required=True)
    call.add_argument("--market", required=True, choices=[item.value for item in MarketType])
    call.add_argument("--selection", required=True)
    call.add_argument("--line", type=float)
    call.add_argument("--american-odds", type=int, required=True)
    call.add_argument("--sportsbook", required=True)
    call.add_argument("--probability", type=float, required=True)
    call.add_argument("--model-uncertainty", type=float)
    call.add_argument("--model-version", required=True)
    call.add_argument(
        "--origin", choices=[item.value for item in ModelOrigin], default=ModelOrigin.ANALYST_ESTIMATE.value
    )
    call.add_argument(
        "--model-state", choices=[item.value for item in ModelState], default=ModelState.RESEARCH.value
    )
    call.add_argument("--baseline-id")
    call.add_argument("--observed-at")
    call.add_argument("--model-artifact-hash", default="")
    call.add_argument("--calibration-method", default="identity")
    call.add_argument("--calibration-version", default="identity-v1")
    call.add_argument("--calibration-artifact-hash", default="")
    call.add_argument("--feature-schema-version", default="1")
    call.add_argument("--code-revision", default="unknown")
    call.add_argument("--decision-no-vig-probability", type=float)
    call.add_argument("--decision-consensus-probability", type=float)
    call.add_argument("--decision-consensus-line", type=float)
    call.add_argument("--rationale", required=True)
    call.add_argument("--risks", default="")

    closing = commands.add_parser("update-closing")
    closing.add_argument("--pick-id", required=True)
    closing.add_argument("--closing-line", type=float)
    closing.add_argument("--closing-american-odds", type=int, required=True)
    closing.add_argument("--closing-no-vig-probability", type=float)
    closing.add_argument("--closing-consensus-probability", type=float)
    closing.add_argument("--closing-consensus-line", type=float)

    void = commands.add_parser("void")
    void.add_argument("--pick-id", required=True)
    void.add_argument("--reason", required=True)

    review = commands.add_parser("review-loss")
    review.add_argument("--pick-id", required=True)
    review.add_argument("--classification", required=True, choices=sorted(LOSS_CLASSIFICATIONS))
    review.add_argument("--cause", required=True)
    review.add_argument("--action", required=True)

    ban = commands.add_parser("ban-team")
    ban_commands = ban.add_subparsers(dest="ban_command", required=True)
    for action in ("add", "remove", "check"):
        child = ban_commands.add_parser(action)
        child.add_argument("--league", required=True, choices=[item.value for item in League])
        child.add_argument("--team", required=True)
        if action == "add":
            child.add_argument("--reason", default="manual_governance")
            child.add_argument("--review-after")
    ban_commands.add_parser("list")

    collect = commands.add_parser("collect-scores", help="pull recent soccer scores from The Odds API (free tier, last 3 days)")
    collect.add_argument("--days", type=int, default=3, help="days to look back (max 3 on free tier)")
    return root


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def _polymarket_slate(args, config) -> dict:
    if args.provider == "kalshi":
        return {"provider": "kalshi", "status": "deferred", "note": KALSHI_DEFERRED_MESSAGE}
    client = PolymarketUSClient()
    game_date = date.fromisoformat(args.date)
    if args.league:
        events = {args.league.upper(): client.slate(args.league, game_date, args.timezone)}
    elif args.all:
        events = {}
        for sport in SPORTS:
            for league, slate in client.sport_slate(sport, game_date, args.timezone).items():
                events[league] = slate
    elif args.sport:
        events = client.sport_slate(args.sport, game_date, args.timezone)
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
        "prospective_bbo_capture": bbo_capture,
        "note": (
            "Public market discovery plus prospective executable-BBO storage. "
            "Event-list quotes remain indicative."
        ),
    }


def _forecast_mlb(args_date: str, log: bool, config, registry, bans, ledger, audit) -> dict:
    """Legacy Measured Edge research path retained as an explicit rollback."""
    spec = load_formula_spec(PROJECT_ROOT / "config/models/mlb-analyst-poisson-trend-v0.2.yaml")
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
        PROJECT_ROOT / "config/models/measured-edge-margin-v1.json",
        PROJECT_ROOT / "config/models/measured-edge-totals-v1.json",
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
            try:
                logged.append(ledger.append_evaluated(request, eligibility, now=observed_at))
            except DuplicatePickError as error:
                duplicates.append(error.pick_id)
    return {
        "sport": "mlb",
        "model_name": "Measured Edge Paired Models",
        "model_versions": ["measured-edge-margin-v1", "measured-edge-totals-v1"],
        "game_date": args_date,
        "scheduled_games": scheduled,
        "market_calls_created": len(candidates),
        "logged": len(logged),
        "duplicate_pick_ids": duplicates,
        "skipped": skipped,
        "candidates": [asdict(candidate) for candidate in candidates],
        "note": "All entries are zero-unit research; closing odds are attached only after start.",
    }


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
) -> dict:
    """Default production forecast path for audited learned moneyline models."""
    model_config = config["models"][sport.upper()]
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
        candidates, skipped, scheduled = build_learned_moneyline_slate(
            sport=sport,
            game_date=args_date,
            store=FeatureStore(Path(ledger_path(config)).parent),
            client=ESPNClient(),
            artifact_path=artifact_path,
            observed_at=utc_now(),
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
    logged: list[dict] = []
    duplicates: list[str] = []
    unmatched: list[dict] = []
    if log and calls and registry is not None and bans is not None and ledger is not None:
        data_root = Path(ledger_path(config)).parent
        observed_at = utc_now()
        # Promotion to unit-carrying calls happens only via config review;
        # until then every learned pick logs as zero-unit research.
        configured_state = str(model_config.get("model_state", "research"))
        for candidate in calls:
            quote = match_executable_quote(data_root, sport, args_date, candidate)
            if quote is None:
                unmatched.append(
                    {"event_id": candidate.event_id,
                     "reason": "no stored executable moneyline BBO matched this matchup"}
                )
                continue
            # Gate: require minimum edge over executable Polymarket ask.
            # Model probability must exceed the market ask by at least 2% (absolute).
            model_edge = candidate.model_probability - quote["executable_ask"]
            if model_edge < 0.02:
                unmatched.append(
                    {"event_id": candidate.event_id,
                     "reason": f"model edge {model_edge:.4f} below 2% minimum over executable ask {quote['executable_ask']:.4f}"}
                )
                continue
            request = PickRequest(
                event_start_utc=candidate.event_start_utc,
                event_id=candidate.event_id,
                league=League(sport.upper()),
                away_team=candidate.away_team,
                home_team=candidate.home_team,
                market_type=MarketType.MONEYLINE,
                selection=candidate.selection,
                line=None,
                sportsbook="polymarket_us",
                american_odds=probability_to_american(quote["executable_ask"]),
                model_probability=candidate.model_probability,
                model_uncertainty=None,
                model_version=candidate.model_version,
                rationale=(
                    f"Learned LR call at threshold {candidate.confidence_threshold:.4f}; "
                    f"executable ask {quote['executable_ask']:.4f} "
                    f"({quote['market_slug']})."
                ),
                risks="Learned model; promotion gate not yet open; zero-unit research until review.",
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=ModelState(configured_state),
                observed_at_utc=str(quote.get("observed_at_utc") or ""),
                model_artifact_hash=candidate.model_artifact_hash,
                calibration_method="learned_lr",
                calibration_version=candidate.model_version,
                calibration_artifact_hash=candidate.model_artifact_hash,
                feature_schema_version=candidate.feature_snapshot_hash[:16],
                entity_map_version=registry.version,
                code_revision=candidate.model_version,
                decision_no_vig_probability=quote.get("no_vig_probability"),
            )
            try:
                request.validate(now=observed_at)
                away = registry.resolve(request.league, request.away_team, request.event_start_utc)
                home = registry.resolve(request.league, request.home_team, request.event_start_utc)
                eligibility_kwargs: dict = {"now": observed_at}
                if maximum_data_age_hours is not None:
                    eligibility_kwargs["maximum_age_hours"] = maximum_data_age_hours
                if maximum_unreviewed_disagreement is not None:
                    eligibility_kwargs["maximum_unreviewed_disagreement"] = maximum_unreviewed_disagreement
                eligibility = evaluate_eligibility(
                    request, registry, bans,
                    ledger.exposure(
                        request, now=observed_at,
                        canonical_team_ids=(away.canonical_team_id, home.canonical_team_id),
                    ),
                    unit_policy(config), **eligibility_kwargs,
                )
                logged.append(ledger.append_evaluated(request, eligibility, now=observed_at))
            except DuplicatePickError as error:
                duplicates.append(error.pick_id)
            except (EntityResolutionError, ValueError) as error:
                unmatched.append({"event_id": candidate.event_id, "reason": str(error)[:200]})
    if not log:
        logging_note = "Logging not requested."
    elif not calls:
        logging_note = "No calls above the learned confidence threshold."
    else:
        logging_note = (
            f"Logged {len(logged)} of {len(calls)} calls against stored executable asks; "
            f"{len(duplicates)} duplicates, {len(unmatched)} without a matched quote."
        )
    return {
        "sport": sport,
        "status": "learned_forecast_complete",
        "model_version": candidates[0].model_version if candidates else model_config["active_production_version"],
        "artifact": str(artifact_path),
        "game_date": args_date,
        "scheduled_games": scheduled,
        "calls": len(calls),
        "qualified_shadow_calls": len(qualified_calls),
        "zero_unit_research_calls": len(research_calls),
        "logged": len(logged),
        "logged_pick_ids": [row["pick_id"] for row in logged],
        "duplicate_pick_ids": duplicates,
        "unmatched_quotes": unmatched,
        "skipped": skipped,
        "candidates": [candidate.to_dict() for candidate in candidates],
        "note": logging_note,
    }


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
    from .models.registry import get_model

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


def _settle_all_unsettled(args, config, ledger) -> dict:
    """Grade every started open pick from ESPN scoreboards; void postponed if asked."""
    now = utc_now()
    espn = ESPNClient()
    market_store = MarketOddsSnapshotStore(market_odds_snapshot_path(config))
    settled, voided, pending, failures = [], [], [], []
    for row in ledger.rows():
        if row["status"] != "open":
            continue
        try:
            start = parse_utc(row["event_start_utc"])
        except ValueError:
            failures.append({"pick_id": row["pick_id"], "reason": "bad event_start_utc"})
            continue
        if start > now:
            pending.append(row["pick_id"])
            continue
        leagues = _LEDGER_LEAGUE_TO_ESPN.get(row["league"], ())
        game_day = start.astimezone(EASTERN).date().isoformat()
        match = _find_espn_result(espn, leagues, game_day, row)
        if match is None:
            pending.append(row["pick_id"])
            continue
        status = match.get("status_name", "")
        if status in {"STATUS_POSTPONED", "STATUS_CANCELED"}:
            if args.void_postponed:
                voided.append(ledger.void(row["pick_id"], f"event {status.lower()}")["pick_id"])
            else:
                pending.append(row["pick_id"])
            continue
        if not match.get("completed"):
            pending.append(row["pick_id"])
            continue
        closing_line = closing_odds = closing_probability = None
        quote = market_store.closing_quote(
            row["event_id"], row["event_start_utc"], row["market_type"], row["selection"]
        )
        if quote is not None:
            closing_odds = int(quote["american_odds"])
            closing_probability = float(quote["decision_probability"])
            if quote.get("line") is not None:
                closing_line = float(quote["line"])
        try:
            result = ledger.settle(
                row["pick_id"],
                int(match["away_score"]),
                int(match["home_score"]),
                closing_line,
                closing_odds,
                closing_raw_probability=closing_probability,
            )
            settled.append({"pick_id": row["pick_id"], "result": result["result"]})
        except (KeyError, ValueError) as error:
            failures.append({"pick_id": row["pick_id"], "reason": str(error)})
    return {
        "settled": settled,
        "voided": voided,
        "still_open": pending,
        "failures": failures,
        "note": "Results pulled from ESPN scoreboards; closing prices from stored pregame BBO asks.",
    }


def _find_espn_result(espn: ESPNClient, leagues, game_day: str, row) -> dict | None:
    """Find a completed-game record matching a ledger row by id or team names."""
    away_names = {row["away_team"].casefold(), row["original_away_team"].casefold()}
    home_names = {row["home_team"].casefold(), row["original_home_team"].casefold()}
    for league in leagues:
        try:
            scoreboard = espn.scoreboard(league, game_day)
        except Exception:  # noqa: BLE001 - one bad league feed must not stop settlement
            continue
        for event in scoreboard.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            status = competition.get("status", {}).get("type", {})
            by_side = {item.get("homeAway"): item for item in competition.get("competitors", [])}
            away, home = by_side.get("away"), by_side.get("home")
            if not away or not home:
                continue
            id_match = str(event.get("id")) == row["event_id"]
            name_match = (
                away["team"].get("displayName", "").casefold() in away_names
                and home["team"].get("displayName", "").casefold() in home_names
            )
            if not (id_match or name_match):
                continue
            record = {
                "status_name": status.get("name", ""),
                "completed": bool(status.get("completed")),
            }
            if record["completed"]:
                try:
                    record["away_score"] = int(float(away.get("score", 0) or 0))
                    record["home_score"] = int(float(home.get("score", 0) or 0))
                except (TypeError, ValueError):
                    record["completed"] = False
            return record
    return None


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
            "iteration_policy": "continuous_no_parameter_freezes",
            "promotion_requires": "versioned walk-forward ablation and locked holdout",
        },
        "note": "Shadow research accounting only; no real-money authorization.",
    }


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    config = load_config()
    audit = AuditLog(audit_path(config))
    registry = EntityRegistry.from_json(entity_registry_path(config))
    bans = TeamBanList(config_path(), registry, audit)
    research_scoring = config.get("research_scoring", {})
    research_score_units = (
        float(research_scoring["default_units"]) if research_scoring.get("auto_score_on_settlement") else None
    )
    ledger = PickLedger(
        ledger_path(config),
        audit_path(config),
        research_score_units=research_score_units,
        research_scoring_note=research_scoring.get("note", "fixed-stake hypothetical research scoring"),
    )
    data_root = Path(ledger_path(config)).parent
    try:
        if args.command == "init-ledger":
            ledger.initialize()
            output = {"ledger": str(ledger.path), "status": "ready", "schema_version": LEDGER_SCHEMA_VERSION}
        elif args.command == "report":
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
        elif args.command == "models":
            output = {league.value: asdict(spec) for league, spec in MODEL_SPECS.items()}
        elif args.command == "summary":
            output = _summary(config, ledger)
        elif args.command == "live-portfolio":
            output = PolymarketExecutor(audit).portfolio_snapshot()
        elif args.command == "polymarket-slate":
            output = _polymarket_slate(args, config)
        elif args.command == "polymarket-snapshot":
            snapshot = PolymarketUSClient().snapshot(args.slug)
            if args.sport:
                event_day = utc_now().astimezone(EASTERN).date().isoformat()
                store = PolymarketSnapshotStore.for_sport_date(data_root, args.sport, event_day)
            else:
                store = PolymarketSnapshotStore(polymarket_snapshot_path(config))
            store.append(snapshot)
            output = snapshot
        elif args.command == "polymarket-ledger-prices":
            contracts = []
            for value in args.contract:
                sport, separator, slug = value.partition("=")
                if not separator or not sport or not slug:
                    raise ValueError("contract must use SPORT=MARKET_SLUG")
                if sport not in SPORTS:
                    raise ValueError(f"unsupported contract sport: {sport}")
                contracts.append({"sport": sport, "market_slug": slug})
            output = refresh_contract_snapshots(
                PolymarketUSClient(), contracts, data_root, args.date
            )
        elif args.command == "polymarket-clv":
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
        elif args.command in {"forecast", "log"}:
            log = args.command == "log" or getattr(args, "log", False)
            sports = list(SPORTS) if getattr(args, "all", False) else [args.sport or "mlb"]
            results = {}
            for sport in sports:
                selected_model = getattr(args, "model", "learned")
                if selected_model == "legacy-measured-edge":
                    if sport != "mlb":
                        raise ValueError("legacy-measured-edge is available only for MLB")
                    results[sport] = _forecast_mlb(args.date, log, config, registry, bans, ledger, audit)
                elif sport in LEARNED_PRODUCTION_SPORTS:
                    results[sport] = _forecast_learned_sport(
                        sport, args.date, log, config, registry, bans, ledger,
                        maximum_data_age_hours=float(config["project"].get("maximum_data_age_hours", 12)),
                        maximum_unreviewed_disagreement=float(
                            config["project"].get("maximum_unreviewed_market_disagreement", 0.10)
                        ),
                    )
                else:
                    results[sport] = _forecast_research_sport(sport, args.date, config)
            output = results[sports[0]] if len(sports) == 1 else results
        elif args.command == "settle":
            if args.all_unsettled:
                output = _settle_all_unsettled(args, config, ledger)
            else:
                if not args.pick_id or args.away_score is None or args.home_score is None:
                    raise ValueError("provide --pick-id with --away-score/--home-score, or --all-unsettled")
                closing_line = args.closing_line
                closing_odds = args.closing_american_odds
                closing_probability = None
                if closing_odds is None:
                    row = next((r for r in ledger.rows() if r["pick_id"] == args.pick_id), None)
                    if row is None:
                        raise KeyError(f"unknown pick id: {args.pick_id}")
                    quote = MarketOddsSnapshotStore(market_odds_snapshot_path(config)).closing_quote(
                        row["event_id"], row["event_start_utc"], row["market_type"], row["selection"]
                    )
                    if quote is not None:
                        closing_odds = int(quote["american_odds"])
                        closing_probability = float(quote["decision_probability"])
                        if quote.get("line") is not None:
                            closing_line = float(quote["line"])
                output = ledger.settle(
                    args.pick_id,
                    args.away_score,
                    args.home_score,
                    closing_line,
                    closing_odds,
                    closing_no_vig_probability=args.closing_no_vig_probability,
                    closing_consensus_probability=args.closing_consensus_probability,
                    closing_consensus_line=args.closing_consensus_line,
                    closing_raw_probability=closing_probability,
                )
        elif args.command == "daily":
            slate_args = argparse.Namespace(
                provider="polymarket",
                league=None,
                all=True,
                sport=None,
                date=args.date,
                timezone="America/New_York",
                no_snapshot_bbo=False,
            )
            slate = _polymarket_slate(slate_args, config)
            from .data_sources.odds_soccer_scores import collect_soccer_scores
            soccer_collection = collect_soccer_scores(days_from=3)
            forecast_result = {
                sport: _forecast_learned_sport(
                    sport, args.date, True, config, registry, bans, ledger,
                    maximum_data_age_hours=float(config["project"].get("maximum_data_age_hours", 12)),
                    maximum_unreviewed_disagreement=float(
                        config["project"].get("maximum_unreviewed_market_disagreement", 0.10)
                    ),
                )
                for sport in LEARNED_PRODUCTION_SPORTS
            }
            for result in forecast_result.values():
                result.pop("candidates", None)
            # Read back stored Polymarket odds snapshots for per-sport summaries.
            odds_by_sport = {}
            for sport in LEARNED_PRODUCTION_SPORTS:
                snap_path = (
                    Path(ledger_path(config)).parent
                    / "odds" / sport / args.date / "polymarket_snapshots.jsonl"
                )
                if snap_path.exists():
                    snaps = [
                        json.loads(line)
                        for line in snap_path.read_text(encoding="utf-8").strip().split("\n")
                        if line.strip()
                    ]
                    odds_by_sport[sport] = {
                        "snapshots": len(snaps),
                        "moneyline_snapshots": sum(
                            1 for s in snaps if s.get("market_type") == "moneyline"
                        ),
                        "spread_snapshots": sum(
                            1 for s in snaps if s.get("market_type") == "spread"
                        ),
                        "total_snapshots": sum(
                            1 for s in snaps if s.get("market_type") == "total"
                        ),
                        "with_executable_bbo": sum(
                            1 for s in snaps
                            if s.get("long", {}).get("ask") is not None
                            and s.get("short", {}).get("ask") is not None
                        ),
                    }
            settle_args = argparse.Namespace(all_unsettled=True, void_postponed=False)
            settlement = _settle_all_unsettled(settle_args, config, ledger)
            output = {
                "date": args.date,
                "step1_polymarket_search": {
                    "event_count": slate["event_count"],
                    "leagues_with_events": [
                        league for league, items in slate["events_by_league"].items() if items
                    ],
                    "bbo_capture": slate.get("prospective_bbo_capture", {}),
                },
                "step2_3_forecast_and_log": forecast_result,
                "step3b_polymarket_odds_snapshots": odds_by_sport,
                "step4_settlement": settlement,
                "step1b_soccer_scores": soccer_collection,
                "step5_summary": _summary(config, ledger),
            }
        elif args.command == "ingest":
            output = Ingestor(data_root, audit=audit).ingest_scores(args.sport, args.date)
        elif args.command == "bootstrap":
            ingestor = Ingestor(data_root, audit=audit)
            if args.all:
                output = {sport: ingestor.bootstrap(sport, args.from_date, args.to_date) for sport in SPORTS}
            elif args.sport:
                output = ingestor.bootstrap(args.sport, args.from_date, args.to_date)
            else:
                raise ValueError("provide --sport or --all")
        elif args.command == "bootstrap-entities":
            output = Ingestor(data_root, audit=audit).bootstrap_entities(
                args.league, entity_registry_path(config)
            )
        elif args.command == "features":
            store = FeatureStore(data_root)
            snapshots = store.compute_all(args.sport, args.date)
            output = {
                name: {"computation_hash": snap["computation_hash"], "input_games": snap["input_games"]}
                for name, snap in snapshots.items()
            }
        elif args.command == "backtest":
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
        elif args.command == "validate-models":
            output = run_validation_audit(
                FeatureStore(data_root),
                args.sports,
                PROJECT_ROOT / "data/historical/mlb_market_lines_reconstructed.jsonl",
            )
            destination = PROJECT_ROOT / args.output
            destination.parent.mkdir(parents=True, exist_ok=True)
            if args.write_artifacts:
                output["production_artifacts"] = write_production_artifacts(
                    output, PROJECT_ROOT / "config/models"
                )
            destination.write_text(
                json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
                encoding="utf-8",
            )
            output["report_path"] = str(destination)
        elif args.command == "validate-totals":
            output = validate_all_total_score_models(
                FeatureStore(data_root),
                args.sports,
                PROJECT_ROOT / "config/models" if args.write_artifacts else None,
            )
            destination = PROJECT_ROOT / args.output
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
                encoding="utf-8",
            )
            output["report_path"] = str(destination)
        elif args.command == "reconstruct-mlb-markets":
            output = Ingestor(data_root, audit=audit).reconstruct_mlb_markets(
                args.start,
                args.end,
                PROJECT_ROOT / args.output,
            )
        elif args.command == "train-residual":
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
        elif args.command == "execute":
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
                if (
                    execution_config.get("manual_research_require_positive_edge", True)
                    and edge <= 0
                ):
                    raise ExecutionGateError("REFUSED: manual research order has no positive edge.")
                # The "limit below model probability" rule is a BUY guard (do not
                # pay more than fair value). A SELL is an exit at a target price
                # and is exempt.
                if args.action == "buy" and args.price >= model_probability:
                    raise ExecutionGateError(
                        "REFUSED: manual buy limit must remain below the model probability."
                    )
                for team_key in ("away_team", "home_team"):
                    _, banned = bans.check(league, row[team_key])
                    if banned:
                        raise ExecutionGateError(
                            f"REFUSED: {row[team_key]} is on the permanent team ban list."
                        )
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
            )
        elif args.command == "sell-position":
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
        elif args.command == "exposure":
            rows = ledger.rows()
            qualified_open = [
                row
                for row in rows
                if row["record_type"] == "QUALIFIED_SHADOW_CALL" and row["status"] == "open"
            ]
            output = {
                "open_units": round(sum(float(row["units"] or 0) for row in qualified_open), 2),
                "open_qualified_calls": len(qualified_open),
                "zero_unit_research_observations": sum(
                    row["record_type"] == "RESEARCH_OBSERVATION" for row in rows
                ),
                "starting_bankroll_units": config["bankroll"]["reference_units"],
                "note": "Shadow research accounting only; no real-money authorization",
            }
        elif args.command == "ban-team":
            output = _handle_ban(args, bans)
        elif args.command == "collect-scores":
            from .data_sources.odds_soccer_scores import collect_soccer_scores
            output = collect_soccer_scores(days_from=args.days)
        elif args.command == "score-research":
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
        elif args.command == "call":
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
            exposure = ledger.exposure(
                request, canonical_team_ids=(away.canonical_team_id, home.canonical_team_id)
            )
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
        elif args.command == "update-closing":
            output = ledger.update_closing(
                args.pick_id,
                args.closing_line,
                args.closing_american_odds,
                closing_no_vig_probability=args.closing_no_vig_probability,
                closing_consensus_probability=args.closing_consensus_probability,
                closing_consensus_line=args.closing_consensus_line,
            )
        elif args.command == "void":
            output = ledger.void(args.pick_id, args.reason)
        elif args.command == "review-loss":
            output = ledger.review_loss(args.pick_id, args.classification, args.cause, args.action)
        else:
            raise ValueError(f"unknown command: {args.command}")
        print(json.dumps(output, indent=2, sort_keys=True, default=str))
    except ExecutionGateError as error:
        print(json.dumps({"status": "refused", "error": str(error)}, indent=2), file=sys.stderr)
        raise SystemExit(3)
    except DuplicatePickError as error:
        audit.append("pick_rejected", error.pick_id, {"reason_code": "NO_CALL_DUPLICATE"})
        print(
            json.dumps(
                {
                    "decision": "NO_CALL",
                    "reason_code": "NO_CALL_DUPLICATE",
                    "existing_pick_id": error.pick_id,
                },
                indent=2,
            )
        )
    except EntityResolutionError as error:
        audit.append(
            "pick_rejected", "entity", {"reason_code": "NO_CALL_ENTITY_UNRESOLVED", "detail": str(error)}
        )
        _fail(str(error), "NO_CALL_ENTITY_UNRESOLVED")
    except (KeyError, ValueError) as error:
        if args.command == "polymarket-clv":
            reason = "POLYMARKET_CLV_NOT_AVAILABLE"
        else:
            reason = "NO_CALL_EVENT_STARTED" if "started" in str(error) else "NO_CALL_INVALID_MARKET"
        _fail(str(error), reason)


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


def _fail(message: str, reason_code: str) -> None:
    print(
        json.dumps({"decision": "NO_CALL", "reason_code": reason_code, "error": message}, indent=2),
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
