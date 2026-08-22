from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.reservations.models import Reservation
from app.reservations.validator import validate_activation
from pydantic import ValidationError


def complete_fields() -> dict[str, object]:
    return {
        "property_name": "Example Hotel",
        "check_in": date(2026, 10, 1),
        "check_out": date(2026, 10, 2),
        "nights": 1,
        "adults": 2,
        "rooms_count": 1,
        "room_type": "Double Room",
        "booked_total_price": Decimal("100"),
        "currency": "eur",
        "source_text": "sanitized confirmation",
        "extraction_confidence": 1,
    }


def test_active_reservation_requires_critical_fields() -> None:
    fields = complete_fields()
    fields.pop("room_type")

    with pytest.raises(ValidationError, match="cannot activate reservation"):
        Reservation(active=True, **fields)


def test_currency_is_normalized_and_nights_are_checked() -> None:
    reservation = Reservation(active=True, **complete_fields())

    assert reservation.currency == "EUR"
    assert validate_activation(reservation).is_valid

    invalid = reservation.model_copy(update={"nights": 2})
    assert (
        "nights does not match the check-in/check-out dates" in validate_activation(invalid).errors
    )
