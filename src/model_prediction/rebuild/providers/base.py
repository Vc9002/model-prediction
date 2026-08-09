"""Provider-neutral contracts for free/open rebuild data.

Provider failures are explicit states.  In particular, an empty DataFrame is
never used as a stand-in for unavailable data.
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


class SourceGrade(StrEnum):
    A = "A"  # direct structured provider
    B = "B"  # stable open/versioned dataset
    C = "C"  # public undocumented endpoint
    D = "D"  # derived or imputed


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

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_grade"] = self.source_grade.value
        return value


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
