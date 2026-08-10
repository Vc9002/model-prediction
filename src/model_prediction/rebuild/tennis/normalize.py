"""Pure provider-row to canonical tennis match transformations.

Both TennisMyLife (historical/ongoing CSV) and ESPN (live scoreboard) rows
land in a shared result-type vocabulary so a future consumer never has to
re-parse a raw score string or ESPN status code to know whether a match
ended normally. See `_classify_tennismylife_result`/
`_classify_espn_result`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl

from model_prediction.rebuild.providers.base import SourceResponseMetadata
from model_prediction.rebuild.schemas import validate_or_raise

from .contracts import TENNIS_CONTRACTS

# TennisMyLife/Sackmann-style score tokens. Order matters: DEF/W-O/W/O
# checks must run before a bare-RET check since some scores combine
# partial-set play with a terminal token (e.g. "7-6(6) 2-1 RET").
_WALKOVER_TOKENS = ("W/O", "WO", "WALKOVER")
_DEFAULT_TOKENS = ("DEF",)
_RETIREMENT_TOKENS = ("RET",)


def _classify_tennismylife_result(score_raw: str) -> tuple[str, bool]:
    text = (score_raw or "").strip().upper()
    if not text:
        return "unfinished", False
    tokens = text.replace("-", " ").split()
    if any(token in _WALKOVER_TOKENS for token in tokens) or text in _WALKOVER_TOKENS:
        return "walkover", False
    if any(token in _DEFAULT_TOKENS for token in tokens):
        return "default", False
    if any(token in _RETIREMENT_TOKENS for token in tokens):
        return "retirement", False
    return "completed", True


_ESPN_STATUS_RESULT_TYPE = {
    "STATUS_FINAL": ("completed", True),
    "STATUS_RETIRED": ("retirement", False),
    "STATUS_WALKOVER": ("walkover", False),
    "STATUS_SCHEDULED": ("unfinished", False),
    "STATUS_IN_PROGRESS": ("unfinished", False),
    "STATUS_POSTPONED": ("unfinished", False),
    "STATUS_CANCELED": ("unfinished", False),
}


def _classify_espn_result(status_name: str) -> tuple[str, bool]:
    return _ESPN_STATUS_RESULT_TYPE.get(str(status_name or "").strip().upper(), ("unknown_irregular", False))


def _required(value: Any, field: str) -> str:
    if value is None:
        raise ValueError(f"missing tennis {field}")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    result = str(value).strip()
    if not result or result.lower() in {"nan", "none"}:
        raise ValueError(f"missing tennis {field}")
    return result


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _provenance(metadata: SourceResponseMetadata) -> dict[str, Any]:
    return {
        "source": metadata.provider,
        "source_version": metadata.source_version or "unknown",
        "observed_at_utc": metadata.observed_at_utc,
        "retrieved_at_utc": metadata.retrieved_at_utc,
        "raw_snapshot_hash": metadata.content_hash,
        "availability_basis": "capture_time_only",
        "pit_eligible": True,
        "license_id": metadata.license_id,
        "license_url": metadata.license_url,
        "attribution_required": metadata.attribution_required,
        "attribution_text": metadata.attribution_text,
        "upstream_rights_status": metadata.upstream_rights_status,
        "commercial_use_status": metadata.commercial_use_status,
        "production_allowed": metadata.production_allowed,
    }


def normalize_tennismylife_matches(
    frame: pl.DataFrame,
    metadata: SourceResponseMetadata,
    *,
    tour: str,
    match_kind: str = "main",
) -> pl.DataFrame:
    """Normalize one immutable TennisMyLife CSV capture (`year_matches`/
    `ongoing_matches` result). `tour`/`match_kind` come from the caller,
    not the frame, since TennisMyLife's files are already tour/kind-scoped
    by filename rather than carrying a column for it."""
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        tourney_id = _required(row.get("tourney_id"), "tourney_id")
        match_num = _as_int(row.get("match_num"))
        canonical_match_id = f"tennis_mylife:{tourney_id}:{match_num if match_num is not None else 'na'}"
        winner_id = _required(row.get("winner_id"), "winner_id")
        loser_id = _required(row.get("loser_id"), "loser_id")
        score_raw = str(row.get("score") or "")
        result_type, completed = _classify_tennismylife_result(score_raw)
        rows.append(
            {
                **_provenance(metadata),
                "canonical_match_id": canonical_match_id,
                "tour": tour,
                "match_kind": match_kind,
                "tourney_id": tourney_id,
                "tourney_name": _required(row.get("tourney_name"), "tourney_name"),
                "surface": str(row.get("surface") or "") or None,
                "tourney_level": str(row.get("tourney_level") or "") or None,
                "tourney_date": _required(row.get("tourney_date"), "tourney_date"),
                "match_num": match_num,
                "round": str(row.get("round") or "") or None,
                "best_of": _as_int(row.get("best_of")),
                "minutes": _as_int(row.get("minutes")),
                "winner_tennis_player_id": f"{metadata.provider}:{winner_id}",
                "winner_provider_player_id": winner_id,
                "winner_player_name": _required(row.get("winner_name"), "winner_name"),
                "loser_tennis_player_id": f"{metadata.provider}:{loser_id}",
                "loser_provider_player_id": loser_id,
                "loser_player_name": _required(row.get("loser_name"), "loser_name"),
                "winner_rank": _as_int(row.get("winner_rank")),
                "loser_rank": _as_int(row.get("loser_rank")),
                "winner_seed": str(row.get("winner_seed") or "") or None,
                "loser_seed": str(row.get("loser_seed") or "") or None,
                "score_raw": score_raw or None,
                "result_type": result_type,
                "completed": completed,
            }
        )
    normalized = pl.DataFrame(rows)
    if normalized.height:
        validate_or_raise(normalized, TENNIS_CONTRACTS["matches"])
    return normalized


_SINGLES_GROUPING_SLUGS = frozenset({"mens-singles", "womens-singles"})


def normalize_espn_scoreboard(frame: pl.DataFrame, metadata: SourceResponseMetadata) -> pl.DataFrame:
    """Normalize one immutable ESPN scoreboard capture.

    Singles matches only: verified live that ESPN's scoreboard response
    never omits a singles competitor's name, but doubles rows carry a
    composite team `competitor_*_id` (e.g. "723-6368") with an empty name
    field -- a doubles pair is not one `tennis_player_id`, and resolving a
    team id into its two individual players is real, separate,
    not-yet-built work. Filtering here rather than crashing on the first
    doubles row, or worse, silently writing a blank player name.
    """
    rows: list[dict[str, Any]] = []
    for row in frame.filter(pl.col("grouping_slug").is_in(_SINGLES_GROUPING_SLUGS)).iter_rows(named=True):
        event_id = _required(row.get("espn_event_id"), "espn_event_id")
        competition_id = _required(row.get("espn_competition_id"), "espn_competition_id")
        canonical_match_id = f"espn_tennis:{event_id}:{competition_id}"
        competitor_1_id = _required(row.get("competitor_1_id"), "competitor_1_id")
        competitor_2_id = _required(row.get("competitor_2_id"), "competitor_2_id")
        status_name = _required(row.get("status_name"), "status_name")
        result_type, completed = _classify_espn_result(status_name)
        start_date = row.get("start_date")
        event_start_utc: str | None
        if isinstance(start_date, datetime):
            event_start_utc = start_date.astimezone(UTC).isoformat()
        else:
            event_start_utc = str(start_date or "") or None
        rows.append(
            {
                **_provenance(metadata),
                "canonical_match_id": canonical_match_id,
                "tour": _required(row.get("tour"), "tour"),
                "tournament_name": str(row.get("tournament_name") or "") or None,
                "grouping_slug": str(row.get("grouping_slug") or "") or None,
                "event_start_utc": event_start_utc,
                "status_name": status_name,
                "status_state": _required(row.get("status_state"), "status_state"),
                "competitor_1_tennis_player_id": f"{metadata.provider}:{competitor_1_id}",
                "competitor_1_provider_player_id": competitor_1_id,
                "competitor_1_player_name": _required(row.get("competitor_1_name"), "competitor_1_name"),
                "competitor_1_winner": bool(row["competitor_1_winner"]) if row.get("competitor_1_winner") is not None else None,
                "competitor_2_tennis_player_id": f"{metadata.provider}:{competitor_2_id}",
                "competitor_2_provider_player_id": competitor_2_id,
                "competitor_2_player_name": _required(row.get("competitor_2_name"), "competitor_2_name"),
                "competitor_2_winner": bool(row["competitor_2_winner"]) if row.get("competitor_2_winner") is not None else None,
                "result_type": result_type,
                "completed": completed,
            }
        )
    normalized = pl.DataFrame(rows)
    if normalized.height:
        validate_or_raise(normalized, TENNIS_CONTRACTS["current_events"])
    return normalized
