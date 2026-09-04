"""One explicit browser-to-history check operation; no scheduling or alerts."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from app.booking.models import ParseStatus
from app.booking.navigation import BookingSearchUrlError, build_booking_search_url
from app.booking.parser import BookingRateParser
from app.browser.models import NavigationStatus
from app.db.repository import PriceCheckRepository
from app.matching.matcher import ExactReservationMatcher
from app.matching.models import MatchClassification
from app.pricing.diagnostics import reason_code_for, sanitize_error_detail
from app.pricing.models import (
    CheckDiagnosticPhase,
    CheckReasonCode,
    PriceCheckRecord,
    PriceCheckStatus,
)
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
        started_at = datetime.now(UTC)
        incomplete_detail = self._incomplete_reservation_detail(reservation)
        if incomplete_detail:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    checked_at=started_at,
                    started_at=started_at,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.INCOMPLETE_RESERVATION,
                    reason_code=CheckReasonCode.INCOMPLETE_RESERVATION,
                    diagnostic_phase=CheckDiagnosticPhase.RESERVATION_VALIDATION,
                    safe_error_detail=incomplete_detail,
                ),
                [],
            )
        try:
            navigation_url = build_booking_search_url(reservation.booking_url, reservation)
        except BookingSearchUrlError:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    checked_at=started_at,
                    started_at=started_at,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.INCOMPLETE_RESERVATION,
                    reason_code=CheckReasonCode.INCOMPLETE_RESERVATION,
                    diagnostic_phase=CheckDiagnosticPhase.RESERVATION_VALIDATION,
                    safe_error_detail=(
                        "Kontrolu nelze spustit, protože odkaz na Booking.com není platný. "
                        "Doplňte jej v editaci rezervace."
                    ),
                ),
                [],
            )
        try:
            navigation = self.browser.navigate(navigation_url)
        except Exception as error:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    checked_at=started_at,
                    started_at=started_at,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.BROWSER_ERROR,
                    reason_code=CheckReasonCode.UNEXPECTED_ERROR,
                    safe_error_detail=sanitize_error_detail(
                        error, fallback="unexpected browser failure"
                    ),
                ),
                [],
            )
        status = self._navigation_status(navigation.status)
        if status is not None:
            availability_unknown = status is PriceCheckStatus.AVAILABILITY_UNKNOWN
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    checked_at=started_at,
                    started_at=started_at,
                    run_id=run_id or "explicit-check",
                    status=status,
                    error=navigation.error,
                    reason_code=(
                        CheckReasonCode.AVAILABILITY_UNKNOWN if availability_unknown else None
                    ),
                    diagnostic_phase=(
                        CheckDiagnosticPhase.AVAILABILITY_DETECTION
                        if availability_unknown
                        else None
                    ),
                    safe_error_detail=(
                        "Dostupnost se nepodařilo ověřit. Booking.com pro zadaný termín "
                        "nezobrazil nabídky ani potvrzení, že je ubytování vyprodané."
                        if availability_unknown
                        else None
                    ),
                ),
                [],
            )
        content = self.browser.page_content() if hasattr(self.browser, "page_content") else None
        page = self.browser.current_page() if content is None else None
        if content is None and page is None:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    checked_at=started_at,
                    started_at=started_at,
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
                    checked_at=started_at,
                    started_at=started_at,
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
                    checked_at=started_at,
                    started_at=started_at,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.NO_AVAILABILITY,
                    parser_status=parsed.status,
                    safe_error_detail="Nebyla nalezena bezpečně porovnatelná nabídka.",
                    warnings=parsed.warnings,
                ),
                parsed.offers,
            )
        if parsed.status not in {ParseStatus.SUCCESS, ParseStatus.PARTIAL}:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    checked_at=started_at,
                    started_at=started_at,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.PARSER_ERROR,
                    parser_status=parsed.status,
                    error="Povinná struktura cenové nabídky nebyla rozpoznána.",
                    warnings=parsed.warnings,
                ),
                parsed.offers,
            )
        if not parsed.offers:
            return self._persist(
                PriceCheckRecord(
                    reservation_id=reservation.id,
                    checked_at=started_at,
                    started_at=started_at,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.PARSER_ERROR,
                    parser_status=parsed.status,
                    error="Povinná struktura cenové nabídky nebyla rozpoznána.",
                    warnings=parsed.warnings,
                ),
                [],
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
                    checked_at=started_at,
                    started_at=started_at,
                    run_id=run_id or "explicit-check",
                    status=PriceCheckStatus.AMBIGUOUS if ambiguous else PriceCheckStatus.NO_MATCH,
                    parser_status=parsed.status,
                    match_classification=match.classification,
                    match_score=match.score,
                    match_result=match,
                    safe_error_detail="Nebyla nalezena bezpečně porovnatelná nabídka.",
                    warnings=parsed.warnings + match.warnings,
                ),
                parsed.offers,
            )
        comparison = self.pricing.compare(reservation, match)
        return self._persist(
            PriceCheckRecord(
                reservation_id=reservation.id,
                checked_at=started_at,
                started_at=started_at,
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
        finished_at = datetime.now(UTC)
        reason = record.reason_code or reason_code_for(record.status, record.error)
        safe_detail = record.safe_error_detail or sanitize_error_detail(record.error)
        completed = record.model_copy(
            update={
                "finished_at": finished_at,
                "duration_ms": max(
                    0,
                    int((finished_at - (record.started_at or record.checked_at)).total_seconds() * 1000),
                ),
                "reason_code": reason,
                "diagnostic_phase": record.diagnostic_phase
                or self._diagnostic_phase(record),
                "safe_error_detail": safe_detail,
                "error": safe_detail,
            }
        )
        return self.history.create(completed, offers)

    @staticmethod
    def _diagnostic_phase(record: PriceCheckRecord) -> CheckDiagnosticPhase:
        if record.status in {
            PriceCheckStatus.LOGGED_OUT,
            PriceCheckStatus.CAPTCHA_REQUIRED,
        }:
            return CheckDiagnosticPhase.PAGE_STATE_DETECTION
        if record.status in {
            PriceCheckStatus.NAVIGATION_ERROR,
            PriceCheckStatus.TIMEOUT,
            PriceCheckStatus.BROWSER_ERROR,
        }:
            return CheckDiagnosticPhase.PAGE_NAVIGATION
        if record.status is PriceCheckStatus.AVAILABILITY_UNKNOWN:
            return CheckDiagnosticPhase.AVAILABILITY_DETECTION
        if record.status in {
            PriceCheckStatus.PARSER_ERROR,
            PriceCheckStatus.NO_AVAILABILITY,
        }:
            return CheckDiagnosticPhase.OFFER_COLLECTION
        return CheckDiagnosticPhase.EXACT_MATCH

    @staticmethod
    def _incomplete_reservation_detail(reservation: Reservation) -> str | None:
        if not reservation.booking_url:
            return (
                "Kontrolu nelze spustit, protože chybí odkaz na Booking.com. "
                "Doplňte jej v editaci rezervace."
            )
        if not reservation.check_in or not reservation.check_out:
            return (
                "Kontrolu nelze spustit, protože není uveden příjezd nebo odjezd. "
                "Doplňte jej v editaci rezervace."
            )
        if reservation.adults is None:
            return (
                "Kontrolu nelze spustit, protože není uveden počet dospělých. "
                "Doplňte jej v editaci rezervace."
            )
        if reservation.children is None:
            return (
                "Kontrolu nelze spustit, protože není uveden počet dětí. "
                "Doplňte jej v editaci rezervace."
            )
        if reservation.rooms_count is None:
            return (
                "Kontrolu nelze spustit, protože není uveden počet pokojů. "
                "Doplňte jej v editaci rezervace."
            )
        return None

    @staticmethod
    def _navigation_status(status: NavigationStatus) -> PriceCheckStatus | None:
        mapping = {
            NavigationStatus.LOGIN_REQUIRED: PriceCheckStatus.LOGGED_OUT,
            NavigationStatus.CAPTCHA_REQUIRED: PriceCheckStatus.CAPTCHA_REQUIRED,
            NavigationStatus.TIMEOUT: PriceCheckStatus.TIMEOUT,
            NavigationStatus.NAVIGATION_ERROR: PriceCheckStatus.NAVIGATION_ERROR,
            NavigationStatus.BROWSER_CRASH: PriceCheckStatus.BROWSER_ERROR,
            NavigationStatus.PAGE_CLOSED: PriceCheckStatus.BROWSER_ERROR,
            NavigationStatus.AVAILABILITY_UNKNOWN: PriceCheckStatus.AVAILABILITY_UNKNOWN,
        }
        return mapping.get(status)

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return sanitize_error_detail(error, fallback="parser failure") or "parser failure"
