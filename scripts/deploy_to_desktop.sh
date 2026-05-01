#!/usr/bin/env bash
# Lay out a self-contained, lab-ready copy of the Odyssey app at
# ~/Desktop/odyssey/. From there it goes via the VMware shared folder
# onto the lab Windows box's C: drive, where run.bat drives it.
#
# The deploy is the runtime subset only:
#   - plr_v4 device package + the three capabilities the app uses
#   - odyssey_app (no records/scans/exports — those are runtime state)
#   - pylabrobot snapshot (BackendParams + serializer dependency)
#   - requirements.txt + a Windows-tuned run.bat
#
# Sibling experiments (plr_v4/arduino, titrino, seven_direct, flex; the
# unused capability stubs) are dropped so the lab box only sees what
# this particular app actually imports.
#
# Re-run after every change. The destination is wiped and rebuilt.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${DEPLOY_DEST:-${HOME}/Desktop/odyssey}"
PLR_SRC="${PLR_SRC:-${HOME}/pylabrobot}"

echo "==> source repo  : ${REPO_ROOT}"
echo "==> destination  : ${DEST}"
echo "==> pylabrobot   : ${PLR_SRC}"
echo

if [[ ! -d "${PLR_SRC}/pylabrobot" ]]; then
  echo "ERROR: ${PLR_SRC}/pylabrobot not found." >&2
  echo "Set PLR_SRC=/path/to/pylabrobot if the editable install lives elsewhere." >&2
  exit 1
fi

rm -rf "${DEST}"
mkdir -p "${DEST}"

RSYNC_BASE=(
  -a
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.pytest_cache'
  --exclude '.mypy_cache'
  --exclude '.DS_Store'
)

# ---- plr_v4: only the bits this app imports ----
mkdir -p "${DEST}/plr_v4/capabilities"

cp "${REPO_ROOT}/plr_v4/__init__.py"      "${DEST}/plr_v4/"
cp "${REPO_ROOT}/plr_v4/device.py"        "${DEST}/plr_v4/"
cp "${REPO_ROOT}/plr_v4/device_card.py"   "${DEST}/plr_v4/"
cp "${REPO_ROOT}/plr_v4/signals.py"       "${DEST}/plr_v4/"

rsync "${RSYNC_BASE[@]}" \
  "${REPO_ROOT}/plr_v4/odyssey/" "${DEST}/plr_v4/odyssey/"

cp "${REPO_ROOT}/plr_v4/capabilities/__init__.py" "${DEST}/plr_v4/capabilities/"
cp "${REPO_ROOT}/plr_v4/capabilities/base.py"     "${DEST}/plr_v4/capabilities/"
for cap in scanning image_retrieval instrument_status; do
  rsync "${RSYNC_BASE[@]}" \
    "${REPO_ROOT}/plr_v4/capabilities/${cap}/" \
    "${DEST}/plr_v4/capabilities/${cap}/"
done

# ---- odyssey_app: runtime + UI, no state, no b2inst registration tools ----
rsync "${RSYNC_BASE[@]}" \
  --exclude 'records' \
  --exclude 'scans' \
  --exclude 'exports' \
  --exclude 'b2inst_*.py' \
  "${REPO_ROOT}/odyssey_app/" "${DEST}/odyssey_app/"

# ---- pylabrobot: snapshot the editable install so the lab box doesn't
#      need its own clone (BackendParams + serializer are imported at
#      module load and the app fails fast without them).
rsync "${RSYNC_BASE[@]}" \
  --exclude 'tests' \
  --exclude 'docs' \
  "${PLR_SRC}/pylabrobot/" "${DEST}/pylabrobot/"

# ---- top-level: requirements + lab-tuned launcher + icon ----
cp "${REPO_ROOT}/requirements.txt" "${DEST}/requirements.txt"
cp "${REPO_ROOT}/scripts/odyssey.ico" "${DEST}/odyssey.ico"

