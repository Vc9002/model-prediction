"""Pure SportsDataverse WNBA source-to-canonical transformations."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from model_prediction.rebuild.identity import (
    IdentityRegistry,
    resolve_espn_scoreboard_event_id,
    resolve_or_register_player,
    resolve_or_register_team,
)
from model_prediction.rebuild.providers.base import SourceResponseMetadata
from model_prediction.rebuild.schemas import validate_or_raise

from .contracts import WNBA_CONTRACTS
from .time import sports_event_date

ESPN_WNBA_IDENTITY_SOURCE = "espn_public:wnba"


def _team_canonical_id(
    registry: IdentityRegistry,
    source_id: Any,
    team_name: Any,
    effective_from_utc: str,
) -> str:
    value = str(source_id or "")
    name = str(team_name or "").strip()
    if not value or not name:
        raise ValueError("missing WNBA source team ID or name")
    return resolve_or_register_team(
        registry,
        sport="wnba",
        source_id=ESPN_WNBA_IDENTITY_SOURCE,
        source_team_id=value,
        team_name=name,
        effective_from_utc=effective_from_utc,
    ).entity_id


def _player_canonical_id(
    registry: IdentityRegistry,
    source_id: Any,
    player_name: Any,
    effective_from_utc: str,
) -> str:
    value = str(source_id or "")
    name = str(player_name or "").strip()
    if not value or not name:
        raise ValueError("missing WNBA source player ID or name")
    return resolve_or_register_player(
        registry,
        sport="wnba",
        source_id=ESPN_WNBA_IDENTITY_SOURCE,
        source_player_id=value,
        player_name=name,
        effective_from_utc=effective_from_utc,
    ).entity_id


def _existing_identity(registry: IdentityRegistry, source_entity_id: Any, entity_type: str) -> str | None:
    value = str(source_entity_id or "")
    if not value:
        return None
    identity = registry.resolve(ESPN_WNBA_IDENTITY_SOURCE, value)
    if identity is None or identity.entity_type != entity_type or identity.sport != "wnba":
        raise ValueError(f"unresolved WNBA {entity_type} identity for ESPN ID {value}")
    return identity.entity_id


def _utc_iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timezone-naive WNBA event timestamp")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        raise TypeError("date-only WNBA event timestamp cannot be treated as UTC")
    raw = str(value or "")
    if not raw:
        raise ValueError("missing WNBA event timestamp")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError("timezone-naive WNBA event timestamp")
    return parsed.astimezone(UTC).isoformat()


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid WNBA integer: {value!r}") from exc
    if not numeric.is_integer():
        raise ValueError(f"invalid WNBA integer: {value!r}")
    return int(numeric)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid WNBA number: {value!r}") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"invalid WNBA number: {value!r}")
    return numeric


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0", ""}:
            return False
    if value is None:
        return False
    raise ValueError(f"invalid WNBA boolean: {value!r}")


def _provenance(metadata: SourceResponseMetadata, record_id: str) -> dict[str, Any]:
    return {
        "source": metadata.provider,
        "source_record_id": record_id,
        "source_version": metadata.source_version or "unknown",
        "observed_at_utc": metadata.observed_at_utc,
        "retrieved_at_utc": metadata.retrieved_at_utc,
        "ingested_at_utc": datetime.now(UTC).isoformat(),
        "raw_snapshot_hash": metadata.content_hash,
        "schema_version": "1",
        "source_grade": metadata.source_grade.value,
        "commercial_use_status": metadata.commercial_use_status,
        "production_allowed": metadata.production_allowed,
        # A mutable release downloaded now does not prove it was observed at
        # an earlier historical decision time.
        "availability_basis": "capture_time_only",
    }


def _schedule(
    frame: pl.DataFrame,
    metadata: SourceResponseMetadata,
    registry: IdentityRegistry,
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id = str(row["game_id"])
        event_start = _utc_iso(row["game_date_time"])
        completed = _as_bool(row.get("status_type_completed", False))
        home_name = str(row["home_display_name"])
        away_name = str(row["away_display_name"])
        home_canonical_id = _team_canonical_id(registry, row["home_id"], home_name, event_start)
        away_canonical_id = _team_canonical_id(registry, row["away_id"], away_name, event_start)
        event_canonical_id = resolve_espn_scoreboard_event_id(
            registry,
            "wnba",
            "espn_public",
            event_id,
            {"id": row["home_id"], "displayName": home_name},
            {"id": row["away_id"], "displayName": away_name},
            home_canonical_id,
            away_canonical_id,
            event_start,
            metadata.observed_at_utc,
            str(row.get("venue_full_name") or ""),
        )
        if event_canonical_id is None:
            raise ValueError(f"unresolved canonical WNBA event: {event_id}")
        rows.append(
            {
                **_provenance(metadata, event_id),
                "event_id": event_id,
                "season": int(row["season"]),
                "season_type": _as_int(row.get("season_type")),
                "event_start_utc": event_start,
                "sports_event_date": sports_event_date(event_start),
                "status": str(row.get("status_type_name") or row.get("status_type_description") or "UNKNOWN"),
                "completed": completed,
                "home_team_id": str(row["home_id"]),
                "away_team_id": str(row["away_id"]),
                "home_team_canonical_id": home_canonical_id,
                "away_team_canonical_id": away_canonical_id,
                "event_canonical_id": event_canonical_id,
                "home_team_name": home_name,
                "away_team_name": away_name,
                "home_score": _as_int(row.get("home_score")),
                "away_score": _as_int(row.get("away_score")),
                "venue_id": str(row.get("venue_id") or "") or None,
                "venue_name": row.get("venue_full_name"),
                "pit_eligible": True,
            }
        )
    return pl.DataFrame(rows)


def _team_box(
    frame: pl.DataFrame,
    metadata: SourceResponseMetadata,
    registry: IdentityRegistry,
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id, team_id = str(row["game_id"]), str(row["team_id"])
        event_start = _utc_iso(row["game_date_time"])
        team_name = str(row["team_display_name"])
        team_canonical_id = _team_canonical_id(registry, team_id, team_name, event_start)
        opponent_id = str(row["opponent_team_id"])
        opponent_canonical_id = _existing_identity(registry, opponent_id, "team")
        if opponent_canonical_id is None:
            raise ValueError(f"missing WNBA opponent ID for {event_id}:{team_id}")
        rows.append(
            {
                **_provenance(metadata, f"{event_id}:{team_id}"),
                "event_id": event_id,
                "season": int(row["season"]),
                "event_start_utc": event_start,
                "sports_event_date": sports_event_date(event_start),
                "team_id": team_id,
                "team_canonical_id": team_canonical_id,
                "opponent_team_id": opponent_id,
                "opponent_team_canonical_id": opponent_canonical_id,
                "team_name": team_name,
                "home_away": str(row["team_home_away"]),
                "points": _as_int(row.get("team_score")),
                "field_goals_made": _as_int(row.get("field_goals_made")),
                "field_goals_attempted": _as_int(row.get("field_goals_attempted")),
                "three_points_made": _as_int(row.get("three_point_field_goals_made")),
                "three_points_attempted": _as_int(row.get("three_point_field_goals_attempted")),
                "free_throws_made": _as_int(row.get("free_throws_made")),
                "free_throws_attempted": _as_int(row.get("free_throws_attempted")),
                "offensive_rebounds": _as_int(row.get("offensive_rebounds")),
                "defensive_rebounds": _as_int(row.get("defensive_rebounds")),
                "assists": _as_int(row.get("assists")),
                "turnovers": _as_int(row.get("turnovers") or row.get("team_turnovers")),
                "pit_eligible": True,
            }
        )
    return pl.DataFrame(rows)


def _player_box(
    frame: pl.DataFrame,
    metadata: SourceResponseMetadata,
    registry: IdentityRegistry,
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id, team_id, player_id = str(row["game_id"]), str(row["team_id"]), str(row["athlete_id"])
        event_start = _utc_iso(row["game_date_time"])
        player_name = str(row["athlete_display_name"])
        team_canonical_id = _existing_identity(registry, team_id, "team")
        if team_canonical_id is None:
            raise ValueError(f"missing WNBA team ID for player {player_id}")
        rows.append(
            {
                **_provenance(metadata, f"{event_id}:{team_id}:{player_id}"),
                "event_id": event_id,
                "season": int(row["season"]),
                "event_start_utc": event_start,
                "sports_event_date": sports_event_date(event_start),
                "team_id": team_id,
                "player_id": player_id,
                "team_canonical_id": team_canonical_id,
                "player_canonical_id": _player_canonical_id(
                    registry,
                    player_id,
                    player_name,
                    event_start,
                ),
                "player_name": player_name,
                "starter": _as_bool(row.get("starter", False)),
                "active": _as_bool(row.get("active", False)),
                "did_not_play": _as_bool(row.get("did_not_play", False)),
                "minutes_text": str(row.get("min") or "") or None,
                "points": _as_int(row.get("pts")),
                "rebounds": _as_int(row.get("reb")),
                "assists": _as_int(row.get("ast")),
                "plus_minus": _as_float(row.get("plus_minus")),
                "pit_eligible": True,
            }
        )
    return pl.DataFrame(rows)


def _rosters(
    frame: pl.DataFrame,
    metadata: SourceResponseMetadata,
    registry: IdentityRegistry,
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        season, team_id, player_id = int(row["season"]), str(row["team_id"]), str(row["athlete_id"])
        team_name = str(row["team_display_name"])
        player_name = str(row["display_name"])
        team_canonical_id = _team_canonical_id(
            registry,
            team_id,
            team_name,
            metadata.observed_at_utc,
        )
        rows.append(
            {
                **_provenance(metadata, f"{season}:{team_id}:{player_id}"),
                "season": season,
                "team_id": team_id,
                "team_canonical_id": team_canonical_id,
                "team_name": team_name,
                "player_id": player_id,
                "player_canonical_id": _player_canonical_id(
                    registry,
                    player_id,
                    player_name,
                    metadata.observed_at_utc,
                ),
                "player_name": player_name,
                "position": row.get("position_abbreviation"),
                "jersey": row.get("jersey"),
                "roster_status": row.get("status_name") or row.get("status_type"),
                "pit_eligible": True,
            }
        )
    return pl.DataFrame(rows)


def _pbp(
    frame: pl.DataFrame,
    metadata: SourceResponseMetadata,
    registry: IdentityRegistry,
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id, play_id = str(row["game_id"]), str(row["id"])
        event_start = _utc_iso(row["game_date_time"])
        rows.append(
            {
                **_provenance(metadata, f"{event_id}:{play_id}"),
                "event_id": event_id,
                "play_id": play_id,
                "season": int(row["season"]),
                "event_start_utc": event_start,
                "sports_event_date": sports_event_date(event_start),
                "period": int(row["period_number"]),
                "clock": str(row.get("clock_display_value") or row.get("time") or ""),
                "text": str(row.get("text") or ""),
                "team_id": str(row.get("team_id") or "") or None,
                "team_canonical_id": _existing_identity(registry, row.get("team_id"), "team"),
                "player_1_id": str(row.get("athlete_id_1") or "") or None,
                "player_1_canonical_id": _existing_identity(
                    registry,
                    row.get("athlete_id_1"),
                    "player",
                ),
                "home_score": _as_int(row.get("home_score")),
                "away_score": _as_int(row.get("away_score")),
                "scoring_play": _as_bool(row.get("scoring_play", False)),
                "pit_eligible": True,
            }
        )
    return pl.DataFrame(rows)


NORMALIZERS: dict[
    str,
    tuple[str, Callable[[pl.DataFrame, SourceResponseMetadata, IdentityRegistry], pl.DataFrame]],
] = {
    "schedule": ("games", _schedule),
    "team_box": ("team_box", _team_box),
    "player_box": ("player_box", _player_box),
    "rosters": ("rosters", _rosters),
    "pbp": ("pbp", _pbp),
}


def normalize_wnba_table(
    source_table: str,
    frame: pl.DataFrame,
    metadata: SourceResponseMetadata,
    *,
    identity: IdentityRegistry,
) -> tuple[str, pl.DataFrame]:
    if source_table not in NORMALIZERS:
        raise ValueError(f"no WNBA normalizer for {source_table}")
    target, normalizer = NORMALIZERS[source_table]
    normalized = normalizer(frame, metadata, identity)
    validate_or_raise(normalized, WNBA_CONTRACTS[target])
    return target, normalized
