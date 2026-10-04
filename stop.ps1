# stop -- stop the dashboard started by .\dash.ps1 (Windows).
# Targets the exact PID recorded in .dashboard.pid, never a name/pattern match.
$PidFile = Join-Path $PSScriptRoot '.dashboard.pid'
if (-not (Test-Path $PidFile)) {
    Write-Output "No PID file -- dashboard wasn't started by .\dash.ps1 (or already stopped)."
    exit 0
}
$PidValue = [int](Get-Content $PidFile -Raw)
$proc = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
if ($proc) {
    Stop-Process -Id $PidValue
    Write-Output "Dashboard stopped (PID $PidValue)"
} else {
    Write-Output "No process at PID $PidValue -- already stopped."
}
Remove-Item -Force $PidFile
