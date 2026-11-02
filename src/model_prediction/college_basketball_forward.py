"""Operational adapter for the existing NCAAB tempo model."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from .config import PROJECT_ROOT
from .data_sources.espn import ESPNClient
from .data_sources.polymarket_us import PolymarketUSClient, probability_to_american
from .domain import League, MarketType, ModelOrigin, ModelState, PickRequest, parse_utc, utc_now
from .eligibility import EligibilityResult, RecordType
from .entities import CanonicalTeam
from .features.base import FeatureStore
from .ledger import DuplicatePickError, defer_sqlite_xlsx_exports
from .models.basketball import UpcomingGame
from .models.basketball_leagues import ncaab_v2_model
from .production_registry import ProductionModelRegistry

ncaab_model = ncaab_v2_model


def _normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("utf-8")
    s = s.lower().replace("&", "and").replace(".", "").replace("'", "").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


_NCAAB_ALIASES: dict[str, str] = {
    "miami (fl)": "miami hurricanes",
    "miami fl": "miami hurricanes",
    "miami": "miami hurricanes",
    "florida": "florida gators",
    "north carolina": "north carolina tar heels",
    "penn state": "penn state nittany lions",
    "illinois": "illinois fighting illini",
    "oklahoma": "oklahoma sooners",
    "minnesota": "minnesota golden gophers",
    "north dakota": "north dakota fighting hawks",
    "louisiana": "louisiana ragin cajuns",
    "la tech": "louisiana tech bulldogs",
    "louisiana tech": "louisiana tech bulldogs",
    "louisiana monroe": "ul monroe warhawks",
    "ul monroe": "ul monroe warhawks",
    "maryland eastern shore": "maryland eastern shore hawks",
    "fdu": "fairleigh dickinson knights",
    "usc upstate": "south carolina upstate spartans",
    "central connecticut state": "central connecticut blue devils",
    "central connecticut": "central connecticut blue devils",
    "maine": "maine black bears",
    "st thomas (mn)": "st thomas tommies",
    "st thomas mn": "st thomas tommies",
    "saint thomas": "st thomas tommies",
    "uconn": "connecticut",
    "nc state": "nc state wolfpack",
    "penn": "pennsylvania quakers",
    "ole miss": "ole miss rebels",
    "pitt": "pittsburgh panthers",
    "pittsburgh": "pittsburgh panthers",
    "ucf": "ucf knights",
    "usf": "south florida bulls",
    "byu": "byu cougars",
    "smu": "smu mustangs",
    "tcu": "tcu horned frogs",
    "lsu": "lsu tigers",
    "vcu": "vcu rams",
    "unc": "north carolina tar heels",
    "unlv": "unlv rebels",
    "utep": "utep miners",
    "utsa": "utsa roadrunners",
    "umass": "umass minutemen",
    "liu": "long island university sharks",
}


def _resolve_ncaab_team(
    school: str,
    mascot: str,
    names: set[str],
    normalized_names: dict[str, str],
) -> str:
    norm_school = _normalize_name(school)
    norm_mascot = _normalize_name(mascot) if mascot and not any(c in mascot for c in "+-0123456789") else ""

    if norm_school in _NCAAB_ALIASES:
        target = _NCAAB_ALIASES[norm_school]
        for k, v in normalized_names.items():
            if k == target or k.startswith(target + " ") or target in k:
                return v

    if norm_mascot:
        combined = f"{norm_school} {norm_mascot}"
        if combined in normalized_names:
            return normalized_names[combined]
        for k, v in normalized_names.items():
            if k == combined:
                return v

    exact_prefix = [v for k, v in normalized_names.items() if k.startswith(norm_school + " ")]
    if len(exact_prefix) == 1:
        return exact_prefix[0]

    if norm_mascot:
        mascot_cands = [
            v for k, v in normalized_names.items() if k.endswith(" " + norm_mascot) and norm_school in k
        ]
        if len(mascot_cands) == 1:
            return mascot_cands[0]

    if norm_school.startswith("saint "):
        st_variant = "st " + norm_school[6:]
        cands = [v for k, v in normalized_names.items() if k.startswith(st_variant + " ")]
        if len(cands) == 1:
            return cands[0]
    elif norm_school.startswith("st "):
        st_variant = "saint " + norm_school[3:]
        cands = [v for k, v in normalized_names.items() if k.startswith(st_variant + " ")]
        if len(cands) == 1:
            return cands[0]

    school_tokens = set(norm_school.split())
    ranked = []
    for k, v in normalized_names.items():
        k_tokens = set(k.split())
        if school_tokens.issubset(k_tokens):
            diff = len(k_tokens) - len(school_tokens)
            ranked.append((diff, v))
    ranked.sort(key=lambda x: x[0])
    if ranked and (len(ranked) == 1 or ranked[0][0] < ranked[1][0]):
        return ranked[0][1]

    if school in names:
        return school
    combined_raw = f"{school} {mascot}".strip() if norm_mascot else school
    if combined_raw in names:
        return combined_raw

    return combined_raw


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
        "model_version": "ncaab-tempo-v2",
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
    normalized_names = {_normalize_name(name): name for name in names}

    planned: list[
        tuple[dict[str, Any], dict[str, Any], str, float | None, list[dict[str, Any]], str, str, str, str]
    ] = []
    upcoming_batch: list[UpcomingGame] = []

    for event in events:
        identity = event.get("event_id") or event.get("id")
        start = event.get("event_start_utc")
        if not identity or not start or parse_utc(start) <= observed:
            result["skipped"].append({"event_id": identity, "reason": "missing_identity_or_event_started"})
            continue

        away_school = None
        home_school = None
        away_mascot = ""
        home_mascot = ""

        for m in event.get("markets", []):
            sides = m.get("sides", [])
            a_side = next((s for s in sides if s.get("selection") == "away"), None)
            h_side = next((s for s in sides if s.get("selection") == "home"), None)
            if a_side and h_side:
                if a_side.get("team") and h_side.get("team"):
                    away_school = str(a_side["team"])
                    home_school = str(h_side["team"])
                    desc_a = str(a_side.get("description", ""))
                    desc_h = str(h_side.get("description", ""))
                    if desc_a and not any(c in desc_a for c in "+-0123456789"):
                        away_mascot = desc_a
                    if desc_h and not any(c in desc_h for c in "+-0123456789"):
                        home_mascot = desc_h
                    break
                if a_side.get("description") and h_side.get("description"):
                    desc_a = str(a_side["description"])
                    desc_h = str(h_side["description"])
                    if not any(c in desc_a for c in "+-0123456789") and not any(
                        c in desc_h for c in "+-0123456789"
                    ):
                        away_school = desc_a
                        home_school = desc_h
                        break

        if not away_school:
            title = event.get("title", "")
            for sep in (" vs. ", " vs ", " at ", " @ "):
                if sep in title:
                    parts = title.split(sep)
                    if len(parts) == 2:
                        away_school = parts[0].strip()
                        home_school = parts[1].strip()
                        break

        if not away_school or not home_school:
            result["skipped"].append({"event_id": identity, "reason": "team_identity_unavailable"})
            continue

        away = _resolve_ncaab_team(away_school, away_mascot, names, normalized_names)
        home = _resolve_ncaab_team(home_school, home_mascot, names, normalized_names)

        for market in _main_line_markets(event.get("markets", [])):
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

            game_key = f"{identity}_{len(upcoming_batch)}"
            upcoming = UpcomingGame(
                str(game_key),
                start,
                away,
                home,
                spread_away_line=away_side.get("line") if kind == "spread" else None,
                total_line=line if kind == "total" else None,
            )
            upcoming_batch.append(upcoming)
            planned.append((event, market, kind, line, sides, away, home, start, str(identity), game_key))

    if not upcoming_batch:
        result["logged"] = 0
        result["status"] = "ok"
        return result

    predictions = model.predict_games(history, upcoming_batch)
    if predictions and all(hasattr(p, "event_id") for p in predictions):
        pred_lookup = {(p.event_id, p.market_type): p for p in predictions}
    else:
        pred_lookup = {
            (planned[i][9], getattr(p, "market_type", planned[i][2])): p
            for i, p in enumerate(predictions)
            if i < len(planned)
        }

    with defer_sqlite_xlsx_exports():
        for event, market, kind, line, sides, away, home, start, identity, game_key in planned:
            prediction = pred_lookup.get((game_key, kind))
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
                    event_id=identity,
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
                dynamic_units = (
                    getattr(model, "compute_dynamic_units", None)
                    and model.compute_dynamic_units(
                        edge, min_edge=policy.get("min_edge", 0.02), max_edge=0.08
                    )
                ) or 1.00
                eligibility = EligibilityResult(
                    RecordType.RESEARCH_OBSERVATION,
                    "CALL",
                    "PAPER_CALL_MODEL_UNVALIDATED",
                    dynamic_units,
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
