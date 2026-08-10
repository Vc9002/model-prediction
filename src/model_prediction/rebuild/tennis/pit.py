"""Fail-closed point-in-time eligibility for tennis observations."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl


def _decision_iso(decision_time_utc: datetime) -> str:
    if decision_time_utc.tzinfo is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    return decision_time_utc.astimezone(UTC).isoformat()


def eligible_matches_as_of(
    matches: pl.DataFrame,
    decision_time_utc: datetime,
    *,
    completed_only: bool = False,
) -> pl.DataFrame:
    required = {"canonical_match_id", "observed_at_utc", "pit_eligible", "completed"}
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"tennis PIT input is missing required columns: {missing}")
    decision = _decision_iso(decision_time_utc)
    eligible = matches.filter(pl.col("pit_eligible") & (pl.col("observed_at_utc") <= decision))
    if completed_only:
        eligible = eligible.filter(pl.col("completed"))
    return eligible.sort("observed_at_utc").unique(
        subset=["canonical_match_id"], keep="last", maintain_order=True
    )


def eligible_prior_matches_for_player(
    matches: pl.DataFrame,
    *,
    tennis_player_id: str,
    decision_time_utc: datetime,
    exclude_canonical_match_id: str | None = None,
) -> pl.DataFrame:
    """Completed matches (any result_type) either side of `tennis_player_id`
    observed at or before `decision_time_utc`.

    `tennis_player_id` is provider-scoped (see contracts.py's IDENTITY_COLUMNS
    docstring), so this only surfaces prior matches captured by the *same*
    provider as the id -- it cannot cross-reference a player across
    TennisMyLife and ESPN without a real identity resolver, which does not
    exist yet.
    """
    required = {"winner_tennis_player_id", "loser_tennis_player_id"}
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"tennis player PIT input is missing required columns: {missing}")
    eligible = eligible_matches_as_of(matches, decision_time_utc, completed_only=True).filter(
        (pl.col("winner_tennis_player_id") == tennis_player_id)
        | (pl.col("loser_tennis_player_id") == tennis_player_id)
    )
    if exclude_canonical_match_id is not None:
        eligible = eligible.filter(pl.col("canonical_match_id") != exclude_canonical_match_id)
    return eligible.sort(["tourney_date", "canonical_match_id"])
