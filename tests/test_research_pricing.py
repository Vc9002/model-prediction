import copy
import json
from datetime import datetime

import pytest

from model_prediction.research_generation import digest
from model_prediction.research_pairing import simulate_contracts
from model_prediction.research_pricing import (
    LEGACY_PRICING_POLICY,
    nrfi_contracts,
    price_forecast,
    replay_price,
)
from scripts import capture_research_prices, score_research_prices


def sample():
    start = "2026-09-12T17:10:00Z"
    slug = "astatc-mlb-col-det-2026-09-12-yrfi"
    market = {
        "id": "801071",
        "slug": slug,
        "gameStartTime": start,
        "sportsMarketType": "baseball_team_first_inning_run",
        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_PROP",
        "line": 1,
        "question": "Will any run be scored in the 1st inning of COL vs DET?",
        "description": "This market will settle to Yes if either team scores at least one run in the first inning in the Colorado Rockies vs Detroit Tigers MLB game scheduled for 2026-09-12 at 1:10PM ET. If the game is delayed, postponed, or suspended and not rescheduled to a date within two days of the originally scheduled date, the market will settle to the last fair market price. Outcome sourced from MLB.",
        "active": True,
        "closed": False,
        "archived": False,
        "status": "MARKET_STATUS_OPEN",
        "feeCoefficient": 0.06,
        "minimumTradeQty": 0.01,
        "marketSides": [
            {
                "id": str(i),
                "description": desc,
                "long": i == 1,
                "tradable": True,
                "marketId": 801071,
                "identifier": slug,
            }
            for i, desc in ((1, "Yes"), (2, "No"))
        ],
    }
    event = {
        "id": "108656",
        "slug": "mlb-col-det-2026-09-12",
        "startTime": start,
        "active": True,
        "closed": False,
        "teams": [
            {"id": i, "name": name, "league": "mlb"}
            for i, name in ((1, "Colorado Rockies"), (2, "Detroit Tigers"))
        ],
        "markets": [copy.deepcopy(market)],
    }
    book = {
        "marketSlug": slug,
        "state": "MARKET_STATE_OPEN",
        "transactTime": "2026-09-12T14:59:00Z",
        "bids": [{"px": {"value": ".49", "currency": "USD"}, "qty": "7.5"}],
        "offers": [{"px": {"value": ".50", "currency": "USD"}, "qty": "123"}],
    }
    prediction = {
        "sport": "MLB",
        "market": "nrfi",
        "line": None,
        "fixture": {"event_id": "espn-1"},
        "raw_fixture": {
            "event_start_utc": start,
            "home_team": "Detroit Tigers",
            "away_team": "Colorado Rockies",
            "event_id": "espn-1",
            "home_team_id": "6",
            "away_team_id": "27",
        },
        "observed_at_utc": "2026-09-12T13:00:00Z",
        "model_id": "test",
        "probabilities": {"nrfi": 0.7, "yrfi": 0.3},
    }
    prediction["snapshot_hash"] = digest(prediction)
    quote = {
        "provider": "polymarket_us_public_gateway",
        "event": event,
        "market": market,
        "book": book,
        "request_started_at_utc": "2026-09-12T14:59:00Z",
        "observed_at_utc": "2026-09-12T15:00:00Z",
        "pricing_decision_at_utc": "2026-09-12T15:00:00Z",
    }
    return prediction, quote


def test_nrfi_yes_no_payouts_depth_and_later_pricing():
    p, q = sample()
    contracts = nrfi_contracts(p, q)
    assert contracts[0]["settlement_values"] == {"nrfi": 0, "yrfi": 1}
    assert contracts[1]["settlement_values"] == {"nrfi": 1, "yrfi": 0}
    assert contracts[1]["ask"] == 0.51 and contracts[1]["ask_size"] == 7.5
    record = price_forecast(p, q)
    choice = replay_price(p, record)
    assert choice["side"] == "short" and choice["quantity"] == 7
    assert choice["cost_usd"] <= 5 and choice["pnl_usd"] is None
    assert q["pricing_decision_at_utc"] != p["observed_at_utc"]
    for label in ("nrfi", "yrfi"):
        settled = simulate_contracts(p["probabilities"], label, contracts)
        assert settled["pnl_usd"] == pytest.approx((7 if label == "nrfi" else 0) - choice["cost_usd"])
    record["choice"]["quantity"] += 1
    with pytest.raises(ValueError, match="replay_mismatch"):
        replay_price(p, record)


