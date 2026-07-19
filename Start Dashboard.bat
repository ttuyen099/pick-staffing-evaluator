@echo off
title PickMatrix
cd /d "%~dp0"
echo.
echo   PickMatrix - Starting...
echo.
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   ERROR: Python not found.
    echo   Install from https://www.python.org/downloads/
    echo   CHECK "Add Python to PATH" during install!
    echo.
    pause >nul
    exit /b 1
)
python -c "import requests, yaml, bs4" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Installing packages...
    python -m pip install requests pyyaml beautifulsoup4 urllib3
)
echo   Checking for updates...
python -c "import requests;r=requests.get('https://raw.githubusercontent.com/ttuyen099/pick-staffing-evaluator/main/version.txt',timeout=5);remote=r.text.strip();local=open('version.txt').read().strip();exit(0 if remote==local else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Updating to latest version...
    python updater.py
    echo   Updated!
)
echo.
echo   PickMatrix - Dashboard: http://localhost:8787
echo   Firefox must be logged into FCLM.
echo   Press Ctrl+C to stop.
echo.
python staffing_dashboard_server.py
echo.
echo   Server stopped.
pause >nul
