"""Pure SportsDataverse WNBA source-to-canonical transformations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from model_prediction.rebuild.providers.base import SourceResponseMetadata
from model_prediction.rebuild.schemas import validate_or_raise

from .contracts import WNBA_CONTRACTS


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
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        # A mutable release downloaded now does not prove it was observed at
        # an earlier historical decision time.
        "availability_basis": "capture_time_only",
    }


def _schedule(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id = str(row["game_id"])
        event_start = _utc_iso(row["game_date_time"])
        completed = bool(row.get("status_type_completed", False))
        rows.append({
            **_provenance(metadata, event_id),
            "event_id": event_id,
            "season": int(row["season"]),
            "season_type": _as_int(row.get("season_type")),
            "event_start_utc": event_start,
            "status": str(row.get("status_type_name") or row.get("status_type_description") or "UNKNOWN"),
            "completed": completed,
            "home_team_id": str(row["home_id"]),
            "away_team_id": str(row["away_id"]),
            "home_team_name": str(row.get("home_display_name") or row.get("home_name") or ""),
            "away_team_name": str(row.get("away_display_name") or row.get("away_name") or ""),
            "home_score": _as_int(row.get("home_score")),
            "away_score": _as_int(row.get("away_score")),
            "venue_id": str(row.get("venue_id") or "") or None,
            "venue_name": row.get("venue_full_name"),
            "pit_eligible": True,
        })
    return pl.DataFrame(rows)


def _team_box(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id, team_id = str(row["game_id"]), str(row["team_id"])
        rows.append({
            **_provenance(metadata, f"{event_id}:{team_id}"),
            "event_id": event_id,
            "season": int(row["season"]),
            "event_start_utc": _utc_iso(row["game_date_time"]),
            "team_id": team_id,
            "opponent_team_id": str(row.get("opponent_team_id") or "") or None,
            "team_name": str(row.get("team_display_name") or row.get("team_name") or ""),
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
        })
    return pl.DataFrame(rows)


def _player_box(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id, team_id, player_id = str(row["game_id"]), str(row["team_id"]), str(row["athlete_id"])
        rows.append({
            **_provenance(metadata, f"{event_id}:{team_id}:{player_id}"),
            "event_id": event_id,
            "season": int(row["season"]),
            "event_start_utc": _utc_iso(row["game_date_time"]),
            "team_id": team_id,
            "player_id": player_id,
            "player_name": str(row.get("athlete_display_name") or ""),
            "starter": bool(row.get("starter", False)),
            "active": bool(row.get("active", False)),
            "did_not_play": bool(row.get("did_not_play", False)),
            "minutes_text": str(row.get("min") or "") or None,
            "points": _as_int(row.get("pts")),
            "rebounds": _as_int(row.get("reb")),
            "assists": _as_int(row.get("ast")),
            "plus_minus": _as_float(row.get("plus_minus")),
            "pit_eligible": True,
        })
    return pl.DataFrame(rows)


def _rosters(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        season, team_id, player_id = int(row["season"]), str(row["team_id"]), str(row["athlete_id"])
        rows.append({
            **_provenance(metadata, f"{season}:{team_id}:{player_id}"),
            "season": season,
            "team_id": team_id,
            "team_name": str(row.get("team_display_name") or ""),
            "player_id": player_id,
            "player_name": str(row.get("display_name") or row.get("full_name") or ""),
            "position": row.get("position_abbreviation"),
            "jersey": row.get("jersey"),
            "roster_status": row.get("status_name") or row.get("status_type"),
            "pit_eligible": True,
        })
    return pl.DataFrame(rows)


def _pbp(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id, play_id = str(row["game_id"]), str(row["id"])
        rows.append({
            **_provenance(metadata, f"{event_id}:{play_id}"),
            "event_id": event_id,
            "play_id": play_id,
            "season": int(row["season"]),
            "event_start_utc": _utc_iso(row["game_date_time"]),
            "period": int(row["period_number"]),
            "clock": str(row.get("clock_display_value") or row.get("time") or ""),
            "text": str(row.get("text") or ""),
            "team_id": str(row.get("team_id") or "") or None,
            "player_1_id": str(row.get("athlete_id_1") or "") or None,
            "home_score": _as_int(row.get("home_score")),
            "away_score": _as_int(row.get("away_score")),
            "scoring_play": bool(row.get("scoring_play", False)),
            "pit_eligible": True,
        })
    return pl.DataFrame(rows)


NORMALIZERS: dict[str, tuple[str, Callable[[pl.DataFrame, SourceResponseMetadata], pl.DataFrame]]] = {
    "schedule": ("games", _schedule),
    "team_box": ("team_box", _team_box),
    "player_box": ("player_box", _player_box),
    "rosters": ("rosters", _rosters),
    "pbp": ("pbp", _pbp),
}


def normalize_wnba_table(
    source_table: str, frame: pl.DataFrame, metadata: SourceResponseMetadata
) -> tuple[str, pl.DataFrame]:
    if source_table not in NORMALIZERS:
        raise ValueError(f"no WNBA normalizer for {source_table}")
    target, normalizer = NORMALIZERS[source_table]
    normalized = normalizer(frame, metadata)
    validate_or_raise(normalized, WNBA_CONTRACTS[target])
    return target, normalized
