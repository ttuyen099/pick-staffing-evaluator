@echo off
title PickMatrix
cd /d "%~dp0"

echo.
echo   PickMatrix - Starting...
echo.

:: Find Python - check PATH first
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON=python"
    goto :found
)

:: Check common locations (with quotes for spaces)
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
if exist "C:\Python312\python.exe" (
    set "PYTHON=C:\Python312\python.exe"
    goto :found
)

echo   ERROR: Python not found.
echo   Install from https://www.python.org/downloads/
echo   CHECK "Add Python to PATH" during install!
echo.
pause
exit /b 1

:found
echo   Python: "%PYTHON%"

:: Install dependencies
"%PYTHON%" -c "import requests, yaml, bs4" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Installing packages...
    "%PYTHON%" -m pip install requests pyyaml beautifulsoup4 urllib3
)

:: Check for updates
"%PYTHON%" -c "import requests;r=requests.get('https://raw.githubusercontent.com/ttuyen099/pick-staffing-evaluator/main/version.txt',timeout=5);remote=r.text.strip();local=open('version.txt').read().strip();exit(0 if remote==local else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Updating...
    "%PYTHON%" updater.py
)

echo.
echo   PickMatrix - http://localhost:8787
echo   Press Ctrl+C to stop.
echo.

"%PYTHON%" staffing_dashboard_server.py

echo.
echo   Server stopped.
pause
