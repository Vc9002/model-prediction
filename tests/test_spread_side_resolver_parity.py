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
import json
from pathlib import Path

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


# ------------------------------------------------------------------ real NCAAB triples
#
# tests/fixtures/ncaab_spread_side_triples.json: 602 (away, home, polymarket team, line, selection,
# row line, event title) triples cut from the real data, each with the side it must resolve to.
# 150 real games are sampled at random for the broad group (3 selection/line combinations each), and
# every game involving one of the 13 new aliases, UML vs UMass, the one mislabelled ledger row and
# every game whose two schools share a place name are included whole.
#
# Where the inputs came from (both read-only, 2026-11-25):
#   - ESPN names (away_team/home_team): the decision payloads of the NCAAB spread rows in
#     ledgers.db, joined to the Polymarket event by event_id (695 of 961 spread events join; the
#     same id keys both). The few schools whose only snapshot games have no ledger row (EIU, UML,
#     "Tenn. State", "Ore. State" and their opponents) take their name from the same school's other
#     ledger rows.
#   - Polymarket team, line, descriptions and event title: odds/ncaab/*/polymarket_snapshots.jsonl
#     spread rows (valid timestamps only).
#
# How the expected side was derived, WITHOUT the code under test:
#   1. A spread snapshot's team and line are its LONG side; SHORT is the opponent at the negated
#      line. That comes from the data, not from match_spread_side's docstring: on all 92,050 valid
#      pre-start spread rows long.description parses to `line` and short.description to `-line`
#      (asserted below on every triple), and in 629 of the 630 (event, team) ladders with three or
#      more rungs the long ask rises with the line (0 fall, 1 flat), as it must if the team is the
#      one that has to cover that line; it would fall if `team` were the short side.
#   2. The event title lists the two teams "away vs. home", an orientation that holds on all 616
#      events whose ledger names decide it (0 reversed; the other 44 differ only by abbreviation, e.g.
#      "FDU vs. Butler" against "Fairleigh Dickinson Knights").
#   3. The market's team is either exactly one title participant, or an abbreviation whose school was
#      identified by hand (UCA, NAU, EIU, UML, Tenn. State, Ore. State, plus two long-form spellings).
#      That fixes whether it is the away or the home team.
#   4. So a row on the market team at the market line is LONG, a row on the opponent at the negated
#      line is SHORT, and every other (selection, line) pair is no match.
# "truth_side" holds that; "expected_side" differs only where the resolver refuses on purpose (UML in
# UMass Lowell vs UMass matches both row teams), and "expected_mapper_side" differs only for the one
# real ledger row whose home team is mislabelled (Central Connecticut for UConn), which only the
# mapper's title check can see.

_FIXTURE = Path(__file__).parent / "fixtures" / "ncaab_spread_side_triples.json"
_TRIPLES = json.loads(_FIXTURE.read_text(encoding="utf-8"))["triples"]
# The 13 Polymarket names added to the alias table on 2026-11-25.
_NEW_ALIASES = [
    "UCA", "NAU", "EIU", "UML", "Tenn. State", "Ore. State", "UNCW", "FDU", "Penn",
    "St. Thomas (MN)", "USC Upstate", "ARK-Pine Bluff", "Mich. State",
]  # fmt: skip


def _triple_id(t: dict) -> str:
    return f"{t['event_id']}-{t['polymarket_team']}-{t['selection']}-{t['row_line']}"


def _triple_inputs(t: dict) -> tuple[dict, dict]:
    row = {
        "league": "NCAAB",
        "market_type": "spread",
        "away_team": t["away"],
        "home_team": t["home"],
        "selection": t["selection"],
        "line": str(t["row_line"]),
    }
    snapshot = {
        "event_title": t["event_title"],
        "market_type": "spread",
        "team": t["polymarket_team"],
        "line": t["market_line"],
    }
    return row, snapshot


@pytest.mark.parametrize("triple", _TRIPLES, ids=_triple_id)
def test_real_triples_resolve_the_same_side_in_the_core_the_gate_and_the_mapper(triple: dict) -> None:
    row, snapshot = _triple_inputs(triple)

    core = polymarket_matching.match_spread_side(
        row, triple["row_line"], triple["polymarket_team"], triple["market_line"]
    ).side
    gate = _gate_side(row, {k: v for k, v in snapshot.items() if k != "event_title"})
    mapper = backtests._spread_side_for_row(row, snapshot)

    # The data's own long/short convention the truth rests on (see the notes above).
    assert float(triple["long_description"]) == triple["market_line"]
    assert float(triple["short_description"]) == -triple["market_line"]
    assert core == triple["expected_side"]
    assert gate == triple["expected_side"]
    assert mapper == triple["expected_mapper_side"]
    # Fail closed is allowed where documented; a flipped side never is.
    for got in (core, gate, mapper):
        assert got in (None, triple["truth_side"])


def test_fixture_covers_every_new_alias_the_refusals_and_a_broad_sample() -> None:
    def names_in(t: dict) -> set[str]:
        return {t["polymarket_team"], *t["event_title"].split(" vs. ")}

    for alias in _NEW_ALIASES:
        sided = [t for t in _TRIPLES if alias in names_in(t) and t["expected_side"]]
        assert {t["expected_side"] for t in sided} == {"long", "short"}, alias

    uml = [
        t for t in _TRIPLES if t["polymarket_team"] == "UML" and t["event_title"] == "UMass Lowell vs. UMass"
    ]
    assert uml
    assert any(t["truth_side"] and t["expected_side"] is None and t["refusal_reason"] for t in uml)

    broad = [t for t in _TRIPLES if t["group"] == "broad"]
    assert len({t["event_id"] for t in broad}) >= 150
    assert {t["expected_side"] for t in broad} == {"long", "short", None}
    assert any(t["expected_mapper_side"] != t["expected_side"] for t in _TRIPLES)  # the mislabelled row
