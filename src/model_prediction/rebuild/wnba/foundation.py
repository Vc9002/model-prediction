"""WNBA raw-first historical backfill orchestration."""

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
from model_prediction.rebuild.providers.sportsdataverse import SportsDataverseProvider

from .audit import audit_wnba_season
from .normalize import NORMALIZERS, normalize_wnba_table
from .store import WNBANormalizedStore


class WNBAFoundation:
    def __init__(
        self,
        provider: SportsDataverseProvider,
        normalized_root: str | Path,
    ) -> None:
        self.provider = provider
        self.store = WNBANormalizedStore(normalized_root)

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
        tables: Iterable[str] = ("schedule", "team_box", "player_box", "rosters", "pbp"),
        force: bool = False,
    ) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        for season in seasons:
            source_hashes: dict[str, str] = {}
            schema_hashes: dict[str, str | None] = {}
            row_counts: dict[str, int] = {}
            errors: dict[str, str] = {}
            for source_table in tables:
                if source_table not in NORMALIZERS:
                    errors[source_table] = "unsupported table"
                    continue
                result = self.provider.season_table(source_table, season, force=force)
                if result.status is not ProviderStatus.AVAILABLE or result.frame is None or result.metadata is None:
                    errors[source_table] = result.reason or result.status.value
                    continue
                target, normalized = normalize_wnba_table(source_table, result.frame, result.metadata)
                self.store.write(target, season, normalized)
                source_hashes[source_table] = result.metadata.content_hash
                schema_hashes[source_table] = result.metadata.schema_hash
                row_counts[target] = normalized.height

            dataset_material = {
                "sport": "wnba",
                "season": season,
                "source_hashes": source_hashes,
                "schema_hashes": schema_hashes,
                "row_counts": row_counts,
            }
            dataset_hash = hashlib.sha256(
                json.dumps(dataset_material, sort_keys=True).encode("utf-8")
            ).hexdigest()
            manifest = {
                **dataset_material,
                "generated_at": datetime.now(UTC).isoformat(),
                "start": f"{season}-01-01",
                "end": f"{season}-12-31",
                "rows": sum(row_counts.values()),
                "event_count": (
                    self.store.read_season("games", season).select("event_id").n_unique()
                    if self.store.partition_dir("games", season).exists()
                    else 0
                ),
                "feature_schema_hash": None,
                "dataset_hash": dataset_hash,
                "availability_basis": "capture_time_only",
                "errors": errors,
            }
            manifest_path = (
                self.store.root / "wnba" / "_manifests" / f"season={season}" / f"{dataset_hash}.json"
            )
            self._atomic_json(manifest_path, manifest)
            reports.append({**manifest, "manifest": str(manifest_path)})
        return {"sport": "wnba", "seasons": reports}

    def audit(self, season: int) -> dict[str, Any]:
        return audit_wnba_season(self.store, season)
