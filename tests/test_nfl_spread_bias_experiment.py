"""A spread bias experiment may learn only from earlier settled dates."""

from datetime import UTC, date, datetime

from model_prediction.models.nfl_structural_v5 import discrete_spread_prob
from scripts.evaluate_nfl_spread_bias_v1 import SpreadCase, _selected_probability, walkforward


def _case(day: int, decision_hour: int, settled_day: int, error: float) -> SpreadCase:
    return SpreadCase(
        event_id=f"game-{day}-{decision_hour}",
        event_date=date(2026, 11, day),
        decision_at=datetime(2026, 11, day, decision_hour, tzinfo=UTC),
        settled_at=datetime(2026, 11, settled_day, 23, tzinfo=UTC),
        artifact_hash="same",
        mean=0.0,
        sd=14.5,
        home_line=-3.5,
        selection="home",
        incumbent_probability=0.55,
        outcome=1,
        actual_margin=error,
        quote_probability=0.51,
    )


def test_walkforward_excludes_same_date_and_later_settlement() -> None:
    cases = [
        _case(1, 12, 1, 10.0),
        _case(2, 12, 3, -20.0),
        _case(2, 13, 2, 30.0),
        _case(3, 12, 3, 0.0),
    ]
    rows = walkforward(cases)
    assert [row["event_id"] for row in rows] == ["game-2-12", "game-2-13", "game-3-12"]
    assert rows[0]["training_games"] == 1
    assert rows[0]["bias_points"] == 10.0
    assert rows[1]["bias_points"] == 10.0
    assert rows[2]["training_games"] == 2
    assert rows[2]["bias_points"] == 20.0


def test_zero_bias_reproduces_the_saved_line_orientation() -> None:
    case = _case(1, 12, 1, 0.0)
    home, away, _ = discrete_spread_prob(case.home_line, case.mean, case.sd)
    assert _selected_probability(case, 0.0) == home / (home + away)
