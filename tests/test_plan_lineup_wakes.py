from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "plan_lineup_wakes",
    Path(__file__).resolve().parents[1] / "scripts" / "plan_lineup_wakes.py",
)
plan_lineup_wakes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plan_lineup_wakes)

plan_wakes = plan_lineup_wakes.plan_wakes
WAKE_LEAD_MINUTES = plan_lineup_wakes.WAKE_LEAD_MINUTES

# Fixed reference instant: 2026-08-19 00:00 UTC == 2026-08-18 20:00 EDT.
NOW = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)


def _game(game_pk: int, start_utc: str, state: str = "Pre-Game") -> dict:
    return {
        "gamePk": game_pk,
        "gameDate": start_utc,
        "status": {"detailedState": state},
        "teams": {
            "away": {"team": {"name": f"Away{game_pk}"}},
            "home": {"team": {"name": f"Home{game_pk}"}},
        },
    }


class _Client:
    def __init__(self, games: list[dict]) -> None:
        self._games = games

    def schedule(self, game_date: str) -> list[dict]:
        return [g for g in self._games if str(g["gameDate"]).startswith(game_date)]


def _plan(games, **kwargs):
    return plan_wakes(client=_Client(games), now=NOW, **kwargs)


def test_a_late_west_coast_game_inside_the_sleep_window_gets_a_wake() -> None:
    """06:10 UTC == 02:10 EDT, inside the 01:00-09:00 sleep window. This is
    exactly the systematically-missed case: without a wake, launchd
    coalesces the missed hourly firings into one run after the machine
    wakes, long after first pitch."""
    wakes = _plan([_game(1, "2026-08-19T06:10:00Z")])

    assert len(wakes) == 1
    expected = datetime(2026, 8, 19, 6, 10, tzinfo=UTC) - timedelta(minutes=WAKE_LEAD_MINUTES)
    assert wakes[0]["wake_local"] == expected.astimezone()
    assert wakes[0]["covers"] == ["Away1 @ Home1"]


def test_an_evening_game_outside_the_sleep_window_needs_no_wake() -> None:
    """23:05 UTC == 19:05 EDT. The ordinary hourly firing already covers
    it; scheduling a wake would be pointless power churn."""
    assert _plan([_game(1, "2026-08-19T23:05:00Z")]) == []


def test_games_close_together_coalesce_into_one_wake() -> None:
    """One collector run captures the whole slate, so two games eight
    minutes apart must not produce two wakes."""
    wakes = _plan([_game(1, "2026-08-19T06:10:00Z"), _game(2, "2026-08-19T06:18:00Z")])

    assert len(wakes) == 1
    assert sorted(wakes[0]["covers"]) == ["Away1 @ Home1", "Away2 @ Home2"]


def test_games_far_apart_get_their_own_wakes() -> None:
    # 06:00Z -> 01:25 EDT and 09:00Z -> 04:25 EDT, both inside the window,
    # three hours apart.
    wakes = _plan([_game(1, "2026-08-19T06:00:00Z"), _game(2, "2026-08-19T09:00:00Z")])

    assert len(wakes) == 2


def test_a_wake_landing_just_before_the_sleep_window_is_left_to_the_hourly_job() -> None:
    """05:00Z wakes at 00:25 EDT, 35 minutes before the 01:00 window opens.
    The machine is still awake then, so the ordinary hourly firing covers
    it and no power event is needed."""
    assert _plan([_game(1, "2026-08-19T05:00:00Z")]) == []


def test_a_started_game_is_never_scheduled_for() -> None:
    """Its order is already recoverable from the final boxscore; waking the
    machine for it buys nothing."""
    assert _plan([_game(1, "2026-08-19T06:10:00Z", state="In Progress")]) == []


def test_a_window_already_past_is_not_scheduled() -> None:
    """A game starting 10 minutes from now cannot have a useful wake
    scheduled 35 minutes before it — that moment is in the past."""
    soon = (NOW + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    assert _plan([_game(1, soon)]) == []


def test_tomorrow_utc_is_searched_so_late_local_games_are_not_missed() -> None:
    """A late local game belongs to the NEXT UTC date: 2026-08-20T06:10Z is
    01:35 EDT on the 20th. Searching only today's UTC schedule would
    silently miss precisely the late games this planner exists to
    protect."""
    wakes = _plan([_game(1, "2026-08-20T06:10:00Z")])

    assert len(wakes) == 1
    assert wakes[0]["covers"] == ["Away1 @ Home1"]
