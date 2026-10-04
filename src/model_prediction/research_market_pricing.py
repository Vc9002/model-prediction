"""Raw gateway settlement adapters for CFB and draw-capable baseball.

Rules are checked against complete provider descriptions, separately from the
question, side labels and line. Conflicting provider fields are not reconciled
by guessing which one is authoritative.
"""

from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo

from .data_sources.cfb_data import resolve_team
from .international_baseball import KBO_TEAMS, NPB_TEAMS
from .research_forward import aware_time
from .research_incumbent_evaluation import POLICY
from .research_quote_contracts import number

LEAGUES = {"NCAAF": "cfb", "KBO": "kbo", "NPB": "npb", "WNBA": "wnba"}
TYPES = {
    ("NCAAF", "moneyline"): "football_team_full_game_winner",
    ("NCAAF", "spread"): "football_team_full_game_spread",
    ("NCAAF", "total"): "football_team_full_game_total",
    ("KBO", "moneyline"): "baseball_team_full_game_winner",
    ("NPB", "moneyline"): "baseball_team_full_game_winner",
    ("SOCCER", "moneyline"): "soccer_team_full_time_winner",
    ("WNBA", "spread"): "basketball_team_full_game_spread",
    ("WNBA", "total"): "basketball_team_full_game_total",
}


def team_identity(sport: str, team: dict[str, Any]) -> str:
    if sport == "WNBA":
        name, alias = str(team["name"]).strip(), str(team["alias"]).strip()
        if not name or not alias:
            raise ValueError("pricing_wnba_team_identity_unavailable")
        return name if name.endswith(alias) else f"{name} {alias}"
    if sport == "NCAAF":
        found = resolve_team(team["safeName"])
        if found is None:
            raise ValueError("pricing_unknown_cfb_team")
        return found.canonical_name
    teams = KBO_TEAMS if sport == "KBO" else NPB_TEAMS
    candidates = [
        key
        for key, value in teams.items()
        if team["name"].casefold()
        in {str(name).casefold() for name in (value["name"], *value.get("aliases", ()))}
    ]
    if len(candidates) != 1:
        raise ValueError("pricing_unknown_or_ambiguous_baseball_team")
    return candidates[0]


def participant_map(prediction: dict[str, Any], event: dict[str, Any]) -> dict[str, str]:
    sport, fixture = prediction["sport"], prediction["raw_fixture"]
    if sport not in LEAGUES or len(event["teams"]) != 2:
        raise ValueError("pricing_unsupported_event")
    mapped = {}
    for team in event["teams"]:
        if team["league"] != LEAGUES[sport] or not team.get("id"):
            raise ValueError("pricing_provider_team_identity")
        identity = team_identity(sport, team)
        field = "team" if sport == "WNBA" else "team_id"
        sides = [side for side in ("home", "away") if str(fixture[f"{side}_{field}"]) == identity]
        if len(sides) != 1 or str(team["id"]) in mapped:
            raise ValueError("pricing_forecast_participant_mismatch")
        mapped[str(team["id"])] = sides[0]
    if set(mapped.values()) != {"home", "away"}:
        raise ValueError("pricing_ambiguous_participants")
    return mapped


def matching_event(prediction: dict[str, Any], event: dict[str, Any]) -> bool:
    if prediction["sport"] == "SOCCER":
        from .research_soccer_pricing import matching_event as soccer_event

        return soccer_event(prediction, event)
    if aware_time(event["startTime"]) != aware_time(prediction["raw_fixture"]["event_start_utc"]):
        return False
    try:
        participant_map(prediction, event)
    except (KeyError, ValueError, TypeError):
        return False
    return bool(
        re.fullmatch(
            rf"{LEAGUES[prediction['sport']]}-[a-z0-9]+-[a-z0-9]+-\d{{4}}-\d{{2}}-\d{{2}}", event["slug"]
        )
    )


def matching_market(prediction: dict[str, Any], event: dict[str, Any], market: dict[str, Any]) -> bool:
    if prediction["sport"] == "SOCCER":
        from .research_soccer_pricing import matching_market as soccer_market

        return soccer_market(prediction, event, market)
    sport, kind = prediction["sport"], prediction["market"]
    if market.get("sportsMarketType") != TYPES.get((sport, kind)):
        return False
    if kind == "moneyline":
        return market["slug"] == f"aec-{event['slug']}" and market.get("line") is None
    if kind == "total":
        return number(market["line"]) == number(prediction["line"])
    mapped = participant_map(prediction, event)
    long = [s for s in market["marketSides"] if s.get("long") is True]
    if len(long) != 1:
        return False
    side = mapped[str(long[0]["team"]["id"])]
    return number(market["line"]) * (1 if side == "home" else -1) == number(prediction["line"])


