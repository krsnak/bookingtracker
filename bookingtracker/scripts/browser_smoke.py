"""Manual-only macOS smoke check for the persistent Booking browser profile."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from time import monotonic, sleep

from app.browser.models import AuthenticationState
from app.browser.service import BookingBrowserService
from app.config import AppPaths, BrowserSettings

GRAND_HOTEL_URL = (
    "https://www.booking.com/hotel/no/grand-honefoss.html"
    "?checkin=2026-08-26&checkout=2026-08-27&group_adults=2&group_children=0&no_rooms=1"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--second-url", required=True, help="Second Booking hotel URL to open")
    parser.add_argument(
        "--wait-for-login-seconds",
        type=int,
        default=0,
        help="Keep the visible browser open for a manual Booking login when needed",
    )
    parser.add_argument(
        "--wait-for-login-confirmation",
        action="store_true",
        help="Wait for an operator confirmation before continuing after manual login",
    )
    args = parser.parse_args()

    service = BookingBrowserService(BrowserSettings.development(AppPaths.from_environment()))
    try:
        print("Starting persistent Booking browser…")
        print(asdict(service.start()))
        first = service.navigate(GRAND_HOTEL_URL)
        print("Grand Hotel Hønefoss:", asdict(first))
        if first.manual_action_required and args.wait_for_login_confirmation:
            prompt = (
                "Manual Booking login required. "
                "Log in in Chrome, then press ENTER here to continue… "
            )
            input(prompt)
            if service.is_logged_in() is not AuthenticationState.AUTHENTICATED:
                print("Authentication was not confirmed after manual login.")
                return 1
            print("Authenticated state detected; re-running first navigation…")
            first = service.navigate(GRAND_HOTEL_URL)
            print("Grand Hotel Hønefoss after login:", asdict(first))
        elif first.manual_action_required and args.wait_for_login_seconds > 0:
            print("Manual Booking login required; waiting in the visible browser…")
            deadline = monotonic() + args.wait_for_login_seconds
            while monotonic() < deadline:
                if service.is_logged_in() is AuthenticationState.AUTHENTICATED:
                    print("Authenticated state detected; re-running first navigation…")
                    first = service.navigate(GRAND_HOTEL_URL)
                    print("Grand Hotel Hønefoss after login:", asdict(first))
                    break
                sleep(2)
        second = service.navigate(args.second_url)
        print("Second URL:", asdict(second))
        print("Final health:", asdict(service.health()))
        return 0 if first.succeeded and second.succeeded else 1
    finally:
        print("Stopping browser…")
        service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
