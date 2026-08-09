"""Pure source-to-normalized transforms for MLB v3."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import polars as pl

from model_prediction.rebuild.providers.base import SourceResponseMetadata
from model_prediction.rebuild.schemas import validate_or_raise

from .contracts import MLB_V3_CONTRACTS


def _utc_iso(value: Any) -> str:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("MLB v3 timestamp must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


def _text_id(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _provenance(metadata: SourceResponseMetadata, source_event_id: str) -> dict[str, Any]:
    return {
        "observed_at_utc": _utc_iso(metadata.observed_at_utc),
        "retrieved_at_utc": _utc_iso(metadata.retrieved_at_utc),
        "source": metadata.provider,
        "source_event_id": source_event_id,
        "raw_snapshot_hash": metadata.content_hash,
        "availability_basis": "capture_time_only",
        "pit_eligible": True,
        "commercial_use_status": metadata.commercial_use_status,
        "production_allowed": metadata.production_allowed,
    }


def canonical_event_id(game_pk: int, period: str = "FULL_GAME") -> str:
    return f"mlb:game:{game_pk}:period:{period.lower()}"


def normalize_schedule(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        game_pk = int(row["game_pk"])
        event_start = _utc_iso(row["game_date"])
        status = str(row.get("status_detailed") or row.get("status_abstract") or "UNKNOWN")
        rows.append({
            **_provenance(metadata, str(game_pk)),
            "canonical_event_id": canonical_event_id(game_pk),
            "game_pk": game_pk,
            "season": int(row["season"]),
            "game_date": str(row.get("official_date") or event_start[:10]),
            "event_start_utc": event_start,
            "home_team_id": str(row["home_team_id"]),
            "away_team_id": str(row["away_team_id"]),
            "doubleheader_number": int(row.get("game_number") or 1),
            "double_header": str(row.get("double_header") or "N"),
            "period": "FULL_GAME",
            "status": status,
            "status_code": _text_id(row.get("status_code")),
            "postponed": "postpon" in status.lower() or str(row.get("status_code") or "") == "D",
            "rescheduled_from_date": _text_id(row.get("rescheduled_from_date")),
            "reschedule_date": _text_id(row.get("reschedule_date")),
            "resume_date": _text_id(row.get("resume_date")),
            "original_date": _text_id(row.get("original_date")),
            "home_probable_pitcher_id": _text_id(row.get("home_probable_pitcher_id")),
            "away_probable_pitcher_id": _text_id(row.get("away_probable_pitcher_id")),
            "venue_id": _text_id(row.get("venue_id")),
        })
    normalized = pl.DataFrame(rows)
    validate_or_raise(normalized, MLB_V3_CONTRACTS["games"])
    return normalized


def normalize_game_feed(
    frame: pl.DataFrame, metadata: SourceResponseMetadata
) -> dict[str, pl.DataFrame]:
    probable_rows: list[dict[str, Any]] = []
    lineup_rows: list[dict[str, Any]] = []
    roster_rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        game_pk = int(row["game_pk"])
        side = str(row["team_side"])
        base = {
            **_provenance(metadata, str(game_pk)),
            "canonical_event_id": canonical_event_id(game_pk),
            "game_pk": game_pk,
            "team_side": side,
            "team_id": str(row["team_id"]),
        }
        probable_rows.append({
            **base,
            "pitcher_id": _text_id(row.get("probable_pitcher_id")),
            "pitcher_name": _text_id(row.get("probable_pitcher_name")),
        })
        lineup = json.loads(str(row.get("batting_order_json") or "[]"))
        for index, player_id in enumerate(lineup, start=1):
            lineup_rows.append({
                **base,
                "batting_order": index,
                "player_id": str(player_id),
                "confirmation_state": "CONFIRMED" if lineup else "UNAVAILABLE",
            })
        for player_id in json.loads(str(row.get("roster_player_ids_json") or "[]")):
            roster_rows.append({**base, "player_id": str(player_id)})
    outputs = {
        "probable_pitchers": pl.DataFrame(probable_rows),
        "lineups": pl.DataFrame(lineup_rows),
        "rosters": pl.DataFrame(roster_rows),
    }
    for name, output in outputs.items():
        if not output.is_empty():
            validate_or_raise(output, MLB_V3_CONTRACTS[name])
    return outputs


def normalize_transactions(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows = []
    for row in frame.iter_rows(named=True):
        transaction_id = str(row["id"])
        player = row.get("person") or {}
        from_team = row.get("fromTeam") or {}
        to_team = row.get("toTeam") or {}
        rows.append({
            **_provenance(metadata, transaction_id),
            "transaction_id": transaction_id,
            "transaction_date": _text_id(row.get("date")),
            "effective_date": _text_id(row.get("effectiveDate")),
            "player_id": _text_id(player.get("id")),
            "from_team_id": _text_id(from_team.get("id")),
            "to_team_id": _text_id(to_team.get("id")),
            "transaction_type": _text_id(row.get("typeCode") or row.get("typeDesc")),
        })
    output = pl.DataFrame(rows)
    if not output.is_empty():
        validate_or_raise(output, MLB_V3_CONTRACTS["transactions"])
    return output


def normalize_statcast(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    rows = []
    for row in frame.iter_rows(named=True):
        game_pk = int(row["game_pk"])
        rows.append({
            **_provenance(metadata, str(game_pk)),
            "canonical_event_id": canonical_event_id(game_pk),
            "game_pk": game_pk,
            "game_date": str(row["game_date"]),
            "at_bat_number": int(row["at_bat_number"]),
            "pitch_number": int(row["pitch_number"]),
            "pitcher_id": _text_id(row.get("pitcher")),
            "batter_id": _text_id(row.get("batter")),
            "pitch_type": _text_id(row.get("pitch_type")),
            "release_speed": row.get("release_speed"),
            "release_spin_rate": row.get("release_spin_rate"),
            "release_extension": row.get("release_extension"),
            "estimated_woba_using_speedangle": row.get("estimated_woba_using_speedangle"),
        })
    output = pl.DataFrame(rows)
    if not output.is_empty():
        validate_or_raise(output, MLB_V3_CONTRACTS["statcast_pitches"])
    return output


def normalize_weather(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    game_pk = int(metadata.source_event_id or metadata.requested_parameters["game_pk"])
    rows = []
    for row in frame.iter_rows(named=True):
        rows.append({
            **_provenance(metadata, str(game_pk)),
            "canonical_event_id": canonical_event_id(game_pk),
            "game_pk": game_pk,
            "valid_time_utc": _utc_iso(row["valid_time"]),
            "forecast_issued_at_utc": _utc_iso(metadata.observed_at_utc),
            "weather_source_quality": "A_FORECAST_CAPTURE",
            **{key: value for key, value in row.items() if key != "valid_time"},
        })
    output = pl.DataFrame(rows)
    if not output.is_empty():
        validate_or_raise(output, MLB_V3_CONTRACTS["weather_forecasts"])
    return output
