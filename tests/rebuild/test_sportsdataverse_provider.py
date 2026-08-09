from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import httpx
import polars as pl
import pytest

from model_prediction.rebuild.providers.base import ProviderStatus, assert_economic_use_allowed
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.sportsdataverse import SportsDataverseProvider

FIXTURE = Path(__file__).parent / "fixtures/providers/sportsdataverse/wnba_schedule_rows.json"
SCOREBOARD_FIXTURE = Path(__file__).parent / "fixtures/providers/sportsdataverse/wnba_scoreboard.json"


def _parquet_bytes(*, remove: str | None = None) -> bytes:
    frame = pl.DataFrame(json.loads(FIXTURE.read_text())).with_columns(
        pl.col("game_date_time").str.to_datetime(time_zone="UTC")
    )
    if remove:
        frame = frame.drop(remove)
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    return buffer.getvalue()


def _team_box_parquet_bytes(*, remove: str | None = None) -> bytes:
    frame = pl.DataFrame([{
        "game_id": "1",
        "season": 2024,
        "game_date_time": "2024-05-15T23:00:00+00:00",
        "team_id": "1",
        "team_display_name": "A",
        "opponent_team_id": "2",
        "team_home_away": "home",
        "team_score": 80,
        "field_goals_made": 30,
        "field_goals_attempted": 70,
        "three_point_field_goals_made": 7,
        "three_point_field_goals_attempted": 20,
        "free_throws_made": 13,
        "free_throws_attempted": 18,
        "offensive_rebounds": 8,
        "defensive_rebounds": 28,
        "turnovers": 10,
    }])
    if remove:
        frame = frame.drop(remove)
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    return buffer.getvalue()


def test_historical_asset_is_raw_first_and_cached(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=_parquet_bytes(), headers={"content-type": "application/octet-stream"})

    http = HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=1),
    )
    provider = SportsDataverseProvider(http, ProviderRawCache(tmp_path))
    first = provider.schedule(sport="wnba", season=2024)
    second = provider.schedule(sport="wnba", season=2024)

    assert first.status is ProviderStatus.AVAILABLE
    assert second.status is ProviderStatus.AVAILABLE
    assert second.metadata is not None and second.metadata.from_cache
    assert calls == 1
    assert len(list(tmp_path.rglob("*.bin"))) == 1
    assert first.metadata is not None
    assert first.metadata.commercial_use_status == "unresolved"
    assert first.metadata.production_allowed is False
    with pytest.raises(PermissionError, match="not cleared"):
        assert_economic_use_allowed(first.metadata)


def test_missing_required_column_is_degraded_not_fake_empty(tmp_path):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=_parquet_bytes(remove="home_id"))
    )
    provider = SportsDataverseProvider(
        HttpProviderClient(client=httpx.Client(transport=transport), retry=RetryPolicy(attempts=1)),
        ProviderRawCache(tmp_path),
    )
    result = provider.schedule(sport="wnba", season=2024)
    assert result.status is ProviderStatus.DEGRADED
    assert result.frame is None
    assert "home_id" in (result.reason or "")
    assert len(list(tmp_path.rglob("*.bin"))) == 1


def test_missing_required_team_box_feature_metric_is_degraded(tmp_path):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=_team_box_parquet_bytes(remove="turnovers"),
        )
    )
    provider = SportsDataverseProvider(
        HttpProviderClient(client=httpx.Client(transport=transport), retry=RetryPolicy(attempts=1)),
        ProviderRawCache(tmp_path),
    )
    result = provider.season_table("team_box", 2024)
    assert result.status is ProviderStatus.DEGRADED
    assert "turnovers" in (result.reason or "")


def test_404_is_unavailable_and_not_retried(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, content=b"missing", request=request)

    provider = SportsDataverseProvider(
        HttpProviderClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            retry=RetryPolicy(attempts=3),
            sleep=lambda _seconds: None,
        ),
        ProviderRawCache(tmp_path),
    )
    result = provider.schedule(sport="wnba", season=1900)
    assert result.status is ProviderStatus.UNAVAILABLE
    assert calls == 0

    result = provider.schedule(sport="wnba", season=2024)
    assert result.status is ProviderStatus.UNAVAILABLE
    assert calls == 1


def test_current_scoreboard_is_captured_before_parsing(tmp_path):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=SCOREBOARD_FIXTURE.read_bytes(), request=request)
    )
    provider = SportsDataverseProvider(
        HttpProviderClient(client=httpx.Client(transport=transport), retry=RetryPolicy(attempts=1)),
        ProviderRawCache(tmp_path),
    )
    result = provider.current_schedule(date(2026, 8, 10))
    assert result.status is ProviderStatus.AVAILABLE
    assert result.frame is not None
    assert result.frame["game_id"].to_list() == [401000002]
    assert len(list(tmp_path.rglob("*.bin"))) == 1


def test_malformed_current_scoreboard_is_degraded_and_raw_is_retained(tmp_path):
    body = b'{"unexpected": []}'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body, request=request))
    provider = SportsDataverseProvider(
        HttpProviderClient(client=httpx.Client(transport=transport), retry=RetryPolicy(attempts=1)),
        ProviderRawCache(tmp_path),
    )
    result = provider.current_schedule(date(2026, 8, 10))
    assert result.status is ProviderStatus.DEGRADED
    assert result.frame is None
    assert len(list(tmp_path.rglob("*.bin"))) == 1
