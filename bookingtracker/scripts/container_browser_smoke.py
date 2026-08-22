"""Container-only headless Chromium lifecycle gate; never contacts Booking."""

from __future__ import annotations

from app.browser.service import BookingBrowserService
from app.config import AppPaths, BrowserSettings


def main() -> int:
    service = BookingBrowserService(BrowserSettings.development(AppPaths.from_environment()))
    health = service.start()
    if not health.context_running:
        print(f"browser start failed: {health.last_error}")
        return 1
    result = service.navigate("data:text/html,<title>BookingTracker smoke</title>")
    stopped = service.stop()
    if not result.succeeded or stopped.context_running:
        print("browser lifecycle smoke failed")
        return 1
    print("browser lifecycle smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
