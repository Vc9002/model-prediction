from datetime import date

import pandas as pd

from model_prediction.data_sources.tennis_sackmann import (
    player_form_from_matches,
    surface_elo_ratings,
)


def matches():
    return pd.DataFrame(
        [
            {
                "tourney_date": 20260101,
                "surface": "Hard",
                "score": "6-4 6-4",
                "winner_id": 1,
                "loser_id": 2,
                "w_svpt": 60,
                "w_1stWon": 28,
                "w_2ndWon": 12,
                "l_svpt": 60,
                "l_1stWon": 22,
                "l_2ndWon": 10,
            },
            {
                "tourney_date": 20260201,
                "surface": "Hard",
                "score": "RET",
                "winner_id": 2,
                "loser_id": 1,
                "w_svpt": 10,
                "w_1stWon": 5,
                "w_2ndWon": 2,
                "l_svpt": 8,
                "l_1stWon": 1,
                "l_2ndWon": 1,
            },
            {
                "tourney_date": 20270101,
                "surface": "Hard",
                "score": "6-0 6-0",
                "winner_id": 2,
                "loser_id": 1,
                "w_svpt": 40,
                "w_1stWon": 30,
                "w_2ndWon": 8,
                "l_svpt": 40,
                "l_1stWon": 2,
                "l_2ndWon": 1,
            },
        ]
    )


def test_loader_excludes_retirements_and_future_matches() -> None:
    form = player_form_from_matches(matches(), 1, "Player One", "hard", date(2026, 7, 1))
    assert form.serve_points == 60
    assert form.serve_points_won == 40 / 60
    assert form.return_points == 60
    assert form.return_points_won == 28 / 60


def test_surface_elo_is_chronological_and_excludes_retirements() -> None:
    ratings = surface_elo_ratings(matches(), "hard", date(2026, 7, 1))
    assert ratings["1"] > 1500
    assert ratings["2"] < 1500
