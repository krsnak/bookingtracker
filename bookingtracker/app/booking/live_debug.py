"""Safe local live/replay laboratory using the production parser and matcher."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.alerts.service import AlertService
from app.booking.models import ParseResult, ParseStatus, RateOffer
from app.booking.navigation import build_booking_search_url
from app.booking.parser import BookingRateParser
from app.booking.selectors import BookingSelectors
from app.matching.matcher import ExactReservationMatcher
from app.matching.models import MatchResult
from app.pricing.check_service import PriceCheckService
from app.pricing.diagnostics import reason_code_for, sanitize_error_detail
from app.pricing.models import CheckDiagnosticPhase, CheckReasonCode, PriceCheckRecord
from app.pricing.service import ComparablePriceService
from app.reservations.models import Reservation
from app.scheduling.models import CheckTrigger, ScheduleState
from app.scheduling.policy import SchedulePolicy
from app.scheduling.service import CheckRunner


class LiveBookingConfig(BaseModel):
    """Non-secret facts needed to reproduce one exact availability search."""

    property_name: str = Field(min_length=1, max_length=160)
    hotel_url: str
    check_in: date
    check_out: date
    adults: int = Field(ge=1, le=30)
    children: int = Field(ge=0, le=10)
    children_ages: list[int] = Field(default_factory=list)
    rooms: int = Field(ge=1, le=10)
    room_type: str = Field(min_length=1, max_length=240)
    meal_plan: str | None = Field(default=None, max_length=160)
    breakfast: bool | None
    cancellation_required: bool | None
    currency: str = Field(min_length=3, max_length=3)
    booked_total_price: Decimal | None = Field(default=None, ge=0)

    @field_validator("hotel_url")
    @classmethod
    def booking_hotel_url_only(cls, value: str) -> str:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or not (
            hostname == "booking.com" or hostname.endswith(".booking.com")
        ):
            raise ValueError("hotel_url must be an HTTPS Booking.com URL")
        if "/hotel/" not in parsed.path:
            raise ValueError("hotel_url must identify a Booking hotel page")
        return urlunsplit(("https", parsed.netloc, parsed.path, "", ""))

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def validate_search(self) -> LiveBookingConfig:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if len(self.children_ages) not in {0, self.children}:
            raise ValueError("children_ages must be empty or contain one age per child")
        return self

    def navigation_url(self) -> str:
        return build_booking_search_url(self.hotel_url, self.reservation())

    def reservation(self) -> Reservation:
        return Reservation(
            property_name=self.property_name,
            booking_url=self.hotel_url,
            check_in=self.check_in,
            check_out=self.check_out,
            nights=(self.check_out - self.check_in).days,
            adults=self.adults,
            children=self.children,
            children_ages=self.children_ages or None,
            rooms_count=self.rooms,
            room_type=self.room_type,
            meal_plan=self.meal_plan,
            breakfast_included=self.breakfast,
            free_cancellation=self.cancellation_required,
            booked_total_price=self.booked_total_price,
            currency=self.currency,
            source_text="local live debug configuration",
            extraction_confidence=1,
        )


def load_live_config(path: Path) -> LiveBookingConfig:
    return LiveBookingConfig.model_validate_json(path.read_text(encoding="utf-8"))


def elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def capture_availability_html(page: object) -> str:
    """Capture one narrow known root, or all known legacy rows without interpreting them."""
    for selector in BookingSelectors.DEBUG_CAPTURE_ROOTS:
        locator = page.locator(selector)  # type: ignore[attr-defined]
        if locator.count():
            return str(locator.first.evaluate("element => element.outerHTML"))
    legacy_rates = page.locator(BookingSelectors.LEGACY_RATE)  # type: ignore[attr-defined]
    if legacy_rates.count():
        rows = legacy_rates.evaluate_all("elements => elements.map(element => element.outerHTML)")
        return "<table>" + "".join(str(row) for row in rows) + "</table>"
    raise RuntimeError("no narrow availability capture root was found")


def missing_offer_fields(offer: RateOffer, config: LiveBookingConfig) -> list[str]:
    missing: list[str] = []
    if offer.adults is None and not offer.occupancy_text:
        missing.append("occupancy")
    if config.breakfast is not None and offer.breakfast_included is None:
        missing.append("breakfast")
    if config.meal_plan and not offer.meal_plan:
        missing.append("meal_plan")
    if config.cancellation_required is not None and (
        offer.free_cancellation is None and offer.non_refundable is None
    ):
        missing.append("cancellation")
    return missing


def offer_diagnostic(
    index: int, offer: RateOffer, config: LiveBookingConfig, match: MatchResult
) -> dict[str, object]:
    evaluation = match.candidate_evaluations[index - 1]
    return {
        "index": index,
        "room": offer.room_name,
        "guests": offer.occupancy_text
        or {"adults": offer.adults, "children": offer.children},
        "meal_plan": offer.meal_plan,
        "breakfast": offer.breakfast_included,
        "cancellation": {
            "free": offer.free_cancellation,
            "non_refundable": offer.non_refundable,
        },
        "price": str(offer.current_price),
        "currency": offer.currency,
        "missing_required_fields": missing_offer_fields(offer, config),
        "match_classification": evaluation.classification.value,
        "accepted": evaluation.accepted,
    }


def analyze_html(
    html: str,
    config: LiveBookingConfig,
    *,
    source_url: str = "https://www.booking.com/hotel/debug/local.html",
) -> tuple[ParseResult, MatchResult, dict[str, object]]:
    timings: dict[str, int] = {}
    started = perf_counter()
    parsed = BookingRateParser().parse_html(html, source_url=source_url)
    timings["offer_collection_ms"] = elapsed_ms(started)

    started = perf_counter()
    match = ExactReservationMatcher().match(config.reservation(), parsed.offers)
    timings["exact_match_ms"] = elapsed_ms(started)

    if parsed.status in {ParseStatus.UNSUPPORTED_STRUCTURE, ParseStatus.ERROR} or (
        parsed.status is ParseStatus.PARTIAL and not parsed.offers
    ):
        reason = CheckReasonCode.PARSER_ERROR
        phase = CheckDiagnosticPhase.OFFER_COLLECTION
        outcome = "parser_error"
    elif not match.accepted:
        reason = CheckReasonCode.NO_COMPARABLE_OFFER
        phase = CheckDiagnosticPhase.EXACT_MATCH
        outcome = "no_comparable_offer"
    else:
        reason = None
        phase = CheckDiagnosticPhase.EXACT_MATCH
        outcome = "success"

    report: dict[str, object] = {
        "event": "booking_live_debug_result",
        "property": config.property_name,
        "outcome": outcome,
        "parser_status": parsed.status.value,
        "candidate_count": len(parsed.offers),
        "candidates": [
            offer_diagnostic(index, offer, config, match)
            for index, offer in enumerate(parsed.offers, start=1)
        ],
        "reason_code": reason.value if reason else None,
        "diagnostic_phase": phase.value,
        "timings": timings,
    }
    return parsed, match, report


class VolatileCheckHistory:
    """Dry-run sink satisfying production services without opening SQLite."""

    def __init__(self) -> None:
        self.record: PriceCheckRecord | None = None
        self.offers: list[RateOffer] = []
        self.schedule: ScheduleState | None = None

    def create(self, record: PriceCheckRecord, offers: list[RateOffer]) -> PriceCheckRecord:
        safe_detail = sanitize_error_detail(record.safe_error_detail or record.error)
        self.record = record.model_copy(
            update={
                "reason_code": record.reason_code or reason_code_for(record.status, record.error),
                "safe_error_detail": safe_detail,
                "error": safe_detail,
            }
        )
        self.offers = list(offers)
        return self.record

    def complete_with_schedule(
        self, record: PriceCheckRecord, state: ScheduleState
    ) -> PriceCheckRecord:
        self.record = record
        self.schedule = state
        return record


class _VolatileReservations:
    def __init__(self, reservation: Reservation) -> None:
        self.reservation = reservation

    def get(self, reservation_id):  # noqa: ANN001, ANN201
        return self.reservation if reservation_id == self.reservation.id else None


class _VolatileSchedules:
    def get(self, reservation_id):  # noqa: ANN001, ANN201, ARG002
        return None


class NullAlertService:
    """Explicit proof that dry-run CheckRunner cannot persist or deliver alerts."""

    def __init__(self) -> None:
        self.process_calls = 0

    def process(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.process_calls += 1


def run_production_check(browser: object, config: LiveBookingConfig) -> dict[str, object]:
    """Run the real CheckRunner pipeline with volatile persistence and no alert side effects."""
    reservation = config.reservation().model_copy(update={"active": True})
    history = VolatileCheckHistory()
    alerts = NullAlertService()
    runner = CheckRunner(
        _VolatileReservations(reservation),  # type: ignore[arg-type]
        PriceCheckService(
            browser,  # type: ignore[arg-type]
            BookingRateParser(),
            ExactReservationMatcher(),
            ComparablePriceService(),
            history,  # type: ignore[arg-type]
        ),
        _VolatileSchedules(),  # type: ignore[arg-type]
        SchedulePolicy(),
        alerts,  # type: ignore[arg-type]
        clock=lambda: datetime.now(UTC),
    )
    record = runner.run_check(reservation.id, CheckTrigger.MANUAL)
    if record is None:
        raise RuntimeError("production CheckRunner returned no record")
    comparison = record.comparison
    return {
        "record": record,
        "candidate_count": len(history.offers),
        "alerts_delivered": 0,
        "price_drop_eligible": AlertService._is_price_drop(record),
        "alert_process_calls": alerts.process_calls,
        "production_database_opened": False,
        "scheduler_repository_written": False,
        "volatile_next_check_at": history.schedule.next_check_at if history.schedule else None,
        "comparison": {
            "comparable": comparison.comparable,
            "direction": comparison.direction.value,
            "booked_price": str(comparison.booked_price)
            if comparison.booked_price is not None
            else None,
            "current_price": str(comparison.current_price)
            if comparison.current_price is not None
            else None,
            "currency": comparison.currency,
            "delta_amount": str(comparison.delta_amount)
            if comparison.delta_amount is not None
            else None,
            "delta_percent": str(comparison.delta_percent)
            if comparison.delta_percent is not None
            else None,
            "warnings": comparison.warnings,
        }
        if comparison
        else None,
    }
