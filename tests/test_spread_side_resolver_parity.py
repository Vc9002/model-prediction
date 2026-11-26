"""The dashboard quote mapper (dashboard/backtests.py::_spread_side_for_row) picks the market and side
the auto-buyer orders; the execution gate (polymarket_execute._resolve_spread_side) re-checks that side
against a live quote. Both now run polymarket_matching.match_spread_side. Before 2026-11-25 they were
two hand-kept copies, 42-entry alias table included, and nothing held them together.

These tests pin that the two public behaviours agree on every case: same long/short answer, and the
gate refuses exactly where the mapper returns None.
"""

from __future__ import annotations

import importlib
import itertools

import pytest

from model_prediction import polymarket_matching
from model_prediction.data_sources import polymarket_execute as pe

# Imported by path: the dashboard package re-exports a function named `backtests`.
backtests = importlib.import_module("model_prediction.dashboard.backtests")

# (away, home, market teams to try): real Polymarket US spread "team" strings, including the
# abbreviations and lookalike schools that caused or nearly caused wrong-side buys.
_GAMES = [
    ("Central Arkansas Bears", "Jacksonville Dolphins", ["UCA", "Central Arkansas", "Jacksonville"]),
    ("Louisiana-Monroe", "Louisiana", ["Louisiana", "Louisiana-Monroe"]),
    ("Texas Southern Tigers", "Texas Longhorns", ["Texas", "Texas Southern"]),
    ("UMass Lowell River Hawks", "Massachusetts Minutemen", ["UML", "UMass Lowell", "UMass"]),
    ("Los Angeles Dodgers", "Chicago Cubs", ["Los Angeles Dodgers", "Chicago Cubs", "Dodgers"]),
    ("UConn Huskies", "Central Connecticut Blue Devils", ["UConn", "Central Connecticut State"]),
    ("Miami Hurricanes", "Miami (OH) RedHawks", ["Miami (FL)", "Miami (OH)"]),
]
_LINES = [-3.5, 3.5, -1.5, 0.5]


def _gate_side(row: dict, snapshot: dict) -> str | None:
    try:
        return pe._resolve_spread_side(row, snapshot)
    except pe.ExecutionGateError:
        return None


def _cases():
    for (away, home, market_teams), market_line, selection, row_line in itertools.product(
        _GAMES, _LINES, ("away", "home"), _LINES
    ):
        for team in market_teams:
            row = {
                "league": "NCAAB",
                "market_type": "spread",
                "away_team": away,
                "home_team": home,
                "selection": selection,
                "line": str(row_line),
            }
            snapshot = {
                "event_title": f"{away} vs. {home}",
                "market_type": "spread",
                "team": team,
                "line": market_line,
                "long": {"description": f"{market_line:+.2f}", "ask": 0.52},
                "short": {"description": f"{-market_line:+.2f}", "ask": 0.50},
            }
            yield row, snapshot


def test_mapper_and_gate_agree_on_every_case() -> None:
    disagreements = []
    resolved = 0
    for row, snapshot in _cases():
        mapper = backtests._spread_side_for_row(row, snapshot)
        gate = _gate_side(row, snapshot)
        resolved += mapper is not None
        if mapper != gate:
            disagreements.append((row["away_team"], row["home_team"], snapshot["team"], row, mapper, gate))
    assert disagreements == []
    # The grid must exercise both outcomes, or agreement proves nothing.
    assert resolved > 50


def test_gate_does_not_need_an_event_title() -> None:
    """The live snapshot the gate fetches (PolymarketUSClient.snapshot) has no event_title, so the
    shared core must not check the title; only the dashboard mapper does, before calling it."""
    row = {
        "league": "MLB",
        "away_team": "Los Angeles Dodgers",
        "home_team": "Chicago Cubs",
        "selection": "home",
        "line": "1.5",
    }
    live = {"market_type": "spread", "team": "Los Angeles Dodgers", "line": -1.5}

    assert pe._resolve_spread_side(row, live) == "short"
    assert backtests._spread_side_for_row(row, live) is None  # mapper still requires the game


def test_both_modules_run_the_one_shared_matcher() -> None:
    assert backtests._team_matches is polymarket_matching.team_matches
    assert pe._team_name_matches is polymarket_matching.team_matches
    assert backtests._participant_matches is pe._participant_matches
    assert not hasattr(pe, "_TEAM_NAME_ALIASES")
    assert not hasattr(backtests, "_TEAM_NAME_ALIASES")


@pytest.mark.parametrize(("team", "expected"), [("neither", "neither"), ("both", "both")])
def test_gate_names_the_team_refusal(team: str, expected: str) -> None:
    row = {"away_team": "Louisiana-Monroe", "home_team": "Louisiana", "selection": "away", "line": "-2.5"}
    market_team = "Louisiana" if team == "both" else "Nowhere State"
    with pytest.raises(pe.ExecutionGateError, match=f"matches {expected} of the row's teams"):
        pe._resolve_spread_side(row, {"team": market_team, "line": -2.5})