def test_first_archived_nrfi_policy_remains_replayable_after_metadata_correction():
    p, q = sample()
    original = price_forecast(p, q, policy=LEGACY_PRICING_POLICY)
    current = price_forecast(p, q)
    assert replay_price(p, original) == replay_price(p, current)
    assert original["schema"] != current["schema"]


def test_cli_reports_unavailable_forecasts_as_partial_without_requesting_prices(tmp_path, monkeypatch):
    forecasts = tmp_path / "forecasts"
    forecasts.mkdir()
    (forecasts / "generation_manifest.json").write_text('{"models": []}')
    (forecasts / "predictions.jsonl").write_text(
        json.dumps(
            {
                "sport": "MLB",
                "market": "nrfi",
                "event_id": "unavailable",
                "status": "NO_PREDICTION",
                "reason": "missing_starter",
            }
        )
        + "\n"
    )

    class NoNetwork:
        def events(self, league):
            raise AssertionError("No price request is allowed without a forecast")

    monkeypatch.setattr(capture_research_prices, "PolymarketUSClient", NoNetwork)
    output = tmp_path / "prices"
    monkeypatch.setattr(
        "sys.argv", ["capture_research_prices", "--forecasts", str(forecasts), "--output", str(output)]
    )
    assert capture_research_prices.main() == 2
    report = json.loads((output / "report.json").read_text())
    assert report["counts"] == {"NO_FORECAST": 1} and report["pnl_usd"] is None


@pytest.mark.parametrize(
    "change",
    [
        "team",
        "league",
        "start",
        "f5",
        "type",
        "line",
        "side",
        "side_id",
        "not_tradable",
        "closed",
        "fee",
        "currency",
        "nan",
        "crossed",
        "zero_depth",
        "no_depth",
        "duplicate_depth",
        "old_book",
        "future_book",
        "old_request",
        "future_prediction",
        "post_start",
        "changed_rule",
        "ambiguous_market",
    ],
)
def test_nrfi_rejects_invalid_prices_and_scope(change):
    p, q = sample()
    e, m, b = q["event"], q["market"], q["book"]
    if change == "team":
        e["teams"][0]["name"] = "Other"
    elif change == "league":
        e["teams"][0]["league"] = "npb"
    elif change == "start":
        e["startTime"] = "2026-09-12T17:11:00Z"
    elif change == "f5":
        e["slug"] += "-f5"
    elif change == "type":
        m["sportsMarketType"] = "total"
    elif change == "line":
        m["line"] = 0.5
    elif change == "side":
        m["marketSides"][0]["long"] = False
    elif change == "side_id":
        m["marketSides"][1]["id"] = "1"
    elif change == "not_tradable":
        m["marketSides"][0]["tradable"] = False
    elif change == "closed":
        b["state"] = "MARKET_STATE_CLOSED"
    elif change == "fee":
        m["feeCoefficient"] = 0.07
    elif change == "currency":
        b["bids"][0]["px"]["currency"] = "EUR"
    elif change == "nan":
        b["offers"][0]["qty"] = "NaN"
    elif change == "crossed":
        b["bids"][0]["px"]["value"] = ".7"
    elif change == "zero_depth":
        b["offers"][0]["qty"] = "0"
    elif change == "no_depth":
        b["offers"] = []
    elif change == "duplicate_depth":
        b["bids"] *= 2
    elif change == "old_book":
        b["transactTime"] = "2026-09-12T14:54:59Z"
    elif change == "future_book":
        b["transactTime"] = "2026-09-12T15:00:01Z"
    elif change == "old_request":
        q["request_started_at_utc"] = "2026-09-12T14:54:59Z"
    elif change == "future_prediction":
        p["observed_at_utc"] = "2026-09-12T15:00:01Z"
    elif change == "post_start":
        q["pricing_decision_at_utc"] = p["raw_fixture"]["event_start_utc"]
    elif change == "changed_rule":
        m["description"] = m["description"].replace("either team", "the home team")
        e["markets"][0]["description"] = m["description"]
    elif change == "ambiguous_market":
        e["markets"] *= 2
    with pytest.raises((ValueError, TypeError, KeyError)):
        nrfi_contracts(p, q)


