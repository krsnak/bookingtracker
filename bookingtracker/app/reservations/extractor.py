"""Application service that turns pasted confirmation text into a candidate."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.reservations.deterministic_parser import (
    clean_lines,
    has_conflicting_anchored_properties,
    parse_anchored_property_name,
    parse_booking_url,
    parse_cancellation,
    parse_dates_with_evidence,
    parse_meal_facts,
    parse_nights,
    parse_occupancy,
    parse_payment_conditions,
    parse_prices,
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


@dataclass(frozen=True)
class _PropertyIdentity:
    property_name: str | None
    booking_url: str | None
    name_source: str | None
    url_source: str | None
    conflict: bool = False


def _identity_tokens(value: str) -> set[str]:
    replacements = str.maketrans(
        {
            "ø": "o",
            "ł": "l",
            "đ": "d",
            "ð": "d",
            "þ": "th",
            "æ": "ae",
            "œ": "oe",
            "ß": "ss",
        }
    )
    folded = (
        unicodedata.normalize("NFKD", value.casefold())
        .translate(replacements)
        .encode("ascii", "ignore")
        .decode()
    )
    return {token for token in re.split(r"[^a-z0-9]+", folded) if token}


def _normalized_name(value: str) -> str:
    return "".join(sorted(_identity_tokens(value)))


def _name_supports_hotel_url(name: str, hotel_url: str) -> bool:
    path = urlsplit(hotel_url).path
    slug = path.rsplit("/", 1)[-1].removesuffix(".html")
    slug_tokens = _identity_tokens(slug)
    name_tokens = _identity_tokens(name)
    return bool(slug_tokens) and slug_tokens <= name_tokens


def _pdf_property_identity(
    document: ReservationImportDocument, anchored_property: str | None, anchored_conflict: bool
) -> _PropertyIdentity:
    unique_urls = list(dict.fromkeys(link.canonical_url for link in document.hotel_links))
    if len(unique_urls) > 1:
        return _PropertyIdentity(None, None, None, None, conflict=True)
    if not unique_urls:
        return _PropertyIdentity(
            anchored_property if not anchored_conflict else None,
            parse_booking_url(clean_lines(document.text)) if not anchored_conflict else None,
            "confirmation_anchor" if anchored_property and not anchored_conflict else None,
            "text_canonical_url" if anchored_property and not anchored_conflict else None,
            conflict=anchored_conflict,
        )

    hotel_url = unique_urls[0]
    linked_names = list(
        dict.fromkeys(
            link.visible_text.strip()
            for link in document.hotel_links
            if (
                link.canonical_url == hotel_url
                and link.visible_text
                and _name_supports_hotel_url(link.visible_text, hotel_url)
            )
        )
    )
    if len({_normalized_name(name) for name in linked_names}) > 1:
        return _PropertyIdentity(None, None, None, None, conflict=True)
    linked_name = linked_names[0] if linked_names else None
    if anchored_conflict:
        return _PropertyIdentity(None, None, None, None, conflict=True)
    if linked_name:
        if (
            anchored_property
            and _normalized_name(linked_name) != _normalized_name(anchored_property)
        ):
            return _PropertyIdentity(None, None, None, None, conflict=True)
        return _PropertyIdentity(linked_name, hotel_url, "pdf_hotel_link_text", "pdf_hotel_link")
    if anchored_property and _name_supports_hotel_url(anchored_property, hotel_url):
        return _PropertyIdentity(
            anchored_property, hotel_url, "confirmation_anchor", "pdf_hotel_link"
        )
    title_property = (
        parse_anchored_property_name(clean_lines(document.document_title))
        if document.document_title
        else None
    )
    if title_property and _name_supports_hotel_url(title_property, hotel_url):
        return _PropertyIdentity(title_property, hotel_url, "document_title", "pdf_hotel_link")
    return _PropertyIdentity(None, hotel_url, None, "pdf_hotel_link")


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
        anchored_property = parse_anchored_property_name(lines)
        identity = _pdf_property_identity(
            document, anchored_property, has_conflicting_anchored_properties(lines)
        )
        field_values = {
            "property_name": identity.property_name,
            "booking_url": identity.booking_url,
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
            property_aliases=[],
            property_name_evidence=identity.name_source,
            booking_url_evidence=identity.url_source,
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
            warnings=(
                warnings
                + date_warnings
                + document.warnings
                + (
                    ["Konfliktní evidence ubytování vyžaduje ruční kontrolu."]
                    if identity.conflict
                    else []
                )
            ),
            ambiguous_fields=ambiguous,
        )
        validation = validate_activation(validation_seed)
        return validation_seed.model_copy(
            update={
                "missing_critical_fields": validation.missing_fields,
                "validation_errors": validation.errors + date_validation_errors,
            }
        )
