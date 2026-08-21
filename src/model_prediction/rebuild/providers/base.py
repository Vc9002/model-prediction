"""Provider-neutral contracts for free/open rebuild data.

Provider failures are explicit states. In particular, an empty DataFrame is
never used as a stand-in for unavailable data, and missing/unresolved source
rights are never silently treated as cleared.

Consolidated from five independently-evolved per-sport copies of this same
file (mlb-v3, wnba-v1, nfl-v1, soccer-v1, tennis-v1 rebuild worktrees) --
soccer-v1's `SourceResponseMetadata`/rights-status shape was the most
complete and is the canonical one here; mlb-v3's `DataUseContext`/
`assert_frame_use_allowed` DataFrame-level gate is genuinely distinct (checks
a whole normalized table, not one record) and kept alongside it, not
replaced by it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol

import polars as pl


class ProviderStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    POLICY_BLOCKED = "POLICY_BLOCKED"


class SourceGrade(StrEnum):
    A = "A"  # direct structured provider
    B = "B"  # stable open/versioned dataset
    C = "C"  # public undocumented endpoint
    D = "D"  # derived or imputed


class DataUseContext(StrEnum):
    RESEARCH = "RESEARCH"
    PRODUCTION_DATASET = "PRODUCTION_DATASET"
    PRODUCTION_MODEL = "PRODUCTION_MODEL"
    SHADOW_ECONOMICS = "SHADOW_ECONOMICS"
    LIVE_EXECUTION = "LIVE_EXECUTION"


RIGHTS_STATUSES = frozenset({"cleared", "unresolved", "prohibited"})
USE_SCOPES = frozenset({"research_shadow_only", "production_economic", "policy_blocked"})


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def dataframe_schema_hash(frame: pl.DataFrame) -> str:
    schema = [(name, str(dtype)) for name, dtype in frame.schema.items()]
    return hashlib.sha256(canonical_json(schema)).hexdigest()


@dataclass(frozen=True)
class SourceResponseMetadata:
    provider: str
    sport: str
    endpoint_family: str
    requested_parameters: dict[str, Any]
    request_time_utc: str
    retrieved_at_utc: str
    observed_at_utc: str
    http_status: int | None
    content_hash: str
    schema_hash: str | None
    source_event_id: str | None = None
    content_type: str | None = None
    source_version: str | None = None
    source_grade: SourceGrade = SourceGrade.B
    from_cache: bool = False
    source_asset: str | None = None
    provider_chain: str | None = None
    license_id: str | None = None
    license_url: str | None = None
    attribution_required: bool = False
    attribution_text: str | None = None
    subscription_required: bool = False
    subscription_scope: str = "none"
    upstream_rights_status: str = "unresolved"
    commercial_use_status: str = "unresolved"
    use_scope: str = "research_shadow_only"
    production_allowed: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (
            ("attribution_required", self.attribution_required),
            ("subscription_required", self.subscription_required),
            ("production_allowed", self.production_allowed),
        ):
            if not isinstance(value, bool):
                raise TypeError(f"{field_name} must be an explicit boolean")
        if not isinstance(self.subscription_scope, str) or not self.subscription_scope.strip():
            raise ValueError("subscription_scope must be explicit")
        if self.upstream_rights_status not in RIGHTS_STATUSES:
            raise ValueError("upstream_rights_status must be explicit and recognized")
        if self.commercial_use_status not in RIGHTS_STATUSES:
            raise ValueError("commercial_use_status must be explicit and recognized")
        if self.use_scope not in USE_SCOPES:
            raise ValueError("use_scope must be explicit and recognized")
        if self.attribution_required and not (self.attribution_text or "").strip():
            raise ValueError("attribution_text is required when attribution is required")
        if self.subscription_required and self.subscription_scope == "none":
            raise ValueError("subscription_scope is required when a subscription is required")
        if (
            "prohibited" in {self.commercial_use_status, self.upstream_rights_status}
            and self.use_scope != "policy_blocked"
        ):
            raise ValueError("prohibited rights require policy_blocked use scope")
        if self.production_allowed and (
            self.commercial_use_status != "cleared"
            or self.upstream_rights_status != "cleared"
            or self.use_scope != "production_economic"
        ):
            raise ValueError("production_allowed requires cleared commercial and upstream rights")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_grade"] = self.source_grade.value
        return value


def assert_economic_use_allowed(metadata: SourceResponseMetadata) -> None:
    """Reject production/economic use of one record unless every rights gate is cleared."""

    if (
        not metadata.production_allowed
        or metadata.commercial_use_status != "cleared"
        or metadata.upstream_rights_status != "cleared"
        or metadata.use_scope != "production_economic"
    ):
        raise PermissionError(
            f"provider {metadata.provider} is not cleared for production/economic use: "
            f"commercial={metadata.commercial_use_status}; "
            f"upstream={metadata.upstream_rights_status}; scope={metadata.use_scope}"
        )


def assert_frame_use_allowed(frame: pl.DataFrame, context: DataUseContext) -> None:
    """Require explicit, per-row source-rights clearance outside tagged research use.

    Distinct from `assert_economic_use_allowed`: that checks one provider
    response's metadata before it enters storage; this checks a whole
    normalized/analytical table (which may blend rows from several source
    responses) before it's used outside research -- a table is only as
    clean as its least-cleared row.
    """
    if context is DataUseContext.RESEARCH:
        return
    if "production_allowed" not in frame.columns:
        raise PermissionError(f"{context.value} input lacks production-rights provenance")
    if frame.is_empty() or not bool(frame["production_allowed"].all()):
        raise PermissionError(f"{context.value} input contains source data not cleared for production use")


@dataclass(frozen=True)
class ProviderResult:
    status: ProviderStatus
    metadata: SourceResponseMetadata | None
    frame: pl.DataFrame | None = None
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.status is ProviderStatus.AVAILABLE and self.frame is not None

    @classmethod
    def unavailable(cls, reason: str, metadata: SourceResponseMetadata | None = None) -> ProviderResult:
        return cls(ProviderStatus.UNAVAILABLE, metadata, None, reason)

    @classmethod
    def policy_blocked(cls, reason: str) -> ProviderResult:
        return cls(ProviderStatus.POLICY_BLOCKED, None, None, reason)


class SportsDataProvider(Protocol):
    """Broad source interface; unsupported methods must return UNAVAILABLE."""

    def schedule(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult: ...

    def events(self, *, sport: str, **kwargs: Any) -> ProviderResult: ...

    def teams(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult: ...

    def players(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult: ...

    def rosters(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult: ...

    def boxscores(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult: ...

    def play_by_play(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult: ...

    def standings(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult: ...
