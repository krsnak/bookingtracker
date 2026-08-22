"""Application-level browser states and results, independent of Playwright."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class BrowserState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    LOGGED_OUT = "logged_out"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA_REQUIRED = "captcha_required"
    ERROR = "error"


class AuthenticationState(StrEnum):
    AUTHENTICATED = "authenticated"
    LOGGED_OUT = "logged_out"
    UNKNOWN = "unknown"


class NavigationStatus(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    NAVIGATION_ERROR = "navigation_error"
    BROWSER_CRASH = "browser_crash"
    PAGE_CLOSED = "page_closed"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA_REQUIRED = "captcha_required"


@dataclass(frozen=True)
class BrowserHealth:
    state: BrowserState
    process_running: bool
    context_running: bool
    page_available: bool
    booking_auth_state: AuthenticationState
    manual_action_required: bool
    last_successful_navigation: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class NavigationResult:
    requested_url: str
    final_url: str | None
    title: str | None
    status: NavigationStatus
    authenticated_state: AuthenticationState
    manual_action_required: bool
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is NavigationStatus.SUCCESS
