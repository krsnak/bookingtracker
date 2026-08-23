"""Strict, reviewable reservation domain models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ReservationSource(StrEnum):
    PASTED_BOOKING_CONFIRMATION = "pasted_booking_confirmation"


class FieldConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class PriceBreakdown(BaseModel):
    """Amounts retained separately so Booking totals never get conflated."""

    total_price: Decimal | None = None
    payable_price: Decimal | None = None
    base_price: Decimal | None = None
    taxes_and_fees: Decimal | None = None
    vat: Decimal | None = None
    city_tax: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class RoomBreakdown(BaseModel):
    room_type: str
    count: int = Field(ge=1)


class ReservationDraft(BaseModel):
    """Common imported reservation fields; intentionally allows unknowns."""

    source: ReservationSource = ReservationSource.PASTED_BOOKING_CONFIRMATION
    property_name: str | None = None
    booking_url: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    nights: int | None = Field(default=None, ge=1)
    adults: int | None = Field(default=None, ge=1)
    children: int | None = Field(default=None, ge=0)
    children_ages: list[int] | None = None
    rooms_count: int | None = Field(default=None, ge=1)
    room_type: str | None = None
    rooms_breakdown: list[RoomBreakdown] | None = None
    meal_plan: str | None = None
    breakfast_included: bool | None = None
    cancellation_text: str | None = None
    free_cancellation: bool | None = None
    cancellation_deadline: datetime | None = None
    booked_total_price: Decimal | None = None
    booked_payable_price: Decimal | None = None
    booked_base_price: Decimal | None = None
    taxes_and_fees: Decimal | None = None
    vat: Decimal | None = None
    city_tax: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    payment_conditions: str | None = None
    price_drop_threshold_percent: Decimal | None = Field(default=None, gt=0, le=100)
    source_text: str = Field(min_length=1)
    extraction_confidence: float = Field(ge=0, le=1)
    field_confidence: dict[str, FieldConfidence] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @model_validator(mode="after")
    def validate_date_order(self) -> ReservationDraft:
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class ReservationCandidate(ReservationDraft):
    """Unpersisted parse result that must pass validation and user review."""

    missing_critical_fields: list[str] = Field(default_factory=list)
    ambiguous_fields: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)

    @property
    def can_activate(self) -> bool:
        return not self.missing_critical_fields and not self.validation_errors


class Reservation(ReservationDraft):
    """Reviewed, active-or-inactive reservation suitable for persistence."""

    id: UUID = Field(default_factory=uuid4)
    active: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def active_reservation_is_complete(self) -> Reservation:
        if self.active:
            from app.reservations.validator import validate_activation

            result = validate_activation(self)
            if not result.is_valid:
                raise ValueError("cannot activate reservation: " + "; ".join(result.errors))
        return self
