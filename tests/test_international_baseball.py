from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

import pytest

from model_prediction.data_sources.polymarket_us import LEAGUE_SLUGS, POLYMARKET_SPORT_LEAGUES
from model_prediction.international_baseball import (
    HomeElo,
    _chronological_split,
    forecast_international_baseball_slate,
    parse_kbo_rows,
    parse_npb_calendar,
    tie_aware_fair_values,
    validate_international_baseball_baseline,
)


def test_polymarket_taxonomy_includes_separate_kbo_npb_leagues() -> None:
    assert LEAGUE_SLUGS["KBO"] == "kbo"
    assert LEAGUE_SLUGS["NPB"] == "npb"
    assert POLYMARKET_SPORT_LEAGUES["kbo"] == ("KBO",)
    assert POLYMARKET_SPORT_LEAGUES["npb"] == ("NPB",)


def test_parse_official_kbo_result_including_tie() -> None:
    payload = {
        "rows": [
            {
                "row": [
                    {"Text": "04.02(수)", "Class": "day"},
                    {"Text": "<b>18:30</b>", "Class": "time"},
                    {
                        "Text": '<span>롯데</span><em><span class="same">3</span>'
                        '<span>vs</span><span class="same">3</span></em><span>한화</span>',
                        "Class": "play",
                    },
                    {
                        "Text": "<a href='/Schedule/GameCenter/Main.aspx?gameDate=20250402&gameId=20250402LTHH0&section=REVIEW'>리뷰</a>",
                        "Class": "relay",
                    },
                ]
            }
        ]
    }
    rows = parse_kbo_rows(payload, 2025)
    assert rows == [
        {
            "game_id": "kbo:20250402LTHH0",
            "league": "kbo",
            "season": 2025,
            "game_date": "2025-04-02",
            "scheduled_local_time": "18:30",
            "away_team_id": "LOTTE",
            "home_team_id": "HANWHA",
            "away_team_name": "Lotte Giants",
            "home_team_name": "Hanwha Eagles",
            "away_score": 3,
            "home_score": 3,
            "tie": True,
            "source_url": "https://www.koreabaseball.com/Schedule/Schedule.aspx?gameDate=20250402&gameId=20250402LTHH0",
        }
    ]


def test_parse_official_npb_calendar_skips_cancellations_and_keeps_ties() -> None:
    page = """
    <a href="/bis/eng/2025/games/s2025040200119.html">T 6 - 6 DB</a>
    <a href="/bis/eng/2025/games/s2025040200491.html">E * - * L</a>
    """
    rows = parse_npb_calendar(page)
    assert len(rows) == 1
    assert rows[0]["away_team_id"] == "DB"
    assert rows[0]["home_team_id"] == "T"
    assert rows[0]["tie"] is True


def test_tie_aware_contract_values_sum_to_one() -> None:
    away, home = tie_aware_fair_values(0.60, 0.04)
    assert home == pytest.approx(0.596)
    assert away == pytest.approx(0.404)
    assert away + home == pytest.approx(1.0)


