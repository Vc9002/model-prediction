"""Focused invariants for the retrospective possession challenger."""

from scripts.test_ncaab_possession_v4 import _features, _ratings


def test_same_day_results_do_not_enter_features() -> None:
    first = {
        "id": "1",
        "day": "2025-11-01",
        "home": "A",
        "away": "B",
        "pace": 70,
        "home_eff": 130,
        "away_eff": 90,
    }
    second = {
        "id": "2",
        "day": "2025-11-01",
        "home": "A",
        "away": "B",
        "pace": 80,
        "home_eff": 80,
        "away_eff": 120,
    }
    next_day = {
        "id": "3",
        "day": "2025-11-02",
        "home": "A",
        "away": "B",
        "pace": 75,
        "home_eff": 100,
        "away_eff": 100,
    }
    features = _features([first, second, next_day])
    assert features["1"] == features["2"]
    assert features["1"]["min_games"] == 0
    assert features["3"]["min_games"] == 2


def test_opponent_adjustment_shrinks_single_game() -> None:
    game = {
        "id": "1",
        "day": "2025-11-01",
        "home": "A",
        "away": "B",
        "pace": 70,
        "home_eff": 130,
        "away_eff": 90,
    }
    ratings = _ratings([game])
    assert ratings["A"][0] > ratings["B"][0]
    assert ratings["A"][1] < ratings["B"][1]
    assert ratings["A"][3] == ratings["B"][3] == 1