cat > "${DEST}/make_desktop_shortcut.bat" <<'BAT'
@echo off
setlocal
:: Build TWO Desktop shortcuts:
::   "Odyssey Server"  -> run.bat        (starts uvicorn)
::   "Odyssey App"     -> open_app.bat   (opens Chrome at localhost:8000)
:: Workflow on the lab box: click Server first, wait for the
:: "Application startup complete" line, then click App. This avoids
:: the timing race where Chrome opened before the server was ready.
::
:: Run this once after the first copy of the deploy folder; safe to
:: re-run any time (it just rebuilds both shortcuts).

cd /d "%~dp0"
set "HERE=%CD%"
set "ICON=%HERE%\odyssey.ico"
set "DESKTOP=%USERPROFILE%\Desktop"

if not exist "%HERE%\run.bat" (
    echo ERROR: run.bat not found in %HERE%. Run this from the deploy folder.
    pause
    exit /b 1
)
if not exist "%HERE%\open_app.bat" (
    echo ERROR: open_app.bat not found in %HERE%.
    pause
    exit /b 1
)

set "PS_TMP=%TEMP%\odyssey_make_shortcuts.ps1"
> "%PS_TMP%" echo $ws = New-Object -ComObject WScript.Shell
>> "%PS_TMP%" echo $a = $ws.CreateShortcut('%DESKTOP%\Odyssey Server.lnk')
>> "%PS_TMP%" echo $a.TargetPath = '%HERE%\run.bat'
>> "%PS_TMP%" echo $a.WorkingDirectory = '%HERE%'
>> "%PS_TMP%" echo $a.IconLocation = '%ICON%'
>> "%PS_TMP%" echo $a.Description = 'Odyssey — start the local server'
>> "%PS_TMP%" echo $a.Save()
>> "%PS_TMP%" echo $b = $ws.CreateShortcut('%DESKTOP%\Odyssey App.lnk')
>> "%PS_TMP%" echo $b.TargetPath = '%HERE%\open_app.bat'
>> "%PS_TMP%" echo $b.WorkingDirectory = '%HERE%'
>> "%PS_TMP%" echo $b.IconLocation = '%ICON%'
>> "%PS_TMP%" echo $b.Description = 'Odyssey — open the app in Chrome'
>> "%PS_TMP%" echo $b.Save()

echo.
echo Creating Desktop shortcuts...
echo   "Odyssey Server" -^> %HERE%\run.bat
echo   "Odyssey App"    -^> %HERE%\open_app.bat
echo   Icon:               %ICON%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_TMP%"
set "PS_RC=%ERRORLEVEL%"
del "%PS_TMP%" >nul 2>&1

if not "%PS_RC%"=="0" (
    echo PowerShell exited with code %PS_RC%. See the manual fallback below.
    goto manual
)
if not exist "%DESKTOP%\Odyssey Server.lnk" goto manual
if not exist "%DESKTOP%\Odyssey App.lnk"    goto manual

echo Done. Two icons on your Desktop:
echo   Odyssey Server  — click first
echo   Odyssey App     — click after "Application startup complete"
pause
exit /b 0

:manual
echo.
echo --- MANUAL FALLBACK ---
echo If the script can't create the shortcuts, do it by hand:
echo.
echo Server shortcut:
echo   1. Right-click run.bat ^> Send to ^> Desktop (create shortcut)
echo   2. Right-click the new shortcut ^> Properties ^> Change Icon ^>
echo      Browse to %ICON%
echo   3. Rename to "Odyssey Server"
echo.
echo App shortcut:
echo   1. Right-click open_app.bat ^> Send to ^> Desktop (create shortcut)
echo   2. Same Change Icon step.
echo   3. Rename to "Odyssey App"
echo.
pause
exit /b 1
BAT

cat > "${DEST}/run.bat" <<'BAT'
@echo off
cd /d "%~dp0"

