@echo off
title PickMatrix
cd /d "%~dp0"

echo.
echo   PickMatrix - Starting...
echo.

:: Find Python
where python >nul 2>&1
if %errorlevel% equ 0 (set PYTHON=python& goto :found)
where python3 >nul 2>&1
if %errorlevel% equ 0 (set PYTHON=python3& goto :found)
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"& goto :found)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"& goto :found)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"& goto :found)
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"& goto :found)
if exist "C:\Python313\python.exe" (set "PYTHON=C:\Python313\python.exe"& goto :found)
if exist "C:\Python312\python.exe" (set "PYTHON=C:\Python312\python.exe"& goto :found)

echo   ERROR: Python not found!
echo   Install from https://www.python.org/downloads/
echo   Check "Add Python to PATH" during install!
echo.
pause
exit /b 1

:found
:: Install dependencies
"%PYTHON%" -c "import requests, yaml, bs4" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Installing packages (first time)...
    "%PYTHON%" -m pip install requests pyyaml beautifulsoup4 urllib3 --quiet 2>&1
    echo   Done.
)

:: Auto-update check
echo   Checking for updates...
"%PYTHON%" -c "import requests;r=requests.get('https://raw.githubusercontent.com/ttuyen099/pick-staffing-evaluator/main/version.txt',timeout=5);remote=r.text.strip();local=open('version.txt').read().strip();print('CURRENT:'+local+' REMOTE:'+remote);exit(0 if remote==local else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Update available! Downloading...
    "%PYTHON%" updater.py
    echo   Updated!
    echo.
)

echo.
echo   ====================================================
echo     PickMatrix v1.0 - Pick Staffing Evaluator
echo     Dashboard: http://localhost:8787
echo   ====================================================
echo.
echo   Firefox must be open + logged into FCLM
echo   Press Ctrl+C to stop.
echo.

"%PYTHON%" staffing_dashboard_server.py

echo.
echo   Server stopped.
echo.
pause
