import copy
import json
from pathlib import Path

import pytest

from model_prediction.research_generation import digest
from model_prediction.research_market_pricing import contracts_for_prediction, matching_market
from model_prediction.research_pricing import price_forecast, replay_price
from model_prediction.research_soccer_pricing import identity


def example(target="home_win"):
    e = json.loads((Path(__file__).parent / "fixtures/research_market_terms_20260913.json").read_text())[
        "events"
    ]["SOCCER"]
    p = {
        "sport": "SOCCER",
        "market": "moneyline",
        "line": None,
        "model_id": "test",
        "observed_at_utc": "2026-09-13T06:00:00Z",
        "probabilities": {"a_win": 0.4, "b_win": 0.35, "draw": 0.25},
        "raw_fixture": {
            "league": "EPL",
            "home_team": "Coventry City",
            "away_team": "Brighton & Hove Albion",
            "event_start_utc": e["startTime"],
            "period": "regulation_90_minutes",
            "pricing_target": target,
        },
    }
    p["snapshot_hash"] = digest(p)
    m = next(m for m in e["markets"] if matching_market(p, e, m))
    q = {
        "provider": "polymarket_us_public_gateway",
        "event": e,
        "market": copy.deepcopy(m),
        "request_started_at_utc": "2026-09-13T06:59:00Z",
        "observed_at_utc": "2026-09-13T07:00:00Z",
        "pricing_decision_at_utc": "2026-09-13T07:00:00Z",
        "book": {
            "marketSlug": m["slug"],
            "state": "MARKET_STATE_OPEN",
            "transactTime": "2026-09-13T06:59:00Z",
            "bids": [{"px": {"value": ".30", "currency": "USD"}, "qty": "100"}],
            "offers": [{"px": {"value": ".32", "currency": "USD"}, "qty": "100"}],
        },
    }
    return p, q


@pytest.mark.parametrize("target,label", [("home_win", "a_win"), ("away_win", "b_win"), ("draw", "draw")])
def test_all_soccer_contracts_preserve_three_way_payouts(target, label):
    p, q = example(target)
    contracts, probabilities = contracts_for_prediction(p, q)
    assert probabilities == p["probabilities"]
    assert contracts[0]["settlement_values"] == {l: int(l == label) for l in probabilities}
    assert contracts[1]["settlement_values"] == {l: int(l != label) for l in probabilities}
    assert replay_price(p, price_forecast(p, q))["pnl_usd"] is None


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "extra_time",
        "question",
        "same_city_rival",
        "league",
        "target",
        "side_team",
        "duplicate_side",
        "long",
        "partial",
        "future_book",
        "fee",
        "start",
        "nan",
    ],
)
def test_soccer_rejects_scope_and_mapping_mismatches(change):
    p, q = example()
    m = q["market"]
    if change == "scope":
        p["raw_fixture"].pop("period")
    elif change == "extra_time":
        m["description"] = m["description"].replace("90 minutes plus stoppage time", "extra time")
        q["event"]["markets"][0]["description"] = m["description"]
    elif change == "question":
        m["question"] = "Will Brighton win?"
    elif change == "same_city_rival":
        p["raw_fixture"]["home_team"] = "Coventry United"
    elif change == "league":
        p["raw_fixture"]["league"] = "CHAMPIONSHIP"
    elif change == "target":
        p["raw_fixture"]["pricing_target"] = "draw"
    elif change == "side_team":
        m["marketSides"][0]["team"]["id"] = q["event"]["teams"][1]["id"]
    elif change == "duplicate_side":
        m["marketSides"] *= 2
    elif change == "long":
        m["marketSides"][0]["long"] = False
    elif change == "partial":
        m["slug"] = m["slug"].replace("-cov", "-fh-cov", 1)
    elif change == "future_book":
        q["book"]["transactTime"] = "2026-09-13T07:00:01Z"
    elif change == "fee":
        m["feeCoefficient"] = 0.07
    elif change == "start":
        m["gameStartTime"] = "2026-09-13T13:01:00Z"
    elif change == "nan":
        q["book"]["offers"][0]["qty"] = "NaN"
    with pytest.raises((ValueError, KeyError)):
        contracts_for_prediction(p, q)


def test_team_identity_retains_derby_distinctions():
    assert identity("Manchester City FC") != identity("Manchester United FC")
    assert identity("Manchester City") == identity("Manchester City FC")
