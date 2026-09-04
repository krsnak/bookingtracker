from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from app.booking.selectors import BookingSelectors
from app.browser.dom import OptionalLocatorReader
from app.browser.models import AuthenticationState, BrowserState, NavigationStatus
from app.browser.service import BookingBrowserService
from app.config import BrowserSettings
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


class FakeLocator:
    def __init__(
        self,
        count: int = 0,
        text: str = "",
        inner_error: Exception | None = None,
        on_click: Callable[[], None] | None = None,
        visible: bool = True,
        enabled: bool = True,
    ) -> None:
        self._count = count
        self._text = text
        self._inner_error = inner_error
        self.inner_text_timeouts: list[int] = []
        self._on_click = on_click
        self._visible = visible
        self._enabled = enabled

    @property
    def first(self) -> FakeLocator:
        return self

    def nth(self, index: int) -> FakeLocator:
        del index
        return self

    def count(self) -> int:
        return self._count

    def inner_text(self, timeout: int) -> str:
        self.inner_text_timeouts.append(timeout)
        if self._inner_error:
            raise self._inner_error
        return self._text

    def click(self, timeout: int) -> None:
        del timeout
        if self._on_click:
            self._on_click()

    def is_visible(self) -> bool:
        return self._visible

    def is_enabled(self) -> bool:
        return self._enabled


class FakePage:
    def __init__(self) -> None:
        self.url = "https://www.booking.com/"
        self.closed = False
        self.body_text = ""
        self.body_error: Exception | None = None
        self.selector_counts: dict[str, int] = {}
        self.goto_error: Exception | None = None
        self.goto_calls: list[str] = []
        self.wait_for_selector_calls: list[tuple[str, str, int]] = []
        self.wait_for_selector_error: Exception | None = None
        self.active_navigations = 0
        self.max_active_navigations = 0
        self.scroll_calls = 0
        self.activation_clicks = 0
        self.activate_surface: str | None = None
        self.activate_body_text: str | None = None
        self.activation_visible = True
        self.activation_enabled = True

    def locator(self, selector: str) -> FakeLocator:
        if selector == "body":
            return FakeLocator(count=1, text=self.body_text, inner_error=self.body_error)
        if selector == 'button[data-testid="reviews-block-availability"]':
            return FakeLocator(
                count=self.selector_counts.get(selector, 0),
                on_click=self._activate,
                visible=self.activation_visible,
                enabled=self.activation_enabled,
            )
        if selector == BookingSelectors.UNKNOWN_OFFER_HINT:
            return FakeLocator(
                count=sum(
                    self.selector_counts.get(hint, 0)
                    for hint in UNKNOWN_OFFER_HINT_SELECTORS
                )
            )
        return FakeLocator(count=self.selector_counts.get(selector, 0))

    def _activate(self) -> None:
        self.activation_clicks += 1
        if self.activate_body_text is not None:
            self.body_text = self.activate_body_text
        if self.activate_surface:
            self.selector_counts[self.activate_surface] = 1
            self.wait_for_selector_error = None

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

    def wait_for_selector(self, selector: str, *, state: str, timeout: int) -> None:
        self.wait_for_selector_calls.append((selector, state, timeout))
        if self.wait_for_selector_error:
            raise self.wait_for_selector_error

    def evaluate(self, script: str) -> str:
        if "scrollTo" in script:
            self.scroll_calls += 1
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


