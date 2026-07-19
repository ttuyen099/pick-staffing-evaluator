@echo off
title PickMatrix
cd /d "%~dp0"

echo.
echo   PickMatrix - Starting...
echo.

:: Find Python
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON=python"
    goto :found
)
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
    set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    goto :found
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto :found
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto :found
)
if exist "C:\Program Files\Python313\python.exe" (
    set "PYTHON=C:\Program Files\Python313\python.exe"
    goto :found
)
if exist "C:\Program Files\Python312\python.exe" (
    set "PYTHON=C:\Program Files\Python312\python.exe"
    goto :found
)
if exist "C:\Python313\python.exe" (
    set "PYTHON=C:\Python313\python.exe"
    goto :found
)

echo   ERROR: Python not found.
echo   Install from https://www.python.org/downloads/
echo   CHECK "Add Python to PATH" during install!
echo.
pause
exit /b 1

:found
:: Install dependencies
"%PYTHON%" -c "import requests, yaml, bs4" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Installing packages...
    "%PYTHON%" -m pip install requests pyyaml beautifulsoup4 urllib3 --quiet
)

:: Check for updates (using API, no cache)
echo   Checking for updates...
"%PYTHON%" -c "import requests,base64,time;r=requests.get('https://api.github.com/repos/ttuyen099/pick-staffing-evaluator/contents/version.txt',timeout=5);remote=base64.b64decode(r.json()['content']).decode().strip();local=open('version.txt').read().strip();exit(0 if remote==local else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Update found! Downloading...
    "%PYTHON%" updater.py
    echo   Updated!
)

echo.
echo   PickMatrix - http://localhost:8787
echo   Press Ctrl+C to stop.
echo.

"%PYTHON%" staffing_dashboard_server.py

echo.
echo   Server stopped.
pause
