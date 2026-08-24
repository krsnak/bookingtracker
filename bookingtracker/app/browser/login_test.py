from pathlib import Path
from playwright.sync_api import sync_playwright

from app.browser.dom import OptionalLocatorReader

ROOT = Path.home() / "BookingTracker"
PROFILE = ROOT / "data" / "booking_profile"

HOTEL_URL = (
    "https://www.booking.com/hotel/no/grand-honefoss.html"
    "?checkin=2026-08-26"
    "&checkout=2026-08-27"
    "&group_adults=2"
    "&group_children=0"
    "&no_rooms=1"
)

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        headless=False,
        channel="chrome",
        viewport=None,
    )

    page = context.pages[0] if context.pages else context.new_page()

    page.goto(
        HOTEL_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    page.wait_for_timeout(5000)

    print("\nTitle:", page.title())

    rows = page.locator("tr")

    results = []

    for i in range(rows.count()):
        row = rows.nth(i)
        reader = OptionalLocatorReader(row)
        text = reader.text(":scope")

        if not text:
            continue

        room = None

        for selector in [
            '[data-testid="room-name"]',
            ".hprt-roomtype-link",
            '[data-testid="room-type"]',
        ]:
            room = reader.text(selector)

            if room:
                break

        if not room:
            continue

        price_candidates = []

        for selector in [
            '[data-testid="price-and-discounted-price"]',
            ".bui-price-display__value",
            ".prco-valign-middle-helper",
        ]:
            for value in reader.texts(selector):
                if value not in price_candidates:
                    price_candidates.append(value)

        results.append(
            {
                "room": room,
                "prices": price_candidates,
                "text": text,
            }
        )

    print("\nNalezené pokoje a ceny:\n")

    for index, result in enumerate(results, start=1):
        print(f"{index}. {result['room']}")

        if result["prices"]:
            for price in result["prices"]:
                print(f"   Cena: {price}")
        else:
            print("   Cena: nenalezena")

        print()

    input("\nStiskni ENTER pro ukončení...")

    context.close()
