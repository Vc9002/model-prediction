"""Immutable content-addressed normalized Parquet storage for MLB v3."""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from pathlib import Path

import polars as pl

from .contracts import PRIMARY_KEYS


class MLBV3NormalizedStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def partition_dir(self, table: str, season: int) -> Path:
        if table not in PRIMARY_KEYS:
            raise ValueError(f"unsupported MLB v3 table: {table}")
        return self.root / "mlb_v3" / table / f"season={season}"

    def write(self, table: str, season: int, frame: pl.DataFrame) -> Path:
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

    def read(self, table: str, season: int) -> pl.DataFrame:
        paths = sorted(self.partition_dir(table, season).glob("part-*.parquet"))
        if not paths:
            return pl.DataFrame()
        frame = pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")
        return frame.unique(subset=PRIMARY_KEYS[table], keep="last", maintain_order=True)
