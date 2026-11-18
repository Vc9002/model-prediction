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
    def settle(self, pick_id, away, home, line, odds, **kwargs):
        self.args = (away, home)
        return {"result": "win"}

    def void(self, pick_id, reason):
        self.voided = reason
        return {"result": "void"}


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


@pytest.fixture(autouse=True)
def _no_closing_line_lookup(monkeypatch):
    # The settle path also looks up a closing probability on disk; keep these tests hermetic.
    monkeypatch.setattr(
        "model_prediction.cli.settle._closing_probability_for_moneyline_pick",
        lambda *args, **kwargs: (None, None),
    )


ROW = {
    "pick_id": "p",
    "event_start_utc": "2026-10-22T18:00:00Z",
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
    # A terminal market with non-binary prices is voided cleanly, never guessed.
    ledger = _Ledger()
    out = _settle_by_resolution(ROW, ledger, _Client("MARKET_STATE_EXPIRED", [0.4, 0.6], sides))
    assert out["voided"] and "non-binary" in ledger.voided


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


@pytest.mark.parametrize(
    ("ledger_team", "market_sides", "winner"),
    [
        # Real ledger-vs-Polymarket spellings that returned "resolution side unresolved" and
        # would have voided winning picks after 7 days (2026-11-12/13).
        ("Oerebro Hockey", ["Oerebro HK", "Bjoerkloeven IF"], "Oerebro HK"),
        ("Anadolu Efes Istanbul", ["Anadolu Efes SK", "Virtus Bologna"], "Anadolu Efes SK"),
        ("Besiktas Istanbul", ["Besiktas JK", "LDLC ASVEL"], "Besiktas JK"),
    ],
)
def test_moneyline_resolution_matches_spelling_variants_by_name_tokens(ledger_team, market_sides, winner):
    row = {**ROW, "market_type": "moneyline", "selection": "away", "away_team": ledger_team, "home_team": "Z"}
    sides = [(name, name) for name in market_sides]
    prices = [1.0 if name == winner else 0.0 for name in market_sides]
    ledger = _Ledger()
    done = _settle_by_resolution(row, ledger, _Client("MARKET_STATE_EXPIRED", prices, sides))
    assert done["settled"]
    assert ledger.args == (1, 0)  # the away pick won


def test_name_token_fallback_fails_closed_on_ties_and_no_overlap():
    # Both sides share only "Istanbul" with the ledger team: ambiguous, so stay pending.
    row = {
        **ROW,
        "market_type": "moneyline",
        "selection": "away",
        "away_team": "Istanbul Basket",
        "home_team": "Z",
    }
    tie = [("Istanbul Spor", "Istanbul Spor"), ("Istanbul United", "Istanbul United")]
    out = _settle_by_resolution(row, _Ledger(), _Client("MARKET_STATE_EXPIRED", [1.0, 0.0], tie))
    assert out["reason"].startswith("resolution side unresolved")
    # No distinctive token in common at all.
    none = [("Alpha Club", "Alpha Club"), ("Omega Club", "Omega Club")]
    out = _settle_by_resolution(row, _Ledger(), _Client("MARKET_STATE_EXPIRED", [1.0, 0.0], none))
    assert out["reason"].startswith("resolution side unresolved")


def test_spread_resolution_matches_spelling_variant_and_keeps_line_check():
    row = {
        **ROW,
        "market_type": "spread",
        "selection": "away",
        "line": 1.5,
        "away_team": "Besiktas Istanbul",
        "home_team": "LDLC ASVEL Villeurbanne",
    }
    sides = [("+1.50", "Besiktas JK"), ("-1.50", "LDLC ASVEL")]
    ledger = _Ledger()
    assert _settle_by_resolution(row, ledger, _Client("MARKET_STATE_EXPIRED", [1.0, 0.0], sides))["settled"]
    away, home = ledger.args
    assert grade_pick(MarketType.SPREAD, "away", 1.5, away, home) is PickResult.WIN
