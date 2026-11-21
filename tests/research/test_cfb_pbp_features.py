"""Micro-tests for scripts/cfb_pbp_features.py: success rate, garbage time, and pre-play scoring.

The pre-play test pins the one ESPN quirk that silently breaks garbage-time filtering: a play's
homeScore/awayScore are post-play, so the play that crosses a margin threshold is itself not garbage.
"""

from __future__ import annotations

import pytest

from scripts.cfb_pbp_features import game_rows, is_garbage_time, is_success


@pytest.mark.parametrize(
    ("down", "distance", "yards", "expected"),
    [
        (1, 10, 5, True),  # exactly 50%
        (1, 10, 4, False),
        (1, 5, 3, True),  # 2.5 needed, 3 gained
        (1, 5, 2, False),
        (2, 10, 7, True),  # exactly 70%
        (2, 10, 6, False),
        (2, 3, 3, True),  # 2.1 needed
        (2, 3, 2, False),
        (3, 4, 4, True),
        (3, 4, 3, False),
        (4, 1, 1, True),
        (4, 1, 0, False),
        (1, 10, -3, False),
    ],
)
def test_success_thresholds(down: int, distance: int, yards: int, expected: bool) -> None:
    assert is_success(down, distance, yards) is expected


def test_touchdown_is_success_and_turnover_never_is() -> None:
    assert is_success(3, 15, 9, touchdown=True)
    assert not is_success(1, 10, 25, turnover=True)


def test_success_rejects_bad_down() -> None:
    with pytest.raises(ValueError):
        is_success(0, 10, 5)


@pytest.mark.parametrize(
    ("period", "margin", "expected"),
    [
        (1, 50, False),  # never in Q1
        (2, 38, False),  # strict >
        (2, 39, True),
        (2, -39, True),
        (3, 28, False),
        (3, 29, True),
        (4, 22, False),
        (4, -23, True),
        (5, 40, False),  # overtime never
        (None, 40, False),
    ],
)
def test_garbage_time_thresholds(period: int | None, margin: int, expected: bool) -> None:
    assert is_garbage_time(period, margin) is expected


def _play(
    type_text: str,
    team: str,
    period: int,
    down: int,
    distance: int,
    yards: int,
    home: int,
    away: int,
    *,
    turnover: bool = False,
) -> dict:
    return {
        "type": {"text": type_text},
        "period": {"number": period},
        "clock": {"displayValue": "10:00"},
        "homeScore": home,
        "awayScore": away,
        "statYardage": yards,
        "isTurnover": turnover,
        "start": {"down": down, "distance": distance, "yardsToEndzone": 50, "team": {"id": team}},
    }


def _payload(drives: list[dict]) -> dict:
    return {
        "header": {
            "competitions": [
                {
                    "date": "2025-09-06T19:30Z",
                    "neutralSite": False,
                    "competitors": [
                        {"homeAway": "home", "id": "1", "score": "45"},
                        {"homeAway": "away", "id": "2", "score": "0"},
                    ],
                }
            ]
        },
        "drives": {"previous": drives},
    }


def test_pre_play_margin_uses_previous_row_score() -> None:
    # Q4, home leads 16-0 before the drive. The TD play's own row already shows 23-0 (> 22), but its
    # pre-play margin is 16, so it is not garbage; the next home snap (pre-play 23) is.
    # drive0 carries the 16-0 lead in (a field goal row: no scrimmage snaps, so not a counted drive).
    drive0 = {
        "team": {"id": "1"},
        "timeElapsed": {"displayValue": "0:05"},
        "plays": [_play("Field Goal Good", "1", 4, 4, 5, 0, 16, 0)],
    }
    drive1 = {
        "team": {"id": "1"},
        "timeElapsed": {"displayValue": "1:00"},
        "plays": [
            _play("Rush", "1", 4, 1, 10, 0, 16, 0),
            _play("Rushing Touchdown", "1", 4, 2, 10, 30, 23, 0),
        ],
    }
    drive2 = {
        "team": {"id": "1"},
        "timeElapsed": {"displayValue": "0:30"},
        "plays": [
            _play("Kickoff", "2", 4, 1, 10, 0, 23, 0),
            _play("Rush", "1", 4, 1, 10, 6, 23, 0),
        ],
    }
    rows, unknown = game_rows(
        {"event_id": "9", "season": 2025, "game_class": "FBS-FBS"}, _payload([drive0, drive1, drive2])
    )
    home = next(r for r in rows if r["is_home"])
    assert not unknown
    assert home["kickoff_utc"] == "2025-09-06T19:30Z"
    assert home["off_plays"] == 3
    assert home["off_plays_ng"] == 2  # only the post-TD snap is garbage
    assert home["off_successes"] == 2  # TD counts; the 0-yard 1st down does not; 6 of 10 on 1st does
    assert home["off_successes_ng"] == 1
    assert home["off_explosives"] == 1
    assert home["off_drives"] == 2
    assert home["off_points"] == 7  # 16 -> 23 on drive 1, nothing on drive 2
    assert home["off_drives_ng"] == 1  # drive 2 starts in garbage time
    assert home["off_sec_per_play"] == pytest.approx(90 / 3)


