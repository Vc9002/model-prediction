from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from ..audit import AuditLog
from ..bans import TeamBanList
from ..config import (
    audit_path,
    config_path,
    entity_registry_path,
    ledger_path,
    load_config,
)
from ..data_sources.polymarket_execute import (
    ExecutionGateError,
)
from ..entities import EntityRegistry, EntityResolutionError
from ..ledger import DuplicatePickError
from ..main_ledgers import MultiSportPickLedger
from .commands import (
    cmd_backtest,
    cmd_ban_team,
    cmd_bootstrap,
    cmd_bootstrap_entities,
    cmd_call,
    cmd_collect_scores,
    cmd_compare_champion,
    cmd_esports_backfill,
    cmd_esports_forecast,
    cmd_execute,
    cmd_exposure,
    cmd_features,
    cmd_freeze_production,
    cmd_ingest,
    cmd_init_ledger,
    cmd_international_baseball_backfill,
    cmd_international_baseball_forecast,
    cmd_live_portfolio,
    cmd_models,
    cmd_order_status,
    cmd_polymarket_clv,
    cmd_polymarket_ledger_prices,
    cmd_polymarket_slate,
    cmd_polymarket_snapshot,
    cmd_reconstruct_mlb_markets,
    cmd_refresh_mlb_baselines,
    cmd_report,
    cmd_review_loss,
    cmd_score_research,
    cmd_sell_position,
    cmd_summary,
    cmd_train_residual,
    cmd_update_closing,
    cmd_validate_esports,
    cmd_validate_international_baseball,
    cmd_validate_models,
    cmd_validate_totals,
    cmd_verify_chain,
    cmd_verify_checklist,
    cmd_void,
    cmd_wnba_availability_capture,
)
from .parser import parser

# Defined HERE, not imported from .state: ruff's BLE001 exemption for the
# blind-except handlers in this module requires a locally-defined logger
# (found 2026-08-19 -- importing the same logger object tripped 20 BLE001s).
logger = logging.getLogger("model_prediction.cli")

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
    run_forecast,
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
    run_settle,
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
            output = cmd_init_ledger(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "report":
            output = cmd_report(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "models":
            output = cmd_models(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "summary":
            output = cmd_summary(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "live-portfolio":
            output = cmd_live_portfolio(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "order-status":
            output = cmd_order_status(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "polymarket-slate":
            output = cmd_polymarket_slate(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "polymarket-snapshot":
            output = cmd_polymarket_snapshot(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "polymarket-ledger-prices":
            output = cmd_polymarket_ledger_prices(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "polymarket-clv":
            output = cmd_polymarket_clv(args, config, registry, bans, ledger, audit, data_root)
        elif args.command in {"forecast", "log", "flat-forecast"}:
            output = run_forecast(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "settle":
            output = run_settle(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "daily":
            output = run_daily(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "ingest":
            output = cmd_ingest(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "wnba-availability-capture":
            output = cmd_wnba_availability_capture(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "bootstrap":
            output = cmd_bootstrap(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "esports-backfill":
            output = cmd_esports_backfill(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "validate-esports":
            output = cmd_validate_esports(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "esports-forecast":
            output = cmd_esports_forecast(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "international-baseball-backfill":
            output = cmd_international_baseball_backfill(
                args, config, registry, bans, ledger, audit, data_root
            )
        elif args.command == "validate-international-baseball":
            output = cmd_validate_international_baseball(
                args, config, registry, bans, ledger, audit, data_root
            )
        elif args.command == "international-baseball-forecast":
            output = cmd_international_baseball_forecast(
                args, config, registry, bans, ledger, audit, data_root
            )
        elif args.command == "bootstrap-entities":
            output = cmd_bootstrap_entities(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "features":
            output = cmd_features(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "backtest":
            output = cmd_backtest(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "validate-models":
            output = cmd_validate_models(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "validate-totals":
            output = cmd_validate_totals(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "reconstruct-mlb-markets":
            output = cmd_reconstruct_mlb_markets(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "refresh-mlb-baselines":
            output = cmd_refresh_mlb_baselines(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "train-residual":
            output = cmd_train_residual(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "execute":
            output = cmd_execute(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "sell-position":
            output = cmd_sell_position(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "exposure":
            output = cmd_exposure(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "ban-team":
            output = cmd_ban_team(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "collect-scores":
            output = cmd_collect_scores(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "verify-checklist":
            output = cmd_verify_checklist(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "verify-chain":
            output = cmd_verify_chain(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "score-research":
            output = cmd_score_research(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "call":
            output = cmd_call(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "update-closing":
            output = cmd_update_closing(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "void":
            output = cmd_void(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "review-loss":
            output = cmd_review_loss(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "freeze-production":
            output = cmd_freeze_production(args, config, registry, bans, ledger, audit, data_root)
        elif args.command == "compare-champion":
            output = cmd_compare_champion(args, config, registry, bans, ledger, audit, data_root)
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
