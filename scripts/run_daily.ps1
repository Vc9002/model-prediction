# model-prediction daily runner -- Windows port of run_daily.sh.
# Step 1: Settle ALL open picks from previous days (both ledgers)
# Step 2: Unified slate + forecast:
#   - MLB/WNBA qualified calls -> main
#   - all learned US-sport candidates -> flat
#   - soccer/esports/KBO/NPB -> research, valid subset -> gated research
# Re-running is safe: clears and replaces today's picks on each run.
$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$ScriptPath = $MyInvocation.MyCommand.Path

# Hold one non-blocking lock for the whole settle/ingest/forecast workflow.
# Re-entry runs with the lock-held marker set and skips this wrapper.
if ($env:MODEL_PREDICTION_DAILY_LOCK_HELD -ne '1') {
    $env:MODEL_PREDICTION_DAILY_LOCK_HELD = '1'
    $env:PYTHONPATH = 'src' + $(if ($env:PYTHONPATH) { ';' + $env:PYTHONPATH } else { '' })
    $RuntimeRoot = if ($env:MODEL_PREDICTION_RUNTIME_ROOT) { $env:MODEL_PREDICTION_RUNTIME_ROOT.Trim() } else { 'data' }
    & $Py -m model_prediction.daily_lock --lock (Join-Path $RuntimeRoot 'locks\daily.lock') -- `
        powershell -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File $ScriptPath
    exit $LASTEXITCODE
}

function Get-NewYorkDate([int]$OffsetDays = 0) {
    $tz = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
    $now = [TimeZoneInfo]::ConvertTimeFromUtc((Get-Date).ToUniversalTime(), $tz)
    return $now.AddDays($OffsetDays).ToString('yyyy-MM-dd')
}

function Write-Log([string]$Message) {
    Add-Content -Path $Log -Value $Message -Encoding utf8
}

# Runs a python command with PYTHONPATH set, appending its output to the log.
# Returns the command's exit code.
function Invoke-Step([string[]]$PyArgs, [string]$PythonPath = 'src') {
    $env:PYTHONPATH = $PythonPath
    & $Py @PyArgs 2>&1 | Out-File -FilePath $Log -Append -Encoding utf8
    return $LASTEXITCODE
}

$RunDate = Get-NewYorkDate
$Yesterday = Get-NewYorkDate -1
$Log = "data\logs\daily_$RunDate.log"
New-Item -ItemType Directory -Force -Path 'data\logs' | Out-Null

# Load .env (KEY=VALUE lines), the equivalent of `set -a; source .env`.
if (Test-Path '.env') {
    foreach ($line in Get-Content '.env') {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $value = $Matches[2].Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($Matches[1], $value, 'Process')
        }
    }
}

$Stopwatch = [Diagnostics.Stopwatch]::StartNew()
Write-Log "=== model-prediction daily $RunDate (America/New_York) ==="
Write-Log ("Started: " + (Get-Date -Format o))

# -- Preflight ----------------------------------------------------------------
# The scheduled job runs the live working tree, which can be mid-edit. A
# config/model.yaml validation error or a SyntaxError/ImportError in the CLI
# package makes every step below fail identically (the 2026-10-24 01:00 EDT run
# spent 53 minutes on 17 identical tracebacks), so stop before step 1 instead.
$PreflightExit = Invoke-Step @('-c', 'import model_prediction.cli; from model_prediction.config import load_config; load_config()')
if ($PreflightExit -ne 0) {
    Write-Log "PREFLIGHT FAILED (exit $PreflightExit): config or CLI import is broken; no step was run."
    exit 1
}

# -- Step 1: Settlement ------------------------------------------------------
Write-Log '--- Step 1: Settlement ---'
$Stopwatch.Restart()
$SettleExit = Invoke-Step @('-m', 'model_prediction.cli', 'settle', '--all-unsettled')
Write-Log "Settlement exit code: $SettleExit"
Write-Log ("Settlement took: {0}s" -f [int]$Stopwatch.Elapsed.TotalSeconds)

# -- Step 1c: MLB v9 Flat benchmark settlement -------------------------------
Write-Log '--- Step 1c: MLB v9 benchmark settlement ---'
$Stopwatch.Restart()
$V9SettleExit = Invoke-Step @('scripts/forecast_mlb_v9_benchmark.py', '--settle') 'src;.'
Write-Log "MLB v9 settlement exit code: $V9SettleExit"
Write-Log ("MLB v9 settlement took: {0}s" -f [int]$Stopwatch.Elapsed.TotalSeconds)

# -- Step 1b: Historical game ingestion --------------------------------------
# Yesterday and today are both ingested so a single missed run self-heals.
# Each sport writes its own dataset, so sports run as parallel jobs; each
# sport's two dates stay sequential to avoid racing on the same file.
Write-Log '--- Step 1b: Historical game ingestion ---'
$Stopwatch.Restart()
$Sports = @('mlb', 'nba', 'wnba', 'nfl', 'ncaaf', 'ncaab', 'tennis')
$IngestExit = 0
# One job per sport (each pays the ~12s CLI import once per date, and the
# imports overlap instead of queueing 14 deep). Output is collected per job so
# lines from different sports do not interleave in the log.
$env:PYTHONPATH = 'src'
$IngestJobs = foreach ($sport in $Sports) {
    Start-Job -ArgumentList $Py, $RepoRoot, $sport, $Yesterday, $RunDate -ScriptBlock {
        param($Py, $RepoRoot, $sport, $d1, $d2)
        Set-Location $RepoRoot
        $worst = 0
        foreach ($d in @($d1, $d2)) {
            & $Py -m model_prediction.cli ingest --sport $sport --date $d 2>&1
            if ($LASTEXITCODE -ne 0) { $worst = $LASTEXITCODE }
        }
        "INGEST_EXIT=$worst"
    }
}
foreach ($job in $IngestJobs) {
    $lines = @(Receive-Job -Job $job -Wait -AutoRemoveJob)
    $lines | Where-Object { $_ -notmatch '^INGEST_EXIT=' } | Out-File -FilePath $Log -Append -Encoding utf8
    $exitLine = $lines | Where-Object { $_ -match '^INGEST_EXIT=' } | Select-Object -Last 1
    $code = if ($exitLine) { [int]($exitLine -replace '^INGEST_EXIT=', '') } else { 1 }
    if ($code -ne 0) { $IngestExit = $code }
}
Write-Log "Ingestion exit code: $IngestExit"
Write-Log ("Ingestion took: {0}s" -f [int]$Stopwatch.Elapsed.TotalSeconds)

# -- Step 1d: Automated Model Calibration ------------------------------------
Write-Log '--- Step 1d: Automated Model Calibration ---'
$Stopwatch.Restart()
$CalibrateExit = Invoke-Step @('-m', 'model_prediction.cli', 'calibrate', '--all', '--update-artifacts')
Write-Log "Calibration exit code: $CalibrateExit"
Write-Log ("Calibration took: {0}s" -f [int]$Stopwatch.Elapsed.TotalSeconds)

# -- Step 2: Unified daily forecast ------------------------------------------
Write-Log '--- Step 2: Unified slate + main/flat/research forecasts ---'
$Stopwatch.Restart()
$DailyExit = Invoke-Step @('-m', 'model_prediction.cli', 'daily', '--date', $RunDate, '--skip-settlement')
Write-Log "Unified daily exit code: $DailyExit"
Write-Log ("Unified daily took: {0}s" -f [int]$Stopwatch.Elapsed.TotalSeconds)

# -- Step 2b: MLB v9 Challenger flat forecast --------------------------------
Write-Log '--- Step 2b: MLB v9 Challenger flat forecast ---'
$Stopwatch.Restart()
$V9ForecastExit = Invoke-Step @('scripts/forecast_mlb_v9_benchmark.py') 'src;.'
Write-Log "MLB v9 forecast exit code: $V9ForecastExit"
Write-Log ("MLB v9 forecast took: {0}s" -f [int]$Stopwatch.Elapsed.TotalSeconds)

# -- Step 2c: Automated Health and Data Gap Monitor ---------------------------
Write-Log '--- Step 2c: Automated Health and Data Gap Monitor ---'
$Stopwatch.Restart()
$HealthExit = Invoke-Step @('scripts/health_and_data_gap_monitor.py', '--auto-heal') 'src;.'
Write-Log "Health monitor exit code: $HealthExit"
Write-Log ("Health monitor took: {0}s" -f [int]$Stopwatch.Elapsed.TotalSeconds)

# -- Step 3: Automated Log Retention and WAL Checkpointing --------------------
Write-Log '--- Step 3: Automated Log Retention and WAL Maintenance ---'
$Stopwatch.Restart()
$MaintenanceExit = Invoke-Step @('scripts/rotate_logs.py', '--max-age-days', '14') 'src;.'
Write-Log "Log maintenance exit code: $MaintenanceExit"
Write-Log ("Log maintenance took: {0}s" -f [int]$Stopwatch.Elapsed.TotalSeconds)

Write-Log ("Finished: " + (Get-Date -Format o))
Write-Log "Exit codes -- settle: $SettleExit, v9_settle: $V9SettleExit, ingest: $IngestExit, calibrate: $CalibrateExit, daily: $DailyExit, v9_forecast: $V9ForecastExit, health: $HealthExit, maintenance: $MaintenanceExit"

if ($SettleExit -ne 0 -or $V9SettleExit -ne 0 -or $IngestExit -ne 0 -or $CalibrateExit -ne 0 -or $DailyExit -ne 0 -or $V9ForecastExit -ne 0 -or $HealthExit -ne 0 -or $MaintenanceExit -ne 0) {
    exit 1
}
exit 0
