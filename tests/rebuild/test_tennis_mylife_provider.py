from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from model_prediction.rebuild.providers.base import ProviderStatus, assert_economic_use_allowed
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.tennis_mylife import TennisMyLifeProvider

FIXTURES = Path(__file__).parent / "fixtures/providers/tennis"
INDEX_BODY = (FIXTURES / "data_files_index_sample.json").read_bytes()
WTA_CSV_BODY = (FIXTURES / "wta_2026_sample.csv").read_bytes()


def _http(handler) -> HttpProviderClient:
    return HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=1),
    )


def test_data_files_index_is_raw_first_cached_and_rights_gated(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/data-files"
        return httpx.Response(200, content=INDEX_BODY, request=request)

    provider = TennisMyLifeProvider(_http(handler), ProviderRawCache(tmp_path))
    first = provider.data_files_index()
    second = provider.data_files_index()

    assert first.status is ProviderStatus.AVAILABLE
    assert first.frame is not None and "2026_wta.csv" in first.frame["name"].to_list()
    assert first.metadata is not None
    assert first.metadata.production_allowed is False
    with pytest.raises(PermissionError, match="not cleared"):
        assert_economic_use_allowed(first.metadata)
    assert second.metadata is not None and second.metadata.from_cache
    assert calls == 1


def test_year_matches_resolves_real_url_from_index_not_a_hardcoded_pattern(tmp_path):
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/data-files":
            return httpx.Response(200, content=INDEX_BODY, request=request)
        assert request.url.path == "/data/2026_wta.csv"
        return httpx.Response(200, content=WTA_CSV_BODY, request=request)

    provider = TennisMyLifeProvider(_http(handler), ProviderRawCache(tmp_path))
    result = provider.year_matches("wta", 2026)

    assert result.status is ProviderStatus.AVAILABLE
    assert result.frame is not None and result.frame.height >= 1
    assert "/api/data-files" in requested_paths
    assert "/data/2026_wta.csv" in requested_paths


def test_year_matches_fails_closed_when_file_not_in_index(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=INDEX_BODY, request=request)

    provider = TennisMyLifeProvider(_http(handler), ProviderRawCache(tmp_path))
    result = provider.year_matches("atp", 1900)  # not in the sample index
    assert result.status is ProviderStatus.UNAVAILABLE
    assert "no file named" in (result.reason or "")


def test_qualifying_only_supported_for_atp():
    provider = TennisMyLifeProvider(_http(lambda r: httpx.Response(200)), ProviderRawCache("/tmp/unused"))
    result = provider.year_matches("wta", 2026, kind="qualifying")
    assert result.status is ProviderStatus.UNAVAILABLE
    assert "only publishes qualifying" in (result.reason or "")


def test_ongoing_matches_resolves_correct_filename_per_tour(tmp_path):
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/data-files":
            return httpx.Response(200, content=INDEX_BODY, request=request)
        return httpx.Response(200, content=WTA_CSV_BODY, request=request)

    provider = TennisMyLifeProvider(_http(handler), ProviderRawCache(tmp_path))
    provider.ongoing_matches("atp")
    assert "/data/ongoing_tourneys.csv" in requested_paths


def test_missing_required_columns_is_degraded(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/data-files":
            return httpx.Response(200, content=INDEX_BODY, request=request)
        return httpx.Response(200, content=b"not_a_real_column\n1\n", request=request)

    provider = TennisMyLifeProvider(_http(handler), ProviderRawCache(tmp_path))
    result = provider.year_matches("wta", 2026)
    assert result.status is ProviderStatus.DEGRADED
    assert "schema drift" in (result.reason or "")


def test_a_cached_index_failure_does_not_block_a_later_retry(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, content=b"unavailable", request=request)
        return httpx.Response(200, content=INDEX_BODY, request=request)

    provider = TennisMyLifeProvider(_http(handler), ProviderRawCache(tmp_path))
    first = provider.data_files_index()
    assert first.status is ProviderStatus.UNAVAILABLE
    second = provider.data_files_index()
    assert second.status is ProviderStatus.AVAILABLE
    assert calls == 2
