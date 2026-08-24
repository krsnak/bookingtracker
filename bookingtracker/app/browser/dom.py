"""Bounded reads for optional Playwright locators."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


class OptionalLocatorReader:
    """Read optional DOM evidence without accumulating locator auto-waits."""

    def __init__(
        self,
        page: object,
        *,
        total_timeout_ms: int = 250,
        per_locator_timeout_ms: int = 100,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._page = page
        self._per_locator_timeout_ms = per_locator_timeout_ms
        self._clock = clock
        self._deadline = clock() + total_timeout_ms / 1000

    def exists(self, selector: str) -> bool:
        if self._remaining_ms() <= 0:
            return False
        try:
            locator = self._page.locator(selector)  # type: ignore[attr-defined]
            return locator.count() > 0
        except PlaywrightTimeoutError:
            return False

    def text(self, selector: str) -> str | None:
        remaining = self._remaining_ms()
        if remaining < 10:
            return None
        try:
            locator = self._page.locator(selector)  # type: ignore[attr-defined]
            if locator.count() == 0:
                return None
            timeout = min(remaining, self._per_locator_timeout_ms)
            value = str(locator.first.inner_text(timeout=timeout)).strip()
            return value or None
        except PlaywrightTimeoutError:
            return None

    def texts(self, selector: str, *, max_items: int = 20) -> list[str]:
        if self._remaining_ms() < 10:
            return []
        locator = self._page.locator(selector)  # type: ignore[attr-defined]
        try:
            count = min(locator.count(), max_items)
        except PlaywrightTimeoutError:
            return []
        values: list[str] = []
        for index in range(count):
            remaining = self._remaining_ms()
            if remaining < 10:
                break
            try:
                value = str(
                    locator.nth(index).inner_text(
                        timeout=min(remaining, self._per_locator_timeout_ms)
                    )
                ).strip()
            except PlaywrightTimeoutError:
                continue
            if value:
                values.append(value)
        return values

    def _remaining_ms(self) -> int:
        return max(0, int((self._deadline - self._clock()) * 1000))
