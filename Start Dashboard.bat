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
::
:: Exit codes from the check:  1 = update available   0 = up to date / check failed
:: Any failure (offline, proxy, bad response) deliberately maps to 0 = "skip the
:: update and just start" - an update must never be FORCED by a failed check.
::
:: Uses raw.githubusercontent.com (CDN, no per-IP limit) rather than
:: api.github.com, which is capped at 60 req/hour per public IP - a limit the
:: whole building shares behind the FC's NAT and exhausts within minutes.
echo   Checking for updates...
:: NOTE: this line must not contain a literal ^> or ^< - cmd treats them as
:: redirection even inside the Python string. The comparison uses tuple.__gt__.
"%PYTHON%" -c "import sys,time,requests;exec(\"try:\n r=requests.get('https://raw.githubusercontent.com/ttuyen099/pick-staffing-evaluator/main/version.txt?t='+str(int(time.time())),timeout=5)\n if r.status_code!=200: sys.exit(0)\n rt=tuple(int(x) for x in r.text.strip().split('.'))\n lt=tuple(int(x) for x in open('version.txt').read().strip().split('.'))\n sys.exit(int(rt.__gt__(lt)))\nexcept SystemExit: raise\nexcept Exception: sys.exit(0)\")" >nul 2>&1
set "NEEDUPDATE=%errorlevel%"
:: Self-heal: if the per-site configs folder is missing, force an update so
:: users get their site in the dropdown even when the version already matches.
if not exist "sites\HOU8.yaml" set "NEEDUPDATE=1"
:: The updater exits non-zero and leaves the install untouched if ANY file
:: failed to download, so only report success (and re-run for the new file
:: list) when the first pass actually completed. The second pass lets a
:: freshly-downloaded updater.py apply its OWN newer file list in the same
:: session - how new files reach users whose old updater didn't know about them.
if "%NEEDUPDATE%" neq "0" (
    echo   Update found! Downloading...
    "%PYTHON%" updater.py
    if errorlevel 1 (
        echo   Update could not be completed - starting with the current version.
    ) else (
        "%PYTHON%" updater.py
        echo   Updated!
    )
)

echo.
echo   PickMatrix - http://localhost:8787
echo   Press Ctrl+C to stop.
echo.

"%PYTHON%" staffing_dashboard_server.py %SITEARG%

echo.
echo   Server stopped.
pause
