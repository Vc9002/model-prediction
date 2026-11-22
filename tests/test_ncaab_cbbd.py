"""Network-free tests for the CollegeBasketballData.com client (auth, quota floor, cache, parsing)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from itertools import pairwise

import httpx
import pytest

from model_prediction.data_sources.ncaab_cbbd import (
    RESPONSE_GAME_CAP,
    CBBDAuthError,
    CBBDClient,
    CBBDQuotaError,
    build_cbbd_crosswalk,
    parse_games,
    parse_lines,
    parse_team_box,
    season_windows,
)
from model_prediction.data_sources.ncaab_team_crosswalk import NO_CALL_COLLISION, NO_CALL_UNMATCHED, EspnTeam

SECRET = "s" * 40

LINES_ROW = {
    "gameId": 6016,
    "season": 2024,
    "startDate": "2023-11-07T01:00:00.000Z",
    "homeTeamId": 26,
    "awayTeamId": 83,
    "lines": [
        {
            "provider": "ESPN BET",
            "spread": -13.5,
            "overUnder": 130.5,
            "homeMoneyline": -300,
            "awayMoneyline": 250,
            "spreadOpen": None,
            "overUnderOpen": None,
        }
    ],
}


def _client(tmp_path, handler, **kwargs) -> CBBDClient:
    return CBBDClient(
        environ={"CBBD_API_KEY": SECRET},
        cache_root=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def test_no_key_fails_closed():
    with pytest.raises(CBBDAuthError):
        CBBDClient(environ={})


def test_bearer_auth_cache_and_quota_without_leaking_key(tmp_path, caplog):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        assert "key" not in request.url.params
        return httpx.Response(200, json=[LINES_ROW], headers={"X-CallLimit-Remaining": "2500"})

    client = _client(tmp_path, handler)
    with caplog.at_level(logging.INFO):
        first = client.fetch("lines", {"season": 2024})
        second = client.fetch("lines", {"season": 2024})
    assert first == second and len(seen) == 1 and client.calls_made == 1
    assert client.remaining == 2500
    meta = json.loads(next(tmp_path.rglob("*.meta.json")).read_text(encoding="utf-8"))
    assert (
        meta["fetched_at_utc"]
        and meta["params"] == {"season": 2024}
        and meta["calls_remaining_after"] == 2500
    )
    for path in tmp_path.rglob("*"):
        assert SECRET not in path.name
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes()
    assert SECRET not in caplog.text
    # A fresh process sees the persisted quota.
    assert _client(tmp_path, handler).remaining == 2500


def test_quota_floor_blocks_requests_but_not_cache(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[], headers={"X-CallLimit-Remaining": "150"})

    client = _client(tmp_path, handler)
    client.fetch("teams", {})  # remaining unknown -> allowed; now 150
    assert client.fetch("teams", {}) == []  # cached, no call
    with pytest.raises(CBBDQuotaError):
        client.fetch("games", {"season": 2020})
    assert len(calls) == 1


def test_rejected_key_raises_auth_error(tmp_path):
    client = _client(tmp_path, lambda r: httpx.Response(401, headers={"X-CallLimit-Remaining": "10"}))
    with pytest.raises(CBBDAuthError) as info:
        client.fetch("teams", {})
    assert SECRET not in str(info.value)


def test_windowed_fetch_halves_a_capped_window(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        start = datetime.fromisoformat(request.url.params["startDateRange"])
        end = datetime.fromisoformat(request.url.params["endDateRange"])
        n = RESPONSE_GAME_CAP if (end - start).days > 20 else 2
        rows = [{"id": f"{start.date()}-{i}"} for i in range(n)]
        return httpx.Response(200, json=rows, headers={"X-CallLimit-Remaining": "2000"})

    client = _client(tmp_path, handler)
    window = (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 31, 23, 59, 59, tzinfo=UTC))
    rows = client.fetch_windowed("games", 2024, [window])
    assert len(rows) == 4 and client.calls_made == 3  # capped call + two halves


def test_season_windows_are_contiguous():
    windows = season_windows(2026)
    assert windows[0][0] == datetime(2025, 10, 15, tzinfo=UTC)
    for (_, end), (start, _) in pairwise(windows):
        assert (start - end).total_seconds() == 1


def test_parse_lines_uses_probabilities_not_american_odds():
    (line,) = parse_lines([LINES_ROW])
    assert line.spread == -13.5 and line.over_under == 130.5 and line.spread_open is None
    assert line.home_moneyline_prob == pytest.approx(0.75)
    assert line.away_moneyline_prob == pytest.approx(100 / 350)
    assert not hasattr(line, "home_moneyline")


def test_parse_games_and_team_box():
    (game,) = parse_games(
        [
            {
                "id": 5906,
                "sourceId": "401581587",
                "season": 2024,
                "startDate": "2023-11-06T21:30:00.000Z",
                "status": "final",
                "neutralSite": True,
                "conferenceGame": False,
                "homeTeamId": 223,
                "homeTeam": "Oregon",
                "homePoints": 82,
                "awayTeamId": 98,
                "awayTeam": "Georgia",
                "awayPoints": 71,
            }
        ]
    )
    assert game.source_id == "401581587" and game.neutral_site is True and game.home_points == 82
    (box,) = parse_team_box(
        [
            {
                "gameId": 5906,
                "teamId": 223,
                "opponentId": 98,
                "isHome": True,
                "teamStats": {
                    "fieldGoals": {"made": 30, "attempted": 60},
                    "freeThrows": {"made": 15, "attempted": 20},
                    "rebounds": {"offensive": 9, "defensive": 25, "total": 34},
                    "turnovers": {"total": 11, "teamTotal": 1},
                    "points": {"total": 82},
                    "possessions": 70.2,
                },
            }
        ]
    )
    assert (box.fga, box.fta, box.oreb, box.tov, box.points) == (60, 20, 9, 11, 82)
    assert box.complete


def test_cbbd_crosswalk_by_source_id_and_name_check():
    espn = [
        EspnTeam("41", "UConn Huskies", "UConn", "UConn", "UConn"),
        EspnTeam(
            "2115", "Central Connecticut Blue Devils", "Central Connecticut", "C Connecticut", "C Connecticut"
        ),
    ]
    teams = [
        {"id": 1, "sourceId": "41", "school": "UConn", "displayName": "UConn Huskies"},
        {
            "id": 2,
            "sourceId": "2115",
            "school": "Central Connecticut",
            "displayName": "Central Connecticut Blue Devils",
        },
        {
            "id": 3,
            "sourceId": "41",
            "school": "Central Connecticut",
            "displayName": "Central Connecticut Blue Devils",
        },
        {"id": 4, "sourceId": "999", "school": "Old School", "displayName": "Old School Owls"},
    ]
    xw = build_cbbd_crosswalk(teams, espn)
    assert xw[2].espn_team_id == "2115"
    assert xw[3].reason == NO_CALL_UNMATCHED  # id says UConn, names say CCSU: declined
    assert xw[1].espn_team_id == "41"
    assert xw[4].espn_team_id == "999" and "not in current ESPN" in xw[4].detail
    two = build_cbbd_crosswalk(
        [teams[0], {"id": 5, "sourceId": "41", "school": "UConn", "displayName": "UConn Huskies"}], espn
    )
    assert two[1].reason == two[5].reason == NO_CALL_COLLISION
