"""Hockey history must supply real timestamps to offseason-aware EWMA."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from model_prediction.hockey_forward import _load_historical_games
from model_prediction.models.hockey import HockeyModel, UpcomingHockeyMatch


def test_json_history_runs_all_hockey_markets_across_offseason(tmp_path):
    rows = [
        {
            "start": start,
            "completed": True,
            "league": "NHL",
            "away_team": "Away",
            "home_team": "Home",
            "away_score": 2,
            "home_score": 4,
        }
        for start in ("2026-04-01T19:00:00Z", "2026-10-01T19:00:00Z")
    ]
    path = tmp_path / "processed" / "hockey" / "games.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    loaded = _load_historical_games(tmp_path)
    assert all(isinstance(game.start, datetime) for game in loaded)
    expected = [
        SimpleNamespace(**{**row, "start": datetime.fromisoformat(row["start"]).astimezone(UTC)})
        for row in rows
    ]
    model = HockeyModel()
    match = UpcomingHockeyMatch("test", "2026-10-25T19:00:00Z", "Away", "Home")
    predictions = model.predict_games(loaded, [match])
    assert predictions == model.predict_games(expected, [match])
    assert {p.market_type for p in predictions} == {"moneyline", "spread", "total"}


def test_invalid_history_start_is_excluded(tmp_path):
    path = tmp_path / "processed" / "hockey" / "games.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"completed": True, "start": "not-a-date"}), encoding="utf-8")
    assert _load_historical_games(tmp_path) == []
