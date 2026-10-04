# Production scheduler entrypoint for the model-prediction canary (Windows).
# Windows port of run_production.sh. Run by the supervisor's "production" worker.
#
# Runs: python -m model_prediction.cli_production predict
$ErrorActionPreference = 'Stop'

$RepoRoot = if ($env:MODEL_PREDICTION_REPO_ROOT) { $env:MODEL_PREDICTION_REPO_ROOT } else { Split-Path -Parent $PSScriptRoot }
$RuntimeRoot = if ($env:MODEL_PREDICTION_RUNTIME_ROOT) { $env:MODEL_PREDICTION_RUNTIME_ROOT } else { Join-Path $env:USERPROFILE 'model-prediction-runtime' }

New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeRoot 'logs') | Out-Null

$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    [Console]::Error.WriteLine("[$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))] ERROR: venv python not found at $VenvPython")
    exit 1
}

Set-Location $RepoRoot
$env:MODEL_PREDICTION_REPO_ROOT = $RepoRoot
$env:MODEL_PREDICTION_RUNTIME_ROOT = $RuntimeRoot

& $VenvPython -m model_prediction.cli_production predict
exit $LASTEXITCODE
