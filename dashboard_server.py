#!/usr/bin/env python3
"""Local operations dashboard server for the model-prediction system.

Serves dashboard.html plus a small JSON API computed from the project's data
files. View clearing and order status use local dashboard state. Real order
submission remains behind the model-prediction CLI's hard gate and a separate,
exact-ticket confirmation in the UI.

Run:  python3 dashboard_server.py  [--port 8765]
Then open http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import yaml

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None

ROOT = Path(__file__).resolve().parent

# Load .env file so subprocess CLI commands inherit Polymarket keys
_ENV_PATH = ROOT / ".env"
if _ENV_PATH.exists():
    with _ENV_PATH.open(encoding="utf-8") as _fh:
        for _line in _fh:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                if _key.strip() and _key.strip() not in os.environ:
                    os.environ[_key.strip()] = _val.strip()

DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs" / "latest"
DASH_DIR = ROOT / "dashboard"
LOG_FILE = DASH_DIR / "server.log"
JOBS_FILE = DASH_DIR / "jobs.json"
ARCHIVE_FILE = DASH_DIR / "archive.json"
ORDERS_FILE = DASH_DIR / "orders.json"
PORTFOLIO_HISTORY_FILE = DASH_DIR / "portfolio_history.json"
CONFIG_FILE = ROOT / "config" / "model.yaml"


def _log(message: str) -> None:
    try:
        DASH_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()[:19]
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass
EASTERN = ZoneInfo("America/New_York")
SPORTS = ("mlb", "nba", "wnba", "nfl", "soccer")
GATEWAY = "https://gateway.polymarket.us"

_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_LOCK = threading.Lock()
_ACTION_LOCK = threading.Lock()
_LAST_ACTION: dict[str, object] = {}
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_RUNNER: list[str] | None = None
_ORDER_PREVIEWS: dict[str, dict] = {}
_ORDER_LOCK = threading.Lock()


def _resolve_runner() -> list[str]:
    """Find a Python that can actually import model_prediction.

    The dashboard is often started with the system python3, while the package
    lives in the project's .venv — so try the venv first, then whatever is
    running this server, then a `model-prediction` executable on PATH.
    """
    global _RUNNER
    if _RUNNER is not None:
        return _RUNNER
    candidates = [
        str(ROOT / ".venv" / "bin" / "python"),
        str(ROOT / ".venv" / "Scripts" / "python.exe"),  # windows
        sys.executable,
    ]
    for python in candidates:
        if not Path(python).exists():
            continue
        try:
            probe = subprocess.run(
                [python, "-c", "import model_prediction"],
                cwd=ROOT, capture_output=True, text=True, timeout=30,
                env=_runner_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue  # e.g. a venv built on another OS/architecture
        if probe.returncode == 0:
            _RUNNER = [python, "-m", "model_prediction.cli"]
            return _RUNNER
    import shutil

    binary = shutil.which("model-prediction")
    if binary:
        _RUNNER = [binary]
        return _RUNNER
    raise RuntimeError(
        "no Python environment with model_prediction installed was found. "
        "Create it with: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
    )


def _runner_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _cached(key: str, ttl: float, builder):
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = builder()
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
    return value


# ---------------------------------------------------------------------------
# Readers (all read-only)
# ---------------------------------------------------------------------------


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _unit_value_usd() -> float:
    try:
        payload = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        value = float((payload.get("bankroll") or {}).get("unit_value_usd", 7.5))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return 7.5
    return value if value > 0 else 7.5


def _config_payload() -> dict:
    try:
        payload = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _row_has_banned_team(row: dict) -> bool:
    config = _config_payload()
    configured = (config.get("team_ban_list") or {}).get("teams") or {}
    banned_ids = {
        str(item.get("canonical_team_id") or "")
        for values in configured.values()
        for item in (values or [])
        if isinstance(item, dict)
    }
    registry = _read_json(DATA / "entities" / "teams.json") or {}
    entries = registry.get("teams") if isinstance(registry, dict) else registry
    banned_names: set[str] = set()
    for entry in entries or []:
        if str(entry.get("canonical_team_id") or "") not in banned_ids:
            continue
        banned_names.add(str(entry.get("canonical_name") or "").strip().casefold())
        for alias in entry.get("aliases") or []:
            if isinstance(alias, dict):
                banned_names.add(str(alias.get("source_name") or "").strip().casefold())
            else:
                banned_names.add(str(alias or "").strip().casefold())
    return any(
        str(row.get(key) or "").strip().casefold() in banned_names
        for key in ("away_team", "home_team")
    )


def _manual_research_eligibility(row: dict) -> tuple[bool, str]:
    if row.get("record_type") != "RESEARCH_OBSERVATION":
        return False, "not a research observation"
    config = _config_payload()
    execution = config.get("execution") or {}
    if not execution.get("allow_manual_research_orders", False):
        return False, "manual research orders are disabled"
    league = str(row.get("league") or "").upper()
    active_version = (config.get("models", {}).get(league, {}) or {}).get(
        "active_production_version"
    )
    if execution.get("manual_research_require_active_model", True):
        if not active_version or row.get("model_version") != active_version:
            return False, "pick is not from the active production model"
    edge = _number(row.get("model_probability")) - _number(
        row.get("market_implied_probability")
    )
    if execution.get("manual_research_require_positive_edge", True) and edge <= 0:
        return False, "model has no positive edge at the logged market price"
    if _row_has_banned_team(row):
        return False, "a team in this matchup is permanently banned"
    return True, "manual active-model order"


_PICKS_CACHE: dict[str, object] = {"mtime": None, "rows": []}


def read_picks() -> list[dict]:
    """Parse picks.xlsx only when its mtime changes — parsing is the single
    most expensive repeated operation the dashboard performs."""
    path = DATA / "picks.xlsx"
    if load_workbook is None or not path.exists():
        return []
    mtime = path.stat().st_mtime
    with _CACHE_LOCK:
        if _PICKS_CACHE["mtime"] == mtime:
            return _PICKS_CACHE["rows"]  # type: ignore[return-value]
    rows_out = _parse_picks(path)
    with _CACHE_LOCK:
        _PICKS_CACHE["mtime"] = mtime
        _PICKS_CACHE["rows"] = rows_out
    return rows_out


def _parse_picks(path: Path) -> list[dict]:
    wb = load_workbook(path, read_only=True, data_only=True)
    sheet = wb["Picks"] if "Picks" in wb.sheetnames else wb.active
    rows = sheet.iter_rows(values_only=True)
    try:
        headers = [str(h) if h is not None else "" for h in next(rows)]
    except StopIteration:
        return []
    keep = [
        "pick_id", "created_at_utc", "event_start_utc", "event_id", "league",
        "away_team", "home_team", "market_type", "selection", "line",
        "american_odds", "market_implied_probability", "model_probability",
        "model_uncertainty", "edge", "confidence_score", "units",
        "model_version", "status", "result", "away_score", "home_score",
        "probability_clv", "pnl_units", "settled_at_utc", "record_type",
        "decision", "reason_code", "research_score_units", "research_pnl_units",
        "sportsbook", "decision_no_vig_probability",
    ]
    index = {name: headers.index(name) for name in keep if name in headers}
    picks = []
    for values in rows:
        if values is None or all(value is None for value in values):
            continue
        row = {name: values[position] for name, position in index.items()}
        if not row.get("pick_id"):
            continue
        picks.append(row)
    wb.close()
    return picks


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pick_probability(row) -> float | None:
    value = _number(row.get("model_probability"), -1)
    return value if 0 < value < 1 else None


def _pick_pnl(row) -> float:
    """Unit P&L: real units if staked, else retrospective research scoring."""
    units = _number(row.get("units"))
    if units > 0:
        return _number(row.get("pnl_units"))
    if row.get("research_score_units"):
        return _number(row.get("research_pnl_units"))
    return 0.0


def performance(picks: list[dict]) -> dict:
    settled = [row for row in picks if row.get("status") == "settled"
               and row.get("result") in ("win", "loss")]
    settled.sort(key=lambda row: str(row.get("settled_at_utc") or ""))
    wins = sum(1 for row in settled if row["result"] == "win")
    cumulative, curve = 0.0, []
    for row in settled:
        cumulative += _pick_pnl(row)
        curve.append({
            "t": str(row.get("settled_at_utc") or "")[:16],
            "pnl": round(cumulative, 4),
        })
    by_sport, by_market, by_bucket, by_month = {}, {}, {}, {}
    buckets = (("0.50-0.55", 0.50, 0.55), ("0.55-0.60", 0.55, 0.60),
               ("0.60-0.65", 0.60, 0.65), ("0.65+", 0.65, 1.01))
    for row in settled:
        won = row["result"] == "win"
        for table, key in ((by_sport, str(row.get("league"))),
                           (by_market, str(row.get("market_type")))):
            entry = table.setdefault(key, {"wins": 0, "calls": 0, "pnl": 0.0})
            entry["calls"] += 1
            entry["wins"] += won
            entry["pnl"] = round(entry["pnl"] + _pick_pnl(row), 4)
        p = _pick_probability(row)
        if p is not None:
            p_side = max(p, 1 - p)
            for name, lo, hi in buckets:
                if lo <= p_side < hi:
                    entry = by_bucket.setdefault(name, {"wins": 0, "calls": 0})
                    entry["calls"] += 1
                    entry["wins"] += won
        month = str(row.get("settled_at_utc") or "")[:7]
        if month:
            entry = by_month.setdefault(month, {"pnl": 0.0, "calls": 0, "wins": 0})
            entry["pnl"] = round(entry["pnl"] + _pick_pnl(row), 4)
            entry["calls"] += 1
            entry["wins"] += won
    # calibration: 10 buckets predicted vs actual
    calibration = []
    for index in range(10):
        lo, hi = index / 10, (index + 1) / 10
        members = [row for row in settled if (p := _pick_probability(row)) is not None
                   and (lo <= p < hi or (hi == 1.0 and p == 1.0))]
        if members:
            calibration.append({
                "bucket": f"{lo:.1f}-{hi:.1f}",
                "mean_p": round(sum(_pick_probability(m) for m in members) / len(members), 4),
                "hit_rate": round(sum(1 for m in members if m["result"] == "win") / len(members), 4),
                "count": len(members),
            })
    # streaks
    current = longest_w = longest_l = 0
    run_w = run_l = 0
    for row in settled:
        if row["result"] == "win":
            run_w, run_l = run_w + 1, 0
        else:
            run_l, run_w = run_l + 1, 0
        longest_w, longest_l = max(longest_w, run_w), max(longest_l, run_l)
    current = run_w if run_w else -run_l
    clv = [_number(row.get("probability_clv"), None) for row in settled
           if row.get("probability_clv") not in (None, "")]
    clv = [value for value in clv if value is not None]
    staked = sum(max(_number(row.get("units")), _number(row.get("research_score_units")))
                 for row in settled)
    total_pnl = round(sum(_pick_pnl(row) for row in settled), 4)
    return {
        "total_picks": len(picks),
        "settled": len(settled),
        "open": sum(1 for row in picks if row.get("status") == "open"),
        "wins": wins,
        "losses": len(settled) - wins,
        "win_rate": round(wins / len(settled), 4) if settled else None,
        "total_pnl": total_pnl,
        "roi": round(total_pnl / staked, 4) if staked else None,
        "pnl_curve": curve,
        "by_sport": by_sport,
        "by_market": by_market,
        "by_confidence": {name: table for name, table in by_bucket.items()},
        "by_month": dict(sorted(by_month.items())),
        "calibration": calibration,
        "streaks": {"longest_win": longest_w, "longest_loss": longest_l, "current": current},
        "mean_clv": round(sum(clv) / len(clv), 6) if clv else None,
    }


def status() -> dict:
    validation = _read_json(OUTPUTS / "termination-audit-2026-07-17.json") or {}
    audits = sorted(OUTPUTS.glob("termination-audit-*.json"))
    if audits:
        validation = _read_json(audits[-1]) or validation
    models = sorted(path.name for path in (ROOT / "config" / "models").glob("*.json"))
    data_counts, last_ingest = {}, {}
    for sport in SPORTS:
        data_counts[sport] = _count_lines(DATA / "historical" / f"{sport}_games_all.jsonl")
        raw_dir = DATA / "raw" / sport
        dates = sorted(d.name for d in raw_dir.iterdir() if d.is_dir()) if raw_dir.exists() else []
        last_ingest[sport] = dates[-1] if dates else None
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
    today = datetime.now(timezone.utc).astimezone(EASTERN).date()
    for sport, day in last_ingest.items():
        if day:
            age = (today - datetime.strptime(day, "%Y-%m-%d").date()).days
            if age > 1:
                alerts.append({"level": "warn", "kind": "data_stale",
                               "text": f"{sport.upper()} ingest is {age} days old"})
    results = (validation.get("results") or {})
    for sport, row in results.items():
        if isinstance(row, dict) and row.get("qualified") is False:
            alerts.append({"level": "info", "kind": "not_qualified",
                           "text": f"{sport.upper()} below qualification gate"})
    tests = _LAST_ACTION.get("run_tests")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models_loaded": len(models),
        "model_artifacts": models,
        "data_counts": data_counts,
        "last_ingest": last_ingest,
        "audit_events": audit_events,
        "last_audit_event": (last_event or {}).get("event_type") if last_event else None,
        "alerts": alerts,
        "tests": tests or {"status": "not_run_this_session"},
        "validation_status": validation.get("status"),
        "promotion_allowed": validation.get("promotion_allowed"),
        "polymarket_odds": odds_summary(),
        "polymarket_configured": bool(os.environ.get("POLYMARKET_PRIVATE_KEY")),
        "edge_filter_min": 0.02,
        "unit_value_usd": _unit_value_usd(),
    }


MATRIX_SPORTS = ("mlb", "nba", "wnba", "nfl", "soccer")


def _newest_validation() -> tuple[dict, str]:
    """Newest learned-model-validation*.json merged with soccer-validation.json."""
    candidates = sorted(
        OUTPUTS.glob("learned-model-validation*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    merged: dict = {"sports": {}, "production_artifacts": {}}
    sources = []
    if candidates:
        newest = _read_json(candidates[-1]) or {}
        merged["sports"].update(newest.get("sports") or {})
        merged["production_artifacts"].update(newest.get("production_artifacts") or {})
        sources.append(candidates[-1].name)
    soccer = _read_json(OUTPUTS / "soccer-validation.json") or {}
    if soccer.get("sports"):
        merged["sports"].update(soccer["sports"])
        merged["production_artifacts"].update(soccer.get("production_artifacts") or {})
        sources.append("soccer-validation.json")
    return merged, " + ".join(sources)


def _ml_cell(sport_meta: dict) -> dict:
    """Moneyline cell from the production variant's primary_65 locked holdout."""
    variants = sport_meta.get("variants") or {}
    variant = variants.get("elo_trend") or next(
        (v for v in variants.values() if isinstance(v, dict) and v.get("primary_65")), None
    )
    primary = (variant or {}).get("primary_65") or {}
    holdout = primary.get("locked_holdout") or {}
    if not holdout:
        return {"state": "no_data"}
    return {
        "state": "qualified" if holdout.get("qualified") else "tested_not_qualified",
        "hit_rate": holdout.get("hit_rate"),
        "calls": holdout.get("calls"),
        "units": holdout.get("units_at_minus_110"),
        "brier": holdout.get("brier_score"),
        "threshold": primary.get("learned_threshold"),
        "roi": holdout.get("roi"),
    }


