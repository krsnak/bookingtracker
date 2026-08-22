from __future__ import annotations

import threading
import time
from pathlib import Path

from app.browser.models import AuthenticationState, BrowserState, NavigationStatus
from app.browser.service import BookingBrowserService
from app.config import BrowserSettings


class FakeLocator:
    def __init__(self, count: int = 0, text: str = "") -> None:
        self._count = count
        self._text = text

    def count(self) -> int:
        return self._count

    def inner_text(self, timeout: int) -> str:
        del timeout
        return self._text


class FakePage:
    def __init__(self) -> None:
        self.url = "https://www.booking.com/"
        self.closed = False
        self.body_text = ""
        self.selector_counts: dict[str, int] = {}
        self.goto_error: Exception | None = None
        self.goto_calls: list[str] = []
        self.active_navigations = 0
        self.max_active_navigations = 0

    def locator(self, selector: str) -> FakeLocator:
        if selector == "body":
            return FakeLocator(text=self.body_text)
        return FakeLocator(count=self.selector_counts.get(selector, 0))

    def goto(self, url: str, **_: object) -> None:
        self.goto_calls.append(url)
        self.active_navigations += 1
        self.max_active_navigations = max(self.max_active_navigations, self.active_navigations)
        try:
            time.sleep(0.01)
            if self.goto_error:
                raise self.goto_error
            self.url = url
        finally:
            self.active_navigations -= 1

    def title(self) -> str:
        return (
            "BookingTracker browser smoke" if self.url.startswith("data:") else "Booking test page"
        )

    def evaluate(self, _: str) -> str:
        return "Fake Chromium"

    def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = [FakePage()]
        self.closed = False

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.calls: list[dict[str, object]] = []

    def launch_persistent_context(self, **kwargs: object) -> FakeContext:
        self.calls.append(kwargs)
        return self.context


class FakePlaywright:
    def __init__(self, context: FakeContext) -> None:
        self.chromium = FakeChromium(context)
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def build_service(tmp_path: Path) -> tuple[BookingBrowserService, FakeContext, FakePlaywright]:
    context = FakeContext()
    playwright = FakePlaywright(context)
    settings = BrowserSettings(profile_dir=tmp_path / "booking_profile", channel="chrome")
    return (
        BookingBrowserService(settings, playwright_factory=lambda: playwright),
        context,
        playwright,
    )


def test_lifecycle_and_page_recovery(tmp_path: Path) -> None:
    service, context, playwright = build_service(tmp_path)

    assert service.status() is BrowserState.STOPPED
    health = service.start()
    original_page = service.current_page()
    assert health.state is BrowserState.READY
    assert health.context_running
    assert original_page is context.pages[0]
    assert len(playwright.chromium.calls) == 1

    service.start()
    assert len(playwright.chromium.calls) == 1

    original_page.closed = True
    recovered_page = service.ensure_page()
    assert recovered_page is not original_page
    assert service.health().page_available

    service.stop()
    assert context.closed
    assert playwright.stopped
    assert service.status() is BrowserState.STOPPED


def test_logged_out_and_captcha_states_are_explicit(tmp_path: Path) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None

    page.selector_counts['[data-testid="header-sign-in-button"]'] = 1
    logged_out = service.navigate("https://www.booking.com/hotel/example")
    assert logged_out.status is NavigationStatus.LOGIN_REQUIRED
    assert logged_out.authenticated_state is AuthenticationState.LOGGED_OUT
    assert logged_out.manual_action_required
    assert service.status() is BrowserState.LOGIN_REQUIRED

    page.selector_counts.clear()
    page.body_text = "Please complete the CAPTCHA security challenge"
    captcha = service.navigate("https://www.booking.com/hotel/example")
    assert captcha.status is NavigationStatus.CAPTCHA_REQUIRED
    assert captcha.manual_action_required
    assert service.status() is BrowserState.CAPTCHA_REQUIRED


def test_navigation_maps_timeout_and_browser_crash(tmp_path: Path) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None

    page.goto_error = TimeoutError("navigation timed out")
    timeout = service.navigate("https://www.booking.com/hotel/timeout")
    assert timeout.status is NavigationStatus.TIMEOUT

    page.goto_error = RuntimeError("Target page, context or browser has been closed")
    crash = service.navigate("https://www.booking.com/hotel/crash")
    assert crash.status is NavigationStatus.PAGE_CLOSED


def test_browser_start_failure_is_reported(tmp_path: Path) -> None:
    settings = BrowserSettings(profile_dir=tmp_path / "booking_profile")
    service = BookingBrowserService(
        settings, playwright_factory=lambda: (_ for _ in ()).throw(RuntimeError("gone"))
    )

    health = service.start()
    assert health.state is BrowserState.ERROR
    assert not health.context_running
    assert "browser start failed" in (health.last_error or "")


def test_smoke_test_closes_only_disposable_page(tmp_path: Path) -> None:
    service, context, _ = build_service(tmp_path)
    service.start()
    main_page = context.pages[0]
    result = service.smoke_test()
    assert result["success"] is True
    assert result["test_page_closed"] is True
    assert main_page.closed is False
    assert context.closed is False


def test_error_state_does_not_retry_start_during_navigation(tmp_path: Path) -> None:
    settings = BrowserSettings(profile_dir=tmp_path / "booking_profile")
    attempts = 0

    def unavailable_factory() -> object:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("browser unavailable")

    service = BookingBrowserService(settings, playwright_factory=unavailable_factory)
    service.start()
    result = service.navigate("https://www.booking.com/hotel/example")

    assert attempts == 1
    assert result.status is NavigationStatus.BROWSER_CRASH


def test_navigation_is_serialized(tmp_path: Path) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    threads = [
        threading.Thread(target=service.navigate, args=(f"https://www.booking.com/hotel/{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    page = service.current_page()
    assert page is not None
    assert len(page.goto_calls) == 2
    assert page.max_active_navigations == 1
