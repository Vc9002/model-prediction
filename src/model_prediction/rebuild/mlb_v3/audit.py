"""Structural, coverage, and provenance audit for MLB v3 normalized data."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl

from .contracts import PRIMARY_KEYS
from .store import MLBV3NormalizedStore

MIN_COVERAGE = {
    "starters": 1.0,
    "statcast": 1.0,
    "lineups": 1.0,
    "rosters": 1.0,
    "weather": 1.0,
}


def _duplicates(frame: pl.DataFrame, keys: list[str]) -> int:
    if frame.is_empty():
        return 0
    return frame.group_by(keys).len().filter(pl.col("len") > 1).height


def _parse_aware(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("timestamp is timezone-naive")
    return parsed.astimezone(UTC)


def _timestamp_violations(frames: list[pl.DataFrame]) -> int:
    violations = 0
    for frame in frames:
        if frame.is_empty():
            continue
        for observed, retrieved in frame.select("observed_at_utc", "retrieved_at_utc").iter_rows():
            try:
                if _parse_aware(observed) > _parse_aware(retrieved):
                    violations += 1
            except (TypeError, ValueError):
                violations += 1
    return violations


def _covered_two_sides(frame: pl.DataFrame, *, min_rows_per_side: int = 1) -> set[int]:
    if frame.is_empty():
        return set()
    counts = frame.group_by("game_pk", "team_side").len()
    valid = counts.filter(pl.col("len") >= min_rows_per_side)
    return {
        int(game_pk)
        for game_pk, sides in valid.group_by("game_pk").agg(pl.col("team_side").n_unique()).iter_rows()
        if sides >= 2
    }


def _coverage(name: str, expected: set[int], covered: set[int]) -> dict[str, Any]:
    matched = expected & covered
    ratio = len(matched) / len(expected) if expected else None
    passes = ratio is None or ratio >= MIN_COVERAGE[name]
    return {
        "expected_games": len(expected),
        "covered_games": len(matched),
        "coverage": ratio,
        "threshold": MIN_COVERAGE[name],
        "missing_game_pks": sorted(expected - covered),
        "passes": passes,
    }


def audit_mlb_v3(store: MLBV3NormalizedStore, season: int) -> dict[str, Any]:
    tables = {
        name: store.read_all(name, season)
        for name in (
            "games",
            "probable_pitchers",
            "lineups",
            "rosters",
            "statcast_pitches",
            "weather_forecasts",
        )
    }
    games = tables["games"]
    if games.is_empty():
        return {
            "sport": "mlb",
            "version": "v3",
            "season": season,
            "status": "NO_DATA",
            "games_present": 0,
            "qualification_note": "No normalized MLB v3 game captures exist.",
        }

    conflict_counts = {name: store.conflict_count(name, season) for name in tables}
    latest_games = games.sort("observed_at_utc").unique(["game_pk", "period"], keep="last")
    eligible_games = {
        int(row["game_pk"])
        for row in latest_games.iter_rows(named=True)
        if not row["postponed"] and "cancel" not in str(row["status"]).lower()
    }
    completed_games = {
        int(row["game_pk"])
        for row in latest_games.iter_rows(named=True)
        if any(token in str(row["status"]).lower() for token in ("final", "completed", "game over"))
    }

    starters = tables["probable_pitchers"]
    starter_covered = (
        set()
        if starters.is_empty()
        else _covered_two_sides(starters.filter(pl.col("pitcher_id").is_not_null()))
    )
    lineup_covered = _covered_two_sides(tables["lineups"], min_rows_per_side=9)
    roster_covered = _covered_two_sides(tables["rosters"])
    statcast_covered = (
        set()
        if tables["statcast_pitches"].is_empty()
        else {int(value) for value in tables["statcast_pitches"]["game_pk"].unique().to_list()}
    )
    weather_covered = (
        set()
        if tables["weather_forecasts"].is_empty()
        else {int(value) for value in tables["weather_forecasts"]["game_pk"].unique().to_list()}
    )
    coverage = {
        "starters": _coverage("starters", eligible_games, starter_covered),
        "statcast": _coverage("statcast", completed_games, statcast_covered),
        "lineups": _coverage("lineups", eligible_games, lineup_covered),
        "rosters": _coverage("rosters", eligible_games, roster_covered),
        "weather": _coverage("weather", eligible_games, weather_covered),
    }
    timestamp_violations = _timestamp_violations(list(tables.values()))
    duplicate_observations = {name: _duplicates(frame, PRIMARY_KEYS[name]) for name, frame in tables.items()}
    hard_fail = timestamp_violations > 0 or any(conflict_counts.values())
    coverage_fail = any(not value["passes"] for value in coverage.values())
    report: dict[str, Any] = {
        "sport": "mlb",
        "version": "v3",
        "season": season,
        "games_present": latest_games.height,
        "duplicate_observations": duplicate_observations,
        "conflicting_primary_keys": conflict_counts,
        "doubleheader_games": latest_games.filter(pl.col("doubleheader_number") > 1).height,
        "postponed_games": latest_games.filter(pl.col("postponed")).height,
        "delayed_games": latest_games.filter(pl.col("delayed")).height,
        "suspended_games": latest_games.filter(pl.col("suspended")).height,
        "starter_observations": starters.height,
        "lineup_rows": tables["lineups"].height,
        "roster_rows": tables["rosters"].height,
        "statcast_rows": tables["statcast_pitches"].height,
        "weather_rows": tables["weather_forecasts"].height,
        "coverage": coverage,
        "timestamp_violations": timestamp_violations,
        "availability_basis": "capture_time_only",
        "status": "ERROR" if hard_fail else ("DEGRADED" if coverage_fail else "HEALTHY"),
        "qualification_note": (
            "Captures made now do not prove historical pregame availability. "
            "Retrospective qualification remains blocked until replay-safe vintages exist."
        ),
    }
    return report
