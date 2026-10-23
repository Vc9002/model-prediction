"""Settlement from Polymarket contract resolution when no results API covers the league."""

from __future__ import annotations

import pytest

from model_prediction.cli.settle import _scores_for_outcome, _settle_by_resolution
from model_prediction.domain import MarketType
from model_prediction.pricing import PickResult, grade_pick


@pytest.mark.parametrize(
    "market,selection,line",
    [
        ("total", "over", 3.5),
        ("total", "under", 3.5),
        ("total", "over", 6.5),
        ("total", "under", 2.5),
        ("spread", "home", -1.5),
        ("spread", "away", 1.5),
        ("spread", "home", 2.5),
        ("spread", "away", -3.5),
        ("moneyline", "home", None),
        ("moneyline", "away", None),
    ],
)
def test_synthetic_scores_grade_to_the_resolved_outcome(market, selection, line):
    for won in (True, False):
        away, home = _scores_for_outcome(market, selection, line, won)
        result = grade_pick(MarketType(market), selection, line, away, home)
        assert result is (PickResult.WIN if won else PickResult.LOSS), (
            market,
            selection,
            line,
            won,
            away,
            home,
        )


class _Ledger:
    def settle(self, pick_id, away, home, line, odds):
        self.args = (away, home)
        return {"result": "win"}


class _Client:
    def __init__(self, state, prices, sides):
        self.state, self.prices, self.sides = state, prices, sides

    def market(self, slug):
        return {
            "marketSides": [
                {"description": d, "price": {"value": str(p)}, "team": {"name": t} if t else None}
                for (d, t), p in zip(self.sides, self.prices)
            ]
        }

    def book(self, slug):
        return {"state": self.state}


ROW = {
    "pick_id": "p",
    "market_type": "total",
    "selection": "under",
    "line": 3.5,
    "home_team": "A",
    "away_team": "B",
    "rationale": "x (tsc-khl-a-b-2026-10-22-3pt5).",
}


def test_total_resolution_settles_and_unresolved_or_unclean_stay_pending():
    sides = [("Over", None), ("Under", None)]
    ledger = _Ledger()
    done = _settle_by_resolution(ROW, ledger, _Client("MARKET_STATE_EXPIRED", [0.0, 1.0], sides))
    assert done["settled"] and ledger.args == (0, 0)  # under won -> total below the line
    assert _settle_by_resolution(ROW, _Ledger(), _Client("MARKET_STATE_OPEN", [0.0, 1.0], sides)) is None
    assert _settle_by_resolution(ROW, _Ledger(), _Client("MARKET_STATE_EXPIRED", [0.4, 0.6], sides)) is None


def test_spread_resolution_matches_team_and_line():
    row = {
        **ROW,
        "market_type": "spread",
        "selection": "away",
        "line": 1.5,
        "home_team": "Home Club",
        "away_team": "Away Club",
    }
    sides = [("+1.50", "Away Club"), ("-1.50", "Home Club")]
    ledger = _Ledger()
    assert _settle_by_resolution(row, ledger, _Client("MARKET_STATE_EXPIRED", [1.0, 0.0], sides))["settled"]
    away, home = ledger.args
    assert grade_pick(MarketType.SPREAD, "away", 1.5, away, home) is PickResult.WIN
