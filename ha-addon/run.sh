#!/usr/bin/with-contenv bashio
set -euo pipefail
export BOOKINGTRACKER_DATA_DIR=/data
export BOOKINGTRACKER_LOGS_DIR=/data/logs
export BOOKINGTRACKER_BROWSER_CHANNEL=
export BOOKINGTRACKER_BROWSER_HEADLESS=true
export BOOKINGTRACKER_BROWSER_EXECUTABLE=/usr/bin/chromium-browser
exec python3 -m uvicorn app.web.main:app --host 0.0.0.0 --port 8000
