"""Raw-first Polymarket US public market capture for the rebuild.

Wraps the existing, already-tested `data_sources.polymarket_us.PolymarketUSClient`
(proven, identical across every sport branch that forked this repo) rather
than reimplementing its pagination/error-handling -- this module's job is
only to route that client's calls through the shared immutable raw-capture
cache so market evidence has the same provenance/rights shape as every other
rebuild provider, not to replace a working transport.

Execution stays disabled regardless of this provider's rights status --
`production_allowed=False` here is about DATA rights, not the rebuild's
separate, independently-gated shadow-only invariant (see CLAUDE.md Part 3:
no real orders, no incumbent ledger writes, until separate authorization).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime

import polars as pl

from ...data_sources.polymarket_us import PolymarketUSClient
from .base import (
    ProviderResult,
    ProviderStatus,
    SourceGrade,
    SourceResponseMetadata,
    canonical_json,
    dataframe_schema_hash,
)
from .cache import ProviderRawCache
from .rights import SourceRightsProfile

POLYMARKET_RIGHTS = SourceRightsProfile(
    source_asset="Polymarket US public Gamma/CLOB gateway (events, market, book)",
    provider_chain="gateway.polymarket.us",
    license_id="polymarket-us-terms-review-required",
    license_url="https://polymarket.com/tos",
    attribution_required=False,
    attribution_text=None,
    subscription_required=False,
    subscription_scope="none",
    upstream_rights_status="unresolved",
    commercial_use_status="unresolved",
    use_scope="research_shadow_only",
    production_allowed=False,
    policy_note=(
        "Public gateway reachability does not by itself clear this rebuild "
        "provider for economic/production use -- the rebuild's own shadow-only "
        "execution gate (CLAUDE.md Part 3) is a separate, independent block "
        "regardless of this field."
    ),
)


class PolymarketProvider:
    provider_id = "polymarket_us"
    rights = POLYMARKET_RIGHTS

    def __init__(self, client: PolymarketUSClient, cache: ProviderRawCache) -> None:
        self.client = client
        self.cache = cache

    def sport_slate(self, sport: str, game_date: date, *, force: bool = False) -> ProviderResult:
        """Every gateway-league event for one sport-date, as raw normalized events."""
        parameters = {"sport": sport.lower(), "game_date": game_date.isoformat()}
        endpoint = "sport_slate"
        cached = self.cache.latest(self.provider_id, sport.lower(), endpoint, parameters)
        if cached is not None and not force:
            return self._frame_from_cached_events(cached.read_bytes(), cached.metadata)
        retrieved_at = datetime.now().astimezone().isoformat()
        try:
            result = self.client.sport_slate(sport, game_date)
        except Exception as exc:  # noqa: BLE001 -- external transport boundary
            return ProviderResult.unavailable(f"Polymarket sport_slate failed: {exc}")
        all_events = [event for league_events in result.events.values() for event in league_events]
        body = canonical_json({"events": all_events, "errors": result.errors})
        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport=sport.lower(),
            endpoint_family=endpoint,
            requested_parameters=parameters,
            request_time_utc=retrieved_at,
            retrieved_at_utc=retrieved_at,
            observed_at_utc=retrieved_at,
            http_status=200 if not result.errors else 207,
            content_hash=hashlib.sha256(body).hexdigest(),
            schema_hash=None,
            source_version="polymarket-us-gateway-v2",
            source_grade=SourceGrade.A,
            **self.rights.metadata_kwargs(),
        )
        self.cache.store(metadata, body)
        return self._frame_from_cached_events(body, metadata, errors=result.errors)

    @staticmethod
    def _frame_from_cached_events(
        body: bytes, metadata: SourceResponseMetadata, *, errors: dict[str, str] | None = None
    ) -> ProviderResult:
        payload = json.loads(body)
        events = payload.get("events", [])
        errors = errors if errors is not None else payload.get("errors", {})
        frame = pl.DataFrame(events) if events else pl.DataFrame(schema={"event_id": pl.String})
        enriched = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        reason = f"league_errors={errors}" if errors else ("NO_EVENTS" if frame.is_empty() else None)
        status = ProviderStatus.DEGRADED if errors and frame.is_empty() else ProviderStatus.AVAILABLE
        return ProviderResult(status, enriched, frame, reason)

    def snapshot(self, slug: str, *, sport: str, observed_at: datetime | None = None, force: bool = False) -> ProviderResult:
        """One market's real executable book (best bid/ask, depth) at capture time.

        Never cached across calls with `force=False` skipped -- a book is a
        moving quote, and every real observation is new evidence, not a
        replaceable "latest" value. `force` here only controls whether we
        still attempt a fresh HTTP call; every successful call is stored.
        """
        moment = observed_at or datetime.now().astimezone()
        try:
            raw = self.client.snapshot(slug, moment)
        except Exception as exc:  # noqa: BLE001 -- external transport boundary
            return ProviderResult.unavailable(f"Polymarket snapshot failed for {slug}: {exc}")
        body = canonical_json(raw)
        retrieved_at = datetime.now().astimezone().isoformat()
        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport=sport.lower(),
            endpoint_family="market_snapshot",
            requested_parameters={"slug": slug},
            request_time_utc=retrieved_at,
            retrieved_at_utc=retrieved_at,
            observed_at_utc=moment.isoformat(),
            http_status=200,
            content_hash=hashlib.sha256(body).hexdigest(),
            schema_hash=None,
            source_event_id=slug,
            source_version="polymarket-us-gateway-v2",
            source_grade=SourceGrade.A,
            **self.rights.metadata_kwargs(),
        )
        self.cache.store(metadata, body)
        frame = pl.DataFrame([raw])
        enriched = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(ProviderStatus.AVAILABLE, enriched, frame)
