from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from model_prediction.rebuild.wnba.features import build_team_form_snapshot
from model_prediction.rebuild.wnba.store import WNBANormalizedStore


def _games() -> pl.DataFrame:
    return pl.DataFrame({
        "event_id": ["g1", "g2"],
        "event_start_utc": ["2024-05-01T00:00:00+00:00", "2024-05-03T00:00:00+00:00"],
        "observed_at_utc": ["2026-08-09T00:00:00+00:00", "2026-08-09T00:00:00+00:00"],
        "completed": [True, True],
        "pit_eligible": [True, True],
    })


def _boxes() -> pl.DataFrame:
    rows = []
    for event_id, team_points, opponent_points in [("g1", 80, 70), ("g2", 90, 85)]:
        rows.extend([
            {
                "event_id": event_id,
                "event_start_utc": "2024-05-01T00:00:00+00:00",
                "observed_at_utc": "2026-08-09T00:00:00+00:00",
                "pit_eligible": True,
                "team_id": "A",
                "opponent_team_id": "B",
                "points": team_points,
                "field_goals_made": 30,
                "field_goals_attempted": 70,
                "three_points_made": 8,
                "three_points_attempted": 24,
                "free_throws_made": 12,
                "free_throws_attempted": 16,
                "offensive_rebounds": 10,
                "defensive_rebounds": 25,
                "turnovers": 12,
            },
            {
                "event_id": event_id,
                "event_start_utc": "2024-05-01T00:00:00+00:00",
                "observed_at_utc": "2026-08-09T00:00:00+00:00",
                "pit_eligible": True,
                "team_id": "B",
                "opponent_team_id": "A",
                "points": opponent_points,
                "field_goals_made": 27,
                "field_goals_attempted": 68,
                "three_points_made": 7,
                "three_points_attempted": 22,
                "free_throws_made": 9,
                "free_throws_attempted": 12,
                "offensive_rebounds": 8,
                "defensive_rebounds": 24,
                "turnovers": 14,
            },
        ])
    return pl.DataFrame(rows)


def test_team_form_is_prior_only_and_has_expected_windows():
    result = build_team_form_snapshot(
        _games(),
        _boxes(),
        team_id="A",
        decision_time_utc=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert result["status"] == "AVAILABLE"
    assert result["sample_size"] == 2
    assert result["last_5_sample"] == 2
    assert result["last_10_sample"] == 2
    assert result["last_20_sample"] == 2
    assert result["season_sample"] == 2
    assert result["season_ortg"] is not None
    assert result["season_drtg"] is not None
    assert result["season_netrtg"] > 0
    assert result["season_efg"] > 0


def test_future_observation_is_excluded():
    boxes = _boxes().with_columns(pl.lit("2026-08-11T00:00:00+00:00").alias("observed_at_utc"))
    result = build_team_form_snapshot(
        _games(),
        boxes,
        team_id="A",
        decision_time_utc=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["sample_size"] == 0


def test_persisted_and_in_memory_paths_use_same_transform(tmp_path):
    store = WNBANormalizedStore(tmp_path)
    store.write("games", 2024, _games())
    store.write("team_box", 2024, _boxes())
    decision = datetime(2026, 8, 10, tzinfo=UTC)
    in_memory = build_team_form_snapshot(_games(), _boxes(), team_id="A", decision_time_utc=decision)
    persisted = build_team_form_snapshot(
        store.read_season("games", 2024),
        store.read_season("team_box", 2024),
        team_id="A",
        decision_time_utc=decision,
    )
    assert persisted == in_memory
