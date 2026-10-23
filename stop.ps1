# stop -- stop the dashboard started by .\dash.ps1 (Windows).
# Stops (1) the PID recorded in .dashboard.pid together with its child
# processes (the venv launcher spawns the python that actually owns the port),
# and (2) anything still listening on the dashboard port whose command line is
# dashboard_server -- e.g. an older server a previous start left behind, which
# would otherwise keep serving stale code. Never a bare name/pattern match.
$PidFile = Join-Path $PSScriptRoot '.dashboard.pid'
$Port = 8765

function Stop-Tree([int]$RootId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$RootId" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-Tree $child.ProcessId }
    if (Get-Process -Id $RootId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $RootId -Force -ErrorAction SilentlyContinue
        Write-Output "Stopped PID $RootId"
        $script:stopped++
    }
}

$stopped = 0
if (Test-Path $PidFile) {
    $PidValue = [int](Get-Content $PidFile -Raw)
    Stop-Tree $PidValue
    Remove-Item -Force $PidFile
}

# Leftover listeners on the dashboard port (and their launcher parents).
$owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($owner in $owners) {
    $info = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
    if ($info -and $info.CommandLine -match 'dashboard_server') {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($info.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parent -and $parent.CommandLine -match 'dashboard_server') { Stop-Tree $parent.ProcessId }
        Stop-Tree $owner
    }
}

if ($stopped -gt 0) {
    Write-Output "Dashboard stopped ($stopped process(es))"
} else {
    Write-Output "No dashboard process found -- already stopped."
}
