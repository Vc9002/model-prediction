"""Immutable season-partitioned normalized WNBA Parquet storage."""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from pathlib import Path

import polars as pl


class WNBANormalizedStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def partition_dir(self, table: str, season: int) -> Path:
        return self.root / "wnba" / table / f"season={season}"

    def write(self, table: str, season: int, frame: pl.DataFrame) -> Path:
        buffer = io.BytesIO()
        frame.write_parquet(buffer)
        payload = buffer.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        path = self.partition_dir(table, season) / f"part-{digest}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with tmp.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    def read_season(self, table: str, season: int) -> pl.DataFrame:
        paths = sorted(self.partition_dir(table, season).glob("part-*.parquet"))
        if not paths:
            return pl.DataFrame()
        frame = pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")
        contract_keys = {
            "games": ["event_id", "observed_at_utc"],
            "team_box": ["event_id", "team_id", "observed_at_utc"],
            "player_box": ["event_id", "team_id", "player_id", "observed_at_utc"],
            "rosters": ["season", "team_id", "player_id", "observed_at_utc"],
            "pbp": ["event_id", "play_id", "observed_at_utc"],
        }
        return frame.unique(subset=contract_keys[table], keep="last", maintain_order=True)
