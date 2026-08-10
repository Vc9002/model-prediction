"""Content-addressed append-only normalized soccer Parquet storage."""

from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from pathlib import Path

import polars as pl

from model_prediction.rebuild.schemas import validate_or_raise

from .contracts import SOCCER_MATCHES

_PARTITION_VALUE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SoccerNormalizedStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _safe(value: str) -> str:
        if not _PARTITION_VALUE.fullmatch(value):
            raise ValueError(f"unsafe soccer partition value: {value!r}")
        return value

    def partition_dir(self, source: str, competition: str, season: str) -> Path:
        return (
            self.root
            / "soccer"
            / "matches"
            / f"source={self._safe(source)}"
            / f"competition={self._safe(competition)}"
            / f"season={self._safe(season)}"
        )

    def write_matches(self, frame: pl.DataFrame) -> list[Path]:
        validate_or_raise(frame, SOCCER_MATCHES)
        written: list[Path] = []
        for key, partition in frame.partition_by(
            ["source", "competition_id", "season_id"], as_dict=True, maintain_order=True
        ).items():
            source, competition, season = (str(value) for value in key)
            buffer = io.BytesIO()
            partition.write_parquet(buffer)
            payload = buffer.getvalue()
            digest = hashlib.sha256(payload).hexdigest()
            path = self.partition_dir(source, competition, season) / f"part-{digest}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
                try:
                    with tmp.open("wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, path)
                finally:
                    tmp.unlink(missing_ok=True)
            written.append(path)
        return written

    def read_matches(self) -> pl.DataFrame:
        paths = sorted((self.root / "soccer" / "matches").rglob("part-*.parquet"))
        if not paths:
            return pl.DataFrame()
        return pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed").unique(
            subset=["source", "source_match_id", "observed_at_utc"],
            keep="last",
            maintain_order=True,
        )
