"""model-prediction CLI.
# TODO(P2-1): ~4,300-line monolith with ~48 subcommands and near-zero
# direct behavioral coverage — see MASTER.md P2-1 / docs/TODO.md. Split
# per the ENGINEERING_ROADMAP before adding new commands.

HACK(DD-6): 4,407-line monolithic file with 48 subcommands, near-zero test
coverage. Should be split into a cli/ package with one module per command
group (forecast, settle, daily, validate, esports, etc.). See MASTER.md §DD-6.

Daily loop: polymarket-slate -> forecast --log -> settle --all-unsettled ->
summary (or `daily` for all of it). Everything is shadow/paper by default;
the only real-money path is the `execute` subcommand behind the hard gate in
``data_sources/polymarket_execute.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from dataclasses import replace as replace
from datetime import UTC as UTC
from datetime import date
from pathlib import Path

from ..audit import AuditLog
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
    config_path,
    entity_registry_path,
    ledger_path,
    load_config,
    market_odds_snapshot_path,
    polymarket_snapshot_path,
    unit_policy,
)
from ..data_sources.espn import ESPNClient
from ..data_sources.espn import ESPNMLBClient as ESPNMLBClient
from ..data_sources.espn_probables import (
    capture_probable_starter_snapshot as capture_probable_starter_snapshot,
)
from ..data_sources.espn_wnba_injuries import capture_espn_event_injuries
from ..data_sources.mlb_injuries import (
    capture_roster_snapshot as capture_roster_snapshot,
)
from ..data_sources.mlb_injuries import (
    capture_transactions_snapshot as capture_transactions_snapshot,
)
from ..data_sources.mlb_injuries import (
    team_id_for_name as team_id_for_name,
)
from ..data_sources.mlb_lineups import capture_and_store as capture_and_store
from ..data_sources.mlb_market_odds import MarketOddsSnapshotStore
from ..data_sources.mlb_market_odds import MLBMarketOddsFeed as MLBMarketOddsFeed
from ..data_sources.polymarket_execute import (
    ExecutionGateError,
    OrderTicket,
    PolymarketExecutor,
)
from ..data_sources.polymarket_us import (
    PolymarketSnapshotStore,
    PolymarketUSClient,
    probability_to_american,
    refresh_contract_snapshots,
)
from ..data_sources.the_odds_api import TheOddsAPIClient as TheOddsAPIClient
from ..data_sources.wnba_injuries import capture_latest_report
from ..domain import (
    EASTERN,
    LEARNED_PRODUCTION_SPORTS,
    LOSS_CLASSIFICATIONS,
    League,
    MarketType,
    ModelOrigin,
    ModelState,
    PickRequest,
    RecordType,
    eastern_today,
    parse_utc,
    utc_now,
)
from ..domain import (
    PRODUCTION_SPORTS as PRODUCTION_SPORTS,
)
from ..domain import (
    iso_utc as iso_utc,
)
from ..eligibility import (
    evaluate_eligibility,
)
from ..eligibility import (
    evaluate_gated_research_eligibility as evaluate_gated_research_eligibility,
)
from ..entities import EntityRegistry, EntityResolutionError
from ..esports import (
    TITLE_SPECS,
    backfill_esports,
    forecast_esports_slate,
    validate_all_esports_baselines,
)
from ..esports import (
    refresh_recent_matches as refresh_recent_matches,
)
from ..features.base import FeatureStore
from ..forward import build_mlb_slate as build_mlb_slate
from ..ingest import Ingestor
from ..international_baseball import (
    LEAGUE_SPECS as INTERNATIONAL_BASEBALL_LEAGUE_SPECS,
)
from ..international_baseball import (
    backfill_international_baseball,
    forecast_international_baseball_slate,
    validate_all_international_baseball_baselines,
)
from ..international_baseball import (
    refresh_recent_international_baseball_matches as refresh_recent_international_baseball_matches,
)
from ..learned_forward import build_learned_moneyline_slate as build_learned_moneyline_slate
from ..learned_forward import match_executable_quote as match_executable_quote
from ..ledger import LEDGER_SCHEMA_VERSION, DuplicatePickError
from ..main_ledgers import MAIN_LEDGER_SPORTS, MultiSportPickLedger
from ..mlb_baseline_refresh import refresh_if_due
from ..models import MODEL_SPECS
from ..models.market_residual import MarketResidualModel, ResidualTrainingRow
from ..models.mlb import load_formula_spec as load_formula_spec
from ..models.registry import model_spec
from ..research_ledgers import (
    RESEARCH_LEDGER_SPORTS,
    existing_research_ledgers,
    research_ledger,
)
from ..runtime_paths import rolling_models_root as rolling_models_root
from ..soccer_forward import build_soccer_total_slate as build_soccer_total_slate
from ..tennis_forward import build_tennis_slate as build_tennis_slate
from ..total_score import validate_all_total_score_models
from ..units import edge_scaled_units
from ..validation import run_validation_audit, write_production_artifacts

# Defined HERE, not imported from .state: ruff's BLE001 exemption for the
# blind-except handlers in this module requires a locally-defined logger
# (found 2026-08-19 -- importing the same logger object tripped 20 BLE001s).
logger = logging.getLogger("model_prediction.cli")

from .commands import (
    _clear_today_open,
    _handle_ban,
    _polymarket_slate,
    _research_models_dir,
    _row_artifact_qualified,
    _summary,
    _verify_chain,
)
from .daily import run_daily
from .forecast import (  # noqa: F401 -- re-export for compat (DD-6 split)
    _append_secondary_ledger,
    _forecast_international_sport,
    _forecast_learned_sport,
    _forecast_mlb,
    _forecast_mlb_totals_flat,
    _forecast_research_sport,
    _forecast_soccer_sport,
    _forecast_tennis_sport,
    _forecast_wnba_spread_slate,
    _forecast_wnba_spread_sport,
    _load_market_residual_model,
    _log_esports_forecast,
    _refresh_esports_ratings,
    _refresh_international_baseball_ratings,
    _select_wnba_spread_market,
)
from .settle import (  # noqa: F401 -- re-export for compat (DD-6 split)
    _closing_probability_for_moneyline_pick,
    _extract_market_slug,
    _find_espn_result,
    _find_soccer_result,
    _find_tennis_result,
    _identity_key,
    _load_soccer_scores,
    _settle_all_unsettled,
    _settle_esports_pick,
    _settle_international_baseball_pick,
    _settle_tennis_pick,
)
from .state import (  # noqa: F401 -- re-export for compat (DD-6 split)
    _LEDGER_LEAGUE_TO_ESPN,
    _LEDGER_LOCK,
    _TERMINAL_MARKET_STATES,
    DAILY_INTERNATIONAL_BASEBALL_SPORTS,
    DAILY_LEARNED_SPORTS,
    DUAL_LEDGER_SPORTS,
    ESPN_SPORTS,
    ESPORTS_TITLES,
    FLAT_LEDGER_SPORTS,
    RESEARCH_ONLY_DAILY_SPORTS,
    SPORTS,
)


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
    slate.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )
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
    ledger_prices.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )
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
    clv.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )

    forecast = commands.add_parser("forecast", help="pregame learned LR + confidence-gate moneyline slate")
    forecast.add_argument("--sport", choices=SPORTS + ESPORTS_TITLES)
    forecast.add_argument("--all", action="store_true")
    forecast.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )
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
    forecast.add_argument(
        "--force", action="store_true", help="bypass event_started guard (for historical backfill)"
    )

    flat_forecast = commands.add_parser(
        "flat-forecast", help="forecast every game with no edge gate → flat_picks.xlsx"
    )
    flat_forecast.add_argument("--sport", choices=SPORTS + ESPORTS_TITLES)
    flat_forecast.add_argument("--all", action="store_true")
    flat_forecast.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )
    flat_forecast.add_argument("--log", action="store_true", help="log all calls to flat ledger")
    flat_forecast.add_argument(
        "--force", action="store_true", help="bypass event_started guard (for historical backfill)"
    )

    log_cmd = commands.add_parser("log", help="alias for forecast --log")
    log_cmd.add_argument("--sport", choices=SPORTS, default="mlb")
    log_cmd.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )
    log_cmd.add_argument("--model", choices=("learned", "legacy-measured-edge"), default="learned")

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
    daily.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )
    daily.add_argument(
        "--skip-settlement",
        action="store_true",
        help="skip settlement when the caller already completed it",
    )

    ingest = commands.add_parser("ingest", help="cache one date of ESPN scores locally")
    ingest.add_argument("--sport", required=True, choices=ESPN_SPORTS)
    ingest.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )

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
    esports_validate.add_argument("--output", default="outputs/latest/esports-baseline-validation.json")
    esports_validate.add_argument("--write-artifacts", action="store_true")

    esports_forecast = commands.add_parser(
        "esports-forecast",
        help="zero-unit exact-identity LoL/CS2 prices for Polymarket US match-winner contracts",
    )
    esports_forecast.add_argument("--title", choices=tuple(TITLE_SPECS))
    esports_forecast.add_argument("--all", action="store_true", help="forecast all esports titles")
    esports_forecast.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )
    esports_forecast.add_argument("--timezone", default="America/New_York")
    esports_forecast.add_argument("--log", action="store_true", help="log forecast to research ledger")

    international_backfill = commands.add_parser(
        "international-baseball-backfill",
        help="backfill official no-key KBO and NPB regular-season results",
    )
    international_backfill.add_argument("--league", choices=tuple(INTERNATIONAL_BASEBALL_LEAGUE_SPECS))
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
    international_forecast.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )
    international_forecast.add_argument("--timezone")

    entities = commands.add_parser("bootstrap-entities", help="merge ESPN team lists into the registry")
    entities.add_argument("--league", required=True)

    features = commands.add_parser("features", help="compute point-in-time feature snapshots")
    features.add_argument("--sport", required=True, choices=SPORTS)
    features.add_argument(
        "--date", default=eastern_today().isoformat(), help="ISO date; defaults to today in US-Eastern time"
    )
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
    mlb_baselines.add_argument("--force", action="store_true", help="refresh even if the last one was recent")
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

    collect = commands.add_parser(
        "collect-scores", help="pull recent soccer scores from The Odds API (free tier, last 3 days)"
    )
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

    freeze = commands.add_parser(
        "freeze-production",
        help="snapshot current production champions as an immutable frozen registry",
    )
    freeze.add_argument(
        "--output",
        default=None,
        help="override the default frozen champions path",
    )

    compare = commands.add_parser(
        "compare-champion",
        help="run a paired champion-vs-challenger evaluation",
    )
    compare.add_argument(
        "--challenger-predictions",
        required=True,
        help="path to JSON/JSONL file with challenger predictions",
    )
    compare.add_argument(
        "--champion-predictions",
        required=True,
        help="path to JSON/JSONL file with champion predictions",
    )
    compare.add_argument("--sport", required=True, help="sport key (mlb, wnba, etc.)")
    compare.add_argument("--market", default="moneyline", help="market type (default: moneyline)")

    return root


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


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
    data_root = Path(ledger_path(config)).parent
    # Main used to be one file (data/picks.xlsx) holding every sport, mixed
    # together and distinguished only by a `league` column. Split 2026-08-03
    # into one file per sport (data/main/<sport>.xlsx), same as Research/
    # Gated Research always worked. MultiSportPickLedger presents the exact
    # same interface a single PickLedger always had, routing each call to
    # the right per-sport file underneath -- every command below keeps
    # working unchanged. See main_ledgers.py for the real behavior change
    # this implies (exposure caps are now independent per sport).
    ledger = MultiSportPickLedger(
        data_root,
        research_score_units=research_score_units,
        research_scoring_mode=str(research_scoring.get("sizing", "fixed")),
        research_scoring_note=research_scoring.get("note", "fixed-stake hypothetical research scoring"),
        retired=not config["project"].get("main_ledger_enabled", True),
    )
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
            output = {league.value: asdict(model_spec(league)) for league in MODEL_SPECS}
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
            output = refresh_contract_snapshots(PolymarketUSClient(), contracts, data_root, args.date)
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
                [*FLAT_LEDGER_SPORTS, "soccer", "tennis"]
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
                    _clear_today_open(
                        flat_ledger, args.date, by_event_date=True, leagues=main_ledger_sport_scope
                    )
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
                        research_ledger=(
                            research_ledger(data_directory, sport) if log and not is_flat else None
                        ),
                        gated_ledger=(
                            research_ledger(data_directory, sport, gated=True)
                            if log and not is_flat
                            else None
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
        elif args.command == "settle":
            if args.all_unsettled:
                output = _settle_all_unsettled(args, config, ledger)
                # Also settle the flat ledger
                flat_ledger = MultiSportPickLedger(data_root, flat=True)
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
            output = run_daily(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "ingest":
            output = Ingestor(data_root, audit=audit).ingest_scores(args.sport, args.date)
        elif args.command == "wnba-availability-capture":
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
        elif args.command == "bootstrap":
            ingestor = Ingestor(data_root, audit=audit)
            if args.all:
                output = {
                    sport: ingestor.bootstrap(sport, args.from_date, args.to_date) for sport in ESPN_SPORTS
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
                title: backfill_esports(data_root, title, args.from_date, args.to_date) for title in titles
            }
        elif args.command == "validate-esports":
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
        elif args.command == "international-baseball-backfill":
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
        elif args.command == "validate-international-baseball":
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
        elif args.command == "international-baseball-forecast":
            output = forecast_international_baseball_slate(
                data_root,
                _research_models_dir(),
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
                _research_models_dir() if args.write_artifacts else None,
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
            output = refresh_if_due(data_root, PROJECT_ROOT, min_days=args.min_days, force=args.force)
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
            from ..data_sources.odds_soccer_scores import collect_soccer_scores

            output = collect_soccer_scores(days_from=args.days)
        elif args.command == "verify-checklist":
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
                if row["league"].lower() == sport
                and row["record_type"] == RecordType.RESEARCH_OBSERVATION.value
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
        elif args.command == "freeze-production":
            registry = ProductionRegistry()
            snapshots = registry.freeze()
            store = FrozenProductionStore()
            store.write(registry)
            output = {
                "status": "frozen",
                "frozen_at_utc": snapshots[0].frozen_at_utc if snapshots else "",
                "champions": [s.to_dict() for s in snapshots],
            }
        elif args.command == "compare-champion":
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


def _fail(message: str, reason_code: str) -> None:
    print(
        json.dumps({"decision": "NO_CALL", "reason_code": reason_code, "error": message}, indent=2),
        file=sys.stderr,
    )
    raise SystemExit(2)
