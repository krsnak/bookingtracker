from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.reservations.deterministic_parser import (
    parse_anchored_property_name,
    parse_cancellation,
    parse_dates_with_evidence,
    parse_occupancy,
    parse_property_name,
)
from app.reservations.extractor import ReservationExtractor
from app.reservations.import_document import ReservationImportDocument
from app.reservations.models import FieldConfidence, ImportDocumentSource

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
    assert candidate.children == 0
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

    assert candidate.property_name == "Zajazd Fakir"
    assert candidate.property_aliases == ["Fakir Inn"]
    assert candidate.booking_url == "https://www.booking.com/hotel/pl/zajazd-fakir.html"
    assert candidate.check_in == date(2026, 8, 25)
    assert candidate.check_out == date(2026, 8, 26)
    assert candidate.nights == 1
    assert candidate.room_type == "Standard Twin Room"
    assert candidate.adults == 2 and candidate.children == 0
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


def test_waiting_sentence_is_authoritative_property_anchor() -> None:
    assert parse_anchored_property_name(
        ["Nikdy vás nepožádáme o platbu.", "Ubytování Grand Hotel Hønefoss", "vás bude očekávat"]
    ) == "Grand Hotel Hønefoss"


def test_conflicting_waiting_sentence_anchors_require_review() -> None:
    assert parse_anchored_property_name(
        [
            "Ubytování Safe Hotel vás bude očekávat",
            "Accommodation Other Hotel will be waiting for you",
        ]
    ) is None

    candidate = ReservationExtractor().extract(
        "Ubytování Safe Hotel vás bude očekávat\n"
        "Accommodation Other Hotel will be waiting for you\n"
        "2026-08-26\n2026-08-27\n2 adults\n1 night, Twin Room\nTotal price NOK 100"
    )
    assert candidate.property_name is None
    assert "property_name" in candidate.missing_critical_fields


def test_security_paragraph_is_never_a_property_name() -> None:
    candidate = ReservationExtractor().extract(
        "Nikdy vás kvůli platbě nebudeme žádat o údaje.\n"
        "2026-08-26\n2026-08-27\n2 adults\n1 night, Twin Room\nTotal price NOK 100"
    )
    assert candidate.property_name is None


def test_confirmation_anchor_beats_payment_cards_and_generic_fallbacks() -> None:
    lines = [
        "American Express, Visa, Euro/Mastercard, Diners Club, JCB, Maestro",
        "Your booking is confirmed at Riad Dar Sirine & Palmyra 12.",
        "Payment methods",
    ]

    assert parse_anchored_property_name(lines) == "Riad Dar Sirine & Palmyra"
    assert parse_property_name(lines) == "Riad Dar Sirine & Palmyra"
    assert parse_property_name([lines[0], "Payment methods"]) is None


def test_confirmation_anchor_keeps_guest_house_but_rejects_section_headings() -> None:
    assert parse_anchored_property_name(
        [
            "Your booking is confirmed at Sample Guest House at Market Square 12."
        ]
    ) == "Sample Guest House at Market Square"
    assert parse_property_name(["Hotel: Payment methods"]) is None
    assert parse_property_name(["Accommodation: Cancellation policy"]) is None


def test_property_section_headings_are_whole_normalized_values_not_keywords() -> None:
    assert parse_property_name(["Booking property: Sunrise Guest House"]) == "Sunrise Guest House"
    assert (
        parse_property_name(["Booking property: Guest House Dar Example"])
        == "Guest House Dar Example"
    )
    assert parse_property_name(["Booking property: Riad Example Hotel"]) == "Riad Example Hotel"
    assert parse_property_name(["Booking property: Guest details:"]) is None
    assert parse_property_name(["Booking property: Cancellation policy"]) is None
    assert parse_property_name(["Booking property: Booking details"]) is None
    assert parse_property_name(["Booking property: Price information"]) is None


