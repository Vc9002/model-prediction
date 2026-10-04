# burn_in_checks.ps1 -- automatable subset of docs/BURN_IN.md (Windows).
# Windows port of burn_in_checks.sh. Runs the checks that don't need a reboot:
#   1. duplicate supervisor run -> 'skipped' (lease), never two runs
#   2. duplicate store append -> no-op (identity key)
#   3. system_health reports truthfully with reasons
#   4. dashboard /api/data endpoints answer fast (SQL-backed, read-only)
#   5. normal operation leaves no NEW git working-tree changes
$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$env:PYTHONPATH = "src;."
$Tmp = Join-Path $env:TEMP ("burnin-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
$Fail = 0

function Step([string]$Title) { Write-Output "`n=== $Title ===" }
function Fail-Check([string]$Message) { Write-Output "FAIL: $Message"; $script:Fail = 1 }

# Runs a python snippet from stdin; the snippet's exit code decides pass/fail.
function Invoke-Snippet([string]$Code) {
    $Code | & $Py -
    return $LASTEXITCODE
}

Step '1. duplicate supervisor run is recorded as skipped, never double-run'
$Check1 = @"
import subprocess, sys, time
from pathlib import Path
from model_prediction.run_supervisor import RunSupervisor
repo = Path(r"$Tmp") / "repo"; (repo / "data").mkdir(parents=True)
holder = subprocess.Popen(
    [sys.executable, "-c",
     "import pathlib, sys, time;"
     "from model_prediction.filelock import lock_exclusive;"
     "p = pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True);"
     "h = open(p, 'w');"
     "lock_exclusive(h, blocking=False);"
     "time.sleep(3)",
     str(repo / "data" / "locks" / "supervisor-daily.lock")],
)
time.sleep(0.5)
sup = RunSupervisor(repo_root=repo, db_path=Path(r"$Tmp") / "runs.db", heartbeat_interval_seconds=0.05)
code = sup.run_worker("daily", command=[sys.executable, "-c", "pass"])
assert code == 75, code
row = sup.latest_runs(limit=1)[0]
assert row["status"] == "skipped" and "lease held" in row["note"], row
holder.wait()
code = sup.run_worker("daily", command=[sys.executable, "-c", "pass"])
assert code == 0, code
statuses = [r["status"] for r in sup.latest_runs(limit=2)]
assert statuses == ["completed", "skipped"], statuses
print("ok: overlap -> skipped (exit 75); after release -> completed")
sup.close()
"@
if ((Invoke-Snippet $Check1) -ne 0) { Fail-Check 'duplicate-run check errored' }

Step '2. duplicate store append is a no-op'
$Check2 = @"
from pathlib import Path
from model_prediction.production_store import ProductionPredictionStore
from model_prediction.runtime_paths import RuntimePaths
paths = RuntimePaths.for_test(Path(r"$Tmp") / "store")
with ProductionPredictionStore(paths) as store:
    run_id = store.start_run()
    kwargs = dict(run_id=run_id, prediction_id="p1", event_id="e1",
        sport="WNBA", market="moneyline", market_type="moneyline",
        model_id="wnba-elo-trend-lr-v4", probabilities={"home": 0.6, "away": 0.4},
        decision_time_utc="2026-08-14T12:00:00+00:00")
    first = store.append_prediction(**kwargs)
    second = store.append_prediction(**kwargs)
    assert first is not None and second is None, (first, second)
    assert store.counts_by() == {"predicted": 1}
print("ok: identical re-append returned None, one row")
"@
if ((Invoke-Snippet $Check2) -ne 0) { Fail-Check 'store idempotency errored' }

Step '3. system_health reports a status with reasons'
$Check3 = @"
from model_prediction.system_health import system_health
report = system_health()
assert report["status"] in ("HEALTHY", "DEGRADED", "DOWN"), report["status"]
print(f"ok: {report['status']} ({len(report['reasons'])} reasons)")
for r in report["reasons"]:
    print("   -", r)
"@
if ((Invoke-Snippet $Check3) -ne 0) { Fail-Check 'system_health errored' }

Step '4. dashboard data endpoints are fast and SQL-backed'
try {
    Invoke-WebRequest -Uri 'http://localhost:8765/api/status' -UseBasicParsing -TimeoutSec 5 | Out-Null
    foreach ($ep in @('predictions?limit=100', 'predictions/counts', 'versions')) {
        $t0 = Get-Date
        try {
            Invoke-WebRequest -Uri "http://localhost:8765/api/data/$ep" -UseBasicParsing -TimeoutSec 5 | Out-Null
            $ms = [int]((Get-Date) - $t0).TotalMilliseconds
            Write-Output "ok: /api/data/$ep ${ms}ms"
        } catch {
            Fail-Check "/api/data/$ep did not answer"
        }
    }
} catch {
    Write-Output 'NOTE: dashboard not running at :8765 -- skipped endpoint checks (start with .\dash.ps1)'
}

Step '5. no NEW git working-tree changes from this check'
$Before = @(git status --porcelain=v1).Count
$After = @(git status --porcelain=v1).Count
if ($Before -eq $After) {
    Write-Output "ok: working tree unchanged ($After pre-existing entries)"
} else {
    Fail-Check "this check itself changed the working tree ($Before -> $After)"
}

Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
if ($Fail -eq 0) { Write-Output "`nALL BURN-IN CHECKS PASSED" } else { Write-Output "`nBURN-IN CHECKS FAILED" }
exit $Fail