echo === Odyssey Western Blot Imager — server ===
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11+ and add to PATH.
    pause
    exit /b 1
)

pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
    echo.
)

:: %CD% is the deploy folder. Putting it on PYTHONPATH makes both
:: plr_v4/ and the bundled pylabrobot/ snapshot importable as top-level
:: packages without a pip install of either.
set "PYTHONPATH=%CD%;%PYTHONPATH%"

:: We deliberately don't auto-open Chrome here. The earlier auto-open
:: raced the FastAPI startup hook — Chrome hit localhost:8000 before
:: uvicorn finished authenticating against the instrument and the user
:: saw ERR_CONNECTION_REFUSED. Now there are two Desktop shortcuts:
::   "Odyssey Server" → this file (starts uvicorn)
::   "Odyssey App"    → open_app.bat (opens Chrome at localhost:8000)
:: Click Server first; once you see "Application startup complete",
:: click App.
set ODYSSEY_OPEN_BROWSER=0

if exist credentials.bat (
    call credentials.bat
    echo Loaded credentials.bat — connecting to %ODYSSEY_HOST% as %ODYSSEY_USER%
) else (
    set /p ODYSSEY_HOST="Odyssey IP [169.254.206.190]: "
    if "%ODYSSEY_HOST%"=="" set ODYSSEY_HOST=169.254.206.190
    set /p ODYSSEY_USER="Odyssey username [odyssey]: "
    if "%ODYSSEY_USER%"=="" set ODYSSEY_USER=odyssey
    set /p ODYSSEY_PASS="Odyssey password [odyssey]: "
    if "%ODYSSEY_PASS%"=="" set ODYSSEY_PASS=odyssey
    echo Connecting to %ODYSSEY_HOST%...
)

echo.
echo Server starting at http://localhost:8000
echo Wait for "Application startup complete", then click "Odyssey App"
echo on the Desktop (or type localhost:8000 in Chrome).
echo Press Ctrl+C here to stop the server.
echo.
uvicorn odyssey_app.app:app --host 0.0.0.0 --port 8000
pause
BAT

cat > "${DEST}/open_app.bat" <<'BAT'
@echo off
:: Open Chrome at the running Odyssey app. Click this AFTER you see
:: "Application startup complete" in the "Odyssey Server" cmd window
:: — clicking too early gives ERR_CONNECTION_REFUSED.
set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" (
    echo Chrome not found at %CHROME_EXE%.
    echo Falling back to the Windows default browser.
    start "" http://localhost:8000
    exit /b 0
)
start "" "%CHROME_EXE%" --new-window http://localhost:8000
BAT

cat > "${DEST}/credentials.bat.example" <<'BAT'
@echo off
:: ---------------------------------------------------------------
:: Copy this file to ``credentials.bat`` once on the lab box and
:: fill in the real instrument credentials. Both files live next
:: to run.bat. ``credentials.bat`` is read by run.bat and skipped
:: by the deploy script — copying a fresh deploy across will not
:: overwrite it (Windows file-copy will ask "overwrite/skip?";
:: choose Skip for credentials.bat).
::
:: With a credentials.bat in place, run.bat goes straight to the
:: real instrument and pops the browser — no prompts.
::
:: Leaving this file untouched (no credentials.bat) keeps the
:: original interactive prompt flow.
:: ---------------------------------------------------------------
set ODYSSEY_HOST=169.254.206.190
set ODYSSEY_USER=odyssey
set ODYSSEY_PASS=odyssey
BAT

cat > "${DEST}/LAB_README.md" <<'MD'
# Odyssey Western Blot Imager — lab deploy

