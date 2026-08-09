"""Structural and provenance audit for MLB v3 normalized data."""

from __future__ import annotations

from typing import Any

import polars as pl

from .store import MLBV3NormalizedStore


def _duplicates(frame: pl.DataFrame, keys: list[str]) -> int:
    if frame.is_empty():
        return 0
    return frame.group_by(keys).len().filter(pl.col("len") > 1).height


def audit_mlb_v3(store: MLBV3NormalizedStore, season: int) -> dict[str, Any]:
    games = store.read("games", season)
    if games.is_empty():
        return {
            "sport": "mlb",
            "version": "v3",
            "season": season,
            "status": "NO_DATA",
            "games_present": 0,
            "qualification_note": "No normalized MLB v3 game captures exist.",
        }
    starters = store.read("probable_pitchers", season)
    lineups = store.read("lineups", season)
    rosters = store.read("rosters", season)
    statcast = store.read("statcast_pitches", season)
    weather = store.read("weather_forecasts", season)
    latest_games = games.sort("observed_at_utc").unique(["game_pk", "period"], keep="last")
    timestamp_violations = sum(
        frame.filter(pl.col("observed_at_utc") > pl.col("retrieved_at_utc")).height
        for frame in (games, starters, lineups, rosters, statcast, weather)
        if not frame.is_empty()
    )
    report: dict[str, Any] = {
        "sport": "mlb",
        "version": "v3",
        "season": season,
        "games_present": latest_games.height,
        "duplicate_game_observations": _duplicates(games, ["game_pk", "period", "observed_at_utc"]),
        "duplicate_game_pk": _duplicates(latest_games, ["game_pk", "period"]),
        "doubleheader_games": latest_games.filter(pl.col("doubleheader_number") > 1).height,
        "postponed_games": latest_games.filter(pl.col("postponed")).height,
        "starter_observations": starters.height,
        "lineup_rows": lineups.height,
        "roster_rows": rosters.height,
        "statcast_rows": statcast.height,
        "weather_rows": weather.height,
        "timestamp_violations": timestamp_violations,
        "availability_basis": "capture_time_only",
    }
    hard_fail = report["duplicate_game_observations"] > 0 or timestamp_violations > 0
    core_missing = starters.is_empty() or statcast.is_empty()
    report["status"] = "ERROR" if hard_fail else ("DEGRADED" if core_missing else "HEALTHY")
    report["qualification_note"] = (
        "Captures made now do not prove historical pregame availability. "
        "Retrospective qualification remains blocked until replay-safe vintages exist."
    )
    return report
