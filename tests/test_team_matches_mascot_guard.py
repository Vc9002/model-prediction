"""The last-token (mascot) rule of polymarket_matching.team_matches skipped the prefix/suffix guards
the contiguous match applies, so a bare school name matched the Polymarket abbreviation of a
different school that merely contains it: "Arkansas" matched "UCA" (Central Arkansas), "Illinois"
matched "EIU", "Michigan" matched "Mich. State". Through the dashboard mapper that is a spread row
for one game quoted against another game's market, on the other team's side.
"""

from __future__ import annotations

import importlib

import pytest

from model_prediction.data_sources import polymarket_execute as pe
from model_prediction.polymarket_matching import team_matches

backtests = importlib.import_module("model_prediction.dashboard.backtests")


@pytest.mark.parametrize(
    ("bare", "abbreviation"),
    [
        ("Arkansas", "UCA"),
        ("Illinois", "EIU"),
        ("Arizona", "NAU"),
        ("Michigan", "Mich. State"),
        ("Oregon", "Ore. State"),
        ("Tennessee", "Tenn. State"),
    ],
)
def test_a_bare_school_name_does_not_match_another_school_that_contains_it(bare, abbreviation) -> None:
    assert not team_matches(bare, abbreviation)


@pytest.mark.parametrize(
    ("team", "description"),
    [
        ("Arkansas", "Arkansas Razorbacks"),  # contiguous, no guard involved
        ("Central Arkansas Bears", "Bears"),  # moneyline descriptions are the mascot alone
        ("Denver Nuggets", "DEN Nuggets"),
        ("Golden State Warriors", "GS Warriors"),
        ("Los Angeles Dodgers", "Dodgers"),
        # A prefix word on the TEAM side is part of its name, not a different school, when the
        # description is the mascot alone (a moneyline's long/short description).
        ("Texas Southern Tigers", "Tigers"),
        ("Georgia Southern Eagles", "Eagles"),
        ("Charleston Southern Buccaneers", "Buccaneers"),
    ],
)
def test_the_mascot_rule_still_matches_when_nothing_disqualifies_it(team, description) -> None:
    assert team_matches(team, description)


def test_a_disqualifying_word_before_the_mascot_blocks_the_mascot_rule() -> None:
    # "Western Warriors" is another school; the franchise-style mascot match must not bridge it.
    assert not team_matches("Golden State Warriors", "Western Warriors")


def test_a_bare_name_cannot_pull_a_row_onto_another_games_market() -> None:
    """Row: Arkansas at Jacksonville. Market: Central Arkansas vs Jacksonville ("UCA", -3.5). Before
    the fix "Arkansas" matched "UCA", the title matched, and the row resolved to a side of a game
    it is not in."""
    row = {
        "league": "NCAAB",
        "away_team": "Arkansas",
        "home_team": "Jacksonville Dolphins",
        "selection": "away",
        "line": "-3.5",
    }
    snapshot = {
        "event_title": "Central Arkansas vs. Jacksonville",
        "market_type": "spread",
        "team": "UCA",
        "line": -3.5,
    }

    assert backtests._spread_side_for_row(row, snapshot) is None
    with pytest.raises(pe.ExecutionGateError):
        pe._resolve_spread_side(row, snapshot)
