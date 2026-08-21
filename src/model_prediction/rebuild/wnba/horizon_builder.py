"""One PIT-safe WNBA feature path shared by replay and live operation.

Recovered from the archived `origin/rebuild/wnba-v1` branch
(`archive/model-source-rebuild-wnba-v1-95c7dcc2`) per
`docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md`'s RECOVER verdict.
Ported unmodified: every dependency (`rebuild.horizons`,
`rebuild.providers.base.{canonical_json,dataframe_schema_hash}`,
`rebuild.storage.FeatureStore`, local `.features`/`.store`/`.time`) is
byte-identical in current `main` to what this file was originally written
against (verified directly, see the archive-review doc).

Two real, deliberate properties worth calling out for whoever next touches
this file:

1. `_target_as_of_cutoff`'s stabilization loop exists because a postponed
   game's start can itself change -- it re-resolves the target from
   schedule state known as of the *previous* cutoff estimate until the
   known start stops moving, and fails closed (raises) if it never
   converges within 5 attempts, rather than silently using a stale cutoff.
2. `_assert_research_source_provenance` hard-fails unless every source row
   is `capture_time_only` / `commercial_use_status="unresolved"` /
   `production_allowed=False` -- this recovered feature path is
   structurally incapable of producing anything that would pass as
   production-cleared, by design, until the upstream SportsDataverse/ESPN
   commercial-use question is resolved. This module unblocks *research*
   only, not production.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from model_prediction.rebuild.horizons import HORIZON_HOURS_BEFORE, HORIZONS
from model_prediction.rebuild.providers.base import canonical_json, dataframe_schema_hash
from model_prediction.rebuild.storage import FeatureStore

from .features import METRICS, WINDOWS, build_team_form_snapshot
from .store import WNBANormalizedStore
from .time import sports_event_date

FEATURE_SCHEMA_VERSION = "wnba_team_form_v1"
RESEARCH_PROVENANCE_COLUMNS = {
    "availability_basis",
    "commercial_use_status",
    "production_allowed",
    "raw_snapshot_hash",
}


@dataclass(frozen=True)
class WNBAFeatureBuildResult:
    mode: str
    game_date: str
    horizon: str
    rows: list[dict[str, Any]]
    missing_reasons: dict[str, int]
    target_games: int
    snapshot_hash: str | None
    feature_schema_hash: str | None
    payload_path: str | None


def _increment(reasons: dict[str, int], reason: str) -> None:
    reasons[reason] = reasons.get(reason, 0) + 1


def _assert_research_source_provenance(frame: pl.DataFrame, table: str) -> None:
    """Reject missing/unknown rights metadata before any PIT filtering."""

    missing = sorted(RESEARCH_PROVENANCE_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"WNBA {table} source is missing provenance columns: {missing}")
    for column in RESEARCH_PROVENANCE_COLUMNS:
        if frame[column].null_count():
            raise ValueError(f"WNBA {table} source has null provenance: {column}")
    if frame.filter(pl.col("availability_basis") != "capture_time_only").height:
        raise ValueError(f"WNBA {table} source has unsupported availability provenance")
    if frame.filter(pl.col("commercial_use_status") != "unresolved").height:
        raise ValueError(f"WNBA {table} source commercial-use rights are not research-blocked")
    if frame.filter(pl.col("production_allowed") != False).height:
        raise ValueError(f"WNBA {table} source is production-enabled")
    if frame.filter(pl.col("raw_snapshot_hash").cast(pl.String).str.len_chars() == 0).height:
        raise ValueError(f"WNBA {table} source has empty raw snapshot hashes")


def _latest_targets(games: pl.DataFrame, game_date: str) -> pl.DataFrame:
    required = {"event_id", "event_start_utc", "sports_event_date", "observed_at_utc"}
    if games.is_empty() or not required.issubset(games.columns):
        return pl.DataFrame()
    return (
        games.with_columns(
            pl.col("observed_at_utc").str.to_datetime(time_zone="UTC", strict=True).alias("_observed"),
            pl.col("event_start_utc").str.to_datetime(time_zone="UTC", strict=True).alias("_event_start"),
        )
        .sort("_observed")
        .group_by("event_id", maintain_order=True)
        .last()
        .filter(pl.col("sports_event_date") == game_date)
        .drop("_observed")
    )


def _target_as_of_cutoff(
    games: pl.DataFrame,
    *,
    event_id: str,
    initial_start: datetime,
    hours_before: float,
) -> tuple[dict[str, Any], datetime] | None:
    """Resolve the target using only schedule state knowable at its cutoff.

    A postponed game's start can itself change. Recompute the cutoff from the
    latest schedule state at the prior cutoff until the known start stabilizes;
    fail closed if it does not converge.
    """

    event_rows = games.filter(pl.col("event_id") == event_id).with_columns(
        pl.col("observed_at_utc").str.to_datetime(time_zone="UTC", strict=True).alias("_observed")
    )
    known_start = initial_start.astimezone(UTC)
    for _attempt in range(5):
        decision_time = known_start - timedelta(hours=hours_before)
        eligible = event_rows.filter(pl.col("_observed") <= decision_time).sort("_observed")
        if eligible.is_empty():
            return None
        row = eligible.tail(1).drop("_observed").to_dicts()[0]
        row_start = datetime.fromisoformat(str(row["event_start_utc"]))
        if row_start.tzinfo is None:
            raise ValueError("WNBA target event start must be timezone-aware")
        row_start = row_start.astimezone(UTC)
        if row_start == known_start:
            if not bool(row.get("pit_eligible", False)):
                return None
            return row, decision_time
        known_start = row_start
    raise ValueError(f"WNBA target schedule did not stabilize at a PIT cutoff: {event_id}")


def _metrics_complete(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("status") != "AVAILABLE":
        return False
    return all(
        isinstance(snapshot.get(f"{label}_{metric}"), (int, float))
        and math.isfinite(float(snapshot[f"{label}_{metric}"]))
        for label in [*(f"last_{window}" for window in WINDOWS), "season"]
        for metric in METRICS
    )


def _feature_row(
    target: dict[str, Any],
    *,
    horizon: str,
    decision_time: datetime,
    home: dict[str, Any],
    away: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_id": str(target["event_id"]),
        "event_start_utc": datetime.fromisoformat(str(target["event_start_utc"])).astimezone(UTC).isoformat(),
        "decision_time_utc": decision_time.astimezone(UTC).isoformat(),
        "horizon": horizon,
        "sports_event_date": str(target["sports_event_date"]),
        "home_team_id": str(target["home_team_id"]),
        "away_team_id": str(target["away_team_id"]),
        "home_team_canonical_id": str(target["home_team_canonical_id"]),
        "away_team_canonical_id": str(target["away_team_canonical_id"]),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "availability_basis": str(target["availability_basis"]),
        "commercial_use_status": str(target["commercial_use_status"]),
        "production_allowed": bool(target["production_allowed"]),
        "target_raw_snapshot_hash": str(target["raw_snapshot_hash"]),
    }
    for side, snapshot in (("home", home), ("away", away)):
        for key, value in snapshot.items():
            if key not in {"team_id", "status", "as_of_utc"}:
                row[f"{side}_{key}"] = value
    source_hashes = sorted(
        {
            str(target["raw_snapshot_hash"]),
            *(str(value) for value in home["source_raw_snapshot_hashes"]),
            *(str(value) for value in away["source_raw_snapshot_hashes"]),
        }
    )
    row["source_raw_snapshot_hashes"] = source_hashes
    row["source_manifest_hash"] = hashlib.sha256(
        canonical_json(
            {
                "event_id": str(target["event_id"]),
                "raw_snapshot_hashes": source_hashes,
            }
        )
    ).hexdigest()
    return row


def _write_schema_manifest(
    path: Path,
    *,
    feature_schema_hash: str,
    columns: list[str],
) -> None:
    payload = json.dumps(
        {
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_schema_hash": feature_schema_hash,
            "columns": columns,
        },
        indent=2,
        sort_keys=True,
    )
    if path.exists():
        if path.read_text() != payload:
            raise ValueError(f"conflicting immutable WNBA feature schema manifest: {path}")
        return
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(payload)
        try:
            os.link(tmp, path)
        except FileExistsError:
            if path.read_text() != payload:
                raise ValueError(f"conflicting immutable WNBA feature schema manifest: {path}")
    finally:
        tmp.unlink(missing_ok=True)


def _build(
    data_root: str | Path,
    game_date: str,
    horizon: str,
    *,
    mode: str,
    ledger: Any | None = None,
    run_id: str | None = None,
    knowledge_time_utc: datetime | None = None,
) -> WNBAFeatureBuildResult:
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}, got {horizon!r}")
    if mode not in {"replay", "live"}:
        raise ValueError(f"unsupported WNBA feature mode: {mode}")
    parsed_date = datetime.fromisoformat(game_date)
    if (
        game_date != parsed_date.date().isoformat()
        or parsed_date.tzinfo is not None
        or parsed_date.time() != datetime.min.time()
    ):
        raise ValueError("WNBA feature game_date must be an ISO calendar date")
    if knowledge_time_utc is not None and knowledge_time_utc.tzinfo is None:
        raise ValueError("WNBA live knowledge time must be timezone-aware")
    live_knowledge_time = (
        (knowledge_time_utc or datetime.now(UTC)).astimezone(UTC) if mode == "live" else None
    )

    root = Path(data_root)
    season = parsed_date.year
    normalized = WNBANormalizedStore(root / "normalized")
    games = normalized.read_observations("games", season)
    team_box = normalized.read_observations("team_box", season)
    if not games.is_empty():
        _assert_research_source_provenance(games, "games")
    if not team_box.is_empty():
        _assert_research_source_provenance(team_box, "team_box")
    targets = _latest_targets(games, game_date)
    reasons: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    hours_before = HORIZON_HOURS_BEFORE[horizon]

    for latest in targets.iter_rows(named=True):
        initial_start = latest["_event_start"]
        resolved = _target_as_of_cutoff(
            games,
            event_id=str(latest["event_id"]),
            initial_start=initial_start,
            hours_before=hours_before,
        )
        if resolved is None:
            _increment(reasons, "target_unavailable_at_decision_cutoff")
            continue
        target, decision_time = resolved
        if live_knowledge_time is not None and live_knowledge_time < decision_time:
            _increment(reasons, "decision_cutoff_not_reached")
            continue
        target_sports_date = sports_event_date(str(target["event_start_utc"]))
        if target_sports_date != game_date or str(target["sports_event_date"]) != game_date:
            _increment(reasons, "target_date_changed_after_cutoff")
            continue
        required_target = {
            "home_team_id",
            "away_team_id",
            "home_team_canonical_id",
            "away_team_canonical_id",
        }
        if not required_target.issubset(target) or any(not target[key] for key in required_target):
            _increment(reasons, "target_identity_unavailable")
            continue
        home = build_team_form_snapshot(
            games,
            team_box,
            team_id=str(target["home_team_id"]),
            decision_time_utc=decision_time,
        )
        away = build_team_form_snapshot(
            games,
            team_box,
            team_id=str(target["away_team_id"]),
            decision_time_utc=decision_time,
        )
        if not _metrics_complete(home) or not _metrics_complete(away):
            _increment(reasons, "team_form_metrics_unavailable")
            continue
        rows.append(
            _feature_row(
                target,
                horizon=horizon,
                decision_time=decision_time,
                home=home,
                away=away,
            )
        )

    if not rows:
        return WNBAFeatureBuildResult(
            mode,
            game_date,
            horizon,
            [],
            reasons,
            targets.height,
            None,
            None,
            None,
        )

    rows.sort(key=lambda row: str(row["event_id"]))
    frame = pl.DataFrame(rows).select(sorted(pl.DataFrame(rows).columns)).sort("event_id")
    feature_schema_hash = dataframe_schema_hash(frame)
    snapshot_hash = hashlib.sha256(
        canonical_json(
            {
                "game_date": game_date,
                "horizon": horizon,
                "feature_schema_hash": feature_schema_hash,
                "rows": rows,
            }
        )
    ).hexdigest()
    feature_store = FeatureStore(root / "features")
    feature_store.write_snapshot("wnba", horizon, frame, snapshot_hash)
    payload_path = root / "features" / "wnba" / horizon / f"{snapshot_hash}.parquet"
    persisted = pl.read_parquet(payload_path)
    if not frame.equals(persisted):
        raise ValueError(f"persisted WNBA feature snapshot failed verification: {payload_path}")
    schema_path = payload_path.with_suffix(".schema.json")
    _write_schema_manifest(
        schema_path,
        feature_schema_hash=feature_schema_hash,
        columns=frame.columns,
    )
    if ledger is not None and run_id is not None:
        ledger.record_feature_snapshot(
            run_id=run_id,
            sport="wnba",
            horizon=horizon,
            dataset_hash=snapshot_hash,
            decision_time_utc=(str(rows[0]["decision_time_utc"]) if len(rows) == 1 else None),
            feature_schema_version=f"{FEATURE_SCHEMA_VERSION}:{feature_schema_hash}",
            row_count=frame.height,
            payload_path=str(payload_path),
        )
    return WNBAFeatureBuildResult(
        mode,
        game_date,
        horizon,
        rows,
        reasons,
        targets.height,
        snapshot_hash,
        feature_schema_hash,
        str(payload_path),
    )


def build_wnba_replay_features(
    data_root: str | Path,
    game_date: str,
    horizon: str,
    *,
    ledger: Any | None = None,
    run_id: str | None = None,
) -> WNBAFeatureBuildResult:
    return _build(
        data_root,
        game_date,
        horizon,
        mode="replay",
        ledger=ledger,
        run_id=run_id,
    )


def build_wnba_live_features(
    data_root: str | Path,
    game_date: str,
    horizon: str,
    *,
    ledger: Any | None = None,
    run_id: str | None = None,
    knowledge_time_utc: datetime | None = None,
) -> WNBAFeatureBuildResult:
    return _build(
        data_root,
        game_date,
        horizon,
        mode="live",
        ledger=ledger,
        run_id=run_id,
        knowledge_time_utc=knowledge_time_utc,
    )
