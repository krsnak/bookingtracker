from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.booking.models import ParseStatus
from app.booking.normalization import parse_price
from app.booking.parser import BookingRateParser
from app.matching.matcher import ExactReservationMatcher
from test_exact_reservation_matcher import reservation

FIXTURES = Path(__file__).parents[1] / "fixtures"
PARSER = BookingRateParser()


def parse_fixture(name: str):
    return PARSER.parse_html((FIXTURES / name).read_text(), source_url="https://example.test/hotel")


def test_papaya_room_yields_two_distinct_rate_offers() -> None:
    result = parse_fixture("booking_papaya_rates.html")

    assert result.status is ParseStatus.SUCCESS
    assert result.rooms_detected == 1
    assert len(result.offers) == 2
    first, second = result.offers
    assert first.room_name == second.room_name == "Economy Triple Room"
    assert first.current_price == Decimal("16.88")
    assert first.original_price == Decimal("18.88")
    assert first.current_price < first.original_price
    assert first.currency == "EUR"
    assert first.genius is True
    assert first.genius_discount_percent == 11
    assert first.breakfast_included is True
    assert first.breakfast_genius_benefit is True
    assert first.free_cancellation is True
    assert first.non_refundable is False
    assert first.cancellation_deadline == datetime(2026, 9, 4)
    assert first.payment_conditions == "No payment until 2 September 2026"
    assert first.taxes_included is True
    assert second.current_price == Decimal("19.50")
    assert second.original_price is None
    assert second.breakfast_included is True
    assert second.breakfast_genius_benefit is False
    assert second.non_refundable is True
    assert second.free_cancellation is False


def test_multiple_czech_rooms_and_optional_unknowns_are_preserved() -> None:
    result = parse_fixture("booking_hoenefoss_rates.html")

    assert result.status is ParseStatus.SUCCESS
    assert result.rooms_detected == 3
    assert len(result.offers) == 3
    first, family, balcony = result.offers
    assert first.room_name == "Dvoulůžkový pokoj s manželskou postelí"
    assert first.current_price == Decimal("3534")
    assert first.currency == "CZK"
    assert first.breakfast_included is True
    assert first.breakfast_genius_benefit is False
    assert first.free_cancellation is True
    assert first.cancellation_deadline == datetime(2026, 9, 4)
    assert family.genius is None
    assert family.breakfast_included is None
    assert family.cancellation_text is None
    assert balcony.non_refundable is True
    assert balcony.payment_conditions == "Platba předem"


def test_localized_price_formats_are_decimal_safe() -> None:
    assert parse_price("3 534 Kč") == (Decimal("3534"), "CZK")
    assert parse_price("490 Kč") == (Decimal("490"), "CZK")
    assert parse_price("€ 18.88") == (Decimal("18.88"), "EUR")
    assert parse_price("NOK 1,400") == (Decimal("1400"), "NOK")


def test_no_availability_is_not_unsupported_structure() -> None:
    result = parse_fixture("booking_no_availability.html")

    assert result.status is ParseStatus.NO_AVAILABILITY
    assert result.offers == []


def test_rate_without_reliable_current_price_is_partial_not_an_offer() -> None:
    result = parse_fixture("booking_partial_rates.html")

    assert result.status is ParseStatus.PARTIAL
    assert result.offers == []
    assert "no current-price selector" in result.warnings[0]


def test_unknown_page_structure_is_explicit() -> None:
    result = PARSER.parse_html("<main>Booking page</main>", source_url="https://example.test")

    assert result.status is ParseStatus.UNSUPPORTED_STRUCTURE
    assert result.offers == []


def test_legacy_booking_rate_row_fallback_is_scoped_and_explicit() -> None:
    result = parse_fixture("booking_legacy_rate_rows.html")

    assert result.status is ParseStatus.SUCCESS
    assert result.rooms_detected == 1
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.room_name == "Dvoulůžkový pokoj s manželskou postelí"
    assert offer.current_price == Decimal("3543")
    assert offer.currency == "CZK"
    assert offer.breakfast_included is True
    assert offer.free_cancellation is True
    assert offer.taxes_included is True
    assert offer.evidence["rate_selector"] == "tr.js-rt-block-row"


def test_storhaugen_missing_optional_evidence_keeps_parsing_later_exact_offer() -> None:
    result = parse_fixture("booking_storhaugen_optional_missing.html")

    assert result.status is ParseStatus.PARTIAL
    assert len(result.offers) == 1
    assert result.offers[0].room_name == "Standard Double Room"
    assert result.offers[0].meal_plan is None
    assert result.offers[0].payment_conditions is None
    assert result.offers[0].genius is None
    matched = ExactReservationMatcher().match(
        reservation(
            property_name="STORHAUGEN GARD",
            room_type="Standard Double Room",
            booked_total_price=Decimal("1500"),
            currency="NOK",
        ),
        result.offers,
    )
    assert matched.accepted
    assert matched.matched_rate == result.offers[0]