Self-contained snapshot. Built by `scripts/deploy_to_desktop.sh` on the
Mac. Copy this whole folder to the lab Windows box's C: drive (e.g.
`C:\odyssey\`).

## Layout
- `plr_v4/`               — device package + the three capabilities the app uses
- `odyssey_app/`          — FastAPI server, static UI, metadata schema, lab PID instance card
- `pylabrobot/`           — vendored snapshot of upstream PLR (provides `BackendParams`)
- `requirements.txt`
- `run.bat`               — starts uvicorn (no auto-Chrome — see workflow below)
- `open_app.bat`          — opens Chrome at http://localhost:8000
- `make_desktop_shortcut.bat` — one-time helper to make two Desktop shortcuts
- `odyssey.ico`           — icon used by both shortcuts
- `credentials.bat.example` — copy to `credentials.bat` for headless launch (see below)

## Two-shortcut workflow on the lab Windows box
1. **Double-click "Odyssey Server"** (or `run.bat`). A cmd window opens, uvicorn starts.
2. **Wait for the line "Application startup complete"** in that window — usually 5–10 s while the FastAPI startup hook authenticates against the Odyssey.
3. **Double-click "Odyssey App"** (or `open_app.bat`). Chrome opens at `http://localhost:8000`.
4. Use the app. To shut down: bring the server cmd window to the front and press Ctrl+C.

This replaces the old auto-launching run.bat. The earlier auto-open
race (Chrome hits the port before the server is ready, browser shows
ERR_CONNECTION_REFUSED, manual refresh required) is gone — you control
the timing.

## Make the Desktop shortcuts
On the lab box, double-click `make_desktop_shortcut.bat` once. It
builds two icons on your Desktop:
- **Odyssey Server** → `run.bat`
- **Odyssey App**    → `open_app.bat`

Both use `odyssey.ico`. Safe to re-run any time.

If the helper fails ("MANUAL FALLBACK" in the cmd window), do each
one by hand:
1. Right-click the `.bat` → Send to → Desktop (create shortcut).
2. Right-click the new Desktop shortcut → Properties → Change Icon → Browse to `C:\odyssey\odyssey.ico` → OK.
3. Rename if you like.

If a shortcut **exists but doesn't launch** (cmd window flashes and
closes), the issue is almost always inside the `.bat` itself, not the
shortcut. Test by double-clicking the `.bat` directly: if that also
flashes-and-closes, Python isn't on PATH or `pip install -r
requirements.txt` is failing. The cmd output usually names the cause;
run from an already-open cmd window so the error message stays
visible.

## First-time setup on the lab box (one-time)
1. Copy `credentials.bat.example` to `credentials.bat`.
2. Edit `credentials.bat` and fill in the real instrument host / user / password.
3. Save. Done — `run.bat` from now on goes straight to the real instrument with no prompts.

`credentials.bat` is **per-lab-box**: it stays on the C: drive, never travels back
to the Mac, and is never regenerated by the deploy script. When you copy a fresh
deploy folder across, Windows file-copy will ask whether to overwrite each
existing file — answer **Skip** for `credentials.bat` and **Yes** for everything
else. Without `credentials.bat`, `run.bat` falls back to the original prompt
flow (Connect to real Odyssey? → IP → user → pass).

## Re-deploying after edits on the Mac
```
~/vs\ code/titronic/scripts/deploy_to_desktop.sh
```
This wipes and rebuilds `~/Desktop/odyssey/`. Copy across again.

## Where things come from / customisations
- `DEPLOY_DEST=…`  override destination (default `~/Desktop/odyssey/`)
- `PLR_SRC=…`      override the pylabrobot source (default `~/pylabrobot/`)
- The lab PID lives in `odyssey_app/instrument.py`. Override at the
  Windows shell with `set ODYSSEY_PID=…` (or in `credentials.bat`) if a
  different unit is attached.
- `ODYSSEY_OPEN_BROWSER=0` in `credentials.bat` disables the
  auto-browser-open if you're running headless.
MD

# ---- summary ----
echo "==> done."
( cd "${DEST}" && ls -la )
echo
echo -n "size: "
du -sh "${DEST}" | cut -f1