def test_turnover_and_non_scrimmage_rows_excluded_from_success() -> None:
    drive = {
        "team": {"id": "2"},
        "timeElapsed": {"displayValue": "0:45"},
        "plays": [
            _play("Pass Reception", "2", 1, 1, 10, 25, 0, 0),
            _play("Penalty", "2", 1, 1, 10, -5, 0, 0),
            _play("Pass Interception Return", "2", 1, 1, 15, 40, 0, 0, turnover=True),
            _play("Timeout", "2", 1, 1, 15, 0, 0, 0),
        ],
    }
    rows, _ = game_rows({"event_id": "9", "season": 2025, "game_class": "FBS-FBS"}, _payload([drive]))
    away = next(r for r in rows if not r["is_home"])
    home = next(r for r in rows if r["is_home"])
    assert away["off_plays"] == 2
    assert away["off_successes"] == 1
    assert away["off_turnovers"] == 1
    assert away["off_yards"] == 25  # interception return yards are not offensive yards
    assert home["def_turnovers"] == 1  # the mirror: home's defense forced it


def test_goal_to_go_distance_zero_uses_yards_to_endzone() -> None:
    # ESPN 2016 rows encode 1st-and-goal as distance 0; those snaps (and the TDs) must still count.
    goal = _play("Rush", "1", 1, 1, 0, 2, 0, 0)
    goal["start"]["yardsToEndzone"] = 3
    td = _play("Rushing Touchdown", "1", 1, 2, 0, 1, 7, 0)
    td["start"]["yardsToEndzone"] = 1
    drive = {"team": {"id": "1"}, "timeElapsed": {"displayValue": "0:40"}, "plays": [goal, td]}
    rows, _ = game_rows({"event_id": "9", "season": 2016, "game_class": "FBS-FBS"}, _payload([drive]))
    home = next(r for r in rows if r["is_home"])
    assert home["off_plays"] == 2
    assert home["off_successes"] == 2  # 2 of 3 on 1st-and-goal, then the TD
    assert home["off_drives"] == 1
    assert home["off_points"] == 7


def test_back_to_back_duplicate_row_and_score_glitch() -> None:
    # A repeated interception row (new id, same content) is one turnover; a trailing 0-0 row does not
    # reset the running score.
    pick = _play("Interception", "2", 2, 3, 8, 0, 7, 0)
    drive_a = {"team": {"id": "2"}, "timeElapsed": {"displayValue": "0:20"}, "plays": [pick, dict(pick)]}
    end = _play("End of Game", "1", 4, 1, 10, 0, 0, 0)
    drive_b = {"team": {"id": "1"}, "timeElapsed": {"displayValue": "0:10"}, "plays": [end]}
    first = _play("Field Goal Good", "1", 1, 4, 5, 0, 7, 0)
    drive_0 = {"team": {"id": "1"}, "timeElapsed": {"displayValue": "0:10"}, "plays": [first]}
    rows, _ = game_rows(
        {"event_id": "9", "season": 2021, "game_class": "FBS-FBS"}, _payload([drive_0, drive_a, drive_b])
    )
    away = next(r for r in rows if not r["is_home"])
    assert away["off_plays"] == 1
    assert away["off_turnovers"] == 1
    assert away["off_points"] == 0
