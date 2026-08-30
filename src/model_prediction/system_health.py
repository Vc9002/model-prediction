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
- **DEGRADED** — any registered model failed contract validation (a
  deliberately *disabled* entry is reported, not degraded — see the
  registry check); the
  latest run of a worker was skipped (overlap) or that worker has never
  run under the supervisor; the last recorded prediction is older than
  ``health.max_data_age_minutes``; a currently-serving sport's market
  price is a single repeated value (a hardcoded fallback, not a quote);
  or a sport's source capture stopped (had recent activity, then nothing
  for 7+ days / 2+ days for market snapshots).
- **HEALTHY** — none of the above.

Offseason sports (no games for weeks) are informational, not alarms: the
capture staleness flag only fires for a sport that was recently active.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
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
_STALE_OPEN_WARN_HOURS = 24.0
_STALE_OPEN_HARD_HOURS = 72.0
_SERVED_VALUE_WINDOW_DAYS = 14.0
_SERVED_VALUE_MIN_ROWS = 10


def _stale_open_rows(paths: RuntimePaths) -> dict[str, Any]:
    """Open rows far past their frozen start with no settlement resolution.

    Postponed/rescheduled games expose this class: a re-dated game never
    completes under its original event_id, and the non-ESPN settlement
    paths (tennis, KBO/NPB, edge ledger, ESPN-dropped events) can pend
    forever. This makes the 2026-08-24 audit's manual "24 open rows past
    start" count continuously visible. Drain-minimal by design: a pure
    read-only query of the canonical ledger the daily already maintains —
    health never polls the network.
    """
    if not paths.ledgers_db.is_file():
        return {"available": False, "open_over_24h": 0, "open_over_72h": 0}
    connection = sqlite3.connect(f"file:{paths.ledgers_db}?mode=ro", uri=True, timeout=10.0)
    connection.row_factory = sqlite3.Row
    cutoff_24 = (datetime.now(UTC) - timedelta(hours=_STALE_OPEN_WARN_HOURS)).isoformat()
    cutoff_72 = (datetime.now(UTC) - timedelta(hours=_STALE_OPEN_HARD_HOURS)).isoformat()
    try:
        by_sport = {
            str(row["sport"]): int(row["n"])
            for row in connection.execute(
                """
                SELECT sport, COUNT(*) AS n FROM ledger_records
                WHERE status = 'open' AND event_start_utc < ?
                GROUP BY sport ORDER BY n DESC
                """,
                (cutoff_24,),
            )
        }
        open_over_72h = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM ledger_records
                WHERE status = 'open' AND event_start_utc < ?
                """,
                (cutoff_72,),
            ).fetchone()[0]
        )
        samples = [
            dict(row)
            for row in connection.execute(
                """
                SELECT pick_id, sport, event_start_utc, market_type
                FROM ledger_records
                WHERE status = 'open' AND event_start_utc < ?
                ORDER BY event_start_utc ASC
                LIMIT 10
                """,
                (cutoff_24,),
            )
        ]
    finally:
        connection.close()
    return {
        "available": True,
        "open_over_24h": sum(by_sport.values()),
        "open_over_72h": open_over_72h,
        "by_sport": by_sport,
        "samples": samples,
    }


def _degenerate_served_values(paths: RuntimePaths, serving_sports: set[str] | None = None) -> dict[str, Any]:
    """Sports whose served market price is a single repeated value.

    This is the check that would have caught the 2026-08-29 NCAAF defect on
    the first cycle. Three market-probability variables were declared and
    never assigned, so every call priced against a hardcoded constant instead
    of a real ask; all 32 rows carried market_probability 0.5 while every
    other sport showed 5-53 distinct values. The full verification stack was
    green the entire time -- 2,462 tests, 0 ruff, 0 mypy -- because nothing
    asserts anything about the *distribution* of served values, only their
    type and range. A constant where a distribution belongs is invisible to
    every static check and obvious in one GROUP BY against real output.

    Deliberately narrow: only recent rows (a long-dead sport's frozen history
    is not news), only sports with enough rows for one value to be meaningful,
    only sports a currently-enabled model still serves (a demoted sport's bad
    rows are history, and an alarm that no action can clear is the failure
    mode this whole check exists to avoid repeating), and only the market
    price -- a model probability can legitimately repeat, a market price
    essentially cannot.
    """
    if not paths.ledgers_db.is_file():
        return {"available": False, "degenerate": []}
    connection = sqlite3.connect(f"file:{paths.ledgers_db}?mode=ro", uri=True, timeout=10.0)
    connection.row_factory = sqlite3.Row
    cutoff = (datetime.now(UTC) - timedelta(days=_SERVED_VALUE_WINDOW_DAYS)).isoformat()
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT sport,
                       COUNT(market_probability) AS n,
                       COUNT(DISTINCT market_probability) AS distinct_values,
                       MIN(market_probability) AS value
                FROM ledger_records
                WHERE created_at_utc >= ? AND market_probability IS NOT NULL
                GROUP BY sport
                """,
                (cutoff,),
            )
        ]
    finally:
        connection.close()
    degenerate = [
        {
            "sport": str(row["sport"]),
            "rows": int(row["n"]),
            "distinct_market_probabilities": int(row["distinct_values"]),
            "value": float(row["value"]),
        }
        for row in rows
        if int(row["n"]) >= _SERVED_VALUE_MIN_ROWS
        and int(row["distinct_values"]) <= 1
        and (serving_sports is None or str(row["sport"]).casefold() in serving_sports)
    ]
    return {
        "available": True,
        "window_days": _SERVED_VALUE_WINDOW_DAYS,
        "min_rows": _SERVED_VALUE_MIN_ROWS,
        "by_sport": {
            str(row["sport"]): int(row["distinct_values"])
            for row in sorted(rows, key=lambda r: str(r["sport"]))
        },
        "degenerate": sorted(degenerate, key=lambda item: item["sport"]),
    }


