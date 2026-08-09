"""Strict, pure normalization for synthetic/Sackmann-shaped tennis rows.

The transforms preserve acquisition time as acquisition time.  They never
turn a date-only ranking or tournament date into a fabricated UTC timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import polars as pl

from model_prediction.rebuild.providers.base import SourceResponseMetadata
from model_prediction.rebuild.schemas import validate_or_raise

from .contracts import TENNIS_CONTRACTS
from .snapshot import TennisSnapshotManifest

PLAYER_COLUMNS = {
    "player_id", "first_name", "last_name", "hand", "birth_date", "country_code", "height",
}
RANKING_COLUMNS = {"ranking_date", "ranking", "player_id", "ranking_points", "tours"}
MATCH_COLUMNS = {
    "tourney_id", "tourney_name", "surface", "draw_size", "tourney_level", "tourney_date",
    "match_num", "winner_id", "winner_seed", "winner_entry", "winner_name", "winner_hand",
    "winner_ht", "winner_ioc", "winner_age", "loser_id", "loser_seed", "loser_entry",
    "loser_name", "loser_hand", "loser_ht", "loser_ioc", "loser_age", "score", "best_of",
    "round", "minutes", "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon",
    "w_SvGms", "w_bpSaved", "w_bpFaced", "l_ace", "l_df", "l_svpt", "l_1stIn",
    "l_1stWon", "l_2ndWon", "l_SvGms", "l_bpSaved", "l_bpFaced", "winner_rank",
    "winner_rank_points", "loser_rank", "loser_rank_points",
}


@dataclass(frozen=True)
class TennisNormalizationContext:
    manifest: TennisSnapshotManifest
    metadata: SourceResponseMetadata

    def validate(self) -> None:
        self.manifest.validate()
        if self.metadata.provider != self.manifest.provider:
            raise ValueError("manifest/provider metadata mismatch")
        if self.metadata.sport != "tennis":
            raise ValueError("tennis normalizer requires sport=tennis metadata")
        for name, value in {
            "observed_at_utc": self.metadata.observed_at_utc,
            "retrieved_at_utc": self.metadata.retrieved_at_utc,
        }.items():
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if (
            self.manifest.availability_basis == "capture_time_only"
            and self.metadata.observed_at_utc != self.metadata.retrieved_at_utc
        ):
            raise ValueError("capture_time_only cannot claim an observation before retrieval")


def _validate_columns(frame: pl.DataFrame, *, required: set[str], allowed: set[str], table: str) -> None:
    if frame.is_empty():
        raise ValueError(f"{table} source contained no rows")
    columns = set(frame.columns)
    missing = required - columns
    unknown = columns - allowed
    if missing or unknown:
        raise ValueError(f"{table} schema drift: missing={sorted(missing)} unknown={sorted(unknown)}")


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"missing required tennis field: {field}")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _positive_int(value: Any, field: str, *, optional: bool = False) -> int | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError(f"missing required tennis integer: {field}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid tennis integer for {field}: {value!r}") from exc
    if not numeric.is_integer():
        raise ValueError(f"invalid tennis integer for {field}: {value!r}")
    result = int(numeric)
    if result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def _nonnegative_int(value: Any, field: str, *, optional: bool = False) -> int | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError(f"missing required tennis integer: {field}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid tennis integer for {field}: {value!r}") from exc
    if not numeric.is_integer() or numeric < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return int(numeric)


def _nonnegative_float(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid tennis number for {field}: {value!r}") from exc
    if result < 0:
        raise ValueError(f"{field} must be nonnegative")
    return result


def _date_yyyymmdd(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError(f"missing required tennis date: {field}")
    raw = str(value).strip()
    try:
        if len(raw) != 8 or not raw.isdigit():
            raise ValueError
        parsed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYYMMDD, got {raw!r}") from exc
    return parsed.isoformat()


def _provenance(context: TennisNormalizationContext, record_id: str) -> dict[str, Any]:
    manifest, metadata = context.manifest, context.metadata
    return {
        "source": manifest.provider,
        "source_record_id": record_id,
        "source_revision": manifest.source_revision,
        "source_version": manifest.source_revision,
        "observed_at_utc": metadata.observed_at_utc,
        "retrieved_at_utc": metadata.retrieved_at_utc,
        "raw_snapshot_hash": metadata.content_hash,
        "availability_basis": manifest.availability_basis,
        # A manifest assertion is not proof that each row existed before a
        # historical decision. No source-history verifier exists yet.
        "historical_observation_verified": False,
        "commercial_use_status": manifest.commercial_use_status,
        "production_allowed": manifest.production_allowed,
        "primary_source_status": manifest.primary_source_status,
        "attribution_required": manifest.attribution_required,
        "share_alike_required": manifest.share_alike_required,
        "license_id": manifest.license_id,
    }


def normalize_players(frame: pl.DataFrame, context: TennisNormalizationContext) -> pl.DataFrame:
    context.validate()
    _validate_columns(
        frame,
        required={"player_id", "first_name", "last_name", "hand", "birth_date", "country_code"},
        allowed=PLAYER_COLUMNS,
        table="players",
    )
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        player_id = _required_text(row.get("player_id"), "player_id")
        if player_id == "0":
            raise ValueError("player_id=0 is unresolved and cannot be normalized as a stable identity")
        first_name = _required_text(row.get("first_name"), "first_name")
        last_name = _required_text(row.get("last_name"), "last_name")
        rows.append({
            **_provenance(context, f"{context.manifest.tour}:{player_id}"),
            "tour": context.manifest.tour,
            "player_source_id": player_id,
            "canonical_player_id": None,
            "first_name": first_name,
            "last_name": last_name,
            "display_name": f"{first_name} {last_name}",
            "identity_status": "UNRESOLVED",
            "hand": _optional_text(row.get("hand")),
            "birth_date": _date_yyyymmdd(row.get("birth_date"), "birth_date", optional=True),
            "country_code": _optional_text(row.get("country_code")),
            "height_cm": _positive_int(row.get("height"), "height", optional=True),
        })
    normalized = pl.DataFrame(rows)
    _assert_unique(normalized, ["tour", "source_revision", "player_source_id"], "players")
    validate_or_raise(normalized, TENNIS_CONTRACTS["players"])
    return normalized


def normalize_rankings(frame: pl.DataFrame, context: TennisNormalizationContext) -> pl.DataFrame:
    context.validate()
    _validate_columns(
        frame,
        required={"ranking_date", "ranking", "player_id"},
        allowed=RANKING_COLUMNS,
        table="rankings",
    )
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        player_id = _required_text(row.get("player_id"), "player_id")
        if player_id == "0":
            raise ValueError("ranking player_id=0 cannot be linked safely")
        ranking_date = _date_yyyymmdd(row.get("ranking_date"), "ranking_date")
        record_id = f"{context.manifest.tour}:{ranking_date}:{player_id}"
        rows.append({
            **_provenance(context, record_id),
            "tour": context.manifest.tour,
            "ranking_date": ranking_date,
            "temporal_granularity": "DATE_ONLY",
            "effective_at_utc": None,
            "player_source_id": player_id,
            "canonical_player_id": None,
            "ranking": _positive_int(row.get("ranking"), "ranking"),
            "ranking_points": _nonnegative_int(
                row.get("ranking_points"), "ranking_points", optional=True,
            ),
            "tours": _nonnegative_int(row.get("tours"), "tours", optional=True),
        })
    normalized = pl.DataFrame(rows)
    _assert_unique(
        normalized,
        ["tour", "source_revision", "ranking_date", "player_source_id"],
        "rankings",
    )
    validate_or_raise(normalized, TENNIS_CONTRACTS["rankings"])
    return normalized


def normalize_matches(frame: pl.DataFrame, context: TennisNormalizationContext) -> pl.DataFrame:
    context.validate()
    required = {
        "tourney_id", "tourney_name", "surface", "tourney_level", "tourney_date", "match_num",
        "winner_id", "winner_name", "loser_id", "loser_name", "score", "best_of", "round",
    }
    _validate_columns(frame, required=required, allowed=MATCH_COLUMNS, table="matches")
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        tourney_id = _required_text(row.get("tourney_id"), "tourney_id")
        match_num = _nonnegative_int(row.get("match_num"), "match_num")
        winner_id = _required_text(row.get("winner_id"), "winner_id")
        loser_id = _required_text(row.get("loser_id"), "loser_id")
        if "0" in {winner_id, loser_id} or winner_id == loser_id:
            raise ValueError("tennis match requires two distinct stable player IDs")
        start_date = _date_yyyymmdd(row.get("tourney_date"), "tourney_date")
        source_match_id = f"{context.manifest.tour}:{tourney_id}:{match_num}"
        surface = _required_text(row.get("surface"), "surface").upper()
        if surface not in {"HARD", "CLAY", "GRASS", "CARPET"}:
            raise ValueError(f"unsupported or unknown tennis surface: {surface}")
        rows.append({
            **_provenance(context, source_match_id),
            "tour": context.manifest.tour,
            "source_match_id": source_match_id,
            "season": int(str(start_date)[:4]),
            "tourney_id": tourney_id,
            "tourney_name": _required_text(row.get("tourney_name"), "tourney_name"),
            "tournament_start_date": start_date,
            # Sackmann's tourney_date is not an actual match timestamp.
            "actual_start_utc": None,
            "temporal_granularity": "TOURNAMENT_START_DATE_ONLY",
            "match_num": match_num,
            "round": _required_text(row.get("round"), "round"),
            "surface": surface,
            "tourney_level": _required_text(row.get("tourney_level"), "tourney_level"),
            "draw_size": _positive_int(row.get("draw_size"), "draw_size", optional=True),
            "best_of": _positive_int(row.get("best_of"), "best_of"),
            "winner_source_id": winner_id,
            "loser_source_id": loser_id,
            "winner_canonical_id": None,
            "loser_canonical_id": None,
            "winner_name": _required_text(row.get("winner_name"), "winner_name"),
            "loser_name": _required_text(row.get("loser_name"), "loser_name"),
            "winner_seed": _optional_text(row.get("winner_seed")),
            "winner_entry": _optional_text(row.get("winner_entry")),
            "winner_hand": _optional_text(row.get("winner_hand")),
            "winner_height_cm": _positive_int(row.get("winner_ht"), "winner_ht", optional=True),
            "winner_country_code": _optional_text(row.get("winner_ioc")),
            "winner_age_years": _nonnegative_float(row.get("winner_age"), "winner_age"),
            "loser_seed": _optional_text(row.get("loser_seed")),
            "loser_entry": _optional_text(row.get("loser_entry")),
            "loser_hand": _optional_text(row.get("loser_hand")),
            "loser_height_cm": _positive_int(row.get("loser_ht"), "loser_ht", optional=True),
            "loser_country_code": _optional_text(row.get("loser_ioc")),
            "loser_age_years": _nonnegative_float(row.get("loser_age"), "loser_age"),
            "score": _required_text(row.get("score"), "score"),
            "minutes": _nonnegative_int(row.get("minutes"), "minutes", optional=True),
            "winner_aces": _nonnegative_int(row.get("w_ace"), "w_ace", optional=True),
            "winner_double_faults": _nonnegative_int(row.get("w_df"), "w_df", optional=True),
            "winner_service_points": _nonnegative_int(row.get("w_svpt"), "w_svpt", optional=True),
            "winner_first_serves_in": _nonnegative_int(row.get("w_1stIn"), "w_1stIn", optional=True),
            "winner_first_serve_points_won": _nonnegative_int(
                row.get("w_1stWon"), "w_1stWon", optional=True,
            ),
            "winner_second_serve_points_won": _nonnegative_int(
                row.get("w_2ndWon"), "w_2ndWon", optional=True,
            ),
            "winner_service_games": _nonnegative_int(row.get("w_SvGms"), "w_SvGms", optional=True),
            "winner_break_points_saved": _nonnegative_int(
                row.get("w_bpSaved"), "w_bpSaved", optional=True,
            ),
            "winner_break_points_faced": _nonnegative_int(
                row.get("w_bpFaced"), "w_bpFaced", optional=True,
            ),
            "loser_aces": _nonnegative_int(row.get("l_ace"), "l_ace", optional=True),
            "loser_double_faults": _nonnegative_int(row.get("l_df"), "l_df", optional=True),
            "loser_service_points": _nonnegative_int(row.get("l_svpt"), "l_svpt", optional=True),
            "loser_first_serves_in": _nonnegative_int(row.get("l_1stIn"), "l_1stIn", optional=True),
            "loser_first_serve_points_won": _nonnegative_int(
                row.get("l_1stWon"), "l_1stWon", optional=True,
            ),
            "loser_second_serve_points_won": _nonnegative_int(
                row.get("l_2ndWon"), "l_2ndWon", optional=True,
            ),
            "loser_service_games": _nonnegative_int(row.get("l_SvGms"), "l_SvGms", optional=True),
            "loser_break_points_saved": _nonnegative_int(
                row.get("l_bpSaved"), "l_bpSaved", optional=True,
            ),
            "loser_break_points_faced": _nonnegative_int(
                row.get("l_bpFaced"), "l_bpFaced", optional=True,
            ),
            "winner_rank_as_reported": _positive_int(row.get("winner_rank"), "winner_rank", optional=True),
            "winner_rank_points_as_reported": _positive_int(
                row.get("winner_rank_points"), "winner_rank_points", optional=True,
            ),
            "loser_rank_as_reported": _positive_int(row.get("loser_rank"), "loser_rank", optional=True),
            "loser_rank_points_as_reported": _positive_int(
                row.get("loser_rank_points"), "loser_rank_points", optional=True,
            ),
        })
    normalized = pl.DataFrame(rows)
    _assert_unique(normalized, ["tour", "source_revision", "source_match_id"], "matches")
    validate_or_raise(normalized, TENNIS_CONTRACTS["matches"])
    return normalized


def _assert_unique(frame: pl.DataFrame, keys: list[str], table: str) -> None:
    duplicates = frame.group_by(keys).len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError(f"{table} contains {duplicates.height} duplicate primary key(s)")


__all__ = [
    "MATCH_COLUMNS", "PLAYER_COLUMNS", "RANKING_COLUMNS", "TennisNormalizationContext",
    "normalize_matches", "normalize_players", "normalize_rankings",
]
