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
    $vbs = Join-Path $RepoRoot 'scripts\run_silent.vbs'
    $psArgs = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -Command `"& { `$env:MODEL_PREDICTION_RUNTIME_ROOT = '$RuntimeRoot'; `$env:MODEL_PREDICTION_LEDGER_AUTHORITY = 'sqlite'; & '$Py' -m model_prediction.run_supervisor run $Worker }`""
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$vbs`" powershell.exe $psArgs" -WorkingDirectory $RepoRoot
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType $LogonType
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $Triggers -Settings $Settings `
        -Principal $principal -Description $Description | Out-Null
    Write-Output "registered: $TaskName ($Worker, $LogonType, Hidden)"
}

# Daily picks: 11x/day, dense while lines move and games are played, one overnight run (operator 2026-11-22:
# "more during the day, less at night"). 00:30 settles late games and creates the next slate; 07:00 replaces
# 06:00; every 90 min from 09:00 to 21:00. A run takes ~33 min (max ~48), so runs never overlap; if one does,
# the supervisor's lease skips it (exit 75).
$dailyTriggers = @('00:30', '07:00', '09:00', '10:30', '12:00', '13:30', '15:00', '16:30', '18:00', '19:30', '21:00') | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
Register-WorkerTask 'ModelPrediction-Daily' 'daily' $dailyTriggers 'model-prediction daily pipeline (settle, ingest, forecast)'

# Model calibration: 4x/day, following daily settlement
$calibrateTriggers = @('01:00', '07:00', '13:00', '19:00') | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
Register-WorkerTask 'ModelPrediction-Calibrate' 'calibrate' $calibrateTriggers 'model-prediction calibration tournament and drift monitor'

# Production canary and rebuild shadow: every 3 hours, per their launcher headers.
$threeHourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-WorkerTask 'ModelPrediction-Production' 'production' $threeHourly 'model-prediction production canary (predict)'
# Rebuild shadow: frozen 2026-11-13 (operator directive, scope narrowed; see CLAUDE.md
# 'Scope'). The live task was disabled in place, not deleted; re-registering must not
# re-enable it. Uncomment both lines to thaw.
# $threeHourly2 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(35) -RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration (New-TimeSpan -Days 3650)
# Register-WorkerTask 'ModelPrediction-RebuildShadow' 'rebuild-shadow' $threeHourly2 'model-prediction rebuild shadow (isolated, no production writes)'

# Offsite backup: 03:00 daily, matching com.vc.model-backup-offsite.plist.
$vbs = Join-Path $RepoRoot 'scripts\run_silent.vbs'
$backupAction = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument "`"$vbs`" powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$RepoRoot\scripts\backup_offsite_sync.ps1`"" -WorkingDirectory $RepoRoot
$backupTrigger = New-ScheduledTaskTrigger -Daily -At '03:00'
Unregister-ScheduledTask -TaskName 'ModelPrediction-BackupOffsite' -Confirm:$false -ErrorAction SilentlyContinue
$backupPrincipal = New-ScheduledTaskPrincipal -UserId $User -LogonType $LogonType
Register-ScheduledTask -TaskName 'ModelPrediction-BackupOffsite' -Action $backupAction -Trigger $backupTrigger -Settings $Settings `
    -Principal $backupPrincipal -Description 'model-prediction runtime DB backup + offsite copy' | Out-Null
Write-Output "registered: ModelPrediction-BackupOffsite ($LogonType)"

# Auto-buyer: every 15 minutes from 08:00 to 02:00 local (the buyer only buys games within 1h of start, so it needs
# several looks per game; nothing starts between 02:00 and 09:00 ET, and 08:00 still covers NFL London 09:30 kickoffs).
# A daily trigger cannot take a repetition directly, so borrow one from a -Once trigger.
$autoBuyerTriggers = New-ScheduledTaskTrigger -Daily -At '08:00'
$autoBuyerTriggers.Repetition = (New-ScheduledTaskTrigger -Once -At '08:00' -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 18)).Repetition
Register-WorkerTask 'ModelPrediction-AutoBuyer' 'auto-buyer' $autoBuyerTriggers 'model-prediction auto-buyer (paper unless saved state is live)'

# NBA pre-tip capture: every 10 minutes from 11:00 to 02:00 local (this host runs on Eastern time),
# so each game gets ESPN odds and Polymarket quotes about 20 and 10 minutes before tip (NBA v10
# plan Phase 0.1). A daily trigger cannot take a repetition directly, so borrow one from a -Once trigger.
$nbaPretipTrigger = New-ScheduledTaskTrigger -Daily -At '11:00'
$nbaPretipTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At '11:00' -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Hours 15)).Repetition
Register-WorkerTask 'ModelPrediction-NbaPretipCapture' 'nba-pretip-capture' $nbaPretipTrigger 'model-prediction NBA pre-tip ESPN odds and Polymarket quote capture'

# NCAAF pre-kickoff close capture: every 10 minutes from 09:05 to 01:05 local (Eastern), so each
# game's own contracts plus its moneyline and most balanced spread/total are quoted two or three
# times in the last 25 minutes before kickoff (settlement's close; quick 261121-pkc). Offset 5
# minutes from the NBA capture, which shares the gateway's per-IP rate limit. Off-days are free:
# a run with no game in its window makes no exchange request.
$ncaafPrekickoffTrigger = New-ScheduledTaskTrigger -Daily -At '09:05'
$ncaafPrekickoffTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At '09:05' -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Hours 16)).Repetition
Register-WorkerTask 'ModelPrediction-NcaafPrekickoffCapture' 'ncaaf-prekickoff-capture' $ncaafPrekickoffTrigger 'model-prediction NCAAF pre-kickoff Polymarket close capture'
