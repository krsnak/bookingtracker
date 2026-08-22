"""Manual-only live smoke test for the Booking rate-offer parser."""

from __future__ import annotations

from app.booking.parser import BookingRateParser
from app.browser.service import BookingBrowserService
from app.config import AppPaths, BrowserSettings

HOTELS = {
    "Grand Hotel Hønefoss": (
        "https://www.booking.com/hotel/no/grand-honefoss.html"
        "?checkin=2026-08-26&checkout=2026-08-27&group_adults=2&group_children=0&no_rooms=1"
    ),
    "Papaya Hostel": (
        "https://www.booking.com/hotel/ma/moroccan-friends-guesthouse.html"
        "?checkin=2026-09-18&checkout=2026-09-19&group_adults=2&group_children=0&no_rooms=1"
    ),
}


def offer_summary(offer: object) -> dict[str, object]:
    data = offer.model_dump()  # type: ignore[attr-defined]
    return {
        key: data[key]
        for key in (
            "room_name",
            "current_price",
            "original_price",
            "currency",
            "genius",
            "breakfast_included",
            "breakfast_genius_benefit",
            "free_cancellation",
            "non_refundable",
            "payment_conditions",
            "parser_warnings",
        )
    }


def main() -> int:
    service = BookingBrowserService(BrowserSettings.development(AppPaths.from_environment()))
    parser = BookingRateParser()
    try:
        health = service.start()
        if not health.context_running:
            print(f"Browser unavailable: {health.last_error}")
            return 1
        for property_name, url in HOTELS.items():
            navigation = service.navigate(url)
            if not navigation.succeeded:
                print(f"{property_name}: navigation {navigation.status}")
                continue
            page = service.current_page()
            if page is None:
                print(f"{property_name}: no page available")
                continue
            result = parser.parse(page, source_url=url)
            print(
                {
                    "property": property_name,
                    "status": result.status,
                    "rooms_detected": result.rooms_detected,
                    "rate_offers": len(result.offers),
                    "warnings": result.warnings,
                    "offers": [offer_summary(offer) for offer in result.offers],
                }
            )
        return 0
    finally:
        service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