def matrix() -> dict:
    validation, source = _newest_validation()
    total_validation = _read_json(OUTPUTS / "total-score-validation.json") or {}
    spread_validation = _read_json(OUTPUTS / "spread-validation.json") or {}
    total_results = total_validation.get("sports") or {}
    spread_results = spread_validation.get("sports") or {}
    sports_meta = validation.get("sports") or {}
    markets = ["moneyline", "spread", "total", "f5_spread", "f5_total", "yrfi_nrfi"]
    grid = {}
    for sport in MATRIX_SPORTS:
        row = {}
        meta = sports_meta.get(sport) or {}
        row["moneyline"] = _ml_cell(meta)
        readiness = meta.get("multi_market_readiness") or {}
        spread_key = "full_game_spread" if sport == "mlb" else "spread"
        total_key = "full_game_total" if sport == "mlb" else "total"

        # Spread cell
        sp_model = spread_results.get(sport) or {}
        if sp_model.get("status") == "research_score_model_candidate":
            qual = (sp_model.get("market_qualification") or {}).get("reason", "")
            state = "qualified" if qual == "qualified" else "research_spread_candidate"
            row["spread"] = {
                "state": state,
                "mae": (sp_model.get("locked_holdout") or {}).get("mae"),
                "calls": (sp_model.get("holdout") or {}).get("calls"),
                "hit_rate": (sp_model.get("holdout") or {}).get("hit_rate"),
                "holdout_rows": (sp_model.get("training") or {}).get("holdout_rows"),
                "qualification": qual,
            }
        else:
            spread_readiness = readiness.get(spread_key)
            row["spread"] = {
                "state": "blocked" if spread_readiness else "no_data",
                "readiness": spread_readiness,
            }
        score_model = total_results.get(sport) or {}
        holdout = score_model.get("locked_holdout") or {}
        if score_model.get("status") == "research_score_model_candidate":
            qual = (score_model.get("market_qualification") or {}).get("reason", "")
            state = "qualified" if qual == "qualified" else "research_total_candidate"
            called_holdout = score_model.get("holdout") or {}
            row["total"] = {
                "state": state,
                "mae": holdout.get("mae"),
                "baseline_mae": holdout.get("baseline_mae"),
                "mae_gain": holdout.get("mae_gain_vs_rolling_league_mean"),
                "mae_gain_interval": holdout.get("mae_gain_95pct_interval"),
                "holdout_rows": score_model.get("holdout_observations")
                or (score_model.get("training") or {}).get("holdout_rows"),
                "train_rows": score_model.get("train_observations"),
                "validation_rows": score_model.get("validation_observations"),
                "readiness": readiness.get(total_key),
                "qualification": qual,
                "calls": called_holdout.get("calls"),
                "hit_rate": called_holdout.get("hit_rate"),
                "brier": called_holdout.get("brier"),
                "reference_line": score_model.get("reference_line"),
                "threshold": score_model.get("threshold"),
                "model": score_model.get("model"),
            }
        else:
            total_readiness = readiness.get(total_key)
            row["total"] = {
                "state": "blocked" if total_readiness else "no_data",
                "readiness": total_readiness,
            }
        for market in ("f5_spread", "f5_total", "yrfi_nrfi"):
            row[market] = {"state": "no_data" if sport == "mlb" else "not_applicable"}
        grid[sport] = row
    gate = validation and (
        "locked holdout hit rate >= 65% target (learned threshold), >= 60% floor, >= 50 calls"
    )
    return {"markets": markets, "grid": grid, "source": source, "gate": gate}


def backtests() -> list[dict]:
    items = []
    if OUTPUTS.exists():
        for path in sorted(OUTPUTS.glob("*.json")):
            payload = _read_json(path) or {}
            items.append({
                "file": path.name,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                .isoformat()[:16],
                "status": payload.get("status"),
                "sport": payload.get("sport") or ",".join(payload.get("sports_scope", [])[:4]),
                "keys": sorted(payload)[:12],
                "size_kb": round(path.stat().st_size / 1024, 1),
            })
    return items


