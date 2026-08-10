"""Strict, vintage-preserving normalized NFL table contracts."""

from __future__ import annotations

from model_prediction.rebuild.schemas import ColumnSpec, TableContract

NFLVERSE_RIGHTS_COLUMNS = [
    ColumnSpec("license_id", str, False),
    ColumnSpec("license_url", str, False),
    ColumnSpec("attribution_required", bool, False),
    ColumnSpec("attribution_text", str, False),
    ColumnSpec("upstream_rights_status", str, False),
    ColumnSpec("commercial_use_status", str, False),
    ColumnSpec("production_allowed", bool, False),
]

NFL_CONTRACTS: dict[str, TableContract] = {
    "games": TableContract(
        name="nfl_games_v1",
        primary_key=["event_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("event_id", str, False),
            ColumnSpec("season", int, False),
            ColumnSpec("season_type", str, False),
            ColumnSpec("week", int, False),
            ColumnSpec("event_start_utc", str, False),
            ColumnSpec("home_team_id", str, False),
            ColumnSpec("away_team_id", str, False),
            ColumnSpec("completed", bool, False),
            ColumnSpec("observed_at_utc", str, False),
            ColumnSpec("effective_at_utc", str, False),
            ColumnSpec("retrieved_at_utc", str, False),
            ColumnSpec("raw_snapshot_hash", str, False),
            ColumnSpec("pit_eligible", bool, False),
            ColumnSpec("availability_basis", str, False),
            *NFLVERSE_RIGHTS_COLUMNS,
        ],
    ),
    "plays": TableContract(
        name="nfl_plays_v1",
        primary_key=["event_id", "play_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("event_id", str, False),
            ColumnSpec("play_id", str, False),
            ColumnSpec("season", int, False),
            ColumnSpec("week", int, False),
            ColumnSpec("quarter", int, False),
            ColumnSpec("game_complete", bool, False),
            ColumnSpec("observed_at_utc", str, False),
            ColumnSpec("effective_at_utc", str, False),
            ColumnSpec("retrieved_at_utc", str, False),
            ColumnSpec("raw_snapshot_hash", str, False),
            ColumnSpec("pit_eligible", bool, False),
            ColumnSpec("availability_basis", str, False),
            *NFLVERSE_RIGHTS_COLUMNS,
        ],
    ),
    "weekly_rosters": TableContract(
        name="nfl_weekly_rosters_v1",
        primary_key=[
            "season",
            "week",
            "team_id",
            "player_id",
            "observed_at_utc",
        ],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("season", int, False),
            ColumnSpec("week", int, False),
            ColumnSpec("team_id", str, False),
            ColumnSpec("player_id", str, False),
            ColumnSpec("player_name", str, False),
            ColumnSpec("position", str, False),
            ColumnSpec("observed_at_utc", str, False),
            ColumnSpec("effective_at_utc", str, False),
            ColumnSpec("retrieved_at_utc", str, False),
            ColumnSpec("raw_snapshot_hash", str, False),
            ColumnSpec("pit_eligible", bool, False),
            ColumnSpec("availability_basis", str, False),
            *NFLVERSE_RIGHTS_COLUMNS,
        ],
    ),
}
