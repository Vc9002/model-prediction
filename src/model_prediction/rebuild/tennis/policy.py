"""Tennis historical-source policy.

This module is deliberately boring: there is no network fallback, mirror
discovery, or mutable ``master`` URL.  A future operator may approve a local,
checksum-pinned snapshot after licensing and provenance review; until then the
source is unavailable and tennis cannot claim a validated data foundation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from model_prediction.rebuild.providers.base import ProviderResult


class TennisSourcePolicyError(ValueError):
    """Raised when source use would cross the explicit research boundary."""


class CommercialUseStatus(StrEnum):
    PROHIBITED = "prohibited"
    ALLOWED = "allowed"


class PrimarySourceStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"


class TennisDataUse(StrEnum):
    RESEARCH = "research"
    ECONOMIC = "economic"
    PRODUCTION = "production"


@dataclass(frozen=True)
class HistoricalSourcePolicy:
    provider: str
    enabled: bool
    network_download_allowed: bool
    approved_for_commercial_use: bool
    commercial_use_status: CommercialUseStatus
    production_allowed: bool
    primary_source_status: PrimarySourceStatus
    attribution_required: bool
    share_alike_required: bool
    license_id: str
    availability_basis: str
    former_primary_urls: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        bool_fields = {
            "enabled": self.enabled,
            "network_download_allowed": self.network_download_allowed,
            "approved_for_commercial_use": self.approved_for_commercial_use,
            "production_allowed": self.production_allowed,
            "attribution_required": self.attribution_required,
            "share_alike_required": self.share_alike_required,
        }
        for name, value in bool_fields.items():
            if type(value) is not bool:
                raise TennisSourcePolicyError(f"{name} must be an explicit boolean")
        if not isinstance(self.commercial_use_status, CommercialUseStatus):
            raise TennisSourcePolicyError("commercial_use_status must be explicit and recognized")
        if not isinstance(self.primary_source_status, PrimarySourceStatus):
            raise TennisSourcePolicyError("primary_source_status must be explicit and recognized")
        commercially_allowed = self.commercial_use_status is CommercialUseStatus.ALLOWED
        if self.approved_for_commercial_use is not commercially_allowed:
            raise TennisSourcePolicyError("commercial-use approval fields are inconsistent")
        if self.production_allowed and not commercially_allowed:
            raise TennisSourcePolicyError("production cannot be allowed for commercially prohibited data")
        if self.license_id == "CC-BY-NC-SA-4.0":
            if commercially_allowed or self.production_allowed:
                raise TennisSourcePolicyError("CC BY-NC-SA data cannot be approved for economic production use")
            if not self.attribution_required or not self.share_alike_required:
                raise TennisSourcePolicyError("CC BY-NC-SA requires attribution and share-alike metadata")

    def rights_metadata(self) -> dict[str, str | bool]:
        return {
            "commercial_use_status": self.commercial_use_status.value,
            "production_allowed": self.production_allowed,
            "primary_source_status": self.primary_source_status.value,
            "attribution_required": self.attribution_required,
            "share_alike_required": self.share_alike_required,
            "license_id": self.license_id,
        }

    def assert_use_allowed(self, intended_use: TennisDataUse | str | None) -> None:
        try:
            if intended_use is None:
                raise ValueError
            use = TennisDataUse(intended_use)
        except (TypeError, ValueError) as exc:
            raise TennisSourcePolicyError("tennis data use must be explicit and recognized") from exc
        if use in {TennisDataUse.ECONOMIC, TennisDataUse.PRODUCTION} and (
            self.commercial_use_status is not CommercialUseStatus.ALLOWED
            or not self.approved_for_commercial_use
            or not self.production_allowed
        ):
            raise TennisSourcePolicyError(
                f"{use.value} use is prohibited by the tennis source license policy"
            )

    def unavailable_result(self) -> ProviderResult:
        return ProviderResult.unavailable(f"SOURCE_UNAVAILABLE: {self.reason}")

    def require_approved_local_root(
        self,
        root: str | Path,
        *,
        intended_use: TennisDataUse | str | None = TennisDataUse.RESEARCH,
    ) -> Path:
        """Accept only an operator-supplied local path when policy is enabled.

        Merely constructing a manifest cannot turn the source on.  The code
        policy must be changed in a reviewed commit after source and license
        approval; this makes accidental runtime configuration insufficient.
        """
        self.assert_use_allowed(intended_use)
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
    commercial_use_status=CommercialUseStatus.PROHIBITED,
    production_allowed=False,
    primary_source_status=PrimarySourceStatus.UNAVAILABLE,
    attribution_required=True,
    share_alike_required=True,
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


__all__ = [
    "HISTORICAL_SOURCE_POLICY",
    "CommercialUseStatus",
    "HistoricalSourcePolicy",
    "PrimarySourceStatus",
    "TennisDataUse",
    "TennisSourcePolicyError",
]
