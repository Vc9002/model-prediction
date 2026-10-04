# backup_offsite_sync.ps1 -- nightly runtime-root backup + offsite copy (Windows).
# Windows port of backup_offsite_sync.sh. Two legs:
#   1. scripts/backup_runtime_databases.py -- hot, integrity-checked local copies
#      of the canonical SQLite stores into <runtime_root>\backups.
#   2. mirror that directory to OneDrive (offsite). robocopy /MIR is the
#      equivalent of rsync --delete. Replace this leg only if the offsite target moves.
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$RuntimeRoot = if ($env:MODEL_PREDICTION_RUNTIME_ROOT) { $env:MODEL_PREDICTION_RUNTIME_ROOT } else { Join-Path $env:USERPROFILE 'model-prediction-runtime' }
$LocalBackupDir = Join-Path $RuntimeRoot 'backups'
$OffsiteDir = Join-Path $env:USERPROFILE 'OneDrive\model-prediction-offsite-backups'
$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'

Set-Location $RepoRoot
$env:PYTHONPATH = 'src;.'
$env:MODEL_PREDICTION_RUNTIME_ROOT = $RuntimeRoot
& $Py scripts/backup_runtime_databases.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

New-Item -ItemType Directory -Force -Path $OffsiteDir | Out-Null
# robocopy exit codes 0-7 mean success (8+ means a real failure).
robocopy $LocalBackupDir $OffsiteDir /MIR /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Error "offsite mirror failed (robocopy exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}
Write-Output "offsite sync complete: $OffsiteDir"
exit 0
