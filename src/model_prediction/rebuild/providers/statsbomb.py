"""StatsBomb Open Data policy boundary for this trading-oriented repository."""

from __future__ import annotations

from typing import Any

from .base import ProviderResult
from .rights import SourceRightsProfile

STATSBOMB_OPEN_RIGHTS = SourceRightsProfile(
    source_asset="StatsBomb Open Data",
    provider_chain="StatsBomb Open Data repository",
    license_id="StatsBomb-Open-Data-User-Agreement",
    license_url="https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf",
    attribution_required=True,
    attribution_text="StatsBomb Open Data",
    subscription_required=False,
    subscription_scope="none",
    upstream_rights_status="prohibited",
    commercial_use_status="prohibited",
    use_scope="policy_blocked",
    production_allowed=False,
    policy_note=(
        "The agreement expressly prohibits commercial exploitation of the data "
        "and derived analysis; collection is disabled in this repository."
    ),
)

POLICY_REASON = (
    "POLICY_BLOCKED: StatsBomb Open Data is not enabled because its agreement "
    "prohibits commercial exploitation of the data and derived analysis; "
    "no network request was made"
)


class StatsBombOpenDataProvider:
    """Intentionally non-networking until counsel/owner records compatible permission."""

    provider_id = "statsbomb_open_data"
    rights = STATSBOMB_OPEN_RIGHTS

    def events(self, *, sport: str, **_kwargs: Any) -> ProviderResult:
        if sport != "soccer":
            return ProviderResult.unavailable(f"StatsBomb adapter does not serve {sport}")
        return ProviderResult.policy_blocked(POLICY_REASON)

    def schedule(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult:
        return self.events(sport=sport, season=season, **kwargs)
