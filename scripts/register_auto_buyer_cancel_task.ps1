# register_auto_buyer_cancel_task.ps1 -- register ONLY ModelPrediction-AutoBuyerCancel (quick-261125-hih).
# The cancel-only backstop for live resting fallbacks: every 2 minutes from 08:00 for 18 hours, the
# supervisor runs worker auto-buyer-cancel, which cancels by order id and never places an order.
#
# Unlike scripts/register_windows_tasks.ps1 this touches no other task. It copies the principal
# (user, S4U logon type, run level), the settings (priority 7 = BelowNormal, hidden, time limit,
# IgnoreNew) and the action (run_silent.vbs -> powershell -> run_supervisor, with the runtime root and
# ledger authority env vars) from the live ModelPrediction-AutoBuyer task, and swaps only the worker
# name. It does not start the task. S4U registration may need an elevated shell.
#
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_auto_buyer_cancel_task.ps1
$ErrorActionPreference = 'Stop'

$NewTaskName = 'ModelPrediction-AutoBuyerCancel'
$src = Get-ScheduledTask -TaskName 'ModelPrediction-AutoBuyer'
if ($src.Actions.Count -ne 1) { throw "expected exactly one action on ModelPrediction-AutoBuyer, found $($src.Actions.Count)" }
$srcAction = $src.Actions[0]
$needle = ' run auto-buyer }'
$hits = ([regex]::Matches($srcAction.Arguments, [regex]::Escape($needle))).Count
if ($hits -ne 1) { throw "expected the auto-buyer action to contain '$needle' exactly once, found $hits" }
$newArgs = $srcAction.Arguments.Replace($needle, ' run auto-buyer-cancel }')
$action = New-ScheduledTaskAction -Execute $srcAction.Execute -Argument $newArgs -WorkingDirectory $srcAction.WorkingDirectory

# A daily trigger cannot take a repetition directly, so borrow one from a -Once trigger (same as the auto-buyer).
$trigger = New-ScheduledTaskTrigger -Daily -At '08:00'
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At '08:00' -RepetitionInterval (New-TimeSpan -Minutes 2) -RepetitionDuration (New-TimeSpan -Hours 18)).Repetition

$existing = Get-ScheduledTask -TaskName $NewTaskName -ErrorAction SilentlyContinue
if ($existing) { Unregister-ScheduledTask -TaskName $NewTaskName -Confirm:$false }
Register-ScheduledTask -TaskName $NewTaskName -Action $action -Trigger $trigger -Settings $src.Settings `
    -Principal $src.Principal `
    -Description 'model-prediction auto-buyer cancel backstop (cancel-only by order id, own lock, never places orders)' | Out-Null

$t = Get-ScheduledTask -TaskName $NewTaskName
Write-Output "registered: $NewTaskName (logon $($t.Principal.LogonType), user $($t.Principal.UserId), priority $($t.Settings.Priority), state $($t.State))"
Write-Output "action: $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"
Write-Output "trigger: start $($t.Triggers[0].StartBoundary) every $($t.Triggers[0].Repetition.Interval) for $($t.Triggers[0].Repetition.Duration)"
