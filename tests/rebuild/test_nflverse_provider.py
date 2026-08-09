from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import polars as pl

from model_prediction.rebuild.providers.base import ProviderStatus
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.nflverse import NFLVerseProvider

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
    assert second.metadata is not None and second.metadata.from_cache is True
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
