from __future__ import annotations

from typing import Any

import pytest

from model_prediction.data_sources import espn_probables


@pytest.fixture(autouse=True)
def _clear_probables_cache() -> None:
    espn_probables._pull_espn_probables.cache_clear()


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
        "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
        "?dates=20260718"
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
