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


def test_extracts_english_confirmation_without_treating_destination_as_property() -> None:
    candidate = extract_fixture("booking_english_confirmation.txt")

    assert candidate.property_name == "Fakir Inn"
    assert candidate.property_aliases == ["Zajazd Fakir"]
    assert candidate.booking_url == "https://www.booking.com/hotel/pl/zajazd-fakir.html"
    assert candidate.check_in == date(2026, 8, 25)
    assert candidate.check_out == date(2026, 8, 26)
    assert candidate.nights == 1
    assert candidate.room_type == "Standard Twin Room"
    assert candidate.adults == 2 and candidate.children is None
    assert candidate.breakfast_included is False and candidate.meal_plan == "No meals included"
    assert candidate.cancellation_deadline == datetime(2026, 8, 25, 14)
    assert candidate.booked_base_price == Decimal("194.44")
    assert candidate.vat == Decimal("15.56") and candidate.city_tax == Decimal("10.00")
    assert candidate.booked_total_price == Decimal("220.00")
    assert candidate.booked_payable_price == Decimal("220.00")
    assert candidate.payment_conditions == "Booking automatically charges card"


def test_extracts_sahara_sands_pdf_layout_regression_without_promoting_cta() -> None:
    candidate = extract_fixture("booking_sahara_sands_synthetic.txt")

    assert candidate.property_name == "Sahara Sands Hotel"
    assert "Zjistit více" not in candidate.property_aliases
    assert candidate.booking_url == "https://www.booking.com/hotel/ma/diamant-sahara-camp.html"
    assert candidate.check_in == date(2026, 9, 12)
    assert candidate.check_out == date(2026, 9, 14)
    assert candidate.nights == 2
    assert candidate.free_cancellation is True
    assert candidate.cancellation_deadline == datetime(2026, 9, 6, 23, 59)
    assert "29.84 EUR" in (candidate.cancellation_text or "")
    assert candidate.payment_conditions == "Automatická budoucí platba kartou přes Booking.com"
    assert candidate.booked_base_price == Decimal("57.94")
    assert candidate.city_tax == Decimal("1.74")
    assert candidate.taxes_and_fees == Decimal("1.74")
    assert candidate.vat is None
    assert candidate.booked_total_price == Decimal("59.68")
    assert candidate.booked_payable_price == Decimal("59.68")
    assert candidate.currency == "EUR"


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


def test_extracts_czech_markdown_tables_without_cross_section_price_confusion() -> None:
    candidate = extract_fixture("booking_czech_markdown_confirmation.txt")

    assert candidate.property_name == "Papaya Hostel"
    assert (
        candidate.booking_url == "https://www.booking.com/hotel/ma/moroccan-friends-guesthouse.html"
    )
    assert candidate.check_in == date(2026, 9, 5)
    assert candidate.check_out == date(2026, 9, 6)
    assert candidate.nights == 1
    assert candidate.adults == 2
    assert candidate.children is None  # Unspecified children remain unknown, never assumed absent.
    assert candidate.room_type == "Třílůžkový pokoj s balkonem"
    assert candidate.breakfast_included is True
    assert candidate.meal_plan == "Breakfast included"
    assert candidate.booked_base_price == Decimal("19.13")
    assert candidate.vat == Decimal("3.83")
    assert candidate.city_tax == Decimal("2")
    assert candidate.taxes_and_fees == Decimal("5.83")
    assert candidate.booked_total_price == Decimal("24.95")
    assert candidate.booked_payable_price == Decimal("22.95")
    assert candidate.currency == "EUR"
    assert candidate.free_cancellation is True
    assert candidate.cancellation_deadline == datetime(2026, 9, 3, 23, 59)
    assert candidate.payment_conditions == "Automatická budoucí platba kartou přes Booking.com"
    assert any("rounding" in warning for warning in candidate.warnings)
    assert candidate.can_activate


