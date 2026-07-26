from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from .domain import parse_utc


@dataclass(frozen=True)
class SourceRecord:
    observed_at_utc: str
    effective_at_utc: str
    source: str
    endpoint: str
    request_parameters: dict[str, Any]
    content_hash: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        observed_at_utc: str,
        effective_at_utc: str,
        source: str,
        endpoint: str,
        request_parameters: dict[str, Any],
        payload: dict[str, Any],
    ) -> SourceRecord:
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return cls(observed_at_utc, effective_at_utc, source, endpoint, request_parameters, digest, payload)


def records_available_at(records: Iterable[SourceRecord], decision_at_utc: str) -> list[SourceRecord]:
    decision = parse_utc(decision_at_utc)
    return [
        record
        for record in records
        if parse_utc(record.observed_at_utc) <= decision and parse_utc(record.effective_at_utc) <= decision
    ]


@dataclass(frozen=True)
class GameResearchPacket:
    event: dict[str, Any]
    decision_timestamp_utc: str
    canonical_teams: dict[str, str]
    market: dict[str, Any]
    decision_price: dict[str, Any]
    model_output: dict[str, Any]
    uncertainty: float | None
    feature_contributions: dict[str, float]
    freshness_state: str
    source_ids: tuple[str, ...]
    risk_flags: tuple[str, ...]
    ban_list_result: dict[str, Any]
    eligibility_result: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
