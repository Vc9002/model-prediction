"""Production CLI entrypoint for the model-prediction canary.

Serves the primary production model (wnba-elo-trend-lr-v4, allowlisted
alongside the other production models in config/production.yaml):

    python -m model_prediction.cli_production predict
    python -m model_prediction.cli_production health
    python -m model_prediction.cli_production status
    python -m model_prediction.cli_production ledger [LIMIT]
    python -m model_prediction.cli_production settle <row_id> <won|lost|void> [--note TEXT]
    python -m model_prediction.cli_production void <row_id> [--note TEXT]
    python -m model_prediction.cli_production supersede <row_id> [--note TEXT]
    python -m model_prediction.cli_production error <row_id> [--note TEXT]

The production canary infrastructure (config/production.yaml,
production_canary.py) is already built and tested. This module wires it
into a runnable CLI and is the single entrypoint called by the launchd
production scheduler.

Every prediction the ``predict`` cycle reports is mirrored into the
production ledger (SQLite, ``<repo data>/production/predictions.db``)
with an idempotency key, and every cycle starts/completes a run row.  The
mirror is fail-soft: a ledger failure logs and never fails the prediction
command.  The lifecycle subcommands (``settle``/``void``/``supersede``/
``error``) transition an open prediction to a terminal state — guarded so
a terminal prediction can never be re-transitioned.  Unlike the scheduler
path, the lifecycle commands are fail-LOUD: an operator action that
silently no-ops is worse than an error.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
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
from .production_ledger import ProductionLedger

_STATE_FILE_NAME = "production_state.json"
_LEDGER_DB_REL = Path("production") / "predictions.db"

_logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────


def _today_et() -> str:
    """Return today's date as YYYY-MM-DD in US Eastern time."""
    return datetime.now(tz=EASTERN).strftime("%Y-%m-%d")


def _utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _resolve_data_root() -> Path:
    """Incumbent-side production data root: always the repo's data/.

    Deliberately NOT env-var-aware. MIGRATION_MANIFEST.json scoped the
    runtime root (MODEL_PREDICTION_RUNTIME_ROOT) to rebuild/ mutable state
    only; the production canary is incumbent-side, and its state file,
    SQLite ledger, and FeatureStore game history all live under repo
    ``data/`` and are maintained by the incumbent daily pipeline. Routing
    them through the runtime root split them in two (a live split-brain
    found 2026-08-13: the scheduled run wrote a second, empty state file
    and ledger under the runtime root while the dashboard read the stale
    repo ones).
    """
    return PROJECT_ROOT / "data"


def _state_path() -> Path:
    """Path to the production state file under the repo data root."""
    rt = _resolve_data_root()
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


def _ledger_path(runtime_root: Path) -> Path:
    """SQLite production ledger location under the runtime root."""
    return runtime_root / _LEDGER_DB_REL


