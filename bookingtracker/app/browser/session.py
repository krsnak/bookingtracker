"""Conservative Booking page-state detection; no session values are read."""

from __future__ import annotations

from app.browser.dom import OptionalLocatorReader
from app.browser.models import AuthenticationState, BrowserState

_ACCOUNT_SELECTORS = (
    '[data-testid="header-profile"]',
    '[data-testid="profile-menu-trigger"]',
    '[aria-label*="Account"]',
)
_SIGN_IN_SELECTORS = (
    '[data-testid="header-sign-in-button"]',
    'a[href*="account/sign-in"]',
    'button:has-text("Sign in")',
)
_CHALLENGE_MARKERS = ("captcha", "challenge", "verify", "security-check")
_CHALLENGE_SELECTORS = (
    '[data-testid*="captcha"]',
    'iframe[src*="captcha"]',
    '[id*="challenge"]',
)


def detect_page_state(page: object) -> tuple[AuthenticationState, BrowserState | None]:
    """Return UNKNOWN when page evidence is incomplete rather than guessing."""
    reader = OptionalLocatorReader(page)
    url = str(page.url).casefold()  # type: ignore[attr-defined]
    body_text = (reader.text("body") or "").casefold()

    evidence = f"{url} {body_text}"
    if any(marker in evidence for marker in _CHALLENGE_MARKERS) or any(
        reader.exists(selector) for selector in _CHALLENGE_SELECTORS
    ):
        return AuthenticationState.UNKNOWN, BrowserState.CAPTCHA_REQUIRED
    if any(reader.exists(selector) for selector in _ACCOUNT_SELECTORS):
        return AuthenticationState.AUTHENTICATED, BrowserState.READY
    if "account/sign-in" in url or any(reader.exists(selector) for selector in _SIGN_IN_SELECTORS):
        return AuthenticationState.LOGGED_OUT, BrowserState.LOGIN_REQUIRED
    return AuthenticationState.UNKNOWN, None
