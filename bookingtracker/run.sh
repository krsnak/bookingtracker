#!/bin/sh
set -eu
export BOOKINGTRACKER_DATA_DIR=/data
export BOOKINGTRACKER_LOGS_DIR=/data/logs
export BOOKINGTRACKER_BROWSER_CHANNEL=
export BOOKINGTRACKER_BROWSER_HEADLESS=false
export BOOKINGTRACKER_BROWSER_EXECUTABLE=/usr/bin/chromium
export BOOKINGTRACKER_BROWSER_ARGS=--no-sandbox,--disable-dev-shm-usage
export BOOKINGTRACKER_REMOTE_DESKTOP_ENABLED=true
export BOOKINGTRACKER_BROWSER_DISPLAY=:99
export BOOKINGTRACKER_XAUTHORITY=/run/bookingtracker/Xauthority
export BOOKINGTRACKER_NOVNC_ASSETS_DIR=/usr/share/novnc
exec /opt/venv/bin/python -m uvicorn app.web.main:app --host 0.0.0.0 --port 8000
