"""Tests for the reconciled shared provider layer (base/http/cache/rights).

Consolidates coverage from five per-sport copies of this same test file
(mlb-v3, wnba-v1, nfl-v1, soccer-v1, tennis-v1) plus new tests for the
capabilities merged in during reconciliation: `store_blob`'s collision
guard, `record_parse_result`, and `assert_frame_use_allowed`/
`DataUseContext`.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx
import polars as pl
import pytest

from model_prediction.rebuild.providers.base import (
    DataUseContext,
    SourceGrade,
    SourceResponseMetadata,
    assert_economic_use_allowed,
    assert_frame_use_allowed,
)
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.rights import SourceRightsProfile


def _metadata(body: bytes, retrieved: datetime, **overrides) -> SourceResponseMetadata:
    iso = retrieved.astimezone(UTC).isoformat()
    kwargs = {
        "provider": "fixture",
        "sport": "wnba",
        "endpoint_family": "schedule",
        "requested_parameters": {"season": 2024},
        "request_time_utc": iso,
        "retrieved_at_utc": iso,
        "observed_at_utc": iso,
        "http_status": 200,
        "content_hash": hashlib.sha256(body).hexdigest(),
        "schema_hash": "schema",
        "source_grade": SourceGrade.B,
    }
    kwargs.update(overrides)
    return SourceResponseMetadata(**kwargs)


# ── cache.py ──────────────────────────────────────────────────────────────


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


def test_store_refuses_to_overwrite_corrupted_content_addressed_blob(tmp_path):
    cache = ProviderRawCache(tmp_path)
    metadata = _metadata(b"good", datetime(2026, 1, 1, tzinfo=UTC))
    cached = cache.store(metadata, b"good")
    cached.body_path.write_bytes(b"bad")
    with pytest.raises(ValueError, match="conflicting immutable provider blob"):
        cache.store(metadata, b"good")


def test_same_observation_id_with_conflicting_manifest_fails_closed(tmp_path):
    cache = ProviderRawCache(tmp_path)
    body = b"same"
    metadata = _metadata(body, datetime(2026, 1, 1, tzinfo=UTC))
    cache.store(metadata, body)
    conflicting = SourceResponseMetadata(**{**metadata.__dict__, "schema_hash": "changed"})
    with pytest.raises(ValueError, match="conflicting immutable provider observation manifest"):
        cache.store(conflicting, body)


def test_latest_reconstructs_full_rights_shape(tmp_path):
    cache = ProviderRawCache(tmp_path)
    body = b"rights-round-trip"
    metadata = _metadata(
        body,
        datetime(2026, 1, 1, tzinfo=UTC),
        source_asset="Fixture Asset",
        provider_chain="fixture -> upstream",
        license_id="fixture-license",
        license_url="https://example.invalid/license",
        attribution_required=True,
        attribution_text="Fixture Attribution",
        subscription_required=True,
        subscription_scope="single_application",
        upstream_rights_status="cleared",
        commercial_use_status="cleared",
        use_scope="production_economic",
        production_allowed=True,
    )
    cache.store(metadata, body)
    reloaded = cache.latest("fixture", "wnba", "schedule", {"season": 2024})
    assert reloaded.metadata.source_asset == "Fixture Asset"
    assert reloaded.metadata.license_id == "fixture-license"
    assert reloaded.metadata.attribution_text == "Fixture Attribution"
    assert reloaded.metadata.subscription_scope == "single_application"
    assert reloaded.metadata.production_allowed is True
    assert reloaded.metadata.from_cache is True


def test_record_parse_result_is_separate_immutable_evidence(tmp_path):
    cache = ProviderRawCache(tmp_path)
    body = b"parseable"
    metadata = _metadata(body, datetime(2026, 1, 1, tzinfo=UTC))
    cache.store(metadata, body)
    path = cache.record_parse_result(metadata, parser_version="v1", status="AVAILABLE", schema_hash="abc")
    assert path.exists()
    # Same inputs -> same content-addressed path, not a second file.
    again = cache.record_parse_result(metadata, parser_version="v1", status="AVAILABLE", schema_hash="abc")
    assert again == path
    # A different parser version is new, distinguishable evidence.
    drifted = cache.record_parse_result(
        metadata, parser_version="v2", status="DEGRADED", schema_hash="def", reason="schema drift"
    )
    assert drifted != path
    assert drifted.exists()


def test_latest_success_ignores_a_cached_failure(tmp_path):
    """The negative-cache-poisoning fix: a cached 500 must not be reusable."""
    cache = ProviderRawCache(tmp_path)
    body = b"error page"
    failed = _metadata(body, datetime(2026, 1, 1, tzinfo=UTC), http_status=500)
    cache.store(failed, body)

    # latest() still returns the failure -- it's real evidence, not hidden.
    assert cache.latest("fixture", "wnba", "schedule", {"season": 2024}) is not None
    assert cache.latest("fixture", "wnba", "schedule", {"season": 2024}).metadata.http_status == 500

    # latest_success() must not treat it as reusable content.
    assert cache.latest_success("fixture", "wnba", "schedule", {"season": 2024}) is None


def test_latest_success_returns_the_most_recent_successful_observation_even_after_a_later_failure(tmp_path):
    cache = ProviderRawCache(tmp_path)
    good_body = b"good content"
    good = _metadata(good_body, datetime(2026, 1, 1, tzinfo=UTC), http_status=200)
    cache.store(good, good_body)

    bad_body = b"rate limited"
    bad = _metadata(bad_body, datetime(2026, 1, 2, tzinfo=UTC), http_status=429)
    cache.store(bad, bad_body)

    # latest() picks the newest observation regardless of outcome (the 429).
    assert cache.latest("fixture", "wnba", "schedule", {"season": 2024}).metadata.http_status == 429
    # latest_success() skips over it and finds the real prior success.
    success = cache.latest_success("fixture", "wnba", "schedule", {"season": 2024})
    assert success is not None
    assert success.metadata.http_status == 200
    assert success.read_bytes() == good_body


def test_latest_success_honors_custom_accepted_statuses(tmp_path):
    """Polymarket's own synthetic 207 'partial success' status is reusable."""
    cache = ProviderRawCache(tmp_path)
    body = b"partial slate"
    partial = _metadata(body, datetime(2026, 1, 1, tzinfo=UTC), http_status=207)
    cache.store(partial, body)
    assert cache.latest_success("fixture", "wnba", "schedule", {"season": 2024}) is None
    found = cache.latest_success(
        "fixture", "wnba", "schedule", {"season": 2024}, accepted_statuses=frozenset({200, 207})
    )
    assert found is not None and found.metadata.http_status == 207


