"""Rights and source audit for the blocked historical tennis foundation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

import polars as pl

from .policy import HISTORICAL_SOURCE_POLICY, HistoricalSourcePolicy


@dataclass(frozen=True)
class TennisRightsAudit:
    provider: str
    audit_status: str
    commercial_use_status: str
    production_allowed: bool
    primary_source_status: str
    attribution_required: bool
    share_alike_required: bool
    license_id: str
    normalized_rows_checked: int
    violations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def audit_tennis_rights(
    tables: Mapping[str, pl.DataFrame] | None = None,
    *,
    policy: HistoricalSourcePolicy = HISTORICAL_SOURCE_POLICY,
) -> TennisRightsAudit:
    """Report the deny-by-default rights state and reject metadata drift."""
    expected = policy.rights_metadata()
    rights_columns = tuple(expected)
    violations: list[str] = []
    rows_checked = 0
    for table_name, frame in (tables or {}).items():
        missing = set(rights_columns) - set(frame.columns)
        if missing:
            violations.append(f"{table_name}: missing rights columns {sorted(missing)}")
            continue
        rows_checked += frame.height
        for column, expected_value in expected.items():
            values = frame[column].unique().to_list()
            if values != [expected_value]:
                violations.append(
                    f"{table_name}: {column}={values!r}, expected {[expected_value]!r}"
                )
    return TennisRightsAudit(
        provider=policy.provider,
        audit_status="INVALID" if violations else "BLOCKED_NONCOMMERCIAL_SOURCE",
        commercial_use_status=str(expected["commercial_use_status"]),
        production_allowed=bool(expected["production_allowed"]),
        primary_source_status=str(expected["primary_source_status"]),
        attribution_required=bool(expected["attribution_required"]),
        share_alike_required=bool(expected["share_alike_required"]),
        license_id=str(expected["license_id"]),
        normalized_rows_checked=rows_checked,
        violations=tuple(violations),
    )


__all__ = ["TennisRightsAudit", "audit_tennis_rights"]
