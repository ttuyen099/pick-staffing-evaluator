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
:: Optional site selection: "Start Dashboard.bat CLT3" overrides site.txt.
:: If no arg and site.txt exists, the server reads site.txt automatically.
set "SITEARG="
if not "%~1"=="" (
    set "SITEARG=--site=%~1"
    echo   Site: %~1
    goto :site_done
)
if exist "site.txt" (
    set /p SITETXT=<site.txt
    goto :site_from_file
)
echo   Site: default (HOU8)
goto :site_done

:site_from_file
echo   Site (from site.txt): %SITETXT%

:site_done

:: Install dependencies
"%PYTHON%" -c "import requests, yaml, bs4" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Installing packages...
    "%PYTHON%" -m pip install requests pyyaml beautifulsoup4 urllib3 --quiet
)

:: Check for updates. Only update when the GitHub version is STRICTLY NEWER
:: than the local version, so a newer local build is never overwritten.
echo   Checking for updates...
"%PYTHON%" -c "import requests,base64;r=requests.get('https://api.github.com/repos/ttuyen099/pick-staffing-evaluator/contents/version.txt',timeout=5);remote=base64.b64decode(r.json()['content']).decode().strip();local=open('version.txt').read().strip();rt=tuple(int(x) for x in remote.split('.'));lt=tuple(int(x) for x in local.split('.'));exit(1 if rt>lt else 0)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Update found! Downloading...
    "%PYTHON%" updater.py
    echo   Updated!
)

echo.
echo   PickMatrix - http://localhost:8787
echo   Press Ctrl+C to stop.
echo.

"%PYTHON%" staffing_dashboard_server.py %SITEARG%

echo.
echo   Server stopped.
pause