def test_capture_score_and_archive_tamper(tmp_path, monkeypatch):
    p, q = sample()
    forecasts = tmp_path / "forecasts"
    (forecasts / "models/mlb-nrfi").mkdir(parents=True)
    (forecasts / "sources").mkdir()
    (forecasts / "predictions.jsonl").write_text(
        json.dumps({"status": "CAPTURED_REPLAY_PASS", "prediction": p}) + "\n"
    )
    (forecasts / "generation_manifest.json").write_text("{}")
    for file in ("models/mlb-nrfi/model.joblib", "models/mlb-nrfi/recipe.json", "sources/mlb-nrfi.jsonl"):
        (forecasts / file).write_text("retained source bytes")
    rebuilt = []

    def rebuild(directory, prediction):
        # Exercise the orchestration boundary. Full model/source replay has its
        # own tests and is also run on the actual prospective capture.
        rebuilt.append(directory)
        assert prediction == p
        return p["probabilities"]

    monkeypatch.setattr(capture_research_prices, "rebuild", rebuild)
    monkeypatch.setattr(score_research_prices, "rebuild", rebuild)

    class Clock:
        @staticmethod
        def now(tz):
            return datetime.fromisoformat("2026-09-12T15:00:00+00:00")

        fromisoformat = datetime.fromisoformat

    class Client:
        def events(self, league):
            return [q["event"]]

        def event(self, slug):
            return q["event"]

        def market(self, slug):
            return q["market"]

        def book(self, slug):
            return q["book"]

    monkeypatch.setattr(capture_research_prices, "datetime", Clock)
    prices = tmp_path / "prices"
    report = capture_research_prices.run(forecasts, prices, Client())
    assert report["counts"] == {"PRICED_REPLAY_PASS": 1}
    assert report["calls"] == 1 and report["pnl_usd"] is None
    raw = {
        "source_schema": "espn-scoreboard-result-v1",
        "event_id": "espn-1",
        "status": "completed",
        "game_start_utc": p["raw_fixture"]["event_start_utc"],
        "observed_at_utc": "2026-09-12T23:00:00Z",
        "home": {"team_name": "Detroit Tigers", "team_id": "6"},
        "away": {"team_name": "Colorado Rockies", "team_id": "27"},
        "first_inning_runs_home": 0,
        "first_inning_runs_away": 0,
    }
    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.write_text(json.dumps(raw) + "\n")
    report = score_research_prices.run(prices, outcomes, tmp_path / "scored")
    assert report["counts"] == {"SCORED": 1} and report["simulation"]["pnl_usd"] > 0
    assert len(rebuilt) == 2
    outcomes.write_text(json.dumps(raw) + "\n" + json.dumps(raw | {"first_inning_runs_away": 1}) + "\n")
    report = score_research_prices.run(prices, outcomes, tmp_path / "conflict")
    assert report["counts"] == {"INTEGRITY_FAILURE": 1} and report["simulation"]["pnl_usd"] is None
    (prices / "forecasts/sources/mlb-nrfi.jsonl").write_text("changed")
    with pytest.raises(ValueError, match="file_hash_mismatch"):
        score_research_prices.run(prices, outcomes, tmp_path / "tampered")
