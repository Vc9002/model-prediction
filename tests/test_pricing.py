from model_prediction.domain import MarketType, PickResult
from model_prediction.pricing import (
    american_to_decimal,
    grade_pick,
    implied_probability,
    normalize_no_vig,
    profit_units,
)


def test_odds_conversion() -> None:
    assert american_to_decimal(-110) == 1 + 100 / 110
    assert american_to_decimal(150) == 2.5
    assert round(implied_probability(-110), 6) == 0.52381


def test_grades_moneyline_spread_total_and_push() -> None:
    assert grade_pick(MarketType.MONEYLINE, "home", None, 3, 5) is PickResult.WIN
    assert grade_pick(MarketType.SPREAD, "away", 2.5, 100, 101) is PickResult.WIN
    assert grade_pick(MarketType.TOTAL, "under", 8.5, 3, 4) is PickResult.WIN
    assert grade_pick(MarketType.TOTAL, "over", 8, 3, 5) is PickResult.PUSH
    assert profit_units(PickResult.LOSS, 1.5, 1.91) == -1.5


def test_no_vig_normalization() -> None:
    probabilities = normalize_no_vig((implied_probability(-110), implied_probability(-110)))
    assert probabilities == (0.5, 0.5)


def test_invalid_american_odds_dead_zone_and_negative_spread() -> None:
    assert american_to_decimal(100) == 2.0
    assert american_to_decimal(-100) == 2.0
    assert grade_pick(MarketType.SPREAD, "home", -2.5, 100, 103) is PickResult.WIN
    assert grade_pick(MarketType.SPREAD, "home", -3, 100, 103) is PickResult.PUSH
