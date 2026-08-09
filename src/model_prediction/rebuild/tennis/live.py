"""Provider-neutral, fail-closed validation for live tennis events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import polars as pl

from model_prediction.rebuild.providers.base import ProviderResult, ProviderStatus, SourceResponseMetadata


class LiveRejectReason(StrEnum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_STALE = "SOURCE_STALE"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    NO_SCHEDULED_EVENTS = "NO_SCHEDULED_EVENTS"
    POST_START = "POST_START"
    EVENT_AMBIGUOUS = "EVENT_AMBIGUOUS"
    PLAYER_UNRESOLVED = "PLAYER_UNRESOLVED"
    PLAYER_MAPPING_AMBIGUOUS = "PLAYER_MAPPING_AMBIGUOUS"
    NOT_SINGLES = "NOT_SINGLES"
    SURFACE_UNKNOWN = "SURFACE_UNKNOWN"


@dataclass(frozen=True)
class TennisLivePolicy:
    max_age_seconds: int = 300
    scheduled_statuses: frozenset[str] = frozenset({"SCHEDULED", "PRE_MATCH"})
    surfaces: frozenset[str] = frozenset({"HARD", "CLAY", "GRASS", "CARPET"})

    def __post_init__(self) -> None:
        if self.max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")


@dataclass(frozen=True)
class RejectedLiveEvent:
    provider_event_id: str | None
    reason: LiveRejectReason
    detail: str


@dataclass(frozen=True)
class TennisLiveResult:
    status: ProviderStatus
    metadata: SourceResponseMetadata | None
    frame: pl.DataFrame | None = None
    reason: LiveRejectReason | None = None
    rejected: tuple[RejectedLiveEvent, ...] = field(default_factory=tuple)

    @property
    def available(self) -> bool:
        return self.status is ProviderStatus.AVAILABLE and self.frame is not None and not self.frame.is_empty()


_REQUIRED_COLUMNS = {
    "provider_event_id", "tour", "event_start_utc", "status", "discipline", "surface",
    "player_a_id", "player_b_id", "player_a_canonical_id", "player_b_canonical_id",
}


def _parse_aware(value: object, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timezone-naive {field_name}")
    return parsed.astimezone(UTC)


def validate_live_events(
    result: ProviderResult,
    *,
    decision_time_utc: datetime,
    policy: TennisLivePolicy | None = None,
) -> TennisLiveResult:
    """Validate any provider's frame using one tennis-specific contract."""
    policy = policy or TennisLivePolicy()
    if decision_time_utc.tzinfo is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    decision = decision_time_utc.astimezone(UTC)
    if result.status is ProviderStatus.STALE:
        return TennisLiveResult(ProviderStatus.STALE, result.metadata, reason=LiveRejectReason.SOURCE_STALE)
    if result.status is not ProviderStatus.AVAILABLE or result.frame is None or result.metadata is None:
        return TennisLiveResult(
            ProviderStatus.UNAVAILABLE,
            result.metadata,
            reason=LiveRejectReason.SOURCE_UNAVAILABLE,
        )
    if result.metadata.sport != "tennis":
        return TennisLiveResult(
            ProviderStatus.DEGRADED,
            result.metadata,
            reason=LiveRejectReason.SCHEMA_DRIFT,
            rejected=(RejectedLiveEvent(None, LiveRejectReason.SCHEMA_DRIFT, "provider sport is not tennis"),),
        )
    try:
        observed = _parse_aware(result.metadata.observed_at_utc, "observed_at_utc")
    except ValueError as exc:
        return TennisLiveResult(
            ProviderStatus.DEGRADED,
            result.metadata,
            reason=LiveRejectReason.SCHEMA_DRIFT,
            rejected=(RejectedLiveEvent(None, LiveRejectReason.SCHEMA_DRIFT, str(exc)),),
        )
    age_seconds = (decision - observed).total_seconds()
    if age_seconds < 0:
        return TennisLiveResult(
            ProviderStatus.DEGRADED,
            result.metadata,
            reason=LiveRejectReason.SCHEMA_DRIFT,
            rejected=(RejectedLiveEvent(None, LiveRejectReason.SCHEMA_DRIFT, "observation is after decision"),),
        )
    if age_seconds > policy.max_age_seconds:
        return TennisLiveResult(ProviderStatus.STALE, result.metadata, reason=LiveRejectReason.SOURCE_STALE)
    missing = _REQUIRED_COLUMNS - set(result.frame.columns)
    if missing:
        return TennisLiveResult(
            ProviderStatus.DEGRADED,
            result.metadata,
            reason=LiveRejectReason.SCHEMA_DRIFT,
            rejected=(
                RejectedLiveEvent(None, LiveRejectReason.SCHEMA_DRIFT, f"missing columns: {sorted(missing)}"),
            ),
        )
    if result.frame.is_empty():
        return TennisLiveResult(
            ProviderStatus.UNAVAILABLE,
            result.metadata,
            reason=LiveRejectReason.NO_SCHEDULED_EVENTS,
        )

    event_ids = [str(row.get("provider_event_id") or "") for row in result.frame.iter_rows(named=True)]
    duplicate_event_ids = {event_id for event_id in event_ids if event_id and event_ids.count(event_id) > 1}
    accepted: list[dict[str, object]] = []
    rejected: list[RejectedLiveEvent] = []
    for row in result.frame.iter_rows(named=True):
        event_id = str(row.get("provider_event_id") or "") or None
        reason, detail = _validate_event(row, decision, policy, duplicate_event_ids)
        if reason is not None:
            rejected.append(RejectedLiveEvent(event_id, reason, detail))
            continue
        assert event_id is not None
        accepted.append(row)

    if not accepted:
        reason = rejected[0].reason if rejected else LiveRejectReason.NO_SCHEDULED_EVENTS
        return TennisLiveResult(ProviderStatus.UNAVAILABLE, result.metadata, reason=reason, rejected=tuple(rejected))
    return TennisLiveResult(
        ProviderStatus.AVAILABLE,
        result.metadata,
        pl.DataFrame(accepted),
        rejected=tuple(rejected),
    )


