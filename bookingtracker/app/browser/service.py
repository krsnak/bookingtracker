"""Long-lived, serialized Playwright wrapper for the Booking browser context."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
import platform
from threading import RLock
from typing import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.browser.models import (
    AuthenticationState,
    BrowserHealth,
    BrowserState,
    NavigationResult,
    NavigationStatus,
)
from app.browser.session import detect_page_state
from app.config import BrowserSettings

PlaywrightFactory = Callable[[], object]


class BookingBrowserService:
    """Owns a single persistent browser context and serializes all navigation."""

    def __init__(
        self,
        settings: BrowserSettings,
        playwright_factory: PlaywrightFactory | None = None,
    ) -> None:
        self._settings = settings
        self._playwright_factory = playwright_factory
        self._playwright: object | None = None
        self._context: object | None = None
        self._context_active = False
        self._primary_page: object | None = None
        self._state = BrowserState.STOPPED
        self._auth_state = AuthenticationState.UNKNOWN
        self._last_successful_navigation: datetime | None = None
        self._last_error: str | None = None
        self._lock = RLock()

    def start(self) -> BrowserHealth:
        with self._lock:
            if self._context_is_alive():
                return self.health()
            self._state = BrowserState.STARTING
            self._last_error = None
            try:
                factory = self._playwright_factory or self._default_playwright_factory
                self._playwright = factory()
                chromium = self._playwright.chromium  # type: ignore[attr-defined]
                self._settings.profile_dir.mkdir(parents=True, exist_ok=True)
                kwargs: dict[str, object] = {
                    "user_data_dir": str(self._settings.profile_dir),
                    "headless": self._settings.headless,
                    "viewport": None,
                }
                if self._settings.channel:
                    kwargs["channel"] = self._settings.channel
                if self._settings.executable_path:
                    kwargs["executable_path"] = str(self._settings.executable_path)
                if self._settings.launch_args:
                    kwargs["args"] = list(self._settings.launch_args)
                self._context = chromium.launch_persistent_context(**kwargs)
                self._context_active = True
                self._primary_page = self._recover_page()
                self._refresh_page_state()
                if self._state is BrowserState.STARTING:
                    self._state = BrowserState.READY
            except (OSError, RuntimeError, PlaywrightError) as error:
                self._set_error(f"browser start failed: {error}")
            return self.health()

    def stop(self) -> BrowserHealth:
        with self._lock:
            with suppress(AttributeError, RuntimeError):
                if self._context:
                    self._context.close()  # type: ignore[attr-defined]
            with suppress(AttributeError, RuntimeError):
                if self._playwright:
                    self._playwright.stop()  # type: ignore[attr-defined]
            self._context = None
            self._context_active = False
            self._playwright = None
            self._primary_page = None
            self._state = BrowserState.STOPPED
            self._auth_state = AuthenticationState.UNKNOWN
            return self.health()

    def ensure_page(self) -> object | None:
        with self._lock:
            if not self._context_is_alive():
                if self._state is BrowserState.STOPPED:
                    self.start()
                else:
                    return None
            if not self._context_is_alive():
                return None
            try:
                self._primary_page = self._recover_page()
            except (AttributeError, RuntimeError, PlaywrightError) as error:
                self._set_error(f"page recovery failed: {error}")
                return None
            return self._primary_page

    def current_page(self) -> object | None:
        with self._lock:
            return self._primary_page if self._page_is_alive(self._primary_page) else None

    def navigate(self, url: str) -> NavigationResult:
        with self._lock:
            page = self.ensure_page()
            if page is None:
                return self._result(url, NavigationStatus.BROWSER_CRASH, error=self._last_error)
            try:
                page.goto(  # type: ignore[attr-defined]
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._settings.navigation_timeout_ms,
                )
                self._refresh_page_state(page)
                if self._state is BrowserState.CAPTCHA_REQUIRED:
                    return self._result(url, NavigationStatus.CAPTCHA_REQUIRED, page)
                if self._state in {BrowserState.LOGGED_OUT, BrowserState.LOGIN_REQUIRED}:
                    return self._result(url, NavigationStatus.LOGIN_REQUIRED, page)
                self._last_successful_navigation = datetime.now()
                self._state = BrowserState.READY
                return self._result(url, NavigationStatus.SUCCESS, page)
            except (TimeoutError, PlaywrightTimeoutError) as error:
                self._last_error = str(error)
                return self._result(url, NavigationStatus.TIMEOUT, error=str(error))
            except (AttributeError, RuntimeError, PlaywrightError) as error:
                message = str(error)
                status = self._classify_navigation_error(message)
                self._last_error = message
                if status is NavigationStatus.BROWSER_CRASH:
                    self._state = BrowserState.ERROR
                    self._context_active = False
                return self._result(url, status, error=message)

    def is_logged_in(self) -> AuthenticationState:
        with self._lock:
            page = self.current_page()
            if page:
                self._refresh_page_state(page)
            return self._auth_state

    def smoke_test(self) -> dict[str, object]:
        with self._lock:
            result: dict[str, object] = {
                "success": False,
                "architecture": platform.machine(),
                "chromium_executable": str(self._settings.executable_path)
                if self._settings.executable_path
                else None,
                "persistent_context_active": self._context_is_alive(),
                "test_page_loaded": False,
                "test_page_closed": False,
                "error": None,
            }
            if not self._context_is_alive():
                result["error"] = "browser context is not active"
                return result
            page = None
            try:
                page = self._context.new_page()  # type: ignore[union-attr]
                page.goto("data:text/html,<title>BookingTracker browser smoke</title>")
                result["browser_version"] = page.evaluate("navigator.userAgent")
                result["test_page_loaded"] = page.title() == "BookingTracker browser smoke"
                result["success"] = bool(result["test_page_loaded"])
            except (AttributeError, RuntimeError, PlaywrightError) as error:
                result["error"] = str(error).split("\n", 1)[0]
            finally:
                if page is not None:
                    with suppress(AttributeError, RuntimeError):
                        page.close()
                        result["test_page_closed"] = True
            return result

    def requires_manual_action(self) -> bool:
        return self._state in {BrowserState.LOGIN_REQUIRED, BrowserState.CAPTCHA_REQUIRED}

    def status(self) -> BrowserState:
        return self._state

    def health(self) -> BrowserHealth:
        with self._lock:
            context_running = self._context_is_alive()
            return BrowserHealth(
                state=self._state,
                process_running=self._playwright is not None and context_running,
                context_running=context_running,
                page_available=self._page_is_alive(self._primary_page),
                booking_auth_state=self._auth_state,
                manual_action_required=self.requires_manual_action(),
                last_successful_navigation=self._last_successful_navigation,
                last_error=self._last_error,
            )

    @staticmethod
    def _default_playwright_factory() -> object:
        from playwright.sync_api import sync_playwright

        return sync_playwright().start()

    def _recover_page(self) -> object:
        if self._page_is_alive(self._primary_page):
            return self._primary_page
        pages = list(self._context.pages)  # type: ignore[union-attr]
        for page in pages:
            if self._page_is_alive(page):
                return page
        return self._context.new_page()  # type: ignore[union-attr]

    @staticmethod
    def _page_is_alive(page: object | None) -> bool:
        if page is None:
            return False
        try:
            return not page.is_closed()  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError):
            return False

    def _context_is_alive(self) -> bool:
        if self._context is None or not self._context_active:
            return False
        try:
            _ = self._context.pages  # type: ignore[attr-defined]
            return True
        except (AttributeError, RuntimeError):
            self._context_active = False
            return False

    def _refresh_page_state(self, page: object | None = None) -> None:
        target = page or self._primary_page
        if target is None:
            return
        auth_state, state = detect_page_state(target)
        self._auth_state = auth_state
        if state is not None:
            self._state = state

    def _result(
        self,
        requested_url: str,
        status: NavigationStatus,
        page: object | None = None,
        error: str | None = None,
    ) -> NavigationResult:
        return NavigationResult(
            requested_url=requested_url,
            final_url=str(page.url) if page else None,  # type: ignore[attr-defined]
            title=page.title() if page else None,  # type: ignore[attr-defined]
            status=status,
            authenticated_state=self._auth_state,
            manual_action_required=self.requires_manual_action(),
            error=error,
        )

    @staticmethod
    def _classify_navigation_error(message: str) -> NavigationStatus:
        lowered = message.casefold()
        if "closed" in lowered:
            return NavigationStatus.PAGE_CLOSED
        if "browser" in lowered or "target page" in lowered:
            return NavigationStatus.BROWSER_CRASH
        return NavigationStatus.NAVIGATION_ERROR

    def _set_error(self, message: str) -> None:
        lowered = message.casefold()
        if "user data directory" in lowered and "use" in lowered:
            message = "BROWSER_PROFILE_LOCKED: BookingTracker profile is in use by another process"
        self._state = BrowserState.ERROR
        self._last_error = message.split("\nBrowser logs:", maxsplit=1)[0]
