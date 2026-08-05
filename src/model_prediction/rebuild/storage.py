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


def _json_default(obj: Any) -> Any:
    """Canonical fallback for json.dumps.

    Collectors regularly hand RawStore.write() pandas/numpy-typed payloads
    (pybaseball's statcast() returns pandas.Timestamp columns, for example).
    The stdlib json encoder raises TypeError on those, and that TypeError was
    previously swallowed by a collector's broad except-and-report-degraded
    handler, silently discarding real, successfully-fetched data (see
    outputs/rebuild/takeover_status.md Checkpoint 4). Duck-typed rather than
    importing pandas/numpy here, so RawStore stays usable by any collector's
    payload shape without adding a hard dependency.
    """
    if hasattr(obj, "isoformat"):  # datetime, date, pandas.Timestamp
        return obj.isoformat()
    if hasattr(obj, "item"):  # numpy scalar: int64, float64, bool_, etc.
        return obj.item()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


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


class RawSnapshotRef:
    """Immutable reference to a stored raw snapshot."""

    def __init__(self, source: str, source_record_id: str,
                 observed_at_utc: str, snapshot_hash: str, path: Path) -> None:
        self.source = source
        self.source_record_id = source_record_id
        self.observed_at_utc = observed_at_utc
        self.snapshot_hash = snapshot_hash
        self.path = path

    def __repr__(self) -> str:
        return (f"RawSnapshotRef(source={self.source}, record={self.source_record_id}, "
                f"hash={self.snapshot_hash[:16]}...)")


class RawStore:
    """Immutable content-addressed raw storage.

    Every distinct response receives a unique path incorporating its hash.
    Identical payloads are idempotent — same hash returns existing file.
    No overwrite semantics. No allow_refresh option. A refreshed response
    is a new snapshot with a new hash.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _content_path(self, source: str, date_str: str,
                      record_id: str, payload_hash: str) -> Path:
        """data/rebuild/raw/{source}/{date}/{record_id}/{hash}.json.gz"""
        return self.root / source / date_str / record_id / f"{payload_hash}.json.gz"

    def write(self, source: str, date_str: str, record_id: str, payload: Any, *,
              observed_at: str | None = None) -> RawSnapshotRef:
        """Write an immutable content-addressed raw snapshot.

        Returns a RawSnapshotRef. If the exact same payload already exists
        (by hash), returns the existing ref without rewriting. Different
        payloads always produce different files — no overwrites.
        """
        observed = observed_at or utc_now().isoformat()
        raw_bytes = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, default=_json_default
        ).encode("utf-8")
        snapshot_hash = sha256_hex(raw_bytes)
        p = self._content_path(source, date_str, record_id, snapshot_hash)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            with gzip.open(p, "wb") as f:
                f.write(raw_bytes)
        return RawSnapshotRef(
            source=source,
            source_record_id=record_id,
            observed_at_utc=observed,
            snapshot_hash=snapshot_hash,
            path=p,
        )

    def read(self, ref: RawSnapshotRef) -> Any:
        with gzip.open(ref.path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))

    def verify_hash(self, ref: RawSnapshotRef) -> bool:
        """Verify the stored file matches the recorded hash."""
        with gzip.open(ref.path, "rb") as f:
            return sha256_hex(f.read()) == ref.snapshot_hash

    def list_snapshots(self, source: str, date_str: str,
                       record_id: str) -> list[RawSnapshotRef]:
        """List all snapshots for a given source/date/record."""
        parent = self.root / source / date_str / record_id
        if not parent.exists():
            return []
        refs = []
        for f in sorted(parent.glob("*.json.gz")):
            h = f.stem
            refs.append(RawSnapshotRef(
                source=source, source_record_id=record_id,
                observed_at_utc="", snapshot_hash=h, path=f,
            ))
        return refs


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
