# verify_integrity_overlap.ps1 -- run BOTH audit-chain verifiers (Windows).
# Windows port of verify_integrity_overlap.sh.
#   1. legacy chain:  python -m model_prediction.cli verify-chain  (data/events.jsonl)
#   2. SQLite chain:  python -m model_prediction.ledger_parity verify-integrity
# Exit 0 only when both are green. The legacy CLI always exits 0, so its
# greenness comes from parsing chain_intact; the SQLite exit code is authoritative.
$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$env:PYTHONPATH = 'src;.'
$Fail = 0

if ($env:MODEL_PREDICTION_RUNTIME_ROOT) {
    Write-Output "SQLite store root: $env:MODEL_PREDICTION_RUNTIME_ROOT (MODEL_PREDICTION_RUNTIME_ROOT set)"
} else {
    Write-Output 'SQLite store root: repo data/ (MODEL_PREDICTION_RUNTIME_ROOT unset)'
}
Write-Output ''

Write-Output '=== legacy verify-chain (data/events.jsonl) ==='
$legacyOut = & $Py -m model_prediction.cli verify-chain 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Write-Output 'verify-chain errored'
    Write-Output $legacyOut
    exit 1
}
$legacy = $legacyOut | ConvertFrom-Json
if ($legacy.chain_intact -eq $true) {
    Write-Output "legacy chain: OK ($($legacy.audit_lines) events)"
} else {
    Write-Output 'legacy chain: BROKEN'
    $legacyOut -split "`n" | Select-String -Pattern 'break_count|chain_intact|audit_lines' | ForEach-Object { Write-Output $_.Line }
    $Fail = 1
}
Write-Output ''

Write-Output '=== SQLite verify-integrity (ledger_events) ==='
$sqliteOut = & $Py -m model_prediction.ledger_parity verify-integrity 2>&1 | Out-String
$sqliteCode = $LASTEXITCODE
Write-Output $sqliteOut
if ($sqliteCode -ne 0) { $Fail = 1 }
Write-Output ''

if ($Fail -ne 0) {
    Write-Output 'OVERLAP INTEGRITY: NOT GREEN'
    exit 1
}
Write-Output 'OVERLAP INTEGRITY: GREEN (legacy + SQLite)'
exit 0
