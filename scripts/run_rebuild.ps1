# Rebuild shadow scheduler entrypoint (Windows).
# Windows port of run_rebuild.sh. Runs the ISOLATED rebuild pipeline once per
# sport enabled in config/rebuild.yaml for today's ET date. Shadow-only; it
# never invokes the incumbent `model_prediction.cli forecast`.
$ErrorActionPreference = 'Continue'

function Get-Stamp { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }

# First output line, deliberately before anything can fail silently.
Write-Output "[start] rebuild-shadow $(Get-Stamp)"

$RepoRoot = if ($env:MODEL_PREDICTION_REPO_ROOT) { $env:MODEL_PREDICTION_REPO_ROOT } else { Split-Path -Parent $PSScriptRoot }
$RuntimeRoot = if ($env:MODEL_PREDICTION_RUNTIME_ROOT) { $env:MODEL_PREDICTION_RUNTIME_ROOT } else { Join-Path $env:USERPROFILE 'model-prediction-runtime' }
$RebuildLog = Join-Path $RuntimeRoot 'logs\rebuild.log'
New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeRoot 'logs') | Out-Null

$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    Write-Output "[$(Get-Stamp)] ERROR: venv python not found at $VenvPython"
    exit 1
}

Set-Location $RepoRoot
$env:MODEL_PREDICTION_REPO_ROOT = $RepoRoot
$env:MODEL_PREDICTION_RUNTIME_ROOT = $RuntimeRoot

$RunDate = [TimeZoneInfo]::ConvertTimeFromUtc((Get-Date).ToUniversalTime(),
    [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')).ToString('yyyy-MM-dd')
# Late horizon matches docs/rebuild/OPERATIONS.md's standard smoke shape.
$Horizon = 'late'

# Enabled sports only: a disabled sport is a deliberate state, not a failure.
$ReadSportsScript = @'
import yaml
from pathlib import Path
cfg = yaml.safe_load(Path("config/rebuild.yaml").read_text())
print(" ".join(
    sport for sport, entry in cfg.get("sports", {}).items()
    if isinstance(entry, dict) and entry.get("enabled", True)
))
'@
$Sports = ($ReadSportsScript | & $VenvPython -).Trim()

# An empty list is a config-read failure, not a legitimate "nothing to do".
if (-not $Sports) {
    Add-Content -Path $RebuildLog -Encoding utf8 -Value "[$(Get-Stamp)] rebuild-shadow: no enabled sports derived from config/rebuild.yaml -- exiting non-zero"
    exit 1
}

# One sport failing must not stop the others; a non-zero exit signals partial failure.
$Failed = 0
foreach ($sport in $Sports.Split(' ', [StringSplitOptions]::RemoveEmptyEntries)) {
    Add-Content -Path $RebuildLog -Encoding utf8 -Value "[$(Get-Stamp)] rebuild-shadow $sport $RunDate $Horizon"
    & $VenvPython -m model_prediction.rebuild.cli --sport $sport --date $RunDate --horizon $Horizon 2>&1 |
        Out-File -FilePath $RebuildLog -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Add-Content -Path $RebuildLog -Encoding utf8 -Value "[$(Get-Stamp)] rebuild-shadow $sport FAILED"
        $Failed = 1
    }
}
exit $Failed
