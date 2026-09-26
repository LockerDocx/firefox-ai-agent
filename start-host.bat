@echo off
setlocal
cd /d "%~dp0"
title AI Agent for Firefox - local host

rem ============================================================
rem  Double-click starter. Prepares everything and runs the host.
rem ============================================================

rem Two ways Windows carries Python: the "py" launcher (python.org installer) and
rem python.exe. The Store's python.exe is a stub that opens the Store, and it fails
rem this check just like an outdated interpreter would - hence the hint below.
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo  [!] Python 3.11 or newer is required, and this PC does not have one.
  echo      Install it from https://www.python.org/downloads/
  echo      IMPORTANT: tick "Add python.exe to PATH" in the installer.
  echo      If a Microsoft Store window opened instead, that is the Store stub:
  echo      install the real Python from python.org ^(with that box ticked^).
  echo      Then double-click this file again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo.
  echo  First run: preparing the agent. About one minute, internet needed...
  %PY% -m venv .venv
  ".venv\Scripts\python" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python" -m pip install --quiet -e ".[documents]"
  if errorlevel 1 (
    echo.
    echo  [!] Installation failed. Check your internet connection and try again.
    echo.
    pause
    exit /b 1
  )
)

rem Earlier templates shipped a wrong NVIDIA model id (zai/ instead of z-ai/): fix it in place.
if exist ".env" (
  findstr /C:"zai/glm-5.3" ".env" >nul 2>nul && (
    powershell -NoProfile -Command "(Get-Content .env) -replace 'zai/glm-5.3','z-ai/glm-5.3' | Set-Content .env" >nul 2>nul
    echo  Fixed an outdated model id in .env ^(zai/ -^> z-ai/^).
  )
)

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
)

echo  Registering the host with Firefox...
".venv\Scripts\jev-register-host.exe" >nul 2>nul
if errorlevel 1 (
  echo   [!] Could not register it - keep this window open while you use the agent.
) else (
  echo   Done: from now on the sidebar starts the agent by itself, no window needed.
  echo   This one stays open only as a fallback: if the panel says offline, it is to blame.
)
echo.
echo  Host starting. KEEP THIS WINDOW OPEN while you use the sidebar.
echo.
echo  Paste your free API key in the sidebar - it will ask
echo  ^(2 minutes at https://build.nvidia.com^).
echo.
echo  Now in Firefox:
echo    1. Type  about:debugging  in the address bar and press Enter
echo    2. Click "This Firefox"  then  "Load Temporary Add-on..."
echo    3. Open this folder, then the "extension" folder, pick "manifest.json"
echo    4. Open the AI Agent sidebar with the toolbar button
echo.
".venv\Scripts\jev-firefox"
echo.
echo  The host stopped.
echo.
pause
exit /b 0
