"""EuroLeague lane: results from the official live API, tempo model, Polymarket prices.

Prices moneyline, full-game spread and full-game total with the dedicated
EuroLeague tempo model (``eurolg-tempo-v1``). The lane is unverified: rows go to
the flat ledger always and to the main ledger only when
``models.EUROLEAGUE.main_ledger_enabled`` is set.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .config import PROJECT_ROOT
from .data_sources.euroleague import club_names, refresh_euroleague_history, resolve_club
from .data_sources.polymarket_us import PolymarketUSClient, probability_to_american
from .domain import League, MarketType, ModelOrigin, ModelState, PickRequest, parse_utc, utc_now
from .eligibility import EligibilityResult, RecordType
from .entities import CanonicalTeam
from .features.base import FeatureStore
from .hockey_forward import _parse_teams_from_event
from .ledger import DuplicatePickError
from .models.basketball import UpcomingGame
from .models.basketball_leagues import euroleague_model
from .production_registry import ProductionModelRegistry

MARKETS = ("moneyline", "spread", "total")


def _balance(market: dict[str, Any]) -> float:
    prices = [
        float(s["price_probability"])
        for s in market.get("sides", [])
        if s.get("price_probability") is not None
    ]
    return max((abs(p - 0.5) for p in prices), default=1.0)


def _main_line_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Moneyline plus, per spread/total, only the most balanced (main) line.

    Alternate lines are the same opinion repeated and would multiply correlated
    rows in the ledger.
    """
    kept = [m for m in markets if m.get("market_type") == "moneyline"]
    for kind in ("spread", "total"):
        candidates = [m for m in markets if m.get("market_type") == kind and len(m.get("sides", [])) == 2]
        if candidates:
            kept.append(min(candidates, key=_balance))
    return kept


@dataclass(frozen=True)
class BasketballLaneSpec:
    """One non-NBA basketball league: where its history comes from and how names resolve."""

    key: str  # processed/<key>/games.jsonl, ledger sport key, CLI name
    code: str  # Polymarket league code, registry sport, config key
    league: League
    model_factory: Callable[[], Any]
    refresh: Callable[..., dict]
    resolver: Callable[[str, str | Path, set[str]], str | None]


def _euroleague_resolver(name: str, data_root: str | Path, known: set[str]) -> str | None:
    resolved = resolve_club(name, club_names(data_root))
    return resolved if resolved in known else None


def forecast_euroleague_sport(*, data_root, args_date, config, history_client=None, **ledgers):
    spec = BasketballLaneSpec(
        "euroleague",
        "EUROLEAGUE",
        League.EUROLEAGUE,
        euroleague_model,
        refresh_euroleague_history,
        _euroleague_resolver,
    )
    return forecast_basketball_lane(
        spec,
        data_root=data_root,
        args_date=args_date,
        config=config,
        history_client=history_client,
        **ledgers,
    )


def forecast_basketball_lane(
    spec: BasketballLaneSpec,
    *,
    data_root,
    args_date,
    config,
    research_ledger=None,
    gated_ledger=None,
    flat_ledger=None,
    main_ledger=None,
    history_client=None,
):
    result: dict[str, Any] = {
        "sport": spec.key,
        "logged": 0,
        "flat_logged": 0,
        "main_logged": 0,
        "research_ledger_logged": 0,
        "gated_ledger_logged": 0,
        "skipped": [],
        "errors": [],
    }
    try:
        result["history_refresh"] = spec.refresh(data_root, args_date, client=history_client)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        result["history_refresh"] = {"status": "error", "reason": str(exc)}
    history = FeatureStore(data_root).games_before(spec.key, args_date)
    if not history:
        result.update(status="data_unavailable", reason=f"no_completed_{spec.key}_history")
        return result
    events = PolymarketUSClient().slate(spec.code, date.fromisoformat(args_date))
    result["scheduled_games"] = len(events)
    if not events:
        result["status"] = "no_scheduled_events"
        return result
    model, observed = spec.model_factory(), utc_now()
    result["model_version"] = model.version
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    for market in MARKETS:
        champion = registry.champion(spec.code, market)
        if champion is None or not champion.available or champion.model_id != model.version:
            raise ValueError(f"{spec.code} {market} champion does not match executable model {model.version}")
    policy = config.get("models", {}).get(spec.code, {})
    min_edge = float(policy.get("min_edge", 0.035))
    main_enabled = bool(policy.get("main_ledger_enabled", False))
    min_team_games = int(policy.get("min_team_games", 8))
    games_by_team: dict[str, int] = {}
    for game in history:
        for name in (game.home_team, game.away_team):
            games_by_team[name] = games_by_team.get(name, 0) + 1
    known = set(games_by_team)
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
        away_name, home_name = away, home
        away, home = spec.resolver(away_name, data_root, known), spec.resolver(home_name, data_root, known)
        if away is None or home is None:
            result["skipped"].append(
                {"event_id": identity, "reason": "team_history_unmatched", "teams": [away_name, home_name]}
            )
            continue
        if min(games_by_team[away], games_by_team[home]) < min_team_games:
            result["skipped"].append({"event_id": identity, "reason": "insufficient_team_history"})
            continue
        for market in _main_line_markets(event.get("markets", [])):
            kind = market.get("market_type")
            if kind not in MARKETS:
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
            side = max(
                valid_sides,
                key=lambda s: prediction.probabilities[s["selection"]] - float(s["price_probability"]),
            )
            selection, ask = side["selection"], float(side["price_probability"])
            probability = prediction.probabilities[selection]
            edge = probability - ask
            request = PickRequest(
                event_start_utc=start,
                event_id=str(identity),
                league=spec.league,
                away_team=away,
                home_team=home,
                market_type=MarketType(kind),
                selection=selection,
                line=(side.get("line") if side.get("line") is not None else line)
                if kind != "moneyline"
                else None,
                sportsbook="model_opinion_indicative_market",
                american_odds=probability_to_american(ask),
                model_probability=probability,
                model_uncertainty=prediction.uncertainty,
                model_version=model.version,
                rationale=f"{prediction.rationale} ({market.get('market_slug', '')}).",
                risks="Paper tempo model; live market quote is an indicative reference, not verified executable BBO.",
                model_origin=ModelOrigin.STATISTICAL_MODEL,
                model_state=ModelState.SHADOW_QUALIFIED,
                observed_at_utc=observed.isoformat(),
            )
            teams = [
                CanonicalTeam(name, spec.league, name, name[:3], True, None, None, ())
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
                ("research_ledger_logged", research_ledger, edge >= min_edge),
                ("gated_ledger_logged", gated_ledger, edge >= min_edge),
                ("main_logged", main_ledger, main_enabled and edge >= min_edge),
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
