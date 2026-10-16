# register_windows_tasks.ps1 -- schedule the model-prediction workers (Windows).
# Windows counterpart of the launchd plists in ops/launchd/. Every scheduled
# run goes through run_supervisor, which owns the leases and records each run.
# The auto-buyer runs every 30 minutes, matching the launchd plist. It only places
# real orders when the saved state is mode=live, so this task is paper-safe by default.
# Re-run safely; existing tasks with these names are replaced.
param(
    [ValidateSet('Interactive', 'S4U')]
    [string]$LogonType = 'Interactive',
    [string]$User = $env:USERNAME
)
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$RuntimeRoot = if ($env:MODEL_PREDICTION_RUNTIME_ROOT) { $env:MODEL_PREDICTION_RUNTIME_ROOT.Trim() } else { Join-Path $RepoRoot 'data' }
$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew -Hidden

function Register-WorkerTask([string]$TaskName, [string]$Worker, $Triggers, [string]$Description) {
    $psArgs = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -Command `"& { `$env:MODEL_PREDICTION_RUNTIME_ROOT = '$RuntimeRoot'; `$env:MODEL_PREDICTION_LEDGER_AUTHORITY = 'sqlite'; & '$Py' -m model_prediction.run_supervisor run $Worker }`""
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory $RepoRoot
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType $LogonType
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $Triggers -Settings $Settings `
        -Principal $principal -Description $Description | Out-Null
    Write-Output "registered: $TaskName ($Worker, $LogonType, Hidden)"
}

# Daily picks: 4x/day, matching com.modelprediction.daily.plist.
$dailyTriggers = @('00:30', '06:00', '12:00', '18:00') | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
Register-WorkerTask 'ModelPrediction-Daily' 'daily' $dailyTriggers 'model-prediction daily pipeline (settle, ingest, forecast)'

# Model calibration: 4x/day, following daily settlement
$calibrateTriggers = @('01:00', '07:00', '13:00', '19:00') | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
Register-WorkerTask 'ModelPrediction-Calibrate' 'calibrate' $calibrateTriggers 'model-prediction calibration tournament and drift monitor'

# Production canary and rebuild shadow: every 3 hours, per their launcher headers.
$threeHourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-WorkerTask 'ModelPrediction-Production' 'production' $threeHourly 'model-prediction production canary (predict)'
$threeHourly2 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(35) -RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-WorkerTask 'ModelPrediction-RebuildShadow' 'rebuild-shadow' $threeHourly2 'model-prediction rebuild shadow (isolated, no production writes)'

# Offsite backup: 03:00 daily, matching com.vc.model-backup-offsite.plist.
$backupAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$RepoRoot\scripts\backup_offsite_sync.ps1`"" -WorkingDirectory $RepoRoot
$backupTrigger = New-ScheduledTaskTrigger -Daily -At '03:00'
Unregister-ScheduledTask -TaskName 'ModelPrediction-BackupOffsite' -Confirm:$false -ErrorAction SilentlyContinue
$backupPrincipal = New-ScheduledTaskPrincipal -UserId $User -LogonType $LogonType
Register-ScheduledTask -TaskName 'ModelPrediction-BackupOffsite' -Action $backupAction -Trigger $backupTrigger -Settings $Settings `
    -Principal $backupPrincipal -Description 'model-prediction runtime DB backup + offsite copy' | Out-Null
Write-Output "registered: ModelPrediction-BackupOffsite ($LogonType)"

# Auto-buyer: every 30 minutes, matching com.modelprediction.auto-buyer.plist.
$autoBuyerTriggers = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(10) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-WorkerTask 'ModelPrediction-AutoBuyer' 'auto-buyer' $autoBuyerTriggers 'model-prediction auto-buyer (paper unless saved state is live)'
