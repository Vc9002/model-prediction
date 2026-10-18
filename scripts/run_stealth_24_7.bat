@echo off
REM ==============================================================================
REM Model Prediction - 24/7 Stealth Background Service Launcher
REM Launches dashboard server and auto-buyer worker with BELOW_NORMAL OS priority
REM so gaming, schoolwork, and foreground apps get 100% CPU and zero stutter.
REM ==============================================================================

set "REPO_ROOT=E:\model-prediction"
set "PYTHON=%REPO_ROOT%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Error: Virtual environment python not found at %PYTHON%
    exit /b 1
)

echo Starting Model Prediction Dashboard in Stealth Mode (port 8765)...
start /b /belownormal "" "%PYTHON%" "%REPO_ROOT%\dashboard_server.py"

echo Starting Auto-Buyer Cycle in Stealth Mode...
start /b /belownormal "" "%PYTHON%" "%REPO_ROOT%\src\model_prediction\auto_buyer_worker.py"

echo [OK] 24/7 background services running in stealth mode with idle/background priority.
