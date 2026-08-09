"""Reusable latest-vintage-as-of logic for MLB v3 observations."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl


def latest_as_of(
    frame: pl.DataFrame,
    *,
    entity_keys: list[str],
    decision_time_utc: datetime,
) -> pl.DataFrame:
    if decision_time_utc.tzinfo is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    required = {*entity_keys, "observed_at_utc", "pit_eligible"}
    if not required.issubset(frame.columns):
        raise ValueError(f"MLB v3 PIT input is missing columns: {sorted(required - set(frame.columns))}")
    cutoff = decision_time_utc.astimezone(UTC).isoformat()
    return (
        frame.filter(pl.col("pit_eligible") & (pl.col("observed_at_utc") <= cutoff))
        .sort("observed_at_utc")
        .unique(subset=entity_keys, keep="last", maintain_order=True)
    )
