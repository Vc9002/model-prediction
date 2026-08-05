"""Medallion storage layer — immutable raw, normalized Parquet, point-in-time features, timestamped markets.

Every observation carries the standard provenance columns:
    source, source_record_id, source_version, observed_at_utc, effective_at_utc,
    event_start_utc, ingested_at_utc, available, missing_reason, raw_snapshot_hash,
    schema_version

A decision at time T may only use information with observed_at_utc <= T.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl


def utc_now() -> datetime:
    return datetime.now(UTC)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Standard provenance columns for every observation ───────────────────────

PROVENANCE_COLUMNS: list[str] = [
    "source",
    "source_record_id",
    "source_version",
    "observed_at_utc",
    "effective_at_utc",
    "event_start_utc",
    "ingested_at_utc",
    "available",
    "missing_reason",
    "raw_snapshot_hash",
    "schema_version",
]


def provenance_row(
    source: str,
    source_record_id: str,
    source_version: str,
    observed_at_utc: str,
    effective_at_utc: str,
    event_start_utc: str,
    available: bool = True,
    missing_reason: str | None = None,
    raw_snapshot_hash: str = "",
    schema_version: str = "1",
) -> dict[str, Any]:
    """Build one standard provenance dict. ingested_at_utc is set to now."""
    return {
        "source": source,
        "source_record_id": source_record_id,
        "source_version": source_version,
        "observed_at_utc": observed_at_utc,
        "effective_at_utc": effective_at_utc,
        "event_start_utc": event_start_utc,
        "ingested_at_utc": utc_now().isoformat(),
        "available": available,
        "missing_reason": missing_reason,
        "raw_snapshot_hash": raw_snapshot_hash,
        "schema_version": schema_version,
    }


# ── Raw storage ─────────────────────────────────────────────────────────────


class RawStore:
    """Immutable compressed provider responses under data/rebuild/raw/{source}/."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, source: str, date_str: str, record_id: str) -> Path:
        """data/rebuild/raw/{source}/{date}/{record_id}.json.gz"""
        return self.root / source / date_str / f"{record_id}.json.gz"

    def exists(self, source: str, date_str: str, record_id: str) -> bool:
        return self.path(source, date_str, record_id).exists()

    def write(self, source: str, date_str: str, record_id: str, payload: Any) -> str:
        """Write an immutable raw snapshot. Returns the SHA-256 hash."""
        p = self.path(source, date_str, record_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        raw_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        snapshot_hash = sha256_hex(raw_bytes)
        with gzip.open(p, "wb") as f:
            f.write(raw_bytes)
        return snapshot_hash

    def read(self, source: str, date_str: str, record_id: str) -> Any:
        p = self.path(source, date_str, record_id)
        with gzip.open(p, "rb") as f:
            return json.loads(f.read().decode("utf-8"))

    def read_hash(self, source: str, date_str: str, record_id: str) -> str:
        p = self.path(source, date_str, record_id)
        with gzip.open(p, "rb") as f:
            return sha256_hex(f.read())


# ── Normalized storage ──────────────────────────────────────────────────────


class NormalizedStore:
    """Canonical Parquet tables under data/rebuild/normalized/{sport}/{table}/."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.db = duckdb.connect()

    def path(self, sport: str, table: str) -> Path:
        return self.root / sport / f"{table}.parquet"

    def write(self, sport: str, table: str, df: pl.DataFrame, *, mode: str = "append") -> int:
        """Write a DataFrame to a Parquet table. mode='append' or 'overwrite'."""
        p = self.path(sport, table)
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "overwrite" or not p.exists():
            df.write_parquet(str(p))
        else:
            existing = pl.read_parquet(str(p))
            combined = pl.concat([existing, df], how="diagonal_relaxed")
            combined.write_parquet(str(p))
        return df.height

    def read(self, sport: str, table: str) -> pl.DataFrame:
        return pl.read_parquet(str(self.path(sport, table)))

    def query(self, sql: str) -> pl.DataFrame:
        return self.db.sql(sql).pl()

    def register(self, sport: str, table: str, alias: str | None = None) -> None:
        """Register a Parquet table as a DuckDB view for SQL queries."""
        p = str(self.path(sport, table))
        name = alias or f"{sport}_{table}"
        self.db.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM '{p}'")

    @property
    def tables(self) -> dict[str, list[str]]:
        """Return {sport: [table_names]}."""
        result: dict[str, list[str]] = {}
        if not self.root.exists():
            return result
        for sport_dir in sorted(self.root.iterdir()):
            if sport_dir.is_dir():
                tables = [p.stem for p in sorted(sport_dir.glob("*.parquet"))]
                if tables:
                    result[sport_dir.name] = tables
        return result


# ── Feature storage ─────────────────────────────────────────────────────────


class FeatureStore:
    """Point-in-time feature snapshots under data/rebuild/features/{sport}/."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, sport: str, horizon: str) -> Path:
        """data/rebuild/features/{sport}/{horizon}.parquet
        horizon is one of: early, mid, late
        """
        return self.root / sport / f"{horizon}.parquet"

    def exists(self, sport: str, horizon: str) -> bool:
        return self.path(sport, horizon).exists()

    def write_snapshot(
        self,
        sport: str,
        horizon: str,
        df: pl.DataFrame,
        snapshot_hash: str,
    ) -> int:
        """Write a feature snapshot. Overwrites for that horizon."""
        p = self.path(sport, horizon)
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "sport": sport,
            "horizon": horizon,
            "snapshot_hash": snapshot_hash,
            "created_at_utc": utc_now().isoformat(),
            "row_count": df.height,
            "columns": df.columns,
        }
        meta_path = p.with_suffix(".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2))
        df.write_parquet(str(p))
        return df.height

    def read(self, sport: str, horizon: str) -> pl.DataFrame:
        return pl.read_parquet(str(self.path(sport, horizon)))

    def read_meta(self, sport: str, horizon: str) -> dict[str, Any]:
        p = self.path(sport, horizon).with_suffix(".meta.json")
        return json.loads(p.read_text()) if p.exists() else {}

    def available_horizons(self, sport: str) -> list[str]:
        if not self.root.joinpath(sport).exists():
            return []
        return sorted(
            p.stem for p in self.root.joinpath(sport).glob("*.parquet")
            if p.stem != "metadata"
        )


# ── Market storage ──────────────────────────────────────────────────────────


class MarketStore:
    """Timestamped market books and BBOs under data/rebuild/markets/."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, sport: str, date_str: str) -> Path:
        """data/rebuild/markets/{sport}/{date}.parquet"""
        return self.root / sport / f"{date_str}.parquet"

    def write_books(
        self,
        sport: str,
        date_str: str,
        df: pl.DataFrame,
    ) -> int:
        """Write market snapshots for one date. Appends if exists."""
        p = self.path(sport, date_str)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            existing = pl.read_parquet(str(p))
            df = pl.concat([existing, df], how="diagonal_relaxed")
        df.write_parquet(str(p))
        return df.height

    def read(self, sport: str, date_str: str) -> pl.DataFrame:
        return pl.read_parquet(str(self.path(sport, date_str)))

    def available_dates(self, sport: str) -> list[str]:
        sport_dir = self.root / sport
        if not sport_dir.exists():
            return []
        return sorted(p.stem for p in sport_dir.glob("*.parquet"))
