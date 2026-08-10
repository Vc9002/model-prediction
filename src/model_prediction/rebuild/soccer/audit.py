"""Structural, PIT, and source-rights audit for normalized soccer data."""

from __future__ import annotations

from typing import Any

import polars as pl

from model_prediction.rebuild.providers.football_data import FOOTBALL_DATA_RIGHTS
from model_prediction.rebuild.providers.soccer_espn import ESPN_SOCCER_RIGHTS
from model_prediction.rebuild.providers.statsbomb import STATSBOMB_OPEN_RIGHTS

from .rights import SOCCER_NORMALIZED_RIGHTS_COLUMNS, assert_research_shadow_allowed
from .store import SoccerNormalizedStore

# PR #5's provider consolidation replaced the single soccer_rights.py
# SOCCER_SOURCE_RIGHTS dict with one SourceRightsProfile constant per
# provider module (the same reusable providers/rights.py::SourceRightsProfile
# shape every sport's providers now share) -- reassembled here since audit
# reporting still needs the per-provider view, not just per-source-module.
SOCCER_SOURCE_RIGHTS = {
    "espn_site_v2": ESPN_SOCCER_RIGHTS,
    "football_data_v4": FOOTBALL_DATA_RIGHTS,
    "statsbomb_open_data": STATSBOMB_OPEN_RIGHTS,
}


def audit_soccer_data(store: SoccerNormalizedStore) -> dict[str, Any]:
    matches = store.read_matches()
    configured = {
        provider: profile.to_dict() for provider, profile in SOCCER_SOURCE_RIGHTS.items()
    }
    if matches.is_empty():
        return {
            "sport": "soccer",
            "status": "UNAVAILABLE",
            "rows": 0,
            "configured_source_policies": configured,
            "research_shadow_allowed": True,
            "economic_use_allowed": False,
            "production_allowed": False,
            "reason": "NO_NORMALIZED_SOCCER_DATA",
        }

    required = {
        "source",
        "source_match_id",
        "observed_at_utc",
        "available_at_utc",
        "raw_snapshot_hash",
        "availability_basis",
        "timestamp_valid",
        "pit_eligible",
        *SOCCER_NORMALIZED_RIGHTS_COLUMNS,
    }
    missing_columns = sorted(required - set(matches.columns))
    null_provenance_rows = matches.height
    malformed_hash_rows = matches.height
    timestamp_violations = matches.height
    duplicate_observations = matches.height
    production_allowed_rows = matches.height
    rights_policy_valid = False
    rights_policy_error: str | None = None
    source_assets: list[dict[str, Any]] = []

    if not missing_columns:
        provenance_columns = [
            "source",
            "source_match_id",
            "observed_at_utc",
            "available_at_utc",
            "raw_snapshot_hash",
            "availability_basis",
            *sorted(SOCCER_NORMALIZED_RIGHTS_COLUMNS - {"attribution_text"}),
        ]
        null_provenance_rows = matches.filter(
            pl.any_horizontal(pl.col(provenance_columns).is_null())
        ).height
        malformed_hash_rows = matches.filter(
            (pl.col("raw_snapshot_hash").str.len_chars() != 64)
            | ~pl.col("raw_snapshot_hash").str.contains(r"^[0-9a-f]{64}$")
        ).height
        checked = matches.with_columns(
            pl.col("observed_at_utc")
            .str.to_datetime(time_zone="UTC", strict=False)
            .alias("_observed"),
            pl.col("available_at_utc")
            .str.to_datetime(time_zone="UTC", strict=False)
            .alias("_available"),
        )
        timestamp_violations = checked.filter(
            pl.col("_observed").is_null()
            | pl.col("_available").is_null()
            | (pl.col("_available") > pl.col("_observed"))
        ).height
        duplicate_observations = (
            matches.group_by(["source", "source_match_id", "observed_at_utc"])
            .len()
            .filter(pl.col("len") > 1)
            .height
        )
        production_allowed_rows = matches.filter(pl.col("production_allowed")).height
        try:
            assert_research_shadow_allowed(matches)
        except (PermissionError, ValueError) as exc:
            rights_policy_error = str(exc)
        else:
            rights_policy_valid = True
        asset_columns = [
            "source",
            "source_asset",
            "provider_chain",
            "license_id",
            "license_url",
            "attribution_required",
            "attribution_text",
            "subscription_required",
            "subscription_scope",
            "upstream_rights_status",
            "commercial_use_status",
            "use_scope",
            "production_allowed",
        ]
        source_assets = matches.select(asset_columns).unique(maintain_order=True).to_dicts()

    hard_fail = bool(
        missing_columns
        or null_provenance_rows
        or malformed_hash_rows
        or timestamp_violations
        or duplicate_observations
        or production_allowed_rows
        or not rights_policy_valid
    )
    return {
        "sport": "soccer",
        "status": "ERROR" if hard_fail else "DEGRADED",
        "rows": matches.height,
        "matches": matches.select(["source", "source_match_id"]).unique().height,
        "missing_provenance_columns": missing_columns,
        "null_provenance_rows": null_provenance_rows,
        "malformed_raw_snapshot_hash_rows": malformed_hash_rows,
        "timestamp_violations": timestamp_violations,
        "duplicate_observations": duplicate_observations,
        "rights_policy_valid": rights_policy_valid,
        "rights_policy_error": rights_policy_error,
        "source_assets": source_assets,
        "configured_source_policies": configured,
        "research_shadow_allowed": rights_policy_valid,
        "economic_use_allowed": False,
        "production_allowed": False,
        "production_allowed_rows": production_allowed_rows,
        "qualification_note": (
            "Captured soccer rows are research/shadow-only. ESPN-derived and "
            "football-data.org commercial rights remain unresolved; StatsBomb "
            "Open Data is policy-blocked. Capture time is not retrospective PIT proof."
        ),
    }