def test_wrapped_confirmation_anchor_never_absorbs_next_section_or_address() -> None:
    assert parse_anchored_property_name(
        [
            "Your booking is confirmed at Sample Guest House at",
            "Market Square 12.",
            "Payment methods",
        ]
    ) == "Sample Guest House at Market Square"
    assert parse_anchored_property_name(
        ["Your booking is confirmed at Sunrise Guest House", "Payment methods"]
    ) == "Sunrise Guest House"
    assert parse_anchored_property_name(
        ["Your booking is confirmed at Sample Guest House at", "Address: 15 Example Street"]
    ) is None


def test_cancellation_policy_parses_day_first_deadline() -> None:
    text, free, deadline = parse_cancellation(
        [
            "Cancellation policy",
            "Free cancellation until 13 September 2026 at 23:59",
            "Payment information",
        ]
    )

    assert text is not None and free is True
    assert deadline == datetime(2026, 9, 13, 23, 59)


def test_day_first_english_stay_dates_exclude_payment_cancellation_and_issue_dates() -> None:
    check_in, check_out, warnings, errors = parse_dates_with_evidence(
        [
            "Payment issued: 2 September 2026",
            "Free cancellation until 3 September 2026",
            "Arrival: 11 September 2026",
            "Departure: 12 September 2026",
        ]
    )

    assert (check_in, check_out) == (date(2026, 9, 11), date(2026, 9, 12))
    assert warnings == [] and errors == []


def test_unlabelled_fallback_never_uses_payment_or_cancellation_dates() -> None:
    check_in, check_out, _warnings, errors = parse_dates_with_evidence(
        [
            "Payment issued: 2 September 2026",
            "Free cancellation until 3 September 2026",
            "11 September 2026",
            "12 September 2026",
        ]
    )

    assert (check_in, check_out) == (date(2026, 9, 11), date(2026, 9, 12))
    assert errors == []


def test_conflicting_explicit_stay_dates_require_manual_review() -> None:
    check_in, check_out, _warnings, errors = parse_dates_with_evidence(
        [
            "Arrival: 11 September 2026",
            "Arrival: 12 September 2026",
            "Departure: 13 September 2026",
        ]
    )

    assert check_in is None and check_out is None
    assert errors


def test_occupancy_only_adults_means_zero_children_in_czech_and_english() -> None:
    assert parse_occupancy(["Rezervace pro: 2 dospělí"]) == (2, 0, None)
    assert parse_occupancy(["Reservations for: 2 adults"]) == (2, 0, None)


def test_occupancy_preserves_explicit_children_and_known_ages() -> None:
    assert parse_occupancy(["2 dospělí, 1 dítě", "Věk dítěte: 7"]) == (2, 1, [7])
    assert parse_occupancy(["2 adults, 1 child"]) == (2, 1, None)


def test_occupancy_conflicts_or_missing_guest_block_remain_unknown() -> None:
    assert parse_occupancy(["2 adults", "3 adults"]) == (None, None, None)
    assert parse_occupancy(["Property: Safe Hotel", "No children mentioned"]) == (None, None, None)


def test_papaya_sanitized_import_sets_zero_children() -> None:
    candidate = extract_fixture("papaya_confirmation.txt")
    assert (candidate.adults, candidate.children, candidate.rooms_count) == (2, 0, 1)


def test_text_and_pdf_documents_share_adults_only_occupancy_result() -> None:
    text = "Property: Safe Hotel\n2 adults\n1 night, Twin Room\nTotal price EUR 100"
    extractor = ReservationExtractor()
    text_candidate = extractor.extract_document(
        ReservationImportDocument(text=text, source=ImportDocumentSource.TEXT)
    )
    pdf_candidate = extractor.extract_document(
        ReservationImportDocument(text=text, source=ImportDocumentSource.PDF)
    )
    assert (text_candidate.adults, text_candidate.children) == (2, 0)
    assert (pdf_candidate.adults, pdf_candidate.children) == (2, 0)


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
    assert candidate.children == 0
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
