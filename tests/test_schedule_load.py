from __future__ import annotations

from datetime import UTC, datetime

from model_prediction.features.base import GameRecord
from model_prediction.features.schedule_load import matchup_schedule_load, team_schedule_load


def _game(event_id: str, start: str, away: str, home: str) -> GameRecord:
    return GameRecord(event_id, start, "NBA", away, home, 100, 101)


def test_schedule_load_uses_only_games_before_event() -> None:
    history = [
        _game("old", "2026-01-08T00:00:00Z", "A", "C"),
        _game("future", "2026-01-10T02:00:00Z", "A", "D"),
    ]
    result = team_schedule_load(
        history,
        "A",
        datetime(2026, 1, 10, 0, 0, tzinfo=UTC),
    )
    assert result.available
    assert result.rest_days_capped == 2
    assert result.games_last_7_days == 1


def test_matchup_schedule_load_is_home_minus_away_and_caps_long_rest() -> None:
    history = [
        _game("home-old", "2025-12-01T00:00:00Z", "C", "HOME"),
        _game("away-recent", "2026-01-09T00:00:00Z", "AWAY", "D"),
    ]
    result = matchup_schedule_load(
        history,
        "HOME",
        "AWAY",
        datetime(2026, 1, 10, 0, 0, tzinfo=UTC),
    )
    assert result == {
        "rest_disparity": 6.0,
        "back_to_back_gap": -1.0,
        "games_last_7_gap": -1.0,
        "schedule_available": 1.0,
    }


def test_matchup_schedule_load_fails_closed_when_one_team_missing() -> None:
    result = matchup_schedule_load(
        [_game("only-home", "2026-01-09T00:00:00Z", "C", "HOME")],
        "HOME",
        "AWAY",
        datetime(2026, 1, 10, 0, 0, tzinfo=UTC),
    )
    assert result["schedule_available"] == 0.0
    assert result["rest_disparity"] == 0.0