def contracts_for_prediction(
    prediction: dict[str, Any], quote: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    if prediction["sport"] == "SOCCER":
        from .research_soccer_pricing import contracts_for_prediction as soccer_contracts

        return soccer_contracts(prediction, quote)
    from .research_pricing import _levels

    sport, kind = prediction["sport"], prediction["market"]
    if (sport, kind) not in TYPES:
        raise ValueError("pricing_adapter_unavailable")
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
        raise ValueError("pricing_quote_chronology_or_staleness")
    if not matching_event(prediction, e) or not matching_market(prediction, e, m):
        raise ValueError("pricing_market_or_event_identity")
    if (
        aware_time(m["gameStartTime"]) != start
        or b["marketSlug"] != m["slug"]
        or not e.get("id")
        or not m.get("id")
    ):
        raise ValueError("pricing_market_book_identity")
    listed = [x for x in e["markets"] if x.get("id") == m["id"] or x.get("slug") == m["slug"]]
    if len(listed) != 1 or any(
        listed[0].get(k) != m.get(k)
        for k in (
            "id",
            "slug",
            "sportsMarketType",
            "sportsMarketTypeV2",
            "gameStartTime",
            "line",
            "description",
            "question",
        )
    ):
        raise ValueError("pricing_detail_market_mismatch")
    if (
        m.get("active") is not True
        or m.get("closed") is not False
        or m.get("archived") is not False
        or e.get("active") is not True
        or e.get("closed") is not False
        or m.get("status") != "MARKET_STATUS_OPEN"
        or b.get("state") != "MARKET_STATE_OPEN"
        or number(m["feeCoefficient"]) != number(POLICY["fee_rate"])
        or not 0 < number(m["minimumTradeQty"]) <= 1
    ):
        raise ValueError("pricing_market_closed_or_unsupported_fee_or_quantity")
    if m["sportsMarketTypeV2"] != f"SPORTS_MARKET_TYPE_{kind.upper()}":
        raise ValueError("pricing_market_type_mismatch")
    sides = m["marketSides"]
    if len(sides) != 2 or len({str(s["id"]) for s in sides}) != 2:
        raise ValueError("pricing_side_identity")
    by_direction = {}
    for s in sides:
        if (
            not isinstance(s.get("long"), bool)
            or s["long"] in by_direction
            or not s.get("id")
            or str(s["marketId"]) != str(m["id"])
            or s["identifier"] != m["slug"]
            or s.get("tradable") is not True
        ):
            raise ValueError("pricing_side_identity")
        by_direction[s["long"]] = s
    mapped = participant_map(prediction, e)
    teams = e["teams"]
    rule_names = [t["safeName"] if sport in {"NCAAF", "WNBA"} else t["name"] for t in teams]
    pairing = f"{rule_names[0]} vs {rule_names[1]}"
    timezone = (
        "America/New_York" if sport in {"NCAAF", "WNBA"} else "Asia/Seoul" if sport == "KBO" else "Asia/Tokyo"
    )
    day = start.astimezone(ZoneInfo(timezone)).date()
    if not e["slug"].endswith(day.isoformat()):
        raise ValueError("pricing_event_date_mismatch")
    date_text = f"{day.strftime('%b')} {day.day}, {day.year}"
    game = f"{pairing} {'College Football' if sport == 'NCAAF' else sport} game scheduled for {date_text}. "
    if sport == "WNBA":
        local = start.astimezone(ZoneInfo(timezone))
        game = f"{pairing} WNBA game scheduled for {day.isoformat()} at {local.strftime('%I:%M%p').lstrip('0')} ET. "
    cfb_tail = (
        "Overtime is included if played. If the game is delayed, postponed, or suspended and not rescheduled "
        "to a date within two weeks of the originally scheduled date, the market will settle to the last fair "
        "market price. Outcome sourced from the relevant governing body."
    )
    if sport == "WNBA":
        cfb_tail = cfb_tail.replace("the relevant governing body", "WNBA")
    p = dict(prediction["probabilities"])
    if kind == "moneyline":
        labels = {"a_win", "b_win"} | ({"draw"} if sport in {"KBO", "NPB"} else set())
        if set(p) != labels or prediction["line"] is not None:
            raise ValueError("pricing_probability_labels")
        outcomes = {}
        for direction, s in by_direction.items():
            role = mapped[str(s["team"]["id"])]
            expected_team = next(t for t in teams if str(t["id"]) == str(s["team"]["id"]))
            if s["team"]["name"] != expected_team["name"] or s["description"] != expected_team["name"]:
                raise ValueError("pricing_moneyline_team_mapping")
            outcomes[direction] = "a_win" if role == "home" else "b_win"
        if len(set(outcomes.values())) != 2 or m["slug"] != f"aec-{e['slug']}":
            raise ValueError("pricing_moneyline_side_mapping")
        expected = "This market will settle to the winner of the " + game
        if sport == "NCAAF":
            expected += (
                "Overtime is included if played. If a tied final score is reported and no official winner is declared, "
                "this market will not resolve automatically and will be reviewed against the official governing-body result. "
                + cfb_tail.removeprefix("Overtime is included if played. ")
            )
        else:
            expected += (
                "Extra innings are included if played. If the game ends in a tie, the market will settle to $0.50. "
                "If the game is delayed, postponed, or suspended and not rescheduled to a date within two days of the "
                f"originally scheduled date, the market will settle to the last fair market price. Outcome sourced from {sport}."
            )
    else:
        line = number(m["line"])
        if line.is_integer() or not (line * 2).is_integer() or p.pop("push", None) != 0:
            raise ValueError("pricing_integer_or_nonstandard_settlement_unverified")
        slug_line = str(abs(line)).replace(".", "pt")
        if kind == "total":
            if set(p) != {"over", "under"} or line <= 0:
                raise ValueError("pricing_total_probability_labels")
            if by_direction[True]["description"] != "Over" or by_direction[False]["description"] != "Under":
                raise ValueError("pricing_total_side_mapping")
            expected_slug = f"tsc-{e['slug']}-{'total-' if sport == 'NCAAF' else ''}{slug_line}"
            outcomes = {True: "over", False: "under"}
            expected = (
                f"This market will settle to Yes if {rule_names[0]} and {rule_names[1]} combine for over {line:g} points in the "
                + game
                + cfb_tail
            )
        else:
            if set(p) != {"home_cover", "away_cover"}:
                raise ValueError("pricing_spread_probability_labels")
            outcomes = {}
            for direction, s in by_direction.items():
                role = mapped[str(s["team"]["id"])]
                if number(s["description"]) != (line if direction else -line):
                    raise ValueError("pricing_spread_line_mapping")
                outcomes[direction] = f"{role}_cover"
            if len(set(outcomes.values())) != 2:
                raise ValueError("pricing_spread_side_mapping")
            expected_slug = f"asc-{e['slug']}-{'pos' if line > 0 else 'neg'}-{slug_line}"
            long_team = next(t for t in teams if str(t["id"]) == str(by_direction[True]["team"]["id"]))
            if line > 0:
                # Current provider positive-line descriptions name the opposite
                # team's win as Yes, conflicting with the long-side +line label.
                raise ValueError("pricing_positive_spread_terms_conflict_or_unverified")
            expected = (
                f"This market will settle to Yes if {long_team['safeName']} wins by more than {abs(line):g}{' points' if sport == 'WNBA' else ''} in the "
                + game
                + cfb_tail
            )
        if m["slug"] != expected_slug:
            raise ValueError("pricing_full_game_slug_mismatch")
    if m["description"] != expected:
        raise ValueError("pricing_settlement_terms_unverified")
    bid, bid_size = _levels(b, "bids")
    ask, ask_size = _levels(b, "offers")
    if bid > ask:
        raise ValueError("pricing_crossed_book")
    contracts = []
    for direction, price, size in ((True, ask, ask_size), (False, round(1 - bid, 12), bid_size)):
        contracts.append(
            {
                "market_id": str(m["id"]),
                "side": "long" if direction else "short",
                "ask": price,
                "ask_size": size,
                "settlement_values": {
                    label: 0.5 if label == "draw" else int(label == outcomes[direction])
                    for label in sorted(p)
                },
            }
        )
    return contracts, p
