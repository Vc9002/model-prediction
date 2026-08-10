"""Hard read boundary protecting MLB v2 sealed prospective evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from model_prediction.runtime_paths import RuntimePaths


@dataclass(frozen=True)
class MLBV3DataBoundary:
    """Allow v3 research to read only its raw/normalized development roots.

    V2 prospective ledgers, shadow databases, consumption registries, and
    verification/economic outputs are not v3 development inputs.
    """

    repo_root: Path

    def assert_read_path(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        # rebuild_root resolves through RuntimePaths (MODEL_PREDICTION_RUNTIME_ROOT),
        # matching every other rebuild data_root computation -- not always
        # repo_root/data/rebuild, since PR #6/#7 moved mutable rebuild state
        # out of the Git checkout by default in production.
        rebuild_root = RuntimePaths.resolve(repo_root=self.repo_root).rebuild_root
        allowed = (
            (rebuild_root / "raw" / "mlb_stats" / "mlb").resolve(),
            (rebuild_root / "raw" / "baseball_savant" / "mlb").resolve(),
            (rebuild_root / "raw" / "open_meteo" / "mlb").resolve(),
            (rebuild_root / "normalized" / "mlb_v3").resolve(),
            (self.repo_root / "config" / "venues").resolve(),
        )
        if not any(resolved == root or root in resolved.parents for root in allowed):
            raise PermissionError(
                "MLB v3 research may not read prospective/v2 evidence or unapproved data roots"
            )
        return resolved


@dataclass(frozen=True)
class MLBV3GuardedRepository:
    """The only filesystem read gateway for MLB v3 research artifacts."""

    boundary: MLBV3DataBoundary

    def resolve(self, path: str | Path) -> Path:
        return self.boundary.assert_read_path(path)

    def read_bytes(self, path: str | Path) -> bytes:
        return self.resolve(path).read_bytes()

    def read_text(self, path: str | Path) -> str:
        return self.resolve(path).read_text()

    def read_json(self, path: str | Path) -> Any:
        return json.loads(self.read_text(path))
