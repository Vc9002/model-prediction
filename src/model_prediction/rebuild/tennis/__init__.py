"""Fail-closed tennis data foundation.

The historical source package intentionally has no downloader.  The former
Jeff Sackmann ATP/WTA repositories are unavailable and their CC BY-NC-SA 4.0
terms require an explicit policy decision before a local snapshot may be used.
"""

from .audit import TennisRightsAudit, audit_tennis_rights
from .policy import (
    HISTORICAL_SOURCE_POLICY,
    CommercialUseStatus,
    HistoricalSourcePolicy,
    PrimarySourceStatus,
    TennisDataUse,
)

__all__ = [
    "HISTORICAL_SOURCE_POLICY",
    "CommercialUseStatus",
    "HistoricalSourcePolicy",
    "PrimarySourceStatus",
    "TennisDataUse",
    "TennisRightsAudit",
    "audit_tennis_rights",
]
