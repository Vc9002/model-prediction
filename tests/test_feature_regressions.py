from __future__ import annotations

from model_prediction.features.base import GameRecord
from model_prediction.features.head_to_head import head_to_head


def test_head_to_head_counts_soccer_draws_without_awarding_them_to_away_team() -> None:
    games = [
        GameRecord("1", "2026-01-01T12:00:00Z", "SOCCER", "A", "B", 1, 1),
        GameRecord("2", "2026-01-02T12:00:00Z", "SOCCER", "B", "A", 0, 2),
    ]

    result = head_to_head(games, "A", "B")

    assert result["team_a_wins"] == 1
    assert result["team_b_wins"] == 0
    assert result["draws"] == 1
    assert result["draw_rate"] == 0.5
