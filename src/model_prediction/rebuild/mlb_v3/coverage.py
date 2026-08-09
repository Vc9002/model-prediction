"""Immutable normalized-coverage manifests for resumable MLB v3 backfills."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from model_prediction.rebuild.providers.base import canonical_json

from .boundary import MLBV3GuardedRepository


class StatcastCoverageStore:
    def __init__(self, normalized_root: Path, repository: MLBV3GuardedRepository) -> None:
        self.normalized_root = normalized_root
        self.root = normalized_root / "mlb_v3" / "_coverage" / "statcast"
        self.repository = repository
        self.repository.resolve(self.root)

    def _range_root(self, start: date, end: date) -> Path:
        return self.root / f"{start.isoformat()}_{end.isoformat()}"

    def record(self, start: date, end: date, payload: dict[str, Any]) -> Path:
        complete = {"start": start.isoformat(), "end": end.isoformat(), **payload}
        digest = hashlib.sha256(canonical_json(complete)).hexdigest()
        path = self._range_root(start, end) / f"{digest}.json"
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(json.dumps(complete, indent=2, sort_keys=True).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def latest_success(self, start: date, end: date) -> dict[str, Any] | None:
        manifests = sorted(self._range_root(start, end).glob("*.json"))
        successes: list[dict[str, Any]] = []
        for path in manifests:
            raw = json.loads(self.repository.read_text(path))
            if raw.get("status") != "AVAILABLE" or raw.get("http_status") != 200:
                continue
            valid_parts = True
            for relative in raw.get("normalized_parts", []):
                part = self.repository.resolve(self.normalized_root / relative)
                expected_hash = part.stem.removeprefix("part-")
                if not part.is_file() or hashlib.sha256(part.read_bytes()).hexdigest() != expected_hash:
                    valid_parts = False
                    break
            if valid_parts:
                successes.append(raw)
        return max(successes, key=lambda row: str(row.get("observed_at_utc", ""))) if successes else None
