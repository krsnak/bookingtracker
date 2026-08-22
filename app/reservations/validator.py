"""Validation that blocks activation without changing extracted facts."""

from __future__ import annotations

from dataclasses import dataclass

from app.reservations.models import ReservationDraft

CRITICAL_FIELDS = (
    "property_name",
    "check_in",
    "check_out",
    "adults",
    "rooms_count",
    "room_type",
    "booked_total_price",
    "currency",
)


@dataclass(frozen=True)
class ValidationResult:
    missing_fields: list[str]
    errors: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.missing_fields and not self.errors


def validate_activation(reservation: ReservationDraft) -> ValidationResult:
    missing = [field for field in CRITICAL_FIELDS if getattr(reservation, field) is None]
    errors: list[str] = []
    if (
        reservation.check_in
        and reservation.check_out
        and reservation.check_out <= reservation.check_in
    ):
        errors.append("check-out must be after check-in")
    if reservation.nights and reservation.check_in and reservation.check_out:
        actual_nights = (reservation.check_out - reservation.check_in).days
        if actual_nights != reservation.nights:
            errors.append("nights does not match the check-in/check-out dates")
    return ValidationResult(missing_fields=missing, errors=errors)
