"""Tests for the WNBA player-log parser and profile builder.

Fixtures mirror the REAL capture-file structure (outer provenance dict
wrapping the ESPN payload; per-team ``statistics[0]`` carrying
``keys``/``athletes`` with positional ``stats`` arrays). The two parser
bugs these pin — team-keyed-by-abbreviation instead of displayName, and
stat lookup by label abbreviation instead of the ``keys`` array's
lowercase full names — were both found only against live files, so the
fixture deliberately copies the real shapes.
"""

from __future__ import annotations

from model_prediction.features.wnba_player_logs import (
    build_wnba_player_logs,
    parse_wnba_player_boxscore,
    team_player_profiles,
)


def _athlete(pid: str, name: str, minutes: str, points: str) -> dict:
    return {
        "active": minutes != "0",
        "athlete": {"id": pid, "displayName": name},
        "starter": True,
        "stats": [minutes, points, "4-7", "0-0", "3-3", "11", "3", "0", "2", "0", "1", "10", "1", "+18"],
    }


def _payload(team_a: str, team_b: str, athletes_a: list[dict], athletes_b: list[dict]) -> dict:
    keys = [
        "minutes",
        "points",
        "fieldGoalsMade-fieldGoalsAttempted",
        "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
        "freeThrowsMade-freeThrowsAttempted",
        "rebounds",
        "assists",
        "turnovers",
        "steals",
        "blocks",
        "offensiveRebounds",
        "defensiveRebounds",
        "fouls",
        "plusMinus",
    ]
    return {
        "event_id": "401857157",
        "payload": {
            "boxscore": {
                "players": [
                    {
                        "team": {"abbreviation": team_a[:3].upper(), "displayName": team_a},
                        "statistics": [{"keys": keys, "athletes": athletes_a}],
                    },
                    {
                        "team": {"abbreviation": team_b[:3].upper(), "displayName": team_b},
                        "statistics": [{"keys": keys, "athletes": athletes_b}],
                    },
                ]
            }
        },
    }


def test_parse_keys_by_display_name_not_abbreviation():
    # The loader unwraps the provenance dict; the parser takes the inner
    # ESPN payload (the one with the top-level "boxscore" key).
    payload = _payload(
        "Minnesota Lynx",
        "Golden State Valkyries",
        [_athlete("1", "A", "34", "20")],
        [_athlete("2", "B", "0", "0")],
    )["payload"]
    parsed = parse_wnba_player_boxscore(payload)
    assert set(parsed.keys()) == {"Minnesota Lynx", "Golden State Valkyries"}
    row = parsed["Minnesota Lynx"][0]
    assert row["minutes"] == 34.0
    assert row["points"] == 20.0
    # Made-attempt pairs: the attempt side of "4-7".
    assert row["fga"] == 7.0
    # DNP rows are kept (they mark absences) but not active.
    assert parsed["Golden State Valkyries"][0]["active"] is False


def test_profiles_vary_and_missing_detected():
    logs = [
        {
            "players": [
                {
                    "player_id": "a",
                    "name": "Star",
                    "minutes": 34.0,
                    "points": 22.0,
                    "fga": 15.0,
                    "fta": 4.0,
                    "turnovers": 2.0,
                    "active": True,
                },
                {
                    "player_id": "b",
                    "name": "Bench",
                    "minutes": 18.0,
                    "points": 6.0,
                    "fga": 5.0,
                    "fta": 1.0,
                    "turnovers": 1.0,
                    "active": True,
                },
            ],
            "team_drtg": 98.0,
        },
        {
            "players": [
                {
                    "player_id": "a",
                    "name": "Star",
                    "minutes": 30.0,
                    "points": 18.0,
                    "fga": 14.0,
                    "fta": 3.0,
                    "turnovers": 1.0,
                    "active": True,
                },
                # Bench missed the second game entirely -> recently missing.
                {
                    "player_id": "b",
                    "name": "Bench",
                    "minutes": 0.0,
                    "points": 0.0,
                    "fga": 0.0,
                    "fta": 0.0,
                    "turnovers": 0.0,
                    "active": False,
                },
            ],
            "team_drtg": 105.0,
        },
    ]
    profiles, missing = team_player_profiles(logs)
    names = {p.player_name for p in profiles}
    assert names == {"Star", "Bench"}
    star = next(p for p in profiles if p.player_name == "Star")
    bench = next(p for p in profiles if p.player_name == "Bench")
    # Star outproduces Bench per 100 possessions; ratings shrink toward
    # the league prior but keep the ordering.
    assert star.off_rating_shrunk > bench.off_rating_shrunk
    assert star.minutes_per_game > bench.minutes_per_game
    assert "Bench" in missing


def test_build_logs_attaches_team_drtg():
    payload = _payload(
        "Minnesota Lynx",
        "Golden State Valkyries",
        [_athlete("1", "A", "34", "20")],
        [_athlete("2", "B", "0", "0")],
    )["payload"]
    parsed = parse_wnba_player_boxscore(payload)
    # Team stats in the real loader's shape (points/fga/fta/oreb/tov).
    team_stats = {
        "Minnesota Lynx": {"points": 80, "fga": 60, "fta": 15, "oreb": 8, "tov": 12},
        "Golden State Valkyries": {"points": 75, "fga": 58, "fta": 18, "oreb": 5, "tov": 14},
    }
    logs = build_wnba_player_logs("Minnesota Lynx", "Golden State Valkyries", parsed, team_stats)
    assert logs is not None
    # Opponent possessions = 58 + 0.44*18 - 5 + 14 = 74.92; DRTG = 75/74.92*100.
    assert logs["Minnesota Lynx"]["team_drtg"] == 75.0 / 74.92 * 100.0
    assert len(logs["Minnesota Lynx"]["players"]) == 1


def test_build_logs_none_when_team_key_missing():
    payload = _payload(
        "Minnesota Lynx",
        "Golden State Valkyries",
        [_athlete("1", "A", "34", "20")],
        [_athlete("2", "B", "0", "0")],
    )["payload"]
    parsed = parse_wnba_player_boxscore(payload)
    assert build_wnba_player_logs("Chicago Sky", "Minnesota Lynx", parsed, {}) is None
