"""Pure nflverse-to-canonical NFL transformations.

Every historical release row inherits the real capture time.  The normalizer
never rewrites it to a game date, season, or week because those values do not
prove when the mutable release row was observable.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from model_prediction.rebuild.providers.base import SourceResponseMetadata
from model_prediction.rebuild.schemas import validate_or_raise

from .contracts import NFL_CONTRACTS

NFLVERSE_GAME_TIMEZONE = ZoneInfo("America/New_York")


def _required_id(value: Any, field: str) -> str:
    if value is None:
        raise ValueError(f"missing NFL {field}")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    result = str(value).strip()
    if not result or result.lower() in {"nan", "none"}:
        raise ValueError(f"missing NFL {field}")
    return result


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


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _kickoff_utc(gameday: Any, gametime: Any) -> str:
    if isinstance(gameday, datetime):
        game_date = gameday.date()
    elif isinstance(gameday, date):
        game_date = gameday
    else:
        raw_date = str(gameday or "").strip()
        if not raw_date:
            raise ValueError("missing NFL gameday")
        game_date = date.fromisoformat(raw_date[:10])

    if isinstance(gametime, datetime):
        game_time = gametime.time().replace(tzinfo=None)
    elif isinstance(gametime, time):
        game_time = gametime.replace(tzinfo=None)
    else:
        raw_time = str(gametime or "").strip()
        if not raw_time:
            raise ValueError("missing NFL gametime")
        game_time = time.fromisoformat(raw_time)
    # nflverse schedules document gametime in US Eastern time, including
    # international games. ZoneInfo handles the season's DST transition.
    local = datetime.combine(game_date, game_time, tzinfo=NFLVERSE_GAME_TIMEZONE)
    return local.astimezone(UTC).isoformat()


def _provenance(metadata: SourceResponseMetadata, record_id: str) -> dict[str, Any]:
    # Effective time equals the real capture time for mutable release assets.
    # A historical game/week label is not substituted for availability.
    return {
        "source": metadata.provider,
        "source_record_id": record_id,
        "source_version": metadata.source_version or "unknown",
        "observed_at_utc": metadata.observed_at_utc,
        "effective_at_utc": metadata.observed_at_utc,
        "retrieved_at_utc": metadata.retrieved_at_utc,
        "ingested_at_utc": datetime.now(UTC).isoformat(),
        "raw_snapshot_hash": metadata.content_hash,
        "schema_version": "1",
        "source_grade": metadata.source_grade.value,
        "availability_basis": "capture_time_only_mutable_release",
        "pit_eligible": True,
        "license_id": metadata.license_id,
        "license_url": metadata.license_url,
        "attribution_required": metadata.attribution_required,
        "attribution_text": metadata.attribution_text,
        "upstream_rights_status": metadata.upstream_rights_status,
        "commercial_use_status": metadata.commercial_use_status,
        "production_allowed": metadata.production_allowed,
    }


def _schedule(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id = _required_id(row.get("game_id"), "game_id")
        home_score = _as_int(row.get("home_score"))
        away_score = _as_int(row.get("away_score"))
        rows.append(
            {
                **_provenance(metadata, event_id),
                "event_id": event_id,
                "season": int(row["season"]),
                "season_type": _required_id(row.get("game_type"), "game_type"),
                "week": int(row["week"]),
                "event_start_utc": _kickoff_utc(row.get("gameday"), row.get("gametime")),
                "home_team_id": _required_id(row.get("home_team"), "home_team"),
                "away_team_id": _required_id(row.get("away_team"), "away_team"),
                "home_score": home_score,
                "away_score": away_score,
                "completed": home_score is not None and away_score is not None,
                "overtime": _as_bool(row.get("overtime")),
                "stadium": str(row.get("stadium") or "") or None,
                "roof": str(row.get("roof") or "") or None,
                "surface": str(row.get("surface") or "") or None,
                "temperature_f": _as_float(row.get("temp")),
                "wind_mph": _as_float(row.get("wind")),
                "home_rest_days": _as_int(row.get("home_rest")),
                "away_rest_days": _as_int(row.get("away_rest")),
            }
        )
    return pl.DataFrame(rows)


def _pbp(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    complete_events = {
        _required_id(row.get("game_id"), "game_id")
        for row in frame.iter_rows(named=True)
        if "END GAME" in str(row.get("desc") or "").upper()
    }
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        event_id = _required_id(row.get("game_id"), "game_id")
        play_id = _required_id(row.get("play_id"), "play_id")
        rows.append(
            {
                **_provenance(metadata, f"{event_id}:{play_id}"),
                "event_id": event_id,
                "play_id": play_id,
                "season": int(row["season"]),
                "season_type": _required_id(row.get("season_type"), "season_type"),
                "week": int(row["week"]),
                "posteam_id": str(row.get("posteam") or "") or None,
                "defteam_id": str(row.get("defteam") or "") or None,
                "quarter": int(row["qtr"]),
                "down": _as_int(row.get("down")),
                "yards_to_go": _as_float(row.get("ydstogo")),
                "yardline_100": _as_float(row.get("yardline_100")),
                "game_seconds_remaining": _as_float(row.get("game_seconds_remaining")),
                "play_type": str(row.get("play_type") or ""),
                "description": str(row.get("desc") or ""),
                "epa": _as_float(row.get("epa")),
                "success": _as_float(row.get("success")),
                "pass_attempt": _as_bool(row.get("pass_attempt")),
                "rush_attempt": _as_bool(row.get("rush_attempt")),
                "touchdown": _as_bool(row.get("touchdown")),
                "interception": _as_bool(row.get("interception")),
                "fumble_lost": _as_bool(row.get("fumble_lost")),
                "home_score": _as_int(row.get("total_home_score")),
                "away_score": _as_int(row.get("total_away_score")),
                # Explicit END GAME evidence is required. A zero regulation clock
                # can precede overtime and therefore does not prove completion.
                "game_complete": event_id in complete_events,
            }
        )
    return pl.DataFrame(rows)


def _weekly_rosters(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        season = int(row["season"])
        week = int(row["week"])
        team_id = _required_id(row.get("team"), "team")
        player_id = _required_id(row.get("gsis_id"), "gsis_id")
        rows.append(
            {
                **_provenance(metadata, f"{season}:{week}:{team_id}:{player_id}"),
                "season": season,
                "week": week,
                "team_id": team_id,
                "player_id": player_id,
                "player_name": _required_id(row.get("full_name"), "full_name"),
                "position": _required_id(row.get("position"), "position"),
                "depth_chart_position": str(row.get("depth_chart_position") or "") or None,
                "jersey_number": _as_int(row.get("jersey_number")),
                "roster_status": str(row.get("status") or "") or None,
            }
        )
    return pl.DataFrame(rows)


NORMALIZERS: dict[str, tuple[str, Callable[[pl.DataFrame, SourceResponseMetadata], pl.DataFrame]]] = {
    "schedule": ("games", _schedule),
    "pbp": ("plays", _pbp),
    "weekly_rosters": ("weekly_rosters", _weekly_rosters),
}


def normalize_nfl_table(
    source_table: str,
    frame: pl.DataFrame,
    metadata: SourceResponseMetadata,
) -> tuple[str, pl.DataFrame]:
    if source_table not in NORMALIZERS:
        raise ValueError(f"no NFL normalizer for {source_table}")
    target, normalizer = NORMALIZERS[source_table]
    normalized = normalizer(frame, metadata)
    validate_or_raise(normalized, NFL_CONTRACTS[target])
    return target, normalized
