"""Tests for the API-FOOTBALL soccer results client and its capture path.

API-FOOTBALL fixture responses are mocked in the documented v3 envelope
shape (get/parameters/errors/results/paging/response, fixture/teams/goals/
score objects). No test here touches the network or needs a key.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from model_prediction.data_sources.api_football import (
    APIFootballClient,
    collect_soccer_scores,
)
from model_prediction.data_sources.provider_capture import EASTERN

OBSERVED = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
WINDOW = ["2026-08-25", "2026-08-24", "2026-08-23"]


def make_fixture(
    fid: int,
    status: str,
    home: str,
    away: str,
    home_goal,
    away_goal,
    *,
    fulltime=None,
    penalty=None,
    date: str = "2026-08-24T19:00:00+00:00",
) -> dict:
    """One fixture object in the documented api-football v3 shape."""
    goals = {"home": home_goal, "away": away_goal}
    return {
        "fixture": {"id": fid, "date": date, "status": {"short": status, "long": status}},
        "league": {"id": 39, "name": "Premier League", "season": 2026},
        "teams": {
            "home": {"id": fid * 2, "name": home, "winner": False},
            "away": {"id": fid * 2 + 1, "name": away, "winner": True},
        },
        "goals": goals,
        "score": {
            "halftime": None,
            "fulltime": fulltime if fulltime is not None else goals,
            "extratime": None,
            "penalty": penalty,
        },
    }


class StubFootballClient:
    """Records every query and returns canned fixtures per (date, league_id)."""

    def __init__(self, fixtures_by_key: dict[tuple[str, int], list[dict]] | None = None) -> None:
        self.fixtures_by_key = fixtures_by_key or {}
        self.calls: list[dict] = []

    def fixtures(self, *, date: str, league_id: int, season: int | None = None) -> list[dict]:
        self.calls.append({"date": date, "league_id": league_id, "season": season})
        return list(self.fixtures_by_key.get((date, league_id), []))


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- client contract ------------------------------------------------------


def test_client_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API_FOOTBALL_KEY"):
        APIFootballClient("")


def test_client_fixtures_queries_documented_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fixtures"
        assert request.headers["x-apisports-key"] == "test-key"
        assert request.url.params["date"] == "2026-08-24"
        assert request.url.params["league"] == "39"
        assert request.url.params["season"] == "2026"
        assert request.url.params["timezone"] == "UTC"
        return httpx.Response(
            200,
            json={
                "get": "fixtures",
                "parameters": {"date": "2026-08-24", "league": "39"},
                "errors": [],
                "results": 1,
                "paging": {"current": 1, "total": 1},
                "response": [make_fixture(1, "FT", "Arsenal", "Chelsea", 2, 1)],
            },
        )

    client = APIFootballClient(
        "test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    fixtures = client.fixtures(date="2026-08-24", league_id=39, season=2026)
    assert fixtures[0]["fixture"]["id"] == 1


def test_client_transport_error_is_redacted() -> None:
    # The key travels in a header (never a URL query param), so HTTP status
    # errors cannot embed it -- the leak path is a transport error whose
    # message carries the request, so the reconstructed error must stay redacted.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection refused for key secret-key-123 at {request.url}")

    client = APIFootballClient(
        "secret-key-123",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(httpx.HTTPError) as excinfo:
        client.fixtures(date="2026-08-24", league_id=39)
    assert "secret-key-123" not in str(excinfo.value)
    assert "[REDACTED]" in str(excinfo.value)


# --- final / non-final status handling -------------------------------------


def test_final_statuses_ft_aet_pen_are_captured_with_scores(tmp_path) -> None:
    fixtures = {
        ("2026-08-24", 39): [
            make_fixture(1, "FT", "Arsenal", "Chelsea", 2, 1),
            # AET: goals is the score after extra time, fulltime stays 90-min.
            make_fixture(
                2,
                "AET",
                "Liverpool",
                "Everton",
                2,
                1,
                fulltime={"home": 1, "away": 1},
            ),
            # PEN: goals is the post-ET (level) score; shootout in score.penalty.
            make_fixture(
                3,
                "PEN",
                "Man United",
                "Man City",
                1,
                1,
                penalty={"home": 4, "away": 2},
            ),
            # FT but no numeric scores: never captured, never fabricated.
            make_fixture(4, "FT", "Spurs", "West Ham", None, None),
        ],
    }
    stub = StubFootballClient(fixtures)
    results = collect_soccer_scores(
        api_key="k",
        data_root=tmp_path,
        leagues={"PREMIER_LEAGUE": 39},
        client=stub,
        request_delay=0.0,
        observed_at=OBSERVED,
    )

    assert results["PREMIER_LEAGUE"]["status"] == "ok"
    assert results["PREMIER_LEAGUE"]["matches_returned"] == 4
    assert results["total_new_games"] == 3

    lines = _lines(tmp_path / "historical" / "soccer_games_all.jsonl")
    by_status = {line["status"]: line for line in lines}
    assert set(by_status) == {"FT", "AET", "PEN"}
    assert by_status["FT"]["home_team"] == "Arsenal"
    assert by_status["FT"]["home_score"] == 2
    assert by_status["FT"]["away_score"] == 1
    assert by_status["AET"]["home_score"] == 2  # extra-time goal included
    assert by_status["PEN"]["home_score"] == 1
    assert by_status["PEN"]["penalty_home"] == 4
    assert by_status["PEN"]["penalty_away"] == 2
    assert all(line["source"] == "api_football" for line in lines)
    assert all(line["observed_at_utc"] == OBSERVED.isoformat() for line in lines)


def test_non_final_statuses_are_skipped_and_never_fabricated(tmp_path) -> None:
    statuses = ["NS", "1H", "HT", "2H", "ET", "LIVE", "ABD", "AWD", "WO"]
    fixtures = {
        ("2026-08-24", 39): [
            make_fixture(i, status, f"Home{i}", f"Away{i}", 1, 0) for i, status in enumerate(statuses)
        ],
    }
    stub = StubFootballClient(fixtures)
    results = collect_soccer_scores(
        api_key="k",
        data_root=tmp_path,
        leagues={"PREMIER_LEAGUE": 39},
        client=stub,
        request_delay=0.0,
        observed_at=OBSERVED,
    )

    historical = tmp_path / "historical" / "soccer_games_all.jsonl"
    assert results["total_new_games"] == 0
    assert not historical.exists() or _lines(historical) == []

    # But the provider's statement is preserved as raw evidence: all nine
    # fixtures (including non-final) are in the immutable snapshot, distinct
    # from "we never asked".
    raw_files = list((tmp_path / "providers" / "api_football" / "soccer" / "raw").rglob("*.json"))
    assert raw_files
    payload = json.loads(raw_files[0].read_text(encoding="utf-8"))
    assert payload["entry_count"] == 9


# --- missing key ------------------------------------------------------------


def test_missing_key_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    results = collect_soccer_scores(data_root=tmp_path, observed_at=OBSERVED)
    assert results["status"] == "no_api_key"
    assert results["error"] == "API_FOOTBALL_KEY not set"
    assert not (tmp_path / "historical" / "soccer_games_all.jsonl").exists()


def test_collect_never_leaks_key_on_http_failure(monkeypatch, tmp_path) -> None:
    api_key = "super-secret-key-xyz"

    class ExplodingClient:
        def fixtures(self, *, date, league_id, season=None):
            raise RuntimeError(
                f"Client error '429' for url "
                f"'https://v3.football.api-sports.io/fixtures?apiKey={api_key}&date={date}'"
            )

    results = collect_soccer_scores(
        api_key=api_key,
        data_root=tmp_path,
        leagues={"PREMIER_LEAGUE": 39},
        client=ExplodingClient(),
        request_delay=0.0,
        observed_at=OBSERVED,
    )
    error = results["PREMIER_LEAGUE"]["error"]
    assert api_key not in error
    assert "***REDACTED***" in error


# --- provenance envelope ----------------------------------------------------


def test_provenance_envelope_and_day_bucketed_raw(tmp_path) -> None:
    fixtures = {
        ("2026-08-24", 39): [
            make_fixture(1, "FT", "Arsenal", "Chelsea", 2, 1),
            make_fixture(2, "NS", "Brighton", "Fulham", 0, 0),
        ],
    }
    stub = StubFootballClient(fixtures)
    collect_soccer_scores(
        api_key="k",
        data_root=tmp_path,
        leagues={"PREMIER_LEAGUE": 39},
        client=stub,
        request_delay=0.0,
        observed_at=OBSERVED,
    )

    # The capture window is the days_from most recent dates ending yesterday,
    # fetched with the season resolved for the capture moment.
    assert {call["date"] for call in stub.calls} == set(WINDOW)
    assert {call["season"] for call in stub.calls} == {2026}

    # Day-bucketed by the eastern date of the capture moment, hash-stamped.
    day = OBSERVED.astimezone(EASTERN).date().isoformat()
    raw_dir = tmp_path / "providers" / "api_football" / "soccer" / "raw" / day
    snapshot_dir = tmp_path / "providers" / "api_football" / "soccer" / "snapshots" / day
    raw_files = list(raw_dir.glob("*.json"))
    assert len(raw_files) == 1
    assert len(list(snapshot_dir.glob("*.json"))) == 1

    payload = json.loads(raw_files[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1"
    assert payload["source"] == "api_football"
    assert payload["sport"] == "soccer"
    assert payload["observed_at_utc"] == OBSERVED.isoformat()
    assert payload["entry_count"] == 2
    for entry in payload["entries"]:
        assert entry["source"] == "api_football"
        assert entry["observed_at_utc"] == OBSERVED.isoformat()
        assert entry["effective_at_utc"] == "2026-08-24T19:00:00+00:00"
        assert entry["source_entity_id"] in {"1", "2"}
        assert "payload" in entry

    # The normalized historical line carries the same capture stamp.
    line = _lines(tmp_path / "historical" / "soccer_games_all.jsonl")[0]
    assert line["observed_at_utc"] == OBSERVED.isoformat()
    assert line["event_start_utc"] == "2026-08-24T19:00:00+00:00"


def test_recapture_of_same_final_fixtures_dedups(tmp_path) -> None:
    fixtures = {
        ("2026-08-24", 39): [make_fixture(1, "FT", "Arsenal", "Chelsea", 2, 1)],
    }
    stub = StubFootballClient(fixtures)
    kwargs = {
        "api_key": "k",
        "data_root": tmp_path,
        "leagues": {"PREMIER_LEAGUE": 39},
        "client": stub,
        "request_delay": 0.0,
        "observed_at": OBSERVED,
    }
    first = collect_soccer_scores(**kwargs)
    second = collect_soccer_scores(**kwargs)

    assert first["total_new_games"] == 1
    assert second["total_new_games"] == 0
    assert len(_lines(tmp_path / "historical" / "soccer_games_all.jsonl")) == 1


# --- season resolution (point-in-time) --------------------------------------


def test_season_resolution_cross_year_leagues() -> None:
    stub = StubFootballClient()
    leagues = {"PREMIER_LEAGUE": 39, "BRASILEIRAO": 71}

    # February: the Premier League's Aug-May season is numbered by its
    # starting year (2025 for 2025-26); Brasileirao is calendar-year 2026.
    collect_soccer_scores(
        api_key="k",
        data_root="/tmp/apifootball-season-test",
        leagues=leagues,
        client=stub,
        request_delay=0.0,
        observed_at=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
    )
    season_calls = {(call["league_id"], call["season"]) for call in stub.calls}
    assert (39, 2025) in season_calls
    assert (71, 2026) in season_calls
    assert (39, 2026) not in season_calls

    # August: both use the current year (Premier League 2026-27 has begun).
    stub.calls.clear()
    collect_soccer_scores(
        api_key="k",
        data_root="/tmp/apifootball-season-test",
        leagues=leagues,
        client=stub,
        request_delay=0.0,
        observed_at=OBSERVED,
    )
    season_calls = {(call["league_id"], call["season"]) for call in stub.calls}
    assert (39, 2026) in season_calls
    assert (71, 2026) in season_calls
