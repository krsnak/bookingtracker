"""One explicit browser-to-history check operation; no scheduling or alerts."""
# ruff: noqa: E501

from __future__ import annotations

from typing import Protocol

from app.booking.models import ParseStatus
from app.booking.parser import BookingRateParser
from app.browser.models import NavigationStatus
from app.db.repository import PriceCheckRepository
from app.matching.matcher import ExactReservationMatcher
from app.matching.models import MatchClassification
from app.pricing.models import PriceCheckRecord, PriceCheckStatus
from app.pricing.service import ComparablePriceService
from app.reservations.models import Reservation


class BrowserForPriceCheck(Protocol):
    def navigate(self, url: str): ...  # noqa: ANN201

    def current_page(self) -> object | None: ...


class PriceCheckService:
    """Coordinates completed layers and persists every terminal outcome."""

    def __init__(
        self,
        browser: BrowserForPriceCheck,
        parser: BookingRateParser,
        matcher: ExactReservationMatcher,
        pricing: ComparablePriceService,
        history: PriceCheckRepository,
    ) -> None:
        self.browser = browser
        self.parser = parser
        self.matcher = matcher
        self.pricing = pricing
        self.history = history

    def check(self, reservation: Reservation, *, run_id: str | None = None) -> PriceCheckRecord:
        if not reservation.booking_url:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.NAVIGATION_ERROR,
                    error="reservation has no Booking URL",
                ),
                [],
            )
        navigation = self.browser.navigate(reservation.booking_url)
        status = self._navigation_status(navigation.status)
        if status is not None:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    run_id=run_id or "explicit-check",
                    status=status,
                    error=navigation.error,
                ),
                [],
            )
        content = self.browser.page_content() if hasattr(self.browser, "page_content") else None
        page = self.browser.current_page() if content is None else None
        if content is None and page is None:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.BROWSER_ERROR,
                    error="no browser page after successful navigation",
                ),
                [],
            )
        try:
            parsed = self.parser.parse_html(
                content if content is not None else page.content(),
                source_url=reservation.booking_url,
            )
        except Exception as error:  # parser failure is a persisted outcome, not a crash
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.PARSER_ERROR,
                    error=self._safe_error(error),
                ),
                [],
            )
        if parsed.status is ParseStatus.NO_AVAILABILITY:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.NO_AVAILABILITY,
                    parser_status=parsed.status,
                    warnings=parsed.warnings,
                ),
                parsed.offers,
            )
        if parsed.status is not ParseStatus.SUCCESS:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.PARSER_ERROR,
                    parser_status=parsed.status,
                    error=parsed.error,
                    warnings=parsed.warnings,
                ),
                parsed.offers,
            )
        match = self.matcher.match(reservation, parsed.offers)
        if not match.accepted:
            ambiguous = any(
                item.classification is MatchClassification.AMBIGUOUS
                for item in match.candidate_evaluations
            )
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.AMBIGUOUS if ambiguous else PriceCheckStatus.NO_MATCH,
                    parser_status=parsed.status,
                    match_classification=match.classification,
                    match_score=match.score,
                    match_result=match,
                    warnings=match.warnings,
                ),
                parsed.offers,
            )
        comparison = self.pricing.compare(reservation, match)
        return self._persist(
            PriceCheckRecord(
                reservation_id=reservation.id,
                run_id=run_id or "explicit-check",
                status=PriceCheckStatus.SUCCESS,
                parser_status=parsed.status,
                matched=True,
                match_classification=match.classification,
                match_score=match.score,
                comparison=comparison,
                match_result=match,
                warnings=parsed.warnings + match.warnings,
            ),
            parsed.offers,
        )

    def _persist(self, record: PriceCheckRecord, offers: list) -> PriceCheckRecord:  # noqa: ANN001
        return self.history.create(record, offers)

    @staticmethod
    def _navigation_status(status: NavigationStatus) -> PriceCheckStatus | None:
        mapping = {
            NavigationStatus.LOGIN_REQUIRED: PriceCheckStatus.LOGGED_OUT,
            NavigationStatus.CAPTCHA_REQUIRED: PriceCheckStatus.CAPTCHA_REQUIRED,
            NavigationStatus.TIMEOUT: PriceCheckStatus.TIMEOUT,
            NavigationStatus.NAVIGATION_ERROR: PriceCheckStatus.NAVIGATION_ERROR,
            NavigationStatus.BROWSER_CRASH: PriceCheckStatus.BROWSER_ERROR,
            NavigationStatus.PAGE_CLOSED: PriceCheckStatus.BROWSER_ERROR,
        }
        return mapping.get(status)

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return str(error).split("\n", maxsplit=1)[0]
