"""NCAAB box-score capture only admits first-seen terminal evidence."""

import json

import pytest

from model_prediction.data_sources.ncaab_boxscores import (
    capture_completed_boxscores,
    parse_completed_boxscore,
)


def _game():
    return {
        "event_id": "123",
        "event_start_utc": "2025-11-01T20:00:00Z",
        "status": "completed",
        "home_team_id": "1",
        "away_team_id": "2",
        "home_team": "Home",
        "away_team": "Away",
        "home_score": 75,
        "away_score": 70,
    }


def _summary():
    def team(team_id):
        return {
            "team": {"id": team_id},
            "statistics": [
                {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "25-60"},
                {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "15-20"},
                {"name": "offensiveRebounds", "displayValue": "10"},
                {"name": "turnovers", "displayValue": "12"},
            ],
        }

    return {
        "header": {"competitions": [{"id": "123", "status": {"type": {"completed": True}}}]},
        "boxscore": {"teams": [team("1"), team("2")]},
    }


def test_boxscore_requires_matching_terminal_event_and_complete_stats():
    game, summary = _game(), _summary()
    record = parse_completed_boxscore(game, summary, "2025-11-02T01:00:00Z")
    assert record["home"]["field_goal_attempts"] == 60
    assert len(record["source_sha256"]) == 64
    summary["header"]["competitions"][0]["status"]["type"]["completed"] = False
    with pytest.raises(ValueError, match="terminal"):
        parse_completed_boxscore(game, summary, "2025-11-02T01:00:00Z")
    summary = _summary()
    summary["boxscore"]["teams"][0]["statistics"].pop()
    with pytest.raises(ValueError, match="components_missing"):
        parse_completed_boxscore(game, summary, "2025-11-02T01:00:00Z")


def test_capture_is_idempotent_and_ignores_unplayed_event(tmp_path):
    class Client:
        calls = 0

        def scoreboard(self, *_args):
            return {
                "events": [
                    {
                        "id": "123",
                        "date": "2025-11-01T20:00:00Z",
                        "competitions": [
                            {
                                "status": {"type": {"completed": True}},
                                "competitors": [
                                    {
                                        "homeAway": "home",
                                        "team": {"id": "1", "displayName": "Home"},
                                        "score": "75",
                                    },
                                    {
                                        "homeAway": "away",
                                        "team": {"id": "2", "displayName": "Away"},
                                        "score": "70",
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "id": "future",
                        "date": "2025-11-01T22:00:00Z",
                        "competitions": [{"status": {"type": {"completed": False}}, "competitors": []}],
                    },
                ]
            }

        def completed_games(self, board):
            from model_prediction.data_sources.espn import ESPNClient

            return ESPNClient.completed_games(board)

        def summary(self, *_args):
            self.calls += 1
            return _summary()

    client = Client()
    first = capture_completed_boxscores(tmp_path, "2025-11-01", client)
    second = capture_completed_boxscores(tmp_path, "2025-11-01", client)
    assert (first["completed"], first["captured"], first["missing"]) == (1, 1, [])
    assert (second["existing"], client.calls) == (1, 1)
    record = json.loads((tmp_path / "features/ncaab/boxscores/2025-11-01/123.json").read_text())
    assert record["event_id"] == "123"
