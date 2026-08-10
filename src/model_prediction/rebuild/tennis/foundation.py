"""Raw-first tennis backfill (TennisMyLife) and current-event capture (ESPN)
orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from model_prediction.rebuild.providers.base import ProviderStatus
from model_prediction.rebuild.providers.tennis_espn import ESPNTennisProvider
from model_prediction.rebuild.providers.tennis_espn import Tour as ESPNTour
from model_prediction.rebuild.providers.tennis_mylife import MatchKind, TennisMyLifeProvider, Tour

from .normalize import normalize_espn_scoreboard, normalize_tennismylife_matches
from .store import TennisNormalizedStore


class TennisFoundation:
    def __init__(
        self,
        normalized_root: str | Path,
        *,
        tennis_mylife: TennisMyLifeProvider | None = None,
        espn: ESPNTennisProvider | None = None,
    ) -> None:
        self.store = TennisNormalizedStore(normalized_root)
        self.tennis_mylife = tennis_mylife
        self.espn = espn

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an immutable manifest, or verify and reuse the prior one."""
        if path.exists():
            existing = json.loads(path.read_text())
            comparable_existing = {key: value for key, value in existing.items() if key != "generated_at"}
            comparable_payload = {key: value for key, value in payload.items() if key != "generated_at"}
            if comparable_existing != comparable_payload:
                raise ValueError(f"conflicting immutable tennis dataset manifest: {path}")
            return existing
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
        return payload

    def backfill_matches(
        self,
        tour: Tour,
        years: Iterable[int],
        *,
        kind: MatchKind = "main",
        force: bool = False,
    ) -> dict[str, Any]:
        if self.tennis_mylife is None:
            raise RuntimeError("TennisMyLife provider is not configured")
        reports: list[dict[str, Any]] = []
        for year in years:
            result = self.tennis_mylife.year_matches(tour, year, kind=kind, force=force)
            if result.status is not ProviderStatus.AVAILABLE or result.frame is None or result.metadata is None:
                reports.append(
                    {"tour": tour, "year": year, "kind": kind, "status": result.status.value, "reason": result.reason}
                )
                continue
            normalized = normalize_tennismylife_matches(result.frame, result.metadata, tour=tour, match_kind=kind)
            self.store.write_matches(normalized)
            manifest = {
                "sport": "tennis",
                "tour": tour,
                "year": year,
                "kind": kind,
                "source_hash": result.metadata.content_hash,
                "schema_hash": result.metadata.schema_hash,
                "rows": normalized.height,
                "generated_at": datetime.now(UTC).isoformat(),
                "availability_basis": "capture_time_only",
            }
            dataset_hash = hashlib.sha256(
                json.dumps({k: v for k, v in manifest.items() if k != "generated_at"}, sort_keys=True).encode()
            ).hexdigest()
            manifest["dataset_hash"] = dataset_hash
            manifest_path = (
                self.store.root / "tennis" / "_manifests" / f"tour={tour}" / f"kind={kind}"
                / f"year={year}" / f"{dataset_hash}.json"
            )
            manifest = self._atomic_json(manifest_path, manifest)
            reports.append({**manifest, "status": "AVAILABLE", "manifest": str(manifest_path)})
        return {"sport": "tennis", "tour": tour, "kind": kind, "seasons": reports}

    def collect_current(self, tour: Literal["atp", "wta", "challenger"], *, force: bool = False) -> dict[str, Any]:
        if self.espn is None:
            raise RuntimeError("ESPN tennis provider is not configured")
        espn_tour: ESPNTour = "atp" if tour == "challenger" else tour
        result = self.espn.scoreboard(espn_tour, force=force)
        if result.status is not ProviderStatus.AVAILABLE or result.frame is None or result.metadata is None:
            return {"sport": "tennis", "tour": tour, "status": result.status.value, "reason": result.reason}
        normalized = normalize_espn_scoreboard(result.frame, result.metadata)
        self.store.write_current_events(normalized)
        return {
            "sport": "tennis",
            "tour": tour,
            "status": "AVAILABLE",
            "rows": normalized.height,
            "source_hash": result.metadata.content_hash,
            "observed_at_utc": result.metadata.observed_at_utc,
        }

    def audit(self, *, tour: str | None = None) -> dict[str, Any]:
        from .audit import audit_tennis_data

        return audit_tennis_data(self.store, tour=tour)
