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


def test_soccer_moneyline_draw_is_a_loss_not_a_push() -> None:
    # Polymarket's soccer win market is three independent Yes/No contracts
    # (home wins / draw / away wins), not one 2-outcome market with a tie
    # case -- a "home" pick that draws is a losing YES contract, a full
    # stake loss, not a refunded push.
    assert grade_pick(MarketType.MONEYLINE, "home", None, 1, 1, league="SOCCER") is PickResult.LOSS
    assert grade_pick(MarketType.MONEYLINE, "away", None, 2, 2, league="SOCCER") is PickResult.LOSS
    assert profit_units(PickResult.LOSS, 1.0, 1.91) == -1.0


def test_non_soccer_moneyline_tie_is_still_a_push() -> None:
    # KBO/NPB really are 2-outcome markets settled 50/50 on a tie (handled
    # separately via binary_contract_settlement_value in ledger.settle) --
    # grade_pick itself must keep returning PUSH for the generic tie case
    # so that special-casing still applies. A bare MONEYLINE tie with no
    # league (or a non-soccer league) must not regress to LOSS either.
    assert grade_pick(MarketType.MONEYLINE, "home", None, 3, 3) is PickResult.PUSH
    assert grade_pick(MarketType.MONEYLINE, "home", None, 3, 3, league="KBO") is PickResult.PUSH
    assert grade_pick(MarketType.MONEYLINE, "away", None, 3, 3, league="NPB") is PickResult.PUSH
