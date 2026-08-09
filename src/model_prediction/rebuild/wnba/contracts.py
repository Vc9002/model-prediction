"""Strict normalized WNBA table contracts."""

from __future__ import annotations

from model_prediction.rebuild.schemas import ColumnSpec, TableContract

WNBA_CONTRACTS: dict[str, TableContract] = {
    "games": TableContract(
        name="wnba_games_v1",
        primary_key=["event_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("event_id", str, False),
            ColumnSpec("season", int, False),
            ColumnSpec("event_start_utc", str, False),
            ColumnSpec("home_team_id", str, False),
            ColumnSpec("away_team_id", str, False),
            ColumnSpec("status", str, False),
            ColumnSpec("completed", bool, False),
            ColumnSpec("observed_at_utc", str, False),
            ColumnSpec("retrieved_at_utc", str, False),
            ColumnSpec("raw_snapshot_hash", str, False),
            ColumnSpec("pit_eligible", bool, False),
        ],
    ),
    "team_box": TableContract(
        name="wnba_team_box_v1",
        primary_key=["event_id", "team_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("event_id", str, False),
            ColumnSpec("season", int, False),
            ColumnSpec("team_id", str, False),
            ColumnSpec("home_away", str, False),
            ColumnSpec("observed_at_utc", str, False),
            ColumnSpec("raw_snapshot_hash", str, False),
            ColumnSpec("pit_eligible", bool, False),
        ],
    ),
    "player_box": TableContract(
        name="wnba_player_box_v1",
        primary_key=["event_id", "team_id", "player_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("event_id", str, False),
            ColumnSpec("season", int, False),
            ColumnSpec("team_id", str, False),
            ColumnSpec("player_id", str, False),
            ColumnSpec("observed_at_utc", str, False),
            ColumnSpec("raw_snapshot_hash", str, False),
            ColumnSpec("pit_eligible", bool, False),
        ],
    ),
    "rosters": TableContract(
        name="wnba_rosters_v1",
        primary_key=["season", "team_id", "player_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("season", int, False),
            ColumnSpec("team_id", str, False),
            ColumnSpec("player_id", str, False),
            ColumnSpec("player_name", str, False),
            ColumnSpec("observed_at_utc", str, False),
            ColumnSpec("raw_snapshot_hash", str, False),
            ColumnSpec("pit_eligible", bool, False),
        ],
    ),
    "pbp": TableContract(
        name="wnba_pbp_v1",
        primary_key=["event_id", "play_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("event_id", str, False),
            ColumnSpec("play_id", str, False),
            ColumnSpec("season", int, False),
            ColumnSpec("period", int, False),
            ColumnSpec("observed_at_utc", str, False),
            ColumnSpec("raw_snapshot_hash", str, False),
            ColumnSpec("pit_eligible", bool, False),
        ],
    ),
}
