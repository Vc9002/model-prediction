"""Immutable season-partitioned normalized WNBA Parquet storage."""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from pathlib import Path
from typing import ClassVar

import polars as pl


class WNBANormalizedStore:
    BUSINESS_KEYS: ClassVar[dict[str, list[str]]] = {
        "games": ["event_id"],
        "team_box": ["event_id", "team_id"],
        "player_box": ["event_id", "team_id", "player_id"],
        "rosters": ["season", "team_id", "player_id"],
        "pbp": ["event_id", "play_id"],
    }

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def partition_dir(self, table: str, season: int) -> Path:
        return self.root / "wnba" / table / f"season={season}"

    def _part_paths(self, table: str, season: int) -> list[Path]:
        paths = sorted(self.partition_dir(table, season).glob("part-*.parquet"))
        for path in paths:
            expected = path.stem.removeprefix("part-")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"WNBA normalized part failed SHA256 verification: {path}")
        return paths

    @staticmethod
    def observation_keys(table: str) -> list[str]:
        return {
            "games": ["event_id", "observed_at_utc"],
            "team_box": ["event_id", "team_id", "observed_at_utc"],
            "player_box": ["event_id", "team_id", "player_id", "observed_at_utc"],
            "rosters": ["season", "team_id", "player_id", "observed_at_utc"],
            "pbp": ["event_id", "play_id", "observed_at_utc"],
        }[table]

    def read_observations(self, table: str, season: int) -> pl.DataFrame:
        paths = self._part_paths(table, season)
        if not paths:
            return pl.DataFrame()
        return pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")

    def part_hashes(self, table: str, season: int) -> list[str]:
        return [path.stem.removeprefix("part-") for path in self._part_paths(table, season)]

    def write(self, table: str, season: int, frame: pl.DataFrame) -> Path:
        keys = self.observation_keys(table)
        if not set(keys).issubset(frame.columns):
            raise ValueError(f"WNBA normalized {table} is missing observation keys")
        if frame.group_by(keys).len().filter(pl.col("len") > 1).height:
            raise ValueError(f"duplicate WNBA {table} observation keys in incoming batch")
        existing = self.read_observations(table, season)
        incoming = frame
        if not existing.is_empty():
            comparable_existing = set(existing.columns) - {"ingested_at_utc"}
            comparable_incoming = set(incoming.columns) - {"ingested_at_utc"}
            if comparable_existing != comparable_incoming:
                raise ValueError(f"conflicting WNBA {table} schema for normalized partition")
            compare_columns = sorted(comparable_existing)
            existing_compare = existing.select(compare_columns)
            incoming_compare = incoming.select(compare_columns)
            joined = incoming_compare.join(
                existing_compare,
                on=keys,
                how="inner",
                suffix="_existing",
            )
            for row in joined.iter_rows(named=True):
                conflict = any(
                    row[column] != row[f"{column}_existing"]
                    for column in compare_columns
                    if column not in keys
                )
                if conflict:
                    raise ValueError(f"conflicting WNBA {table} content for one observation")
            existing_keys = existing.select(keys).unique()
            incoming = incoming.join(existing_keys, on=keys, how="anti")
            if incoming.is_empty():
                return self._part_paths(table, season)[0]

        canonical_columns = sorted(incoming.columns)
        incoming = incoming.select(canonical_columns).sort(keys)
        buffer = io.BytesIO()
        incoming.write_parquet(buffer)
        payload = buffer.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        path = self.partition_dir(table, season) / f"part-{digest}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"conflicting immutable WNBA normalized part: {path}")
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
        frame = self.read_observations(table, season)
        if frame.is_empty():
            return pl.DataFrame()
        keys = self.observation_keys(table)
        duplicates = frame.group_by(keys).len().filter(pl.col("len") > 1)
        if duplicates.height:
            # Identical reruns should never create a second part; duplicate
            # observation keys on disk are therefore an integrity error.
            raise ValueError(f"duplicate WNBA {table} observation keys across normalized parts")
        return frame.sort(keys)

    def read_latest(self, table: str, season: int) -> pl.DataFrame:
        """Latest observed vintage per business key; history remains on disk/read_season."""
        frame = self.read_season(table, season)
        if frame.is_empty():
            return frame
        return (
            frame.with_columns(
                pl.col("observed_at_utc").str.to_datetime(time_zone="UTC", strict=True).alias("_observed")
            )
            .sort("_observed")
            .group_by(self.BUSINESS_KEYS[table], maintain_order=True)
            .last()
            .drop("_observed")
        )
