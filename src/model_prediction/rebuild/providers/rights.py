"""Reusable source-rights profile for every free/open rebuild provider.

Public reachability, a free API tier, or an open-source client package is
not a grant of commercial/economic rights to the underlying data. Every
provider module in this package defines its own named `SourceRightsProfile`
instance(s) and threads them into `SourceResponseMetadata` via
`.metadata_kwargs()`, so a malformed or unresolved-rights row can never be
filtered away into looking safe downstream.

Promoted from soccer-v1's `soccer_rights.py` (the only rebuild branch that
had built this as its own reusable class rather than hand-writing the same
fields inline at every call site) -- kept sport-neutral here so every source
across every sport shares one audited shape, instead of five incompatible
ones.
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
            raise ValueError("source rights profile has empty required metadata")
        if self.upstream_rights_status not in RIGHTS_STATUSES:
            raise ValueError("upstream rights status is unknown")
        if self.commercial_use_status not in RIGHTS_STATUSES:
            raise ValueError("commercial-use status is unknown")
        if self.use_scope not in USE_SCOPES:
            raise ValueError("use scope is unknown")
        if self.attribution_required and not (self.attribution_text or "").strip():
            raise ValueError("source attribution text is required")
        if self.subscription_required and self.subscription_scope == "none":
            raise ValueError("source subscription scope is required")
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
