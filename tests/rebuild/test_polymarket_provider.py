from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from model_prediction.data_sources.polymarket_us import SportSlateResult
from model_prediction.rebuild.providers.base import ProviderStatus
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.polymarket import PolymarketProvider


@dataclass
class _FakeClient:
    slate_result: SportSlateResult | None = None
    snapshot_result: dict | None = None

    def sport_slate(self, sport: str, game_date: date) -> SportSlateResult:
        assert self.slate_result is not None
        return self.slate_result

    def snapshot(self, slug: str, observed_at: datetime) -> dict:
        assert self.snapshot_result is not None
        return {**self.snapshot_result, "slug": slug}


def test_sport_slate_flattens_leagues_and_records_errors(tmp_path):
    result = SportSlateResult(
        events={
            "MLB": [{"event_id": "e1", "startTime": "2026-08-10T23:00:00Z"}],
            "NPB": [],
        },
        errors={"NPB": "boom"},
    )
    client = _FakeClient(slate_result=result)
    provider = PolymarketProvider(client, ProviderRawCache(tmp_path))
    outcome = provider.sport_slate("mlb", date(2026, 8, 10))
    assert outcome.frame is not None
    assert outcome.frame["event_id"].to_list() == ["e1"]
    assert outcome.metadata is not None
    assert outcome.metadata.production_allowed is False
    assert outcome.metadata.http_status == 207  # partial: one league errored


def test_sport_slate_caches_raw_events(tmp_path):
    result = SportSlateResult(events={"MLB": [{"event_id": "e1"}]}, errors={})
    client = _FakeClient(slate_result=result)
    cache = ProviderRawCache(tmp_path)
    provider = PolymarketProvider(client, cache)
    provider.sport_slate("mlb", date(2026, 8, 10))
    cached = cache.latest("polymarket_us", "mlb", "sport_slate", {"sport": "mlb", "game_date": "2026-08-10"})
    assert cached is not None


def test_snapshot_is_stored_as_new_evidence_every_call(tmp_path):
    client = _FakeClient(snapshot_result={"best_bid": 0.45, "best_ask": 0.47})
    cache = ProviderRawCache(tmp_path)
    provider = PolymarketProvider(client, cache)
    first = provider.snapshot("some-market-slug", sport="mlb")
    second = provider.snapshot("some-market-slug", sport="mlb")
    assert first.status is ProviderStatus.AVAILABLE
    assert second.status is ProviderStatus.AVAILABLE
    # Every observation is new evidence -- two distinct manifests, not a dedupe.
    manifests = list(tmp_path.rglob("observations/*.json"))
    assert len(manifests) == 2


def test_snapshot_transport_failure_is_unavailable_not_raised(tmp_path):
    class _BrokenClient:
        def snapshot(self, slug: str, observed_at: datetime) -> dict:
            raise RuntimeError("network down")

    provider = PolymarketProvider(_BrokenClient(), ProviderRawCache(tmp_path))
    result = provider.snapshot("slug", sport="mlb")
    assert result.status is ProviderStatus.UNAVAILABLE
    assert "network down" in (result.reason or "")
