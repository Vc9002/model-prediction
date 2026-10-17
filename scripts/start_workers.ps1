# start_workers.ps1 - Resume all Model Prediction background tasks
$ErrorActionPreference = 'SilentlyContinue'

Write-Host "Resuming Model Prediction background workers..." -ForegroundColor Cyan

$tasks = Get-ScheduledTask -TaskName 'ModelPrediction-*' -ErrorAction SilentlyContinue
if ($tasks) {
    foreach ($t in $tasks) {
        Enable-ScheduledTask -TaskName $t.TaskName -ErrorAction SilentlyContinue | Out-Null
        Write-Host "  [+] Enabled: $($t.TaskName)" -ForegroundColor Green
    }
    Write-Host "`nAll background tasks are now ACTIVE." -ForegroundColor Green
} else {
    Write-Host "No ModelPrediction-* scheduled tasks found. You can register them via scripts\register_windows_tasks.ps1" -ForegroundColor Yellow
}
