"""Evidence-based system health (consolidation A-3, part 1).

Health is derived from what the system actually produced — registry
contracts, supervisor run rows, prediction records, and source capture —
not from a file's mtime or a stub that always says "fresh". The report
tells you *why* something is stale:

    python -m model_prediction.system_health

Status rules (most severe wins):

- **DOWN** — the primary model's contract cannot resolve, or the most
  recent supervisor run for any worker failed (the scheduler path is
  broken until a newer run succeeds).
- **DEGRADED** — any registered model failed contract validation; the
  latest run of a worker was skipped (overlap) or that worker has never
  run under the supervisor; the last recorded prediction is older than
  ``health.max_data_age_minutes``; or a sport's source capture stopped
  (had recent activity, then nothing for 7+ days / 2+ days for market
  snapshots).
- **HEALTHY** — none of the above.

Offseason sports (no games for weeks) are informational, not alarms: the
capture staleness flag only fires for a sport that was recently active.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .production_registry import ProductionModelRegistry
from .production_store import (
    read_latest_prediction_utc,
    read_recent_probabilities,
)
from .run_supervisor import WORKERS, RunSupervisor
from .runtime_paths import RuntimePaths

_CAPTURE_STALE_DAYS = 7.0
_MARKET_STALE_DAYS = 2.0
_RECENT_ACTIVITY_DAYS = 21.0


def _age_minutes(iso_utc: str) -> float:
    try:
        dt = datetime.fromisoformat(iso_utc)
    except ValueError:
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (datetime.now(UTC) - dt).total_seconds() / 60.0


def _latest_event_utc(path: Path, field: str) -> str | None:
    """Most recent event timestamp in an append-only JSONL file.

    The files are ordered by INGEST time, not event time — backfilled
    batches (e.g. the 2026-08-14 reconciliation of 31 games from
    2026-07-19/21) land at the end long after newer events were appended,
    so the last line is not the newest event. ISO-8601 UTC strings compare
    lexicographically, so a max() scan is exact.
    """
    if not path.is_file():
        return None
    latest: str | None = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = str(json.loads(line).get(field) or "")
            except json.JSONDecodeError:
                continue
            if value and (latest is None or value > latest):
                latest = value
    return latest


def _source_capture(repo_root: Path) -> dict[str, Any]:
    """Latest game timestamp per sport from the historical files."""
    capture: dict[str, Any] = {}
    for sport in ("mlb", "nba", "wnba", "nfl", "soccer", "tennis"):
        path = repo_root / "data" / "historical" / f"{sport}_games_all.jsonl"
        latest_utc = _latest_event_utc(path, "event_start_utc")
        age_minutes = _age_minutes(latest_utc) if latest_utc else float("inf")
        capture[sport] = {
            "latest_event_utc": latest_utc,
            "age_hours": round(age_minutes / 60, 1) if latest_utc else None,
            "stale": False,
        }
    return capture


def _market_capture(repo_root: Path) -> dict[str, Any]:
    """Latest Polymarket snapshot date per sport from data/odds/."""
    markets: dict[str, Any] = {}
    odds_root = repo_root / "data" / "odds"
    if not odds_root.is_dir():
        return markets
    for sport_dir in sorted(p for p in odds_root.iterdir() if p.is_dir()):
        dates = sorted(
            d.name
            for d in sport_dir.iterdir()
            if d.is_dir() and d.name[:4].isdigit() and "-" in d.name
        )
        latest = dates[-1] if dates else None
        markets[sport_dir.name] = {
            "latest_snapshot_date": latest,
            "stale": False,
        }
    return markets


def _flag_capture_staleness(capture: dict[str, Any]) -> list[str]:
    """Flag sports whose capture stopped 7-28 days ago.

    Nothing within the last 7 days but something within the last 28 days
    means the sport was recently active and has gone quiet — a real
    staleness signal. Older than that is presumed offseason (NBA/NFL in
    summer) and stays informational.
    """
    flags: list[str] = []
    for sport, info in capture.items():
        latest = info.get("latest_event_utc")
        if not latest:
            continue
        age_hours = info.get("age_hours") or 0.0
        quiet = age_hours > _CAPTURE_STALE_DAYS * 24
        recently_active = age_hours <= (_RECENT_ACTIVITY_DAYS + _CAPTURE_STALE_DAYS) * 24
        info["stale"] = quiet and recently_active
        if info["stale"]:
            flags.append(f"{sport} game capture stale ({age_hours / 24:.1f} days)")
    return flags


def _flag_market_staleness(markets: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    for sport, info in markets.items():
        latest = info.get("latest_snapshot_date")
        if not latest:
            continue
        try:
            dt = datetime.fromisoformat(latest)
        except ValueError:
            continue
        age_days = (datetime.now(UTC).date() - dt.date()).days
        info["stale"] = age_days > _MARKET_STALE_DAYS
        if info["stale"]:
            flags.append(f"{sport} market snapshots stale ({age_days} days)")
    return flags


def system_health(
    repo_root: Path | str | None = None,
    runtime_root: Path | str | None = None,
) -> dict[str, Any]:
    """Run the full evidence-based health check and aggregate a status."""
    root = Path(repo_root) if repo_root is not None else PROJECT_ROOT
    # One resolution for ALL mutable state — the same RuntimePaths every
    # writer uses, so health can never read a different file than the
    # workers write (the 2026-08-13 split-brain lesson).
    paths = (
        RuntimePaths(repo_root=root, runtime_root=Path(runtime_root))
        if runtime_root is not None
        else RuntimePaths.resolve(repo_root=root, require_external_runtime=True)
    )

    report: dict[str, Any] = {
        "status": "HEALTHY",
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "reasons": [],
        "checks": {},
    }
    reasons: list[str] = []

    def degrade(reason: str) -> None:
        reasons.append(reason)
        # Only escalate from HEALTHY — a DOWN verdict stays DOWN no matter
        # how many degrade-level findings follow it.
        if report["status"] == "HEALTHY":
            report["status"] = "DEGRADED"

    def down(reason: str) -> None:
        reasons.append(reason)
        report["status"] = "DOWN"

    # 1. Registry contracts (A-1): every registered production model.
    try:
        registry = ProductionModelRegistry.load(root)
    except Exception as exc:  # noqa: BLE001
        down(f"production registry failed to load: {exc}")
        report["reasons"] = reasons
        return report
    registry_check: dict[str, Any] = {
        "primary": registry.primary.model_id,
        "models": {
            entry.model_id: (
                "ok" if entry.available else f"failed: {entry.load_error}"
            )
            for entry in registry.entries.values()
        },
    }
    report["checks"]["registry"] = registry_check
    if registry.problem_entries():
        degrade(
            f"{len(registry.problem_entries())} production model(s) "
            "failed contract validation"
        )

    # 2. Supervisor run rows (A-2): latest run per worker.
    supervisor = RunSupervisor(db_path=paths.runs_db, paths=paths)
    try:
        runs_check: dict[str, Any] = {}
        for worker in WORKERS:
            rows = supervisor.latest_runs(worker=worker, limit=1)
            if not rows:
                runs_check[worker] = {"latest": None}
                degrade(f"worker '{worker}' has never run under the supervisor")
                continue
            row = rows[0]
            summary = {
                "status": row["status"],
                "started_at_utc": row["started_at_utc"],
                "exit_code": row["exit_code"],
                "age_hours": round(_age_minutes(row["started_at_utc"]) / 60, 1),
                "note": row["note"],
            }
            runs_check[worker] = summary
            if row["status"] == "failed":
                down(f"worker '{worker}' last run failed (exit {row['exit_code']})")
            elif row["status"] == "skipped":
                degrade(f"worker '{worker}' last run was skipped (lease held)")
        report["checks"]["runs"] = runs_check
    finally:
        supervisor.close()

    # 3. Prediction freshness + normalization: the canonical production
    #    DATABASE, never the legacy production_state.json (item 12 — one
    #    operational truth, one storage).
    last_prediction = read_latest_prediction_utc(paths)
    report["checks"]["predictions"] = {
        "latest_prediction_utc": last_prediction,
        "age_minutes": (
            round(_age_minutes(last_prediction), 1) if last_prediction else None
        ),
    }
    max_age_minutes = float(registry.health.get("max_data_age_minutes", 120))
    if last_prediction is None:
        degrade("no production prediction runs recorded in production.db")
    elif _age_minutes(last_prediction) > max_age_minutes:
        degrade(
            f"last production prediction "
            f"{_age_minutes(last_prediction):.0f} minutes ago "
            f"(max_data_age_minutes={max_age_minutes:.0f})"
        )
    if registry.health.get("require_probability_normalization", True):
        for pair in read_recent_probabilities(paths, limit=20):
            values = [v for v in pair.values() if isinstance(v, (int, float))]
            if values and (
                any(not 0.0 <= v <= 1.0 for v in values)
                or abs(sum(values) - 1.0) > 1e-6
            ):
                down(f"stored prediction probabilities not normalized: {pair}")

    # 4. Source capture: historical games + Polymarket snapshots.
    capture = _source_capture(root)
    flags = _flag_capture_staleness(capture)
    for flag in flags:
        degrade(flag)
    report["checks"]["game_capture"] = capture

    markets = _market_capture(root)
    market_flags = _flag_market_staleness(markets)
    for flag in market_flags:
        degrade(flag)
    report["checks"]["market_capture"] = markets

    report["reasons"] = reasons
    return report


def main() -> int:
    print(json.dumps(system_health(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
