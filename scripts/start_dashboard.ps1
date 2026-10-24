# start_dashboard.ps1 - Start the Model Prediction Web Dashboard
$ErrorActionPreference = 'SilentlyContinue'

Write-Host "Checking Model Prediction Dashboard status..." -ForegroundColor Cyan

$conn = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "Dashboard is ALREADY RUNNING (PID: $($conn.OwningProcess))" -ForegroundColor Green
    Write-Host "Opening http://127.0.0.1:8765/ in browser..." -ForegroundColor Gray
    Start-Process 'http://127.0.0.1:8765/'
    exit 0
}

Write-Host "Enabling and starting ModelPredictionDashboard task..." -ForegroundColor Yellow
Enable-ScheduledTask -TaskName 'ModelPredictionDashboard' -ErrorAction SilentlyContinue | Out-Null
Start-ScheduledTask -TaskName 'ModelPredictionDashboard' -ErrorAction SilentlyContinue

Start-Sleep -Seconds 3
$conn = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue

# Fallback direct start if scheduled task did not start it
if (-not $conn) {
    Write-Host "Starting dashboard server directly..." -ForegroundColor Yellow
    $RepoRoot = Split-Path -Parent $PSScriptRoot
    $vbs = Join-Path $RepoRoot 'scripts\run_silent.vbs'
    $Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    $RuntimeRoot = if ($env:MODEL_PREDICTION_RUNTIME_ROOT) { $env:MODEL_PREDICTION_RUNTIME_ROOT } else { 'E:\model-prediction-runtime' }
    $LogDir = Join-Path $RepoRoot 'dashboard'
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
    
    $psArgs = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -Command `"& { `$env:PYTHONPATH = 'src'; `$env:MODEL_PREDICTION_RUNTIME_ROOT = '$RuntimeRoot'; `$env:MODEL_PREDICTION_LEDGER_AUTHORITY = 'sqlite'; & '$Py' dashboard_server.py 1> '$LogDir\launchd.out.log' 2> '$LogDir\launchd.err.log' }`""
    Start-Process -FilePath 'wscript.exe' -ArgumentList "`"$vbs`" powershell.exe $psArgs" -WorkingDirectory $RepoRoot
    Start-Sleep -Seconds 3
    $conn = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
}

if ($conn) {
    Write-Host "Dashboard STARTED successfully! (PID: $($conn.OwningProcess))" -ForegroundColor Green
    Write-Host "URL: http://127.0.0.1:8765/" -ForegroundColor Cyan
    Write-Host "Opening in browser..." -ForegroundColor Gray
    Start-Process 'http://127.0.0.1:8765/'
} else {
    Write-Host "ERROR: Dashboard failed to start on port 8765." -ForegroundColor Red
    Write-Host "Check log for details: $LogDir\launchd.err.log" -ForegroundColor Yellow
}
