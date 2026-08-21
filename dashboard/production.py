"""Production dashboard card — reads production state for the dashboard.

Usage:

    python -c "from dashboard.production import get_production_status; \
               import json; print(json.dumps(get_production_status(), indent=2))"

Returns the production canary snapshot: model identity, health, artifact hash,
and the last known prediction/scheduler run timestamps.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.config import PROJECT_ROOT
from model_prediction.production_canary import (
    _compute_artifact_hash,
    health_check,
    load_production_config,
)

_logger = logging.getLogger(__name__)

_STATE_FILE_NAME = "production_state.json"


def _resolve_runtime_root() -> Path:
    """Incumbent-side production data root: always the repo's data/.

    Deliberately NOT env-var-aware — matches cli_production._resolve_data_root.
    The runtime root is rebuild-scoped only (MIGRATION_MANIFEST.json); the
    production canary's state file, ledger, and FeatureStore history are
    incumbent-side and live under repo ``data/``. Env-aware resolution here
    split the state in two (2026-08-13: the scheduled run wrote a second
    state file under the runtime root while this reader followed the env
    var or fallback depending on how the dashboard was started).
    """
    return PROJECT_ROOT / "data"


def _read_state() -> dict[str, Any]:
    """Read the production state file, or return an empty dict."""
    rt = _resolve_runtime_root()
    sp = rt / _STATE_FILE_NAME
    if not sp.is_file():
        return {}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return {}


def _compute_hash_from_artifact_path(artifact_rel: str) -> str:
    """Compute the artifact hash from the artifact file."""
    artifact_path = PROJECT_ROOT / artifact_rel
    if not artifact_path.is_file():
        return ""
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        return _compute_artifact_hash(payload)
    except (json.JSONDecodeError, OSError, KeyError):
        return ""


def get_production_status() -> dict[str, Any]:
    """Return the production canary dashboard card data.

    Output shape::

        {
            "model_id": "wnba-elo-trend-lr-v4",
            "status": "HEALTHY",
            "artifact_hash": "7afd...",
            "last_prediction_utc": null,
            "last_scheduler_run_utc": null,
            "today_events": 0,
            "today_predictions": 0,
            "automated_orders": false
        }
    """
    state = _read_state()
    now_utc = datetime.now(UTC).isoformat()

    # Defaults from config when state is empty
    model_id = "wnba-elo-trend-lr-v4"
    artifact_hash = ""
    last_prediction_utc = None
    last_scheduler_run_utc = None
    today_events = 0
    today_predictions = 0
    automated_orders = False
    status = "UNKNOWN"

    # Try to load production config
    try:
        config = load_production_config(repo_root=PROJECT_ROOT)
        svc = config.get("prediction_service", {})
        primary = svc.get("primary", {})
        model_id = primary.get("model_id", model_id)
        automated_orders = config.get("execution", {}).get("automated_orders", False)

        artifact_rel = primary.get("artifact", "")
        if artifact_rel:
            artifact_hash = _compute_hash_from_artifact_path(artifact_rel)

        # Run health check
        rt = _resolve_runtime_root()
        health = health_check(config, repo_root=PROJECT_ROOT, runtime_root=rt)
        status = health.get("status", "UNKNOWN")

        # Use the hash from the health check if we didn't compute one
        if not artifact_hash:
            artifact_hash = health.get("artifact_hash", "")
    except Exception:
        # If config can't be loaded, return what we can from state
        _logger.warning("production config/health check failed; serving state-only card", exc_info=True)

    # Overlay state (last-run data)
    if state:
        last_prediction_utc = state.get("last_prediction_utc")
        last_scheduler_run_utc = state.get("last_scheduler_run_utc")
        today_events = state.get("today_events", 0)
        today_predictions = state.get("today_predictions", 0)

    return {
        "model_id": model_id,
        "status": status,
        "artifact_hash": artifact_hash[:12] + "..."
        if artifact_hash and len(artifact_hash) > 12
        else artifact_hash,
        "last_prediction_utc": last_prediction_utc,
        "last_scheduler_run_utc": last_scheduler_run_utc,
        "today_events": today_events,
        "today_predictions": today_predictions,
        "automated_orders": automated_orders,
        "checked_at_utc": now_utc,
    }
