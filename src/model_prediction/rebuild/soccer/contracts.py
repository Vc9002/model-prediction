"""Strict append-only soccer match observation contract."""

from __future__ import annotations

from model_prediction.rebuild.schemas import ColumnSpec, TableContract

SOCCER_RIGHTS_COLUMNS = [
    ColumnSpec("source_asset", str, False),
    ColumnSpec("provider_chain", str, False),
    ColumnSpec("license_id", str, False),
    ColumnSpec("license_url", str, False),
    ColumnSpec("attribution_required", bool, False),
    ColumnSpec("attribution_text", str, True),
    ColumnSpec("subscription_required", bool, False),
    ColumnSpec("subscription_scope", str, False),
    ColumnSpec("upstream_rights_status", str, False),
    ColumnSpec("commercial_use_status", str, False),
    ColumnSpec("use_scope", str, False),
    ColumnSpec("production_allowed", bool, False),
]

SOCCER_MATCHES = TableContract(
    name="soccer_matches_v2",
    primary_key=["source", "source_match_id", "observed_at_utc"],
    conflict_policy="fail_closed",
    columns=[
        ColumnSpec("source", str, False),
        ColumnSpec("source_version", str, False),
        ColumnSpec("source_match_id", str, False),
        ColumnSpec("competition_id", str, False),
        ColumnSpec("competition_name", str, False),
        ColumnSpec("season_id", str, False),
        ColumnSpec("event_start_utc", str, False),
        ColumnSpec("status", str, False),
        ColumnSpec("completed", bool, False),
        ColumnSpec("home_team_id", str, False),
        ColumnSpec("home_team_name", str, False),
        ColumnSpec("away_team_id", str, False),
        ColumnSpec("away_team_name", str, False),
        ColumnSpec("home_score", int, True),
        ColumnSpec("away_score", int, True),
        ColumnSpec("venue_id", str, True),
        ColumnSpec("venue_name", str, True),
        ColumnSpec("provider_updated_at_utc", str, True),
        ColumnSpec("observed_at_utc", str, False),
        ColumnSpec("available_at_utc", str, False),
        ColumnSpec("raw_snapshot_hash", str, False),
        ColumnSpec("availability_basis", str, False),
        ColumnSpec("timestamp_valid", bool, False),
        ColumnSpec("pit_eligible", bool, False),
        *SOCCER_RIGHTS_COLUMNS,
        ColumnSpec("schema_version", str, False),
    ],
)
