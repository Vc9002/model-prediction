from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

import pytest

from model_prediction.data_sources.polymarket_us import LEAGUE_SLUGS, POLYMARKET_SPORT_LEAGUES
from model_prediction.domain import eastern_today
from model_prediction.international_baseball import (
    HomeElo,
    _chronological_split,
    _metrics,
    find_international_baseball_result,
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


def test_metrics_units_at_minus_110_treats_ties_as_a_push() -> None:
    # 2 decisive hits, 1 decisive miss, 1 tie -- the tie must not count as a
    # loss (accuracy_decisive already excludes it; units must match).
    rows = [
        {"probability": 0.7, "outcome": 1.0, "tie": False},
        {"probability": 0.7, "outcome": 1.0, "tie": False},
        {"probability": 0.7, "outcome": 0.0, "tie": False},
        {"probability": 0.6, "outcome": 0.5, "tie": True},
    ]
    metrics = _metrics(rows)
    assert metrics["ties"] == 1
    assert metrics["calls"] == 3
    assert metrics["hits"] == 2
    assert metrics["units_at_minus_110"] == pytest.approx(2 * (10 / 11) - 1)


def test_metrics_empty_rows_report_zero_units_not_none() -> None:
    metrics = _metrics([])
    assert metrics["calls"] == 0
    assert metrics["hits"] == 0
    assert metrics["units_at_minus_110"] == 0.0


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


def test_forecast_refuses_when_training_prefix_hash_no_longer_matches(tmp_path) -> None:
    """Real bug this guards against, 2026-08-02: NPB's settlement fallback
    silently collapsed games.jsonl to a fraction of its real history while
    the ratings artifact kept being used as if nothing had changed --
    nothing ever checked that the two still agreed. games_sha256 alone
    can't catch this prospectively (the file legitimately grows every day),
    so training_prefix_sha256 hashes only the rows through
    trained_through_date, which a healthy history can never change."""
    from model_prediction.international_baseball import _training_prefix_sha256

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
    game_row = {
        "game_id": "kbo:1", "game_date": "2099-07-18",
        "home_team_id": "LG", "away_team_id": "KIA",
        "home_score": 4, "away_score": 2, "tie": False,
    }
    (directory / "games.jsonl").write_text(json.dumps(game_row) + "\n", encoding="utf-8")
    real_prefix_hash = _training_prefix_sha256([game_row], "2099-07-18")

    models = tmp_path / "models"
    models.mkdir()

    def _artifact(prefix_hash: str) -> dict:
        return {
            "model_version": "kbo-tie-aware-elo-v1",
            "artifact_hash": "abc",
            "k": 20,
            "home_advantage": 50,
            "tie_probability": 0.04,
            "trained_through_date": "2099-07-18",
            "training_prefix_sha256": prefix_hash,
            "ratings": {"KIA": 1500, "LG": 1520},
        }

    # Wrong hash (as if the history was altered after this artifact trained) -> refused.
    (models / "kbo-tie-aware-elo-v1.json").write_text(
        json.dumps(_artifact("0" * 64)), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="training history has changed"):
        forecast_international_baseball_slate(
            tmp_path, models, "kbo", "2099-07-20", client=_MarketClient()
        )

    # Real matching hash -> forecast proceeds normally.
    (models / "kbo-tie-aware-elo-v1.json").write_text(
        json.dumps(_artifact(real_prefix_hash)), encoding="utf-8"
    )
    output = forecast_international_baseball_slate(
        tmp_path, models, "kbo", "2099-07-20", client=_MarketClient()
    )
    assert output["priced_count"] == 1


class _HomeFirstMarketClient(_MarketClient):
    """Same event as _MarketClient, but the gateway lists home before away --
    market["sides"] has no ordering guarantee (see
    data_sources.polymarket_us._normalize_event), so this must not be assumed."""

    def slate(self, league, game_date, timezone_name):
        return [
            {
                "event_id": "event-1",
                "event_start_utc": "2099-07-20T09:00:00Z",
                "markets": [
                    {
                        "market_type": "moneyline",
                        "market_slug": "kbo-test",
                        "sides": [
                            {"description": "LG Twins", "selection": "home"},
                            {"description": "Kia Tigers", "selection": "away"},
                        ],
                    }
                ],
            }
        ]


def test_forecast_resolves_home_away_by_tag_not_by_raw_side_position(tmp_path) -> None:
    """Operator directive, 2026-07-31: home/away must be resolved by each
    side's own tag, never by array position -- a swap would silently
    mislabel the ledger row (feeds home_advantage lookups and the official-
    schedule settlement match), even though it can never change WHICH team
    the model picks (that's always by probability, independent of home/away)."""
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
    home_first = forecast_international_baseball_slate(
        tmp_path, models, "kbo", "2099-07-20", client=_HomeFirstMarketClient()
    )
    away_first = forecast_international_baseball_slate(
        tmp_path, models, "kbo", "2099-07-20", client=_MarketClient()
    )
    contract_home_first = home_first["priced_contracts"][0]
    contract_away_first = away_first["priced_contracts"][0]
    # Regardless of which order the gateway listed the sides in, the
    # resolved home/away team names must agree.
    assert contract_home_first["home_team"] == contract_away_first["home_team"] == "LG Twins"
    assert contract_home_first["away_team"] == contract_away_first["away_team"] == "Kia Tigers"


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
    row = {"home_team_id": "A", "away_team_id": "B", "home_score": 5, "away_score": 3, "game_date": "2025-06-01"}
    book.update(row, probability=0.0)
    # outcome=1.0 (home won), probability=0.0 forced -> delta = k * (1.0 - 0.0) = k
    assert book.ratings["A"] == pytest.approx(1500.0 + 20.0)
    assert book.ratings["B"] == pytest.approx(1500.0 - 20.0)


def _write_completed_games(tmp_path, league: str, rows: list[dict]) -> None:
    """Minimal games.jsonl + teams.json for find_international_baseball_result.

    Ledger rows for KBO/NPB carry Polymarket team-name strings, not the
    official schedule's own team_id/game_id scheme, so lookups match by
    game_date + team alias -- teams.json's alias list is what makes that
    matching possible.
    """
    directory = tmp_path / "international_baseball" / league
    directory.mkdir(parents=True)
    (directory / "games.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    team_ids = {row["home_team_id"] for row in rows} | {row["away_team_id"] for row in rows}
    teams = {
        team_id: {"team_id": team_id, "name": team_id.title(), "aliases": [team_id]}
        for team_id in team_ids
    }
    (directory / "teams.json").write_text(json.dumps(teams), encoding="utf-8")


def test_find_international_baseball_result_matches_by_date_and_team_alias(tmp_path) -> None:
    _write_completed_games(
        tmp_path,
        "kbo",
        [
            {
                "game_id": "kbo:1",
                "league": "kbo",
                "game_date": "2026-05-01",
                "home_team_id": "DOOSAN",
                "away_team_id": "HANWHA",
                "home_score": 6,
                "away_score": 4,
                "tie": False,
            }
        ],
    )
    result = find_international_baseball_result(
        tmp_path, "kbo", "2026-05-01", "Doosan", "Hanwha"
    )
    assert result == (4, 6)  # (away_score, home_score)


def test_find_international_baseball_result_returns_none_for_unplayed_game(tmp_path) -> None:
    _write_completed_games(
        tmp_path,
        "kbo",
        [
            {
                "game_id": "kbo:1",
                "league": "kbo",
                "game_date": "2026-05-01",
                "home_team_id": "DOOSAN",
                "away_team_id": "HANWHA",
                "home_score": 6,
                "away_score": 4,
                "tie": False,
            }
        ],
    )
    # Same teams, a different date the cache has no row for, and no live
    # client passed -- must return None (pick stays open), never guess.
    result = find_international_baseball_result(
        tmp_path, "kbo", "2026-05-02", "Doosan", "Hanwha", client=object()
    )
    assert result is None


def test_find_international_baseball_result_none_when_teams_json_missing(tmp_path) -> None:
    directory = tmp_path / "international_baseball" / "kbo"
    directory.mkdir(parents=True)
    (directory / "games.jsonl").write_text(
        json.dumps(
            {
                "game_id": "kbo:1",
                "league": "kbo",
                "game_date": "2026-05-01",
                "home_team_id": "DOOSAN",
                "away_team_id": "HANWHA",
                "home_score": 6,
                "away_score": 4,
                "tie": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = find_international_baseball_result(
        tmp_path, "kbo", "2026-05-01", "Doosan", "Hanwha", client=object()
    )
    assert result is None


class _FakeYearClient:
    """Records every (league, year) requested and returns caller-supplied
    rows for that exact year, [] otherwise -- lets a test assert exactly
    which years were (or weren't) re-fetched."""

    def __init__(self, rows_by_year: dict[int, list[dict]]):
        self.rows_by_year = rows_by_year
        self.requested_years: list[int] = []

    def kbo_year(self, year: int):
        self.requested_years.append(year)
        return list(self.rows_by_year.get(year, [])), 1

    def npb_year(self, year: int):
        self.requested_years.append(year)
        return list(self.rows_by_year.get(year, [])), 1


def _game_row(game_id: str, game_date: str, home="LG", away="KIA", home_score=3, away_score=2):
    return {
        "game_id": game_id,
        "game_date": game_date,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "tie": home_score == away_score,
    }


def test_refresh_merges_current_year_without_touching_older_seasons(tmp_path) -> None:
    """Operator directive, 2026-07-31: KBO/NPB ratings were silently going
    stale (confirmed live: 6-14 days) with nothing analogous to esports'
    refresh_recent_matches. This is the KBO/NPB equivalent -- must only
    re-fetch the current year, merging in without deleting prior seasons."""
    from model_prediction.international_baseball import refresh_recent_international_baseball_matches

    today = eastern_today()
    directory = tmp_path / "international_baseball/kbo"
    directory.mkdir(parents=True)
    old_year_game = _game_row("old-1", f"{today.year - 2}-05-01")
    (directory / "games.jsonl").write_text(json.dumps(old_year_game) + "\n", encoding="utf-8")
    (directory / "teams.json").write_text("{}", encoding="utf-8")
    (directory / "manifest.json").write_text("{}", encoding="utf-8")

    new_game = _game_row("new-1", today.isoformat())
    client = _FakeYearClient({today.year: [new_game]})

    result = refresh_recent_international_baseball_matches(tmp_path, "kbo", client=client)

    assert client.requested_years == [today.year]  # only the current year, nothing else
    games_path = directory / "games.jsonl"
    game_ids = {json.loads(line)["game_id"] for line in games_path.read_text().splitlines() if line.strip()}
    assert game_ids == {"old-1", "new-1"}  # old season preserved, new season merged in
    assert result["game_count"] == 2


def test_settlement_cache_miss_fallback_does_not_destroy_older_seasons(tmp_path) -> None:
    """Real bug fixed 2026-08-02: find_international_baseball_result's
    cache-miss fallback used to call the destructive full-overwrite
    backfill_international_baseball(..., f"{year}-01-01"), which replaces
    games.jsonl wholesale -- silently deleting every earlier season the
    first time a just-finished game wasn't in the cache yet. This is
    exactly what happened to real NPB history (3,936 games back to
    2022-03-25, collapsed to 566 games from this year only). The fallback
    must now merge by game_id like refresh_recent_international_baseball_matches
    does, so older seasons survive a cache miss on a recent game."""
    today = eastern_today()
    directory = tmp_path / "international_baseball/npb"
    directory.mkdir(parents=True)
    # Real NPB team_ids/names -- _merge_year_into_international_baseball_history
    # rewrites teams.json from the real LEAGUE_SPECS table on every call, so a
    # fictional team_id would silently stop matching after the merge and mask
    # the very regression this test exists to catch.
    old_year_game = {
        "game_id": "old-1",
        "game_date": f"{today.year - 2}-05-01",
        "home_team_id": "G",
        "away_team_id": "T",
        "home_score": 3,
        "away_score": 2,
        "tie": False,
    }
    (directory / "games.jsonl").write_text(json.dumps(old_year_game) + "\n", encoding="utf-8")
    (directory / "teams.json").write_text("{}", encoding="utf-8")
    (directory / "manifest.json").write_text("{}", encoding="utf-8")

    missing_game = {
        "game_id": "new-1",
        "game_date": today.isoformat(),
        "home_team_id": "G",
        "away_team_id": "T",
        "home_score": 5,
        "away_score": 1,
        "tie": False,
    }
    client = _FakeYearClient({today.year: [missing_game]})

    result = find_international_baseball_result(
        tmp_path, "npb", today.isoformat(), "Yomiuri Giants", "Hanshin Tigers", client=client
    )

    assert result == (1, 5)  # (away_score, home_score) -- the fallback fetch resolved it
    assert client.requested_years == [today.year]  # only the missing game's year, nothing else
    game_ids = {
        json.loads(line)["game_id"]
        for line in (directory / "games.jsonl").read_text().splitlines()
        if line.strip()
    }
    assert game_ids == {"old-1", "new-1"}  # old season survived the cache-miss fallback


def test_refresh_falls_back_to_full_backfill_when_no_history_exists(tmp_path) -> None:
    """If games.jsonl doesn't exist -- first run ever, or every prior daily
    attempt failed before writing anything -- a current-year-only refresh
    would permanently strand every earlier season. Must fall back to a real
    multi-year backfill instead."""
    from model_prediction.international_baseball import refresh_recent_international_baseball_matches

    today = eastern_today()
    client = _FakeYearClient({today.year: [_game_row("only-game", today.isoformat())]})

    result = refresh_recent_international_baseball_matches(tmp_path, "kbo", client=client)

    # minimum_year for kbo is 2015 -- a real fallback backfill requests
    # every year from there through today, not just the current year.
    assert len(client.requested_years) > 1
    assert client.requested_years[0] == 2015
    assert (tmp_path / "international_baseball/kbo/games.jsonl").exists()
    assert result["game_count"] == 1