def test_extracts_full_czech_gmail_markdown_without_persisting_mail_metadata() -> None:
    candidate = extract_fixture("booking_czech_full_gmail_markdown_confirmation.txt")

    assert candidate.property_name == "Papaya Hostel"
    assert candidate.booking_url == (
        "https://www.booking.com/hotel/ma/moroccan-friends-guesthouse.html"
    )
    assert candidate.check_in == date(2026, 9, 5)
    assert candidate.check_out == date(2026, 9, 6)
    assert candidate.nights == 1
    assert candidate.room_type == "Třílůžkový pokoj s balkonem"
    assert candidate.adults == 2
    assert candidate.breakfast_included is True
    assert candidate.booked_base_price == Decimal("19.13")
    assert candidate.vat == Decimal("3.83")
    assert candidate.city_tax == Decimal("2")
    assert candidate.taxes_and_fees == Decimal("5.83")
    assert candidate.booked_total_price == Decimal("24.95")
    assert candidate.booked_payable_price == Decimal("22.95")
    assert candidate.free_cancellation is True
    assert candidate.cancellation_deadline == datetime(2026, 9, 3, 23, 59)
    assert candidate.payment_conditions == "Automatická budoucí platba kartou přes Booking.com"
    assert candidate.can_activate
    assert "mail.example.invalid" not in candidate.source_text
    assert "secure.booking.com" not in candidate.source_text
    assert "synthetic=value" not in candidate.source_text
    assert "https://booking.com/" not in candidate.source_text
    assert (
        "https://www.booking.com/hotel/ma/moroccan-friends-guesthouse.html" in candidate.source_text
    )
    assert (
        "Není vybrána žádná položka"
        in (FIXTURES / "booking_czech_full_gmail_markdown_confirmation.txt").read_text()
    )


def test_rejects_reversed_or_equal_unlabelled_dates_without_raising_validation_error() -> None:
    for check_in, check_out in (("2026-09-06", "2026-09-05"), ("2026-09-05", "2026-09-05")):
        candidate = ReservationExtractor().extract(
            "Property: Safe Hotel\n"
            f"{check_in}\n{check_out}\n2 adults\nEconomy Triple Room\nTotal price EUR 18.88\n"
            "PIN: synthetic-pin"
        )

        assert candidate.check_in is None
        assert candidate.check_out is None
        assert not candidate.can_activate
        assert candidate.validation_errors == [
            "Nepodařilo se spolehlivě určit datum příjezdu a odjezdu. "
            "Zkontrolujte vložené potvrzení."
        ]
        assert "synthetic-pin" not in candidate.source_text


def test_keeps_only_safe_partial_explicit_stay_date() -> None:
    check_in_only = ReservationExtractor().extract(
        "Property: Safe Hotel\nPříjezd: 5. září 2026\n2 dospělí\n1 noc, Economy Triple Room\n"
        "Celková cena: EUR 18.88"
    )
    check_out_only = ReservationExtractor().extract(
        "Property: Safe Hotel\nOdjezd: 6. září 2026\n2 dospělí\n1 noc, Economy Triple Room\n"
        "Celková cena: EUR 18.88"
    )

    assert check_in_only.check_in == date(2026, 9, 5)
    assert check_in_only.check_out is None
    assert check_out_only.check_in is None
    assert check_out_only.check_out == date(2026, 9, 6)
    assert not check_in_only.can_activate
    assert not check_out_only.can_activate


def test_explicit_dates_win_over_conflicting_teaser_date() -> None:
    candidate = ReservationExtractor().extract(
        "Property: Safe Hotel\nUbytování Safe Hotel vás bude očekávat 4. září 2026.\n"
        "Příjezd: 5. září 2026\nOdjezd: 6. září 2026\n2 dospělí\n"
        "1 noc, Economy Triple Room\nCelková cena: EUR 18.88"
    )

    assert candidate.check_in == date(2026, 9, 5)
    assert candidate.check_out == date(2026, 9, 6)
    assert any("Explicitní data pobytu" in warning for warning in candidate.warnings)
