"""Immutable local-snapshot manifest contract for historical tennis data."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .policy import (
    HISTORICAL_SOURCE_POLICY,
    CommercialUseStatus,
    HistoricalSourcePolicy,
    PrimarySourceStatus,
    TennisDataUse,
    TennisSourcePolicyError,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class SnapshotFile:
    relative_path: str
    sha256: str
    byte_size: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SnapshotFile:
        return cls(
            relative_path=str(payload["relative_path"]),
            sha256=str(payload["sha256"]).lower(),
            byte_size=int(payload["byte_size"]),
        )

    def validate(self) -> None:
        path = Path(self.relative_path)
        if path.is_absolute() or ".." in path.parts or not self.relative_path:
            raise ValueError("snapshot file path must stay within the approved local root")
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError(f"invalid SHA-256 for {self.relative_path}")
        if self.byte_size < 0:
            raise ValueError(f"negative byte size for {self.relative_path}")


@dataclass(frozen=True)
class TennisSnapshotManifest:
    provider: str
    tour: str
    source_repository_url: str
    source_revision: str
    retrieved_at_utc: str
    license_id: str
    attribution: str
    commercial_use_status: str
    production_allowed: bool
    primary_source_status: str
    attribution_required: bool
    share_alike_required: bool
    availability_basis: str
    history_complete: bool
    files: tuple[SnapshotFile, ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TennisSnapshotManifest:
        expected = {
            "provider", "tour", "source_repository_url", "source_revision",
            "retrieved_at_utc", "license_id", "attribution", "commercial_use_status",
            "production_allowed", "primary_source_status", "attribution_required",
            "share_alike_required", "availability_basis", "history_complete", "files",
        }
        unknown = set(payload) - expected
        missing = expected - set(payload)
        if unknown or missing:
            raise ValueError(f"invalid tennis manifest fields: missing={sorted(missing)} unknown={sorted(unknown)}")
        for field in (
            "production_allowed", "attribution_required", "share_alike_required", "history_complete",
        ):
            if type(payload[field]) is not bool:
                raise ValueError(f"{field} must be an explicit boolean")
        manifest = cls(
            provider=str(payload["provider"]),
            tour=str(payload["tour"]).upper(),
            source_repository_url=str(payload["source_repository_url"]),
            source_revision=str(payload["source_revision"]).lower(),
            retrieved_at_utc=str(payload["retrieved_at_utc"]),
            license_id=str(payload["license_id"]),
            attribution=str(payload["attribution"]),
            commercial_use_status=str(payload["commercial_use_status"]),
            production_allowed=payload["production_allowed"],
            primary_source_status=str(payload["primary_source_status"]),
            attribution_required=payload["attribution_required"],
            share_alike_required=payload["share_alike_required"],
            availability_basis=str(payload["availability_basis"]),
            history_complete=bool(payload["history_complete"]),
            files=tuple(SnapshotFile.from_dict(item) for item in payload["files"]),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.provider != "jeff_sackmann":
            raise ValueError("unsupported historical tennis provider")
        if self.tour not in {"ATP", "WTA"}:
            raise ValueError("tour must be ATP or WTA")
        if self.source_repository_url not in HISTORICAL_SOURCE_POLICY.former_primary_urls:
            raise ValueError("mirror or unknown repository cannot substitute for the primary source")
        if not _GIT_SHA_RE.fullmatch(self.source_revision):
            raise ValueError("source_revision must be a full 40-character Git commit SHA")
        observed = datetime.fromisoformat(self.retrieved_at_utc)
        if observed.tzinfo is None:
            raise ValueError("retrieved_at_utc must be timezone-aware")
        if self.license_id != HISTORICAL_SOURCE_POLICY.license_id:
            raise ValueError("manifest license does not match the documented source policy")
        expected_rights = HISTORICAL_SOURCE_POLICY.rights_metadata()
        actual_rights = {
            "commercial_use_status": self.commercial_use_status,
            "production_allowed": self.production_allowed,
            "primary_source_status": self.primary_source_status,
            "attribution_required": self.attribution_required,
            "share_alike_required": self.share_alike_required,
            "license_id": self.license_id,
        }
        if actual_rights != expected_rights:
            raise ValueError("manifest rights metadata does not match the fail-closed source policy")
        if self.commercial_use_status != CommercialUseStatus.PROHIBITED.value:
            raise ValueError("historical tennis commercial use must remain prohibited")
        if self.production_allowed:
            raise ValueError("historical tennis data is not allowed in production")
        if self.primary_source_status != PrimarySourceStatus.UNAVAILABLE.value:
            raise ValueError("former primary source must remain explicitly unavailable")
        if not self.attribution.strip():
            raise ValueError("source attribution is required")
        if self.availability_basis != "capture_time_only":
            raise ValueError(
                "only capture_time_only is supported; no original-history verifier exists"
            )
        if self.history_complete:
            raise ValueError("history_complete cannot be claimed without an original-history verifier")
        if not self.files:
            raise ValueError("snapshot manifest contains no files")
        for item in self.files:
            item.validate()
        paths = [item.relative_path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("snapshot manifest contains duplicate file paths")


def load_snapshot_manifest(path: str | Path) -> TennisSnapshotManifest:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    payload = json.loads(manifest_path.read_text())
    if not isinstance(payload, dict):
        raise TypeError("tennis snapshot manifest must be a JSON object")
    return TennisSnapshotManifest.from_dict(payload)


def verify_local_snapshot(
    root: str | Path,
    manifest: TennisSnapshotManifest,
    *,
    policy: HistoricalSourcePolicy = HISTORICAL_SOURCE_POLICY,
    intended_use: TennisDataUse | str | None = TennisDataUse.RESEARCH,
) -> None:
    """Verify an approved local snapshot without ever performing network I/O."""
    approved_root = policy.require_approved_local_root(root, intended_use=intended_use)
    if policy.provider != manifest.provider or policy.license_id != manifest.license_id:
        raise TennisSourcePolicyError("snapshot manifest does not match the approved source policy")
    for item in manifest.files:
        path = approved_root / item.relative_path
        if not path.is_file():
            raise FileNotFoundError(path)
        content = path.read_bytes()
        if len(content) != item.byte_size:
            raise ValueError(f"snapshot size mismatch for {item.relative_path}")
        digest = hashlib.sha256(content).hexdigest()
        if digest != item.sha256:
            raise ValueError(f"snapshot hash mismatch for {item.relative_path}")


__all__ = [
    "SnapshotFile", "TennisSnapshotManifest", "TennisSourcePolicyError",
    "load_snapshot_manifest", "verify_local_snapshot",
]
