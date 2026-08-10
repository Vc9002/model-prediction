"""NFL raw-first historical release capture orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.rebuild.providers.base import ProviderStatus
from model_prediction.rebuild.providers.nflverse import NFLVerseProvider

from .audit import audit_nfl_season
from .normalize import NORMALIZERS, normalize_nfl_table
from .store import NFLNormalizedStore


class NFLFoundation:
    def __init__(self, provider: NFLVerseProvider, normalized_root: str | Path) -> None:
        self.provider = provider
        self.store = NFLNormalizedStore(normalized_root)

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with tmp.open("w") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def backfill(
        self,
        seasons: Iterable[int],
        *,
        tables: Iterable[str] = ("schedule", "pbp", "weekly_rosters"),
        force: bool = False,
    ) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        for season in seasons:
            source_hashes: dict[str, str] = {}
            schema_hashes: dict[str, str | None] = {}
            row_counts: dict[str, int] = {}
            source_assets: dict[str, dict[str, Any]] = {}
            errors: dict[str, str] = {}
            for source_table in tables:
                if source_table not in NORMALIZERS:
                    errors[source_table] = "unsupported table"
                    continue
                result = self.provider.season_table(source_table, season, force=force)
                if (
                    result.status is not ProviderStatus.AVAILABLE
                    or result.frame is None
                    or result.metadata is None
                ):
                    errors[source_table] = result.reason or result.status.value
                    continue
                target, normalized = normalize_nfl_table(source_table, result.frame, result.metadata)
                self.store.write(target, season, normalized)
                source_hashes[source_table] = result.metadata.content_hash
                schema_hashes[source_table] = result.metadata.schema_hash
                row_counts[target] = normalized.height
                source_assets[source_table] = {
                    "source_version": result.metadata.source_version,
                    "license_id": result.metadata.license_id,
                    "license_url": result.metadata.license_url,
                    "attribution_required": result.metadata.attribution_required,
                    "attribution_text": result.metadata.attribution_text,
                    "upstream_rights_status": result.metadata.upstream_rights_status,
                    "commercial_use_status": result.metadata.commercial_use_status,
                    "production_allowed": result.metadata.production_allowed,
                }

            material = {
                "sport": "nfl",
                "season": season,
                "source_hashes": source_hashes,
                "schema_hashes": schema_hashes,
                "row_counts": row_counts,
                "source_assets": source_assets,
            }
            dataset_hash = hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()
            manifest = {
                **material,
                "generated_at": datetime.now(UTC).isoformat(),
                "rows": sum(row_counts.values()),
                "dataset_hash": dataset_hash,
                "availability_basis": "capture_time_only_mutable_release",
                "retrospective_pit_qualified": False,
                "production_allowed": False,
                "errors": errors,
            }
            manifest_path = (
                self.store.root / "nfl" / "_manifests" / f"season={season}" / f"{dataset_hash}.json"
            )
            self._atomic_json(manifest_path, manifest)
            reports.append({**manifest, "manifest": str(manifest_path)})
        return {"sport": "nfl", "seasons": reports}

    def audit(self, season: int) -> dict[str, Any]:
        return audit_nfl_season(self.store, season)
