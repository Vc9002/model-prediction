"""Fail-closed rights assertions for normalized soccer observations."""

from __future__ import annotations

import polars as pl

from model_prediction.rebuild.providers.base import RIGHTS_STATUSES, USE_SCOPES

SOCCER_NORMALIZED_RIGHTS_COLUMNS = frozenset(
    {
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
    }
)


def _assert_explicit_rights(frame: pl.DataFrame) -> None:
    missing = sorted(SOCCER_NORMALIZED_RIGHTS_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"soccer source rows are missing rights metadata: {missing}")
    if frame.is_empty():
        raise ValueError("soccer source rows cannot prove rights for an empty dataset")
    nonnull = [
        "source_asset",
        "provider_chain",
        "license_id",
        "license_url",
        "attribution_required",
        "subscription_required",
        "subscription_scope",
        "upstream_rights_status",
        "commercial_use_status",
        "use_scope",
        "production_allowed",
    ]
    if frame.select(pl.any_horizontal(pl.col(nonnull).is_null()).any()).item():
        raise ValueError("soccer source rows contain null rights metadata")
    for column in ("source_asset", "provider_chain", "license_id", "license_url"):
        if frame.filter(pl.col(column).str.strip_chars() == "").height:
            raise ValueError(f"soccer source rows contain empty rights metadata: {column}")
    if frame.filter(~pl.col("upstream_rights_status").is_in(list(RIGHTS_STATUSES))).height:
        raise ValueError("soccer source rows contain unknown upstream rights status")
    if frame.filter(~pl.col("commercial_use_status").is_in(list(RIGHTS_STATUSES))).height:
        raise ValueError("soccer source rows contain unknown commercial-use status")
    if frame.filter(~pl.col("use_scope").is_in(list(USE_SCOPES))).height:
        raise ValueError("soccer source rows contain unknown use scope")
    missing_attribution = frame.filter(
        pl.col("attribution_required")
        & (
            pl.col("attribution_text")
            .cast(pl.String, strict=False)
            .fill_null("")
            .str.strip_chars()
            == ""
        )
    ).height
    if missing_attribution:
        raise ValueError("soccer source rows require missing attribution text")
    invalid_subscription = frame.filter(
        pl.col("subscription_required") & (pl.col("subscription_scope") == "none")
    ).height
    if invalid_subscription:
        raise ValueError("soccer source rows require an explicit subscription scope")


def assert_research_shadow_allowed(frame: pl.DataFrame) -> None:
    """Require the audited research-only tags before any PIT filtering."""

    _assert_explicit_rights(frame)
    if frame.filter(pl.col("use_scope") != "research_shadow_only").height:
        raise PermissionError("soccer rows are not approved for research/shadow collection")
    if frame.filter(pl.col("commercial_use_status") != "unresolved").height:
        raise PermissionError("soccer research rows must retain unresolved commercial-use status")
    if frame.filter(pl.col("upstream_rights_status") != "unresolved").height:
        raise PermissionError("soccer research rows must retain unresolved upstream rights status")
    if frame.filter(pl.col("production_allowed") != False).height:
        raise PermissionError("soccer research rows cannot be production-allowed")


def assert_economic_use_allowed(frame: pl.DataFrame) -> None:
    """Require affirmative, row-level clearance for production/economic use."""

    _assert_explicit_rights(frame)
    rejected = frame.filter(
        (pl.col("use_scope") != "production_economic")
        | (pl.col("commercial_use_status") != "cleared")
        | (pl.col("upstream_rights_status") != "cleared")
        | (pl.col("production_allowed") != True)
    )
    if rejected.height:
        raise PermissionError("soccer data is not cleared for production/economic use")
