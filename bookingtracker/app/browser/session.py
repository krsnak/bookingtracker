"""Conservative Booking page-state detection; no session values are read."""

from __future__ import annotations

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


def _has_selector(page: object, selector: str) -> bool:
    try:
        return page.locator(selector).count() > 0  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return False


def detect_page_state(page: object) -> tuple[AuthenticationState, BrowserState | None]:
    """Return UNKNOWN when page evidence is incomplete rather than guessing."""
    try:
        url = str(page.url).casefold()  # type: ignore[attr-defined]
        body_text = str(page.locator("body").inner_text(timeout=1_000)).casefold()  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return AuthenticationState.UNKNOWN, None

    evidence = f"{url} {body_text}"
    if any(marker in evidence for marker in _CHALLENGE_MARKERS):
        return AuthenticationState.UNKNOWN, BrowserState.CAPTCHA_REQUIRED
    if any(_has_selector(page, selector) for selector in _ACCOUNT_SELECTORS):
        return AuthenticationState.AUTHENTICATED, BrowserState.READY
    if "account/sign-in" in url or any(
        _has_selector(page, selector) for selector in _SIGN_IN_SELECTORS
    ):
        return AuthenticationState.LOGGED_OUT, BrowserState.LOGIN_REQUIRED
    return AuthenticationState.UNKNOWN, None
