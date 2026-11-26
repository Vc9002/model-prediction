# dashboard_process_chain.ps1 - which processes are the dashboard server and its launcher chain.
# Dot-sourced by stop_dashboard.ps1. Pure functions over process records (ProcessId,
# ParentProcessId, Name, CommandLine) so tests can feed sample process tables.
#
# One running dashboard is four processes: wscript run_silent.vbs -> powershell -> the venv's
# python.exe launcher stub -> the real python.exe, every one with "dashboard_server.py" in its
# command line. The old stop script matched any command line containing 'dashboard_server', which
# also killed agents' pytest runs of tests/test_dashboard_server.py.

function Test-DashboardServerCommandLine([string]$CommandLine) {
    # True for a launch of the dashboard script itself: "dashboard_server.py" as its own argument
    # (not "test_dashboard_server.py"), in a command line that runs no pytest.
    if ([string]::IsNullOrEmpty($CommandLine)) { return $false }
    if ($CommandLine -match '(?i)pytest') { return $false }
    return [bool]($CommandLine -match '(?i)(^|[\s"''\\/])dashboard_server\.py["'']?(\s|$)')
}

function Test-DashboardLaunchProcess($Process) {
    # True for a process that IS the dashboard or one link of its launcher chain, not merely one
    # naming the file: the process must be python, powershell or wscript, and the command line must
    # run dashboard_server.py as the script of a python interpreter (options other than -m and -c
    # allowed in between). An editor with the file open, `ruff check dashboard_server.py`,
    # `python -m mypy dashboard_server.py` and git all carry the file name and none of them match.
    if (-not $Process) { return $false }
    if ($Process.Name -inotmatch '^(python|pythonw|powershell|wscript)\.exe$') { return $false }
    if (-not (Test-DashboardServerCommandLine $Process.CommandLine)) { return $false }
    $interpreter = '(^|[\s"''\\/])py(thonw?[\d.]*)?(\.exe)?["'']?\s+'
    $options = '((-[XW]\s+\S+|-(?![mcXW])\S*)\s+)*'
    $script = '["'']?([^\s"'']*[\\/])?dashboard_server\.py["'']?(\s|$)'
    return [bool]($Process.CommandLine -match ('(?i)' + $interpreter + $options + $script))
}

function Select-DashboardStopTargets {
    # PIDs to stop: each listener on the dashboard port that is a dashboard launch, then its parents
    # while they are part of the same launcher chain, up to and including the wscript. With no
    # dashboard listener (a server stuck before binding), every dashboard launch instead.
    param(
        [object[]]$Processes,
        [int[]]$ListenerPids
    )
    $byId = @{}
    foreach ($process in @($Processes)) { $byId[[int]$process.ProcessId] = $process }
    $targets = New-Object System.Collections.Generic.List[int]
    foreach ($listener in @($ListenerPids)) {
        $current = $byId[[int]$listener]
        while ($current -and (Test-DashboardLaunchProcess $current)) {
            if (-not $targets.Contains([int]$current.ProcessId)) { $targets.Add([int]$current.ProcessId) }
            if ($current.Name -ieq 'wscript.exe') { break }
            $current = $byId[[int]$current.ParentProcessId]
        }
    }
    if ($targets.Count -eq 0) {
        foreach ($process in @($Processes)) {
            if (Test-DashboardLaunchProcess $process) { $targets.Add([int]$process.ProcessId) }
        }
    }
    return ,$targets.ToArray()
}
