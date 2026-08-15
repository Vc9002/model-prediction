from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from model_prediction.data_sources import espn_probables


@pytest.fixture(autouse=True)
def _clear_probables_cache(monkeypatch, tmp_path) -> None:
    espn_probables._pull_espn_probables.cache_clear()
    # Isolate the on-disk cache from the real data/espn_probables_cache.jsonl:
    # without this, a prior real backtest run's cached entry for a date this
    # test also uses (e.g. a past date with a real, complete scoreboard)
    # would short-circuit the monkeypatched httpx.get below and silently
    # return production data instead of the test's simulated response.
    monkeypatch.setattr(espn_probables, "_DISK_CACHE_PATH", tmp_path / "espn_probables_cache.jsonl")
    monkeypatch.setattr(
        espn_probables,
        "_PIT_ARCHIVE_PATH",
        tmp_path / "point_in_time" / "mlb_probable_starters.jsonl",
    )


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _scoreboard(*, include_both_starters: bool = True) -> dict[str, Any]:
    away_probables = [
        {
            "name": "probableStartingPitcher",
            "athlete": {"fullName": "Zack Littell"},
            "statistics": [{"name": "ERA", "displayValue": "4.90"}],
        }
    ]
    home_probables = (
        [
            {
                "name": "probableStartingPitcher",
                "athlete": {"fullName": "J.T. Ginn"},
                "statistics": [{"name": "ERA", "displayValue": "3.67"}],
            }
        ]
        if include_both_starters
        else []
    )
    return {
        "events": [
            {
                "id": "401816170",
                "date": "2099-07-18T23:00:00Z",
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "away", "probables": away_probables},
                            {"homeAway": "home", "probables": home_probables},
                        ]
                    }
                ],
            }
        ]
    }


def test_probables_normalizes_iso_date_and_returns_exact_starters(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_get(url: str, timeout: int) -> _Response:
        requested_urls.append(url)
        assert timeout == 15
        return _Response(_scoreboard())

    monkeypatch.setattr(espn_probables.httpx, "get", fake_get)

    entry = espn_probables._pull_espn_probables("2026-07-18")["401816170"]

    assert requested_urls == [
        (
            "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
            "?dates=20260718"
        )
    ]
    assert entry == {
        "home_era": 3.67,
        "away_era": 4.90,
        "home_starter": "J.T. Ginn",
        "away_starter": "Zack Littell",
    }


def test_pitcher_gap_uses_exact_probable_starter_eras(monkeypatch) -> None:
    monkeypatch.setattr(
        espn_probables,
        "_pull_espn_probables",
        lambda date_str: {
            "401816170": {
                "home_era": 3.67,
                "away_era": 4.90,
                "home_starter": "J.T. Ginn",
                "away_starter": "Zack Littell",
            }
        },
    )

    gap = espn_probables.espn_pitcher_era_gap(
        "401816170", "Athletics", "Washington Nationals", "2026-07-18"
    )

    assert gap == -1.23


def test_missing_probable_starter_fails_closed_without_team_proxy(monkeypatch) -> None:
    monkeypatch.setattr(
        espn_probables.httpx,
        "get",
        lambda url, timeout: _Response(_scoreboard(include_both_starters=False)),
    )

    with pytest.raises(ValueError, match="NO_CALL_STARTERS_UNAVAILABLE"):
        espn_probables.espn_pitcher_era_gap(
            "401816170", "Athletics", "Washington Nationals", "2026-07-18"
        )


def test_prospective_capture_archives_observation_and_is_usable_before_first_pitch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        espn_probables.httpx,
        "get",
        lambda url, timeout: _Response(_scoreboard()),
    )
    observed = datetime(2099, 7, 18, 18, tzinfo=UTC)

    result = espn_probables.capture_probable_starter_snapshot(
        "2099-07-18",
        observed_at=observed,
    )
    gap = espn_probables.point_in_time_pitcher_era_gap(
        "401816170",
        datetime(2099, 7, 18, 22, tzinfo=UTC),
    )

    assert result["401816170"]["home_starter"] == "J.T. Ginn"
    assert gap == -1.23
    archived = [
        json.loads(line)
        for line in espn_probables._PIT_ARCHIVE_PATH.read_text(encoding="utf-8").splitlines()
    ]
    assert archived[0]["observed_at_utc"] == "2099-07-18T18:00:00Z"
    assert archived[0]["event_start_utc"] == "2099-07-18T23:00:00Z"
    assert archived[0]["pit_eligible"] is True
    assert archived[0]["provenance"] == "prospective_pregame"


def test_point_in_time_probable_starters_exposes_names_not_just_era_gap(monkeypatch) -> None:
    monkeypatch.setattr(
        espn_probables.httpx,
        "get",
        lambda url, timeout: _Response(_scoreboard()),
    )
    espn_probables.capture_probable_starter_snapshot(
        "2099-07-18",
        observed_at=datetime(2099, 7, 18, 18, tzinfo=UTC),
    )

    starters = espn_probables.point_in_time_probable_starters(
        "401816170",
        datetime(2099, 7, 18, 22, tzinfo=UTC),
    )

    assert starters == {"home_starter": "J.T. Ginn", "away_starter": "Zack Littell"}


def test_point_in_time_probable_starters_fails_closed_without_archive() -> None:
    with pytest.raises(ValueError, match="NO_PIT_ARCHIVE"):
        espn_probables.point_in_time_probable_starters(
            "no-such-event", datetime(2099, 7, 18, 22, tzinfo=UTC)
        )


def test_retroactive_capture_is_archived_as_non_pit_and_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        espn_probables.httpx,
        "get",
        lambda url, timeout: _Response(_scoreboard()),
    )
    espn_probables.capture_probable_starter_snapshot(
        "2099-07-18",
        observed_at=datetime(2099, 7, 19, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="NO_PIT_ARCHIVE"):
        espn_probables.point_in_time_pitcher_era_gap(
            "401816170",
            datetime(2099, 7, 19, tzinfo=UTC),
        )