def _git_sha() -> str:
    """Best-effort HEAD SHA for ledger run rows; ``"unknown"`` when unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True, cwd=PROJECT_ROOT,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _open_ledger(runtime_root: Path) -> ProductionLedger | None:
    """Open the production ledger, or None if it is unavailable.

    Scheduler-path ledger writes are fail-soft (same contract as the
    model-ledger mirror: a mirror failure must never fail the primary
    action), so an open failure degrades to no-op writes, not a failed
    prediction cycle.
    """
    try:
        return ProductionLedger(_ledger_path(runtime_root))
    except Exception:  # ledger mirror must not fail the prediction cycle
        _logger.warning(
            "production ledger unavailable at %s; this cycle's predictions "
            "will not be recorded",
            _ledger_path(runtime_root),
            exc_info=True,
        )
        return None


def _soft_ledger_write(
    ledger: ProductionLedger | None, name: str, *args: Any, **kwargs: Any
) -> None:
    """Call a ledger method fail-soft: log, never raise.

    The prediction command must succeed even when the ledger is down —
    the ledger mirrors the cycle's evidence, it does not gate it.
    """
    if ledger is None:
        return
    try:
        getattr(ledger, name)(*args, **kwargs)
    except Exception:  # ledger mirror write must not fail the cycle
        _logger.warning(
            "production ledger %s failed; prediction cycle continues",
            name,
            exc_info=True,
        )


def _split_note(args: list[str]) -> tuple[list[str], str | None]:
    """Split a ``--note TEXT`` pair out of positional args."""
    if "--note" not in args:
        return args, None
    idx = args.index("--note")
    if idx == len(args) - 1:
        raise ValueError("--note requires a value")
    return args[:idx] + args[idx + 2 :], args[idx + 1]


def _open_ledger_checked(runtime_root: Path) -> ProductionLedger:
    """Open the ledger for an operator transition command.

    Operator commands are fail-LOUD: an explicit human settlement that
    silently no-ops is worse than an error the operator can see.
    """
    return ProductionLedger(_ledger_path(runtime_root))


def _print_prediction_row(row: dict[str, Any]) -> None:
    print(
        f"prediction {row['id']}: {row['status']}"
        + (f" outcome={row['resolved_outcome']}" if row.get("resolved_outcome") else "")
        + (f" settled_at={row['settled_at_utc']}" if row.get("settled_at_utc") else "")
        + (f" note={row['note']}" if row.get("note") else "")
    )


# ── subcommands ─────────────────────────────────────────────────────────────


def _cmd_predict() -> int:
    """Run today's WNBA predictions using the production canary model.

    Returns exit code 0 on success (including NO_EVENTS), non-zero on error.
    """
    repo_root = PROJECT_ROOT
    data_root = _resolve_data_root()

    # 1. Load and validate production config
    try:
        config = load_production_config(repo_root=repo_root)
        validate_production_config(config, repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        print(f"ARTIFACT ERROR: {exc}", file=sys.stderr)
        return 1

    # 3b. Open the production ledger and start the run row.  Fail-soft:
    #     if the ledger is unavailable the cycle still runs — the state
    #     file below remains the primary record and the ledger is a mirror.
    run_git_sha = _git_sha()
    ledger = _open_ledger(data_root)
    run_id = None
    if ledger is not None:
        try:
            run_id = ledger.start_run(git_sha=run_git_sha)
        except Exception:  # ledger mirror must not fail the prediction cycle
            _logger.warning(
                "production ledger start_run failed; prediction cycle continues",
                exc_info=True,
            )

    # 4. Check if there are any WNBA games today
    today = _today_et()
    client = ESPNClient()
    try:
        scoreboard = client.scoreboard("WNBA", today)
        events = scoreboard.get("events", [])
    except Exception as exc:  # noqa: BLE001
        print(f"ESPN ERROR: {exc}", file=sys.stderr)
        _soft_ledger_write(ledger, "complete_run", run_id, "failed",
                           note=f"ESPN error: {exc}")
        return 1

    if not events:
        print(f"NO_EVENTS: no WNBA games scheduled for {today}")
        _record_prediction_run(model_id, artifact_hash, 0, 0)
        _soft_ledger_write(ledger, "complete_run", run_id, "completed",
                           note="NO_EVENTS")
        return 0

    # 5. Run predictions
    store = FeatureStore(data_root)
    observed_at = datetime.now(UTC)

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
            _soft_ledger_write(ledger, "complete_run", run_id, "completed",
                               note=f"insufficient history: {msg}")
            return 0
        print(f"PREDICTION ERROR: {exc}", file=sys.stderr)
        _soft_ledger_write(ledger, "complete_run", run_id, "failed",
                           note=f"ValueError: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"PREDICTION ERROR: {exc}", file=sys.stderr)
        _soft_ledger_write(ledger, "complete_run", run_id, "failed",
                           note=f"{type(exc).__name__}: {exc}")
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
        _soft_ledger_write(ledger, "complete_run", run_id, "completed",
                           note="NO_PREDICTIONS")
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
        # Mirror this prediction into the production ledger (fail-soft).
        # The idempotency key (run_id, event_id, sport, market, model_id)
        # makes a re-fired cycle with identical inputs a no-op.
        _soft_ledger_write(
            ledger, "record_prediction", run_id,
            prediction_id=f"{run_id}:{d['event_id']}",
            event_id=d["event_id"],
            sport=model["sport"],
            market=model["market"],
            model_id=model_id,
            probabilities={
                "home": d["home_probability"],
                "away": round(1 - d["home_probability"], 6),
            },
            event_start_utc=d["event_start_utc"],
            prediction_time_utc=observed_at.isoformat(),
            model_artifact_hash=artifact_hash,
            feature_schema_hash=d["feature_snapshot_hash"],
            predicted_side=d["selection"],
            rationale=d["reason"],
            data_timestamp=observed_at.isoformat(),
            git_sha=run_git_sha,
        )

    for s in skipped:
        print(f"  SKIPPED: {s['event_id']} — {s['reason']}")

    _record_prediction_run(model_id, artifact_hash, scheduled, len(candidates))
    _soft_ledger_write(ledger, "complete_run", run_id, "completed",
                       note=f"{len(candidates)} predictions")
    return 0


def _cmd_health() -> int:
    """Run the production canary health check and print the result.

    Returns exit code 0 when HEALTHY, 1 otherwise.
    """
    repo_root = PROJECT_ROOT
    rt = _resolve_data_root()

    try:
        config = load_production_config(repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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


def _cmd_ledger(args: list[str]) -> int:
    """List recent predictions (newest first) with their row ids.

    Row ids are the handle the lifecycle commands (``settle``, ``void``,
    ``supersede``, ``error``) operate on.
    """
    limit = 20
    if args and args[0].isdigit():
        limit = int(args[0])
    try:
        ledger = _open_ledger_checked(_resolve_data_root())
        try:
            rows = ledger.get_predictions(limit=limit)
        finally:
            ledger.close()
    except Exception as exc:  # noqa: BLE001
        print(f"LEDGER ERROR: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("no predictions recorded")
        return 0
    for r in rows:
        print(
            f"{r['id']:>4} {r['status']:<10} {r['event_id']:<20} "
            f"{r['predicted_side'] or '?':<6} "
            f"home={r['probabilities'].get('home'):.3f} {r['model_id']}"
        )
    return 0


def _cmd_settle(args: list[str]) -> int:
    """Resolve a prediction: ``settle <row_id> <won|lost|void> [--note TEXT]``."""
    try:
        rest, note = _split_note(args)
        if len(rest) != 2 or not rest[0].isdigit():
            raise ValueError(
                "usage: settle <row_id> <won|lost|void> [--note TEXT]"
            )
        row_id = int(rest[0])
        ledger = _open_ledger_checked(_resolve_data_root())
        try:
            row = ledger.settle_prediction(row_id, rest[1], note=note)
        finally:
            ledger.close()
    except Exception as exc:  # noqa: BLE001
        print(f"SETTLE ERROR: {exc}", file=sys.stderr)
        return 1
    _print_prediction_row(row)
    return 0


def _cmd_void(args: list[str]) -> int:
    """Void a prediction: ``void <row_id> [--note TEXT]``."""
    try:
        rest, note = _split_note(args)
        if len(rest) != 1 or not rest[0].isdigit():
            raise ValueError("usage: void <row_id> [--note TEXT]")
        row_id = int(rest[0])
        ledger = _open_ledger_checked(_resolve_data_root())
        try:
            row = ledger.void_prediction(row_id, note=note)
        finally:
            ledger.close()
    except Exception as exc:  # noqa: BLE001
        print(f"VOID ERROR: {exc}", file=sys.stderr)
        return 1
    _print_prediction_row(row)
    return 0


def _cmd_supersede(args: list[str]) -> int:
    """Mark a prediction superseded: ``supersede <row_id> [--note TEXT]``.

    Append the replacement forecast separately via the predict cycle
    (the superseded row's ``supersedes_id`` chain records the correction).
    """
    try:
        rest, note = _split_note(args)
        if len(rest) != 1 or not rest[0].isdigit():
            raise ValueError("usage: supersede <row_id> [--note TEXT]")
        row_id = int(rest[0])
        ledger = _open_ledger_checked(_resolve_data_root())
        try:
            row = ledger.supersede_prediction(row_id, note=note)
        finally:
            ledger.close()
    except Exception as exc:  # noqa: BLE001
        print(f"SUPERSEDE ERROR: {exc}", file=sys.stderr)
        return 1
    _print_prediction_row(row)
    return 0


def _cmd_error(args: list[str]) -> int:
    """Flag a prediction as errored: ``error <row_id> [--note TEXT]``.

    *note* should record what went wrong — the row's status is terminal,
    so the note is the only evidence of why it was invalidated.
    """
    try:
        rest, note = _split_note(args)
        if len(rest) != 1 or not rest[0].isdigit():
            raise ValueError("usage: error <row_id> [--note TEXT]")
        row_id = int(rest[0])
        ledger = _open_ledger_checked(_resolve_data_root())
        try:
            row = ledger.mark_prediction_error(row_id, note=note)
        finally:
            ledger.close()
    except Exception as exc:  # noqa: BLE001
        print(f"MARK ERROR: {exc}", file=sys.stderr)
        return 1
    _print_prediction_row(row)
    return 0


# ── main ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m model_prediction.cli_production``."""
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        print(
            "usage: python -m model_prediction.cli_production "
            "{predict|health|status|ledger|settle|void|supersede|error}",
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
    elif cmd == "ledger":
        return _cmd_ledger(args[1:])
    elif cmd == "settle":
        return _cmd_settle(args[1:])
    elif cmd == "void":
        return _cmd_void(args[1:])
    elif cmd == "supersede":
        return _cmd_supersede(args[1:])
    elif cmd == "error":
        return _cmd_error(args[1:])
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        print(
            "usage: python -m model_prediction.cli_production "
            "{predict|health|status|ledger|settle|void|supersede|error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
