"""Immutable content-addressed normalized Parquet storage for MLB v3."""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from pathlib import Path
from typing import Any

import polars as pl

from model_prediction.rebuild.providers.base import DataUseContext, assert_frame_use_allowed, canonical_json
from model_prediction.rebuild.schemas import validate_or_raise

from .boundary import MLBV3GuardedRepository
from .contracts import MLB_V3_CONTRACTS, PRIMARY_KEYS


def _conflicting_primary_keys(frame: pl.DataFrame, keys: list[str]) -> list[tuple[Any, ...]]:
    signatures: dict[tuple[Any, ...], bytes] = {}
    conflicts: set[tuple[Any, ...]] = set()
    for row in frame.iter_rows(named=True):
        key = tuple(row[name] for name in keys)
        signature = canonical_json({name: row[name] for name in sorted(row) if name not in keys})
        prior = signatures.setdefault(key, signature)
        if prior != signature:
            conflicts.add(key)
    return sorted(conflicts, key=repr)


class MLBV3NormalizedStore:
    def __init__(
        self,
        root: str | Path,
        *,
        repository: MLBV3GuardedRepository,
        use_context: DataUseContext = DataUseContext.RESEARCH,
    ) -> None:
        self.root = Path(root)
        self.repository = repository
        self.use_context = use_context
        self.repository.resolve(self.root / "mlb_v3")

    def partition_dir(self, table: str, season: int) -> Path:
        if table not in PRIMARY_KEYS:
            raise ValueError(f"unsupported MLB v3 table: {table}")
        return self.root / "mlb_v3" / table / f"season={season}"

    def write(self, table: str, season: int, frame: pl.DataFrame) -> Path:
        validate_or_raise(frame, MLB_V3_CONTRACTS[table])
        assert_frame_use_allowed(frame, self.use_context)
        existing = self.read_all(table, season)
        combined = frame if existing.is_empty() else pl.concat([existing, frame], how="diagonal_relaxed")
        conflicts = _conflicting_primary_keys(combined, PRIMARY_KEYS[table])
        if conflicts:
            raise ValueError(f"conflicting MLB v3 {table} primary keys: {conflicts[:5]}")
        buffer = io.BytesIO()
        frame.write_parquet(buffer)
        payload = buffer.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        path = self.partition_dir(table, season) / f"part-{digest}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def read_all(self, table: str, season: int) -> pl.DataFrame:
        paths = sorted(self.partition_dir(table, season).glob("part-*.parquet"))
        if not paths:
            return pl.DataFrame()
        return pl.concat(
            [pl.read_parquet(self.repository.resolve(path)) for path in paths],
            how="diagonal_relaxed",
        )

    def read(self, table: str, season: int) -> pl.DataFrame:
        frame = self.read_all(table, season)
        if frame.is_empty():
            return frame
        conflicts = _conflicting_primary_keys(frame, PRIMARY_KEYS[table])
        if conflicts:
            raise ValueError(f"conflicting MLB v3 {table} primary keys: {conflicts[:5]}")
        result = frame.unique(subset=PRIMARY_KEYS[table], keep="first", maintain_order=True)
        assert_frame_use_allowed(result, self.use_context)
        return result

    def conflict_count(self, table: str, season: int) -> int:
        frame = self.read_all(table, season)
        return len(_conflicting_primary_keys(frame, PRIMARY_KEYS[table]))
