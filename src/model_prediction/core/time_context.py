"""Point-in-Time (PIT) execution context and observation boundary enforcer.

Guarantees that no feature computation, model inference, or market valuation
incorporates data timestamped after the authorized decision boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from model_prediction.domain import parse_utc, utc_now


class PointInTimeLeakageError(ValueError):
    """Raised when an unobservable future data point is accessed past the decision boundary."""


@dataclass(frozen=True)
class TimeContext:
    """Immutable point-in-time evaluation context.

    Attributes:
        decision_time_utc: Authoritative timestamp of the decision boundary.
            No data observed after this point may be used.
        event_start_utc: Scheduled or observed start timestamp of the game/event.
    """

    decision_time_utc: datetime
    event_start_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.decision_time_utc.tzinfo is None:
            raise ValueError("decision_time_utc must be timezone-aware (UTC)")
        if self.event_start_utc is not None and self.event_start_utc.tzinfo is None:
            raise ValueError("event_start_utc must be timezone-aware (UTC)")

    @classmethod
    def now(cls, event_start_utc: datetime | str | None = None) -> TimeContext:
        """Create a TimeContext with the current UTC timestamp as decision boundary."""
        current = utc_now()
        start = parse_utc(event_start_utc) if isinstance(event_start_utc, str) else event_start_utc
        return cls(decision_time_utc=current, event_start_utc=start)

    @classmethod
    def of(
        cls,
        decision_time: datetime | str,
        event_start: datetime | str | None = None,
    ) -> TimeContext:
        """Create a deterministic TimeContext from explicit timestamps."""
        dec_dt = parse_utc(decision_time) if isinstance(decision_time, str) else decision_time
        start_dt = parse_utc(event_start) if isinstance(event_start, str) else event_start
        return cls(decision_time_utc=dec_dt, event_start_utc=start_dt)

    def is_observable(self, observed_at_utc: datetime | str | None) -> bool:
        """Check whether a data point was strictly observable at or before decision_time_utc."""
        if observed_at_utc is None:
            return False
        dt = parse_utc(observed_at_utc) if isinstance(observed_at_utc, str) else observed_at_utc
        return dt <= self.decision_time_utc

    def assert_observable(self, observed_at_utc: datetime | str | None, label: str = "") -> None:
        """Raise PointInTimeLeakageError if observed_at_utc is after decision_time_utc."""
        if observed_at_utc is None:
            raise PointInTimeLeakageError(
                f"Missing observation timestamp for {label or 'data point'}; cannot verify PIT validity."
            )
        dt = parse_utc(observed_at_utc) if isinstance(observed_at_utc, str) else observed_at_utc
        if dt > self.decision_time_utc:
            diff = (dt - self.decision_time_utc).total_seconds()
            raise PointInTimeLeakageError(
                f"PIT Leakage Violation ({label or 'feature'}): observed_at ({dt.isoformat()}) "
                f"> decision_time ({self.decision_time_utc.isoformat()}) by {diff:.2f}s"
            )

    def to_dict(self) -> dict[str, str | None]:
        """Serialize context to ISO 8601 strings."""
        return {
            "decision_time_utc": self.decision_time_utc.isoformat(),
            "event_start_utc": self.event_start_utc.isoformat() if self.event_start_utc else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimeContext:
        """Deserialize context from dictionary."""
        return cls.of(
            decision_time=data["decision_time_utc"],
            event_start=data.get("event_start_utc"),
        )
