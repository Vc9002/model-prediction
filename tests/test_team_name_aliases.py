"""ESPN-style NCAAF names must resolve to Polymarket US names in both matchers."""

from __future__ import annotations

import importlib

import pytest

from model_prediction.data_sources import polymarket_execute

backtests = importlib.import_module("model_prediction.dashboard.backtests")

PAIRS = [
    ("Massachusetts Minutemen", "UMass"),
    ("App State Mountaineers", "Appalachian State"),
    ("Central Connecticut Blue Devils", "Central Connecticut State"),
    ("Grambling Tigers", "Grambling State"),
    ("Long Island University Sharks", "LIU"),
    ("Miami Hurricanes", "Miami (FL)"),
    ("San Jos� State Spartans", "San Jose State"),
    ("San José State Spartans", "San Jose State"),
    ("UL Monroe Warhawks", "Louisiana-Monroe"),
    ("SE Louisiana Lions", "Southeastern Louisiana"),
]


@pytest.mark.parametrize(("espn", "polymarket"), PAIRS)
def test_aliases_match_in_both_matchers(espn: str, polymarket: str) -> None:
    assert backtests._team_matches(espn, polymarket)
    assert polymarket_execute._team_name_matches(espn, polymarket)


def test_aliases_do_not_merge_distinct_teams() -> None:
    assert not backtests._team_matches("Miami Hurricanes", "Miami (OH)")
    assert not backtests._team_matches("Massachusetts Minutemen", "Massachusetts Lowell")
    assert not polymarket_execute._team_name_matches("Miami Hurricanes", "Miami (OH)")
