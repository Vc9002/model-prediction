"""Immutable byte cache and append-only observation manifests.

Consolidated from wnba-v1's cache.py (content-addressed `store_blob()` with a
collision guard that rejects a second, different payload silently
overwriting an existing blob) and mlb-v3's cache.py (`record_parse_result()`,
tracking parser-version evidence separately from the raw HTTP observation,
since a schema can drift between parser versions independent of whether the
underlying bytes changed). `latest()` reconstructs the full rights-aware
`SourceResponseMetadata` shape from `base.py`.
"""

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

    @classmethod
    def _write_once(cls, path: Path, body: bytes, *, label: str) -> None:
        if path.exists():
            existing = path.read_bytes()
            if existing != body:
                raise ValueError(f"conflicting immutable provider {label}: {path}")
            return
        cls._atomic_write(path, body)

    def store_blob(self, metadata: SourceResponseMetadata, body: bytes) -> Path:
        """Persist and verify exact response bytes before any parser runs."""
        actual_hash = hashlib.sha256(body).hexdigest()
        if actual_hash != metadata.content_hash:
            raise ValueError("provider content hash does not match raw response bytes")
        key = cache_key(metadata.endpoint_family, metadata.requested_parameters)
        root = self._request_root(metadata.provider, metadata.sport, metadata.endpoint_family, key)
        body_path = root / "blobs" / f"{actual_hash}.bin"
        self._write_once(body_path, body, label="blob")
        # A same-name blob is content-addressed; verify the existing bytes as
        # well instead of trusting mere path existence.
        if hashlib.sha256(body_path.read_bytes()).hexdigest() != actual_hash:
            raise ValueError(f"cached provider body failed SHA256 verification: {body_path}")
        return body_path

    def store(self, metadata: SourceResponseMetadata, body: bytes) -> CachedResponse:
        key = cache_key(metadata.endpoint_family, metadata.requested_parameters)
        root = self._request_root(metadata.provider, metadata.sport, metadata.endpoint_family, key)
        body_path = self.store_blob(metadata, body)

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
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        self._write_once(manifest_path, manifest_bytes, label="observation manifest")
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

        Raw bytes must exist before parsing. The resulting schema belongs to a
        particular parser version, not to the HTTP observation itself, so it is
        recorded in a separate content-addressed manifest -- a parser upgrade
        (schema drift) is then visible as new evidence, not a silent overwrite
        of what the bytes originally parsed to.
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

    def _load_observations(
        self, provider: str, sport: str, endpoint: str, parameters: dict[str, Any]
    ) -> list[tuple[dict[str, Any], Path]]:
        key = cache_key(endpoint, parameters)
        root = self._request_root(provider, sport, endpoint, key)
        manifests = sorted((root / "observations").glob("*.json"))
        return [(json.loads(path.read_text()), path) for path in manifests]

    def _reconstruct(self, raw: dict[str, Any], manifest_path: Path) -> CachedResponse:
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
            source_asset=raw.get("source_asset"),
            provider_chain=raw.get("provider_chain"),
            license_id=raw.get("license_id"),
            license_url=raw.get("license_url"),
            attribution_required=bool(raw.get("attribution_required", False)),
            attribution_text=raw.get("attribution_text"),
            subscription_required=bool(raw.get("subscription_required", False)),
            subscription_scope=raw.get("subscription_scope", "none"),
            upstream_rights_status=raw.get("upstream_rights_status", "unresolved"),
            commercial_use_status=raw.get("commercial_use_status", "unresolved"),
            use_scope=raw.get("use_scope", "research_shadow_only"),
            production_allowed=bool(raw.get("production_allowed", False)),
            from_cache=True,
        )
        return CachedResponse(metadata, self.root / raw["body_path"], manifest_path)

    def latest(
        self, provider: str, sport: str, endpoint: str, parameters: dict[str, Any]
    ) -> CachedResponse | None:
        """Most recent observation, success or failure -- full history for audit/forensics.

        Provider fetch paths deciding whether to skip the network should use
        `latest_success()` instead: a failed observation is real evidence
        worth keeping, but it must never become a permanently-reusable
        "successful" response that blocks every future retry.
        """
        records = self._load_observations(provider, sport, endpoint, parameters)
        if not records:
            return None
        raw, manifest_path = max(records, key=lambda item: item[0]["retrieved_at_utc"])
        return self._reconstruct(raw, manifest_path)

    def latest_success(
        self,
        provider: str,
        sport: str,
        endpoint: str,
        parameters: dict[str, Any],
        *,
        accepted_statuses: frozenset[int] = frozenset({200}),
    ) -> CachedResponse | None:
        """Most recent observation that actually succeeded, ignoring cached failures.

        A cached 429/500/502/503/504 (or any other non-2xx) is real evidence
        (kept by `latest()`), but it is not reusable content -- returning
        `None` here when only failures are on record means the caller falls
        through to a fresh network attempt instead of treating a transient
        upstream outage as a sticky, permanent one.
        """
        records = self._load_observations(provider, sport, endpoint, parameters)
        successful = [item for item in records if item[0].get("http_status") in accepted_statuses]
        if not successful:
            return None
        raw, manifest_path = max(successful, key=lambda item: item[0]["retrieved_at_utc"])
        return self._reconstruct(raw, manifest_path)
