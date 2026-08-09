"""Versioned normalized contracts for the MLB v3 data foundation."""

from __future__ import annotations

from model_prediction.rebuild.schemas import ColumnSpec, TableContract

PROVENANCE_COLUMNS = [
    ColumnSpec("observed_at_utc", str, False),
    ColumnSpec("retrieved_at_utc", str, False),
    ColumnSpec("source", str, False),
    ColumnSpec("source_event_id", str, False),
    ColumnSpec("raw_snapshot_hash", str, False),
    ColumnSpec("availability_basis", str, False),
    ColumnSpec("pit_eligible", bool, False),
    ColumnSpec("commercial_use_status", str, False),
    ColumnSpec("production_allowed", bool, False),
]

MLB_V3_CONTRACTS: dict[str, TableContract] = {
    "games": TableContract(
        name="mlb_v3_games_v1",
        primary_key=["game_pk", "period", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("canonical_event_id", str, False),
            ColumnSpec("game_pk", int, False),
            ColumnSpec("season", int, False),
            ColumnSpec("game_date", str, False),
            ColumnSpec("event_start_utc", str, False),
            ColumnSpec("home_team_id", str, False),
            ColumnSpec("away_team_id", str, False),
            ColumnSpec("doubleheader_number", int, False),
            ColumnSpec("period", str, False),
            ColumnSpec("status", str, False),
            ColumnSpec("postponed", bool, False),
            ColumnSpec("delayed", bool, False),
            ColumnSpec("suspended", bool, False),
            ColumnSpec("resumed", bool, False),
            ColumnSpec("rescheduled_from_date", str, True),
            ColumnSpec("reschedule_date", str, True),
            ColumnSpec("resume_date", str, True),
            ColumnSpec("original_date", str, True),
            *PROVENANCE_COLUMNS,
        ],
    ),
    "probable_pitchers": TableContract(
        name="mlb_v3_probable_pitchers_v1",
        primary_key=["game_pk", "team_side", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("canonical_event_id", str, False),
            ColumnSpec("game_pk", int, False),
            ColumnSpec("team_side", str, False),
            ColumnSpec("team_id", str, False),
            ColumnSpec("pitcher_id", str, True),
            ColumnSpec("pitcher_name", str, True),
            *PROVENANCE_COLUMNS,
        ],
    ),
    "lineups": TableContract(
        name="mlb_v3_lineups_v1",
        primary_key=["game_pk", "team_side", "batting_order", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("canonical_event_id", str, False),
            ColumnSpec("game_pk", int, False),
            ColumnSpec("team_side", str, False),
            ColumnSpec("team_id", str, False),
            ColumnSpec("batting_order", int, False),
            ColumnSpec("player_id", str, False),
            ColumnSpec("confirmation_state", str, False),
            *PROVENANCE_COLUMNS,
        ],
    ),
    "rosters": TableContract(
        name="mlb_v3_game_rosters_v1",
        primary_key=["game_pk", "team_side", "player_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("canonical_event_id", str, False),
            ColumnSpec("game_pk", int, False),
            ColumnSpec("team_side", str, False),
            ColumnSpec("team_id", str, False),
            ColumnSpec("player_id", str, False),
            *PROVENANCE_COLUMNS,
        ],
    ),
    "transactions": TableContract(
        name="mlb_v3_transactions_v1",
        primary_key=["transaction_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("transaction_id", str, False),
            ColumnSpec("transaction_date", str, True),
            ColumnSpec("effective_date", str, True),
            ColumnSpec("player_id", str, True),
            ColumnSpec("from_team_id", str, True),
            ColumnSpec("to_team_id", str, True),
            ColumnSpec("transaction_type", str, True),
            *PROVENANCE_COLUMNS,
        ],
    ),
    "statcast_pitches": TableContract(
        name="mlb_v3_statcast_pitches_v1",
        primary_key=["game_pk", "at_bat_number", "pitch_number", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("canonical_event_id", str, False),
            ColumnSpec("game_pk", int, False),
            ColumnSpec("game_date", str, False),
            ColumnSpec("at_bat_number", int, False),
            ColumnSpec("pitch_number", int, False),
            ColumnSpec("pitcher_id", str, True),
            ColumnSpec("batter_id", str, True),
            ColumnSpec("pitch_type", str, True),
            *PROVENANCE_COLUMNS,
        ],
    ),
    "weather_forecasts": TableContract(
        name="mlb_v3_weather_forecasts_v1",
        primary_key=["game_pk", "valid_time_utc", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            ColumnSpec("canonical_event_id", str, False),
            ColumnSpec("game_pk", int, False),
            ColumnSpec("valid_time_utc", str, False),
            ColumnSpec("forecast_issued_at_utc", str, True),
            ColumnSpec("weather_source_quality", str, False),
            *PROVENANCE_COLUMNS,
        ],
    ),
}

PRIMARY_KEYS = {name: contract.primary_key for name, contract in MLB_V3_CONTRACTS.items()}