def _ledger_economics(paths: RuntimePaths) -> dict[str, Any]:
    """Check that every graded binary row has a coherent scoring basis.

    A process-level green run cannot establish this invariant: the Flat MLB
    confidence downgrade once wrote ``units=0``/``pnl=0`` and the dashboard
    independently rendered a suggested size, so the scheduler succeeded while
    the ledger told two contradictory economic stories. SQLite is canonical;
    inspect it directly and treat research scoring columns as a separate basis.
    """
    if not paths.ledgers_db.is_file():
        return {"available": False, "semantic_errors": 0, "examples": []}
    connection = sqlite3.connect(f"file:{paths.ledgers_db}?mode=ro", uri=True, timeout=10.0)
    connection.row_factory = sqlite3.Row
    try:
        counts = connection.execute(
            """
            WITH scored AS (
              SELECT ledger_tier, sport, pick_id, result,
                     COALESCE(units, 0.0) AS ledger_units,
                     pnl_units AS ledger_pnl,
                     CAST(COALESCE(NULLIF(json_extract(decision_payload_json,
                         '$.research_score_units'), ''), 0.0) AS REAL) AS research_units,
                     CAST(NULLIF(json_extract(decision_payload_json,
                         '$.research_pnl_units'), '') AS REAL) AS research_pnl,
                     json_extract(decision_payload_json, '$.reason_code') AS reason_code
              FROM ledger_records
              WHERE status = 'settled' AND result IN ('win', 'loss')
            ), effective AS (
              SELECT *,
                     CASE WHEN ledger_units > 0 THEN ledger_units ELSE research_units END AS effective_units,
                     CASE WHEN ledger_units > 0 THEN ledger_pnl ELSE research_pnl END AS effective_pnl
              FROM scored
            )
            SELECT
              SUM(CASE WHEN effective_units <= 0 THEN 1 ELSE 0 END) AS unscored_results,
              SUM(CASE WHEN effective_units > 0 AND result = 'win'
                        AND (effective_pnl IS NULL OR effective_pnl <= 0) THEN 1 ELSE 0 END)
                  AS win_pnl_errors,
              SUM(CASE WHEN effective_units > 0 AND result = 'loss'
                        AND (effective_pnl IS NULL OR ABS(effective_pnl + effective_units) > 0.00011)
                       THEN 1 ELSE 0 END) AS loss_pnl_errors
            FROM effective
            """
        ).fetchone()
        summary = {
            "unscored_results": int(counts["unscored_results"] or 0),
            "win_pnl_errors": int(counts["win_pnl_errors"] or 0),
            "loss_pnl_errors": int(counts["loss_pnl_errors"] or 0),
        }
        examples = [
            dict(row)
            for row in connection.execute(
                """
                WITH effective AS (
                  SELECT ledger_tier, sport, pick_id, result,
                         json_extract(decision_payload_json, '$.reason_code') AS reason_code,
                         CASE WHEN COALESCE(units, 0.0) > 0 THEN units
                              ELSE CAST(COALESCE(NULLIF(json_extract(decision_payload_json,
                                  '$.research_score_units'), ''), 0.0) AS REAL) END AS effective_units,
                         CASE WHEN COALESCE(units, 0.0) > 0 THEN pnl_units
                              ELSE CAST(NULLIF(json_extract(decision_payload_json,
                                  '$.research_pnl_units'), '') AS REAL) END AS effective_pnl
                  FROM ledger_records
                  WHERE status = 'settled' AND result IN ('win', 'loss')
                )
                SELECT ledger_tier, sport, pick_id, result, reason_code,
                       effective_units, effective_pnl
                FROM effective
                WHERE effective_units <= 0
                   OR (effective_units > 0 AND result = 'win'
                       AND (effective_pnl IS NULL OR effective_pnl <= 0))
                   OR (effective_units > 0 AND result = 'loss'
                       AND (effective_pnl IS NULL OR ABS(effective_pnl + effective_units) > 0.00011))
                ORDER BY ledger_tier, sport, pick_id
                LIMIT 10
                """
            )
        ]
    finally:
        connection.close()
    semantic_errors = sum(summary.values())
    return {"available": True, **summary, "semantic_errors": semantic_errors, "examples": examples}


