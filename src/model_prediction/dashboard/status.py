"""Dashboard status module (extracted from dashboard_server.py, DD-5).

Part of the dashboard_server.py -> dashboard/ package split. See MASTER.md
DD-5 and dashboard/__init__.py for the re-export shim that keeps the old
`dashboard_server` import path and test-suite symbol references working.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None


from model_prediction.dashboard.backtests import (
    odds_summary,
)
from model_prediction.dashboard.common import (
    _LAST_ACTION,
    DATA,
    EASTERN,
    OUTPUTS,
    ROOT,
    SPORTS,
    _cached,
    _count_lines,
    _read_json,
    _unit_value_usd,
)
from model_prediction.dashboard.evidence import (
    production_evidence,
)
from model_prediction.dashboard.jobs import (
    _latest_persisted_action,
)
from model_prediction.dashboard.matrix import (
    _ml_cell,
    _newest_validation,
    _production_artifact,
)

# ── SECTION: Status & Health ────────────────────────────────────────


def _daily_pipeline_status() -> dict:
    """Split-pipeline staleness from data/logs/daily_*.log.

    The split pipeline runs: settle → polymarket slate → flat forecast →
    main forecast (which also forecasts every esports title and any
    non-production learned sport, routing them to research/gated_research
    ledgers). Each step writes its own exit code to the log. Parses the
    latest log to extract per-step status and
    overall staleness.
    """
    logs = sorted((DATA / "logs").glob("daily_*.log"))
    if not logs:
        return {"last_run_at_utc": None, "age_hours": None, "stale": True, "steps": {}}
    latest = logs[-1]
    mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
    age_hours = (datetime.now(UTC) - mtime).total_seconds() / 3600
    # Parse exit codes from the log. Matched on an exact "0", not a substring
    # check, since e.g. exit code 130 (SIGINT) or 120 also contain the digit
    # "0" and would otherwise be misreported as success.
    steps: dict[str, bool] = {}
    unified_ok: bool | None = None
    try:
        text = latest.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if "Settlement exit code:" in line:
                steps["settle_ok"] = line.split(":")[-1].strip() == "0"
            elif "Ingestion exit code:" in line:
                steps["ingest_ok"] = line.split(":")[-1].strip() == "0"
            elif "Polymarket slate exit code:" in line:
                steps["slate_ok"] = line.split(":")[-1].strip() == "0"
            elif "Flat forecast exit code:" in line:
                steps["flat_ok"] = line.split(":")[-1].strip() == "0"
            elif "Main forecast exit code:" in line:
                steps["main_ok"] = line.split(":")[-1].strip() == "0"
            elif "Unified daily exit code:" in line:
                unified_ok = line.split(":")[-1].strip() == "0"
    except OSError:
        pass
    # Unified pipeline (2026-08-05): slate + flat + main run as one step.
    # When the unified exit code is present, use it as fallback for any
    # step the log didn't report individually.
    if unified_ok is not None:
        for key in ("slate_ok", "flat_ok", "main_ok"):
            if key not in steps:
                steps[key] = unified_ok
    return {
        "last_run_at_utc": mtime.isoformat(),
        "age_hours": round(age_hours, 1),
        "stale": age_hours > 6,
        "steps": steps,
    }


def _data_inventory() -> tuple[dict[str, int], dict[str, str | None], dict[str, str]]:
    """Count each sport from its real storage layout and report refresh provenance."""
    data_counts: dict[str, int] = {}
    last_ingest: dict[str, str | None] = {}
    data_sources: dict[str, str] = {}
    for sport in SPORTS:
        if sport in {"lol", "cs2", "dota2", "valorant", "rainbow_six"}:
            data_path = DATA / "esports" / sport / "matches.jsonl"
            manifest = _read_json(DATA / "esports" / sport / "manifest.json") or {}
            data_counts[sport] = _count_lines(data_path)
            last_ingest[sport] = str(manifest.get("extracted_at_utc") or "")[:10] or None
        elif sport in {"kbo", "npb"}:
            data_path = DATA / "international_baseball" / sport / "games.jsonl"
            manifest = _read_json(DATA / "international_baseball" / sport / "manifest.json") or {}
            data_counts[sport] = _count_lines(data_path)
            last_ingest[sport] = str(manifest.get("extracted_at_utc") or "")[:10] or None
        else:
            data_path = DATA / "historical" / f"{sport}_games_all.jsonl"
            data_counts[sport] = _count_lines(data_path)
            raw_dir = DATA / "raw" / sport
            dates = sorted(d.name for d in raw_dir.iterdir() if d.is_dir()) if raw_dir.exists() else []
            last_ingest[sport] = dates[-1] if dates else None
        data_sources[sport] = str(data_path.relative_to(ROOT))
    return data_counts, last_ingest, data_sources


def status() -> dict:
    validation, _source = _newest_validation()
    termination = _read_json(OUTPUTS / "termination-audit-2026-07-17.json") or {}
    audits = sorted(OUTPUTS.glob("termination-audit-*.json"))
    if audits:
        termination = _read_json(audits[-1]) or termination
    models = sorted(path.name for path in (ROOT / "config" / "models").glob("*.json"))
    data_counts, last_ingest, data_sources = _data_inventory()
    audit_events = _count_lines(DATA / "events.jsonl")
    last_event = None
    events_path = DATA / "events.jsonl"
    if events_path.exists():
        with events_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_event = line
        try:
            last_event = json.loads(last_event) if last_event else None
        except json.JSONDecodeError:
            last_event = None
    alerts = []
    today = datetime.now(UTC).astimezone(EASTERN).date()
    for sport, day in last_ingest.items():
        if day:
            age = (today - datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).date()).days
            if age > 30:
                alerts.append(
                    {"level": "warn", "kind": "data_stale", "text": f"{sport.upper()} data is {age} days old"}
                )

    # Check qualification using the same artifact fallback as _ml_cell and matrix().
    # kbo/npb are zero-unit research baselines tracked via baseball_grid, checked
    # separately below. lol/cs2 are now shadow_qualified production.
    sports_meta = validation.get("sports") or {}
    for sport in ("mlb", "nba", "wnba", "nfl", "soccer", "lol", "cs2", "dota2", "valorant"):
        meta = sports_meta.get(sport) or {}
        artifact = _production_artifact(validation, sport)
        ml = _ml_cell(meta, artifact)
        if ml.get("state") == "tested_not_qualified":
            alerts.append(
                {
                    "level": "warn",
                    "kind": "not_qualified",
                    "text": f"{sport.upper()} moneyline below qualification gate",
                }
            )
        if ml.get("state") == "no_data":
            alerts.append(
                {
                    "level": "info",
                    "kind": "no_data",
                    "text": f"{sport.upper()} moneyline has no validation data",
                }
            )

    for sport, grid_key in (("kbo", "baseball_grid"), ("npb", "baseball_grid")):
        research_ml = ((validation.get(grid_key) or {}).get(sport) or {}).get("moneyline") or {}
        if not research_ml or research_ml.get("state") == "no_data":
            alerts.append(
                {
                    "level": "info",
                    "kind": "no_data",
                    "text": f"{sport.upper()} moneyline has no validation data",
                }
            )

    if not os.environ.get("POLYMARKET_KEY_ID") or not os.environ.get("POLYMARKET_SECRET_KEY"):
        alerts.append(
            {
                "level": "error",
                "kind": "no_api_key",
                "text": "Polymarket API key not configured — execution disabled",
            }
        )

    daily_pipeline = _daily_pipeline_status()
    if daily_pipeline["stale"]:
        detail = (
            f"{daily_pipeline['age_hours']}h since last daily run"
            if daily_pipeline["age_hours"] is not None
            else "no daily log found"
        )
        alerts.append(
            {"level": "warn", "kind": "daily_pipeline_stale", "text": f"Daily pipeline is stale — {detail}"}
        )

    tests = _LAST_ACTION.get("run_tests") or _latest_persisted_action("run_tests")
    # Real bug fixed 2026-08-02: promotion_allowed used to be read straight
    # off a static termination-audit-*.json snapshot (whatever the latest
    # dated file on disk happened to say, sometimes weeks stale), completely
    # independent of /api/production-evidence's own live computation --
    # so the two endpoints could (and did) directly contradict each other:
    # one screen said "allowed", the authoritative evidence calculation said
    # "not established". promotion_allowed is now the live intersection of
    # every model's real evidence (production_evidence()'s own
    # all_production_evidence_valid, itself an AND over each model's
    # artifact/lineage validity and prospective performance completeness)
    # and operational health (no error-level alert active) -- any incomplete
    # input yields False, not a stale flag sitting next to a contradicting panel.
    evidence = _cached("production-evidence", 30, production_evidence)
    operational_checks_green = not any(alert.get("level") == "error" for alert in alerts)
    promotion_allowed = bool(evidence.get("all_production_evidence_valid")) and operational_checks_green
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "models_loaded": len(models),
        "model_artifacts": models,
        "data_counts": data_counts,
        "last_ingest": last_ingest,
        "data_sources": data_sources,
        "audit_events": audit_events,
        "last_audit_event": (last_event or {}).get("event_type") if last_event else None,
        "daily_pipeline": daily_pipeline,
        "alerts": alerts,
        "tests": tests or {"status": "not_run_this_session"},
        "validation_status": termination.get("status") or validation.get("status"),
        "promotion_allowed": promotion_allowed,
        "polymarket_odds": odds_summary(),
        "polymarket_configured": bool(
            os.environ.get("POLYMARKET_KEY_ID") and os.environ.get("POLYMARKET_SECRET_KEY")
        ),
        "edge_filter_min": 0.02,
        "unit_value_usd": _unit_value_usd(),
    }


def _clv_summary(sport: str | None = None) -> dict:
    """Calculate rolling 30-day closing-line value (CLV) and beat rate across sports."""
    from model_prediction.dashboard.picks import read_flat_picks, read_picks
    from model_prediction.pricing import implied_probability

    picks = read_flat_picks() or read_picks()
    if sport:
        picks = [
            p for p in picks if str(p.get("sport") or p.get("league") or "").casefold() == sport.casefold()
        ]

    clv_records = []
    for p in picks:
        dec_odds = p.get("decision_american_odds") or p.get("american_odds")
        close_odds = p.get("closing_american_odds")
        if dec_odds is not None and close_odds is not None:
            try:
                dec_p = implied_probability(int(float(dec_odds)))
                close_p = implied_probability(int(float(close_odds)))
                clv_delta = close_p - dec_p
                clv_records.append(
                    {
                        "event_id": p.get("event_id"),
                        "date": str(p.get("event_start_utc") or p.get("created_at_utc") or "")[:10],
                        "sport": p.get("sport") or p.get("league"),
                        "decision_prob": round(dec_p, 4),
                        "closing_prob": round(close_p, 4),
                        "clv_pct": round(clv_delta * 100, 2),
                        "beat_close": clv_delta > 0,
                    }
                )
            except (ValueError, TypeError):
                continue

    n = len(clv_records)
    if n == 0:
        return {"count": 0, "mean_clv_pct": 0.0, "beat_close_rate": 0.0, "series": []}

    mean_clv = sum(r["clv_pct"] for r in clv_records) / n
    beat_rate = sum(1 for r in clv_records if r["beat_close"]) / n

    return {
        "count": n,
        "mean_clv_pct": round(mean_clv, 2),
        "beat_close_rate": round(beat_rate, 4),
        "series": clv_records[-50:],
    }


def _capture_health_summary() -> dict:
    """Report prospective BBO snapshot capture health and freshness by sport."""
    odds_root = DATA / "odds"
    summary_by_sport: dict = {}
    if odds_root.exists():
        for sport_dir in odds_root.iterdir():
            if sport_dir.is_dir():
                sport = sport_dir.name
                date_dirs = sorted([d for d in sport_dir.iterdir() if d.is_dir()])
                recent_dirs = date_dirs[-7:] if date_dirs else []
                total_snapshots = 0
                for d in recent_dirs:
                    snap_file = d / "polymarket_snapshots.jsonl"
                    if snap_file.exists():
                        total_snapshots += _count_lines(snap_file)
                summary_by_sport[sport] = {
                    "recent_dates_captured": len(recent_dirs),
                    "latest_date": recent_dirs[-1].name if recent_dirs else None,
                    "7day_snapshot_count": total_snapshots,
                    "status": "healthy" if recent_dirs and total_snapshots > 0 else "inactive",
                }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "sports": summary_by_sport,
    }


def _drawdown_summary(sport: str | None = None) -> dict:
    """Calculate realized cumulative P&L curve, peak high water mark, and maximum drawdown."""
    from model_prediction.dashboard.picks import read_flat_picks, read_picks

    picks = read_picks() or read_flat_picks()
    if sport:
        picks = [
            p for p in picks if str(p.get("sport") or p.get("league") or "").casefold() == sport.casefold()
        ]

    settled_picks = [p for p in picks if str(p.get("status") or "").lower() == "settled"]
    settled_picks.sort(key=lambda p: str(p.get("event_start_utc") or p.get("created_at_utc") or ""))

    cumulative_pnl = 0.0
    high_water_mark = 0.0
    max_drawdown_units = 0.0
    series: list[dict] = []

    for p in settled_picks:
        try:
            pnl = float(p.get("pnl_units") or 0.0)
        except (ValueError, TypeError):
            pnl = 0.0
        cumulative_pnl += pnl
        high_water_mark = max(high_water_mark, cumulative_pnl)
        drawdown = high_water_mark - cumulative_pnl
        max_drawdown_units = max(max_drawdown_units, drawdown)

        series.append(
            {
                "date": str(p.get("event_start_utc") or p.get("created_at_utc") or "")[:10],
                "pnl": round(pnl, 4),
                "cumulative_pnl": round(cumulative_pnl, 4),
                "high_water_mark": round(high_water_mark, 4),
                "drawdown": round(drawdown, 4),
            }
        )

    current_drawdown = high_water_mark - cumulative_pnl if settled_picks else 0.0

    return {
        "total_settled_picks": len(settled_picks),
        "total_pnl_units": round(cumulative_pnl, 2),
        "high_water_mark_units": round(high_water_mark, 2),
        "max_drawdown_units": round(max_drawdown_units, 2),
        "current_drawdown_units": round(current_drawdown, 2),
        "series": series[-100:],
    }
