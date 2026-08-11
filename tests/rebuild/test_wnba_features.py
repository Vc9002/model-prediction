"""Tests for the recovered WNBA rolling-form feature engine (features.py),
ported from the archived `origin/rebuild/wnba-v1` branch alongside the
source file itself -- see `docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md`
for the RECOVER verdict this is executing on. Verified directly against
current `main`'s WNBA normalize/store/pit schema (byte-identical to the
branch this was written against), not re-derived from scratch.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from model_prediction.rebuild.wnba.features import build_team_form_snapshot
from model_prediction.rebuild.wnba.store import WNBANormalizedStore


def _games() -> pl.DataFrame:
    return pl.DataFrame({
        "event_id": ["g1", "g2"],
        "event_start_utc": ["2024-05-01T00:00:00+00:00", "2024-05-03T00:00:00+00:00"],
        "sports_event_date": ["2024-04-30", "2024-05-02"],
        "observed_at_utc": ["2026-08-09T00:00:00+00:00", "2026-08-09T00:00:00+00:00"],
        "raw_snapshot_hash": ["game-g1", "game-g2"],
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
                "raw_snapshot_hash": f"box-{event_id}-a",
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
                "raw_snapshot_hash": f"box-{event_id}-b",
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


def test_latest_asof_game_state_is_selected_before_completed_filter():
    games = pl.concat([
        _games().head(1).with_columns(
            pl.lit("2026-08-09T00:00:00+00:00").alias("observed_at_utc"),
            pl.lit(True).alias("completed"),
        ),
        _games().head(1).with_columns(
            pl.lit("2026-08-09T01:00:00+00:00").alias("observed_at_utc"),
            pl.lit(False).alias("completed"),
        ),
    ])
    result = build_team_form_snapshot(
        games,
        _boxes().filter(pl.col("event_id") == "g1"),
        team_id="A",
        decision_time_utc=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["sample_size"] == 0


def test_offset_decision_does_not_admit_box_observed_after_same_instant():
    # 2026-08-10 05:00 +05:00 is exactly 2026-08-10 00:00 UTC. The box
    # observed at 01:00 UTC is one hour in the future and must be excluded.
    boxes = _boxes().with_columns(pl.lit("2026-08-10T01:00:00+00:00").alias("observed_at_utc"))
    decision = datetime.fromisoformat("2026-08-10T05:00:00+05:00")
    result = build_team_form_snapshot(
        _games(),
        boxes,
        team_id="A",
        decision_time_utc=decision,
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["as_of_utc"] == "2026-08-10T00:00:00+00:00"


def test_real_backfilled_2024_data_produces_a_real_available_snapshot():
    """Not a synthetic fixture -- exercises the actual normalized store this
    session backfilled from SportsDataverse (`data/rebuild/normalized/wnba`,
    season 2024), matching CLAUDE.md's "test against real, live data" shadow
    -feature-pattern guidance rather than relying on fixtures alone.

    Real finding from running this against the actual backfill: every row's
    `observed_at_utc` is the *capture* time (today), not a per-game
    historical vintage -- exactly the "capture_time_only" caveat
    `docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md` already documents
    for this data source. A `decision_time_utc` set to a date inside the
    season (e.g. 2024-12-31) therefore yields zero eligible prior games,
    every time -- `observed_at_utc` (today) is always after it. Only a
    decision time at-or-after the real capture time reflects what this
    single-vintage data can actually support."""
    store = WNBANormalizedStore("data/rebuild/normalized")
    games = store.read_latest("games", 2024)
    team_box = store.read_latest("team_box", 2024)
    if games.is_empty() or team_box.is_empty():
        import pytest

        pytest.skip("2024 WNBA data not backfilled in this environment")
    any_team_id = str(team_box["team_id"][0])
    decision = datetime.now(UTC)
    result = build_team_form_snapshot(
        games, team_box, team_id=any_team_id, decision_time_utc=decision,
    )
    assert result["status"] == "AVAILABLE"
    assert result["sample_size"] > 0
    assert result["season_ortg"] is not None
    assert result["season_pace"] is not None