def _get_clv_summary() -> dict:
    try:
        import importlib

        mod = importlib.import_module("model_prediction.dashboard.status")
        res = mod._clv_summary()
        return dict(res) if isinstance(res, dict) else {}
    except (ImportError, AttributeError, KeyError, ValueError, TypeError, OSError):
        return {}


def _status_transitioned(runtime_root: Path, status: str) -> bool:
    """True iff `status` differs from the last-recorded status, and records it.

    Persisted under runtime_root so debounce survives process restarts
    (each dashboard poll / cron run is a fresh Python process).
    """
    state_file = runtime_root / "system_health_last_status.txt"
    try:
        last = state_file.read_text(encoding="utf-8").strip() if state_file.exists() else None
    except OSError:
        last = None
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(status, encoding="utf-8")
    except OSError:
        pass
    return last != status


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
            d.name for d in sport_dir.iterdir() if d.is_dir() and d.name[:4].isdigit() and "-" in d.name
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


from .market_health import market_relative_health


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
    # problem_entries() is "disabled OR failed contract validation", and those
    # are different events: a disabled entry is a deliberate demotion (the
    # three NCAAF models, 2026-08-30), a failed one is a broken artifact.
    # Reporting both as failures made a demotion read as permanent DEGRADED
    # with the reason "failed: None" -- an alarm nobody can act on is an alarm
    # that trains people to ignore the real one. Entries stay declared while
    # disabled precisely so their artifacts keep being hash-verified, so a
    # disabled entry with a load_error is still a genuine failure.
    failed = [entry for entry in registry.problem_entries() if entry.load_error is not None]
    disabled = [entry for entry in registry.problem_entries() if entry.load_error is None]
    registry_check: dict[str, Any] = {
        "primary": registry.primary.model_id,
        "models": {
            entry.model_id: (
                "ok"
                if entry.available
                else (f"failed: {entry.load_error}" if entry.load_error else "disabled")
            )
            for entry in registry.entries.values()
        },
        "disabled": sorted(entry.model_id for entry in disabled),
        "blocked_workflows": registry.blocked_workflows,
    }
    report["checks"]["registry"] = registry_check
    if failed:
        degrade(f"{len(failed)} production model(s) failed contract validation")
    if registry.blocked_workflows:
        degrade(
            f"{len(registry.blocked_workflows)} active workflow(s) are explicitly blocked "
            "from production serving"
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
        "age_minutes": (round(_age_minutes(last_prediction), 1) if last_prediction else None),
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
            if values and (any(not 0.0 <= v <= 1.0 for v in values) or abs(sum(values) - 1.0) > 1e-6):
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

    # 5. Canonical ledger economics. A settled win/loss must have a real
    # scoring basis and sign-consistent P&L; scheduler exit codes cannot prove
    # this semantic invariant.
    ledger_economics = _ledger_economics(paths)
    report["checks"]["ledger_economics"] = ledger_economics

    stale_open = _stale_open_rows(paths)
    report["checks"]["stale_open_rows"] = stale_open
    if stale_open.get("open_over_72h", 0) > 0:
        degrade(
            f"{stale_open['open_over_72h']} open ledger rows are more than 72h past "
            "their scheduled start with no settlement resolution (postponed or "
            "rescheduled events never complete under their original event_id)"
        )
    served_values = _degenerate_served_values(
        paths, {str(entry.sport).casefold() for entry in registry.available_entries()}
    )
    report["checks"]["served_values"] = served_values
    for finding in served_values.get("degenerate", []):
        degrade(
            f"{finding['sport']} served {finding['rows']} rows with market_probability "
            f"constant at {finding['value']:.4f} -- a market price that never varies is a "
            "hardcoded fallback, not a quote"
        )
    if ledger_economics["semantic_errors"]:
        degrade(
            f"canonical ledger has {ledger_economics['semantic_errors']} "
            "settled row(s) with incoherent size/P&L"
        )

    # 6. Rolling Closing Line Value (CLV) health monitoring
    clv_data = _get_clv_summary()
    if clv_data:
        report["checks"]["clv"] = clv_data
        clv_count = clv_data.get("count", 0)
        mean_clv = clv_data.get("mean_clv_pct", 0.0)
        if clv_count >= 20 and mean_clv < -1.0:
            degrade(f"Rolling 30-day CLV negative ({mean_clv:.2f}%) across {clv_count} graded picks")

    # Phase-23 market-relative evidence (wired 2026-08-27): read-only
    # battery from settled ledger rows. Informational by design — it
    # never flips health status by itself; the operator reads it.
    try:
        report["market_relative"] = market_relative_health(paths)
    except Exception as exc:  # noqa: BLE001 — evidence must never crash health
        report["market_relative"] = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}

    report["reasons"] = reasons
    return report


def main() -> int:
    print(json.dumps(system_health(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
