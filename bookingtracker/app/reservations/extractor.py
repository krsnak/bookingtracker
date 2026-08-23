"""Application service that turns pasted confirmation text into a candidate."""

from __future__ import annotations

from app.reservations.deterministic_parser import (
    clean_lines,
    parse_booking_url,
    parse_cancellation,
    parse_dates_with_evidence,
    parse_meal_facts,
    parse_nights,
    parse_occupancy,
    parse_payment_conditions,
    parse_prices,
    parse_property_aliases,
    parse_property_name,
    parse_room,
    sanitize_source_text,
)
from app.reservations.import_document import ReservationImportDocument, text_document
from app.reservations.models import (
    FieldConfidence,
    ImportDocumentSource,
    ReservationCandidate,
    ReservationSource,
)
from app.reservations.validator import validate_activation


class ReservationExtractor:
    """Deterministic Phase 1 extractor; semantic providers remain optional."""

    def extract(self, source_text: str) -> ReservationCandidate:
        return self.extract_document(text_document(source_text))

    def extract_document(self, document: ReservationImportDocument) -> ReservationCandidate:
        """Extract text and PDF through exactly the same deterministic path."""
        lines = clean_lines(document.text)
        check_in, check_out, date_warnings, date_validation_errors = parse_dates_with_evidence(
            lines
        )
        # Keep Pydantic's date invariant as a final guard, but never allow
        # parser evidence to turn an expected import error into an HTTP 500.
        if check_in and check_out and check_out <= check_in:
            check_in = None
            check_out = None
            date_warnings.append("Nekonzistentní data pobytu byla odmítnuta.")
            date_validation_errors.append(
                "Nepodařilo se spolehlivě určit datum příjezdu a odjezdu. "
                "Zkontrolujte vložené potvrzení."
            )
        adults, children, children_ages = parse_occupancy(lines)
        rooms_count, room_type, rooms_breakdown = parse_room(lines)
        price, warnings, ambiguous = parse_prices(lines)
        cancellation_text, free_cancellation, cancellation_deadline = parse_cancellation(lines)
        meal_plan, breakfast_included = parse_meal_facts(lines)
        property_name = parse_property_name(lines)
        field_values = {
            "property_name": property_name,
            "booking_url": (document.uris[0] if document.uris else parse_booking_url(lines)),
            "check_in": check_in,
            "check_out": check_out,
            "adults": adults,
            "rooms_count": rooms_count,
            "room_type": room_type,
            "booked_total_price": price.total_price,
            "currency": price.currency,
        }
        field_confidence = {
            name: FieldConfidence.HIGH if value is not None else FieldConfidence.UNKNOWN
            for name, value in field_values.items()
        }
        confidence = sum(value is not None for value in field_values.values()) / len(field_values)
        validation_seed = ReservationCandidate(
            **field_values,
            source=(
                ReservationSource.BOOKING_CONFIRMATION_PDF
                if document.source is ImportDocumentSource.PDF
                else ReservationSource.PASTED_BOOKING_CONFIRMATION
            ),
            property_aliases=parse_property_aliases(lines, property_name),
            nights=parse_nights(lines),
            children=children,
            children_ages=children_ages,
            rooms_breakdown=rooms_breakdown,
            meal_plan=meal_plan,
            breakfast_included=breakfast_included,
            cancellation_text=cancellation_text,
            free_cancellation=free_cancellation,
            cancellation_deadline=cancellation_deadline,
            booked_payable_price=price.payable_price,
            booked_base_price=price.base_price,
            taxes_and_fees=price.taxes_and_fees,
            vat=price.vat,
            city_tax=price.city_tax,
            payment_conditions=parse_payment_conditions(lines),
            source_text=sanitize_source_text(document.text),
            extraction_confidence=confidence,
            field_confidence=field_confidence,
            warnings=warnings + date_warnings + document.warnings,
            ambiguous_fields=ambiguous,
        )
        validation = validate_activation(validation_seed)
        return validation_seed.model_copy(
            update={
                "missing_critical_fields": validation.missing_fields,
                "validation_errors": validation.errors + date_validation_errors,
            }
        )
