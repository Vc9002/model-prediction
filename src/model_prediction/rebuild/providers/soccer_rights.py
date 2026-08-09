"""Audited rights policies for the soccer research providers.

Public reachability and an open-source client are not grants to use the
underlying sports data economically.  These policies deliberately separate
research/shadow collection from production or trading use.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .base import RIGHTS_STATUSES, USE_SCOPES


@dataclass(frozen=True)
class SourceRightsProfile:
    source_asset: str
    provider_chain: str
    license_id: str
    license_url: str
    attribution_required: bool
    attribution_text: str | None
    subscription_required: bool
    subscription_scope: str
    upstream_rights_status: str
    commercial_use_status: str
    use_scope: str
    production_allowed: bool
    policy_note: str

    def __post_init__(self) -> None:
        required = {
            "source_asset": self.source_asset,
            "provider_chain": self.provider_chain,
            "license_id": self.license_id,
            "license_url": self.license_url,
            "subscription_scope": self.subscription_scope,
            "policy_note": self.policy_note,
        }
        if any(not value.strip() for value in required.values()):
            raise ValueError("soccer source rights profile has empty required metadata")
        if self.upstream_rights_status not in RIGHTS_STATUSES:
            raise ValueError("soccer upstream rights status is unknown")
        if self.commercial_use_status not in RIGHTS_STATUSES:
            raise ValueError("soccer commercial-use status is unknown")
        if self.use_scope not in USE_SCOPES:
            raise ValueError("soccer use scope is unknown")
        if self.attribution_required and not (self.attribution_text or "").strip():
            raise ValueError("soccer source attribution text is required")
        if self.subscription_required and self.subscription_scope == "none":
            raise ValueError("soccer source subscription scope is required")
        if self.production_allowed and (
            self.commercial_use_status != "cleared"
            or self.upstream_rights_status != "cleared"
            or self.use_scope != "production_economic"
        ):
            raise ValueError("production requires cleared commercial and upstream rights")

    def metadata_kwargs(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("policy_note")
        return value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ESPN_SOCCER_RIGHTS = SourceRightsProfile(
    source_asset="ESPN Site v2 soccer scoreboard",
    provider_chain="SportsDataverse catalog -> ESPN Site v2",
    license_id="ESPN-data-rights-unresolved",
    license_url="https://disneytermsofuse.com/",
    attribution_required=False,
    attribution_text=None,
    subscription_required=False,
    subscription_scope="none",
    upstream_rights_status="unresolved",
    commercial_use_status="unresolved",
    use_scope="research_shadow_only",
    production_allowed=False,
    policy_note=(
        "SportsDataverse code/package availability does not grant commercial "
        "rights to ESPN-derived data."
    ),
)

FOOTBALL_DATA_RIGHTS = SourceRightsProfile(
    source_asset="football-data.org API v4 competition matches",
    provider_chain="football-data.org API v4",
    license_id="football-data.org-subscription-terms",
    license_url="https://www.football-data.org/terms",
    attribution_required=True,
    attribution_text="Data provided by football-data.org",
    subscription_required=True,
    subscription_scope="single_application",
    upstream_rights_status="unresolved",
    commercial_use_status="unresolved",
    use_scope="research_shadow_only",
    production_allowed=False,
    policy_note=(
        "Use requires an active subscription, is scoped to one application, "
        "and requires visible attribution; economic rights remain unresolved."
    ),
)

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

SOCCER_SOURCE_RIGHTS = {
    "espn_site_v2": ESPN_SOCCER_RIGHTS,
    "football_data_v4": FOOTBALL_DATA_RIGHTS,
    "statsbomb_open_data": STATSBOMB_OPEN_RIGHTS,
}
