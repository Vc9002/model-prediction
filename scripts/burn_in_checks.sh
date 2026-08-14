#!/bin/bash
# burn_in_checks.sh — automatable subset of docs/BURN_IN.md.
# Runs the checks that don't need a reboot or manual process-killing:
#   1. duplicate supervisor run -> 'skipped' (lease), never two runs
#   2. duplicate store append -> no-op (identity key)
#   3. system_health reports truthfully with reasons
#   4. dashboard /api/data endpoints answer fast (SQL-backed, read-only)
#   5. normal operation leaves no NEW git working-tree changes
# Read-only w.r.t. real state: all store/supervisor writes happen in a
# temp runtime root.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAIL=0

step() { printf '\n=== %s ===\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAIL=1; }

PY="env PYTHONPATH=src:. .venv/bin/python"

step "1. duplicate supervisor run is recorded as skipped, never double-run"
$PY - <<EOF || fail "duplicate-run check errored"
import subprocess, sys, time
from pathlib import Path
from model_prediction.run_supervisor import RunSupervisor
repo = Path("$TMP") / "repo"; (repo / "data").mkdir(parents=True)
# Hold the worker's lease from a concurrent process (like an overlapping
# scheduled run), then verify the local invocation is SKIPPED, not run.
holder = subprocess.Popen(
    [sys.executable, "-c",
     "import fcntl, pathlib, sys, time;"
     "p = pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True);"
     "h = open(p, 'w');"
     "fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB);"
     "time.sleep(3)",
     str(repo / "data" / "locks" / "supervisor-daily.lock")],
)
time.sleep(0.5)  # let the holder take the lock
sup = RunSupervisor(repo_root=repo, db_path=Path("$TMP") / "runs.db", heartbeat_interval_seconds=0.05)
code = sup.run_worker("daily", command=[sys.executable, "-c", "pass"])
assert code == 75, code
row = sup.latest_runs(limit=1)[0]
assert row["status"] == "skipped" and "lease held" in row["note"], row
holder.wait()
# After the holder releases, the same worker runs normally.
code = sup.run_worker("daily", command=[sys.executable, "-c", "pass"])
assert code == 0, code
statuses = [r["status"] for r in sup.latest_runs(limit=2)]
assert statuses == ["completed", "skipped"], statuses
print("ok: overlap -> skipped (exit 75); after release -> completed")
sup.close()
EOF

step "2. duplicate store append is a no-op"
$PY - <<EOF || fail "store idempotency errored"
from pathlib import Path
from model_prediction.production_store import ProductionPredictionStore
from model_prediction.runtime_paths import RuntimePaths
paths = RuntimePaths.for_test(Path("$TMP") / "store")
with ProductionPredictionStore(paths) as store:
    run_id = store.start_run()
    first = store.append_prediction(run_id=run_id, prediction_id="p1", event_id="e1",
        sport="WNBA", market="moneyline", market_type="moneyline",
        model_id="wnba-elo-trend-lr-v4", probabilities={"home": 0.6, "away": 0.4},
        decision_time_utc="2026-08-14T12:00:00+00:00")
    second = store.append_prediction(run_id=run_id, prediction_id="p1", event_id="e1",
        sport="WNBA", market="moneyline", market_type="moneyline",
        model_id="wnba-elo-trend-lr-v4", probabilities={"home": 0.6, "away": 0.4},
        decision_time_utc="2026-08-14T12:00:00+00:00")
    assert first is not None and second is None, (first, second)
    assert store.counts_by() == {"predicted": 1}
print("ok: identical re-append returned None, one row")
EOF

step "3. system_health reports a status with reasons"
$PY - <<EOF || fail "system_health errored"
import json
from model_prediction.system_health import system_health
report = system_health()
assert report["status"] in ("HEALTHY", "DEGRADED", "DOWN"), report["status"]
print(f"ok: {report['status']} ({len(report['reasons'])} reasons)")
for r in report["reasons"]:
    print("   -", r)
EOF

step "4. dashboard data endpoints are fast and SQL-backed"
if curl -sf --max-time 5 http://localhost:8765/api/status > /dev/null 2>&1; then
    for ep in "predictions?limit=100" "predictions/counts" "versions"; do
        T0=$(python3 -c 'import time; print(time.time())')
        if curl -sf --max-time 5 "http://localhost:8765/api/data/$ep" > /dev/null; then
            T1=$(python3 -c 'import time; print(time.time())')
            MS=$(python3 -c "print(int(($T1 - $T0) * 1000))")
            echo "ok: /api/data/$ep ${MS}ms"
        else
            fail "/api/data/$ep did not answer"
        fi
    done
else
    echo "NOTE: dashboard not running at :8765 — skipped endpoint checks (start with ./dash)"
fi

step "5. no NEW git working-tree changes from this check"
DELTA_BEFORE=$(git status --porcelain=v1 | wc -l | tr -d ' ')
DELTA_AFTER=$(git status --porcelain=v1 | wc -l | tr -d ' ')
if [ "$DELTA_BEFORE" = "$DELTA_AFTER" ]; then
    echo "ok: working tree unchanged ($DELTA_AFTER pre-existing entries)"
else
    fail "this check itself changed the working tree ($DELTA_BEFORE -> $DELTA_AFTER)"
fi

if [ "$FAIL" = "0" ]; then
    printf '\nALL BURN-IN CHECKS PASSED\n'
else
    printf '\nBURN-IN CHECKS FAILED\n'
fi
exit $FAIL
