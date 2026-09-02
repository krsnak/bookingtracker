"""Typed output of the Booking availability-page parser."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class ParseStatus(StrEnum):
    SUCCESS = "success"
    NO_AVAILABILITY = "no_availability"
    UNSUPPORTED_STRUCTURE = "unsupported_structure"
    PARTIAL = "partial"
    ERROR = "error"


class RoomFacts(BaseModel):
    """Only explicit, objective room facts; ``None`` means no evidence."""

    accommodation_kind: str | None = None  # ``private_room`` or ``dorm_bed``
    room_capacity: int | None = Field(default=None, ge=1)
    private_bathroom: bool | None = None
    balcony: bool | None = None
    terrace: bool | None = None
    area_sqm: Decimal | None = Field(default=None, gt=0)
    view: str | None = None
    air_conditioning: bool | None = None
    kitchen: bool | None = None
    accessible: bool | None = None
    bed_types: list[str] = Field(default_factory=list)


class RateOffer(BaseModel):
    property_name: str | None = None
    room_name: str
    normalized_room_name: str
    adults: int | None = Field(default=None, ge=1)
    children: int | None = Field(default=None, ge=0)
    occupancy_text: str | None = None
    room_facts: RoomFacts = Field(default_factory=RoomFacts)
    meal_plan: str | None = None
    breakfast_included: bool | None = None
    breakfast_genius_benefit: bool | None = None
    genius: bool | None = None
    genius_discount_percent: int | None = Field(default=None, ge=0, le=100)
    current_price: Decimal
    original_price: Decimal | None = None
    currency: str = Field(min_length=3, max_length=3)
    free_cancellation: bool | None = None
    cancellation_deadline: datetime | None = None
    cancellation_text: str | None = None
    non_refundable: bool | None = None
    payment_conditions: str | None = None
    taxes_included: bool | None = None
    taxes_text: str | None = None
    source_row_text: str
    source_url: str
    scrape_timestamp: datetime
    parser_warnings: list[str] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class ParseResult(BaseModel):
    status: ParseStatus
    offers: list[RateOffer] = Field(default_factory=list)
    rooms_detected: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
