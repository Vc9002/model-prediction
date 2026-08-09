"""Tennis historical-source policy.

This module is deliberately boring: there is no network fallback, mirror
discovery, or mutable ``master`` URL.  A future operator may approve a local,
checksum-pinned snapshot after licensing and provenance review; until then the
source is unavailable and tennis cannot claim a validated data foundation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from model_prediction.rebuild.providers.base import ProviderResult


class TennisSourcePolicyError(ValueError):
    """Raised when source use would cross the explicit research boundary."""


@dataclass(frozen=True)
class HistoricalSourcePolicy:
    provider: str
    enabled: bool
    network_download_allowed: bool
    approved_for_commercial_use: bool
    license_id: str
    availability_basis: str
    former_primary_urls: tuple[str, ...]
    reason: str

    def unavailable_result(self) -> ProviderResult:
        return ProviderResult.unavailable(f"SOURCE_UNAVAILABLE: {self.reason}")

    def require_approved_local_root(self, root: str | Path) -> Path:
        """Accept only an operator-supplied local path when policy is enabled.

        Merely constructing a manifest cannot turn the source on.  The code
        policy must be changed in a reviewed commit after source and license
        approval; this makes accidental runtime configuration insufficient.
        """
        raw = str(root)
        if urlparse(raw).scheme:
            raise TennisSourcePolicyError("remote tennis sources and mirrors are policy-blocked")
        if not self.enabled:
            raise TennisSourcePolicyError(f"historical tennis source is disabled: {self.reason}")
        path = Path(root)
        if not path.is_absolute():
            raise TennisSourcePolicyError("approved tennis snapshot root must be an absolute local path")
        return path


HISTORICAL_SOURCE_POLICY = HistoricalSourcePolicy(
    provider="jeff_sackmann",
    enabled=False,
    network_download_allowed=False,
    approved_for_commercial_use=False,
    license_id="CC-BY-NC-SA-4.0",
    availability_basis="capture_time_only",
    former_primary_urls=(
        "https://github.com/JeffSackmann/tennis_atp",
        "https://github.com/JeffSackmann/tennis_wta",
    ),
    reason=(
        "former primary ATP/WTA repositories are unavailable; no approved local snapshot, "
        "commercial-use permission, or historical observation-time evidence exists"
    ),
)