def _write_synthetic_history(tmp_path, league: str = "kbo") -> None:
    directory = tmp_path / "international_baseball" / league
    directory.mkdir(parents=True)
    teams = ["KIA", "DOOSAN", "HANWHA", "KIWOOM", "KT", "LG", "LOTTE", "NC", "SAMSUNG", "SSG"]
    rows = []
    start = date(2022, 3, 1)
    for index in range(600):
        away = teams[index % len(teams)]
        home = teams[(index + 3) % len(teams)]
        tie = index % 31 == 0
        home_win = index % 5 not in {0, 1}
        rows.append(
            {
                "game_id": f"{league}:{index}",
                "league": league,
                "season": 2022,
                "game_date": (start + timedelta(days=index // 5)).isoformat(),
                "away_team_id": away,
                "home_team_id": home,
                "away_score": 3 if tie else (2 if home_win else 5),
                "home_score": 3 if tie else (5 if home_win else 2),
                "tie": tie,
            }
        )
    games_path = directory / "games.jsonl"
    games_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    games_hash = hashlib.sha256(games_path.read_bytes()).hexdigest()
    (directory / "manifest.json").write_text(
        json.dumps({"games_sha256": games_hash}), encoding="utf-8"
    )


def test_validation_is_locked_chronological_and_never_promotes(tmp_path) -> None:
    _write_synthetic_history(tmp_path)
    report = validate_international_baseball_baseline(tmp_path, "kbo", tmp_path / "models")
    assert report["status"] == "ok"
    assert report["chronological_split"]["train"]["through_date"] < report["chronological_split"]["validation"]["from_date"]
    assert report["chronological_split"]["validation"]["through_date"] < report["chronological_split"]["locked_test"]["from_date"]
    assert report["promotion_eligible"] is False
    assert report["units"] == 0
    artifact = json.loads((tmp_path / "models/kbo-tie-aware-elo-v1.json").read_text())
    assert artifact["qualified_for_betting"] is False
    assert artifact["target"] == "expected moneyline settlement where a tie pays 0.50"


class _MarketClient:
    def slate(self, league, game_date, timezone_name):
        assert (league, timezone_name) == ("KBO", "Asia/Seoul")
        return [
            {
                "event_id": "event-1",
                "event_start_utc": "2099-07-20T09:00:00Z",
                "markets": [
                    {
                        "market_type": "moneyline",
                        "market_slug": "kbo-test",
                        "sides": [
                            {"description": "Kia Tigers", "selection": "away"},
                            {"description": "LG Twins", "selection": "home"},
                        ],
                    }
                ],
            }
        ]

    def snapshot(self, slug):
        assert slug == "kbo-test"
        return {
            "observed_at_utc": "2099-07-19T00:00:00Z",
            "long": {"description": "Kia Tigers", "ask": 0.49},
            "short": {"description": "LG Twins", "ask": 0.52},
        }


def test_forecast_uses_home_away_and_current_asks_but_stays_zero_unit(tmp_path) -> None:
    directory = tmp_path / "international_baseball/kbo"
    directory.mkdir(parents=True)
    (directory / "teams.json").write_text(
        json.dumps(
            {
                "KIA": {"name": "Kia Tigers", "aliases": ["KIA"]},
                "LG": {"name": "LG Twins", "aliases": ["LG"]},
            }
        ),
        encoding="utf-8",
    )
    models = tmp_path / "models"
    models.mkdir()
    (models / "kbo-tie-aware-elo-v1.json").write_text(
        json.dumps(
            {
                "model_version": "kbo-tie-aware-elo-v1",
                "artifact_hash": "abc",
                "k": 20,
                "home_advantage": 50,
                "tie_probability": 0.04,
                "trained_through_date": "2099-07-18",
                "ratings": {"KIA": 1500, "LG": 1520},
            }
        ),
        encoding="utf-8",
    )
    output = forecast_international_baseball_slate(
        tmp_path, models, "kbo", "2099-07-20", client=_MarketClient()
    )
    assert output["priced_count"] == 1
    assert output["units"] == 0
    contract = output["priced_contracts"][0]
    assert contract["qualification"] == "NO_CALL_MODEL_UNVALIDATED"
    assert sum(side["model_fair_settlement_value"] for side in contract["sides"]) == pytest.approx(1.0)
    assert all(side["executable_ask"] is not None for side in contract["sides"])


class _CollisionMarketClient:
    """Both side descriptions alias to the same team_id (e.g. an alias collision)."""

    def slate(self, league, game_date, timezone_name):
        return [
            {
                "event_id": "event-collision",
                "event_start_utc": "2099-07-20T09:00:00Z",
                "markets": [
                    {
                        "market_type": "moneyline",
                        "market_slug": "kbo-collision",
                        "sides": [
                            {"description": "Kia Tigers", "selection": "away"},
                            {"description": "KIA", "selection": "home"},
                        ],
                    }
                ],
            }
        ]

    def snapshot(self, slug):
        raise AssertionError("a colliding contract must never reach snapshot pricing")


def test_forecast_no_calls_when_both_sides_resolve_to_same_team(tmp_path) -> None:
    directory = tmp_path / "international_baseball/kbo"
    directory.mkdir(parents=True)
    (directory / "teams.json").write_text(
        json.dumps({"KIA": {"name": "Kia Tigers", "aliases": ["KIA"]}}),
        encoding="utf-8",
    )
    models = tmp_path / "models"
    models.mkdir()
    (models / "kbo-tie-aware-elo-v1.json").write_text(
        json.dumps(
            {
                "model_version": "kbo-tie-aware-elo-v1",
                "artifact_hash": "abc",
                "k": 20,
                "home_advantage": 50,
                "tie_probability": 0.04,
                "trained_through_date": "2099-07-18",
                "ratings": {"KIA": 1500},
            }
        ),
        encoding="utf-8",
    )
    output = forecast_international_baseball_slate(
        tmp_path, models, "kbo", "2099-07-20", client=_CollisionMarketClient()
    )
    assert output["priced_count"] == 0
    assert output["no_call_count"] == 1
    assert output["no_calls"][0]["reason"] == "NO_CALL_MODEL_UNVALIDATED_NEW_TEAM"


def test_chronological_split_does_not_crash_with_one_unique_date() -> None:
    """A degenerate backfill sharing one game_date must not IndexError."""
    rows = [{"game_date": "2022-03-01", "game_id": str(i)} for i in range(600)]
    train, validation, test = _chronological_split(rows)
    assert len(train) + len(validation) + len(test) == len(rows)


def test_home_elo_update_does_not_recompute_on_explicit_zero_probability() -> None:
    """A frozen probability of exactly 0.0 must be used as-is, not treated as unset."""
    book = HomeElo(k=20.0, home_advantage=50.0, ratings={"A": 1500.0, "B": 1500.0})
    row = {"home_team_id": "A", "away_team_id": "B", "home_score": 5, "away_score": 3}
    book.update(row, probability=0.0)
    # outcome=1.0 (home won), probability=0.0 forced -> delta = k * (1.0 - 0.0) = k
    assert book.ratings["A"] == pytest.approx(1500.0 + 20.0)
    assert book.ratings["B"] == pytest.approx(1500.0 - 20.0)
