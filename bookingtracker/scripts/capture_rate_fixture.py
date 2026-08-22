"""Manual developer tool: capture and sanitize a Booking availability subtree."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.booking.capture import sanitize_rate_fixture_html
from app.booking.selectors import BookingSelectors
from app.browser.service import BookingBrowserService
from app.config import AppPaths, BrowserSettings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    service = BookingBrowserService(BrowserSettings.development(AppPaths.from_environment()))
    try:
        navigation = service.start()
        if not navigation.context_running:
            print(f"Browser unavailable: {navigation.last_error}")
            return 1
        result = service.navigate(args.url)
        if not result.succeeded:
            print(f"Navigation unavailable: {result.status}")
            return 1
        page = service.current_page()
        if page is None:
            print("No page available")
            return 1
        subtree = page.locator(BookingSelectors.AVAILABILITY).first.inner_html()  # type: ignore[attr-defined]
        args.output.write_text(sanitize_rate_fixture_html(subtree))
        print(f"Wrote sanitized candidate fixture to {args.output}; review before committing.")
        return 0
    finally:
        service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
