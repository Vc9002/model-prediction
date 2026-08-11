"""Backfill ESPN soccer scoreboard data by season.

The `rebuild-data backfill` CLI requires --date (YYYY-MM-DD) for soccer, with
the provider formatting it to YYYYMMDD. ESPN's site v2 API also accepts
`dates=YYYY` to return a full calendar year of games. This script directly
calls the ESPN API with the year-only parameter, then normalizes and persists
through the existing SoccerFoundation pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import polars as pl

from model_prediction.rebuild.config import load_rebuild_config
from model_prediction.rebuild.providers.base import (
    ProviderResult,
    ProviderStatus,
    SourceGrade,
    SourceResponseMetadata,
)
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.soccer_espn import (
    ESPN_SOCCER_LEAGUES,
    ESPN_SOCCER_RIGHTS,
)
from model_prediction.rebuild.safety import RebuildPathPolicy, assert_shadow_only
from model_prediction.rebuild.soccer.normalize import normalize_soccer_matches
from model_prediction.rebuild.soccer.store import SoccerNormalizedStore


def fetch_season(
    http: HttpProviderClient,
    cache: ProviderRawCache,
    league_code: str,
    season: int,
    *,
    force: bool = False,
) -> ProviderResult:
    """Fetch a full calendar year from the ESPN site v2 scoreboard.

    Uses `dates={season}` which ESPN interprets as a full-year query.
    """
    provider_id = "espn_site_v2"
    endpoint = "soccer_scoreboard"
    parameters = {"dates": str(season), "limit": 1000, "league": league_code}

    if not force:
        cached = cache.latest_success(provider_id, "soccer", endpoint, parameters)
        if cached is not None:
            try:
                payload = json.loads(cached.read_bytes())
                events = payload.get("events")
                if isinstance(events, list):
                    metadata = cached.metadata
                    return _parse_scoreboard(payload, metadata)
            except Exception:  # noqa: S110, BLE001
                pass

    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/scoreboard"
    try:
        fetched = http.get(url, params={"dates": str(season), "limit": 1000})
    except Exception as exc:  # noqa: BLE001 -- external transport boundary
        return ProviderResult.unavailable(
            f"ESPN soccer transport failed ({type(exc).__name__})"
        )

    metadata = SourceResponseMetadata(
        provider=provider_id,
        sport="soccer",
        endpoint_family=endpoint,
        requested_parameters=parameters,
        request_time_utc=fetched.request_time_utc,
        retrieved_at_utc=fetched.retrieved_at_utc,
        observed_at_utc=fetched.retrieved_at_utc,
        http_status=fetched.status_code,
        content_hash=hashlib.sha256(fetched.body).hexdigest(),
        schema_hash=None,
        content_type=fetched.headers.get("content-type"),
        source_version="espn-site-v2",
        source_grade=SourceGrade.C,
        **ESPN_SOCCER_RIGHTS.metadata_kwargs(),
    )

    cache.store(metadata, fetched.body)
    if fetched.status_code != 200:
        return ProviderResult.unavailable(
            f"ESPN soccer returned HTTP {fetched.status_code}", metadata
        )

    try:
        payload = json.loads(fetched.body)
    except json.JSONDecodeError as exc:
        return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"JSON parse: {exc}")

    return _parse_scoreboard(payload, metadata)


def _parse_scoreboard(
    payload: dict[str, Any], metadata: SourceResponseMetadata
) -> ProviderResult:
    """Parse ESPN scoreboard JSON into provider rows (same logic as ESPNSoccerProvider._parse)."""
    try:
        events = payload.get("events")
        if not isinstance(events, list):
            raise TypeError("payload lacks events list")
        rows: list[dict[str, Any]] = []
        for event in events:
            competition = event["competitions"][0]
            competitors = {item["homeAway"]: item for item in competition["competitors"]}
            home, away = competitors["home"], competitors["away"]
            status = competition.get("status", {}).get("type", {})
            league = event.get("league") or {}
            season_info = event.get("season") or {}
            venue = competition.get("venue") or {}
            rows.append(
                {
                    "source_match_id": str(event["id"]),
                    "competition_id": str(
                        league.get("slug") or metadata.requested_parameters["league"]
                    ),
                    "competition_name": str(
                        league.get("name") or metadata.requested_parameters["league"]
                    ),
                    "season_id": str(season_info.get("year") or "unknown"),
                    "event_start": str(event["date"]),
                    "status": str(status.get("name") or "UNKNOWN"),
                    "completed": bool(status.get("completed", False)),
                    "home_team_id": str(home["team"]["id"]),
                    "home_team_name": str(home["team"].get("displayName") or ""),
                    "away_team_id": str(away["team"]["id"]),
                    "away_team_name": str(away["team"].get("displayName") or ""),
                    "home_score": (
                        int(home["score"])
                        if home.get("score") not in (None, "")
                        else None
                    ),
                    "away_score": (
                        int(away["score"])
                        if away.get("score") not in (None, "")
                        else None
                    ),
                    "venue_id": str(venue.get("id") or "") or None,
                    "venue_name": venue.get("fullName"),
                    "provider_updated_at": None,
                }
            )
        frame = (
            pl.DataFrame(rows)
            if rows
            else pl.DataFrame(
                schema={
                    "source_match_id": pl.String,
                    "competition_id": pl.String,
                    "season_id": pl.String,
                    "event_start": pl.String,
                }
            )
        )
    except (KeyError, TypeError, ValueError) as exc:
        return ProviderResult(
            ProviderStatus.DEGRADED, metadata, None, f"ESPN soccer schema drift: {exc}"
        )

    from model_prediction.rebuild.providers.base import dataframe_schema_hash

    updated = replace(metadata, schema_hash=dataframe_schema_hash(frame))
    return ProviderResult(
        ProviderStatus.AVAILABLE,
        updated,
        frame,
        "NO_EVENTS" if frame.is_empty() else None,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backfill ESPN soccer by season")
    parser.add_argument(
        "--league", action="append", dest="leagues", required=True,
        help="ESPN league code (repeatable, e.g. eng.1)",
    )
    parser.add_argument(
        "--season", type=int, action="append", dest="seasons", required=True,
        help="Season year (repeatable, e.g. 2023)",
    )
    parser.add_argument(
        "--data-root", default="data/rebuild",
        help="Rebuild data root (default: data/rebuild)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-fetch even if cached",
    )
    args = parser.parse_args(argv)

    config = load_rebuild_config()
    assert_shadow_only(config)
    policy = RebuildPathPolicy.from_config(config)
    policy.assert_runtime_write(config.paths.data_root)

    data_root = Path(args.data_root)
    cache = ProviderRawCache(data_root / "raw")
    http = HttpProviderClient(retry=RetryPolicy())
    store = SoccerNormalizedStore(data_root / "normalized")

    total_written = 0
    for league in args.leagues:
        league_code = ESPN_SOCCER_LEAGUES.get(league)
        if league_code is None:
            print(f"  [SKIP] unknown league: {league}", file=sys.stderr)
            continue
        for season in args.seasons:
            print(f"Backfilling {league} ({league_code}) season {season} ...", flush=True)
            result = fetch_season(http, cache, league_code, season, force=args.force)
            if result.status is not ProviderStatus.AVAILABLE or result.frame is None:
                print(f"  [SKIP] {result.reason}", file=sys.stderr)
                continue
            if result.metadata is None:
                print("  [SKIP] no metadata", file=sys.stderr)
                continue
            try:
                normalized = normalize_soccer_matches(result.frame, result.metadata)
                written = normalized.height
                if written > 0:
                    store.write_matches(normalized)
                print(f"  -> {written} rows written", flush=True)
                total_written += written
            except Exception as exc:  # noqa: BLE001 -- best-effort normalization
                print(f"  [WARN] normalize failed: {exc}", file=sys.stderr)
        time.sleep(0.3)  # polite pause between leagues

    http.close()
    print(f"\nTotal rows written: {total_written}")

    # Verify
    frame = store.read_matches()
    if frame.height > 0:
        print(f"Total store rows: {frame.height}")
        print(f"Leagues: {sorted(frame['competition_id'].unique().to_list())}")
        print(f"Seasons: {sorted(frame['season_id'].unique().to_list())}")
        final = frame.filter(pl.col("status") == "STATUS_FINAL")
        print(f"Final matches: {final.height}")


if __name__ == "__main__":
    main()
