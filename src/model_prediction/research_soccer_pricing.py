"""One explicitly selected soccer 90-minute Yes/No contract per forecast.

The three-way distribution is retained: No on a team-win contract includes a
draw. A draw contract pays Yes only for the draw, never half value. Target is
declared in the frozen fixture (default home win), before any price selection.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from zoneinfo import ZoneInfo

from .data_sources.polymarket_us import LEAGUE_SLUGS
from .research_forward import aware_time
from .research_incumbent_evaluation import POLICY
from .research_quote_contracts import number


def identity(name: str) -> str:
    words = re.findall(
        r"[a-z0-9]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    )
    return " ".join(w for w in words if w not in {"fc", "sc", "cf"})


def participants(prediction: dict[str, Any], event: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fixture = prediction["raw_fixture"]
    if len(event["teams"]) != 2:
        raise ValueError("soccer_pricing_ambiguous_participants")
    mapped = {}
    for side in ("home", "away"):
        matches = [t for t in event["teams"] if identity(t["name"]) == identity(fixture[f"{side}_team"])]
        if (
            len(matches) != 1
            or not matches[0].get("id")
            or matches[0]["league"] != LEAGUE_SLUGS[fixture["league"]]
        ):
            raise ValueError("soccer_pricing_participant_mismatch")
        mapped[side] = matches[0]
    if mapped["home"]["id"] == mapped["away"]["id"]:
        raise ValueError("soccer_pricing_ambiguous_participants")
    return mapped


def matching_event(prediction: dict[str, Any], event: dict[str, Any]) -> bool:
    fixture = prediction["raw_fixture"]
    try:
        participants(prediction, event)
        return aware_time(event["startTime"]) == aware_time(fixture["event_start_utc"]) and bool(
            re.fullmatch(
                rf"{LEAGUE_SLUGS[fixture['league']]}-[a-z0-9]+-[a-z0-9]+-\d{{4}}-\d{{2}}-\d{{2}}",
                event["slug"],
            )
        )
    except (KeyError, ValueError, TypeError):
        return False


def target(prediction: dict[str, Any], event: dict[str, Any]) -> tuple[str, str]:
    requested = prediction["raw_fixture"].get("pricing_target", "home_win")
    mapped = participants(prediction, event)
    if requested == "draw":
        return "draw", "draw"
    if requested not in {"home_win", "away_win"}:
        raise ValueError("soccer_pricing_invalid_target")
    side = requested.removesuffix("_win")
    return ("a_win" if side == "home" else "b_win"), mapped[side]["abbreviation"].lower()


def matching_market(prediction: dict[str, Any], event: dict[str, Any], market: dict[str, Any]) -> bool:
    _, suffix = target(prediction, event)
    return (
        market.get("sportsMarketType") == "soccer_team_full_time_winner"
        and market.get("sportsMarketTypeV2") == "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME"
        and market.get("slug") == f"atc-{event['slug']}-{suffix}"
    )


def contracts_for_prediction(
    prediction: dict[str, Any], quote: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    from .research_pricing import _levels

    if prediction["sport"] != "SOCCER" or prediction["market"] != "moneyline":
        raise ValueError("soccer_pricing_adapter_unavailable")
    if prediction["raw_fixture"].get("period") != "regulation_90_minutes" or prediction["line"] is not None:
        raise ValueError("soccer_pricing_regulation_scope_unverified")
    probabilities = prediction["probabilities"]
    if set(probabilities) != {"a_win", "draw", "b_win"}:
        raise ValueError("soccer_pricing_three_way_distribution_required")
    e, m, b = (quote[k] for k in ("event", "market", "book"))
    start = aware_time(prediction["raw_fixture"]["event_start_utc"])
    predicted, requested, observed, decision, transact = (
        aware_time(t)
        for t in (
            prediction["observed_at_utc"],
            quote["request_started_at_utc"],
            quote["observed_at_utc"],
            quote["pricing_decision_at_utc"],
            b["transactTime"],
        )
    )
    if (
        quote["provider"] != "polymarket_us_public_gateway"
        or not aware_time(POLICY["fee_effective_utc"])
        <= predicted
        <= requested
        <= observed
        <= decision
        < start
        or transact > observed
        or (decision - transact).total_seconds() > POLICY["maximum_quote_age_seconds"]
        or (decision - requested).total_seconds() > POLICY["maximum_quote_age_seconds"]
    ):
        raise ValueError("soccer_pricing_chronology_or_staleness")
    if not matching_event(prediction, e) or not matching_market(prediction, e, m):
        raise ValueError("soccer_pricing_market_or_event_identity")
    if (
        not e.get("id")
        or not m.get("id")
        or aware_time(m["gameStartTime"]) != start
        or b["marketSlug"] != m["slug"]
    ):
        raise ValueError("soccer_pricing_market_book_identity")
    if (
        e.get("active") is not True
        or e.get("closed") is not False
        or m.get("active") is not True
        or m.get("closed") is not False
        or m.get("archived") is not False
        or m.get("status") != "MARKET_STATUS_OPEN"
        or b.get("state") != "MARKET_STATE_OPEN"
        or number(m["feeCoefficient"]) != number(POLICY["fee_rate"])
        or not 0 < number(m["minimumTradeQty"]) <= 1
        or m.get("line") is not None
    ):
        raise ValueError("soccer_pricing_closed_or_unsupported_fee_or_quantity")
    listed = [x for x in e["markets"] if x.get("slug") == m["slug"] or x.get("id") == m["id"]]
    if len(listed) != 1 or any(
        listed[0].get(k) != m.get(k)
        for k in (
            "id",
            "slug",
            "gameStartTime",
            "line",
            "sportsMarketType",
            "sportsMarketTypeV2",
            "description",
            "question",
        )
    ):
        raise ValueError("soccer_pricing_detail_market_mismatch")
    long_win, _ = target(prediction, e)
    mapped = participants(prediction, e)
    sides = m["marketSides"]
    if (
        len(sides) != 2
        or len({str(s["id"]) for s in sides}) != 2
        or {s["description"] for s in sides} != {"Yes", "No"}
    ):
        raise ValueError("soccer_pricing_side_mapping")
    selected = None if long_win == "draw" else mapped["home" if long_win == "a_win" else "away"]
    for side in sides:
        if (
            side.get("long") is not (side["description"] == "Yes")
            or side.get("tradable") is not True
            or str(side["marketId"]) != str(m["id"])
            or side["identifier"] != m["slug"]
            or not side.get("id")
        ):
            raise ValueError("soccer_pricing_side_mapping")
        team = side.get("team")
        if (selected is None and team) or (
            selected is not None
            and (not team or str(team["id"]) != str(selected["id"]) or team["name"] != selected["name"])
        ):
            raise ValueError("soccer_pricing_selected_team_mismatch")
    found = re.search(r"Outcome sourced from ([^.]+)\.$", m["description"])
    if found is None:
        raise ValueError("soccer_pricing_missing_rule_source")
    league_name = found.group(1)
    local = start.astimezone(ZoneInfo("America/New_York"))
    if not e["slug"].endswith(local.date().isoformat()):
        raise ValueError("soccer_pricing_event_date_mismatch")
    names = [t["name"] for t in e["teams"]]
    pair = f"{names[0]} vs {names[1]}"
    scheduled = f"{local.date().isoformat()} {local.strftime('%I:%M%p').lstrip('0')} ET"
    expected_rule = (
        f"This market will settle to the winner at the end of 90 minutes plus stoppage time in the {pair} {league_name} "
        f"match scheduled for {scheduled}. If the match is tied following 90 minutes plus stoppage time, the market will settle to Tie. "
        "If the match is delayed, postponed, or suspended and not rescheduled to a date within two weeks of the originally scheduled "
        f"date, the market will settle to the last fair market price. Outcome sourced from {league_name}."
    )
    date_text = f"{local.strftime('%b')} {local.day}, {local.year}"
    if selected is None:
        expected_question = f"Will the {league_name} match {pair} scheduled for {date_text} end in a draw?"
    else:
        opponent = next(t["name"] for t in e["teams"] if t["id"] != selected["id"])
        expected_question = f"Will {selected['name']} win against {opponent} in the {league_name} match scheduled for {date_text}?"
    if m["description"] != expected_rule or m["question"] != expected_question:
        raise ValueError("soccer_pricing_settlement_terms_unverified")
    bid, bid_size = _levels(b, "bids")
    ask, ask_size = _levels(b, "offers")
    if bid > ask:
        raise ValueError("soccer_pricing_crossed_book")
    return [
        {
            "market_id": str(m["id"]),
            "side": direction,
            "ask": price,
            "ask_size": size,
            "settlement_values": {
                label: int((label == long_win) is (direction == "long")) for label in sorted(probabilities)
            },
        }
        for direction, price, size in (("long", ask, ask_size), ("short", round(1 - bid, 12), bid_size))
    ], probabilities
