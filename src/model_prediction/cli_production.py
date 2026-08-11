"""Production CLI entrypoint for the model-prediction canary.

Serves the production model (wnba-elo-trend-lr-v4) with three subcommands:

    python -m model_prediction.cli_production predict
    python -m model_prediction.cli_production health
    python -m model_prediction.cli_production status

The production canary infrastructure (config/production.yaml,
production_canary.py) is already built and tested. This module wires it
into a runnable CLI and is the single entrypoint called by the launchd
production scheduler.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .data_sources.espn import ESPNClient
from .domain import EASTERN
from .features.base import FeatureStore
from .learned_forward import build_learned_moneyline_slate
from .production_canary import (
    _compute_artifact_hash,
    get_production_model,
    health_check,
    load_production_config,
    validate_production_config,
)
from .runtime_paths import RuntimePaths

_STATE_FILE_NAME = "production_state.json"


# ── helpers ────────────────────────────────────────────────────────────────


def _today_et() -> str:
    """Return today's date as YYYY-MM-DD in US Eastern time."""
    return datetime.now(tz=EASTERN).strftime("%Y-%m-%d")


def _utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _resolve_runtime_root() -> Path:
    """Resolve the runtime root (mutable state outside the Git repo)."""
    env = os.environ.get("MODEL_PREDICTION_RUNTIME_ROOT")
    if env:
        return Path(env)
    # Fall back to RuntimePaths default (repo_root/data)
    return RuntimePaths.resolve().runtime_root


def _state_path() -> Path:
    """Path to the production state file under the runtime root."""
    rt = _resolve_runtime_root()
    rt.mkdir(parents=True, exist_ok=True)
    return rt / _STATE_FILE_NAME


def _read_state() -> dict[str, Any]:
    """Read the production state file, or return an empty dict."""
    sp = _state_path()
    if not sp.is_file():
        return {}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict[str, Any]) -> None:
    """Write the production state file atomically."""
    sp = _state_path()
    tmp = sp.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(sp)


def _record_prediction_run(
    model_id: str, artifact_hash: str, event_count: int, prediction_count: int
) -> None:
    """Record a prediction run in the production state file."""
    state = _read_state()
    state["model_id"] = model_id
    state["artifact_hash"] = artifact_hash
    state["last_prediction_utc"] = _utc_now_iso()
    state["last_scheduler_run_utc"] = _utc_now_iso()
    state["today_events"] = event_count
    state["today_predictions"] = prediction_count
    _write_state(state)


# ── subcommands ─────────────────────────────────────────────────────────────


