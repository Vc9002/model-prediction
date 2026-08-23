from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import polars as pl
import pytest

from model_prediction.rebuild.providers.base import ProviderStatus, assert_economic_use_allowed
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.nflverse import NFLVERSE_RELEASE_ASSETS, NFLVerseProvider

FIXTURES = Path(__file__).parent / "fixtures/providers/nflverse"


def _parquet_fixture(name: str) -> bytes:
    frame = pl.DataFrame(json.loads((FIXTURES / name).read_text()))
    output = io.BytesIO()
    frame.write_parquet(output)
    return output.getvalue()


def test_schedule_is_raw_first_filtered_and_cached_with_capture_time(tmp_path):
    body = _parquet_fixture("nfl_schedule_rows.json")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path.endswith("/schedules/games.parquet")
        return httpx.Response(200, content=body, headers={"content-type": "application/octet-stream"})

    http = HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=1),
    )
    cache = ProviderRawCache(tmp_path)
    provider = NFLVerseProvider(http, cache)
    first = provider.season_table("schedule", 2024)
    second = provider.season_table("schedule", 2024)

    assert first.status is ProviderStatus.AVAILABLE
    assert first.frame is not None and first.frame["season"].to_list() == [2024]
    assert first.metadata is not None
    assert first.metadata.observed_at_utc == first.metadata.retrieved_at_utc
    assert first.metadata.license_id == "CC-BY-4.0"
    assert first.metadata.license_url == "https://creativecommons.org/licenses/by/4.0/"
    assert first.metadata.attribution_required is True
    assert "nflverse" in (first.metadata.attribution_text or "")
    assert first.metadata.upstream_rights_status == "unresolved"
    assert first.metadata.commercial_use_status == "unresolved"
    assert first.metadata.production_allowed is False
    with pytest.raises(PermissionError, match="not cleared for production/economic use"):
        assert_economic_use_allowed(first.metadata)
    assert second.metadata is not None and second.metadata.from_cache is True
    assert second.metadata.license_id == "CC-BY-4.0"
    assert second.metadata.production_allowed is False
    assert calls == 1
    assert (
        cache.latest(
            "nflverse",
            "nfl",
            "nfl_schedule",
            {
                "asset": "games.parquet",
                "table": "schedule",
            },
        ).read_bytes()
        == body
    )


def test_schema_drift_is_degraded_not_silently_empty(tmp_path):
    output = io.BytesIO()
    pl.DataFrame({"season": [2024], "game_id": ["x"]}).write_parquet(output)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=output.getvalue(), request=request)

    provider = NFLVerseProvider(
        HttpProviderClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            retry=RetryPolicy(attempts=1),
        ),
        ProviderRawCache(tmp_path),
    )
    result = provider.season_table("schedule", 2024)
    assert result.status is ProviderStatus.DEGRADED
    assert result.frame is None
    assert "missing required columns" in (result.reason or "")


def test_invalid_non_parquet_body_is_retained_and_fails_closed(tmp_path):
    body = b"not parquet"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, request=request)

    cache = ProviderRawCache(tmp_path)
    provider = NFLVerseProvider(
        HttpProviderClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            retry=RetryPolicy(attempts=1),
        ),
        cache,
    )
    result = provider.season_table("pbp", 2024)
    assert result.status is ProviderStatus.DEGRADED
    cached = cache.latest(
        "nflverse",
        "nfl",
        "nfl_pbp",
        {"asset": "play_by_play_2024.parquet", "table": "pbp", "season": 2024},
    )
    assert cached is not None and cached.read_bytes() == body


def test_every_nflverse_asset_is_attribution_tagged_and_not_production_cleared():
    for asset in NFLVERSE_RELEASE_ASSETS.values():
        assert asset.project_license_id == "CC-BY-4.0"
        assert asset.attribution_required is True
        assert asset.attribution_text
        assert asset.upstream_rights_status == "unresolved"
        assert asset.commercial_use_status == "unresolved"
        assert asset.production_allowed is False


def test_nfl_injury_impact_calculator():
    from model_prediction.rebuild.providers.nflverse import NFLInjuryImpactCalculator

    # Home team missing Starting QB (OUT = 5.0) and WR1 (QUESTIONABLE = 0.35 * 1.25 = 0.4375)
    home_injuries = [
        {"position": "QB", "report_status": "Out"},
        {"position": "WR", "report_status": "Questionable"},
    ]
    # Away team missing starting Cornerback (OUT = 1.0)
    away_injuries = [
        {"position": "CB", "report_status": "Out"},
    ]

    home_penalty = NFLInjuryImpactCalculator.calculate_team_injury_penalty(home_injuries)
    away_penalty = NFLInjuryImpactCalculator.calculate_team_injury_penalty(away_injuries)

    assert home_penalty == pytest.approx(5.44, abs=0.01)
    assert away_penalty == pytest.approx(1.00, abs=0.01)

    # Net spread delta from Home perspective: Away penalty (1.0) - Home penalty (5.44) = -4.44
    delta = NFLInjuryImpactCalculator.calculate_matchup_injury_delta(home_injuries, away_injuries)
    assert delta == pytest.approx(-4.44, abs=0.01)
