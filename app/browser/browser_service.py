from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path.home() / "BookingTracker"
PROFILE = ROOT / "data" / "booking_profile"

START_URL = (
    "https://www.booking.com/hotel/no/grand-honefoss.html"
    "?checkin=2026-08-26"
    "&checkout=2026-08-27"
    "&group_adults=2"
    "&group_children=0"
    "&no_rooms=1"
)


def check_current_page(page):
    print("\nČtu aktuální stránku...")

    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(5000)

    print("Title:", page.title())
    print("URL:", page.url)

    rows = page.locator("tr")
    results = []

    for i in range(rows.count()):
        row = rows.nth(i)

        room = None

        for selector in [
            '[data-testid="room-name"]',
            ".hprt-roomtype-link",
            '[data-testid="room-type"]',
        ]:
            item = row.locator(selector)

            if item.count() > 0:
                try:
                    room = item.first.inner_text().strip()
                except Exception:
                    pass

            if room:
                break

        if not room:
            continue

        prices = []

        for selector in [
            '[data-testid="price-and-discounted-price"]',
            ".bui-price-display__value",
            ".prco-valign-middle-helper",
        ]:
            items = row.locator(selector)

            for j in range(items.count()):
                try:
                    value = items.nth(j).inner_text().strip()

                    if value and value not in prices:
                        prices.append(value)

                except Exception:
                    pass

        results.append(
            {
                "room": room,
                "prices": prices,
            }
        )

    print("\nNalezené nabídky:\n")

    if not results:
        print("Žádné rate rows nenalezeny.")
    else:
        for index, result in enumerate(results, start=1):
            print(f"{index}. {result['room']}")

            if result["prices"]:
                for price in result["prices"]:
                    print(f"   Cena: {price}")
            else:
                print("   Cena: nenalezena")

    print("\nKontrola hotová.")


def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            channel="chrome",
            viewport=None,
        )

        page = context.pages[0] if context.pages else context.new_page()

        print("\nOtevírám startovní Booking stránku...")

        page.goto(
            START_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.wait_for_timeout(5000)

        print("Title:", page.title())
        print("URL:", page.url)
        print("\nBrowser service běží.")
        print("Chrome nech otevřený.")

        while True:
            command = input(
                "\nENTER = přečíst aktuální stránku | vlož URL = otevřít ji | q = konec: "
            ).strip()

            if command.lower() == "q":
                break

            try:
                if command.startswith("http"):
                    print("\nOtevírám URL ve stejné Booking session...")

                    page.goto(
                        command,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )

                    page.wait_for_timeout(5000)

                    print("Title:", page.title())
                    print("URL:", page.url)

                else:
                    check_current_page(page)

            except Exception as e:
                print("\nCHYBA:", e)

        context.close()


if __name__ == "__main__":
    main()