def _validate_event(
    row: dict[str, object],
    decision: datetime,
    policy: TennisLivePolicy,
    duplicate_event_ids: set[str],
) -> tuple[LiveRejectReason | None, str]:
    event_id = str(row.get("provider_event_id") or "")
    if not event_id or event_id in duplicate_event_ids:
        return LiveRejectReason.EVENT_AMBIGUOUS, "missing or duplicate provider event ID"
    if str(row.get("tour") or "").upper() not in {"ATP", "WTA"}:
        return LiveRejectReason.EVENT_AMBIGUOUS, "unknown tour"
    if str(row.get("discipline") or "").upper() != "SINGLES":
        return LiveRejectReason.NOT_SINGLES, "discipline is not explicit singles"
    if str(row.get("status") or "").upper() not in policy.scheduled_statuses:
        return LiveRejectReason.POST_START, "event is not in a pre-match state"
    try:
        event_start = _parse_aware(row.get("event_start_utc"), "event_start_utc")
    except ValueError as exc:
        return LiveRejectReason.SCHEMA_DRIFT, str(exc)
    if event_start <= decision:
        return LiveRejectReason.POST_START, "event has started"
    surface = str(row.get("surface") or "").upper()
    if surface not in policy.surfaces:
        return LiveRejectReason.SURFACE_UNKNOWN, "surface is missing or unsupported"
    player_a = str(row.get("player_a_id") or "")
    player_b = str(row.get("player_b_id") or "")
    if not player_a or not player_b or player_a == player_b:
        return LiveRejectReason.PLAYER_UNRESOLVED, "provider player IDs are missing or not distinct"
    canonical_a = str(row.get("player_a_canonical_id") or "")
    canonical_b = str(row.get("player_b_canonical_id") or "")
    if not canonical_a or not canonical_b:
        return LiveRejectReason.PLAYER_UNRESOLVED, "canonical player mapping is missing"
    if canonical_a == canonical_b:
        return LiveRejectReason.PLAYER_MAPPING_AMBIGUOUS, "both participants map to one canonical player"
    return None, ""


__all__ = [
    "LiveRejectReason", "RejectedLiveEvent", "TennisLivePolicy", "TennisLiveResult",
    "validate_live_events",
]