# ── base.py: SourceResponseMetadata / rights gates ──────────────────────


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
            commercial_use_status="cleared",
            upstream_rights_status="unresolved",
            use_scope="production_economic",
            production_allowed=True,
        )
    unresolved = _metadata(b"asset", datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(PermissionError, match="not cleared for production/economic use"):
        assert_economic_use_allowed(unresolved)


def test_prohibited_rights_require_policy_blocked_scope():
    with pytest.raises(ValueError, match="prohibited rights require policy_blocked"):
        SourceResponseMetadata(
            provider="prohibited-source",
            sport="soccer",
            endpoint_family="events",
            requested_parameters={},
            request_time_utc="2026-01-01T00:00:00+00:00",
            retrieved_at_utc="2026-01-01T00:00:00+00:00",
            observed_at_utc="2026-01-01T00:00:00+00:00",
            http_status=200,
            content_hash="a" * 64,
            schema_hash=None,
            upstream_rights_status="prohibited",
            commercial_use_status="prohibited",
            use_scope="research_shadow_only",
        )


def test_attribution_required_needs_attribution_text():
    with pytest.raises(ValueError, match="attribution_text is required"):
        SourceResponseMetadata(
            provider="needs-attribution",
            sport="soccer",
            endpoint_family="events",
            requested_parameters={},
            request_time_utc="2026-01-01T00:00:00+00:00",
            retrieved_at_utc="2026-01-01T00:00:00+00:00",
            observed_at_utc="2026-01-01T00:00:00+00:00",
            http_status=200,
            content_hash="a" * 64,
            schema_hash=None,
            attribution_required=True,
            attribution_text=None,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commercial_use_status", "unknown"),
        ("upstream_rights_status", "unknown"),
        ("use_scope", "unknown"),
        ("production_allowed", "not-a-bool"),
    ],
)
def test_provider_metadata_rejects_unrecognized_rights_values(field, value):
    kwargs = {
        "provider": "fixture",
        "sport": "soccer",
        "endpoint_family": "events",
        "requested_parameters": {},
        "request_time_utc": "2026-08-10T00:00:00+00:00",
        "retrieved_at_utc": "2026-08-10T00:00:00+00:00",
        "observed_at_utc": "2026-08-10T00:00:00+00:00",
        "http_status": 200,
        "content_hash": "a" * 64,
        "schema_hash": "b" * 64,
        field: value,
    }
    with pytest.raises((TypeError, ValueError)):
        SourceResponseMetadata(**kwargs)


