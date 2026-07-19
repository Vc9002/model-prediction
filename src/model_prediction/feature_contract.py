"""Point-in-time feature contract (model_improvements.md section 3).

Every source observation feeding a model must carry the fields below. A
decision at time T may use only observations with ``observed_at_utc <= T`` --
corrections published after T remain excluded even if their
``effective_at_utc`` is earlier. This module is the single place that
enforces the contract's shape and the point-in-time usability rule;
individual feature modules should build ``FeatureObservation`` values instead
of ad hoc dicts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from .domain import parse_utc

REQUIRED_FIELDS = (
    "event_id",
    "entity_id",
    "feature_name",
    "value",
    "effective_at_utc",
    "observed_at_utc",
    "source",
    "source_version",
    "available",
    "missing_reason",
)

# model_improvements.md's stated reasons for a value being unavailable.
MISSING_REASONS = frozenset({"unknown", "not_published", "stale", "source_failure", "not_applicable"})


@dataclass(frozen=True)
class FeatureObservation:
    """One point-in-time-safe observation of a single feature value."""

    event_id: str
    entity_id: str
    feature_name: str
    value: object
    effective_at_utc: str
    observed_at_utc: str
    source: str
    source_version: str
    available: bool = True
    missing_reason: str | None = None
    snapshot_hash: str = ""

    def __post_init__(self) -> None:
        if not self.available and self.missing_reason not in MISSING_REASONS:
            raise ValueError(
                f"missing_reason must be one of {sorted(MISSING_REASONS)} when available=False, "
                f"got {self.missing_reason!r}"
            )
        if self.available and self.missing_reason is not None:
            raise ValueError("missing_reason must be None when available=True")
        # parse_utc raises on a naive/malformed timestamp -- fail closed.
        parse_utc(self.effective_at_utc)
        parse_utc(self.observed_at_utc)
        if not self.snapshot_hash:
            object.__setattr__(self, "snapshot_hash", self._compute_hash())

    def _compute_hash(self) -> str:
        canonical = json.dumps(
            {
                "event_id": self.event_id,
                "entity_id": self.entity_id,
                "feature_name": self.feature_name,
                "value": self.value,
                "effective_at_utc": self.effective_at_utc,
                "observed_at_utc": self.observed_at_utc,
                "source": self.source,
                "source_version": self.source_version,
                "available": self.available,
                "missing_reason": self.missing_reason,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def is_usable_at(self, decision_time: datetime | str) -> bool:
        """The point-in-time rule: usable only if observed at or before the decision time."""
        cutoff = parse_utc(decision_time) if isinstance(decision_time, str) else decision_time
        return self.available and parse_utc(self.observed_at_utc) <= cutoff


def validate_observation(observation: FeatureObservation | dict) -> list[str]:
    """Return contract violations for an observation; empty list means valid.

    Accepts a ``FeatureObservation`` (already validated by construction) or a
    raw dict, so legacy/external data can be checked before being wrapped.
    """
    if isinstance(observation, FeatureObservation):
        return []
    violations = []
    for field_name in REQUIRED_FIELDS:
        if field_name not in observation:
            violations.append(f"missing required field: {field_name}")
    if violations:
        return violations
    try:
        FeatureObservation(
            **{key: observation[key] for key in REQUIRED_FIELDS},
            snapshot_hash=observation.get("snapshot_hash", ""),
        )
    except (ValueError, TypeError) as error:
        violations.append(str(error))
    return violations


def filter_usable(
    observations: list[FeatureObservation], decision_time: datetime | str
) -> list[FeatureObservation]:
    """Keep only observations usable at ``decision_time`` -- the single chokepoint
    equivalent to ``features.base.games_before`` but for individual feature values.
    """
    return [obs for obs in observations if obs.is_usable_at(decision_time)]
