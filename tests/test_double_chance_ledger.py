from dataclasses import replace

import pytest

from model_prediction.domain import League, MarketType, PickResult
from model_prediction.pricing import grade_pick
from tests.test_ledger import request


@pytest.mark.parametrize(
    "selection,away,home,win",
    [
        ("1X", 0, 1, True),
        ("1X", 1, 1, True),
        ("1X", 1, 0, False),
        ("X2", 0, 1, False),
        ("X2", 1, 1, True),
        ("X2", 1, 0, True),
        ("12", 0, 1, True),
        ("12", 1, 1, False),
        ("12", 1, 0, True),
    ],
)
def test_double_chance_validates_and_grades_each_outcome(selection, away, home, win):
    pick = replace(
        request(), league=League.SOCCER, market_type=MarketType.DOUBLE_CHANCE, selection=selection, line=None
    )
    pick.validate()
    assert grade_pick(pick.market_type, selection, None, away, home, "SOCCER") == (
        PickResult.WIN if win else PickResult.LOSS
    )


def test_double_chance_rejects_a_total_line():
    pick = replace(request(), market_type=MarketType.DOUBLE_CHANCE, selection="1X", line=2.5)
    with pytest.raises(ValueError, match="must not have a line"):
        pick.validate()
