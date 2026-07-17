#!/usr/bin/env python3
"""Read-only dashboard server for the model-prediction system.

Serves dashboard.html plus a small JSON API computed from the project's data
files. STRICTLY a viewer: it never writes to src/, config/, or data/. The only
writes it can trigger are the whitelisted quick actions, each of which shells
out to the existing `model-prediction` CLI (or pytest) after an explicit
confirmation in the UI.

Run:  python3 dashboard_server.py  [--port 8765]
Then open http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import json
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

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs" / "latest"
DASH_DIR = ROOT / "dashboard"
LOG_FILE = DASH_DIR / "server.log"
JOBS_FILE = DASH_DIR / "jobs.json"
ARCHIVE_FILE = DASH_DIR / "archive.json"


def _log(message: str) -> None:
    try:
        DASH_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()[:19]
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass
EASTERN = ZoneInfo("America/New_York")
SPORTS = ("mlb", "nba", "wnba", "nfl")
GATEWAY = "https://gateway.polymarket.us"

_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_LOCK = threading.Lock()
_ACTION_LOCK = threading.Lock()
_LAST_ACTION: dict[str, object] = {}
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_RUNNER: list[str] | None = None


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
    }


def matrix() -> dict:
    audits = sorted(OUTPUTS.glob("termination-audit-*.json"))
    audit = _read_json(audits[-1]) if audits else None
    validation = _read_json(OUTPUTS / "learned-model-validation-v2.json") or {}
    results = (audit or {}).get("results") or {}
    sports_meta = validation.get("sports") or {}
    markets = ["moneyline", "spread", "total", "f5_spread", "f5_total", "yrfi_nrfi"]
    grid = {}
    for sport in SPORTS:
        row = {}
        ml = results.get(sport) or {}
        if ml:
            row["moneyline"] = {
                "state": "qualified" if ml.get("qualified") else "tested_not_qualified",
                "hit_rate": ml.get("holdout_hit_rate"),
                "calls": ml.get("holdout_calls"),
                "units": ml.get("units_at_minus_110"),
                "brier": ml.get("brier_score"),
                "threshold": ml.get("learned_threshold"),
            }
        else:
            row["moneyline"] = {"state": "no_data"}
        readiness = (sports_meta.get(sport) or {}).get("multi_market_readiness") or {}
        for market in ("spread", "total"):
            row[market] = {
                "state": "model_untested" if readiness else "no_data",
                "readiness": readiness.get(market) if isinstance(readiness.get(market), (str, int, float)) else None,
            }
        for market in ("f5_spread", "f5_total", "yrfi_nrfi"):
            row[market] = {"state": "not_applicable" if sport != "mlb" else "no_data"}
        grid[sport] = row
    return {"markets": markets, "grid": grid,
            "source": audits[-1].name if audits else None,
            "gate": (audit or {}).get("methodology", {}).get("primary_gate")}


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
    if value not in SPORTS + ("soccer", "tennis"):
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
                def _picks_with_archive():
                    archived = set(_load_archive()["pick_ids"])
                    return [{**row, "archived": str(row.get("pick_id")) in archived}
                            for row in read_picks()]
                self._send(_cached("picks", 30, _picks_with_archive))
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
        elif parsed.path == "/api/action":
            self._send(start_action(str(payload.get("action")), payload))
        else:
            self._send({"error": "unknown route"}, code=404)


def today_picks(day: str) -> dict:
    """Every ledger pick whose game is played on the given US-Eastern date."""
    rows = []
    for row in read_picks():
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
        rows.append({**row, "start_et": start_et.strftime("%I:%M %p ET"),
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
    """Hide settled ledger rows from the TABLE VIEW only.

    picks.xlsx is never touched: archived rows keep feeding performance,
    calibration, backtests, and research. This is a display ledger-clear,
    not a data delete. Open picks are never archived.
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
        exposed = {
            str(row.get("pick_id"))
            for row in read_picks()
            if row.get("status") == "open"
            and row.get("record_type") == "QUALIFIED_SHADOW_CALL"
            and (row.get("units") or 0) and float(row.get("units") or 0) > 0
        }
        blocked = sorted(requested & exposed)
        allowed = requested - exposed
        existing = set(archive["pick_ids"]) | allowed
        archive["pick_ids"] = sorted(existing)
        archive["history"].append({"at": datetime.now(timezone.utc).isoformat()[:19],
                                   "action": "clear_ids", "rows": len(allowed)})
        _save_archive(archive)
        with _CACHE_LOCK:
            _CACHE.clear()
        return {"status": "ok", "action": "clear_ids", "archived_now": len(allowed),
                "blocked_open_staked": blocked,
                "archived_total": len(existing),
                "note": "View-only: rows remain in picks.xlsx and keep feeding research metrics."}
    if action != "clear" or scope not in ("day", "week", "month", "all"):
        return {"status": "refused",
                "error": "action must be clear(day|week|month|all), clear_ids, or restore"}
    today = datetime.now(timezone.utc).astimezone(EASTERN).date()
    days = {"day": 0, "week": 6, "month": 29}.get(scope)
    existing = set(archive["pick_ids"])
    added = 0
    for row in read_picks():
        if row.get("status") != "settled":
            continue  # open picks never leave the view
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
    print(f"dashboard: http://127.0.0.1:{options.port}/  (read-only; Ctrl-C to stop)")
    server.serve_forever()


if __name__ == "__main__":
    main()
