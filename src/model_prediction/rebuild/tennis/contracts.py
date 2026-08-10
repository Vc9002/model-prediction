"""Strict, vintage-preserving normalized tennis table contracts.

Two tables: `matches` (TennisMyLife historical/ongoing CSV releases -- the
Sackmann-style schema) and `current_events` (ESPN scoreboard captures).
Neither source exposes real match-start timestamps for historical rows
(TennisMyLife's `tourney_date` is date-only, one row per tournament, not
per match), so both tables carry `availability_basis =
"capture_time_only"`: the row proves what this repository observed and
when, never a replayable historical vintage.
"""

from __future__ import annotations

from model_prediction.rebuild.schemas import ColumnSpec, TableContract

RIGHTS_COLUMNS = [
    ColumnSpec("license_id", str, False),
    ColumnSpec("license_url", str, False),
    ColumnSpec("attribution_required", bool, False),
    ColumnSpec("attribution_text", str, True),
    ColumnSpec("upstream_rights_status", str, False),
    ColumnSpec("commercial_use_status", str, False),
    ColumnSpec("production_allowed", bool, False),
]

PROVENANCE_COLUMNS = [
    ColumnSpec("source", str, False),
    ColumnSpec("source_version", str, False),
    ColumnSpec("observed_at_utc", str, False),
    ColumnSpec("retrieved_at_utc", str, False),
    ColumnSpec("raw_snapshot_hash", str, False),
    ColumnSpec("availability_basis", str, False),
    ColumnSpec("pit_eligible", bool, False),
]

# result_type is derived from the raw score/status text, never left for a
# downstream consumer to re-parse: "completed" | "retirement" | "walkover"
# | "default" | "unfinished" | "unknown_irregular". A retirement/walkover/
# default/unfinished match must never be silently treated as an ordinary
# win/loss by a future model -- see normalize.py's _classify_result.
RESULT_COLUMNS = [
    ColumnSpec("result_type", str, False),
    ColumnSpec("completed", bool, False),
]

IDENTITY_COLUMNS = [
    # tennis_player_id is provider-scoped (f"{provider}:{provider_player_id}"),
    # not a cross-provider canonical entity -- resolving TennisMyLife and
    # ESPN rows to one shared player identity is real, separate,
    # not-yet-built work (the same kind of gap WNBA's IdentityRegistry
    # closes for that sport). Never derived from player_name.
    ColumnSpec("winner_tennis_player_id", str, False),
    ColumnSpec("winner_provider_player_id", str, False),
    ColumnSpec("winner_player_name", str, False),
    ColumnSpec("loser_tennis_player_id", str, False),
    ColumnSpec("loser_provider_player_id", str, False),
    ColumnSpec("loser_player_name", str, False),
]

TENNIS_CONTRACTS: dict[str, TableContract] = {
    "matches": TableContract(
        name="tennis_matches_v1",
        primary_key=["canonical_match_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            *PROVENANCE_COLUMNS,
            ColumnSpec("canonical_match_id", str, False),
            ColumnSpec("tour", str, False),
            ColumnSpec("match_kind", str, False),
            ColumnSpec("tourney_id", str, False),
            ColumnSpec("tourney_name", str, False),
            ColumnSpec("surface", str, True),
            ColumnSpec("tourney_level", str, True),
            ColumnSpec("tourney_date", str, False),
            ColumnSpec("match_num", int, True),
            ColumnSpec("round", str, True),
            ColumnSpec("best_of", int, True),
            ColumnSpec("minutes", int, True),
            *IDENTITY_COLUMNS,
            ColumnSpec("winner_rank", int, True),
            ColumnSpec("loser_rank", int, True),
            ColumnSpec("winner_seed", str, True),
            ColumnSpec("loser_seed", str, True),
            ColumnSpec("score_raw", str, True),
            *RESULT_COLUMNS,
            *RIGHTS_COLUMNS,
        ],
    ),
    "current_events": TableContract(
        name="tennis_current_events_v1",
        primary_key=["canonical_match_id", "observed_at_utc"],
        conflict_policy="fail_closed",
        columns=[
            *PROVENANCE_COLUMNS,
            ColumnSpec("canonical_match_id", str, False),
            ColumnSpec("tour", str, False),
            ColumnSpec("tournament_name", str, True),
            ColumnSpec("grouping_slug", str, True),
            ColumnSpec("event_start_utc", str, True),
            ColumnSpec("status_name", str, False),
            ColumnSpec("status_state", str, False),
            ColumnSpec("competitor_1_tennis_player_id", str, False),
            ColumnSpec("competitor_1_provider_player_id", str, False),
            ColumnSpec("competitor_1_player_name", str, False),
            ColumnSpec("competitor_1_winner", bool, True),
            ColumnSpec("competitor_2_tennis_player_id", str, False),
            ColumnSpec("competitor_2_provider_player_id", str, False),
            ColumnSpec("competitor_2_player_name", str, False),
            ColumnSpec("competitor_2_winner", bool, True),
            *RESULT_COLUMNS,
            *RIGHTS_COLUMNS,
        ],
    ),
}
