"""Reusable latest-vintage-as-of logic for MLB v3 observations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl


def _aware_utc(value: Any, *, field: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} contains an invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must contain timezone-aware timestamps")
    return parsed.astimezone(UTC)


def latest_as_of(
    frame: pl.DataFrame,
    *,
    entity_keys: list[str],
    decision_time_utc: datetime,
    event_start_column: str | None = None,
) -> pl.DataFrame:
    if decision_time_utc.tzinfo is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    required = {*entity_keys, "observed_at_utc", "pit_eligible"}
    if event_start_column is not None:
        required.add(event_start_column)
    if not required.issubset(frame.columns):
        raise ValueError(f"MLB v3 PIT input is missing columns: {sorted(required - set(frame.columns))}")

    cutoff = decision_time_utc.astimezone(UTC)
    observed = [_aware_utc(value, field="observed_at_utc") for value in frame["observed_at_utc"].to_list()]
    working = frame.with_columns(pl.Series("_observed_at_dt", observed, dtype=pl.Datetime("us", "UTC")))
    if event_start_column is not None:
        starts = [
            _aware_utc(value, field=event_start_column) for value in frame[event_start_column].to_list()
        ]
        working = working.with_columns(pl.Series("_event_start_dt", starts, dtype=pl.Datetime("us", "UTC")))
        working = working.filter(pl.lit(cutoff) < pl.col("_event_start_dt"))

    eligible = working.filter(pl.col("pit_eligible") & (pl.col("_observed_at_dt") <= cutoff))
    tie_keys = [*entity_keys, "_observed_at_dt"]
    if not eligible.is_empty() and eligible.group_by(tie_keys).len().filter(pl.col("len") > 1).height:
        raise ValueError("MLB v3 PIT input has ambiguous equal-time observations")
    return (
        eligible.sort("_observed_at_dt")
        .unique(subset=entity_keys, keep="last", maintain_order=True)
        .drop(["_observed_at_dt", "_event_start_dt"], strict=False)
    )
