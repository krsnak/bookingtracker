"""Manual, in-memory cross-property discovery using the existing browser session."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from app.booking.models import ParseStatus
from app.booking.navigation import BookingSearchUrlError, build_booking_discovery_url
from app.booking.parser import (
    BookingPropertyQualityParser,
    BookingRateParser,
    BookingSearchCardParser,
)
from app.browser.models import NavigationStatus
from app.matching.cross_property import (
    CrossPropertyAlternativeVerifier,
    QualifiedDiscoveryAlternative,
)
from app.reservations.models import Reservation


class BrowserForDiscovery(Protocol):
    def navigate(self, url: str): ...  # noqa: ANN201

    def current_page(self) -> object | None: ...


class DiscoveryStatus(StrEnum):
    SUCCESS = "success"
    INCOMPLETE_RESERVATION = "incomplete_reservation"
    MANUAL_ACTION_REQUIRED = "manual_action_required"
    NAVIGATION_ERROR = "navigation_error"
    PARSER_ERROR = "parser_error"


class DiscoveryResult(BaseModel):
    """Ephemeral Phase 1 result; it is intentionally not a price-check record."""

    status: DiscoveryStatus
    alternatives: list[QualifiedDiscoveryAlternative] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CrossPropertyDiscoveryService:
    """Search cards lead to details; only detail facts can qualify an alternative."""

    def __init__(
        self,
        browser: BrowserForDiscovery,
        card_parser: BookingSearchCardParser | None = None,
        rate_parser: BookingRateParser | None = None,
        quality_parser: BookingPropertyQualityParser | None = None,
        verifier: CrossPropertyAlternativeVerifier | None = None,
        max_candidates: int = 5,
    ) -> None:
        self.browser = browser
        self.card_parser = card_parser or BookingSearchCardParser()
        self.rate_parser = rate_parser or BookingRateParser()
        self.quality_parser = quality_parser or BookingPropertyQualityParser()
        self.verifier = verifier or CrossPropertyAlternativeVerifier()
        self.max_candidates = max(1, max_candidates)

    def discover(self, reservation: Reservation, destination: str) -> DiscoveryResult:
        try:
            search_url = build_booking_discovery_url(reservation, destination)
        except BookingSearchUrlError:
            return DiscoveryResult(status=DiscoveryStatus.INCOMPLETE_RESERVATION)
        navigation = self.browser.navigate(search_url)
        status = self._navigation_status(navigation.status)
        if status is not None:
            return DiscoveryResult(status=status)
        content = self._page_content()
        if content is None:
            return DiscoveryResult(status=DiscoveryStatus.PARSER_ERROR)
        cards = self.card_parser.parse_html(content, source_url=search_url)
        alternatives: list[QualifiedDiscoveryAlternative] = []
        warnings: list[str] = []
        for card in cards[: self.max_candidates]:
            detail_navigation = self.browser.navigate(card.detail_url)
            detail_status = self._navigation_status(detail_navigation.status)
            if detail_status is not None:
                warnings.append(f"detail navigation skipped: {detail_status.value}")
                if detail_status is DiscoveryStatus.MANUAL_ACTION_REQUIRED:
                    return DiscoveryResult(
                        status=detail_status, alternatives=alternatives, warnings=warnings
                    )
                continue
            detail_content = self._page_content()
            if detail_content is None:
                warnings.append("detail page content unavailable")
                continue
            parsed = self.rate_parser.parse_html(detail_content, source_url=card.detail_url)
            if parsed.status not in {ParseStatus.SUCCESS, ParseStatus.PARTIAL}:
                warnings.append("detail rate structure was not recognized")
                continue
            qualification = self.verifier.verify(
                reservation,
                card,
                self.quality_parser.parse_html(detail_content),
                parsed.offers,
            )
            alternatives.extend(qualification.qualified)
        return DiscoveryResult(
            status=DiscoveryStatus.SUCCESS, alternatives=alternatives, warnings=warnings
        )

    def _page_content(self) -> str | None:
        if hasattr(self.browser, "page_content"):
            content = self.browser.page_content()  # type: ignore[attr-defined]
            if content is not None:
                return content
        page = self.browser.current_page()
        if page is None:
            return None
        try:
            return page.content()  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError):
            return None

    @staticmethod
    def _navigation_status(status: NavigationStatus) -> DiscoveryStatus | None:
        if status is NavigationStatus.SUCCESS:
            return None
        if status in {NavigationStatus.CAPTCHA_REQUIRED, NavigationStatus.LOGIN_REQUIRED}:
            return DiscoveryStatus.MANUAL_ACTION_REQUIRED
        return DiscoveryStatus.NAVIGATION_ERROR
