# install_dashboard_service.ps1 -- run the dashboard at logon (Windows).
# Windows port of install_dashboard_service.sh (which uses macOS launchd). Registers
# a per-user Scheduled Task that starts dashboard_server.py at logon and restarts it
# if it exits. Run once:  powershell -ExecutionPolicy Bypass -File scripts\install_dashboard_service.ps1
# Uninstall:  Unregister-ScheduledTask -TaskName ModelPredictionDashboard -Confirm:$false
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$TaskName = 'ModelPredictionDashboard'
$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$RuntimeRoot = if ($env:MODEL_PREDICTION_RUNTIME_ROOT) { $env:MODEL_PREDICTION_RUNTIME_ROOT } else { Join-Path $RepoRoot 'data' }
$LogDir = Join-Path $RepoRoot 'dashboard'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# The task runs the dashboard through cmd so stdout/stderr land in log files.
$cmd = "set PYTHONPATH=src && set MODEL_PREDICTION_RUNTIME_ROOT=$RuntimeRoot && " +
    "`"$Py`" dashboard_server.py > `"$LogDir\launchd.out.log`" 2> `"$LogDir\launchd.err.log`""
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $cmd" -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'model-prediction dashboard (localhost:8765)' | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4

try {
    Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -UseBasicParsing -TimeoutSec 4 | Out-Null
    Write-Output 'dashboard service installed and running: http://127.0.0.1:8765/'
    Write-Output 'it now starts at logon and restarts itself if it ever crashes.'
} catch {
    Write-Output "service registered but the health check failed -- see $LogDir\launchd.err.log"
    exit 1
}
