"""Operational adapter for the existing NCAAB tempo model."""

from __future__ import annotations

from datetime import date

from .config import PROJECT_ROOT
from .data_sources.espn import ESPNClient
from .data_sources.polymarket_us import PolymarketUSClient, probability_to_american
from .domain import League, MarketType, ModelOrigin, ModelState, PickRequest, parse_utc, utc_now
from .eligibility import EligibilityResult, RecordType
from .entities import CanonicalTeam
from .features.base import FeatureStore
from .hockey_forward import _parse_teams_from_event, _team_matches
from .ledger import DuplicatePickError
from .models.basketball import UpcomingGame
from .models.basketball_leagues import ncaab_model
from .production_registry import ProductionModelRegistry


def forecast_ncaab_sport(
    *,
    data_root,
    args_date,
    config,
    research_ledger=None,
    gated_ledger=None,
    flat_ledger=None,
    main_ledger=None,
):
    board = ESPNClient().scoreboard("NCAAB", args_date)
    result = {
        "sport": "ncaab",
        "model_version": "ncaab-tempo-v1",
        "scheduled_games": len(board.get("events", [])),
        "logged": 0,
        "flat_logged": 0,
        "main_logged": 0,
        "research_ledger_logged": 0,
        "gated_ledger_logged": 0,
        "skipped": [],
        "errors": [],
    }
    if not result["scheduled_games"]:
        result["status"] = "no_scheduled_events"
        return result
    history = FeatureStore(data_root).games_before("ncaab", args_date)
    if not history:
        result.update(status="data_unavailable", reason="no_completed_ncaab_history")
        return result
    events = PolymarketUSClient().slate("NCAAB", date.fromisoformat(args_date))
    model, observed = ncaab_model(), utc_now()
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    for market in ("moneyline", "spread", "total"):
        champion = registry.champion("NCAAB", market)
        if champion is None or not champion.available or champion.model_id != model.version:
            raise ValueError(f"NCAAB {market} champion does not match executable model {model.version}")
    policy = config.get("models", {}).get("NCAAB", {})
    names = {name for game in history for name in (game.home_team, game.away_team)}
    for event in events:
        identity = event.get("event_id") or event.get("id")
        start = event.get("event_start_utc")
        if not identity or not start or parse_utc(start) <= observed:
            result["skipped"].append({"event_id": identity, "reason": "missing_identity_or_event_started"})
            continue
        moneyline_markets = [m for m in event.get("markets", []) if m.get("market_type") == "moneyline"]
        if not moneyline_markets:
            result["skipped"].append({"event_id": identity, "reason": "moneyline_team_identity_unavailable"})
            continue
        away, home = _parse_teams_from_event({**event, "markets": moneyline_markets})
        away_matches = [name for name in names if _team_matches(away, name)]
        home_matches = [name for name in names if _team_matches(home, name)]
        if len(away_matches) != 1 or len(home_matches) != 1:
            result["skipped"].append({"event_id": identity, "reason": "team_history_unmatched"})
            continue
        away, home = away_matches[0], home_matches[0]
        for market in event.get("markets", []):
            kind = market.get("market_type")
            if kind not in {"moneyline", "spread", "total"}:
                continue
            sides = market.get("sides", [])
            away_side = next((s for s in sides if s.get("selection") == "away"), {})
            line = market.get("line")
            if kind == "spread" and away_side.get("line") is None:
                continue
            if kind == "total" and line is None:
                continue
            upcoming = UpcomingGame(
                str(identity),
                start,
                away,
                home,
                spread_away_line=away_side.get("line") if kind == "spread" else None,
                total_line=line if kind == "total" else None,
            )
            prediction = next(
                (p for p in model.predict_games(history, [upcoming]) if p.market_type == kind), None
            )
            if prediction is None:
                continue
            selections = {"over", "under"} if kind == "total" else {"home", "away"}
            valid_sides = [
                side
                for side in sides
                if side.get("selection") in selections
                and side.get("price_probability") is not None
                and 0 < float(side["price_probability"]) < 1
            ]
            if not valid_sides:
                continue
            best_side = max(
                valid_sides,
                key=lambda side: (
                    prediction.probabilities[side["selection"]] - float(side["price_probability"])
                ),
            )
            for side in (best_side,):
                selection, ask = side["selection"], side["price_probability"]
                probability = prediction.probabilities[selection]
                edge = probability - float(ask)
                request = PickRequest(
                    event_start_utc=start,
                    event_id=str(identity),
                    league=League.NCAAB,
                    away_team=away,
                    home_team=home,
                    market_type=MarketType(kind),
                    selection=selection,
                    line=(side.get("line") if side.get("line") is not None else line)
                    if kind != "moneyline"
                    else None,
                    sportsbook="model_opinion_indicative_market",
                    american_odds=probability_to_american(float(ask)),
                    model_probability=probability,
                    model_uncertainty=prediction.uncertainty,
                    model_version=model.version,
                    rationale=prediction.rationale,
                    risks="Paper tempo model; live market quote is an indicative reference, not verified executable BBO.",
                    model_origin=ModelOrigin.STATISTICAL_MODEL,
                    model_state=ModelState.SHADOW_QUALIFIED,
                    observed_at_utc=observed.isoformat(),
                )
                teams = [
                    CanonicalTeam(name, League.NCAAB, name, name[:3], True, None, None, ())
                    for name in (away, home)
                ]
                eligibility = EligibilityResult(
                    RecordType.RESEARCH_OBSERVATION,
                    "CALL",
                    "PAPER_CALL_MODEL_UNVALIDATED",
                    1.0,
                    round(probability * 100),
                    edge,
                    edge,
                    *teams,
                )
                destinations = (
                    ("flat_logged", flat_ledger, True),
                    ("research_ledger_logged", research_ledger, edge >= policy.get("min_edge", 0.02)),
                    ("gated_ledger_logged", gated_ledger, edge >= policy.get("min_edge", 0.02)),
                    ("main_logged", main_ledger, edge >= policy.get("min_edge", 0.02)),
                )
                for counter, destination, eligible in destinations:
                    if destination is None or not eligible:
                        continue
                    try:
                        destination.append_evaluated(request, eligibility, now=observed)
                        result[counter] += 1
                    except DuplicatePickError:
                        pass
                    except (ValueError, OSError) as exc:
                        result["errors"].append({"event_id": identity, "ledger": counter, "reason": str(exc)})
    result["logged"] = result["flat_logged"] or result["main_logged"]
    result["status"] = "error" if result["errors"] else "ok"
    return result