def _cmd_predict() -> int:
    """Run today's WNBA predictions using the production canary model.

    Returns exit code 0 on success (including NO_EVENTS), non-zero on error.
    """
    repo_root = PROJECT_ROOT
    runtime_root = _resolve_runtime_root()

    # 1. Load and validate production config
    try:
        config = load_production_config(repo_root=repo_root)
        validate_production_config(config, repo_root=repo_root)
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 1

    # 2. Get production model info
    model = get_production_model(config)
    model_id = model["model_id"]
    artifact_rel = model["artifact"]
    artifact_path = repo_root / artifact_rel

    # 3. Compute artifact hash for reporting
    try:
        with open(artifact_path, "r", encoding="utf-8") as fh:
            artifact_payload: dict[str, Any] = json.load(fh)
        artifact_hash = _compute_artifact_hash(artifact_payload)
    except Exception as exc:
        print(f"ARTIFACT ERROR: {exc}", file=sys.stderr)
        return 1

    # 4. Check if there are any WNBA games today
    today = _today_et()
    client = ESPNClient()
    try:
        scoreboard = client.scoreboard("WNBA", today)
        events = scoreboard.get("events", [])
    except Exception as exc:
        print(f"ESPN ERROR: {exc}", file=sys.stderr)
        return 1

    if not events:
        print(f"NO_EVENTS: no WNBA games scheduled for {today}")
        _record_prediction_run(model_id, artifact_hash, 0, 0)
        return 0

    # 5. Run predictions
    store = FeatureStore(runtime_root)
    observed_at = datetime.now(timezone.utc)

    try:
        candidates, skipped, scheduled = build_learned_moneyline_slate(
            sport="wnba",
            game_date=today,
            store=store,
            client=client,
            artifact_path=artifact_path,
            observed_at=observed_at,
        )
    except ValueError as exc:
        # Common case: not enough history data yet
        msg = str(exc)
        if "requires" in msg and "cached games before" in msg:
            print(f"NO_EVENTS: insufficient history for {today} — {msg}")
            _record_prediction_run(model_id, artifact_hash, len(events), 0)
            return 0
        print(f"PREDICTION ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"PREDICTION ERROR: {exc}", file=sys.stderr)
        return 1

    # 6. Report results
    print(f"=== Production Canary: {model_id} ===")
    print(f"Date: {today} | Observed at: {observed_at.isoformat()}")
    print(f"Artifact hash: {artifact_hash}")
    print(f"Scheduled events: {scheduled}")
    print(f"Candidates: {len(candidates)}")
    print(f"Skipped: {len(skipped)}")
    print()

    if not candidates:
        print("NO_PREDICTIONS: all events skipped or filtered")
        _record_prediction_run(model_id, artifact_hash, scheduled, 0)
        return 0

    for c in candidates:
        d = c.to_dict()
        print(f"  {d['event_id']}: {d['away_team']} @ {d['home_team']}")
        print(f"    selection: {d['selection']}")
        print(f"    probability: {d['model_probability']:.4f}")
        print(f"    home_probability: {d['home_probability']:.4f}")
        print(f"    action: {d['action']}")
        print(f"    reason: {d['reason']}")
        if d.get("unavailable_features"):
            print(f"    warnings: {', '.join(d['unavailable_features'])}")
        print()

    for s in skipped:
        print(f"  SKIPPED: {s['event_id']} — {s['reason']}")

    _record_prediction_run(model_id, artifact_hash, scheduled, len(candidates))
    return 0


def _cmd_health() -> int:
    """Run the production canary health check and print the result.

    Returns exit code 0 when HEALTHY, 1 otherwise.
    """
    repo_root = PROJECT_ROOT
    rt = _resolve_runtime_root()

    try:
        config = load_production_config(repo_root=repo_root)
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 1

    result = health_check(config, repo_root=repo_root, runtime_root=rt)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "HEALTHY" else 1


def _cmd_status() -> int:
    """Print a human-readable production config summary.

    Returns exit code 0.
    """
    repo_root = PROJECT_ROOT

    try:
        config = load_production_config(repo_root=repo_root)
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 1

    svc = config.get("prediction_service", {})
    primary = svc.get("primary", {})
    allowed = svc.get("allowed_models", [])
    execution = config.get("execution", {})
    state = _read_state()

    print("=== Production Status ===")
    print(f"Model:          {primary.get('model_id', 'unknown')}")
    print(f"Sport:          {primary.get('sport', 'unknown')}")
    print(f"Market:         {primary.get('market', 'unknown')}")
    print(f"Artifact:       {primary.get('artifact', 'unknown')}")
    print(f"Allowed models: {', '.join(allowed) if allowed else 'none'}")
    print(f"Fallback:       {svc.get('fallback_action', 'unknown')}")
    print(f"Auto orders:    {execution.get('automated_orders', False)}")
    print(f"Manual only:    {execution.get('manual_orders_only', True)}")
    print()
    print(f"Last prediction:  {state.get('last_prediction_utc', 'never')}")
    print(f"Last scheduler:   {state.get('last_scheduler_run_utc', 'never')}")
    print(f"Today events:     {state.get('today_events', 'N/A')}")
    print(f"Today predictions:{state.get('today_predictions', 'N/A')}")
    return 0


# ── main ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m model_prediction.cli_production``."""
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        print(
            "usage: python -m model_prediction.cli_production {predict|health|status}",
            file=sys.stderr,
        )
        return 2

    cmd = args[0].lower()

    if cmd == "predict":
        return _cmd_predict()
    elif cmd == "health":
        return _cmd_health()
    elif cmd == "status":
        return _cmd_status()
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        print(
            "usage: python -m model_prediction.cli_production {predict|health|status}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
