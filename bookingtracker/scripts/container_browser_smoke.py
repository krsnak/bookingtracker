"""Container-only headless Chromium lifecycle gate; never contacts Booking."""

from __future__ import annotations

from app.browser.service import BookingBrowserService
from app.browser.smoke import run_browser_smoke
from app.config import AppPaths, BrowserSettings


def main() -> int:
    service = BookingBrowserService(BrowserSettings.development(AppPaths.from_environment()))
    return run_browser_smoke(service)


if __name__ == "__main__":
    raise SystemExit(main())
