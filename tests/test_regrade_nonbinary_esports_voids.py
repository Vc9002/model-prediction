from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.regrade_nonbinary_esports_voids import (
    VOID_SIGNATURE,
    is_candidate,
    plan,
)

_TEAMS = {
    "t-home": {"name": "Home Squad", "slug": "home-squad", "acronym": "HSQ"},
    "t-away": {"name": "Away Squad", "slug": "away-squad", "acronym": "ASQ"},
}


class _FakeBo3:
    def __init__(self, matches: list[dict[str, Any]]) -> None:
        self._matches = matches

    def teams(self, title: str) -> tuple[dict, int]:
        return _TEAMS, len(_TEAMS)

    def finished_matches(self, title: str, start: Any, end: Any) -> tuple[list[dict], int]:
        return self._matches, len(self._matches)


def _match(match_id: str, start: str, winner: str = "t-home") -> dict[str, Any]:
    return {
        "match_id": match_id,
        "start_utc": start,
        "team1_id": "t-home",
        "team2_id": "t-away",
        "winner_id": winner,
    }


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "pick_id": "pick-1",
        "ledger_tier": "flat",
        "sport": "cs2",
        "league": "CS2",
        "event_id": "ev-1",
        "event_start_utc": "2026-10-05T16:00:00Z",
        "home_team": "Home Squad",
        "away_team": "Away Squad",
        "market_type": "moneyline",
        "selection": "home",
        "status": "settled",
        "result": "push",
        "pnl_units": "0.0000",
        "void_reason": f"{VOID_SIGNATURE} (forfeit/postponement per market rules); BO3 has none",
    }
    row.update(overrides)
    return row


def test_candidate_is_the_stored_signature_on_a_voided_esports_row() -> None:
    assert is_candidate(_row())
    assert not is_candidate(_row(result="win"))
    assert not is_candidate(_row(status="open"))
    assert not is_candidate(_row(void_reason="event status_postponed"))
    assert not is_candidate(_row(league="DARTS"))  # not an esports title


def test_plan_regrades_when_one_series_near_the_event_confirms_a_winner() -> None:
    bo3 = _FakeBo3([_match("bo3:1", "2026-10-05T14:30:00Z", winner="t-away")])
    actions, skipped = plan([_row()], bo3, Path("."))
    assert skipped == []
    (action,) = actions
    assert (action.away_score, action.home_score) == (1, 0)
    assert action.bo3_match_id == "bo3:1"


def test_plan_ignores_a_rematch_days_away_and_leaves_the_row_void() -> None:
    bo3 = _FakeBo3([_match("bo3:late", "2026-10-09T16:00:00Z")])
    actions, skipped = plan([_row()], bo3, Path("."))
    assert actions == []
    assert "no finished BO3 series" in skipped[0].reason


def test_plan_leaves_the_row_void_when_two_series_match_in_the_window() -> None:
    bo3 = _FakeBo3([_match("bo3:a", "2026-10-05T10:00:00Z"), _match("bo3:b", "2026-10-05T20:00:00Z")])
    actions, skipped = plan([_row()], bo3, Path("."))
    assert actions == []
    assert "2 BO3 series" in skipped[0].reason


def test_plan_leaves_unresolvable_team_and_non_moneyline_rows_void() -> None:
    bo3 = _FakeBo3([_match("bo3:1", "2026-10-05T16:00:00Z")])
    actions, skipped = plan(
        [_row(home_team="Nobody Known"), _row(pick_id="pick-2", market_type="total")], bo3, Path(".")
    )
    assert actions == []
    assert {s.pick_id: s.reason for s in skipped} == {
        "pick-1": "team name does not resolve to a BO3 team",
        "pick-2": "not a moneyline pick",
    }
