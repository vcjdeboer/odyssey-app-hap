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

# ---- top-level: requirements + lab-tuned launcher ----
cp "${REPO_ROOT}/requirements.txt" "${DEST}/requirements.txt"

cat > "${DEST}/run.bat" <<'BAT'
@echo off
cd /d "%~dp0"

echo === Odyssey Western Blot Imager (lab deploy) ===
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

set ODYSSEY_HOST=
set /p MODE="Connect to real Odyssey? (y/N): "
if /i "%MODE%"=="y" (
    set /p ODYSSEY_HOST="Odyssey IP [169.254.206.190]: "
    if "%ODYSSEY_HOST%"=="" set ODYSSEY_HOST=169.254.206.190
    set /p ODYSSEY_USER="Odyssey username [odyssey]: "
    if "%ODYSSEY_USER%"=="" set ODYSSEY_USER=odyssey
    set /p ODYSSEY_PASS="Odyssey password [odyssey]: "
    if "%ODYSSEY_PASS%"=="" set ODYSSEY_PASS=odyssey
    echo Connecting to %ODYSSEY_HOST%...
) else (
    echo Running in simulated mode.
)

echo.
echo Starting server at http://localhost:8000
echo Press Ctrl+C to stop.
echo.
uvicorn odyssey_app.app:app --host 0.0.0.0 --port 8000
pause
BAT

cat > "${DEST}/LAB_README.md" <<'MD'
# Odyssey Western Blot Imager — lab deploy

Self-contained snapshot. Built by `scripts/deploy_to_desktop.sh` on the
Mac. Copy this whole folder to the lab Windows box's C: drive (e.g.
`C:\odyssey\`) and double-click `run.bat`.

## Layout
- `plr_v4/`        — device package + the three capabilities the app uses
- `odyssey_app/`   — FastAPI server, static UI, metadata schema, lab PID instance card
- `pylabrobot/`    — vendored snapshot of upstream PLR (provides `BackendParams`)
- `requirements.txt`
- `run.bat`        — sets PYTHONPATH so the bundled `pylabrobot/` is importable

## Re-deploying after edits on the Mac
```
~/vs\ code/titronic/scripts/deploy_to_desktop.sh
```
This wipes and rebuilds `~/Desktop/odyssey/`. Copy across again.

## Where things come from / customisations
- `DEPLOY_DEST=…`  override destination (default `~/Desktop/odyssey/`)
- `PLR_SRC=…`      override the pylabrobot source (default `~/pylabrobot/`)
- The lab PID lives in `odyssey_app/instrument.py`. Override at the
  Windows shell with `set ODYSSEY_PID=…` before `run.bat` if a different
  unit is attached.
MD

# ---- summary ----
echo "==> done."
( cd "${DEST}" && ls -la )
echo
echo -n "size: "
du -sh "${DEST}" | cut -f1
