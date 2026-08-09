"""StatsBomb Open Data policy boundary for this trading-oriented repository."""

from __future__ import annotations

from typing import Any

from .base import ProviderResult

POLICY_REASON = (
    "POLICY_BLOCKED: StatsBomb Open Data is not enabled because its terms prohibit "
    "commercial exploitation; no network request was made"
)


class StatsBombOpenDataProvider:
    """Intentionally non-networking until counsel/owner records compatible permission."""

    provider_id = "statsbomb_open_data"

    def events(self, *, sport: str, **_kwargs: Any) -> ProviderResult:
        if sport != "soccer":
            return ProviderResult.unavailable(f"StatsBomb adapter does not serve {sport}")
        return ProviderResult.policy_blocked(POLICY_REASON)

    def schedule(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult:
        return self.events(sport=sport, season=season, **kwargs)
