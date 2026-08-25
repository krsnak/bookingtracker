"""Safe local live/replay laboratory using the production parser and matcher."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from time import perf_counter
from urllib.parse import urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.booking.models import ParseResult, ParseStatus, RateOffer
from app.matching.matcher import ExactReservationMatcher
from app.matching.models import MatchResult
from app.pricing.models import CheckDiagnosticPhase, CheckReasonCode
from app.reservations.models import Reservation


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
        query: list[tuple[str, str]] = [
            ("checkin", self.check_in.isoformat()),
            ("checkout", self.check_out.isoformat()),
            ("group_adults", str(self.adults)),
            ("group_children", str(self.children)),
            ("no_rooms", str(self.rooms)),
            ("selected_currency", self.currency),
        ]
        query.extend(("age", str(age)) for age in self.children_ages)
        parsed = urlsplit(self.hotel_url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))

    def reservation(self) -> Reservation:
        return Reservation(
            property_name=self.property_name,
            booking_url=self.navigation_url(),
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
            currency=self.currency,
            source_text="local live debug configuration",
            extraction_confidence=1,
        )


def load_live_config(path: Path) -> LiveBookingConfig:
    return LiveBookingConfig.model_validate_json(path.read_text(encoding="utf-8"))


def elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


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
    from app.booking.parser import BookingRateParser

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
