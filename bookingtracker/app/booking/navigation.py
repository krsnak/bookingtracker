"""Deterministic Booking availability-search URLs built from stored reservation facts."""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit

from app.reservations.import_document import canonical_booking_hotel_url
from app.reservations.models import Reservation


class BookingSearchUrlError(ValueError):
    """The stored reservation cannot safely identify one availability search."""


def build_booking_search_url(canonical_url: str, reservation: Reservation) -> str:
    """Build one exact search without retaining old query, fragment, or tracking data."""
    canonical = canonical_booking_hotel_url(canonical_url)
    if canonical is None:
        raise BookingSearchUrlError("stored Booking hotel URL is not canonical")

    required = {
        "check_in": reservation.check_in,
        "check_out": reservation.check_out,
        "adults": reservation.adults,
        "children": reservation.children,
        "rooms_count": reservation.rooms_count,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise BookingSearchUrlError(
            "reservation search facts are incomplete: " + ", ".join(sorted(missing))
        )

    query: list[tuple[str, str]] = [
        ("checkin", reservation.check_in.isoformat()),  # type: ignore[union-attr]
        ("checkout", reservation.check_out.isoformat()),  # type: ignore[union-attr]
        ("group_adults", str(reservation.adults)),
        ("group_children", str(reservation.children)),
        ("no_rooms", str(reservation.rooms_count)),
    ]
    if reservation.children and reservation.children_ages:
        if len(reservation.children_ages) == reservation.children:
            query.extend(("age", str(age)) for age in reservation.children_ages)
    if reservation.currency:
        query.append(("selected_currency", reservation.currency.upper()))

    parsed = urlsplit(canonical)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
