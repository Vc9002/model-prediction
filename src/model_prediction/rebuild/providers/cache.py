"""Immutable byte cache and append-only observation manifests."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import SourceGrade, SourceResponseMetadata, canonical_json


def cache_key(endpoint: str, parameters: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json({"endpoint": endpoint, "parameters": parameters})).hexdigest()


@dataclass(frozen=True)
class CachedResponse:
    metadata: SourceResponseMetadata
    body_path: Path
    manifest_path: Path

    def read_bytes(self) -> bytes:
        body = self.body_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != self.metadata.content_hash:
            raise ValueError(f"cached provider body failed SHA256 verification: {self.body_path}")
        return body


class ProviderRawCache:
    """Store one content-addressed blob and every distinct observation of it.

    Identical bytes are deduplicated, but retrieval manifests are never
    deduplicated: observing the same asset again later is separate evidence.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _request_root(self, provider: str, sport: str, endpoint: str, key: str) -> Path:
        return self.root / provider / sport / endpoint / key

    @staticmethod
    def _atomic_write(path: Path, body: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with tmp.open("wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def store(self, metadata: SourceResponseMetadata, body: bytes) -> CachedResponse:
        actual_hash = hashlib.sha256(body).hexdigest()
        if actual_hash != metadata.content_hash:
            raise ValueError("provider content hash does not match raw response bytes")
        key = cache_key(metadata.endpoint_family, metadata.requested_parameters)
        root = self._request_root(metadata.provider, metadata.sport, metadata.endpoint_family, key)
        body_path = root / "blobs" / f"{actual_hash}.bin"
        if not body_path.exists():
            self._atomic_write(body_path, body)

        observation_id = hashlib.sha256(
            canonical_json({
                "retrieved_at_utc": metadata.retrieved_at_utc,
                "request_time_utc": metadata.request_time_utc,
                "content_hash": metadata.content_hash,
            })
        ).hexdigest()
        manifest_path = root / "observations" / f"{observation_id}.json"
        manifest = {
            **metadata.to_dict(),
            "cache_key": key,
            "body_path": str(body_path.relative_to(self.root)),
        }
        self._atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        return CachedResponse(metadata, body_path, manifest_path)

    def record_parse_result(
        self,
        metadata: SourceResponseMetadata,
        *,
        parser_version: str,
        status: str,
        schema_hash: str | None,
        reason: str | None = None,
    ) -> Path:
        """Append immutable parser evidence without mutating the raw observation.

        Raw bytes must exist before parsing.  The resulting schema belongs to a
        particular parser version, not to the HTTP observation itself, so it is
        recorded in a separate content-addressed manifest.
        """
        key = cache_key(metadata.endpoint_family, metadata.requested_parameters)
        root = self._request_root(metadata.provider, metadata.sport, metadata.endpoint_family, key)
        payload = {
            "content_hash": metadata.content_hash,
            "parser_version": parser_version,
            "schema_hash": schema_hash,
            "status": status,
            "reason": reason,
        }
        digest = hashlib.sha256(canonical_json(payload)).hexdigest()
        path = root / "parse_results" / f"{digest}.json"
        if not path.exists():
            self._atomic_write(path, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
        return path

    def latest(
        self, provider: str, sport: str, endpoint: str, parameters: dict[str, Any]
    ) -> CachedResponse | None:
        key = cache_key(endpoint, parameters)
        root = self._request_root(provider, sport, endpoint, key)
        manifests = sorted((root / "observations").glob("*.json"))
        if not manifests:
            return None
        records = [(json.loads(path.read_text()), path) for path in manifests]
        raw, manifest_path = max(records, key=lambda item: item[0]["retrieved_at_utc"])
        metadata = SourceResponseMetadata(
            provider=raw["provider"],
            sport=raw["sport"],
            endpoint_family=raw["endpoint_family"],
            requested_parameters=raw["requested_parameters"],
            request_time_utc=raw["request_time_utc"],
            retrieved_at_utc=raw["retrieved_at_utc"],
            observed_at_utc=raw["observed_at_utc"],
            http_status=raw["http_status"],
            content_hash=raw["content_hash"],
            schema_hash=raw.get("schema_hash"),
            source_event_id=raw.get("source_event_id"),
            content_type=raw.get("content_type"),
            source_version=raw.get("source_version"),
            source_grade=SourceGrade(raw["source_grade"]),
            from_cache=True,
            commercial_use_status=raw.get("commercial_use_status", "unresolved"),
            production_allowed=bool(raw.get("production_allowed", False)),
        )
        return CachedResponse(metadata, self.root / raw["body_path"], manifest_path)
