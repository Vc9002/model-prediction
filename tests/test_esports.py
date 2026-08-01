import hashlib
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from model_prediction.data_sources.polymarket_us import POLYMARKET_SPORT_LEAGUES
from model_prediction.esports import (
    Bo3EsportsClient,
    NeutralElo,
    _metrics,
    forecast_esports_slate,
    refresh_recent_matches,
    validate_esports_baseline,
)


def test_polymarket_us_esports_taxonomy_is_explicit_and_complete() -> None:
    assert POLYMARKET_SPORT_LEAGUES["esports"] == (
        "LOL",
        "CS2",
        "COD",
        "VALORANT",
        "DOTA2",
        "ROCKET_LEAGUE",
        "OVERWATCH",
        "RAINBOW_SIX",
    )


def test_neutral_elo_has_no_team_order_advantage() -> None:
    book = NeutralElo(k=20, ratings={"a": 1600, "b": 1400})
    assert round(book.probability("a", "b") + book.probability("b", "a"), 12) == 1.0


def test_metrics_units_at_minus_110_matches_flat_stake_diagnostic() -> None:
    # 3 correct, 1 wrong at threshold 0 (all rows selected) -> 3*(10/11) - 1
    rows = [
        {"probability": 0.7, "outcome": 1},
        {"probability": 0.7, "outcome": 1},
        {"probability": 0.3, "outcome": 0},
        {"probability": 0.7, "outcome": 0},
    ]
    metrics = _metrics(rows)
    assert metrics["calls"] == 4
    assert metrics["hits"] == 3
    assert metrics["units_at_minus_110"] == pytest.approx(3 * (10 / 11) - 1)


def test_metrics_empty_selection_reports_zero_units_not_none() -> None:
    metrics = _metrics([], threshold=0.9)
    assert metrics["calls"] == 0
    assert metrics["hits"] == 0
    assert metrics["units_at_minus_110"] == 0.0


