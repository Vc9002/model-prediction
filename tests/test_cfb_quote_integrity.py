"""Missing quoted prices must never become college-football calls."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from model_prediction.models import college_football


@pytest.mark.parametrize("missing_market", ("moneyline", "spread", "total"))
def test_cfb_missing_side_price_blocks_that_market(tmp_path, monkeypatch, missing_market):
    odds = {
        "moneyline": {"home": {"close": {"odds": "-150"}}, "away": {"close": {"odds": "+130"}}},
        "pointSpread": {
            "home": {"close": {"line": "-3.5", "odds": "-110"}},
            "away": {"close": {"odds": "-110"}},
        },
        "total": {"over": {"close": {"line": "50.5", "odds": "-110"}}, "under": {"close": {"odds": "-110"}}},
    }
    if missing_market == "moneyline":
        del odds["moneyline"]["away"]
    elif missing_market == "spread":
        del odds["pointSpread"]["away"]
    else:
        del odds["total"]["under"]
    event = {
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
    client = SimpleNamespace(scoreboard=lambda *_: {"events": [event]})
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
                inference_inputs={},
                rationale="fixture",
            )
            for market, prob in (
                ("moneyline", {"home": 0.6, "away": 0.4}),
                ("spread", {"home": 0.55, "away": 0.45}),
                ("total", {"over": 0.55, "under": 0.45}),
            )
        ]

    monkeypatch.setattr(college_football.CollegeFootballModel, "predict_matchup", predict)
    slate = college_football.build_cfb_slate(
        tmp_path, "2030-09-07", client=client, observed_at=datetime(2030, 9, 7, 18, tzinfo=UTC)
    )
    markets = {row["market_type"] for row in slate["priced_contracts"]}
    assert missing_market not in markets
    assert markets == {"moneyline", "spread", "total"} - {missing_market}
