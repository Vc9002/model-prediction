"""Argument parsing for the model_prediction CLI.

Mechanical extraction from the former cli.py monolith (DD-6 split, stage
7). The argparse tree moves verbatim, including the ban-team
sub-subparsers, so every command name and help text is unchanged.
"""

from __future__ import annotations

import argparse

from ..domain import LOSS_CLASSIFICATIONS, League, MarketType, ModelOrigin, ModelState, eastern_today
from ..esports import TITLE_SPECS
from ..international_baseball import LEAGUE_SPECS as INTERNATIONAL_BASEBALL_LEAGUE_SPECS
from .state import ESPN_SPORTS, ESPORTS_TITLES, SPORTS


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
