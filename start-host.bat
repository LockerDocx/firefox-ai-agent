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

rem ── install or update ─────────────────────────────────────────────────────────
rem One place decides what to install: scripts\install.py, the same file the other systems
rem use. Double-clicking always checks, so a newer copy of the agent replaces the previous
rem installation instead of being layered on top, and an existing Laya is never installed a
rem second time. Your keys (.env), history (artifacts\) and files (workspace\) are not touched.
echo.
echo  Checking the installation...
%PY% scripts\install.py
if errorlevel 1 (
  echo.
  echo  [!] The agent could not be installed or updated.
  echo      Check your internet connection, then double-click this file again.
  echo      Your keys and your files were not touched.
  echo.
  pause
  exit /b 1
)

rem Two lines in .env can pin the model the agent used to derive: the wrong id old templates
rem shipped (zai/glm-5.3) and the model itself (z-ai/glm-5.3). Both move to the one this version
rem ships, z-ai/glm-5.3-flash. JEV_KEEP_MODEL=1 skips this for anyone who wants the full-size model.
if exist ".env" (
  findstr /R /C:"^[A-Z_]*MODEL=zai/glm-5.3" /C:"^[A-Z_]*MODEL=z-ai/glm-5.3" ".env" >nul 2>nul && (
    copy /y ".env" ".env.bak" >nul 2>nul
    powershell -NoProfile -Command "if ($env:JEV_KEEP_MODEL -ne '1') { (Get-Content .env) -replace '^([A-Z_]*MODEL=)(zai|z-ai)/glm-5\.3\s*$','${1}z-ai/glm-5.3-flash' | Set-Content .env }" >nul 2>nul
    echo  Updated the pinned model in .env to z-ai/glm-5.3-flash ^(backup: .env.bak^).
  )
)

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
)

rem Firefox registration is done by scripts\install.py, which also reports it.
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
