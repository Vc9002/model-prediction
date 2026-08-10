"""Immutable, content-addressed normalized tennis Parquet storage.

`matches` partitions by tour + year (parsed from `tourney_date`, TennisMyLife's
own event-date field). `current_events` partitions by tour + the UTC calendar
date of `observed_at_utc` (the capture date) -- ESPN scoreboard captures have
no season/year of their own, only a real capture instant.
"""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from pathlib import Path
from typing import ClassVar

import polars as pl


class TennisNormalizedStore:
    PRIMARY_KEYS: ClassVar[dict[str, list[str]]] = {
        "matches": ["canonical_match_id", "observed_at_utc"],
        "current_events": ["canonical_match_id", "observed_at_utc"],
    }

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _matches_partition(self, tour: str, year: int) -> Path:
        return self.root / "tennis" / "matches" / f"tour={tour}" / f"year={year}"

    def _current_events_partition(self, tour: str, capture_date: str) -> Path:
        return self.root / "tennis" / "current_events" / f"tour={tour}" / f"date={capture_date}"

    def write_matches(self, frame: pl.DataFrame) -> list[Path]:
        if frame.is_empty():
            return []
        written: list[Path] = []
        for (tour, tourney_date), part in frame.with_columns(
            pl.col("tourney_date").str.slice(0, 4).cast(pl.Int64).alias("_year")
        ).group_by(["tour", "_year"], maintain_order=True):
            written.append(self._write_part(self._matches_partition(str(tour), int(tourney_date)), part.drop("_year")))
        return written

    def write_current_events(self, frame: pl.DataFrame) -> list[Path]:
        if frame.is_empty():
            return []
        written: list[Path] = []
        for (tour, capture_date), part in frame.with_columns(
            pl.col("observed_at_utc").str.slice(0, 10).alias("_date")
        ).group_by(["tour", "_date"], maintain_order=True):
            written.append(
                self._write_part(self._current_events_partition(str(tour), str(capture_date)), part.drop("_date"))
            )
        return written

    @staticmethod
    def _write_part(partition_dir: Path, frame: pl.DataFrame) -> Path:
        buffer = io.BytesIO()
        frame.write_parquet(buffer)
        payload = buffer.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        path = partition_dir / f"part-{digest}.parquet"
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

    def read_matches(self, *, tour: str | None = None, year: int | None = None) -> pl.DataFrame:
        return self._read("matches", tour=tour, year=year)

    def read_current_events(self, *, tour: str | None = None) -> pl.DataFrame:
        return self._read("current_events", tour=tour)

    def _read(self, table: str, *, tour: str | None, year: int | None = None) -> pl.DataFrame:
        table_root = self.root / "tennis" / table
        if not table_root.exists():
            return pl.DataFrame()
        glob = f"tour={tour or '*'}/"
        glob += f"year={year}/" if table == "matches" and year is not None else "*/"
        paths = sorted(table_root.glob(f"{glob}part-*.parquet"))
        if not paths:
            return pl.DataFrame()
        frame = pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")
        return frame.unique(subset=self.PRIMARY_KEYS[table], keep="last", maintain_order=True)
