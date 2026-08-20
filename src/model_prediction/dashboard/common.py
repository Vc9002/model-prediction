"""Dashboard common module (extracted from dashboard_server.py, DD-5).

Part of the dashboard_server.py -> dashboard/ package split. See MASTER.md
DD-5 and dashboard/__init__.py for the re-export shim that keeps the old
`dashboard_server` import path and test-suite symbol references working.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None

# ── SECTION: Paths & Constants ───────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_PORT = 8765

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
# Main/Flat split from one shared file per tier into one file per sport,
# 2026-08-03 (see model_prediction.main_ledgers for the real module this
# mirrors -- duplicated here as a plain tuple rather than importing that
# module at load time, matching this file's existing pattern of keeping
# model_prediction imports lazy/local to the functions that need them).
_MAIN_LEDGER_SPORTS = ("mlb", "wnba", "soccer", "tennis")
DASH_DIR = ROOT / "dashboard"
PID_FILE = DASH_DIR / "server.pid"
LOG_FILE = DASH_DIR / "server.log"
JOBS_FILE = DASH_DIR / "jobs.json"
ARCHIVE_FILE = DASH_DIR / "archive.json"
ORDERS_FILE = DASH_DIR / "orders.json"
PORTFOLIO_HISTORY_FILE = DASH_DIR / "portfolio_history.json"
CONFIG_FILE = ROOT / "config" / "model.yaml"
FEATURE_REGISTRY_FILE = ROOT / "config" / "tested_features.json"


def _log(message: str) -> None:
    try:
        DASH_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).isoformat()[:19]
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


# EASTERN and SPORTS defined here for self-contained startup, but the canonical
# source of truth is src/model_prediction/domain.py (League enum, EASTERN zone).
# Keep these in sync with domain.py.
EASTERN = ZoneInfo("America/New_York")
SPORTS = (
    "mlb",
    "nba",
    "wnba",
    "nfl",
    "soccer",
    "tennis",
    "lol",
    "cs2",
    "dota2",
    "valorant",
    "rainbow_six",
    "kbo",
    "npb",
)
GATEWAY = "https://gateway.polymarket.us"

_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_LOCK = threading.Lock()
_CONFIG_LOCK = threading.Lock()
_ACTION_LOCK = threading.Lock()
_LAST_ACTION: dict[str, object] = {}
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


_RUNNER: list[str] | None = None
_ORDER_PREVIEWS: dict[str, dict] = {}
_ORDER_LOCK = threading.Lock()
_MARKET_QUESTION_CACHE: dict[str, str | None] = {}
_MARKET_QUESTION_LOCK = threading.Lock()

# Real gap fixed 2026-08-02: this dashboard has real order-execution
# capability (POST /api/order/submit shells out to `execute --execute`) but
# previously had no authentication at all -- only an Origin/Host CSRF check
# (blocks a malicious webpage tricking the browser) and a client-supplied
# confirm:true flag (not a credential; any caller can set it). Neither stops
# a different local process or user account on the same machine from
# curling the API directly and placing a real order. Generated fresh per
# process start (a restart naturally invalidates any leaked/old token); the
# served dashboard.html gets it injected server-side (see do_GET) and
# attaches it to every POST automatically, so the browser UI keeps working
# with no manual step -- only a caller who never loaded the real page (or
# is on a different machine, blocked by the 127.0.0.1 bind regardless)
# lacks it.
_DASHBOARD_TOKEN = secrets.token_urlsafe(32)


def _inject_dashboard_token(html: bytes) -> bytes:
    """Embed the session token and a fetch wrapper that auto-attaches it to
    every POST, so the served UI keeps working with no manual step. Falls
    back to serving the page unmodified (POSTs then need the token supplied
    some other way) if the expected <script> opening isn't found, rather
    than raising and breaking the whole page load over a missing feature."""
    marker = b'<script>\n"use strict";'
    injected = (
        b'<script>\n"use strict";\nwindow.__DASH_TOKEN__='
        + json.dumps(_DASHBOARD_TOKEN).encode()
        + b";\nconst __nativeFetch__=window.fetch.bind(window);"
        b"\nwindow.fetch=(input,init)=>{"
        b'\n  if(init&&init.method==="POST"){'
        b"\n    init={...init,headers:{...(init.headers||{}),'X-Dashboard-Token':window.__DASH_TOKEN__}};"
        b"\n  }"
        b"\n  return __nativeFetch__(input,init);"
        b"\n};"
    )
    if marker not in html:
        return html
    return html.replace(marker, injected, 1)


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
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                env=_runner_env(),
                check=False,
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


# ── SECTION: Cache Layer ────────────────────────────────────────────


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


_runtime_paths_cache: list = []


def _runtime_paths():
    """Resolve RuntimePaths once and cache it -- never per-request."""
    if not _runtime_paths_cache:
        from model_prediction.runtime_paths import RuntimePaths

        _runtime_paths_cache.append(RuntimePaths.resolve(repo_root=ROOT, require_external_runtime=True))
    return _runtime_paths_cache[0]


def rebuild_view(name: str) -> dict:
    """Return one isolated rebuild projection without importing writer classes."""
    from dashboard.rebuild_status import read_rebuild_view

    try:
        paths = _runtime_paths()
    except RuntimeError:
        # Env-less contexts (CI smoke, dev without the launchd env) get
        # the read-only view's own repo-local fallback. The rebuild view
        # never writes, so there is no split-brain risk here.
        paths = None
    return read_rebuild_view(name, ROOT, paths=paths)


def _unit_value_usd() -> float:
    try:
        payload = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        value = float((payload.get("bankroll") or {}).get("unit_value_usd", 7.5))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return 7.5
    return value if value > 0 else 7.5


def _set_unit_value_usd(raw_value: object) -> dict:
    """Atomically persist the one-unit dollar value used by UI and execution."""
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError("1U must be a dollar amount") from error
    if not math.isfinite(value) or not 0.01 <= value <= 100_000:
        raise ValueError("1U must be between $0.01 and $100,000.00")

    with _CONFIG_LOCK:
        original = CONFIG_FILE.read_text(encoding="utf-8")
        previous = _unit_value_usd()
        updated, count = re.subn(
            r"(?m)^(\s+unit_value_usd:\s*).*$",
            rf"\g<1>{value:.2f}",
            original,
        )
        if count != 1:
            raise RuntimeError("expected exactly one bankroll.unit_value_usd setting")
        parsed = yaml.safe_load(updated) or {}
        persisted = float((parsed.get("bankroll") or {}).get("unit_value_usd"))
        if not math.isclose(persisted, value, rel_tol=0, abs_tol=0.000001):
            raise RuntimeError("unit value failed configuration validation")

        temporary = CONFIG_FILE.with_name(f".{CONFIG_FILE.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, CONFIG_FILE.stat().st_mode)
            os.replace(temporary, CONFIG_FILE)
        finally:
            temporary.unlink(missing_ok=True)

    try:
        from model_prediction.audit import AuditLog

        AuditLog(DATA / "events.jsonl").append(
            "unit_value_updated",
            "bankroll.unit_value_usd",
            {"previous_usd": previous, "unit_value_usd": value, "source": "dashboard"},
        )
    except (ImportError, OSError, ValueError) as error:
        _log(f"unit value updated but audit append failed: {error}")
    with _CACHE_LOCK:
        _CACHE.clear()
    return {
        "status": "ok",
        "previous_unit_value_usd": previous,
        "unit_value_usd": value,
        "note": "Applies to future dollar displays and order sizing; historical units are unchanged.",
    }


def _config_payload() -> dict:
    try:
        payload = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    # ── SECTION: Configuration ──────────────────────────────────────────
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
        str(row.get(key) or "").strip().casefold() in banned_names for key in ("away_team", "home_team")
    )


def _manual_research_eligibility(row: dict) -> tuple[bool, str]:
    if row.get("record_type") != "RESEARCH_OBSERVATION":
        return False, "not a research observation"
    config = _config_payload()
    execution = config.get("execution") or {}
    if not execution.get("allow_manual_research_orders", False):
        return False, "manual research orders are disabled"
    league = str(row.get("league") or "").upper()
    active_version = (config.get("models", {}).get(league, {}) or {}).get("active_production_version")
    if execution.get("manual_research_require_active_model", True) and (
        not active_version or row.get("model_version") != active_version
    ):
        return False, "pick is not from the active production model"
    edge = _number(row.get("model_probability")) - _number(row.get("market_implied_probability"))
    if execution.get("manual_research_require_positive_edge", True) and edge <= 0:
        return False, "model has no positive edge at the logged market price"
    if _row_has_banned_team(row):
        return False, "a team in this matchup is permanently banned"
    return True, "manual active-model order"


_PICKS_CACHE: dict[str, object] = {"mtime": None, "rows": []}
_FLAT_PICKS_CACHE: dict[str, object] = {"mtime": None, "rows": []}
# SQLite-backed cache: mirrors Excel ledger data for ~100x faster dashboard reads.
# Falls back to Excel parsing if the SQLite cache hasn't been built yet.
_DASHBOARD_CACHE: object | None = None


def _get_dashboard_cache():
    """Lazily construct and return the shared SQLite dashboard cache.

    Owns the `_DASHBOARD_CACHE` reassignment so it happens exactly once in
    this module's own namespace -- a `global _DASHBOARD_CACHE` in another
    module would only rebind that module's own copy of the name, not this
    one, silently breaking the cache-once contract.
    """
    global _DASHBOARD_CACHE
    if _DASHBOARD_CACHE is None:
        try:
            from model_prediction.dashboard_cache import get_cache as _get_dc
            from model_prediction.runtime_paths import RuntimePaths

            # The cache DB is mutable runtime state (runtime root); the
            # Excel sources it mirrors stay in the repo checkout.
            _DASHBOARD_CACHE = _get_dc(
                DATA, db_path=RuntimePaths.resolve(repo_root=ROOT).runtime_root / "dashboard_cache.db"
            )
        except Exception:  # noqa: BLE001, S110 - cache is optional, callers tolerate None
            pass
    return _DASHBOARD_CACHE


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _today() -> str:
    return datetime.now(UTC).astimezone(EASTERN).date().isoformat()