# ── base.py: DataUseContext / assert_frame_use_allowed ──────────────────


def test_research_context_bypasses_frame_rights_check():
    frame = pl.DataFrame({"x": [1]})
    assert_frame_use_allowed(frame, DataUseContext.RESEARCH)  # no raise, no column needed


def test_production_context_requires_production_allowed_column():
    frame = pl.DataFrame({"x": [1]})
    with pytest.raises(PermissionError, match="lacks production-rights provenance"):
        assert_frame_use_allowed(frame, DataUseContext.PRODUCTION_MODEL)


def test_production_context_rejects_any_uncleared_row():
    frame = pl.DataFrame({"x": [1, 2], "production_allowed": [True, False]})
    with pytest.raises(PermissionError, match="not cleared for production use"):
        assert_frame_use_allowed(frame, DataUseContext.SHADOW_ECONOMICS)


def test_production_context_accepts_fully_cleared_frame():
    frame = pl.DataFrame({"x": [1, 2], "production_allowed": [True, True]})
    assert_frame_use_allowed(frame, DataUseContext.LIVE_EXECUTION)  # no raise


# ── rights.py: SourceRightsProfile ───────────────────────────────────────


def _profile_kwargs(**overrides) -> dict:
    kwargs = {
        "source_asset": "Fixture Asset",
        "provider_chain": "fixture chain",
        "license_id": "fixture-license",
        "license_url": "https://example.invalid",
        "attribution_required": False,
        "attribution_text": None,
        "subscription_required": False,
        "subscription_scope": "none",
        "upstream_rights_status": "unresolved",
        "commercial_use_status": "unresolved",
        "use_scope": "research_shadow_only",
        "production_allowed": False,
        "policy_note": "fixture policy note",
    }
    kwargs.update(overrides)
    return kwargs


def test_source_rights_profile_metadata_kwargs_round_trips_into_metadata():
    profile = SourceRightsProfile(**_profile_kwargs())
    metadata = SourceResponseMetadata(
        provider="fixture",
        sport="mlb",
        endpoint_family="schedule",
        requested_parameters={},
        request_time_utc="2026-08-10T00:00:00+00:00",
        retrieved_at_utc="2026-08-10T00:00:00+00:00",
        observed_at_utc="2026-08-10T00:00:00+00:00",
        http_status=200,
        content_hash="a" * 64,
        schema_hash=None,
        **profile.metadata_kwargs(),
    )
    assert metadata.source_asset == "Fixture Asset"
    assert metadata.upstream_rights_status == "unresolved"


def test_source_rights_profile_rejects_empty_required_fields():
    with pytest.raises(ValueError, match="empty required metadata"):
        SourceRightsProfile(**_profile_kwargs(source_asset=""))


# ── http.py ───────────────────────────────────────────────────────────────


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


def test_http_honors_retry_after_seconds_on_429():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 2:
            return httpx.Response(429, headers={"Retry-After": "5"}, request=request)
        return httpx.Response(200, content=b"ok", request=request)

    sleeps: list[float] = []
    client = HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=3, base_delay_seconds=0.1, jitter_seconds=0),
        sleep=sleeps.append,
        min_interval_seconds=0,
    )
    result = client.get("https://example.invalid/data")
    assert result.status_code == 200
    # Used the real 5s from Retry-After, not the 0.1s exponential-backoff base.
    assert sleeps == [5.0]


def test_http_bounds_an_excessive_retry_after_value():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "999999"}, request=request)

    sleeps: list[float] = []
    client = HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=2, jitter_seconds=0, max_retry_after_seconds=30.0),
        sleep=sleeps.append,
        min_interval_seconds=0,
    )
    client.get("https://example.invalid/data")
    # A malicious/incorrect header must never be honored past the configured ceiling.
    assert sleeps == [30.0]


def test_http_falls_back_to_exponential_backoff_without_retry_after():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls < 2 else 200, content=b"ok", request=request)

    sleeps: list[float] = []
    client = HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=3, base_delay_seconds=0.25, jitter_seconds=0),
        sleep=sleeps.append,
        min_interval_seconds=0,
    )
    client.get("https://example.invalid/data")
    assert sleeps == [0.25]


def test_http_forwards_custom_headers():
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, content=b"ok", request=request)

    client = HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    client.get("https://example.invalid/data", headers={"X-Auth-Token": "secret"})
    assert seen_headers.get("x-auth-token") == "secret"
