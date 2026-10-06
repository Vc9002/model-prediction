# dash -- start the dashboard (Windows).
# Windows port of ./dash. Records its PID to .dashboard.pid so .\stop.ps1 can
# target this exact process (no pattern-matched kill).
$ErrorActionPreference = 'Stop'

$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot
$RuntimeRoot = if ($env:MODEL_PREDICTION_RUNTIME_ROOT) { $env:MODEL_PREDICTION_RUNTIME_ROOT } else { Join-Path $RepoRoot 'data' }
$env:MODEL_PREDICTION_RUNTIME_ROOT = $RuntimeRoot
$env:PYTHONPATH = 'src'

$PidFile = Join-Path $RepoRoot '.dashboard.pid'
$Port = 8765
$Url = "http://localhost:$Port/"

if (Test-Path $PidFile) {
    try {
        $existing = [int]((Get-Content $PidFile -Raw).Trim())
        $p = Get-Process -Id $existing -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -match 'python') {
            try {
                Invoke-WebRequest -Uri "${Url}api/ping" -UseBasicParsing -TimeoutSec 2 | Out-Null
                Write-Output "Dashboard already running (PID $existing): $Url"
                exit 0
            } catch {}
        }
    } catch {}
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$LogDir = Join-Path $RuntimeRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$proc = Start-Process -FilePath $Python -ArgumentList "dashboard_server.py", "--port", "$Port" `
    -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $LogDir 'dashboard.out.log') `
    -RedirectStandardError (Join-Path $LogDir 'dashboard.err.log')
Set-Content -Path $PidFile -Value $proc.Id

for ($i = 0; $i -lt 240; $i++) {
    try {
        Invoke-WebRequest -Uri "${Url}api/ping" -UseBasicParsing -TimeoutSec 5 | Out-Null
        Write-Output "Dashboard: $Url"
        exit 0
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
Write-Error "Dashboard failed to start -- see $LogDir\dashboard.err.log"
exit 1
