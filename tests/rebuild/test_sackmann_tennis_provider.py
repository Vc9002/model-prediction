from __future__ import annotations

from model_prediction.rebuild.providers.base import ProviderStatus
from model_prediction.rebuild.providers.sackmann_tennis import SackmannTennisProvider


def test_matches_is_source_unavailable_with_no_network_surface():
    provider = SackmannTennisProvider()
    result = provider.matches(tour="atp", season=2026)
    assert result.status is ProviderStatus.UNAVAILABLE
    assert result.frame is None and result.metadata is None
    assert "SOURCE_UNAVAILABLE" in (result.reason or "")
    assert "No network request" in (result.reason or "")


def test_rankings_rejects_unsupported_tour():
    provider = SackmannTennisProvider()
    result = provider.rankings(tour="itf")
    assert result.status is ProviderStatus.UNAVAILABLE
    assert "unsupported tour" in (result.reason or "")


def test_rights_profile_reflects_unresolved_and_unattempted_source():
    provider = SackmannTennisProvider()
    assert provider.rights.upstream_rights_status == "unresolved"
    assert provider.rights.production_allowed is False
    assert provider.rights.use_scope == "research_shadow_only"


def test_players_and_schedule_also_fail_closed():
    provider = SackmannTennisProvider()
    assert provider.players(sport="tennis", season=2026).status is ProviderStatus.UNAVAILABLE
    assert provider.schedule(sport="tennis", season=2026).status is ProviderStatus.UNAVAILABLE
