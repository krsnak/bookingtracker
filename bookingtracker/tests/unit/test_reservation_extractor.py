from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.reservations.extractor import ReservationExtractor
from app.reservations.models import FieldConfidence

FIXTURES = Path(__file__).parents[1] / "fixtures"


def extract_fixture(name: str):
    return ReservationExtractor().extract((FIXTURES / name).read_text())


def test_extracts_papaya_confirmation_and_preserves_price_concepts() -> None:
    candidate = extract_fixture("papaya_confirmation.txt")

    assert candidate.property_name == "Papaya Hostel"
    assert candidate.check_in == date(2026, 9, 18)
    assert candidate.check_out == date(2026, 9, 19)
    assert candidate.nights == 1
    assert candidate.adults == 2
    assert candidate.children is None
    assert candidate.rooms_count == 1
    assert candidate.room_type == "Economy Triple Room"
    assert candidate.booked_base_price == Decimal("14.07")
    assert candidate.booked_total_price == Decimal("18.88")
    assert candidate.booked_payable_price == Decimal("16.88")
    assert candidate.vat == Decimal("2.81")
    assert candidate.city_tax == Decimal("2.00")
    assert candidate.taxes_and_fees == Decimal("4.81")
    assert candidate.currency == "EUR"
    assert candidate.free_cancellation is True
    assert candidate.cancellation_deadline == datetime(2026, 9, 16, 23, 59)
    assert candidate.meal_plan is None
    assert candidate.breakfast_included is None
    assert candidate.can_activate
    assert candidate.source_text == (FIXTURES / "papaya_confirmation.txt").read_text()


def test_extracts_format_variation_and_leaves_unknowns_null() -> None:
    candidate = extract_fixture("hoenefoss_confirmation.txt")

    assert candidate.property_name == "Grand Hotel Hønefoss"
    assert candidate.check_in == date(2026, 8, 26)
    assert candidate.check_out == date(2026, 8, 27)
    assert candidate.adults == 2
    assert candidate.children == 1
    assert candidate.children_ages == [7]
    assert candidate.rooms_count == 1
    assert candidate.room_type == "Budget Double Room with Double Bed"
    assert candidate.booked_base_price == Decimal("1250.00")
    assert candidate.booked_total_price == Decimal("1400.00")
    assert candidate.booked_payable_price == Decimal("1400.00")
    assert candidate.taxes_and_fees == Decimal("150.00")
    assert candidate.currency == "NOK"
    assert candidate.meal_plan is None
    assert candidate.breakfast_included is None
    assert candidate.free_cancellation is None
    assert candidate.cancellation_text is None
    assert candidate.can_activate


def test_flags_competing_totals_and_does_not_block_reviewable_critical_data() -> None:
    candidate = extract_fixture("ambiguous_confirmation.txt")

    assert candidate.booked_total_price == Decimal("100.00")
    assert "booked_total_price" in candidate.ambiguous_fields
    assert any("conflicting total" in warning for warning in candidate.warnings)
    assert candidate.can_activate


def test_missing_critical_fields_block_activation() -> None:
    candidate = ReservationExtractor().extract("Booking information\n1 adult\nTotal price €20")

    assert set(candidate.missing_critical_fields) >= {
        "property_name",
        "check_in",
        "check_out",
        "rooms_count",
        "room_type",
    }
    assert not candidate.can_activate
    assert candidate.field_confidence["property_name"] is FieldConfidence.UNKNOWN