def odds_summary(sport: str | None = None) -> dict:
    """Per-sport summary of stored Polymarket odds snapshots for today."""
    today = _today()
    sports = [sport] if sport else list(SPORTS)
    result: dict = {}
    for s in sports:
        path = DATA / "odds" / s / today / "polymarket_snapshots.jsonl"
        if not path.exists():
            result[s] = {"snapshots": 0, "status": "no_data"}
            continue
        snaps = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    snaps.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        moneyline = [s for s in snaps if s.get("market_type") == "moneyline"]
        spread = [s for s in snaps if s.get("market_type") == "spread"]
        total = [s for s in snaps if s.get("market_type") == "total"]
        with_bbo = [s for s in snaps
                    if (s.get("long") or {}).get("ask") is not None
                    and (s.get("short") or {}).get("ask") is not None]
        result[s] = {
            "snapshots": len(snaps),
            "moneyline": len(moneyline),
            "spread": len(spread),
            "total": len(total),
            "with_executable_bbo": len(with_bbo),
            "date": today,
        }
    return result if sport is None else result.get(sport, {})


def market_snapshots(sport: str, day: str) -> dict:
    path = DATA / "odds" / sport / day / "polymarket_snapshots.jsonl"
    latest: dict[str, dict] = {}
    first_ask: dict[str, float] = {}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue
                slug = snap.get("market_slug")
                if not slug:
                    continue
                ask = ((snap.get("long") or {}).get("ask"))
                if slug not in first_ask and ask is not None:
                    first_ask[slug] = ask
                latest[slug] = snap
    rows = []
    for slug, snap in sorted(latest.items()):
        long_side = snap.get("long") or {}
        ask, bid = long_side.get("ask"), long_side.get("bid")
        rows.append({
            "market_slug": slug,
            "market_type": snap.get("market_type"),
            "line": snap.get("line"),
            "league": snap.get("league"),
            "event_start_utc": snap.get("event_start_utc"),
            "description": long_side.get("description"),
            "bid": bid, "ask": ask,
            "spread": round(ask - bid, 4) if ask is not None and bid is not None else None,
            "ask_size": long_side.get("ask_size"), "bid_size": long_side.get("bid_size"),
            "move": round(ask - first_ask[slug], 4)
            if ask is not None and slug in first_ask else None,
            "observed_at_utc": snap.get("observed_at_utc"),
        })
    return {"sport": sport, "date": day, "markets": rows, "count": len(rows)}


def _team_matches(team_name: str, side_description: str) -> bool:
    team = " ".join(team_name.casefold().split())
    description = " ".join(side_description.casefold().split())
    if not team or not description:
        return False
    if team == description:
        return True
    shorter, longer = (
        (description, team) if len(description) <= len(team) else (team, description)
    )
    return f" {shorter} " in f" {longer} "


