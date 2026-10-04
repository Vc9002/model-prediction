"""Replayable later pricing decisions for already frozen research forecasts.

Public gateway responses are retained verbatim. Prediction time and pricing
time are separate; neither historical entry prices nor actual fills are inferred.
Adapters enforce explicit first-inning, full-game and 90-minute settlement rules.
"""

from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo

from .research_forward import aware_time
from .research_generation import digest
from .research_incumbent_evaluation import POLICY
from .research_pairing import simulate_contracts
from .research_quote_contracts import number

LEGACY_SCHEMA = "research-later-pricing-v1"
# Retain exact identity of the first captured batch. Its inherited full-game /
# retrospective wording was inaccurate; actual payout tables and timestamps
# were already first-inning and prospective. v2 corrects metadata only.
LEGACY_PRICING_POLICY = POLICY | {
    "version": LEGACY_SCHEMA,
    "timing": "frozen prediction, then public event/market/book capture, then pricing decision",
    "selection": "one first captured eligible book per forecast within the batch",
    "scope": "completed MLB games with explicit first-inning scores; voids unavailable",
}
V2_SCHEMA = "research-later-pricing-v2"
V2_PRICING_POLICY = {k: v for k, v in LEGACY_PRICING_POLICY.items() if k != "budget_per_event_usd"} | {
    "version": V2_SCHEMA,
    "decision_sampling": "one frozen forecast per event/market/line per batch; no quote-driven forecast selection",
    "settlement": "explicit completed-event payout tables; KBO/NPB draw pays $0.50; voids unavailable",
    "scope": "completed MLB first inning, CFB full-game binary/half-point contracts, KBO/NPB full-game win/draw",
    "evidence": "prospectively captured later quote and hypothetical immediate taker fill; not actual fills",
    "budget_per_event_market_usd": 5,
}
SCHEMA = "research-later-pricing-v3"
PRICING_POLICY = V2_PRICING_POLICY | {
    "version": SCHEMA,
    "scope": V2_PRICING_POLICY["scope"]
    + "; WNBA full-game half-point contracts; soccer 90-minute home/away/draw contract declared before prices",
    "soccer_target": "frozen fixture pricing_target: home_win (default), away_win, or draw; one contract per forecast",
}
SUPPORTED_POLICIES = {p["version"]: p for p in (LEGACY_PRICING_POLICY, V2_PRICING_POLICY, PRICING_POLICY)}


def matching_nrfi_event(fixture: dict[str, Any], event: dict[str, Any]) -> bool:
    """Match the exact two names and start, never compare unrelated provider IDs."""
    teams = event.get("teams", [])
    return (
        len(teams) == 2
        and {t.get("name") for t in teams} == {fixture["home_team"], fixture["away_team"]}
        and all(t.get("league") == "mlb" and t.get("id") for t in teams)
        and len({str(t["id"]) for t in teams}) == 2
        and aware_time(event["startTime"]) == aware_time(fixture["event_start_utc"])
        and bool(re.fullmatch(r"mlb-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}", event["slug"]))
    )


def _levels(book: dict[str, Any], key: str) -> tuple[float, float]:
    levels = []
    for level in book[key]:
        if level["px"]["currency"] != "USD":
            raise ValueError("pricing_non_usd_book")
        price, size = number(level["px"]["value"]), number(level["qty"])
        if not 0 < price < 1 or size <= 0:
            raise ValueError("pricing_invalid_book_level")
        levels.append((price, size))
    if not levels or len({p for p, _ in levels}) != len(levels):
        raise ValueError("pricing_missing_or_duplicate_depth")
    return (max if key == "bids" else min)(levels)


