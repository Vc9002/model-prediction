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
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import duckdb
import polars as pl


def utc_now() -> datetime:
    return datetime.now(UTC)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    """Write a Parquet file via temp file + rename so a crash or kill mid-write
    cannot leave a truncated file at `path` masquerading as a valid table.

    Applies the same real fix RawStore.write() already has (FOUNDATION_COMPLETION.md
    Phase 2 "Interrupted writes cannot create a valid artifact") to every
    other Parquet writer in this module — previously only RawStore had it;
    NormalizedStore/FeatureStore/MarketStore all wrote straight to the final
    path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        df.write_parquet(str(tmp))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


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
            # Real gap fixed here (FOUNDATION_COMPLETION.md Phase 2): this
            # previously wrote directly to the final content-addressed path.
            # A crash or kill mid-write (a real risk during a long live
            # collection run) could leave a truncated/corrupted gzip file
            # sitting at a hash-named path that looks like a valid,
            # immutable snapshot until someone calls verify_hash() on it.
            # Write to a temp file in the same directory (same filesystem,
            # so the rename below is atomic on POSIX) and rename into place
            # only once the write is complete.
            tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                with gzip.open(tmp, "wb") as f:
                    f.write(raw_bytes)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, p)
            finally:
                tmp.unlink(missing_ok=True)
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

    def write(
        self, sport: str, table: str, df: pl.DataFrame, *,
        mode: str = "append",
        primary_key: list[str] | None = None,
        conflict_policy: str = "keep_latest",
    ) -> int:
        """Write a DataFrame to a Parquet table. mode='append' or 'overwrite'.

        Real bug fixed here (see outputs/rebuild/takeover_status.md
        Checkpoint 9 / FOUNDATION_COMPLETION.md Phase 2): append mode
        previously concatenated unconditionally, with no primary-key
        awareness at all — repeated collection of the same real event
        produced multiple identical-content rows (verified live: 188
        STATUS_FINAL MLB scoreboard rows were only 135 real unique games).
        A consumer-side `dedupe_scoreboard()` worked around this for
        already-written data; this fixes it at the write path so future
        writes don't need that workaround.

        When `primary_key` is given, rows sharing the same key value(s)
        are deduplicated after every write:
          - conflict_policy="keep_latest" (default): keeps the
            last-written row per key — correct for observational tables
            where a later collection legitimately reflects newer state
            (e.g. a game's status/score changing as it progresses).
          - conflict_policy="fail_closed": raises if two rows share a key
            but differ in content outside the standard provenance columns
            — correct for tables whose primary key is meant to be
            genuinely immutable (identical re-collection is still fine and
            silently deduped; a real conflicting value is not).
        """
        p = self.path(sport, table)
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "overwrite" or not p.exists():
            combined = df
        else:
            existing = pl.read_parquet(str(p))
            combined = pl.concat([existing, df], how="diagonal_relaxed")

        if primary_key:
            combined = self._dedupe_by_primary_key(combined, primary_key, conflict_policy)

        _atomic_write_parquet(combined, p)
        return df.height

    @staticmethod
    def _dedupe_by_primary_key(
        df: pl.DataFrame, primary_key: list[str], conflict_policy: str,
    ) -> pl.DataFrame:
        provenance_ish = {
            "observed_at_utc", "effective_at_utc", "ingested_at_utc",
            "raw_snapshot_hash", "source", "source_record_id", "source_version",
        }
        compare_cols = [c for c in df.columns if c not in provenance_ish and c not in primary_key]

        if conflict_policy == "fail_closed" and compare_cols:
            distinct_content = (
                df.select([*primary_key, *compare_cols])
                .unique()
                .group_by(primary_key)
                .agg(pl.len().alias("_distinct_variants"))
                .filter(pl.col("_distinct_variants") > 1)
            )
            if distinct_content.height > 0:
                raise ValueError(
                    f"Conflicting content for primary key {primary_key} in "
                    f"{distinct_content.height} row(s) — fail_closed conflict_policy "
                    f"requires identical content per key, not just a key match."
                )

        # "Last write wins": since new rows are concatenated after existing
        # ones above, .last() per key naturally keeps the most recently
        # written row — the intended "keep_latest" semantics for both
        # policies (fail_closed already verified there's no real conflict).
        return df.group_by(primary_key, maintain_order=True).last()

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
    """Immutable, versioned point-in-time feature snapshots under
    data/rebuild/features/{sport}/{horizon}/.

    FOUNDATION_COMPLETION.md Phase 2 "Feature snapshots" requires snapshots
    be immutable and versioned by dataset_hash — this previously wrote
    directly to {sport}/{horizon}.parquet and said outright "Overwrites for
    that horizon," destroying every earlier version on each rebuild. Every
    write now lands at an immutable dataset_hash-named path; a `latest.json`
    manifest records version history and points `read()`/`read_meta()` at
    the current version so existing callers of the "give me the current
    snapshot" behavior are unaffected, while `read_version()`/
    `list_versions()` expose full history.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _horizon_dir(self, sport: str, horizon: str) -> Path:
        """data/rebuild/features/{sport}/{horizon}/
        horizon is one of: early, mid, late
        """
        return self.root / sport / horizon

    def _version_path(self, sport: str, horizon: str, snapshot_hash: str) -> Path:
        return self._horizon_dir(sport, horizon) / f"{snapshot_hash}.parquet"

    def _manifest_path(self, sport: str, horizon: str) -> Path:
        return self._horizon_dir(sport, horizon) / "latest.json"

    def exists(self, sport: str, horizon: str) -> bool:
        return self._manifest_path(sport, horizon).exists()

    def write_snapshot(
        self,
        sport: str,
        horizon: str,
        df: pl.DataFrame,
        snapshot_hash: str,
    ) -> int:
        """Write an immutable feature snapshot version and advance the
        latest-version manifest. Writing the same `snapshot_hash` twice is
        idempotent — the existing immutable file is left untouched, only the
        manifest's `updated_at_utc` and history entry are refreshed.
        """
        d = self._horizon_dir(sport, horizon)
        d.mkdir(parents=True, exist_ok=True)
        version_path = self._version_path(sport, horizon, snapshot_hash)
        version_meta = {
            "sport": sport,
            "horizon": horizon,
            "snapshot_hash": snapshot_hash,
            "created_at_utc": utc_now().isoformat(),
            "row_count": df.height,
            "columns": df.columns,
        }
        if not version_path.exists():
            _atomic_write_parquet(df, version_path)
            version_path.with_suffix(".meta.json").write_text(json.dumps(version_meta, indent=2))

        manifest_path = self._manifest_path(sport, horizon)
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"history": []}
        if not any(h["snapshot_hash"] == snapshot_hash for h in manifest["history"]):
            manifest["history"].append(version_meta)
        manifest["latest_snapshot_hash"] = snapshot_hash
        manifest["updated_at_utc"] = utc_now().isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2))
        return df.height

    def read(self, sport: str, horizon: str) -> pl.DataFrame:
        """Read the current (latest) version for a horizon."""
        manifest = json.loads(self._manifest_path(sport, horizon).read_text())
        return self.read_version(sport, horizon, manifest["latest_snapshot_hash"])

    def read_version(self, sport: str, horizon: str, snapshot_hash: str) -> pl.DataFrame:
        return pl.read_parquet(str(self._version_path(sport, horizon, snapshot_hash)))

    def read_meta(self, sport: str, horizon: str) -> dict[str, Any]:
        p = self._manifest_path(sport, horizon)
        if not p.exists():
            return {}
        manifest = json.loads(p.read_text())
        latest = manifest["latest_snapshot_hash"]
        for h in manifest["history"]:
            if h["snapshot_hash"] == latest:
                return h
        return {}

    def list_versions(self, sport: str, horizon: str) -> list[dict[str, Any]]:
        """Full immutable version history, oldest first."""
        p = self._manifest_path(sport, horizon)
        if not p.exists():
            return []
        return list(json.loads(p.read_text())["history"])

    def available_horizons(self, sport: str) -> list[str]:
        if not self.root.joinpath(sport).exists():
            return []
        return sorted(
            d.name for d in self.root.joinpath(sport).iterdir()
            if d.is_dir() and (d / "latest.json").exists()
        )


