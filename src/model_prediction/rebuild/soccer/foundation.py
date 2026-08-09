"""Offline-testable orchestration for raw-first soccer match observations."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from model_prediction.rebuild.providers.base import ProviderResult, ProviderStatus
from model_prediction.rebuild.providers.football_data import FootballDataProvider
from model_prediction.rebuild.providers.soccer_espn import ESPNSoccerProvider
from model_prediction.rebuild.providers.statsbomb_open import StatsBombOpenDataProvider

from .audit import audit_soccer_data
from .normalize import normalize_soccer_matches
from .store import SoccerNormalizedStore


class SoccerFoundation:
    def __init__(
        self,
        espn: ESPNSoccerProvider,
        normalized_root: str | Path,
        *,
        football_data: FootballDataProvider | None = None,
        statsbomb: StatsBombOpenDataProvider | None = None,
    ) -> None:
        self.espn = espn
        self.football_data = football_data
        self.statsbomb = statsbomb or StatsBombOpenDataProvider()
        self.store = SoccerNormalizedStore(normalized_root)

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        if path.exists():
            if path.read_bytes() != body:
                raise ValueError(f"conflicting immutable soccer dataset manifest: {path}")
            return
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with tmp.open("wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def _persist(self, result: ProviderResult) -> int:
        if result.status is not ProviderStatus.AVAILABLE or result.frame is None or result.metadata is None:
            return 0
        normalized = normalize_soccer_matches(result.frame, result.metadata)
        self.store.write_matches(normalized)
        return normalized.height

    @staticmethod
    def _source_report(
        provider: str,
        competition: str | None,
        result: ProviderResult,
        rights: dict[str, Any],
        rows_written: int,
    ) -> dict[str, Any]:
        metadata = result.metadata.to_dict() if result.metadata is not None else {}
        report = {
            "provider": provider,
            "competition": competition,
            "status": result.status.value,
            "reason": result.reason,
            "rows_written": rows_written,
            **rights,
        }
        for key in (
            "content_hash",
            "schema_hash",
            "source_version",
            "observed_at_utc",
            "retrieved_at_utc",
        ):
            report[key] = metadata.get(key)
        return report

    def _write_dataset_manifest(
        self, game_date: date, reports: list[dict[str, Any]]
    ) -> Path:
        material = {
            "sport": "soccer",
            "date": game_date.isoformat(),
            "sources": reports,
            "use_scope": "research_shadow_only",
            "economic_use_allowed": False,
            "production_allowed": False,
        }
        dataset_hash = hashlib.sha256(
            json.dumps(material, sort_keys=True).encode("utf-8")
        ).hexdigest()
        payload = {
            **material,
            "dataset_hash": dataset_hash,
            "observed_through_utc": max(
                (
                    str(report["retrieved_at_utc"])
                    for report in reports
                    if report.get("retrieved_at_utc")
                ),
                default=None,
            ),
        }
        path = (
            self.store.root
            / "soccer"
            / "_manifests"
            / f"date={game_date.isoformat()}"
            / f"{dataset_hash}.json"
        )
        self._atomic_json(path, payload)
        return path

    def collect_date(
        self,
        game_date: date,
        *,
        espn_leagues: tuple[str, ...] = ("eng.1",),
        football_data_competitions: tuple[str, ...] = (),
        force: bool = False,
    ) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        for league in espn_leagues:
            result = self.espn.current_schedule(game_date, league, force=force)
            reports.append(
                self._source_report(
                    self.espn.provider_id,
                    league,
                    result,
                    self.espn.rights.to_dict(),
                    self._persist(result),
                )
            )
        for competition in football_data_competitions:
            if self.football_data is None:
                reports.append(
                    {
                        "provider": "football_data_v4",
                        "competition": competition,
                        "status": ProviderStatus.UNAVAILABLE.value,
                        "reason": "OPTIONAL_PROVIDER_NOT_CONFIGURED",
                        "rows_written": 0,
                        **FootballDataProvider.rights.to_dict(),
                    }
                )
                continue
            result = self.football_data.matches(competition, game_date, game_date, force=force)
            reports.append(
                self._source_report(
                    self.football_data.provider_id,
                    competition,
                    result,
                    self.football_data.rights.to_dict(),
                    self._persist(result),
                )
            )
        blocked = self.statsbomb.events(sport="soccer")
        reports.append(
            {
                "provider": self.statsbomb.provider_id,
                "competition": None,
                "status": blocked.status.value,
                "reason": blocked.reason,
                "rows_written": 0,
                **self.statsbomb.rights.to_dict(),
            }
        )
        manifest = self._write_dataset_manifest(game_date, reports)
        return {
            "sport": "soccer",
            "date": game_date.isoformat(),
            "sources": reports,
            "manifest": str(manifest),
            "use_scope": "research_shadow_only",
            "economic_use_allowed": False,
            "production_allowed": False,
        }

    def audit(self) -> dict[str, Any]:
        return audit_soccer_data(self.store)