def nrfi_contracts(prediction: dict[str, Any], quote: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate identity, explicit settlement terms, chronology and raw BBO."""
    if prediction["sport"] != "MLB" or prediction["market"] != "nrfi":
        raise ValueError("pricing_adapter_unavailable")
    if prediction["line"] is not None or set(prediction["probabilities"]) != {"nrfi", "yrfi"}:
        raise ValueError("pricing_nrfi_prediction_semantics")
    fixture = prediction["raw_fixture"]
    event, market, book = (quote[key] for key in ("event", "market", "book"))
    predicted, requested, observed, decision, start = (
        aware_time(value)
        for value in (
            prediction["observed_at_utc"],
            quote["request_started_at_utc"],
            quote["observed_at_utc"],
            quote["pricing_decision_at_utc"],
            fixture["event_start_utc"],
        )
    )
    transact = aware_time(book["transactTime"])
    maximum_age = POLICY["maximum_quote_age_seconds"]
    if (
        quote["provider"] != "polymarket_us_public_gateway"
        or not aware_time(POLICY["fee_effective_utc"])
        <= predicted
        <= requested
        <= observed
        <= decision
        < start
        or not transact <= observed
        or (decision - transact).total_seconds() > maximum_age
        or (decision - requested).total_seconds() > maximum_age
    ):
        raise ValueError("pricing_quote_chronology_or_staleness")
    if not matching_nrfi_event(fixture, event) or not event.get("id"):
        raise ValueError("pricing_event_identity_mismatch")
    expected_slug = f"astatc-{event['slug']}-yrfi"
    start_local = start.astimezone(ZoneInfo("America/New_York"))
    if event["slug"].split("-")[-3:] != start_local.date().isoformat().split("-"):
        raise ValueError("pricing_event_date_mismatch")
    if (
        market["slug"] != expected_slug
        or book["marketSlug"] != expected_slug
        or aware_time(market["gameStartTime"]) != start
        or not market.get("id")
        or market["sportsMarketType"] != "baseball_team_first_inning_run"
        or market["sportsMarketTypeV2"] != "SPORTS_MARKET_TYPE_PROP"
        or number(market["line"]) != 1
    ):
        raise ValueError("pricing_nrfi_contract_identity_mismatch")
    listed = [m for m in event["markets"] if m.get("slug") == expected_slug or m.get("id") == market["id"]]
    semantic_fields = (
        "id",
        "slug",
        "sportsMarketType",
        "sportsMarketTypeV2",
        "gameStartTime",
        "line",
        "description",
        "question",
    )
    if len(listed) != 1 or any(listed[0].get(k) != market[k] for k in semantic_fields):
        raise ValueError("pricing_detail_market_mismatch")
    # Require the provider's actual rule, not its misleading team metadata or
    # the numeric `line: 1` field (which is not a first-inning total of 1).
    teams = event["teams"]
    pairing = f"{teams[0]['name']} vs {teams[1]['name']}"
    clock = start_local.strftime("%I:%M%p").lstrip("0")
    expected_rule = (
        "This market will settle to Yes if either team scores at least one run in the first inning in the "
        f"{pairing} MLB game scheduled for {start_local.date().isoformat()} at {clock} ET. "
        "If the game is delayed, postponed, or suspended and not rescheduled to a date within two days of the "
        "originally scheduled date, the market will settle to the last fair market price. Outcome sourced from MLB."
    )
    if market["description"] != expected_rule:
        raise ValueError("pricing_nrfi_settlement_terms_unverified")
    if (
        market.get("active") is not True
        or market.get("closed") is not False
        or market.get("archived") is not False
        or market.get("status") != "MARKET_STATUS_OPEN"
        or event.get("active") is not True
        or event.get("closed") is not False
        or book.get("state") != "MARKET_STATE_OPEN"
        or number(market["feeCoefficient"]) != number(POLICY["fee_rate"])
        or not 0 < number(market["minimumTradeQty"]) <= 1
    ):
        raise ValueError("pricing_market_closed_or_unsupported_fee_or_quantity")
    sides = market["marketSides"]
    if len(sides) != 2 or {s["description"] for s in sides} != {"Yes", "No"}:
        raise ValueError("pricing_nrfi_side_mapping")
    if len({str(s["id"]) for s in sides}) != 2:
        raise ValueError("pricing_duplicate_side_identity")
    for side in sides:
        if (
            side.get("long") is not (side["description"] == "Yes")
            or side.get("tradable") is not True
            or not side.get("id")
            or str(side["marketId"]) != str(market["id"])
            or side["identifier"] != expected_slug
        ):
            raise ValueError("pricing_nrfi_side_mapping")
    bid, bid_size = _levels(book, "bids")
    ask, ask_size = _levels(book, "offers")
    if bid > ask:
        raise ValueError("pricing_crossed_book")
    contracts = []
    for direction, price, size, win in (
        ("long", ask, ask_size, "yrfi"),
        ("short", round(1 - bid, 12), bid_size, "nrfi"),
    ):
        contracts.append(
            {
                "market_id": str(market["id"]),
                "side": direction,
                "ask": price,
                "ask_size": size,
                "settlement_values": {label: int(label == win) for label in ("nrfi", "yrfi")},
            }
        )
    return contracts


def price_forecast(
    prediction: dict[str, Any],
    quote: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = PRICING_POLICY if policy is None else policy
    if policy not in SUPPORTED_POLICIES.values():
        raise ValueError("unsupported_pricing_policy")
    probabilities = prediction["probabilities"]
    if prediction["sport"] == "MLB" and prediction["market"] == "nrfi":
        contracts = nrfi_contracts(prediction, quote)
    else:
        from .research_market_pricing import contracts_for_prediction

        if policy == LEGACY_PRICING_POLICY or (
            prediction["sport"] in {"SOCCER", "WNBA"} and policy == V2_PRICING_POLICY
        ):
            raise ValueError("legacy_pricing_adapter_unavailable")
        contracts, probabilities = contracts_for_prediction(prediction, quote)
    record = {
        "schema": policy["version"],
        "forecast_hash": prediction["snapshot_hash"],
        "model_id": prediction["model_id"],
        "policy_hash": digest(policy),
        "quote": quote,
        "quote_hash": digest(quote),
        "contracts": contracts,
        "choice": simulate_contracts(probabilities, None, contracts),
        "settlement_scope": policy["scope"],
        "actual_fill": None,
        "promote": False,
    }
    if policy != LEGACY_PRICING_POLICY:
        record["pricing_probabilities"] = probabilities
    return record | {"pricing_hash": digest(record)}


def replay_price(prediction: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    if record["schema"] not in SUPPORTED_POLICIES:
        raise ValueError("unsupported_pricing_policy")
    policy = SUPPORTED_POLICIES[record["schema"]]
    rebuilt = price_forecast(prediction, record["quote"], policy=policy)
    if record != rebuilt:
        raise ValueError("research_pricing_replay_mismatch")
    return rebuilt["choice"]
