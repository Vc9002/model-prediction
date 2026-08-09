"""Opt-in live provider checks; never part of deterministic PR test runs."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from model_prediction.rebuild.providers.base import ProviderStatus
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.config import load_rebuild_sources_config
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.sportsdataverse import SportsDataverseProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("REBUILD_LIVE_API_TESTS") != "1",
    reason="set REBUILD_LIVE_API_TESTS=1 to run live provider health checks",
)


def test_live_wnba_scoreboard_contract(tmp_path):
    policy = load_rebuild_sources_config().sportsdataverse
    with HttpProviderClient(
        retry=RetryPolicy(attempts=policy.retries),
        min_interval_seconds=policy.min_interval_seconds,
    ) as http:
        provider = SportsDataverseProvider(http, ProviderRawCache(tmp_path))
        result = provider.current_schedule(datetime.now(UTC).date(), force=True)
    assert result.status is ProviderStatus.AVAILABLE, result.reason
    assert result.frame is not None
    assert result.metadata is not None
    assert result.metadata.http_status == 200
    assert result.metadata.content_hash
    assert result.metadata.schema_hash
