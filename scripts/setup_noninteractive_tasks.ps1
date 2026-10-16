# setup_noninteractive_tasks.ps1 -- Elevate and configure ModelPrediction tasks for non-interactive (reboot-persistent) execution.
# Windows Task Scheduler requires Administrator privileges to register S4U (Service For User) tasks.
#
# When configured with S4U:
#   - Tasks run whether the user is logged on or not
#   - Execution persists through machine reboots and user logouts
#   - No plaintext passwords are stored

$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "Elevated administrator privileges required to register S4U tasks." -ForegroundColor Yellow
    Write-Host "Requesting UAC elevation..." -ForegroundColor Cyan
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Write-Host "Configuring ModelPrediction tasks with LogonType S4U (non-interactive, run without login)..." -ForegroundColor Green
& "$RepoRoot\scripts\register_windows_tasks.ps1" -LogonType S4U -User $env:USERNAME

Write-Host ""
Write-Host "Verification:" -ForegroundColor Cyan
Get-ScheduledTask | Where-Object { $_.TaskName -like "*ModelPrediction*" } |
    Select-Object TaskName, State, @{Name="LogonType";Expression={$_.Principal.LogonType}}, @{Name="UserId";Expression={$_.Principal.UserId}} |
    Format-Table -AutoSize

Write-Host "All ModelPrediction tasks are now configured to run unattended without desktop logon." -ForegroundColor Green