def _pick_quote(row: dict) -> dict | None:
    """Latest exact stored executable side quote for a full-game moneyline pick."""
    if row.get("market_type") != "moneyline":
        return None
    sport = str(row.get("league") or "").lower()
    if sport not in SPORTS:
        return None
    try:
        event_start = datetime.fromisoformat(
            str(row.get("event_start_utc") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return None
    day = event_start.astimezone(EASTERN).date().isoformat()
    path = DATA / "odds" / sport / day / "polymarket_snapshots.jsonl"
    if not path.exists():
        return None
    latest: dict[str, dict] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                snapshot = json.loads(line)
            except json.JSONDecodeError:
                continue
            if snapshot.get("market_type") != "moneyline":
                continue
            slug = str(snapshot.get("market_slug") or "")
            if not slug or any(
                marker in slug.casefold()
                for marker in ("-f5-", "-f3-", "-f7-", "-1st-", "-h1-", "-h2-")
            ):
                continue
            long_description = str((snapshot.get("long") or {}).get("description") or "")
            short_description = str((snapshot.get("short") or {}).get("description") or "")
            away = str(row.get("away_team") or "")
            home = str(row.get("home_team") or "")
            if not (
                (_team_matches(away, long_description) and _team_matches(home, short_description))
                or (_team_matches(home, long_description) and _team_matches(away, short_description))
            ):
                continue
            latest[slug] = snapshot
    if not latest:
        return None
    snapshot = max(latest.values(), key=lambda item: str(item.get("observed_at_utc") or ""))
    selected_team = (
        str(row.get("home_team") or "")
        if str(row.get("selection") or "").casefold() == "home"
        else str(row.get("away_team") or "")
    )
    side_name = (
        "long"
        if _team_matches(selected_team, str((snapshot.get("long") or {}).get("description") or ""))
        else "short"
    )
    side = snapshot.get(side_name) or {}
    ask = _number(side.get("ask"), -1)
    if not 0 < ask < 1:
        return None
    observed = str(snapshot.get("observed_at_utc") or "")
    try:
        observed_at = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        age_seconds = max(0, int((datetime.now(timezone.utc) - observed_at).total_seconds()))
    except ValueError:
        age_seconds = 10**9
    return {
        "market_slug": snapshot.get("market_slug"),
        "side": side_name,
        "description": side.get("description"),
        "bid": side.get("bid"),
        "ask": ask,
        "ask_size": side.get("ask_size"),
        "observed_at_utc": observed,
        "age_seconds": age_seconds,
        "fresh": age_seconds <= 300,
        "market_state": snapshot.get("market_state"),
    }


def _load_orders() -> dict:
    payload = _read_json(ORDERS_FILE) or {}
    orders = payload.get("orders") if isinstance(payload, dict) else None
    rows = list(orders) if isinstance(orders, list) else []
    repaired = False
    # Older dashboard builds mixed the CLI confirmation prompt into stdout.
    # The exchange could accept an order and return an ID, while json.loads()
    # rejected the combined prompt + JSON and locally recorded it as refused.
    # Recover those durable exchange acknowledgements so a refresh cannot offer
    # the same model order a second time.
    for row in rows:
        if row.get("status") != "refused" or not isinstance(row.get("error"), str):
            continue
        decoded = _decode_command_output(row["error"])
        if decoded.get("status") == "submitted" and decoded.get("order_id"):
            row.update(
                status="submitted",
                order_id=str(decoded["order_id"]),
                order_state=decoded.get("order_state"),
                error=None,
            )
            repaired = True
    result = {"orders": rows}
    if repaired:
        _save_orders(result)
    return result


def _save_orders(payload: dict) -> None:
    DASH_DIR.mkdir(exist_ok=True)
    temporary = ORDERS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, ORDERS_FILE)


def _decode_command_output(raw: str) -> dict:
    """Decode a CLI JSON result even when an interactive prompt precedes it."""
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass
    decoder = json.JSONDecoder()
    best: dict = {}
    for index, character in enumerate(str(raw)):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(str(raw)[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and not str(raw)[index + end :].strip():
            best = value
            break
    return best


def _latest_order_for_pick(row: dict, quote: dict | None) -> dict | None:
    """Find an order across equivalent model-version rows for the same contract side."""
    orders = _load_orders()["orders"]
    pick_id = str(row.get("pick_id") or "")
    direct = [order for order in orders if str(order.get("pick_id") or "") == pick_id]
    if direct:
        return direct[-1]
    if quote is None:
        return None
    equivalent = [
        order
        for order in orders
        if order.get("status") == "submitted"
        and order.get("market_slug") == quote.get("market_slug")
        and order.get("side") == quote.get("side")
    ]
    return equivalent[-1] if equivalent else None


def _dashboard_order_status(exchange_state: str | None) -> str:
    state = str(exchange_state or "").upper()
    return {
        "ORDER_STATE_FILLED": "filled",
        "ORDER_STATE_CANCELED": "canceled",
        "ORDER_STATE_REPLACED": "replaced",
        "ORDER_STATE_REJECTED": "rejected",
        "ORDER_STATE_EXPIRED": "expired",
    }.get(state, "submitted")


def _reconcile_orders() -> None:
    """Replace local submission state with the exchange's current order state."""
    payload = _load_orders()
    active = [
        order
        for order in payload["orders"]
        if order.get("status") == "submitted" and order.get("order_id")
    ]
    if not active:
        return
    order_ids = sorted({str(order["order_id"]) for order in active})

    def fetch_order_states() -> dict:
        try:
            command = _resolve_runner() + ["order-status"]
            for order_id in order_ids:
                command.extend(("--order-id", order_id))
            process = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                env=_runner_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        raw = process.stdout if process.returncode == 0 else process.stderr
        return _decode_command_output(raw)

    result = _cached("order-states:" + ",".join(order_ids), 10, fetch_order_states)
    if result.get("status") != "live":
        return
    by_id = {
        str(item.get("order_id")): item
        for item in result.get("orders", [])
        if item.get("order_id")
    }
    changed = False
    for order in active:
        snapshot = by_id.get(str(order["order_id"]))
        if snapshot is None:
            continue
        exchange_state = snapshot.get("order_state")
        updates = {
            "order_state": exchange_state,
            "status": _dashboard_order_status(exchange_state),
            "cum_quantity": snapshot.get("cum_quantity"),
            "leaves_quantity": snapshot.get("leaves_quantity"),
            "last_checked_at_utc": result.get("observed_at_utc"),
        }
        if any(order.get(key) != value for key, value in updates.items()):
            order.update(updates)
            changed = True
    if changed:
        _save_orders(payload)


def _order_readiness(row: dict, quote: dict | None) -> tuple[bool, str]:
    if row.get("status") != "open":
        return False, "pick is not open"
    if row.get("record_type") == "QUALIFIED_SHADOW_CALL":
        if float(row.get("units") or 0) <= 0:
            return False, "qualified pick has no authorized units"
    elif row.get("record_type") == "RESEARCH_OBSERVATION":
        eligible, reason = _manual_research_eligibility(row)
        if not eligible:
            return False, reason
        if not _suggested_units(row):
            return False, "manual order has no authorized unit cap"
    else:
        return False, "unsupported ledger record type"
    if quote is None:
        return False, "no exact executable Polymarket US market mapping"
    if not quote.get("fresh"):
        return False, "market quote is older than 5 minutes; scan prices first"
    if quote.get("market_state") != "MARKET_STATE_OPEN":
        return False, "market is not open"
    missing = [
        name
        for name in ("POLYMARKET_KEY_ID", "POLYMARKET_SECRET_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        return False, f"missing {' and '.join(missing)}"
    return True, "ready"


def _decorate_pick(row: dict) -> dict:
    quote = _pick_quote(row)
    ready, reason = _order_readiness(row, quote)
    order = _latest_order_for_pick(row, quote)
    manual, _ = _manual_research_eligibility(row)
    return {
        **row,
        "quote": quote,
        "order": order,
        "buy_ready": ready,
        "buy_block_reason": reason,
        "unit_value_usd": _unit_value_usd(),
        "order_authorization": (
            "manual_research_override" if manual else "qualified_model"
        ),
    }


def _pick_identity(row: dict) -> tuple[str, ...]:
    """Canonical dashboard identity, independent of model version or logged price."""
    event = str(row.get("event_id") or "").strip()
    if not event:
        event = "|".join(
            str(row.get(key) or "").strip().casefold()
            for key in ("event_start_utc", "away_team", "home_team")
        )
    line = row.get("line")
    try:
        line_value = f"{float(line):g}" if line not in (None, "") else ""
    except (TypeError, ValueError):
        line_value = str(line or "").strip().casefold()
    return (
        str(row.get("league") or "").strip().casefold(),
        event,
        str(row.get("market_type") or "").strip().casefold(),
        str(row.get("selection") or "").strip().casefold(),
        line_value,
        str(row.get("period") or row.get("horizon") or "").strip().casefold(),
    )


def _dedupe_picks(rows: list[dict]) -> list[dict]:
    """Keep the latest ledger observation for each actual bet shown in the UI."""
    latest: dict[tuple[str, ...], tuple[str, int, dict]] = {}
    for index, row in enumerate(rows):
        rank = (str(row.get("created_at_utc") or ""), index, row)
        key = _pick_identity(row)
        if key not in latest or rank[:2] >= latest[key][:2]:
            latest[key] = rank
    return [item[2] for item in sorted(latest.values(), key=lambda item: item[1])]


def dashboard_picks() -> list[dict]:
    """Latest unique picks with persistent local-clear and order state attached."""
    _reconcile_orders()
    archived = set(_load_archive()["pick_ids"])
    return [
        {
            **_decorate_pick(row),
            "archived": str(row.get("pick_id")) in archived,
            "suggested_paper_units": _suggested_units(row),
        }
        for row in _dedupe_picks(read_picks())
    ]


def preview_order(payload: dict) -> dict:
    action = str(payload.get("action") or "buy").lower()
    if action not in ("buy", "sell"):
        return {"status": "refused", "error": "action must be buy or sell"}
    pick_id = str(payload.get("pick_id") or "")
    row = next((item for item in read_picks() if str(item.get("pick_id")) == pick_id), None)
    if row is None:
        return {"status": "refused", "error": "unknown pick id"}
    decorated = _decorate_pick(row)
    quote = decorated["quote"]
    if quote is None:
        return {"status": "refused", "error": "no executable quote for this contract"}
    # Buys require the buy-readiness gate. Sells are exits and only require an
    # executable quote (you can always try to close a position you hold).
    if action == "buy" and not decorated["buy_ready"]:
        return {"status": "refused", "error": decorated["buy_block_reason"]}
    try:
        price = round(float(payload.get("price")), 2)
        size_shares = float(payload.get("size_shares"))
    except (TypeError, ValueError):
        return {"status": "refused", "error": "price and shares must be numeric"}
    if not 0.01 <= price <= 0.99 or abs(price * 100 - round(price * 100)) > 1e-8:
        return {"status": "refused", "error": "limit price must be a 0.01 tick from 0.01 to 0.99"}
    if not 0 < size_shares <= 100000:
        return {"status": "refused", "error": "shares must be greater than 0 and at most 100,000"}
    estimated_cost = round(price * size_shares, 2)
    manual = row.get("record_type") == "RESEARCH_OBSERVATION"

    if action == "sell":
        # A resting SELL limit must sit AT OR ABOVE the current bid (post-only:
        # do not cross into the bid). No dollar cost cap — a sell returns
        # capital. Proceeds are informational.
        bid = quote.get("bid")
        if bid is not None and price <= float(bid):
            return {
                "status": "refused",
                "error": (
                    f"resting sell price must be above the current bid {float(bid):.2f}; "
                    "crossing orders are blocked"
                ),
            }
        maximum_cost = None
    else:
        # Buy path (unchanged): limit below model prob for manual, cost <= unit cap,
        # rest below the ask.
        if manual and price >= float(row.get("model_probability") or 0):
            return {
                "status": "refused",
                "error": (
                    f"manual buy limit {price:.2f} must stay below the model probability "
                    f"{float(row.get('model_probability') or 0):.2f}"
                ),
            }
        authorized_units = _suggested_units(row) if manual else float(row.get("units") or 0)
        maximum_cost = round(float(authorized_units or 0) * _unit_value_usd(), 2)
        if estimated_cost > maximum_cost + 0.005:
            return {
                "status": "refused",
                "error": (
                    f"order cost ${estimated_cost:.2f} exceeds this pick's "
                    f"{float(row.get('units') or 0):g}U cap (${maximum_cost:.2f})"
                ),
            }
        if price >= float(quote["ask"]):
            return {
                "status": "refused",
                "error": (
                    f"resting buy price must be below the current ask {float(quote['ask']):.2f}; "
                    "crossing orders are blocked"
                ),
            }
    nonce = secrets.token_urlsafe(24)
    ticket = {
        "nonce": nonce,
        "pick_id": pick_id,
        "action": action,
        "market_slug": quote["market_slug"],
        "side": quote["side"],
        "price": price,
        "size_shares": size_shares,
        "units": round(estimated_cost / _unit_value_usd(), 4),
        "unit_value_usd": _unit_value_usd(),
        "estimated_cost_usd": estimated_cost,
        "estimated_proceeds_usd": estimated_cost if action == "sell" else None,
        "maximum_cost_usd": maximum_cost,
        "manual_research_order": manual,
        "created_at": time.time(),
        "expires_at": time.time() + 300,
    }
    with _ORDER_LOCK:
        _ORDER_PREVIEWS[nonce] = ticket
    return {"status": "preview", **ticket}


def submit_order(payload: dict) -> dict:
    nonce = str(payload.get("nonce") or "")
    with _ORDER_LOCK:
        ticket = _ORDER_PREVIEWS.pop(nonce, None)
    if ticket is None or time.time() > float(ticket["expires_at"]):
        return {"status": "refused", "error": "order preview expired; preview it again"}
    row = next(
        (item for item in read_picks() if str(item.get("pick_id")) == ticket["pick_id"]),
        None,
    )
    if row is None:
        return {"status": "refused", "error": "pick disappeared before submission"}
    quote = _pick_quote(row)
    if quote is None or quote["market_slug"] != ticket["market_slug"]:
        return {"status": "refused", "error": "market changed; preview the order again"}
    action = ticket.get("action", "buy")
    if action == "sell":
        bid = quote.get("bid")
        if bid is not None and ticket["price"] <= float(bid):
            return {"status": "refused", "error": "bid moved above your limit; preview the sell again"}
    else:
        ready, reason = _order_readiness(row, quote)
        if not ready:
            return {"status": "refused", "error": reason}
        if ticket["price"] >= float(quote["ask"]):
            return {"status": "refused", "error": "ask changed; preview the order again"}
    command = _resolve_runner() + [
        "execute",
        "--pick-id", ticket["pick_id"],
        "--size-shares", str(ticket["size_shares"]),
        "--price", str(ticket["price"]),
        "--side", ticket["side"],
        "--action", action,
        "--order-type", "limit_gtc",
        "--market-slug", ticket["market_slug"],
        "--execute",
    ]
    if ticket.get("manual_research_order"):
        command.append("--manual-research-order")
    process = subprocess.run(
        command,
        cwd=ROOT,
        input="Y\n",
        capture_output=True,
        text=True,
        timeout=30,
        env=_runner_env(),
    )
    raw = process.stdout if process.returncode == 0 else process.stderr
    result = _decode_command_output(raw)
    if not result:
        result = {"status": "refused", "error": raw[-1000:] or "order command failed"}
    record = {
        **ticket,
        "nonce": None,
        "status": result.get("status", "refused"),
        "order_id": result.get("order_id"),
        "order_state": result.get("order_state"),
        "submitted_at_utc": datetime.now(timezone.utc).isoformat(),
        "error": result.get("error"),
    }
    with _ORDER_LOCK:
        orders = _load_orders()
        orders["orders"].append(record)
        _save_orders(orders)
    with _CACHE_LOCK:
        _CACHE.clear()
    return {**result, "pick_id": ticket["pick_id"]}


def _live_bbo(market_slug: str) -> dict | None:
    """Fetch a fresh BBO for one market slug from the public gateway."""
    try:
        from model_prediction.data_sources.polymarket_us import PolymarketUSClient
        return PolymarketUSClient().snapshot(market_slug)
    except Exception:  # noqa: BLE001 - any failure => no quote, caller handles
        return None


def preview_position_sell(payload: dict) -> dict:
    """Preview a resting SELL limit against a held live exchange position."""
    slug = str(payload.get("market_slug") or "")
    side = str(payload.get("side") or "long")
    if not slug or side not in ("long", "short"):
        return {"status": "refused", "error": "market_slug and side (long|short) are required"}
    try:
        price = round(float(payload.get("price")), 2)
        size_shares = float(payload.get("size_shares"))
    except (TypeError, ValueError):
        return {"status": "refused", "error": "price and shares must be numeric"}
    if not 0.01 <= price <= 0.99 or abs(price * 100 - round(price * 100)) > 1e-8:
        return {"status": "refused", "error": "limit price must be a 0.01 tick from 0.01 to 0.99"}
    if not 0 < size_shares <= 1_000_000:
        return {"status": "refused", "error": "shares must be greater than 0"}
    portfolio = live_portfolio_view()
    position = next(
        (
            item
            for item in (portfolio.get("open") or {}).get("positions", [])
            if item.get("market_slug") == slug and item.get("side") == side
        ),
        None,
    )
    if portfolio.get("status") != "live" or position is None:
        return {"status": "refused", "error": "live position could not be verified"}
    held = _number(position.get("available_quantity"), 0.0)
    if size_shares > held + 1e-9:
        return {
            "status": "refused",
            "error": f"cannot sell {size_shares:g} shares; only {held:g} are available",
        }
    snapshot = _live_bbo(slug)
    bid = None
    if snapshot:
        bid = (snapshot.get(side) or {}).get("bid")
    if bid is not None and price <= float(bid):
        return {
            "status": "refused",
            "error": (
                f"resting sell price must be above the current {side} bid {float(bid):.2f}; "
                "crossing orders are blocked"
            ),
        }
    nonce = secrets.token_urlsafe(24)
    ticket = {
        "nonce": nonce, "kind": "position_sell",
        "market_slug": slug, "side": side, "price": price, "size_shares": size_shares,
        "estimated_proceeds_usd": round(price * size_shares, 2),
        "current_bid": bid, "verified_available_quantity": held,
        "created_at": time.time(), "expires_at": time.time() + 300,
    }
    with _ORDER_LOCK:
        _ORDER_PREVIEWS[nonce] = ticket
    return {"status": "preview", **ticket}


def submit_position_sell(payload: dict) -> dict:
    nonce = str(payload.get("nonce") or "")
    with _ORDER_LOCK:
        ticket = _ORDER_PREVIEWS.pop(nonce, None)
    if ticket is None or ticket.get("kind") != "position_sell" or time.time() > float(ticket["expires_at"]):
        return {"status": "refused", "error": "sell preview expired; preview it again"}
    portfolio = live_portfolio_view()
    position = next(
        (
            item
            for item in (portfolio.get("open") or {}).get("positions", [])
            if item.get("market_slug") == ticket["market_slug"]
            and item.get("side") == ticket["side"]
        ),
        None,
    )
    held = _number((position or {}).get("available_quantity"), 0.0)
    if portfolio.get("status") != "live" or position is None or ticket["size_shares"] > held + 1e-9:
        return {"status": "refused", "error": "available live shares changed; preview the sell again"}
    # Re-check the bid moved-through condition against a fresh quote.
    snapshot = _live_bbo(ticket["market_slug"])
    if snapshot:
        bid = (snapshot.get(ticket["side"]) or {}).get("bid")
        if bid is not None and ticket["price"] <= float(bid):
            return {"status": "refused", "error": "bid moved above your limit; preview the sell again"}
    command = _resolve_runner() + [
        "sell-position",
        "--market-slug", ticket["market_slug"],
        "--side", ticket["side"],
        "--price", str(ticket["price"]),
        "--size-shares", str(ticket["size_shares"]),
        "--execute",
    ]
    process = subprocess.run(
        command, cwd=ROOT, input="Y\n", capture_output=True, text=True, timeout=30,
        env=_runner_env(),
    )
    raw = process.stdout if process.returncode == 0 else process.stderr
    result = _decode_command_output(raw)
    if not result:
        result = {"status": "refused", "error": raw[-1000:] or "sell command failed"}
    record = {**ticket, "nonce": None, "status": result.get("status", "refused"),
              "order_id": result.get("order_id"), "order_state": result.get("order_state"),
              "submitted_at_utc": datetime.now(timezone.utc).isoformat(), "error": result.get("error")}
    with _ORDER_LOCK:
        orders = _load_orders()
        orders["orders"].append(record)
        _save_orders(orders)
    with _CACHE_LOCK:
        _CACHE.clear()
    return result


def live_gateway_slate(sport: str, day: str) -> dict:
    """Read-only live discovery quotes from the public gateway (indicative)."""
    league = {"mlb": "mlb", "nba": "nba", "wnba": "wnba", "nfl": "nfl"}.get(sport)
    if league is None:
        return {"events": []}
    url = f"{GATEWAY}/v2/leagues/{league}/events?limit=50&section=general&type=sport"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode())
    except Exception as error:  # noqa: BLE001
        return {"events": [], "error": type(error).__name__}
    events = []
    for event in payload.get("events", []):
        start = str(event.get("startTime") or "")
        if not start:
            continue
        try:
            start_et = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(EASTERN)
        except ValueError:
            continue
        if start_et.date().isoformat() != day:
            continue
        markets = []
        for market in event.get("markets", []):
            sides = []
            for side in market.get("marketSides", []):
                quote = side.get("quote")
                if isinstance(quote, dict):
                    quote = quote.get("value")
                try:
                    quote = float(quote)
                except (TypeError, ValueError):
                    quote = None
                sides.append({"description": side.get("description"), "quote": quote})
            markets.append({"slug": market.get("slug"),
                            "type": market.get("sportsMarketTypeV2") or market.get("sportsMarketType"),
                            "line": market.get("line"), "sides": sides})
        events.append({"title": event.get("title"), "start_utc": start,
                       "slug": event.get("slug"), "markets": markets})
    return {"events": events, "note": "indicative discovery quotes; decision prices come from stored BBO asks"}


def _action_command(name: str, payload: dict) -> list[str]:
    runner = _resolve_runner()
    if name == "run_tests":
        # pytest lives next to whatever python the runner resolved to.
        python = runner[0] if runner[0].endswith(("python", "python.exe", "python3")) else sys.executable
        return [python, "-m", "pytest", "tests/", "-q", "--no-header"]
    cli = runner if len(runner) > 1 else runner  # module or console-script form
    if name == "daily":
        return cli + ["daily", "--date", str(payload.get("date") or _today())]
    if name == "refresh_prices":
        day = str(payload.get("date") or _today())
        command = cli + ["polymarket-ledger-prices", "--date", day]
        seen: set[tuple[str, str]] = set()
        for row in today_picks(day)["picks"]:
            if row.get("status") != "open":
                continue
            quote = row.get("quote") or {}
            sport = str(row.get("league") or "").strip().lower()
            slug = str(quote.get("market_slug") or "").strip()
            target = (sport, slug)
            if sport not in SPORTS or not slug or target in seen:
                continue
            seen.add(target)
            command += ["--contract", f"{sport}={slug}"]
        return command
    if name == "settle":
        return cli + ["settle", "--all-unsettled"]
    if name == "bootstrap":
        command = cli + [
            "bootstrap", "--sport", _safe_sport(payload.get("sport")),
            "--from", str(payload.get("from_date") or _today()),
        ]
        if payload.get("to_date"):
            command += ["--to", str(payload["to_date"])]
        return command
    raise ValueError(f"unknown action: {name}")


def _today() -> str:
    return datetime.now(timezone.utc).astimezone(EASTERN).date().isoformat()


def _safe_sport(value) -> str:
    if value not in SPORTS:
        raise ValueError(f"unsupported sport: {value}")
    return str(value)


def start_action(name: str, payload: dict) -> dict:
    """Launch a whitelisted action as a background job; return its id at once.

    Actions like `daily` legitimately run for minutes (slate discovery plus
    ~200 BBO snapshots). Holding the HTTP request open that long is what made
    the browser show "Failed to fetch" — so the POST returns immediately and
    the page polls /api/job.
    """
    if not _ACTION_LOCK.acquire(blocking=False):
        running = next((j for j in _JOBS.values() if j["status"] == "running"), None)
        return {"status": "busy",
                "error": "another action is already running",
                "job_id": running["job_id"] if running else None}
    try:
        command = _action_command(name, payload)
    except (ValueError, RuntimeError) as error:
        _ACTION_LOCK.release()
        return {"status": "failed", "error": str(error)}
    job_id = f"{name}-{int(time.time())}"
    job = {
        "job_id": job_id, "action": name, "status": "running",
        "command": " ".join(command[-8:]), "output_tail": "",
        "started_at": datetime.now(timezone.utc).isoformat()[:19],
        "started_monotonic": time.time(), "seconds": 0.0,
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        while len(_JOBS) > 20:
            _JOBS.pop(next(iter(_JOBS)))
    _log(f"job started: {job_id} :: {job['command']}")
    _persist_jobs()

    def _work() -> None:
        started = time.time()
        chunks: list[str] = []
        process = None
        try:
            process = subprocess.Popen(
                command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=_runner_env(),
            )
            assert process.stdout is not None
            for line in process.stdout:
                chunks.append(line)
                if len(chunks) > 4000:
                    chunks = chunks[-2000:]
                with _JOBS_LOCK:
                    job["output_tail"] = "".join(chunks)[-12000:]
                    job["seconds"] = round(time.time() - started, 1)
            returncode = process.wait(timeout=3600)
            status = "ok" if returncode == 0 else "failed"
        except Exception as error:  # noqa: BLE001
            status, returncode = "failed", -1
            chunks.append(f"\n{type(error).__name__}: {error}\n")
        finally:
            # A finished (or failed) job must leave NOTHING behind: kill any
            # still-running child, reap it, and close the pipe.
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                if process.stdout is not None:
                    process.stdout.close()
        with _JOBS_LOCK:
            job.update({
                "status": status, "returncode": returncode,
                "seconds": round(time.time() - started, 1),
                "output_tail": "".join(chunks)[-12000:],
            })
        _LAST_ACTION[name] = dict(job)
        _log(f"job finished: {job_id} :: {status} in {job['seconds']}s")
        _persist_jobs()
        with _CACHE_LOCK:
            _CACHE.clear()
        _ACTION_LOCK.release()

    threading.Thread(target=_work, daemon=True).start()
    return {"status": "started", "job_id": job_id, "command": job["command"]}


def job_status(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            # Server may have restarted mid-job; answer from the on-disk record
            # instead of a bare 404 so the page can explain what happened.
            disk = _load_persisted_jobs().get(job_id)
            if disk:
                if disk.get("status") == "running":
                    disk["status"] = "interrupted"
                    disk["error"] = (
                        "the dashboard server restarted while this job was running; "
                        "the underlying CLI run may still have completed — check the ledger/summary"
                    )
                return disk
            return {"status": "unknown", "error": "no such job"}
        snapshot = {key: value for key, value in job.items() if key != "started_monotonic"}
        if snapshot["status"] == "running":
            snapshot["seconds"] = round(time.time() - job["started_monotonic"], 1)
        return snapshot


def _persist_jobs() -> None:
    try:
        DASH_DIR.mkdir(exist_ok=True)
        with _JOBS_LOCK:
            payload = {
                job_id: {k: v for k, v in job.items() if k != "started_monotonic"}
                for job_id, job in _JOBS.items()
            }
        JOBS_FILE.write_text(json.dumps(payload, default=str), encoding="utf-8")
    except OSError:
        pass


def _load_persisted_jobs() -> dict:
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload, content_type="application/json", code=200) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(
            payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        route = parsed.path
        try:
            if route in ("/", "/dashboard.html"):
                page = (ROOT / "dashboard.html")
                if page.exists():
                    self._send(page.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send({"error": "dashboard.html missing"}, code=404)
            elif route == "/api/status":
                self._send(_cached("status", 30, status))
            elif route == "/api/matrix":
                self._send(_cached("matrix", 60, matrix))
            elif route == "/api/picks":
                self._send(_cached("picks", 30, dashboard_picks))
            elif route == "/api/performance":
                self._send(_cached("performance", 30, lambda: performance(read_picks())))
            elif route == "/api/backtests":
                self._send(_cached("backtests", 60, backtests))
            elif route == "/api/backtest":
                name = Path(query.get("file", "")).name
                path = OUTPUTS / name
                if path.exists() and path.suffix == ".json":
                    self._send(path.read_bytes())
                else:
                    self._send({"error": "not found"}, code=404)
            elif route == "/api/market":
                sport = _safe_sport(query.get("sport", "mlb"))
                day = query.get("date") or _today()
                self._send(_cached(f"market:{sport}:{day}", 60,
                                   lambda: market_snapshots(sport, day)))
            elif route == "/api/live":
                sport = _safe_sport(query.get("sport", "mlb"))
                day = query.get("date") or _today()
                self._send(_cached(f"live:{sport}:{day}", 120,
                                   lambda: live_gateway_slate(sport, day)))
            elif route == "/api/audit":
                self._send(_cached("audit", 60, _audit_tail))
            elif route == "/api/job":
                self._send(job_status(str(query.get("id", ""))))
            elif route == "/api/today":
                day = query.get("date") or _today()
                self._send(_cached(f"today:{day}", 20, lambda: today_picks(day)))
            elif route == "/api/odds":
                sport = query.get("sport")
                self._send(_cached(f"odds:{sport or 'all'}", 30,
                                   lambda: odds_summary(sport if sport else None)))
            elif route == "/api/open":
                self._send(_cached("open", 15, open_picks))
            elif route == "/api/history":
                days = int(query.get("days", "30"))
                sport = query.get("sport")
                self._send(_cached(f"history:{days}:{sport or 'all'}", 30,
                                   lambda: history_picks(days, sport)))
            elif route == "/api/bets":
                self._send(_cached("bets", 15, bets_view))
            elif route == "/api/orders":
                self._send(_load_orders())
            elif route == "/api/health":
                self._send({"ok": True, "at": datetime.now(timezone.utc).isoformat()[:19]})
            else:
                self._send({"error": "unknown route"}, code=404)
        except Exception as error:  # noqa: BLE001
            self._send({"error": f"{type(error).__name__}: {error}"}, code=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        if payload.get("confirm") is not True:
            self._send({"status": "refused",
                        "error": "confirmation required: resend with confirm=true"}, code=400)
            return
        if parsed.path == "/api/archive":
            action = str(payload.get("action"))
            scope = payload.get("pick_ids") if action == "clear_ids" else str(payload.get("scope", ""))
            self._send(archive_action(action, scope or []))
        elif parsed.path == "/api/dedupe":
            self._send(dedupe_ledger())
        elif parsed.path == "/api/action":
            self._send(start_action(str(payload.get("action")), payload))
        elif parsed.path == "/api/order/preview":
            self._send(preview_order(payload))
        elif parsed.path == "/api/order/preview-position":
            self._send(preview_position_sell(payload))
        elif parsed.path == "/api/order/submit-position":
            self._send(submit_position_sell(payload))
        elif parsed.path == "/api/order/submit":
            self._send(submit_order(payload))
        else:
            self._send({"error": "unknown route"}, code=404)


def today_picks(day: str) -> dict:
    """Latest unique, locally visible picks played on a US-Eastern date."""
    rows = []
    archived = set(_load_archive()["pick_ids"])
    for row in _dedupe_picks(read_picks()):
        if str(row.get("pick_id")) in archived:
            continue
        start = row.get("event_start_utc")
        if not start:
            continue
        try:
            start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        except ValueError:
            continue
        start_et = start_dt.astimezone(EASTERN)
        if start_et.date().isoformat() != day:
            continue
        rows.append({**_decorate_pick(row), "start_et": start_et.strftime("%I:%M %p ET"),
                     "start_sort": start_dt.isoformat(),
                     "suggested_paper_units": _suggested_units(row)})
    rows.sort(key=lambda r: (r["start_sort"], str(r.get("league")), str(r.get("market_type"))))
    return {
        "date": day,
        "picks": rows,
        "count": len(rows),
        "open": sum(1 for r in rows if r.get("status") == "open"),
        "settled": sum(1 for r in rows if r.get("status") == "settled"),
    }


def open_picks() -> dict:
    """All open ledger picks with model probability, market odds, and edge."""
    rows = []
    for row in read_picks():
        if row.get("status") != "open":
            continue
        model_p = _number(row.get("model_probability"))
        market_p = _number(row.get("market_implied_probability"))
        edge = model_p - market_p if model_p and market_p else None
        rows.append({
            "pick_id": str(row.get("pick_id", "")),
            "league": str(row.get("league", "")),
            "away_team": str(row.get("away_team", "")),
            "home_team": str(row.get("home_team", "")),
            "selection": str(row.get("selection", "")),
            "market_type": str(row.get("market_type", "")),
            "event_start_utc": str(row.get("event_start_utc", "")),
            "model_probability": round(model_p, 4) if model_p else None,
            "market_implied_probability": round(market_p, 4) if market_p else None,
            "edge": round(edge, 4) if edge is not None else None,
            "american_odds": row.get("american_odds"),
            "units": _number(row.get("units")),
            "record_type": str(row.get("record_type", "")),
            "model_version": str(row.get("model_version", "")),
            "reason_code": str(row.get("reason_code", "")),
        })
    # Sort: earliest game first
    rows.sort(key=lambda r: r["event_start_utc"])
    qualified = [r for r in rows if r["record_type"] == "QUALIFIED_SHADOW_CALL"]
    research = [r for r in rows if r["record_type"] == "RESEARCH_OBSERVATION"]
    return {
        "open": rows,
        "count": len(rows),
        "qualified_count": len(qualified),
        "research_count": len(research),
        "total_units": round(sum(r["units"] for r in qualified), 2),
    }


def history_picks(days: int = 30, sport: str | None = None) -> dict:
    """Settled picks within the last N days, optionally filtered by sport."""
    cutoff = datetime.now(timezone.utc)
    rows = []
    for row in read_picks():
        if row.get("status") != "settled":
            continue
        if sport and str(row.get("league", "")).lower() != sport.lower():
            continue
        settled_at = row.get("settled_at_utc")
        if settled_at:
            try:
                settled_dt = datetime.fromisoformat(str(settled_at).replace("Z", "+00:00"))
            except ValueError:
                continue
            if (cutoff - settled_dt).days > days:
                continue
        model_p = _number(row.get("model_probability"))
        market_p = _number(row.get("market_implied_probability"))
        rows.append({
            "pick_id": str(row.get("pick_id", "")),
            "league": str(row.get("league", "")),
            "away_team": str(row.get("away_team", "")),
            "home_team": str(row.get("home_team", "")),
            "selection": str(row.get("selection", "")),
            "market_type": str(row.get("market_type", "")),
            "result": str(row.get("result", "")),
            "away_score": row.get("away_score"),
            "home_score": row.get("home_score"),
            "model_probability": round(model_p, 4) if model_p else None,
            "market_implied_probability": round(market_p, 4) if market_p else None,
            "pnl_units": _number(row.get("pnl_units")),
            "units": _number(row.get("units")),
            "settled_at_utc": str(row.get("settled_at_utc", "")),
            "event_start_utc": str(row.get("event_start_utc", "")),
            "record_type": str(row.get("record_type", "")),
            "american_odds": row.get("american_odds"),
        })
    rows.sort(key=lambda r: r["settled_at_utc"], reverse=True)
    wins = sum(1 for r in rows if r["result"] == "win")
    losses = sum(1 for r in rows if r["result"] == "loss")
    pushes = sum(1 for r in rows if r["result"] == "push")
    total_pnl = sum(r["pnl_units"] for r in rows)
    return {
        "history": rows,
        "count": len(rows),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(wins / (wins + losses), 4) if (wins + losses) else None,
        "total_pnl": round(total_pnl, 4),
        "days": days,
        "sport": sport,
    }


def _amount_value(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _live_model_links() -> dict[tuple[str, str], dict]:
    """Connect exchange contracts to model rows without treating picks as positions."""
    links: dict[tuple[str, str], dict] = {}
    all_rows = read_picks()
    rows_by_id = {str(row.get("pick_id") or ""): row for row in all_rows}

    def _link(row: dict) -> dict:
        return {
            "pick_id": str(row.get("pick_id") or ""),
            "league": str(row.get("league") or ""),
            "away_team": str(row.get("away_team") or ""),
            "home_team": str(row.get("home_team") or ""),
            "selection": str(row.get("selection") or ""),
            "market_type": str(row.get("market_type") or ""),
            "model_probability": _number(row.get("model_probability"), None),
            "model_version": str(row.get("model_version") or ""),
        }

    for row in _dedupe_picks(all_rows):
        quote = _pick_quote(row)
        if quote is None:
            continue
        links[(str(quote["market_slug"]), str(quote["side"]))] = _link(row)
    # An exchange-acknowledged dashboard order links later fills back to the
    # model pick, including a partial fill followed by cancellation.
    for order in _load_orders()["orders"]:
        if order.get("status") not in {"submitted", "filled", "canceled", "replaced"}:
            continue
        row = rows_by_id.get(str(order.get("pick_id") or ""))
        slug = str(order.get("market_slug") or "")
        side = str(order.get("side") or "")
        if row is not None and slug and side:
            links[(slug, side)] = _link(row)
    return links


def _normalize_live_activity(item: dict, links: dict[tuple[str, str], dict]) -> dict | None:
    trade = item.get("trade") if isinstance(item.get("trade"), dict) else None
    resolution = (
        item.get("positionResolution")
        if isinstance(item.get("positionResolution"), dict)
        else None
    )
    if trade:
        slug = str(trade.get("marketSlug") or "")
        occurred = str(trade.get("updateTime") or trade.get("createTime") or "")
        linked = next((value for (market, _), value in links.items() if market == slug), None)
        return {
            "activity_id": f"trade:{trade.get('id') or slug + ':' + occurred}",
            "type": "trade",
            "market_slug": slug,
            "title": str((trade.get("marketMetadata") or {}).get("title") or slug),
            "occurred_at_utc": occurred,
            "price": _amount_value(trade.get("price")),
            "quantity": _number(trade.get("qtyDecimal") or trade.get("qty"), None),
            "cost_basis_usd": _amount_value(trade.get("costBasis")),
            "realized_pnl_usd": _amount_value(trade.get("realizedPnl")),
            "state": str(trade.get("state") or ""),
            "is_aggressor": trade.get("isAggressor"),
            "model_pick": linked,
        }
    if resolution:
        slug = str(resolution.get("marketSlug") or "")
        occurred = str(resolution.get("updateTime") or "")
        before = resolution.get("beforePosition") or {}
        after = resolution.get("afterPosition") or {}
        metadata = after.get("marketMetadata") or before.get("marketMetadata") or {}
        linked = next((value for (market, _), value in links.items() if market == slug), None)
        return {
            "activity_id": f"settlement:{resolution.get('tradeId') or slug + ':' + occurred}",
            "type": "settlement",
            "market_slug": slug,
            "title": str(metadata.get("title") or slug),
            "outcome": str(metadata.get("outcome") or ""),
            "occurred_at_utc": occurred,
            "resolution_side": str(resolution.get("side") or "").removeprefix(
                "POSITION_RESOLUTION_SIDE_"
            ),
            "before_quantity": _number(
                before.get("netPositionDecimal") or before.get("netPosition"), None
            ),
            "after_quantity": _number(
                after.get("netPositionDecimal") or after.get("netPosition"), None
            ),
            "realized_pnl_usd": _amount_value(after.get("realized")),
            "model_pick": linked,
        }
    return None


def _load_portfolio_history() -> dict:
    payload = _read_json(PORTFOLIO_HISTORY_FILE) or {}
    activities = payload.get("activities") if isinstance(payload, dict) else None
    history_start = (
        str(payload.get("history_start_date") or _today())
        if isinstance(payload, dict)
        else _today()
    )
    rows = [
        item
        for item in (list(activities) if isinstance(activities, list) else [])
        if _activity_on_or_after(item, history_start)
    ]
    return {
        "activities": rows,
        "last_synced_at_utc": payload.get("last_synced_at_utc") if isinstance(payload, dict) else None,
        "history_start_date": history_start,
    }


def _activity_on_or_after(item: dict, history_start: str) -> bool:
    try:
        occurred = datetime.fromisoformat(
            str(item.get("occurred_at_utc") or "").replace("Z", "+00:00")
        )
        return occurred.astimezone(EASTERN).date().isoformat() >= history_start
    except ValueError:
        return False


def _save_portfolio_history(activities: list[dict], observed_at: str) -> list[dict]:
    existing = _load_portfolio_history()
    prior = existing["activities"]
    history_start = existing["history_start_date"]
    merged = {
        str(item.get("activity_id")): item
        for item in [*prior, *activities]
        if item.get("activity_id") and _activity_on_or_after(item, history_start)
    }
    rows = sorted(
        merged.values(), key=lambda item: str(item.get("occurred_at_utc") or ""), reverse=True
    )[:2000]
    DASH_DIR.mkdir(exist_ok=True)
    temporary = PORTFOLIO_HISTORY_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "history_start_date": history_start,
                "last_synced_at_utc": observed_at,
                "activities": rows,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, PORTFOLIO_HISTORY_FILE)
    return rows


def _portfolio_history_summary(activities: list[dict], source: str) -> dict:
    trades = [item for item in activities if item.get("type") == "trade"]
    settlements = [item for item in activities if item.get("type") == "settlement"]
    realized = sum(
        value
        for item in activities
        if (value := _number(item.get("realized_pnl_usd"), None)) is not None
    )
    return {
        "activities": activities,
        "count": len(activities),
        "trade_count": len(trades),
        "settlement_count": len(settlements),
        "realized_pnl_usd": round(realized, 2),
        "source": source,
    }


def live_portfolio_view() -> dict:
    """Exchange-confirmed positions and activity; model picks never count as exposure."""
    cached = _load_portfolio_history()
    empty_open = {
        "positions": [],
        "count": 0,
        "cost_basis_usd": 0.0,
        "cash_value_usd": 0.0,
        "realized_pnl_usd": 0.0,
    }
    missing = [
        name
        for name in ("POLYMARKET_KEY_ID", "POLYMARKET_SECRET_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        return {
            "status": "unavailable",
            "error": f"missing {' and '.join(missing)}",
            "open": empty_open,
            "recent_history": _portfolio_history_summary(cached["activities"], "cached"),
            "last_synced_at_utc": cached["last_synced_at_utc"],
            "history_start_date": cached["history_start_date"],
        }
    try:
        process = subprocess.run(
            _resolve_runner() + ["live-portfolio"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env=_runner_env(),
        )
        raw_text = process.stdout if process.returncode == 0 else process.stderr
        raw = json.loads(raw_text)
        if process.returncode != 0 or raw.get("status") != "live":
            raise RuntimeError(str(raw.get("error") or "authenticated portfolio request failed"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, RuntimeError) as error:
        return {
            "status": "unavailable",
            "error": str(error)[:300],
            "open": empty_open,
            "recent_history": _portfolio_history_summary(cached["activities"], "cached"),
            "last_synced_at_utc": cached["last_synced_at_utc"],
            "history_start_date": cached["history_start_date"],
        }

    links = _live_model_links()
    positions = []
    for slug, item in (raw.get("positions") or {}).items():
        net = _number(item.get("netPositionDecimal") or item.get("netPosition"), 0.0)
        if abs(net) < 1e-9:
            continue
        side = "long" if net > 0 else "short"
        metadata = item.get("marketMetadata") or {}
        cost = _amount_value(item.get("cost"))
        cash_value = _amount_value(item.get("cashValue"))
        quote = _live_bbo(str(slug)) or {}
        side_quote = quote.get(side) or {}
        bid = _number(side_quote.get("bid"), None)
        ask = _number(side_quote.get("ask"), None)
        mark = cash_value / abs(net) if cash_value is not None and abs(net) > 0 else None
        exit_default = (
            min(0.99, round(float(bid) + 0.01, 2))
            if bid is not None
            else min(0.99, max(0.01, round(float(mark or 0.5), 2)))
        )
        positions.append(
            {
                "market_slug": str(slug),
                "title": str(metadata.get("title") or slug),
                "outcome": str(metadata.get("outcome") or ""),
                "side": side,
                "quantity": abs(net),
                "available_quantity": abs(
                    _number(item.get("qtyAvailableDecimal") or item.get("qtyAvailable"), 0.0)
                ),
                "cost_basis_usd": cost,
                "cash_value_usd": cash_value,
                "bid": bid,
                "ask": ask,
                "exit_limit_default": exit_default,
                "realized_pnl_usd": _amount_value(item.get("realized")),
                "unrealized_pnl_usd": (
                    round(cash_value - cost, 2)
                    if cash_value is not None and cost is not None
                    else None
                ),
                "expired": bool(item.get("expired")),
                "updated_at_utc": str(item.get("updateTime") or ""),
                "model_pick": links.get((str(slug), side)),
            }
        )
    positions.sort(key=lambda item: item["updated_at_utc"], reverse=True)
    normalized = [
        activity
        for item in (raw.get("activities") or [])
        if (activity := _normalize_live_activity(item, links)) is not None
    ]
    history = _save_portfolio_history(normalized, str(raw.get("observed_at_utc") or ""))
    balances = raw.get("balances") or []
    usd = next((item for item in balances if item.get("currency") == "USD"), None)
    return {
        "status": "live",
        "source": raw.get("source"),
        "observed_at_utc": raw.get("observed_at_utc"),
        "history_start_date": _load_portfolio_history()["history_start_date"],
        "open": {
            "positions": positions,
            "count": len(positions),
            "cost_basis_usd": round(
                sum(_number(item.get("cost_basis_usd")) for item in positions), 2
            ),
            "cash_value_usd": round(
                sum(_number(item.get("cash_value_usd")) for item in positions), 2
            ),
            "realized_pnl_usd": round(
                sum(_number(item.get("realized_pnl_usd")) for item in positions), 2
            ),
        },
        "recent_history": _portfolio_history_summary(history, "exchange_and_persisted"),
        "balance": {
            "current_usd": _number((usd or {}).get("currentBalance"), None),
            "buying_power_usd": _number((usd or {}).get("buyingPower"), None),
            "open_orders_usd": _number((usd or {}).get("openOrders"), None),
            "unsettled_funds_usd": _number((usd or {}).get("unsettledFunds"), None),
        },
    }


def bets_view() -> dict:
    """Backward-compatible route name for the authenticated live portfolio."""
    return live_portfolio_view()


def _model_version_rank(row: dict) -> tuple:
    """Sort key for choosing which duplicate to KEEP. Higher = keep.

    Prefers the numerically-newest model version (v3 > v2), then the most
    recently created row. Production models always outrank older ones.
    """
    version = str(row.get("model_version") or "")
    digits = "".join(ch for ch in version.split("-")[-1] if ch.isdigit())
    version_number = int(digits) if digits else 0
    return (version_number, str(row.get("created_at_utc") or ""))


def dedupe_ledger() -> dict:
    """Physically remove duplicate ledger rows from picks.xlsx.

    A duplicate = same contract identity (league/event/market/selection/line)
    logged under more than one model version or run. Keeps exactly one row per
    identity — the newest model version — and DELETES the rest from the file.
    picks.xlsx is backed up first (picks.xlsx.dedupe-bak-<ts>). Rows carrying
    real units are never deleted. Archived-hidden ids are pruned to match.
    """
    from model_prediction.ledger import FIELDNAMES  # local: heavy import
    from model_prediction.xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic

    path = DATA / "picks.xlsx"
    if not path.exists():
        return {"status": "refused", "error": "picks.xlsx not found"}
    headers, rows = read_xlsx_rows(path)
    if headers != FIELDNAMES:
        return {"status": "refused",
                "error": "picks.xlsx schema does not match this code version; not touching it"}
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault(_pick_identity(row), []).append(row)
    keep: list[dict] = []
    removed = 0
    removed_ids: list[str] = []
    for members in groups.values():
        if len(members) == 1:
            keep.append(members[0])
            continue
        # Never delete a row with real staked units; keep every staked row.
        staked = [m for m in members if _number(m.get("units")) > 0]
        unstaked = [m for m in members if _number(m.get("units")) <= 0]
        survivors = list(staked)
        if unstaked:
            survivors.append(max(unstaked, key=_model_version_rank))
        keep_ids = {id(m) for m in survivors}
        for member in members:
            if id(member) in keep_ids:
                keep.append(member)
            else:
                removed += 1
                removed_ids.append(str(member.get("pick_id") or ""))
    if removed == 0:
        return {"status": "ok", "removed": 0, "kept": len(keep),
                "note": "No duplicate contracts found."}
    backup = path.with_suffix(f".xlsx.dedupe-bak-{int(time.time())}")
    import shutil

    shutil.copy2(path, backup)
    keep.sort(key=lambda r: str(r.get("created_at_utc") or ""))
    write_xlsx_rows_atomic(path, FIELDNAMES, keep)
    # Prune archived ids that no longer exist so the counter stays honest.
    archive = _load_archive()
    surviving = {str(r.get("pick_id")) for r in keep}
    archive["pick_ids"] = sorted(pid for pid in archive["pick_ids"] if pid in surviving)
    archive["history"].append({"at": datetime.now(timezone.utc).isoformat()[:19],
                               "action": "dedupe", "rows": removed})
    _save_archive(archive)
    with _CACHE_LOCK:
        _CACHE.clear()
    _PICKS_CACHE["mtime"] = None
    _log(f"dedupe: removed {removed} duplicate rows, backup {backup.name}")
    return {"status": "ok", "removed": removed, "kept": len(keep),
            "backup": backup.name, "removed_pick_ids": removed_ids[:50],
            "note": f"Physically removed {removed} duplicate rows. Original backed up to {backup.name}."}


def _load_archive() -> dict:
    try:
        payload = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        return {"pick_ids": list(payload.get("pick_ids", [])),
                "history": list(payload.get("history", []))}
    except (OSError, json.JSONDecodeError):
        return {"pick_ids": [], "history": []}


def _save_archive(archive: dict) -> None:
    DASH_DIR.mkdir(exist_ok=True)
    ARCHIVE_FILE.write_text(json.dumps(archive, indent=1), encoding="utf-8")


def archive_action(action: str, scope: str) -> dict:
    """Persistently hide safe ledger rows from the dashboard table.

    picks.xlsx is never touched: archived rows keep feeding performance,
    calibration, backtests, and research. This is a display ledger-clear,
    not a data delete. Open rows with positive units are never archived.
    """
    archive = _load_archive()
    if action == "restore":
        restored = len(archive["pick_ids"])
        archive["pick_ids"] = []
        archive["history"].append({"at": datetime.now(timezone.utc).isoformat()[:19],
                                   "action": "restore", "rows": restored})
        _save_archive(archive)
        with _CACHE_LOCK:
            _CACHE.clear()
        return {"status": "ok", "action": "restore", "restored": restored}
    if action == "clear_ids":
        requested = {str(pick_id) for pick_id in scope if str(pick_id)}
        if not requested:
            return {"status": "refused", "error": "no pick ids supplied"}
        # A visible ledger/Today row is DEDUPED across model versions, so its one
        # pick_id stands in for every sibling row sharing the same contract
        # identity. Expand each requested id to its whole identity group,
        # otherwise dedup resurrects the row from an un-archived sibling.
        all_rows = read_picks()
        identity_to_ids: dict[tuple, set[str]] = {}
        id_to_identity: dict[str, tuple] = {}
        for row in all_rows:
            pid = str(row.get("pick_id") or "")
            if not pid:
                continue
            identity = _pick_identity(row)
            identity_to_ids.setdefault(identity, set()).add(pid)
            id_to_identity[pid] = identity
        expanded: set[str] = set()
        for pid in requested:
            identity = id_to_identity.get(pid)
            expanded |= identity_to_ids.get(identity, {pid}) if identity else {pid}
        exposed = {
            str(row.get("pick_id"))
            for row in all_rows
            if row.get("status") == "open"
            and row.get("record_type") == "QUALIFIED_SHADOW_CALL"
            and float(row.get("units") or 0) > 0
        }
        blocked = sorted(expanded & exposed)
        allowed = expanded - exposed
        existing = set(archive["pick_ids"]) | allowed
        archive["pick_ids"] = sorted(existing)
        archive["history"].append({"at": datetime.now(timezone.utc).isoformat()[:19],
                                   "action": "clear_ids", "rows": len(allowed)})
        _save_archive(archive)
        with _CACHE_LOCK:
            _CACHE.clear()
        return {"status": "ok", "action": "clear_ids",
                "archived_now": len(allowed), "rows_selected": len(requested),
                "blocked_open_staked": blocked,
                "archived_total": len(existing),
                "note": "View-only: rows remain in picks.xlsx and keep feeding research metrics."}
    if action != "clear" or scope not in ("day", "week", "month", "all"):
        return {"status": "refused",
                "error": "action must be clear(day|week|month|all), clear_ids, or restore"}
    today = datetime.now(timezone.utc).astimezone(EASTERN).date()
    days = {"day": 0, "week": 6, "month": 29}.get(scope)
    existing = set(archive["pick_ids"])
    added = protected = 0
    for row in read_picks():
        if (
            row.get("status") == "open"
            and row.get("record_type") == "QUALIFIED_SHADOW_CALL"
            and float(row.get("units") or 0) > 0
        ):
            protected += 1
            continue
        pick_id = str(row.get("pick_id"))
        if pick_id in existing:
            continue
        if days is not None:
            start = str(row.get("event_start_utc") or "")
            try:
                game_day = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(EASTERN).date()
            except ValueError:
                continue
            if (today - game_day).days > days or game_day > today:
                continue
        existing.add(pick_id)
        added += 1
    archive["pick_ids"] = sorted(existing)
    archive["history"].append({"at": datetime.now(timezone.utc).isoformat()[:19],
                               "action": f"clear:{scope}", "rows": added})
    _save_archive(archive)
    with _CACHE_LOCK:
        _CACHE.clear()
    return {"status": "ok", "action": f"clear:{scope}", "archived_now": added,
            "protected_open_staked": protected,
            "archived_total": len(existing),
            "note": "View-only: all rows remain in picks.xlsx and keep feeding research metrics."}


def _suggested_units(row: dict) -> float | None:
    """Edge-scaled paper stake, mirroring units.edge_scaled_units.

    (0.5U base + |p-0.5| * 10, capped at 2.0U, nearest 0.25U — the sizing that
    beat flat staking +34.1U vs +13.3U on the MLB walk-forward.) Shown only for
    open picks where the model has POSITIVE edge vs the market; every actual
    ledger stake stays 0 until a model is promoted past research.
    """
    try:
        p = float(row.get("model_probability") or 0)
        market = float(row.get("market_implied_probability") or 0)
    except (TypeError, ValueError):
        return None
    if row.get("status") != "open" or not (0 < p < 1) or not (0 < market < 1):
        return None
    # Edge-scaled from model confidence, computed for EVERY open pick —
    # including negative-edge research rows — so the sizing the engine would
    # use is always visible. The +EV badge carries the tail/no-tail context.
    raw = 0.5 + abs(p - 0.5) * (2.0 - 0.5) / 0.15
    units = max(0.5, min(2.0, raw))
    return round(units / 0.25) * 0.25


def _audit_tail() -> dict:
    path = DATA / "events.jsonl"
    events = []
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        for line in lines[-25:]:
            try:
                item = json.loads(line)
                events.append({"at": str(item.get("occurred_at_utc", ""))[:19],
                               "type": item.get("event_type"),
                               "subject": str(item.get("subject_id", ""))[:24]})
            except json.JSONDecodeError:
                continue
        return {"total_events": len(lines), "tail": list(reversed(events))}
    return {"total_events": 0, "tail": []}


def main() -> None:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--port", type=int, default=8765)
    options = arguments.parse_args()
    # daemon_threads: request threads die with the server instead of lingering;
    # allow_reuse_address: instant restarts without TIME_WAIT bind errors.
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", options.port), Handler)
    print(f"dashboard: http://127.0.0.1:{options.port}/  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
