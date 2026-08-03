"""model-prediction CLI.

Daily loop: polymarket-slate -> forecast --log -> settle --all-unsettled ->
summary (or `daily` for all of it). Everything is shadow/paper by default;
the only real-money path is the `execute` subcommand behind the hard gate in
``data_sources/polymarket_execute.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

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
from .data_sources.espn import SPORT_LEAGUES, ESPNClient, ESPNMLBClient
from .data_sources.espn_probables import capture_probable_starter_snapshot
from .data_sources.espn_wnba_injuries import capture_espn_event_injuries
from .data_sources.kalshi import DEFERRED_MESSAGE as KALSHI_DEFERRED_MESSAGE
from .data_sources.mlb_injuries import (
    capture_roster_snapshot,
    capture_transactions_snapshot,
    team_id_for_name,
)
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
from .data_sources.wnba_injuries import capture_latest_report
from .domain import (
    EASTERN,
    LEARNED_PRODUCTION_SPORTS,
    LOSS_CLASSIFICATIONS,
    PRODUCTION_SPORTS,
    League,
    MarketType,
    ModelOrigin,
    ModelState,
    PickRequest,
    RecordType,
    eastern_today,
    iso_utc,
    parse_utc,
    utc_now,
)
from .eligibility import (
    evaluate_eligibility,
    evaluate_gated_research_eligibility,
)
from .entities import EntityRegistry, EntityResolutionError
from .esports import (
    TITLE_SPECS,
    backfill_esports,
    forecast_esports_slate,
    refresh_recent_matches,
    validate_all_esports_baselines,
)
from .features.base import FeatureStore
from .forward import build_mlb_slate
from .ingest import Ingestor
from .international_baseball import (
    LEAGUE_SPECS as INTERNATIONAL_BASEBALL_LEAGUE_SPECS,
)
from .international_baseball import (
    backfill_international_baseball,
    forecast_international_baseball_slate,
    refresh_recent_international_baseball_matches,
    validate_all_international_baseball_baselines,
)
from .learned_forward import build_learned_moneyline_slate, match_executable_quote
from .ledger import LEDGER_SCHEMA_VERSION, DuplicatePickError, PickLedger
from .mlb_baseline_refresh import refresh_if_due
from .models import MODEL_SPECS
from .models.market_residual import MarketResidualModel, ResidualTrainingRow
from .models.mlb import load_formula_spec
from .research_ledgers import (
    RESEARCH_LEDGER_SPORTS,
    existing_research_ledgers,
    research_ledger,
)
from .soccer_forward import build_soccer_total_slate
from .tennis_forward import build_tennis_slate
from .total_score import validate_all_total_score_models
from .units import edge_scaled_units
from .validation import run_validation_audit, write_production_artifacts

SPORTS = tuple(POLYMARKET_SPORT_LEAGUES)
ESPN_SPORTS = tuple(SPORT_LEAGUES)
ESPORTS_TITLES = ("lol", "cs2", "dota2", "valorant", "rainbow_six")
DAILY_LEARNED_SPORTS = ("mlb", "nba", "wnba", "nfl")
DAILY_INTERNATIONAL_BASEBALL_SPORTS = ("kbo", "npb")
FLAT_LEDGER_SPORTS = DAILY_LEARNED_SPORTS
RESEARCH_ONLY_DAILY_SPORTS = (
    "soccer",
    "tennis",
    *ESPORTS_TITLES,
    *DAILY_INTERNATIONAL_BASEBALL_SPORTS,
)

logger = logging.getLogger(__name__)

_LEDGER_LOCK = threading.Lock()

# League value on a ledger row -> ESPN league key(s) to search for results.
# WORLD_CUP dropped 2026-07: tournament is over, no games left to forecast or settle.
_LEDGER_LEAGUE_TO_ESPN = {
    "MLB": ("MLB",),
    "NBA": ("NBA",),
    "WNBA": ("WNBA",),
    "NFL": ("NFL",),
    "SOCCER": (
        "EPL", "LA_LIGA", "BUNDESLIGA", "SERIE_A", "MLS", "UCL",
        "BRASILEIRAO", "BRAZIL_SERIE_B", "ARGENTINA", "ARGENTINA_2",
        "COLOMBIA", "CHILE", "URUGUAY", "ECUADOR", "PERU", "SUDAMERICANA",
        "FRIENDLIES", "CLUB_FRIENDLIES",
    ),
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
    order_status = commands.add_parser(
        "order-status",
        help="read authoritative state for submitted exchange orders",
    )
    order_status.add_argument("--order-id", action="append", required=True)

    slate = commands.add_parser(
        "polymarket-slate", help="read dated sports slates from the public Polymarket US API"
    )
    slate.add_argument("--sport", choices=SPORTS)
    slate.add_argument("--league", help="single gateway league key (e.g. MLB, EPL, WTA)")
    slate.add_argument("--all", action="store_true", help="every supported sport")
    slate.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")
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
        help="refresh BBOs only for exact contracts selected from the open ledger",
    )
    ledger_prices.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")
    ledger_prices.add_argument(
        "--contract",
        action="append",
        default=[],
        metavar="SPORT[@GAME_DATE]=MARKET_SLUG",
        help="repeat for each unique open ledger contract; GAME_DATE is Eastern time",
    )

    clv = commands.add_parser("polymarket-clv", help="probability CLV from the final stored pregame snapshot")
    clv.add_argument("--slug", required=True)
    clv.add_argument("--side", required=True, choices=["long", "short"])
    clv.add_argument("--decision-price", required=True, type=float)
    clv.add_argument("--sport")
    clv.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")

    forecast = commands.add_parser(
        "forecast", help="pregame learned LR + confidence-gate moneyline slate"
    )
    forecast.add_argument("--sport", choices=SPORTS + ESPORTS_TITLES)
    forecast.add_argument("--all", action="store_true")
    forecast.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")
    forecast.add_argument("--log", action="store_true", help="log only rows with exact executable prices")
    forecast.add_argument(
        "--model",
        choices=("learned", "legacy-measured-edge"),
        default="learned",
        help="default learned production path; legacy option is MLB-only research rollback",
    )
    forecast.add_argument(
        "--replace-today",
        action="store_true",
        help="clear existing open picks for today before re-forecasting (default on daily)",
    )
    forecast.add_argument("--force", action="store_true", help="bypass event_started guard (for historical backfill)")

    flat_forecast = commands.add_parser(
        "flat-forecast", help="forecast every game with no edge gate → flat_picks.xlsx"
    )
    flat_forecast.add_argument("--sport", choices=SPORTS + ESPORTS_TITLES)
    flat_forecast.add_argument("--all", action="store_true")
    flat_forecast.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")
    flat_forecast.add_argument("--log", action="store_true", help="log all calls to flat ledger")
    flat_forecast.add_argument("--force", action="store_true", help="bypass event_started guard (for historical backfill)")

    log_cmd = commands.add_parser("log", help="alias for forecast --log")
    log_cmd.add_argument("--sport", choices=SPORTS, default="mlb")
    log_cmd.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")
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
    daily.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")
    daily.add_argument(
        "--skip-settlement",
        action="store_true",
        help="skip settlement when the caller already completed it",
    )

    ingest = commands.add_parser("ingest", help="cache one date of ESPN scores locally")
    ingest.add_argument("--sport", required=True, choices=ESPN_SPORTS)
    ingest.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")

    availability = commands.add_parser(
        "wnba-availability-capture",
        help="archive the latest official WNBA injury PDF and normalized point-in-time rows",
    )
    availability.add_argument(
        "--observed-at",
        help="UTC-aware ISO timestamp; defaults to now and never selects a future report",
    )
    availability.add_argument(
        "--event-id",
        action="append",
        default=[],
        help="also archive ESPN event injury statuses; repeat for each WNBA game",
    )

    bootstrap = commands.add_parser("bootstrap", help="idempotent historical backfill from ESPN")
    bootstrap.add_argument("--sport", choices=ESPN_SPORTS)
    bootstrap.add_argument("--all", action="store_true")
    bootstrap.add_argument("--from", dest="from_date", required=True)
    bootstrap.add_argument("--to", dest="to_date")

    esports_backfill = commands.add_parser(
        "esports-backfill",
        help="backfill no-key series-level results for isolated LoL and CS2 research models",
    )
    esports_backfill.add_argument("--title", choices=tuple(TITLE_SPECS))
    esports_backfill.add_argument("--all", action="store_true")
    esports_backfill.add_argument("--from", dest="from_date", required=True)
    esports_backfill.add_argument("--to", dest="to_date")

    esports_validate = commands.add_parser(
        "validate-esports",
        help="select separate LoL/CS2 Elo baselines and grade chronological locked tests",
    )
    esports_validate.add_argument(
        "--titles", nargs="+", choices=tuple(TITLE_SPECS), default=tuple(TITLE_SPECS)
    )
    esports_validate.add_argument(
        "--output", default="outputs/latest/esports-baseline-validation.json"
    )
    esports_validate.add_argument("--write-artifacts", action="store_true")

    esports_forecast = commands.add_parser(
        "esports-forecast",
        help="zero-unit exact-identity LoL/CS2 prices for Polymarket US match-winner contracts",
    )
    esports_forecast.add_argument("--title", choices=tuple(TITLE_SPECS))
    esports_forecast.add_argument("--all", action="store_true", help="forecast all esports titles")
    esports_forecast.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")
    esports_forecast.add_argument("--timezone", default="America/New_York")
    esports_forecast.add_argument("--log", action="store_true", help="log forecast to research ledger")

    international_backfill = commands.add_parser(
        "international-baseball-backfill",
        help="backfill official no-key KBO and NPB regular-season results",
    )
    international_backfill.add_argument(
        "--league", choices=tuple(INTERNATIONAL_BASEBALL_LEAGUE_SPECS)
    )
    international_backfill.add_argument("--all", action="store_true")
    international_backfill.add_argument("--from", dest="from_date", required=True)
    international_backfill.add_argument("--to", dest="to_date")

    international_validate = commands.add_parser(
        "validate-international-baseball",
        help="select separate tie-aware KBO/NPB Elo baselines and grade locked tests",
    )
    international_validate.add_argument(
        "--leagues",
        nargs="+",
        choices=tuple(INTERNATIONAL_BASEBALL_LEAGUE_SPECS),
        default=tuple(INTERNATIONAL_BASEBALL_LEAGUE_SPECS),
    )
    international_validate.add_argument(
        "--output", default="outputs/latest/international-baseball-baseline-validation.json"
    )
    international_validate.add_argument("--write-artifacts", action="store_true")

    international_forecast = commands.add_parser(
        "international-baseball-forecast",
        help="zero-unit tie-aware KBO/NPB fair values using exact Polymarket US BBOs",
    )
    international_forecast.add_argument(
        "--league", required=True, choices=tuple(INTERNATIONAL_BASEBALL_LEAGUE_SPECS)
    )
    international_forecast.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")
    international_forecast.add_argument("--timezone")

    entities = commands.add_parser("bootstrap-entities", help="merge ESPN team lists into the registry")
    entities.add_argument("--league", required=True)

    features = commands.add_parser("features", help="compute point-in-time feature snapshots")
    features.add_argument("--sport", required=True, choices=SPORTS)
    features.add_argument("--date", default=eastern_today().isoformat(),
        help="ISO date; defaults to today in US-Eastern time")
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

    mlb_baselines = commands.add_parser(
        "refresh-mlb-baselines",
        help="regenerate real MLB park factors and league rates from historical data",
    )
    mlb_baselines.add_argument(
        "--force", action="store_true", help="refresh even if the last one was recent"
    )
    mlb_baselines.add_argument(
        "--min-days", type=float, default=7.0, help="minimum days between refreshes unless --force"
    )

    execute = commands.add_parser(
        "execute",
        help="REAL MONEY: place an order for a qualified pick (hard gate; requires --execute)",
    )
    execute.add_argument("--pick-id", required=True)
    execute.add_argument("--size-shares", type=float, required=True)
    execute.add_argument("--price", type=float, required=True, help="executable ask/bid, 0-1")
    execute.add_argument("--side", default="long", choices=["long", "short"])
    execute.add_argument("--action", default="buy", choices=["buy", "sell"])
    execute.add_argument(
        "--order-type",
        default="limit_gtc",
        choices=["limit_gtc", "limit_ioc"],
    )
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

    checklist = commands.add_parser(
        "verify-checklist",
        help="run the model_improvements.md section-13 verification checklist for a sport",
    )
    checklist.add_argument("--sport", required=True)

    commands.add_parser(
        "verify-chain",
        help="verify audit-chain link/hash integrity and ledger<->audit reconciliation",
    )
    return root


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


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
        all_leagues = [
            (sport, league)
            for sport in SPORTS
            for league in POLYMARKET_SPORT_LEAGUES[sport]
        ]

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
        PROJECT_ROOT / "config/models/measured-edge-margin-v2.json",
        PROJECT_ROOT / "config/models/measured-edge-totals-v2.json",
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
        "model_versions": ["measured-edge-margin-v2", "measured-edge-totals-v2"],
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
    validation = validate_all_esports_baselines(data_root, titles, PROJECT_ROOT / "config/models")
    # Keep the dashboard's evidence-consistency report in sync with the
    # artifacts it describes -- otherwise it goes stale again the moment new
    # matches merge in, since it's read as a pinned snapshot elsewhere
    # (dashboard_server.production_evidence).
    report_path = PROJECT_ROOT / "outputs/latest/esports-baseline-validation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(validation, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
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
    validation = validate_all_international_baseball_baselines(
        data_root, leagues, PROJECT_ROOT / "config/models"
    )
    report_path = PROJECT_ROOT / "outputs/latest/international-baseball-baseline-validation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(validation, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return {"backfill": backfill_results, "validation": validation}


def _forecast_mlb_totals_flat(args_date: str, log: bool, config, registry, bans, flat_ledger, audit, main_ledger=None) -> dict:
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
        PROJECT_ROOT / "config/models/measured-edge-margin-v2.json",
        PROJECT_ROOT / "config/models/measured-edge-totals-v2.json",
        observed_at,
        odds_feed,
    )
    totals_candidates = [
        candidate
        for candidate in candidates
        if candidate.market_type in (MarketType.TOTAL, MarketType.SPREAD)
    ]
    logged, duplicates = [], []
    if log:
        for candidate in totals_candidates:
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
            try:
                request.validate(now=observed_at)
                away = registry.resolve(request.league, request.away_team, request.event_start_utc)
                home = registry.resolve(request.league, request.home_team, request.event_start_utc)
                # Exposure check and append happen inside one held lock -- see
                # the matching comment in _log_esports_forecast.
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
                    # Flat: every evaluated candidate is logged, no edge gate.
                    logged.append(flat_ledger.append_evaluated(request, eligibility, now=observed_at))
                    # Main: only genuinely eligible (CALL) rows — operator directive 2026-08-03.
                    if main_ledger is not None and eligibility.decision == "CALL":
                        with suppress(DuplicatePickError):
                            main_ledger.append_evaluated(request, eligibility, now=observed_at)
            except DuplicatePickError as error:
                duplicates.append(error.pick_id)
            except (EntityResolutionError, ValueError) as error:
                skipped.append({"event_id": candidate.event_id, "reason": str(error)[:200]})
    return {
        "sport": "mlb_totals",
        "model_name": "Measured Edge Totals + Spread",
        "model_versions": ["measured-edge-totals-v2", "measured-edge-margin-v2"],
        "game_date": args_date,
        "scheduled_games": scheduled,
        "market_candidates": len(totals_candidates),
        "total_candidates": sum(1 for c in totals_candidates if c.market_type is MarketType.TOTAL),
        "spread_candidates": sum(1 for c in totals_candidates if c.market_type is MarketType.SPREAD),
        "logged": len(logged),
        "logged_pick_ids": [row["pick_id"] for row in logged],
        "duplicate_pick_ids": duplicates,
        "skipped": skipped,
        "note": (
            "Flat only, no main-ledger promotion; MLB moneyline is served "
            "separately by the learned production path."
        ),
    }


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
        utc_now()
        if not force
        else datetime.strptime(args_date, "%Y-%m-%d").replace(tzinfo=UTC)
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
                        {"event_id": candidate.event_id,
                         "reason": "no stored executable moneyline BBO matched this matchup"}
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
                    edge_pct = f"{min_edge*100:.0f}%"
                    edge_blocked.append(
                        {"event_id": candidate.event_id,
                         "reason": f"model edge {model_edge:.4f} below {edge_pct} minimum over executable ask {quote['executable_ask']:.4f} — logged anyway, operator review"}
                    )
            # Convert UTC event time to Eastern for consistent ledger display
            try:
                event_et = datetime.fromisoformat(candidate.event_start_utc.replace('Z','+00:00')).astimezone(EASTERN).strftime('%Y-%m-%dT%H:%M:%S%z')
            except (ValueError, TypeError):
                event_et = candidate.event_start_utc
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
                sportsbook = (
                    "model_opinion_no_executable_quote"
                    if quote_warning
                    else "espn"
                )
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
                row_unavailable_features = tuple(
                    dict.fromkeys((*row_unavailable_features, quote_warning))
                )
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
                market_residual_probability=market_residual_probability,
                unavailable_features=(
                    ",".join(row_unavailable_features)
                    if row_unavailable_features
                    else None
                ),
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
                        request, registry, bans,
                        (exposure_ledger or ledger).exposure(
                            request, now=effective_now,
                            canonical_team_ids=(away.canonical_team_id, home.canonical_team_id),
                        ),
                        unit_policy(config), **eligibility_kwargs,
                    )
                    if quote_warning and eligibility.decision == "CALL":
                        eligibility = replace(
                            eligibility,
                            record_type=RecordType.RESEARCH_OBSERVATION,
                            decision="NO_CALL",
                            reason_code="NO_CALL_MARKET_UNAVAILABLE",
                            units=0,
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
                            decision="NO_CALL",
                            reason_code="NO_CALL_BELOW_LEARNED_CONFIDENCE",
                            units=0,
                        )
                    # What's still NO_CALL here is always a hard trust-
                    # boundary reason or the confidence gate just above.
                    genuinely_eligible = eligibility.decision == "CALL"
                    # Main ledger (MLB/WNBA, non-flat, non-research-routed) holds
                    # ONLY genuine qualified calls -- any remaining NO_CALL is a
                    # structurally-untrustworthy reason, real diagnostic
                    # information that still belongs in flat_picks.xlsx (which
                    # already logs every game every day) rather than muddying main.
                    skip_main_no_call = (
                        not flat_mode
                        and not research_routed
                        and eligibility.decision != "CALL"
                        and quote_warning is None
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
                        with suppress(DuplicatePickError):
                            gated_ledger.append_evaluated(
                                request,
                                eligibility,
                                now=effective_now,
                            )
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
        "model_version": candidates[0].model_version if candidates else model_config.get("active_production_version", "unknown"),
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
    from .data_sources.polymarket_us import probability_to_american
    logged = 0
    errors: list[dict] = []
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
                f"Neutral Elo baseline; executable ask {ask:.4f} "
                f"(market_slug={contract['market_slug']})."
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
                if flat_ledger is not None:
                    with suppress(DuplicatePickError):
                        flat_ledger.append_evaluated(request, eligibility, now=observed_now)
                # gated_ledger: curated subset of rows evaluate_esports_eligibility
                # genuinely approved as a real call. Same relationship
                # flat_picks.xlsx has to picks.xlsx, for research-only sports.
                if gated_ledger is not None and genuinely_eligible:
                    with suppress(DuplicatePickError):
                        gated_ledger.append_evaluated(request, eligibility, now=observed_now)
            logged += 1
        except DuplicatePickError:
            continue
        except (ValueError, KeyError) as error:
            # Record the failure instead of silently discarding it -- a bare
            # `continue` here previously left an entire league able to log
            # zero real predictions for a day with nothing surfaced anywhere
            # (see the KBO/NPB timestamp-ordering incident in DEBUG.md for
            # how bad a silent per-contract swallow can get).
            errors.append({
                "event_id": contract.get("event_id"),
                "reason": f"{type(error).__name__}: {error}",
            })
            logger.warning(
                "esports forecast logging failed for event %s (%s): %s",
                contract.get("event_id"), title, error,
            )
            continue

    forecast["errors"] = errors
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
    from .data_sources.polymarket_us import probability_to_american
    from .international_baseball import forecast_international_baseball_slate

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
        data_root, artifact_dir, league, args_date,
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
                if flat_ledger is not None:
                    with suppress(DuplicatePickError):
                        flat_ledger.append_evaluated(request, eligibility, now=observed_now)
                if gated_ledger is not None and genuinely_eligible:
                    with suppress(DuplicatePickError):
                        gated_ledger.append_evaluated(request, eligibility, now=observed_now)
            logged += 1
        except DuplicatePickError:
            continue
        except (ValueError, KeyError) as error:
            # Record the failure instead of silently discarding it -- see
            # the matching comment in _log_esports_forecast.
            errors.append({
                "event_id": contract.get("event_id"),
                "reason": f"{type(error).__name__}: {error}",
            })
            logger.warning(
                "international baseball forecast logging failed for event %s (%s): %s",
                contract.get("event_id"), league_upper, error,
            )
            continue
    forecast["logged"] = logged
    forecast["errors"] = errors
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
    main_ledger: mirrors gated_ledger's existing "only when genuinely
    eligible" append. Soccer's config was set to status: shadow_qualified
    via an explicit manual qualification_override (operator directive,
    2026-08-02) rather than a genuine walk-forward/locked-holdout pass --
    see config/model.yaml's SOCCER.qualification_override_reason for the
    honest disclosure. Real Main-ledger rows now get produced whenever a
    contract clears min_edge; _row_artifact_qualified (cli.py) still fails
    closed for real execution since no genuinely-qualified soccer artifact
    exists, so PolymarketExecutor.execute requires --manual-research-order
    for any actual order on these rows.
    """
    from .data_sources.polymarket_us import probability_to_american
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
    errors: list[dict] = []
    for contract in forecast.get("priced_contracts", []):
        ask = float(contract["executable_ask"])
        min_team_games = float(
            (contract.get("feature_basis") or {}).get("min_team_games", 0.0)
        )
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
            rationale=(
                f"{contract['rationale']} Executable ask {ask:.4f} "
                f"({contract['market_slug']})."
            ),
            risks=(
                "Research-only soccer score model; draw-aware, but not yet "
                "locked-holdout qualified."
            ),
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
                    maximum_age_hours=float(
                        config["project"].get("maximum_data_age_hours", 12)
                    ),
                    maximum_unreviewed_disagreement=float(
                        config["project"].get(
                            "maximum_unreviewed_market_disagreement",
                            0.10,
                        )
                    ),
                )
                genuinely_eligible = eligibility.decision == "CALL"
                if research_ledger is not None:
                    with suppress(DuplicatePickError):
                        research_ledger.append_evaluated(
                            request,
                            eligibility,
                            now=observed_now,
                        )
                if gated_ledger is not None and genuinely_eligible:
                    with suppress(DuplicatePickError):
                        gated_ledger.append_evaluated(
                            request,
                            eligibility,
                            now=observed_now,
                        )
                        gated += 1
                if flat_ledger is not None:
                    # Flat: log every priced contract regardless of
                    # eligibility, same "show everything" semantics flat
                    # mode uses for every other sport.
                    with suppress(DuplicatePickError):
                        flat_ledger.append_evaluated(
                            request,
                            eligibility,
                            now=observed_now,
                        )
                        flat_logged += 1
                if main_ledger is not None and genuinely_eligible:
                    # Mirrors gated_ledger exactly -- same eligibility
                    # result, same "only when genuinely eligible" gate. See
                    # this function's docstring: inert until soccer is
                    # promoted past status: research in config/model.yaml.
                    with suppress(DuplicatePickError):
                        main_ledger.append_evaluated(
                            request,
                            eligibility,
                            now=observed_now,
                        )
                        main_logged += 1
            logged += 1
        except DuplicatePickError:
            continue
        except (KeyError, ValueError) as error:
            # Record the failure instead of silently discarding it -- see
            # the matching comment in _log_esports_forecast.
            errors.append({
                "event_id": contract.get("event_id"),
                "reason": f"{type(error).__name__}: {error}",
            })
            logger.warning(
                "soccer forecast logging failed for event %s: %s",
                contract.get("event_id"), error,
            )
            continue
    forecast["logged"] = logged
    forecast["gated_logged"] = gated
    forecast["flat_logged"] = flat_logged
    forecast["main_logged"] = main_logged
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

    main_ledger: mirrors gated_ledger's "only when genuinely eligible"
    append, same as _forecast_soccer_sport. TENNIS's config was set to
    status: shadow_qualified via an explicit manual qualification_override
    (operator directive, 2026-08-03) rather than a genuine walk-forward/
    locked-holdout pass -- see config/model.yaml's TENNIS.
    qualification_override_reason. Real Main-ledger rows now get produced
    whenever a contract clears min_edge; _row_artifact_qualified (cli.py)
    still fails closed for real execution since no genuinely-qualified
    tennis artifact exists, so PolymarketExecutor.execute requires
    --manual-research-order for any actual order on these rows.
    """
    from .data_sources.polymarket_us import probability_to_american
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
    errors: list[dict] = []
    for contract in forecast.get("priced_contracts", []):
        ask = float(contract["executable_ask"])
        min_player_matches = float(
            (contract.get("feature_basis") or {}).get("min_player_matches", 0.0)
        )
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
            rationale=(
                f"{contract['rationale']} Executable ask {ask:.4f} "
                f"({contract['market_slug']})."
            ),
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
                    maximum_age_hours=float(
                        config["project"].get("maximum_data_age_hours", 12)
                    ),
                    maximum_unreviewed_disagreement=float(
                        config["project"].get(
                            "maximum_unreviewed_market_disagreement",
                            0.10,
                        )
                    ),
                )
                genuinely_eligible = eligibility.decision == "CALL"
                if research_ledger is not None:
                    with suppress(DuplicatePickError):
                        research_ledger.append_evaluated(
                            request,
                            eligibility,
                            now=observed_now,
                        )
                if gated_ledger is not None and genuinely_eligible:
                    with suppress(DuplicatePickError):
                        gated_ledger.append_evaluated(
                            request,
                            eligibility,
                            now=observed_now,
                        )
                        gated += 1
                if flat_ledger is not None:
                    # Flat: log every priced contract regardless of
                    # eligibility, same "show everything" semantics flat
                    # mode uses for every other sport.
                    with suppress(DuplicatePickError):
                        flat_ledger.append_evaluated(
                            request,
                            eligibility,
                            now=observed_now,
                        )
                        flat_logged += 1
                if main_ledger is not None and genuinely_eligible:
                    with suppress(DuplicatePickError):
                        main_ledger.append_evaluated(
                            request,
                            eligibility,
                            now=observed_now,
                        )
                        main_logged += 1
            logged += 1
        except DuplicatePickError:
            continue
        except (KeyError, ValueError) as error:
            # Record the failure instead of silently discarding it -- see
            # the matching comment in _log_esports_forecast.
            errors.append({
                "event_id": contract.get("event_id"),
                "reason": f"{type(error).__name__}: {error}",
            })
            logger.warning(
                "tennis forecast logging failed for event %s: %s",
                contract.get("event_id"), error,
            )
            continue
    forecast["logged"] = logged
    forecast["gated_logged"] = gated
    forecast["flat_logged"] = flat_logged
    forecast["main_logged"] = main_logged
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
    """Grade every started open pick from ESPN scoreboards or Polymarket resolution."""
    now = utc_now()
    espn = ESPNClient()
    market_store = MarketOddsSnapshotStore(market_odds_snapshot_path(config))
    data_root = Path(ledger_path(config)).parent
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
        # Esports: settle via Polymarket contract resolution
        if row["league"] in ("LOL", "CS2", "DOTA2", "VALORANT", "RAINBOW_SIX"):
            result = _settle_esports_pick(row, ledger, data_root=data_root)
            if result is None:
                pending.append(row["pick_id"])
            elif result.get("voided"):
                voided.append(result["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            else:
                failures.append(result)
            continue
        # KBO/NPB: neither ESPN nor Polymarket resolution covers these --
        # settle from the official league schedule instead.
        if row["league"] in ("KBO", "NPB"):
            result = _settle_international_baseball_pick(row, ledger, config)
            if result is None:
                pending.append(row["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            else:
                failures.append(result)
            continue
        # Tennis: player-vs-player, not team-vs-team -- ESPN's tennis
        # scoreboard shape doesn't fit `_find_espn_result` at all (see
        # `_find_tennis_result`).
        if row["league"] == "TENNIS":
            result = _settle_tennis_pick(row, ledger, espn, data_root=data_root)
            if result is None:
                pending.append(row["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            else:
                failures.append(result)
            continue
        leagues = _LEDGER_LEAGUE_TO_ESPN.get(row["league"], ())
        game_day = start.astimezone(EASTERN).date().isoformat()
        match = _find_espn_result(espn, leagues, game_day, row)
        if match is None:
            # Soccer: try collected scores from The Odds API for leagues
            # outside ESPN coverage (e.g. Brazil Serie B, K League 1).
            if row["league"] == "SOCCER":
                soccer_scores = _load_soccer_scores()
                match = _find_soccer_result(row, soccer_scores)
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
        elif (
            row["league"] == "SOCCER"
            and row["market_type"] == "moneyline"
            and row["selection"] in ("home", "away")
        ):
            # Soccer never writes into market_store (that's MLB's own
            # snapshot store) -- its real snapshot history lives in the
            # generic per-sport-date store the daily slate capture already
            # writes for every sport. Draw selections are skipped here: the
            # home/away team-matching helper below doesn't have a
            # "neither side" case to resolve them against.
            slug = _extract_market_slug(str(row.get("rationale", "")))
            if slug is not None:
                try:
                    closing_probability, closing_odds = _closing_probability_for_moneyline_pick(
                        data_root,
                        "soccer",
                        slug,
                        row["event_start_utc"],
                        row["home_team"],
                        row["away_team"],
                        row["selection"],
                    )
                except (OSError, ValueError):
                    logger.warning("soccer closing-snapshot lookup failed for slug %s", slug, exc_info=True)
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
        except Exception:
            logger.warning("ESPN scoreboard fetch failed for %s on %s; settlement skipping this league", league, game_day, exc_info=True)
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
            # Prefer exact event_id match. When the ledger has a numeric ESPN
            # event_id that does NOT match any event in the scoreboard (e.g.
            # re-forecast rows with regenerated IDs), fall back to name matching
            # with a caution: double-header games sharing the same team names
            # on the same day may be matched incorrectly.
            if id_match or name_match:
                pass
            else:
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


_TERMINAL_MARKET_STATES = {
    "MARKET_STATE_EXPIRED",
    "MARKET_STATE_RESOLVED",
    "MARKET_STATE_SETTLED",
}


def _closing_probability_for_moneyline_pick(
    data_root,
    sport_dir: str,
    slug: str,
    event_start_utc: str,
    home_team: str,
    away_team: str,
    selection: str,
) -> tuple[float | None, int | None]:
    """Best-effort closing (last pregame) probability for a team/player-vs-
    team/player moneyline pick, read from the same per-sport-date Polymarket
    snapshot history the forecast pipeline already captures every day via
    capture_slate_snapshots (data/odds/{sport}/{date}/polymarket_snapshots.jsonl).

    Returns (raw_probability, american_odds) for the row's own selection, or
    (None, None) if no matching pregame snapshot was ever captured for this
    market -- CLV is then simply left blank for that row, same as today.
    """
    from .learned_forward import _team_matches

    game_date = parse_utc(event_start_utc).astimezone(EASTERN).date().isoformat()
    store = PolymarketSnapshotStore.for_sport_date(data_root, sport_dir, game_date)
    snapshot = store.closing_snapshot(slug, event_start_utc)
    if snapshot is None:
        return None, None
    selected_name = home_team if selection == "home" else away_team
    long_desc = str((snapshot.get("long") or {}).get("description", ""))
    short_desc = str((snapshot.get("short") or {}).get("description", ""))
    matches_long = _team_matches(selected_name, long_desc)
    matches_short = _team_matches(selected_name, short_desc)
    if matches_long == matches_short:
        return None, None  # ambiguous or no match -- never guess
    side = snapshot.get("long" if matches_long else "short") or {}
    ask = side.get("ask")
    if ask is None or not 0 < float(ask) < 1:
        return None, None
    return round(float(ask), 6), probability_to_american(float(ask))


def _extract_market_slug(rationale: str) -> str | None:
    """Recover the Polymarket market slug embedded in a row's rationale text.

    Two formats coexist across this project's history: ``market_slug=xxx``
    (esports) and the older ``... (xxx).`` trailing-parenthetical (soccer/
    tennis, and legacy esports rows).
    """
    import re

    match = re.search(r"market_slug=([a-z0-9\-]+)", rationale)
    if match is None:
        match = re.search(r"\(([a-z0-9\-]+)\)", rationale)
    return match.group(1) if match else None


def _settle_esports_pick(row: dict, ledger, data_root=None) -> dict | None:
    """Settle an esports pick from the exchange's terminal market state.

    A resolved Polymarket market reports a terminal book state (verified live:
    ``MARKET_STATE_EXPIRED``) and terminal side prices — exactly 1 for the
    winning team's side and 0 for the loser. Returns None while pending.

    Ledger home/away were assigned from the contract's side ordering at log
    time (teams[0]=home), so the winning description maps directly onto the
    ledger's home/away teams: home won -> scores (0, 1); away won -> (1, 0).
    """
    from .data_sources.polymarket_us import PolymarketUSClient, _amount

    rationale = str(row.get("rationale", ""))
    slug = _extract_market_slug(rationale)
    if slug is None:
        return {"pick_id": row["pick_id"], "reason": "no market slug recorded on row"}
    client = PolymarketUSClient()
    try:
        market = client.market(slug)
        book = client.book(slug)
    except Exception:
        logger.warning("Polymarket market/book fetch failed for slug %s; pick %s stays unsettled", slug, row.get("pick_id"), exc_info=True)
        return None
    if str(book.get("state") or "") not in _TERMINAL_MARKET_STATES:
        return None
    prices: dict[str, float] = {}
    for side in market.get("marketSides", []):
        price = _amount(side.get("price"))
        if price is None:
            return None
        prices[str(side.get("description") or "")] = price
    if len(prices) != 2 or sorted(prices.values()) != [0.0, 1.0]:
        # A terminal book with a non-binary settlement price is not "still
        # pending" -- Polymarket's stats.settlementPx confirms this already
        # IS the market's final, official settlement value; it's just not a
        # clean win/loss. Per these contracts' own resolution rules (forfeit,
        # disqualification, or a postponement never rescheduled within two
        # weeks all "settle to the last fair market price"), this means the
        # match never definitively completed as scheduled. Void rather than
        # leave the pick open forever with no path to resolution.
        try:
            voided = ledger.void(
                row["pick_id"],
                "esports market settled to a non-binary price (forfeit/postponement per market rules)",
            )
            return {"pick_id": row["pick_id"], "voided": True, "result": voided["result"]}
        except (KeyError, ValueError) as error:
            return {"pick_id": row["pick_id"], "reason": str(error)}
    winning_description = next(name for name, price in prices.items() if price == 1.0)
    home_key = _identity_key(str(row["home_team"]))
    away_key = _identity_key(str(row["away_team"]))
    winner_key = _identity_key(winning_description)
    if winner_key == home_key:
        away_score, home_score = 0, 1
    elif winner_key == away_key:
        away_score, home_score = 1, 0
    else:
        return {
            "pick_id": row["pick_id"],
            "reason": f"winning side {winning_description!r} matches neither ledger team",
        }
    closing_probability = closing_odds = None
    if data_root is not None:
        try:
            closing_probability, closing_odds = _closing_probability_for_moneyline_pick(
                data_root,
                "esports",
                slug,
                row["event_start_utc"],
                row["home_team"],
                row["away_team"],
                row["selection"],
            )
        except (OSError, ValueError):
            logger.warning("esports closing-snapshot lookup failed for slug %s", slug, exc_info=True)
    try:
        result = ledger.settle(
            row["pick_id"],
            away_score,
            home_score,
            None,
            closing_odds,
            closing_raw_probability=closing_probability,
        )
        return {"pick_id": row["pick_id"], "result": result["result"], "settled": True}
    except (KeyError, ValueError) as error:
        return {"pick_id": row["pick_id"], "reason": str(error)}


def _settle_international_baseball_pick(row: dict, ledger, config) -> dict | None:
    """Settle a KBO/NPB pick from the official league schedule.

    Ledger home/away for these leagues are Polymarket's own team-name
    strings (see international_baseball.forecast_international_baseball_slate),
    not the official schedule's game_id -- find_international_baseball_result
    matches by game_date + team alias instead. Returns None while the game
    hasn't posted a final result yet.
    """
    from .international_baseball import find_international_baseball_result

    data_root = Path(ledger_path(config)).parent
    try:
        start = parse_utc(row["event_start_utc"])
    except ValueError:
        return {"pick_id": row["pick_id"], "reason": "bad event_start_utc"}
    game_date = start.date().isoformat()
    result = find_international_baseball_result(
        data_root, row["league"], game_date, row["home_team"], row["away_team"]
    )
    if result is None:
        return None
    away_score, home_score = result
    try:
        settlement_value = 0.5 if away_score == home_score else None
        settled = ledger.settle(
            row["pick_id"],
            away_score,
            home_score,
            None,
            None,
            binary_contract_settlement_value=settlement_value,
        )
        return {"pick_id": row["pick_id"], "result": settled["result"], "settled": True}
    except (KeyError, ValueError) as error:
        return {"pick_id": row["pick_id"], "reason": str(error)}


def _find_tennis_result(espn: ESPNClient, game_day: str, row: dict) -> dict | None:
    """Match a ledger row to a completed WTA singles match by player name.

    ESPN's tennis scoreboard nests matches under `groupings` with
    `athlete`-shaped competitors (see
    `data_sources.espn.completed_tennis_singles_matches`) rather than the
    flat `competitions`/`team` shape `_find_espn_result` assumes, so tennis
    needs its own matcher. Only WTA is checked -- tennis is only ever
    forecast (and therefore only ever needs settling) against WTA (see
    tennis_forward.py).
    """
    away_names = {row["away_team"].casefold(), row["original_away_team"].casefold()}
    home_names = {row["home_team"].casefold(), row["original_home_team"].casefold()}
    try:
        scoreboard = espn.scoreboard("WTA", game_day)
    except Exception:
        logger.warning(
            "ESPN WTA scoreboard fetch failed for %s; tennis settlement skipping", game_day, exc_info=True
        )
        return None
    for event in scoreboard.get("events", []):
        for grouping in event.get("groupings", []):
            for competition in grouping.get("competitions", []):
                slug = str(competition.get("type", {}).get("slug", ""))
                if "singles" not in slug:
                    continue
                competitors = competition.get("competitors", [])
                if len(competitors) != 2:
                    continue
                by_side = {item.get("homeAway"): item for item in competitors}
                away, home = by_side.get("away"), by_side.get("home")
                if not away or not home:
                    continue
                away_name = str((away.get("athlete") or {}).get("displayName", ""))
                home_name = str((home.get("athlete") or {}).get("displayName", ""))
                if away_name.casefold() not in away_names or home_name.casefold() not in home_names:
                    continue
                status = competition.get("status", {}).get("type", {})
                completed = bool(status.get("completed"))
                record = {"completed": completed, "status_name": str(status.get("name", ""))}
                if completed:
                    record["away_score"] = 1 if away.get("winner") else 0
                    record["home_score"] = 1 if home.get("winner") else 0
                return record
    return None


def _settle_tennis_pick(row: dict, ledger, espn: ESPNClient, data_root=None) -> dict | None:
    try:
        start = parse_utc(row["event_start_utc"])
    except ValueError:
        return {"pick_id": row["pick_id"], "reason": "bad event_start_utc"}
    game_day = start.astimezone(EASTERN).date().isoformat()
    match = _find_tennis_result(espn, game_day, row)
    if match is None or not match.get("completed"):
        return None
    closing_probability = closing_odds = None
    if data_root is not None:
        slug = _extract_market_slug(str(row.get("rationale", "")))
        if slug is not None:
            try:
                closing_probability, closing_odds = _closing_probability_for_moneyline_pick(
                    data_root,
                    "tennis",
                    slug,
                    row["event_start_utc"],
                    row["home_team"],
                    row["away_team"],
                    row["selection"],
                )
            except (OSError, ValueError):
                logger.warning("tennis closing-snapshot lookup failed for slug %s", slug, exc_info=True)
    try:
        settled = ledger.settle(
            row["pick_id"],
            match["away_score"],
            match["home_score"],
            None,
            closing_odds,
            closing_raw_probability=closing_probability,
        )
        return {"pick_id": row["pick_id"], "result": settled["result"], "settled": True}
    except (KeyError, ValueError) as error:
        return {"pick_id": row["pick_id"], "reason": str(error)}


def _identity_key(value: str) -> str:
    return "".join(c.lower() for c in value if c.isalnum())


def _load_soccer_scores() -> dict[str, dict[str, Any]]:
    """Load collected soccer scores from the historical JSONL, keyed by event_id."""
    import json

    path = PROJECT_ROOT / "data" / "historical" / "soccer_games_all.jsonl"
    scores: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return scores
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                game = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_id = str(game.get("event_id", ""))
            if event_id:
                scores[event_id] = game
    return scores


def _find_soccer_result(
    row: dict,
    scores: dict[str, dict[str, Any]],
) -> dict | None:
    """Match a ledger row to a collected soccer score by event_id or team names."""
    # Exact event_id match first.
    event_id = str(row.get("event_id", ""))
    if event_id and event_id in scores:
        game = scores[event_id]
        try:
            return {
                "status_name": "STATUS_FINAL",
                "completed": True,
                "away_score": int(game["away_score"]),
                "home_score": int(game["home_score"]),
            }
        except (KeyError, TypeError, ValueError):
            return None
    # Fall back to team-name matching.
    away_names = {str(row.get("away_team", "")).casefold(), str(row.get("original_away_team", "")).casefold()}
    home_names = {str(row.get("home_team", "")).casefold(), str(row.get("original_home_team", "")).casefold()}
    for game in scores.values():
        if str(game.get("away_team", "")).casefold() in away_names and str(game.get("home_team", "")).casefold() in home_names:
            try:
                return {
                    "status_name": "STATUS_FINAL",
                    "completed": True,
                    "away_score": int(game["away_score"]),
                    "home_score": int(game["home_score"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
    return None


def _clear_today_open(ledger, date_str: str, by_event_date: bool = False) -> list[str]:
    """Remove open picks for a date before re-forecasting, via the audited path.

    When by_event_date is True, also removes open picks whose event_start_utc
    matches date_str — used for flat ledger to prevent duplicate forecast runs.
    All removals go through ``PickLedger.remove_open_rows`` so they hold the
    ledger lock and append ``pick_removed`` audit events.

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
        research_scoring_mode=str(research_scoring.get("sizing", "fixed")),
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
        elif args.command == "order-status":
            output = PolymarketExecutor(audit).order_snapshots(args.order_id)
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
        elif args.command in {"forecast", "log", "flat-forecast"}:
            log = args.command == "log" or getattr(args, "log", False) or args.command == "flat-forecast"
            replace_today = getattr(args, "replace_today", False) or args.command == "flat-forecast"
            is_flat = args.command == "flat-forecast"
            sports = (
                [*FLAT_LEDGER_SPORTS, "soccer"]
                if is_flat and getattr(args, "all", False)
                else (
                    list(SPORTS) + list(ESPORTS_TITLES)
                    if getattr(args, "all", False)
                    else [args.sport or "mlb"]
                )
            )
            # Constructed unconditionally (not just when is_flat) so sports whose
            # main/flat ledgers form a pair -- soccer, matching how its research/
            # gated ledgers already pair -- can log to both from either command.
            flat_ledger_path = Path(ledger_path(config)).parent / "flat_picks.xlsx"
            flat_ledger = PickLedger(flat_ledger_path)
            if is_flat:
                if replace_today and log:
                    _clear_today_open(flat_ledger, args.date, by_event_date=True)
            elif replace_today and log:
                _clear_today_open(ledger, args.date, by_event_date=True)
            data_directory = Path(ledger_path(config)).parent
            if replace_today and log and not is_flat:
                selected_research_sports = (
                    RESEARCH_LEDGER_SPORTS
                    if getattr(args, "all", False)
                    else tuple(
                        sport
                        for sport in sports
                        if sport.casefold() in RESEARCH_LEDGER_SPORTS
                    )
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
            elif replace_today and log and is_flat and "soccer" in {s.casefold() for s in sports}:
                # Soccer's research/gated/main ledgers all get written
                # together with flat regardless of which command ran (see
                # _forecast_soccer_sport's docstring: main+flat and
                # research+gated are each a pair) -- clearing only
                # flat_ledger above (the is_flat branch) while leaving
                # these three untouched means a second same-day
                # flat-forecast run duplicates every soccer row in them,
                # since only flat gets deduped via the clear. Every other
                # flat-forecast sport only ever writes flat_ledger, so this
                # stays soccer-specific rather than blanket-applied to
                # every RESEARCH_LEDGER_SPORTS entry.
                _clear_today_open(
                    research_ledger(data_directory, "soccer"), args.date, by_event_date=True
                )
                _clear_today_open(
                    research_ledger(data_directory, "soccer", gated=True), args.date, by_event_date=True
                )
                _clear_today_open(ledger, args.date, by_event_date=True)
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
                        artifact_dir=PROJECT_ROOT / "config/models",
                        title=sport,
                        game_date=args.date,
                    )
                    if log and ledger is not None:
                        _log_esports_forecast(
                            results[sport],
                            config,
                            sport_research,
                            flat_mode=is_flat,
                            gated_ledger=sport_gated,
                            flat_ledger=flat_ledger,
                        )
                elif sport in ("kbo", "npb"):
                    results[sport] = _forecast_international_sport(
                        data_root=data_directory,
                        artifact_dir=PROJECT_ROOT / "config/models",
                        league=sport,
                        args_date=args.date,
                        config=config,
                        research_ledger=(
                            research_ledger(data_directory, sport)
                            if log and not is_flat
                            else None
                        ),
                        gated_ledger=(
                            research_ledger(data_directory, sport, gated=True)
                            if log and not is_flat
                            else None
                        ),
                        flat_ledger=flat_ledger,
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
                elif sport in LEARNED_PRODUCTION_SPORTS:
                    use_ledger = flat_ledger if is_flat else ledger
                    results[sport] = _forecast_learned_sport(
                        sport, args.date, log, config, registry, bans, use_ledger,
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
        elif args.command == "settle":
            if args.all_unsettled:
                output = _settle_all_unsettled(args, config, ledger)
                # Also settle the flat ledger
                flat_ledger_path = Path(ledger_path(config)).parent / "flat_picks.xlsx"
                if flat_ledger_path.exists():
                    flat_ledger = PickLedger(flat_ledger_path)
                    output["flat_settlement"] = _settle_all_unsettled(args, config, flat_ledger)
                data_directory = Path(ledger_path(config)).parent
                research_settlement = {}
                for sport_ledger in existing_research_ledgers(data_directory):
                    research_settlement[sport_ledger.path.stem] = _settle_all_unsettled(
                        args,
                        config,
                        sport_ledger,
                    )
                if research_settlement:
                    output["research_settlement"] = research_settlement
                gated_settlement = {}
                for sport_ledger in existing_research_ledgers(
                    data_directory,
                    gated=True,
                ):
                    gated_settlement[sport_ledger.path.stem] = _settle_all_unsettled(
                        args,
                        config,
                        sport_ledger,
                    )
                if gated_settlement:
                    output["gated_research_settlement"] = gated_settlement
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
            # Self-throttled to weekly (see mlb_baseline_refresh's module
            # docstring) -- safe to call every daily cron cycle since it's a
            # cheap no-op most of the time, but keeps park factors and league
            # rates from silently drifting stale for months between manual runs.
            try:
                mlb_baseline_refresh_result = refresh_if_due(data_root, PROJECT_ROOT)
            except (OSError, ValueError):
                logger.warning("MLB baseline refresh failed", exc_info=True)
                mlb_baseline_refresh_result = {"status": "error"}
            slate_args = argparse.Namespace(
                provider="polymarket",
                league=None,
                all=True,
                sport=None,
                date=args.date,
                timezone="America/New_York",
                no_snapshot_bbo=False,
            )
            # Run slate/BBO capture, WNBA availability, priors, soccer scores,
            # and MLB probables concurrently. These are independent I/O tasks.
            from .data_sources.odds_soccer_scores import collect_soccer_scores
            wnba_priors_result = {"status": "skipped"}
            mlb_probables_result: dict[str, Any] = {}
            soccer_collection = {}
            def _capture_wnba():
                try:
                    from .data_sources.espn import ESPNClient
                    wnba_scoreboard = ESPNClient().scoreboard("WNBA", args.date)
                    wnba_event_ids = [
                        str(event["id"]) for event in wnba_scoreboard.get("events", [])
                    ]
                    if wnba_event_ids:
                        capture_latest_report(data_root, observed_at=utc_now())
                        for event_id in wnba_event_ids:
                            try:
                                capture_espn_event_injuries(
                                    data_root, event_id=event_id,
                                    client=ESPNClient(), observed_at=utc_now(),
                                )
                            except Exception:
                                logger.warning("WNBA per-event injury capture failed for event %s", event_id, exc_info=True)
                except Exception:
                    logger.warning("WNBA injury report capture failed for %s", args.date, exc_info=True)
            def _build_priors():
                nonlocal wnba_priors_result
                try:
                    from .data_sources.espn import ESPNClient
                    from .features.base import FeatureStore
                    from .wnba_availability_evaluation import build_and_save_priors
                    wnba_priors_result = build_and_save_priors(
                        store=FeatureStore(data_root),
                        client=ESPNClient(),
                        game_date=args.date,
                        data_root=data_root,
                    )
                except Exception:
                    logger.warning("WNBA availability prior build failed for %s", args.date, exc_info=True)
            def _collect_soccer():
                nonlocal soccer_collection
                try:
                    soccer_collection = collect_soccer_scores(days_from=3)
                except Exception:
                    logger.warning("Soccer score collection failed", exc_info=True)
            def _capture_mlb_probables():
                nonlocal mlb_probables_result
                try:
                    mlb_probables_result = capture_probable_starter_snapshot(
                        args.date,
                    )
                except (OSError, TypeError, ValueError):
                    logger.warning(
                        "MLB probable-starter capture failed for %s",
                        args.date,
                        exc_info=True,
                    )
            mlb_availability_result: dict[str, Any] = {"status": "skipped"}
            def _capture_mlb_availability():
                # Shadow feature (features/mlb_player_availability.py) --
                # only captures raw roster/transaction data here; per-matchup
                # feature computation happens lazily, only if a future
                # artifact requests these feature names.
                nonlocal mlb_availability_result
                try:
                    from .data_sources.espn import ESPNClient
                    mlb_scoreboard = ESPNClient().scoreboard("MLB", args.date)
                    team_ids: set[int] = set()
                    for event in mlb_scoreboard.get("events", []):
                        competitors = event.get("competitions", [{}])[0].get("competitors", [])
                        for competitor in competitors:
                            name = (competitor.get("team") or {}).get("displayName", "")
                            try:
                                team_ids.add(team_id_for_name(name))
                            except ValueError:
                                continue
                    if not team_ids:
                        mlb_availability_result = {"status": "no_games", "date": args.date}
                        return
                    now = utc_now()
                    capture_roster_snapshot(data_root, sorted(team_ids), observed_at=now)
                    # 60-day lookback covers even a 60-Day IL stint's full
                    # placement-to-activation span; the end_date deliberately
                    # extends through today's real date (not just args.date)
                    # so one capture can serve any past cutoff_date's
                    # point-in-time reconstruction, not only today's slate.
                    transactions_start = (now.date() - timedelta(days=60)).isoformat()
                    transactions_end = now.date().isoformat()
                    txn_snapshot = capture_transactions_snapshot(
                        data_root,
                        sorted(team_ids),
                        transactions_start,
                        transactions_end,
                        observed_at=now,
                    )
                    mlb_availability_result = {
                        "status": "captured",
                        "team_count": len(team_ids),
                        "transaction_entries": len(txn_snapshot.get("entries", [])),
                    }
                except Exception:
                    logger.warning(
                        "MLB availability capture failed for %s", args.date, exc_info=True
                    )
                    mlb_availability_result = {"status": "error"}
            with ThreadPoolExecutor(max_workers=6) as io_pool:
                f0 = io_pool.submit(_polymarket_slate, slate_args, config)
                f1 = io_pool.submit(_capture_wnba)
                f2 = io_pool.submit(_build_priors)
                f3 = io_pool.submit(_collect_soccer)
                f4 = io_pool.submit(_capture_mlb_probables)
                f5 = io_pool.submit(_capture_mlb_availability)
                for f in (f1, f2, f3, f4, f5):
                    f.result()  # Wait for all, surface exceptions
                try:
                    slate = f0.result()
                except Exception:
                    # Real bug fixed 2026-08-02: this used to be an unhandled
                    # f0.result() -- a transient Polymarket network error here
                    # crashed the *entire* daily job (MLB, WNBA, NBA, NFL,
                    # soccer, esports, KBO, NPB, tennis, none of it ran) even
                    # though every per-sport forecast below fetches its own
                    # market data independently and doesn't actually need this
                    # step's result. This capture is a prospective BBO/event
                    # snapshot for reporting, not a hard prerequisite for the
                    # forecasts that follow -- log loudly and degrade instead
                    # of taking down work that would have otherwise succeeded.
                    logger.error("Polymarket slate/BBO capture failed for %s", args.date, exc_info=True)
                    slate = {
                        "status": "error",
                        "event_count": 0,
                        "events_by_league": {},
                        "prospective_bbo_capture": {},
                    }
            _clear_today_open(ledger, args.date, by_event_date=True)
            # Also clear and forecast for flat ledger
            flat_ledger_path = Path(ledger_path(config)).parent / "flat_picks.xlsx"
            flat_ledger = PickLedger(flat_ledger_path)
            _clear_today_open(flat_ledger, args.date, by_event_date=True)
            max_data_age = float(config["project"].get("maximum_data_age_hours", 12))
            max_disagreement = float(config["project"].get("maximum_unreviewed_market_disagreement", 0.10))
            # A single decision timestamp makes the learned-slate cache reusable
            # when the same candidates are re-logged to the flat ledger. Before
            # this, two calls a few milliseconds apart rebuilt every feature
            # and repeated upstream reads for no decision-quality benefit.
            forecast_observed_at = utc_now()
            data_directory = Path(ledger_path(config)).parent
            for research_sport in RESEARCH_LEDGER_SPORTS:
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
            # Compute forecasts once, log to both ledgers (compute > log main > log research > log flat)
            forecast_result = {}
            workers = min(len(DAILY_LEARNED_SPORTS), 5)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {}
                for sport in DAILY_LEARNED_SPORTS:
                    futures[pool.submit(
                        _forecast_learned_sport,
                        sport, args.date, True, config, registry, bans, ledger,
                        maximum_data_age_hours=max_data_age,
                        maximum_unreviewed_disagreement=max_disagreement,
                        research_ledger=None,
                        gated_ledger=None,
                        observed_at=forecast_observed_at,
                    )] = sport
                for future in as_completed(futures):
                    sport = futures[future]
                    try:
                        forecast_result[sport] = future.result()
                    except Exception as exc:
                        forecast_result[sport] = {
                            "sport": sport, "status": "error", "reason": str(exc),
                            "logged": 0, "candidates": [],
                        }
            # Re-log computed candidates to flat ledger (flat mode, no edge gate)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                flat_futures = {}
                for sport in DAILY_LEARNED_SPORTS:
                    result = forecast_result.get(sport, {})
                    candidates = result.get("candidates", [])
                    if candidates:
                        flat_futures[pool.submit(
                            _forecast_learned_sport,
                            sport, args.date, True, config, registry, bans, flat_ledger,
                            maximum_data_age_hours=max_data_age,
                            maximum_unreviewed_disagreement=max_disagreement,
                            flat_mode=True,
                            exposure_ledger=ledger,
                            observed_at=forecast_observed_at,
                        )] = sport
                for future in as_completed(flat_futures):
                    sport = flat_futures[future]
                    try:
                        forecast_result[f"_flat_{sport}"] = future.result()
                    except Exception:
                        logger.warning("Flat forecast failed for sport %s", sport, exc_info=True)
            # MLB totals, soccer, tennis, esports, and international baseball
            # are independent of each other and of the four learned sports
            # above (already logged by the pool before this point) -- they
            # used to run one after another and were the dominant share of
            # daily's wall-clock time (soccer alone fans out to 18 ESPN
            # leagues, tennis reads the largest historical dataset in the
            # project). Run them concurrently instead. This is safe to do
            # because every one of these already guards its own ledger
            # appends with the same in-process _LEDGER_LOCK the four learned
            # sports' pool above already relies on for concurrent writes to
            # the same ledger files (see _log_esports_forecast's lock
            # comment, mirrored in _forecast_soccer_sport/_forecast_tennis_sport/
            # _forecast_international_sport/_forecast_mlb_totals_flat).
            def _mlb_totals_task() -> None:
                try:
                    forecast_result["mlb_totals"] = _forecast_mlb_totals_flat(
                        args.date, True, config, registry, bans, flat_ledger, audit,
                        main_ledger=ledger,
                    )
                except Exception:
                    logger.warning("MLB totals flat forecast failed", exc_info=True)

            def _soccer_task() -> None:
                # Soccer: Main+Flat only (operator directive 2026-08-03).
                # Previously uncaught here (a crash would have taken down
                # tennis/esports/international-baseball/settlement below it
                # too) -- now caught like its siblings so one failing block
                # can't wedge the concurrent pool's other independent tasks.
                try:
                    forecast_result["soccer"] = _forecast_soccer_sport(
                        data_root=data_directory,
                        args_date=args.date,
                        config=config,
                        flat_ledger=flat_ledger,
                        main_ledger=ledger,
                    )
                except Exception:
                    logger.warning("Soccer forecast failed", exc_info=True)
                    return
                # Not a hard failure -- these are separate leagues in one daily
                # run, and a loud warning here (rather than an exception) is what
                # would have surfaced the KBO/NPB silent-zero-picks incident
                # immediately instead of it running unnoticed for months.
                _priced_soccer = forecast_result["soccer"].get("priced_contracts") or []
                if _priced_soccer and not forecast_result["soccer"].get("logged"):
                    logger.warning(
                        "zero rows logged for soccer despite %d priced contracts",
                        len(_priced_soccer),
                    )

            def _tennis_task() -> None:
                try:
                    forecast_result["tennis"] = _forecast_tennis_sport(
                        data_root=data_directory,
                        args_date=args.date,
                        config=config,
                        main_ledger=ledger,
                        flat_ledger=flat_ledger,
                    )
                    _priced_tennis = forecast_result["tennis"].get("priced_contracts") or []
                    if _priced_tennis and not forecast_result["tennis"].get("logged"):
                        logger.warning(
                            "zero rows logged for tennis despite %d priced contracts",
                            len(_priced_tennis),
                        )
                except Exception:
                    logger.warning("Tennis forecast failed", exc_info=True)

            def _esports_title_task(title: str) -> None:
                forecast_result[title] = forecast_esports_slate(
                    data_root=data_directory,
                    artifact_dir=PROJECT_ROOT / "config/models",
                    title=title,
                    game_date=args.date,
                )
                _esports_logged = _log_esports_forecast(
                    forecast_result[title],
                    config,
                    research_ledger(data_directory, title),
                    flat_mode=False,
                    gated_ledger=research_ledger(data_directory, title, gated=True),
                    flat_ledger=flat_ledger,
                )
                _priced_esports = forecast_result[title].get("priced_contracts") or []
                if _priced_esports and not _esports_logged:
                    logger.warning(
                        "zero rows logged for %s despite %d priced contracts",
                        title, len(_priced_esports),
                    )

            def _esports_block() -> None:
                try:
                    forecast_result["_esports_ratings_refresh"] = _refresh_esports_ratings(data_directory)
                except Exception:
                    logger.warning("Esports ratings refresh failed", exc_info=True)
                # Titles are independent of each other once ratings are
                # refreshed above -- fan them out too instead of one at a time.
                with ThreadPoolExecutor(max_workers=len(ESPORTS_TITLES)) as title_pool:
                    title_futures = {
                        title_pool.submit(_esports_title_task, title): title
                        for title in ESPORTS_TITLES
                    }
                    for future in as_completed(title_futures):
                        title = title_futures[future]
                        try:
                            future.result()
                        except Exception:
                            logger.warning("Esports forecast failed for title %s", title, exc_info=True)

            def _intl_baseball_league_task(league: str) -> None:
                forecast_result[league] = _forecast_international_sport(
                    data_root=data_directory,
                    artifact_dir=PROJECT_ROOT / "config/models",
                    league=league,
                    args_date=args.date,
                    config=config,
                    research_ledger=research_ledger(data_directory, league),
                    gated_ledger=research_ledger(data_directory, league, gated=True),
                    flat_ledger=flat_ledger,
                )
                _priced_intl = forecast_result[league].get("priced_contracts") or []
                if _priced_intl and not forecast_result[league].get("logged"):
                    logger.warning(
                        "zero rows logged for %s despite %d priced contracts",
                        league, len(_priced_intl),
                    )

            def _intl_baseball_block() -> None:
                try:
                    forecast_result["_international_baseball_ratings_refresh"] = (
                        _refresh_international_baseball_ratings(data_directory)
                    )
                except Exception:
                    logger.warning("International baseball ratings refresh failed", exc_info=True)
                # International baseball — logged to research/gated/flat ledgers
                with ThreadPoolExecutor(
                    max_workers=len(DAILY_INTERNATIONAL_BASEBALL_SPORTS)
                ) as league_pool:
                    league_futures = {
                        league_pool.submit(_intl_baseball_league_task, league): league
                        for league in DAILY_INTERNATIONAL_BASEBALL_SPORTS
                    }
                    for future in as_completed(league_futures):
                        league = league_futures[future]
                        try:
                            future.result()
                        except Exception:
                            logger.warning(
                                "International baseball forecast failed for league %s",
                                league, exc_info=True,
                            )

            with ThreadPoolExecutor(max_workers=5) as research_pool:
                research_futures = [
                    research_pool.submit(_mlb_totals_task),
                    research_pool.submit(_soccer_task),
                    research_pool.submit(_tennis_task),
                    research_pool.submit(_esports_block),
                    research_pool.submit(_intl_baseball_block),
                ]
                # Each task above already catches and logs its own real
                # forecast errors; .result() here only re-raises a bug in the
                # wrapper itself (e.g. a NameError), which should still stop
                # the run loudly rather than be swallowed.
                for future in as_completed(research_futures):
                    future.result()
            flat_result = {
                sport: forecast_result.get(
                    f"_flat_{sport}", forecast_result.get(sport, {})
                )
                for sport in DAILY_LEARNED_SPORTS
            }
            for result in forecast_result.values():
                result.pop("candidates", None)
            # Read back stored Polymarket odds snapshots for per-sport summaries.
            # Tennis/KBO/NPB are captured and forecasted the same as every
            # other sport (see BBO_CAPTURE_SPORTS in polymarket_us.py) but
            # were missing from LEARNED_PRODUCTION_SPORTS, so this summary
            # silently never showed their snapshot counts even though their
            # capture and forecasting worked correctly the whole time.
            odds_by_sport = {}
            for sport in (*LEARNED_PRODUCTION_SPORTS, "tennis", "kbo", "npb"):
                odds_sport = "esports" if sport in ESPORTS_TITLES else sport
                snap_path = (
                    Path(ledger_path(config)).parent
                    / "odds" / odds_sport / args.date / "polymarket_snapshots.jsonl"
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
            for result in flat_result.values():
                result.pop("candidates", None)
            if args.skip_settlement:
                settlement = {"status": "skipped", "reason": "caller_settled_before daily"}
                flat_settlement = settlement
                _research_settlement = settlement
                _gated_settlement = settlement
            else:
                # A postponed/canceled game never becomes "completed" under its
                # original event_id -- ESPN issues a new event_id for any
                # reschedule -- so leaving void_postponed off here means an
                # affected pick sits "open" forever with no automatic path to
                # resolution; only a manually-run `settle --void-postponed`
                # would ever clear it. Auto-void in the unattended daily run.
                settle_args = argparse.Namespace(
                    all_unsettled=True,
                    void_postponed=True,
                )
                settlement = _settle_all_unsettled(settle_args, config, ledger)
                flat_settlement = _settle_all_unsettled(
                    settle_args,
                    config,
                    flat_ledger,
                )
                _research_settlement = {
                    sport_ledger.path.stem: _settle_all_unsettled(
                        settle_args,
                        config,
                        sport_ledger,
                    )
                    for sport_ledger in existing_research_ledgers(data_directory)
                }
                _gated_settlement = {
                    sport_ledger.path.stem: _settle_all_unsettled(
                        settle_args,
                        config,
                        sport_ledger,
                    )
                    for sport_ledger in existing_research_ledgers(
                        data_directory,
                        gated=True,
                    )
                }

            output = {
                "date": args.date,
                "step0_mlb_baseline_refresh": mlb_baseline_refresh_result,
                "step1_polymarket_search": {
                    "status": slate.get("status", "ok"),
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
                "step1c_mlb_probable_starters": {
                    "captured_events": len(mlb_probables_result),
                    "archive": "data/point_in_time/mlb_probable_starters.jsonl",
                },
                "step5_summary": _summary(config, ledger),
                "step5b_wnba_availability": {
                    "priors": wnba_priors_result,
                },
                "step5c_mlb_availability": mlb_availability_result,
                "step6_flat_forecast_and_log": flat_result,
                "step7_flat_settlement": flat_settlement,
                "step8_research_settlement": _research_settlement,
                "step9_gated_research_settlement": _gated_settlement,
            }
        elif args.command == "ingest":
            output = Ingestor(data_root, audit=audit).ingest_scores(args.sport, args.date)
        elif args.command == "wnba-availability-capture":
            availability_observed_at = (
                parse_utc(args.observed_at) if args.observed_at else utc_now()
            )
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
        elif args.command == "bootstrap":
            ingestor = Ingestor(data_root, audit=audit)
            if args.all:
                output = {
                    sport: ingestor.bootstrap(sport, args.from_date, args.to_date)
                    for sport in ESPN_SPORTS
                }
            elif args.sport:
                output = ingestor.bootstrap(args.sport, args.from_date, args.to_date)
            else:
                raise ValueError("provide --sport or --all")
        elif args.command == "esports-backfill":
            if args.all:
                titles = tuple(TITLE_SPECS)
            elif args.title:
                titles = (args.title,)
            else:
                raise ValueError("provide --title or --all")
            output = {
                title: backfill_esports(data_root, title, args.from_date, args.to_date)
                for title in titles
            }
        elif args.command == "validate-esports":
            output = validate_all_esports_baselines(
                data_root,
                args.titles,
                PROJECT_ROOT / "config/models" if args.write_artifacts else None,
            )
            destination = PROJECT_ROOT / args.output
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
                encoding="utf-8",
            )
            output["report_path"] = str(destination)
        elif args.command == "esports-forecast":
            # Manual/debugging use only. run_daily.sh no longer calls this —
            # its --all invocation duplicated the esports logging that
            # `forecast --all` (Step 4) already does per title, producing
            # near-simultaneous duplicate research rows for the same contract.
            titles = list(TITLE_SPECS) if getattr(args, "all", False) else [args.title]
            output = {}
            for title in titles:
                forecast = forecast_esports_slate(
                    data_root,
                    PROJECT_ROOT / "config/models",
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
        elif args.command == "international-baseball-backfill":
            if args.all:
                leagues = tuple(INTERNATIONAL_BASEBALL_LEAGUE_SPECS)
            elif args.league:
                leagues = (args.league,)
            else:
                raise ValueError("provide --league or --all")
            output = {
                league: backfill_international_baseball(
                    data_root, league, args.from_date, args.to_date
                )
                for league in leagues
            }
        elif args.command == "validate-international-baseball":
            output = validate_all_international_baseball_baselines(
                data_root,
                args.leagues,
                PROJECT_ROOT / "config/models" if args.write_artifacts else None,
            )
            destination = PROJECT_ROOT / args.output
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
                encoding="utf-8",
            )
            output["report_path"] = str(destination)
        elif args.command == "international-baseball-forecast":
            output = forecast_international_baseball_slate(
                data_root,
                PROJECT_ROOT / "config/models",
                args.league,
                args.date,
                args.timezone,
            )
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
        elif args.command == "refresh-mlb-baselines":
            output = refresh_if_due(
                data_root, PROJECT_ROOT, min_days=args.min_days, force=args.force
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
                if (
                    args.action == "buy"
                    and execution_config.get("manual_research_require_positive_edge", True)
                    and args.price >= model_probability
                ):
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
                artifact_qualified=_row_artifact_qualified(row, config),
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
        elif args.command == "verify-checklist":
            from .source_policy import DEFAULT_SOURCES
            from .verification_checklist import format_checklist, run_checklist

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
        elif args.command == "verify-chain":
            output = _verify_chain(audit_path(config), ledger)
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


def _fail(message: str, reason_code: str) -> None:
    print(
        json.dumps({"decision": "NO_CALL", "reason_code": reason_code, "error": message}, indent=2),
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
