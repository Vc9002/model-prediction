"""Conservative point-in-time gates for date-only historical tennis data."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    return value.astimezone(UTC)


def ranking_as_of(
    rankings: pl.DataFrame,
    *,
    tour: str,
    player_source_id: str,
    decision_time_utc: datetime,
) -> dict[str, object] | None:
    """Return the one latest safely eligible ranking, or ``None``.

    A ranking date has no publication time.  Same-calendar-day rows are
    therefore excluded even when a match starts later that day.  Observation
    time remains an independent hard gate, so a snapshot acquired in 2026
    cannot be used in a 2025 retrospective decision.
    """
    decision = _aware_utc(decision_time_utc)
    required = {
        "tour", "player_source_id", "ranking_date", "observed_at_utc",
        "temporal_granularity", "availability_basis",
    }
    missing = required - set(rankings.columns)
    if missing:
        raise ValueError(f"tennis ranking PIT input missing columns: {sorted(missing)}")
    candidates = rankings.filter(
        (pl.col("tour") == tour.upper())
        & (pl.col("player_source_id") == str(player_source_id))
        & (pl.col("temporal_granularity") == "DATE_ONLY")
        & (pl.col("ranking_date") < decision.date().isoformat())
        & (pl.col("observed_at_utc") <= decision.isoformat())
    )
    if candidates.is_empty():
        return None
    latest_date = candidates["ranking_date"].max()
    latest = candidates.filter(pl.col("ranking_date") == latest_date)
    if latest.height != 1:
        raise ValueError("ambiguous tennis ranking snapshot for latest eligible date")
    return latest.row(0, named=True)


def eligible_prior_matches(
    matches: pl.DataFrame,
    *,
    target_tournament_start_date: date,
    decision_time_utc: datetime,
) -> pl.DataFrame:
    """Exclude all same-tournament/date-bucket results.

    ``tourney_date`` is not a match timestamp and ``match_num`` is not
    accepted as chronology evidence.  Until an independent provider records
    exact completion observations, only earlier tournament start dates are
    eligible.
    """
    decision = _aware_utc(decision_time_utc)
    required = {
        "tournament_start_date", "actual_start_utc", "temporal_granularity",
        "observed_at_utc", "source_match_id",
    }
    missing = required - set(matches.columns)
    if missing:
        raise ValueError(f"tennis match PIT input missing columns: {sorted(missing)}")
    invalid = matches.filter(
        (pl.col("temporal_granularity") != "TOURNAMENT_START_DATE_ONLY")
        | pl.col("actual_start_utc").is_not_null()
    )
    if invalid.height:
        raise ValueError("date-only tennis source contains fabricated or unsupported match timestamps")
    return (
        matches.filter(
            (pl.col("tournament_start_date") < target_tournament_start_date.isoformat())
            & (pl.col("observed_at_utc") <= decision.isoformat())
        )
        .sort(["tournament_start_date", "source_match_id"])
    )


__all__ = ["eligible_prior_matches", "ranking_as_of"]