def test_cs2_backfill_excludes_legacy_csgo_game_version() -> None:
    rows = [
        {
            "id": 1,
            "slug": "old-csgo",
            "team1_id": 10,
            "team2_id": 20,
            "winner_team_id": 10,
            "team1_score": 2,
            "team2_score": 0,
            "status": "finished",
            "start_date": "2024-01-01T12:00:00.000+00:00",
            "end_date": "2024-01-01T14:00:00.000+00:00",
            "bo_type": 3,
            "discipline_id": 1,
            "game_version": 1,
        },
        {
            "id": 2,
            "slug": "current-cs2",
            "team1_id": 10,
            "team2_id": 20,
            "winner_team_id": 20,
            "team1_score": 1,
            "team2_score": 2,
            "status": "finished",
            "start_date": "2024-01-02T12:00:00.000+00:00",
            "end_date": "2024-01-02T15:00:00.000+00:00",
            "bo_type": 3,
            "discipline_id": 1,
            "game_version": 2,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/matches")
        return httpx.Response(
            200,
            json={"total": {"count": 2, "limit": 100, "offset": 0}, "results": rows},
        )

    source = Bo3EsportsClient(
        "https://example.test/api/v1",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    matches, _ = source.finished_matches("cs2", date(2024, 1, 1), date(2024, 1, 3))
    assert [row["match_id"] for row in matches] == ["bo3:2"]


def test_refresh_recent_matches_merges_instead_of_overwriting(tmp_path) -> None:
    """refresh_recent_matches must merge a short recent fetch into existing
    history, not replace it (unlike backfill_esports, which does a full-file
    overwrite and would silently delete older history if called with a
    short window on a schedule)."""
    directory = tmp_path / "esports" / "lol"
    directory.mkdir(parents=True)
    old_match = {
        "match_id": "bo3:1",
        "source_match_id": 1,
        "start_utc": "2024-01-01T00:00:00Z",
        "end_utc": "2024-01-01T01:00:00Z",
        "team1_id": "bo3:3:10",
        "team1_name": "Old Team A",
        "team1_score": 2,
        "team2_id": "bo3:3:20",
        "team2_name": "Old Team B",
        "team2_score": 0,
        "winner_id": "bo3:3:10",
        "best_of": 3,
        "tier": "a",
        "title": "lol",
        "tournament_id": 1,
        "game_version": None,
        "source_url": "https://bo3.gg/matches/old",
    }
    (directory / "matches.jsonl").write_text(json.dumps(old_match) + "\n", encoding="utf-8")
    old_teams = {
        "bo3:3:10": {"team_id": "bo3:3:10", "source_team_id": 10, "name": "Old Team A", "slug": "a", "acronym": "A"},
        "bo3:3:20": {"team_id": "bo3:3:20", "source_team_id": 20, "name": "Old Team B", "slug": "b", "acronym": "B"},
    }
    (directory / "teams.json").write_text(json.dumps(old_teams), encoding="utf-8")

    new_match_row = {
        "id": 2,
        "team1_id": 10,
        "team2_id": 30,
        "winner_team_id": 30,
        "team1_score": 0,
        "team2_score": 2,
        "status": "finished",
        "start_date": "2026-07-20T12:00:00.000+00:00",
        "end_date": "2026-07-20T14:00:00.000+00:00",
        "bo_type": 3,
        "discipline_id": 3,
        "game_version": None,
    }
    new_team_row = {"id": 30, "discipline_id": 3, "name": "New Team C", "slug": "c", "acronym": "C"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/matches"):
            return httpx.Response(
                200,
                json={"total": {"count": 1, "limit": 100, "offset": 0}, "results": [new_match_row]},
            )
        if request.url.path.endswith("/teams"):
            return httpx.Response(
                200,
                json={"total": {"count": 1, "limit": 100, "offset": 0}, "results": [new_team_row]},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = Bo3EsportsClient(
        "https://example.test/api/v1",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = refresh_recent_matches(tmp_path, "lol", lookback_days=14, client=client)

    assert result["new_or_updated_matches"] == 1
    assert result["total_matches"] == 2

    merged = [
        json.loads(line)
        for line in (directory / "matches.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    match_ids = {row["match_id"] for row in merged}
    assert match_ids == {"bo3:1", "bo3:2"}
    # The old match's team names must survive untouched (team 20 isn't in
    # this fetch's team catalog at all).
    old_row = next(row for row in merged if row["match_id"] == "bo3:1")
    assert old_row["team1_name"] == "Old Team A"
    assert old_row["team2_name"] == "Old Team B"

    teams = json.loads((directory / "teams.json").read_text(encoding="utf-8"))
    assert "bo3:3:20" in teams, "team not present in this fetch's page range must be preserved, not dropped"
    assert teams["bo3:3:20"]["name"] == "Old Team B"
    assert teams["bo3:3:30"]["name"] == "New Team C"

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["match_count"] == 2
    import hashlib

    assert manifest["matches_sha256"] == hashlib.sha256((directory / "matches.jsonl").read_bytes()).hexdigest()


def test_validation_is_chronological_versioned_and_never_promotes_baseline(tmp_path) -> None:
    directory = tmp_path / "esports/lol"
    directory.mkdir(parents=True)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(600):
        winner = "bo3:3:a" if index % 4 else "bo3:3:b"
        rows.append(
            {
                "match_id": f"bo3:{index}",
                "title": "lol",
                "start_utc": (start + timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
                "team1_id": "bo3:3:a",
                "team2_id": "bo3:3:b",
                "winner_id": winner,
            }
        )
    matches_path = directory / "matches.jsonl"
    matches_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    digest = hashlib.sha256(matches_path.read_bytes()).hexdigest()
    (directory / "manifest.json").write_text(
        json.dumps({"matches_sha256": digest}), encoding="utf-8"
    )

    result = validate_esports_baseline(tmp_path, "lol", tmp_path / "artifacts")

    assert result["status"] == "ok"
    assert result["chronological_split"]["train"]["n"] == 360
    assert result["chronological_split"]["locked_test"]["n"] == 120
    assert result["promotion_eligible"] is False
    assert result["units"] == 0
    artifact = json.loads((tmp_path / "artifacts/lol-tiered-elo-v5.json").read_text())
    assert artifact["qualified_for_betting"] is False
    assert artifact["model_state"] == "research"
    assert artifact["artifact_hash"] == result["artifact_hash"]


def test_forecast_requires_exact_identity_and_remains_zero_unit(tmp_path) -> None:
    data = tmp_path / "data/esports/lol"
    artifacts = tmp_path / "artifacts"
    data.mkdir(parents=True)
    artifacts.mkdir()
    (data / "teams.json").write_text(
        json.dumps(
            {
                "a": {"name": "Alpha Esports", "slug": "alpha-esports", "acronym": "ALP"},
                "b": {"name": "Beta Gaming", "slug": "beta-gaming", "acronym": "BET"},
            }
        )
    )
    (artifacts / "lol-tiered-elo-v5.json").write_text(
        json.dumps(
            {
                "model_version": "lol-tiered-elo-v5",
                "trained_through_utc": "2026-07-18T00:00:00Z",
                "artifact_hash": "hash",
                "k": 20,
                "ratings": {"a": 1600, "b": 1400},
                "platt_intercept": None,
                "platt_slope": None,
                "confidence_threshold": 0.10,
            }
        )
    )

    class FakeClient:
        def slate(self, league, game_date, timezone_name):
            assert league == "LOL"
            return [
                {
                    "event_id": "event",
                    "event_start_utc": "2099-01-01T12:00:00Z",
                    "markets": [
                        {
                            "market_type": "moneyline",
                            "market_slug": "market",
                            "sides": [
                                {"description": "Alpha Esports"},
                                {"description": "Beta Gaming"},
                            ],
                        }
                    ],
                }
            ]

        def snapshot(self, slug):
            return {
                "observed_at_utc": "2026-07-18T01:00:00Z",
                "long": {"description": "Alpha Esports", "ask": 0.7},
                "short": {"description": "Beta Gaming", "ask": 0.31},
            }

    result = forecast_esports_slate(
        tmp_path / "data", artifacts, "lol", "2099-01-01", client=FakeClient()
    )

    assert result["priced_count"] == 1
    assert result["priced_contracts"][0]["qualification"] == "NO_CALL_MODEL_UNVALIDATED"
    assert result["priced_contracts"][0]["units"] == 0
    assert result["priced_contracts"][0]["source_teams_resolved"] is True
    assert result["priced_contracts"][0]["source_teams_trained"] is True
    assert result["priced_contracts"][0]["gated_research_eligible"] is True
