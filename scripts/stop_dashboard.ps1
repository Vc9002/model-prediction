# stop_dashboard.ps1 - Stop the Model Prediction Web Dashboard
$ErrorActionPreference = 'SilentlyContinue'

Write-Host "Stopping Model Prediction Dashboard..." -ForegroundColor Cyan

# 1. Disable and stop the Scheduled Task so Windows does not auto-restart it
Disable-ScheduledTask -TaskName 'ModelPredictionDashboard' -ErrorAction SilentlyContinue | Out-Null
Stop-ScheduledTask -TaskName 'ModelPredictionDashboard' -ErrorAction SilentlyContinue | Out-Null

# 2. Terminate any Python process running dashboard_server.py
$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'dashboard_server' }
if ($procs) {
    $procs | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Terminated dashboard_server process (PID: $($_.ProcessId))" -ForegroundColor Gray
    }
}

# 3. Ensure port 8765 is released
$conn = Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue
if ($conn) {
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "Released port 8765 from PID: $($conn.OwningProcess)" -ForegroundColor Gray
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