UNKNOWN_OFFER_HINT_SELECTORS = (
    '[data-testid="room-card"]',
    '[data-testid="rate-card"]',
    '[data-testid="current-price"]',
    '[data-testid="room-card"]',
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


def test_optional_page_state_locator_timeout_does_not_become_navigation_timeout(
    tmp_path: Path,
) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None
    page.body_error = PlaywrightTimeoutError("Locator.inner_text timed out")

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is NavigationStatus.SUCCESS


def test_navigation_waits_boundedly_for_async_availability_surface(tmp_path: Path) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is NavigationStatus.SUCCESS
    selector, state, timeout = page.wait_for_selector_calls[-1]
    assert 'tr.js-rt-block-row' in selector
    assert '[data-testid="availability-table"]' in selector
    assert state == "attached"
    assert timeout == 10_000


def test_missing_availability_surface_is_availability_unknown(tmp_path: Path) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None
    page.wait_for_selector_error = PlaywrightTimeoutError("availability not rendered")

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is NavigationStatus.AVAILABILITY_UNKNOWN
    assert len(page.goto_calls) == 1
    assert len(page.wait_for_selector_calls) == 2


def test_empty_shell_activates_once_without_second_goto(tmp_path: Path) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None
    page.wait_for_selector_error = PlaywrightTimeoutError("availability not rendered")
    page.selector_counts['button[data-testid="reviews-block-availability"]'] = 1

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is NavigationStatus.AVAILABILITY_UNKNOWN
    assert page.scroll_calls == 1
    assert page.activation_clicks == 1
    assert len(page.goto_calls) == 1


def test_activation_can_surface_explicit_no_availability(tmp_path: Path) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None
    page.wait_for_selector_error = PlaywrightTimeoutError("availability not rendered")
    page.selector_counts['button[data-testid="reviews-block-availability"]'] = 1
    page.activate_surface = '[data-testid="no-availability"]'

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is NavigationStatus.SUCCESS
    assert page.activation_clicks == 1


@pytest.mark.parametrize(
    "offer_hints",
    (
        ('[data-testid="room-card"]',),
        ('[data-testid="rate-card"]',),
        ('[data-testid="current-price"]',),
        (
            '[data-testid="room-card"]',
            '[data-testid="rate-card"]',
            '[data-testid="current-price"]',
        ),
    ),
    ids=("room_hint", "rate_hint", "price_hint", "room_rate_price_hints"),
)
def test_unknown_offer_hints_continue_to_offer_parser(
    tmp_path: Path, offer_hints: tuple[str, ...]
) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None
    page.wait_for_selector_error = PlaywrightTimeoutError("availability not rendered")
    for hint in offer_hints:
        page.selector_counts[hint] = 1

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is NavigationStatus.SUCCESS
    assert len(page.goto_calls) == 1
    assert page.activation_clicks == 0


def test_captcha_after_activation_has_priority_over_availability_unknown(tmp_path: Path) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None
    page.wait_for_selector_error = PlaywrightTimeoutError("availability not rendered")
    page.selector_counts['button[data-testid="reviews-block-availability"]'] = 1
    page.activate_body_text = "Please complete the CAPTCHA security challenge"

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is NavigationStatus.CAPTCHA_REQUIRED
    assert page.activation_clicks == 1
    assert page.scroll_calls == 1
    assert len(page.goto_calls) == 1
    assert len(page.wait_for_selector_calls) == 1


@pytest.mark.parametrize(
    ("count", "visible", "enabled"),
    ((0, True, True), (1, False, True), (1, True, False), (2, True, True)),
    ids=("missing", "hidden", "disabled", "ambiguous"),
)
def test_unsafe_activation_control_is_never_clicked(
    tmp_path: Path, count: int, visible: bool, enabled: bool
) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None
    page.wait_for_selector_error = PlaywrightTimeoutError("availability not rendered")
    page.selector_counts['button[data-testid="reviews-block-availability"]'] = count
    page.activation_visible = visible
    page.activation_enabled = enabled

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is NavigationStatus.AVAILABILITY_UNKNOWN
    assert page.activation_clicks == 0
    assert page.scroll_calls == 1
    assert len(page.goto_calls) == 1


@pytest.mark.parametrize(
    ("body_text", "login", "surface", "hint", "goto_error", "expected"),
    (
        (
            "Please complete the CAPTCHA security challenge",
            False,
            BookingSelectors.ROOM,
            False,
            None,
            NavigationStatus.CAPTCHA_REQUIRED,
        ),
        ("", True, BookingSelectors.NO_AVAILABILITY, False, None, NavigationStatus.LOGIN_REQUIRED),
        (
            "",
            False,
            BookingSelectors.ROOM,
            False,
            RuntimeError("network connection interrupted"),
            NavigationStatus.NAVIGATION_ERROR,
        ),
        ("", False, BookingSelectors.ROOM, False, None, NavigationStatus.SUCCESS),
        ("", False, BookingSelectors.NO_AVAILABILITY, True, None, NavigationStatus.SUCCESS),
        ("", False, None, True, None, NavigationStatus.SUCCESS),
        ("", False, None, False, None, NavigationStatus.AVAILABILITY_UNKNOWN),
    ),
    ids=(
        "captcha_over_offer",
        "login_over_no_availability",
        "navigation_error_over_offer",
        "known_offer",
        "no_availability_over_unknown_hint",
        "unknown_offer_hint",
        "empty_shell",
    ),
)
def test_navigation_state_priority(
    tmp_path: Path,
    body_text: str,
    login: bool,
    surface: str | None,
    hint: bool,
    goto_error: Exception | None,
    expected: NavigationStatus,
) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None
    page.body_text = body_text
    page.goto_error = goto_error
    if login:
        page.selector_counts['[data-testid="header-sign-in-button"]'] = 1
    if surface:
        page.selector_counts[surface] = 1
    if hint:
        page.selector_counts['[data-testid="room-card"]'] = 1
    if not surface:
        page.wait_for_selector_error = PlaywrightTimeoutError("availability not rendered")

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is expected


def test_challenge_selector_survives_optional_body_text_timeout(tmp_path: Path) -> None:
    service, _, _ = build_service(tmp_path)
    service.start()
    page = service.current_page()
    assert page is not None
    page.body_error = PlaywrightTimeoutError("Locator.inner_text timed out")
    page.selector_counts['iframe[src*="captcha"]'] = 1

    result = service.navigate("https://www.booking.com/hotel/example")

    assert result.status is NavigationStatus.CAPTCHA_REQUIRED


def test_optional_locator_reader_skips_missing_and_expected_timeout() -> None:
    page = FakePage()
    missing = OptionalLocatorReader(page)
    assert missing.text('[data-testid="optional"]') is None
    assert missing.texts('[data-testid="optional"]') == []

    page.body_error = PlaywrightTimeoutError("expected locator timeout")
    timed_out = OptionalLocatorReader(page, total_timeout_ms=80, per_locator_timeout_ms=50)
    assert timed_out.text("body") is None
    assert timed_out.texts("body") == []


def test_optional_locator_reader_does_not_mask_programming_error() -> None:
    page = FakePage()
    page.body_error = ValueError("broken test implementation")
    with pytest.raises(ValueError, match="broken test implementation"):
        OptionalLocatorReader(page).text("body")


def test_optional_locator_reader_shares_one_bounded_budget() -> None:
    now = [0.0]
    requested_timeouts: list[int] = []

    class SlowLocator(FakeLocator):
        def inner_text(self, timeout: int) -> str:
            requested_timeouts.append(timeout)
            now[0] += timeout / 1000
            raise PlaywrightTimeoutError("optional locator timeout")

    class SlowPage(FakePage):
        def locator(self, selector: str) -> FakeLocator:
            del selector
            return SlowLocator(count=1)

    reader = OptionalLocatorReader(
        SlowPage(),
        total_timeout_ms=250,
        per_locator_timeout_ms=100,
        clock=lambda: now[0],
    )
    assert [reader.text(str(index)) for index in range(4)] == [None, None, None, None]
    assert sum(requested_timeouts) <= 250
    assert len(requested_timeouts) == 3


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
