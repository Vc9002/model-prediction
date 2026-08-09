from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx
import pytest

from model_prediction.rebuild.providers.base import (
    SourceGrade,
    SourceResponseMetadata,
    assert_economic_use_allowed,
)
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy


def _metadata(body: bytes, retrieved: datetime) -> SourceResponseMetadata:
    iso = retrieved.astimezone(UTC).isoformat()
    return SourceResponseMetadata(
        provider="fixture",
        sport="wnba",
        endpoint_family="schedule",
        requested_parameters={"season": 2024},
        request_time_utc=iso,
        retrieved_at_utc=iso,
        observed_at_utc=iso,
        http_status=200,
        content_hash=hashlib.sha256(body).hexdigest(),
        schema_hash="schema",
        source_grade=SourceGrade.B,
    )


def test_identical_raw_bytes_share_blob_but_keep_observations(tmp_path):
    cache = ProviderRawCache(tmp_path)
    body = b"immutable-source"
    first = cache.store(_metadata(body, datetime(2026, 1, 1, tzinfo=UTC)), body)
    second = cache.store(_metadata(body, datetime(2026, 1, 2, tzinfo=UTC)), body)

    assert first.body_path == second.body_path
    assert first.manifest_path != second.manifest_path
    assert len(list(first.body_path.parent.glob("*.bin"))) == 1
    assert len(list(first.manifest_path.parent.glob("*.json"))) == 2
    assert cache.latest("fixture", "wnba", "schedule", {"season": 2024}).read_bytes() == body


def test_cached_hash_mismatch_fails_closed(tmp_path):
    cache = ProviderRawCache(tmp_path)
    cached = cache.store(_metadata(b"good", datetime(2026, 1, 1, tzinfo=UTC)), b"good")
    cached.body_path.write_bytes(b"bad")
    with pytest.raises(ValueError, match="SHA256"):
        cached.read_bytes()


def test_http_retries_only_bounded_retryable_statuses():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls < 3 else 200, content=b"ok", request=request)

    sleeps: list[float] = []
    client = HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=3, base_delay_seconds=0.1, jitter_seconds=0),
        sleep=sleeps.append,
        min_interval_seconds=0,
    )
    result = client.get("https://example.invalid/data")
    assert result.status_code == 200
    assert result.attempts == 3
    assert calls == 3
    assert sleeps == [0.1, 0.2]


def test_http_does_not_retry_403():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, request=request)

    client = HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=3),
        sleep=lambda _seconds: None,
    )
    assert client.get("https://example.invalid/forbidden").status_code == 403
    assert calls == 1


def test_unresolved_rights_cannot_be_marked_production_allowed():
    with pytest.raises(ValueError, match="cleared commercial and upstream rights"):
        SourceResponseMetadata(
            provider="unsafe",
            sport="nfl",
            endpoint_family="asset",
            requested_parameters={},
            request_time_utc="2026-01-01T00:00:00+00:00",
            retrieved_at_utc="2026-01-01T00:00:00+00:00",
            observed_at_utc="2026-01-01T00:00:00+00:00",
            http_status=200,
            content_hash="a" * 64,
            schema_hash=None,
            commercial_use_status="unresolved",
            production_allowed=True,
        )

    with pytest.raises(ValueError, match="cleared commercial and upstream rights"):
        SourceResponseMetadata(
            provider="unsafe-upstream",
            sport="nfl",
            endpoint_family="asset",
            requested_parameters={},
            request_time_utc="2026-01-01T00:00:00+00:00",
            retrieved_at_utc="2026-01-01T00:00:00+00:00",
            observed_at_utc="2026-01-01T00:00:00+00:00",
            http_status=200,
            content_hash="a" * 64,
            schema_hash=None,
            upstream_rights_status="unresolved",
            commercial_use_status="cleared",
            production_allowed=True,
        )

    unresolved = _metadata(b"asset", datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(PermissionError, match="not cleared for production/economic use"):
        assert_economic_use_allowed(unresolved)