# ── Market storage ──────────────────────────────────────────────────────────


class MarketStore:
    """Timestamped market books and BBOs under data/rebuild/markets/."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, sport: str, date_str: str) -> Path:
        """data/rebuild/markets/{sport}/{date}.parquet"""
        return self.root / sport / f"{date_str}.parquet"

    # FOUNDATION_COMPLETION.md Phase 2 "Market snapshots" keys observations by
    # (market_id, side_id, line, period, observed_at_utc). The real collector
    # schema (collectors.py) doesn't have side_id/period columns yet — market
    # side is carried in team_or_side and period isn't modeled at the market
    # row level at all — so this uses the closest columns that actually exist
    # today. Distinct observed_at_utc values are legitimate append-only
    # observations (a later collection sees a real, later book state); this
    # only dedupes true content-identical repeats of the same row, e.g. a
    # collection run retried against the same not-yet-changed API response.
    DEFAULT_PRIMARY_KEY: ClassVar[list[str]] = ["market_id", "team_or_side", "line", "observed_at_utc"]

    def write_books(
        self,
        sport: str,
        date_str: str,
        df: pl.DataFrame,
        *,
        primary_key: list[str] | None = None,
    ) -> int:
        """Write market snapshots for one date. Appends if exists.

        Real bug fixed here (FOUNDATION_COMPLETION.md Phase 2 "Market
        snapshots" + the reviewer-identified gap from the last takeover
        session): this previously concatenated unconditionally with no key
        awareness at all, so a retried/duplicated collection call could
        write exact-duplicate rows with no way to detect it later. Dedupes
        on `primary_key` (defaults to `DEFAULT_PRIMARY_KEY`) using the same
        keep_latest semantics as `NormalizedStore.write()`; pass
        `primary_key=None` explicitly is not supported — pass `[]` to opt
        out entirely if a caller genuinely needs raw concatenation.
        """
        p = self.path(sport, date_str)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            existing = pl.read_parquet(str(p))
            combined = pl.concat([existing, df], how="diagonal_relaxed")
        else:
            combined = df

        key = self.DEFAULT_PRIMARY_KEY if primary_key is None else primary_key
        key = [k for k in key if k in combined.columns]
        if key:
            combined = NormalizedStore._dedupe_by_primary_key(combined, key, "keep_latest")

        _atomic_write_parquet(combined, p)
        return df.height

    def read(self, sport: str, date_str: str) -> pl.DataFrame:
        return pl.read_parquet(str(self.path(sport, date_str)))

    def available_dates(self, sport: str) -> list[str]:
        sport_dir = self.root / sport
        if not sport_dir.exists():
            return []
        return sorted(p.stem for p in sport_dir.glob("*.parquet"))
