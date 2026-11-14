"""NCAAF quote source order: captured Polymarket book, then the event list's indicative price, then ESPN.

2026-11-14: the throttled BBO capture missed every NCAAF contract on every daily run,
and with no event-list fallback every Main row of the day was priced off ESPN's
sportsbook consensus with a blank ``market_quote_provenance``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from model_prediction.models import college_football

GAME_DATE = "2030-09-07"
DECISION_AT = datetime(2030, 9, 7, 18, tzinfo=UTC)


def _espn_event() -> dict:
    odds = {
        "moneyline": {"home": {"close": {"odds": "-150"}}, "away": {"close": {"odds": "+130"}}},
        "pointSpread": {
            "home": {"close": {"line": "-3.5", "odds": "-110"}},
            "away": {"close": {"odds": "-110"}},
        },
        "total": {"over": {"close": {"line": "50.5", "odds": "-110"}}, "under": {"close": {"odds": "-110"}}},
    }
    return {
        "id": "fixture",
        "date": "2030-09-07T20:00:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Georgia Bulldogs"}},
                    {"homeAway": "away", "team": {"displayName": "Alabama Crimson Tide"}},
                ],
                "odds": [odds],
            }
        ],
    }


def _side(description: str, team: str | None, price: float, is_long: bool) -> dict:
    return {"description": description, "team": team, "price_probability": price, "is_long": is_long}


def _listed_event() -> dict:
    """A normalized ``PolymarketUSClient.slate`` event, laid out as the live NCAAF feed is."""
    return {
        "provider": "polymarket_us",
        "league": "NCAAF",
        "event_id": "pm-1",
        "event_slug": "cfb-ala-uga-2030-09-07",
        "title": "Alabama vs. Georgia",
        "event_start_utc": "2030-09-07T20:00:00Z",
        "markets": [
            {
                "market_id": "1",
                "market_slug": "aec-cfb-ala-uga-2030-09-07",
                "market_type": "moneyline",
                "line": None,
                "sides": [
                    _side("Crimson Tide", "Alabama", 0.41, True),
                    _side("Bulldogs", "Georgia", 0.61, False),
                ],
            },
            {
                "market_id": "2",
                "market_slug": "asc-cfb-ala-uga-2030-09-07-pos-3pt5",
                "market_type": "spread",
                "line": 3.5,
                "sides": [_side("+3.50", "Alabama", 0.52, True), _side("-3.50", "Georgia", 0.50, False)],
            },
            {
                "market_id": "3",
                "market_slug": "tsc-cfb-ala-uga-2030-09-07-total-50pt5",
                "market_type": "total",
                "line": 50.5,
                "sides": [_side("Over", None, 0.49, True), _side("Under", None, 0.53, False)],
            },
        ],
    }


@pytest.fixture()
def stubbed_model(monkeypatch):
    monkeypatch.setattr(college_football, "_load_cfb_history", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "model_prediction.features.multi_sport_weather.resolve_cfb_game_weather",
        lambda *_args, **_kwargs: (70.0, 0.0, 0.0, True),
    )

    def predict(_self, _history, _game, **_kwargs):
        return [
            SimpleNamespace(
                market_type=market,
                probabilities=prob,
                uncertainty=0.1,
                model_version="cfb-structural-v4",
                feature_basis={},
                inference_inputs=None,
                rationale="fixture",
            )
            for market, prob in (
                ("moneyline", {"home": 0.6, "away": 0.4}),
                ("spread", {"home": 0.55, "away": 0.45}),
                ("total", {"over": 0.55, "under": 0.45}),
            )
        ]

    monkeypatch.setattr(college_football.CFBStructuralV4Model, "predict_matchup", predict)


def _by_market(slate: dict) -> dict[str, dict]:
    return {row["market_type"]: row for row in slate["priced_contracts"]}


def _espn_client() -> SimpleNamespace:
    return SimpleNamespace(scoreboard=lambda *_: {"events": [_espn_event()]})


def test_event_list_prices_markets_with_no_captured_book(tmp_path, stubbed_model):
    slate = college_football.build_cfb_slate(
        tmp_path,
        GAME_DATE,
        client=_espn_client(),
        observed_at=DECISION_AT,
        polymarket_events=[_listed_event()],
    )
    rows = _by_market(slate)

    assert {row["sportsbook"] for row in rows.values()} == {"polymarket_us_indicative"}
    for row in rows.values():
        assert row["market_lineage"] == {
            "market_quote_observed_at_utc": DECISION_AT.isoformat(),
            "market_quote_timestamp_valid": False,
            "market_quote_source": "polymarket_us",
            "market_quote_provenance": "indicative_slate",
            "market_quote_reconstructed": False,
        }
        assert row["feature_basis"]["quote_provenance"] == "polymarket_indicative_slate"
        assert "event-list price" in row["rationale"]
    # Home (Georgia) is the short side of an Alabama-anchored moneyline.
    assert rows["moneyline"]["selection"] == "home"
    assert rows["moneyline"]["executable_ask"] == pytest.approx(0.61)
    assert rows["moneyline"]["market_slug"] == "aec-cfb-ala-uga-2030-09-07-home"
    # Alabama +3.5 anchors the line, so Georgia is -3.5 at the short side's price.
    assert (rows["spread"]["selection"], rows["spread"]["line"]) == ("home", -3.5)
    assert rows["spread"]["executable_ask"] == pytest.approx(0.50)
    assert rows["spread"]["market_slug"] == "asc-cfb-ala-uga-2030-09-07-pos-3pt5"
    assert (rows["total"]["selection"], rows["total"]["line"]) == ("over", 50.5)
    assert rows["total"]["executable_ask"] == pytest.approx(0.49)


def test_captured_book_beats_the_event_list(tmp_path, stubbed_model):
    archive = tmp_path / "odds" / "ncaaf" / GAME_DATE / "polymarket_snapshots.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        json.dumps(
            {
                "provider": "polymarket_us",
                "usage": "prospective_executable_bbo",
                "reconstructed": False,
                "timestamp_valid": True,
                "market_slug": "aec-cfb-ala-uga-2030-09-07",
                "market_type": "moneyline",
                "line": None,
                "team": "Alabama",
                "event_title": "Alabama vs. Georgia",
                "event_start_utc": "2030-09-07T20:00:00Z",
                "observed_at_utc": "2030-09-07T17:30:00+00:00",
                "long": {"description": "Crimson Tide", "ask": 0.40},
                "short": {"description": "Bulldogs", "ask": 0.62},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    slate = college_football.build_cfb_slate(
        tmp_path,
        GAME_DATE,
        client=_espn_client(),
        observed_at=DECISION_AT,
        polymarket_events=[_listed_event()],
    )
    rows = _by_market(slate)

    assert rows["moneyline"]["sportsbook"] == "polymarket_us"
    assert rows["moneyline"]["executable_ask"] == pytest.approx(0.62)
    assert rows["moneyline"]["market_lineage"]["market_quote_provenance"] == "decision_time_executable_quote"
    assert rows["spread"]["sportsbook"] == "polymarket_us_indicative"
    assert rows["total"]["sportsbook"] == "polymarket_us_indicative"


def test_consensus_rows_carry_a_quote_provenance(tmp_path, stubbed_model):
    slate = college_football.build_cfb_slate(
        tmp_path, GAME_DATE, client=_espn_client(), observed_at=DECISION_AT
    )
    rows = _by_market(slate)

    assert set(rows) == {"moneyline", "spread", "total"}
    for row in rows.values():
        assert row["sportsbook"] == "espn_consensus"
        assert row["market_lineage"]["market_quote_provenance"] == "espn_consensus_indicative"
        assert row["market_lineage"]["market_quote_source"] == "espn_consensus"
        assert row["market_lineage"]["market_quote_timestamp_valid"] is False


def test_live_event_list_never_prices_a_past_decision(tmp_path, stubbed_model, monkeypatch):
    """A list fetched now postdates a replayed decision time, so it must not price it."""

    class LiveList:
        def slate(self, league, game_date, timezone_name="America/New_York"):
            assert league == "NCAAF"
            return [_listed_event()]

    monkeypatch.setattr("model_prediction.data_sources.espn.ESPNClient", _espn_client)
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.PolymarketUSClient", LiveList)
    past_decision = datetime(2026, 9, 5, 18, tzinfo=UTC)

    slate = college_football.build_cfb_slate(tmp_path, GAME_DATE, observed_at=past_decision)

    assert {row["sportsbook"] for row in slate["priced_contracts"]} == {"espn_consensus"}

    # The same live list does price a decision made now.
    slate = college_football.build_cfb_slate(tmp_path, GAME_DATE, observed_at=datetime.now(UTC))

    assert {row["sportsbook"] for row in slate["priced_contracts"]} == {"polymarket_us_indicative"}


def test_quote_provenance_reaches_the_ledger_row(tmp_path, stubbed_model, monkeypatch):
    from model_prediction.cli import forecast
    from model_prediction.ledger import PickLedger

    listed = _listed_event()
    # No total on the event list: that market falls through to ESPN consensus.
    listed["markets"] = [market for market in listed["markets"] if market["market_type"] != "total"]
    slate = college_football.build_cfb_slate(
        tmp_path, GAME_DATE, client=_espn_client(), observed_at=DECISION_AT, polymarket_events=[listed]
    )
    monkeypatch.setattr(college_football, "build_cfb_slate", lambda **_kwargs: slate)
    monkeypatch.setattr(forecast, "utc_now", lambda: DECISION_AT)
    flat = PickLedger(
        tmp_path / "flat_ncaaf.xlsx", audit_path=tmp_path / "events.jsonl", tier="flat", sport="ncaaf"
    )

    result = forecast._forecast_cfb_sport(
        data_root=tmp_path,
        args_date=GAME_DATE,
        config={
            "models": {"NCAAF": {"min_edge": 0.035, "status": "shadow_qualified"}},
            "project": {"maximum_data_age_hours": 24},
            "bankroll": {},
        },
        flat_ledger=flat,
    )

    assert not result["errors"]
    rows = {row["market_type"]: row for row in flat.rows()}
    assert rows["moneyline"]["sportsbook"] == "polymarket_us_indicative"
    assert rows["moneyline"]["market_quote_provenance"] == "indicative_slate"
    assert "Indicative ask 0.6100 (aec-cfb-ala-uga-2030-09-07-home)." in rows["moneyline"]["rationale"]
    assert rows["spread"]["market_quote_provenance"] == "indicative_slate"
    assert rows["total"]["sportsbook"] == "espn_consensus"
    assert rows["total"]["market_quote_provenance"] == "espn_consensus_indicative"
