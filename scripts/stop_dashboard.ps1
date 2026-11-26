# stop_dashboard.ps1 - Stop the Model Prediction Web Dashboard
$ErrorActionPreference = 'SilentlyContinue'

Write-Host "Stopping Model Prediction Dashboard..." -ForegroundColor Cyan

# 1. Disable and stop the Scheduled Task so Windows does not auto-restart it
Disable-ScheduledTask -TaskName 'ModelPredictionDashboard' -ErrorAction SilentlyContinue | Out-Null
Stop-ScheduledTask -TaskName 'ModelPredictionDashboard' -ErrorAction SilentlyContinue | Out-Null

# 2. Stop the server that listens on 8765 and its launcher chain (python, the venv launcher stub,
#    powershell, wscript), by PID and parent walk. Matching every command line that merely contains
#    'dashboard_server' also killed pytest runs of tests/test_dashboard_server.py.
. (Join-Path $PSScriptRoot 'dashboard_process_chain.ps1')
$processes = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine)
$listeners = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($id in (Select-DashboardStopTargets -Processes $processes -ListenerPids $listeners)) {
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    Write-Host "Terminated dashboard process (PID: $id)" -ForegroundColor Gray
}

Start-Sleep -Seconds 1
$check = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $check) {
    Write-Host "Dashboard STOPPED successfully." -ForegroundColor Green
    Write-Host "Auto-start at logon has been disabled." -ForegroundColor Cyan
    Write-Host "Port 8765 is completely free." -ForegroundColor Gray
} else {
    Write-Host "Warning: Port 8765 is still in use by PID: $($check.OwningProcess)" -ForegroundColor Red
}
