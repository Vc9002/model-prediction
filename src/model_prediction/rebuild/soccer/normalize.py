"""Pure provider-row to canonical soccer match transformations."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from model_prediction.rebuild.providers.base import SourceResponseMetadata
from model_prediction.rebuild.schemas import validate_or_raise

from .contracts import SOCCER_MATCHES


def _utc_iso(value: Any, field: str) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        raise TypeError(f"date-only soccer {field} is not point-in-time safe")
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "")
        if not raw:
            raise ValueError(f"missing soccer {field}")
        parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"timezone-naive soccer {field}")
    return parsed.astimezone(UTC).isoformat()


def _score(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise TypeError("boolean soccer score")
    parsed = int(value)
    if parsed < 0:
        raise ValueError("negative soccer score")
    return parsed


def normalize_soccer_matches(
    frame: pl.DataFrame,
    metadata: SourceResponseMetadata,
) -> pl.DataFrame:
    """Normalize one immutable capture; availability is capture time only.

    Provider `lastUpdated` is retained as metadata but is not treated as proof
    that this repository possessed the row at that historical instant.
    """
    if metadata.sport != "soccer":
        raise ValueError(f"soccer normalizer received metadata for {metadata.sport}")
    required_rights = {
        "source_asset": metadata.source_asset,
        "provider_chain": metadata.provider_chain,
        "license_id": metadata.license_id,
        "license_url": metadata.license_url,
    }
    missing_rights = sorted(name for name, value in required_rights.items() if not value)
    if missing_rights:
        raise ValueError(f"soccer source metadata is missing rights provenance: {missing_rights}")
    observed = _utc_iso(metadata.observed_at_utc, "observed_at_utc")
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        match_id = str(row.get("source_match_id") or "").strip()
        home_id = str(row.get("home_team_id") or "").strip()
        away_id = str(row.get("away_team_id") or "").strip()
        if not match_id or not home_id or not away_id or home_id == away_id:
            raise ValueError("invalid soccer match/team identity")
        provider_updated = row.get("provider_updated_at")
        rows.append(
            {
                "source": metadata.provider,
                "source_version": metadata.source_version or "unknown",
                "source_match_id": match_id,
                "competition_id": str(row.get("competition_id") or "").strip(),
                "competition_name": str(row.get("competition_name") or "").strip(),
                "season_id": str(row.get("season_id") or "").strip(),
                "event_start_utc": _utc_iso(row.get("event_start"), "event_start"),
                "status": str(row.get("status") or "UNKNOWN"),
                "completed": bool(row.get("completed", False)),
                "home_team_id": home_id,
                "home_team_name": str(row.get("home_team_name") or "").strip(),
                "away_team_id": away_id,
                "away_team_name": str(row.get("away_team_name") or "").strip(),
                "home_score": _score(row.get("home_score")),
                "away_score": _score(row.get("away_score")),
                "venue_id": str(row.get("venue_id") or "").strip() or None,
                "venue_name": str(row.get("venue_name") or "").strip() or None,
                "provider_updated_at_utc": (
                    _utc_iso(provider_updated, "provider_updated_at") if provider_updated else None
                ),
                "observed_at_utc": observed,
                "available_at_utc": observed,
                "raw_snapshot_hash": metadata.content_hash,
                "availability_basis": "capture_time_only",
                "timestamp_valid": True,
                "pit_eligible": True,
                "source_asset": metadata.source_asset,
                "provider_chain": metadata.provider_chain,
                "license_id": metadata.license_id,
                "license_url": metadata.license_url,
                "attribution_required": metadata.attribution_required,
                "attribution_text": metadata.attribution_text,
                "subscription_required": metadata.subscription_required,
                "subscription_scope": metadata.subscription_scope,
                "upstream_rights_status": metadata.upstream_rights_status,
                "commercial_use_status": metadata.commercial_use_status,
                "use_scope": metadata.use_scope,
                "production_allowed": metadata.production_allowed,
                "schema_version": "2",
            }
        )
    normalized = (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            schema={
                "source": pl.String,
                "source_version": pl.String,
                "source_match_id": pl.String,
                "competition_id": pl.String,
                "competition_name": pl.String,
                "season_id": pl.String,
                "event_start_utc": pl.String,
                "status": pl.String,
                "completed": pl.Boolean,
                "home_team_id": pl.String,
                "home_team_name": pl.String,
                "away_team_id": pl.String,
                "away_team_name": pl.String,
                "home_score": pl.Int64,
                "away_score": pl.Int64,
                "venue_id": pl.String,
                "venue_name": pl.String,
                "provider_updated_at_utc": pl.String,
                "observed_at_utc": pl.String,
                "available_at_utc": pl.String,
                "raw_snapshot_hash": pl.String,
                "availability_basis": pl.String,
                "timestamp_valid": pl.Boolean,
                "pit_eligible": pl.Boolean,
                "source_asset": pl.String,
                "provider_chain": pl.String,
                "license_id": pl.String,
                "license_url": pl.String,
                "attribution_required": pl.Boolean,
                "attribution_text": pl.String,
                "subscription_required": pl.Boolean,
                "subscription_scope": pl.String,
                "upstream_rights_status": pl.String,
                "commercial_use_status": pl.String,
                "use_scope": pl.String,
                "production_allowed": pl.Boolean,
                "schema_version": pl.String,
            }
        )
    )
    if normalized.height:
        for required in (
            "competition_id",
            "competition_name",
            "season_id",
            "home_team_name",
            "away_team_name",
        ):
            if normalized[required].str.strip_chars().eq("").any():
                raise ValueError(f"empty required soccer field: {required}")
    validate_or_raise(normalized, SOCCER_MATCHES)
    return normalized
