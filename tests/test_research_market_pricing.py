import copy
import json
from pathlib import Path

import pytest

from model_prediction.research_generation import digest
from model_prediction.research_market_pricing import TYPES, contracts_for_prediction, team_identity
from model_prediction.research_pairing import simulate_contracts
from model_prediction.research_pricing import price_forecast, replay_price


def example(sport="NCAAF", kind="spread"):
    events = json.loads((Path(__file__).parent / "fixtures/research_market_terms_20260913.json").read_text())[
        "events"
    ]
    event = events[sport]
    market = next(m for m in event["markets"] if m["sportsMarketType"] == TYPES[(sport, kind)])
    teams = event["teams"]
    fixture = {
        "home_team_id": team_identity(sport, teams[1]),
        "away_team_id": team_identity(sport, teams[0]),
        "event_start_utc": event["startTime"],
    }
    if sport == "WNBA":
        fixture.update(home_team=team_identity(sport, teams[1]), away_team=team_identity(sport, teams[0]))
        # Only metadata is real; this open book is hypothetical test input.
        event["closed"] = False
        market.update(closed=False, status="MARKET_STATUS_OPEN")
    line = -float(market["line"]) if kind == "spread" else market.get("line")
    labels = {
        "moneyline": ["a_win", "b_win"],
        "spread": ["home_cover", "away_cover", "push"],
        "total": ["under", "over", "push"],
    }[kind]
    probabilities = dict(zip(labels, [0.7, 0.3, 0], strict=False))
    if sport in {"KBO", "NPB"}:
        probabilities = {"a_win": 0.6, "b_win": 0.2, "draw": 0.2}
    p = {
        "sport": sport,
        "market": kind,
        "line": line,
        "model_id": "test",
        "raw_fixture": fixture,
        "observed_at_utc": "2026-09-13T06:00:00Z",
        "probabilities": probabilities,
    }
    p["snapshot_hash"] = digest(p)
    q = {
        "provider": "polymarket_us_public_gateway",
        "event": event,
        "market": copy.deepcopy(market),
        "request_started_at_utc": "2026-09-13T06:59:00Z",
        "observed_at_utc": "2026-09-13T07:00:00Z",
        "pricing_decision_at_utc": "2026-09-13T07:00:00Z",
        "book": {
            "marketSlug": market["slug"],
            "transactTime": "2026-09-13T06:59:00Z",
            "state": "MARKET_STATE_OPEN",
            "bids": [{"px": {"value": ".59", "currency": "USD"}, "qty": "100"}],
            "offers": [{"px": {"value": ".60", "currency": "USD"}, "qty": "100"}],
        },
    }
    if sport == "WNBA":
        p["observed_at_utc"] = "2026-08-30T06:00:00Z"
        q.update(
            request_started_at_utc="2026-08-30T06:59:00Z",
            observed_at_utc="2026-08-30T07:00:00Z",
            pricing_decision_at_utc="2026-08-30T07:00:00Z",
        )
        q["book"]["transactTime"] = "2026-08-30T06:59:00Z"
        p["snapshot_hash"] = digest({k: v for k, v in p.items() if k != "snapshot_hash"})
    return p, q


@pytest.mark.parametrize("sport,kind", [key for key in TYPES if key[0] != "SOCCER"])
def test_actual_terms_have_explicit_payouts(sport, kind):
    p, q = example(sport, kind)
    contracts, probabilities = contracts_for_prediction(p, q)
    assert len(contracts) == 2 and "push" not in probabilities
    record = price_forecast(p, q)
    choice = replay_price(p, record)
    assert choice["pnl_usd"] is None
    if kind == "spread":
        assert contracts[0]["settlement_values"] == {"away_cover": 1, "home_cover": 0}
        assert p["line"] == (0.5 if sport == "NCAAF" else 10.5)
    if sport in {"KBO", "NPB"}:
        assert contracts[0]["settlement_values"] == {"a_win": 0, "b_win": 1, "draw": 0.5}
        assert contracts[1]["settlement_values"] == {"a_win": 1, "b_win": 0, "draw": 0.5}
        result = simulate_contracts(probabilities, "draw", contracts)
        assert result["called"]
        assert result["pnl_usd"] == pytest.approx(result["quantity"] * 0.5 - result["cost_usd"])
        assert result["settlement_value"] != result["ask"]  # Half-value tie is not a refund.


@pytest.mark.parametrize(
    "change",
    [
        "partial",
        "opposite_line",
        "integer",
        "push_mass",
        "positive_conflict",
        "team",
        "side",
        "line_description",
        "terms",
        "type",
        "start",
        "book_slug",
        "fee",
        "duplicate",
    ],
)
def test_cfb_conflicting_or_incomplete_terms_fail(change):
    p, q = example()
    m, e = q["market"], q["event"]
    if change == "partial":
        m["sportsMarketType"] = "football_team_first_half_spread"
    elif change == "opposite_line":
        p["line"] *= -1
    elif change == "integer":
        m["line"] = -1
        p["line"] = 1
    elif change == "push_mass":
        p["probabilities"]["push"] = 0.01
    elif change == "positive_conflict":
        m["line"] = 0.5
        p["line"] = -0.5
        m["marketSides"][0]["description"] = "+0.5"
        m["marketSides"][1]["description"] = "-0.5"
        e["markets"][1] = copy.deepcopy(m)
    elif change == "team":
        e["teams"][0]["safeName"] = "Georgia"
    elif change == "side":
        m["marketSides"][0]["team"]["id"] = e["teams"][1]["id"]
    elif change == "line_description":
        m["marketSides"][0]["description"] = "+0.5"
    elif change == "terms":
        m["description"] = m["description"].replace("Syracuse wins", "Pittsburgh wins")
        e["markets"][1]["description"] = m["description"]
    elif change == "type":
        m["sportsMarketTypeV2"] = "SPORTS_MARKET_TYPE_PROP"
    elif change == "start":
        m["gameStartTime"] = "2026-09-18T23:30:00Z"
    elif change == "book_slug":
        q["book"]["marketSlug"] += "-1h"
    elif change == "fee":
        m["feeCoefficient"] = 0.07
    elif change == "duplicate":
        e["markets"].append(copy.deepcopy(m))
    with pytest.raises((ValueError, KeyError)):
        contracts_for_prediction(p, q)


def test_baseball_missing_tie_rule_cannot_be_treated_as_refund():
    p, q = example("KBO", "moneyline")
    q["market"]["description"] = q["market"]["description"].replace("$0.50", "the entry price")
    q["event"]["markets"][0]["description"] = q["market"]["description"]
    with pytest.raises(ValueError, match="settlement_terms"):
        contracts_for_prediction(p, q)
