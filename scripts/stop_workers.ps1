# stop_workers.ps1 - Pause all Model Prediction background tasks
$ErrorActionPreference = 'SilentlyContinue'

Write-Host "Pausing all Model Prediction background workers..." -ForegroundColor Cyan

$tasks = Get-ScheduledTask -TaskName 'ModelPrediction-*' -ErrorAction SilentlyContinue
if ($tasks) {
    foreach ($t in $tasks) {
        Stop-ScheduledTask -TaskName $t.TaskName -ErrorAction SilentlyContinue | Out-Null
        Disable-ScheduledTask -TaskName $t.TaskName -ErrorAction SilentlyContinue | Out-Null
        Write-Host "  [-] Paused: $($t.TaskName)" -ForegroundColor Yellow
    }
    
    # Also terminate any running run_supervisor Python process
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'model_prediction\.run_supervisor' }
    if ($procs) {
        $procs | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "  [-] Terminated running worker (PID: $($_.ProcessId))" -ForegroundColor Gray
        }
    }
    
    Write-Host "`nAll background tasks are now PAUSED (CPU & Disk idle)." -ForegroundColor Green
} else {
    Write-Host "No ModelPrediction-* scheduled tasks found." -ForegroundColor Yellow
}
