"""Structural and provenance audit for normalized tennis data."""

from __future__ import annotations

from typing import Any

import polars as pl

from .store import TennisNormalizedStore


def _duplicates(frame: pl.DataFrame, keys: list[str]) -> int:
    if frame.is_empty() or not set(keys).issubset(frame.columns):
        return 0
    return frame.group_by(keys).len().filter(pl.col("len") > 1).height


def _result_type_counts(frame: pl.DataFrame) -> dict[str, int]:
    if frame.is_empty() or "result_type" not in frame.columns:
        return {}
    return dict(frame.group_by("result_type").len().iter_rows())


def audit_tennis_data(store: TennisNormalizedStore, *, tour: str | None = None) -> dict[str, Any]:
    matches = store.read_matches(tour=tour)
    current_events = store.read_current_events(tour=tour)

    duplicate_matches = _duplicates(matches, ["canonical_match_id", "observed_at_utc"])
    duplicate_current_events = _duplicates(current_events, ["canonical_match_id", "observed_at_utc"])
    missing_winner_identity = (
        matches.filter(
            (pl.col("winner_provider_player_id") == "") | (pl.col("loser_provider_player_id") == "")
        ).height
        if not matches.is_empty()
        else 0
    )

    report: dict[str, Any] = {
        "sport": "tennis",
        "tour": tour,
        "matches": matches.height,
        "current_events": current_events.height,
        "duplicate_matches": duplicate_matches,
        "duplicate_current_events": duplicate_current_events,
        "missing_winner_identity": missing_winner_identity,
        "match_result_types": _result_type_counts(matches),
        "current_event_result_types": _result_type_counts(current_events),
        "license_id": "tennismylife-license-unverified / espn-data-rights-unresolved",
        "upstream_rights_status": "unresolved",
        "commercial_use_status": "unresolved",
        "production_allowed": False,
    }
    hard_fail = duplicate_matches > 0 or duplicate_current_events > 0 or missing_winner_identity > 0
    if hard_fail:
        report["status"] = "ERROR"
    elif matches.is_empty() and current_events.is_empty():
        report["status"] = "UNAVAILABLE"
    elif matches.is_empty() or current_events.is_empty():
        # One of the two sources has never been captured for this scope --
        # a real, honest partial state, not a fabricated HEALTHY.
        report["status"] = "DEGRADED"
    else:
        report["status"] = "HEALTHY"
    report["qualification_note"] = (
        "TennisMyLife/ESPN captures made now are not retrospective PIT evidence; "
        "tourney_date is date-only (no real match-start timestamp), and neither "
        "source's rights have been independently cleared for commercial use."
    )
    return report
