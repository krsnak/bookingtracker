"""Typed, reviewable comparable-price and historical-check records."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.booking.models import ParseStatus, RateOffer
from app.matching.models import MatchClassification, MatchResult


class PriceBasis(StrEnum):
    FINAL_TOTAL_INCLUDING_TAXES = "final_total_including_taxes"


class PriceDirection(StrEnum):
    LOWER = "lower"
    SAME = "same"
    HIGHER = "higher"
    UNKNOWN = "unknown"


class PriceCheckStatus(StrEnum):
    SUCCESS = "success"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    NO_AVAILABILITY = "no_availability"
    LOGGED_OUT = "logged_out"
    CAPTCHA_REQUIRED = "captcha_required"
    NAVIGATION_ERROR = "navigation_error"
    PARSER_ERROR = "parser_error"
    BROWSER_ERROR = "browser_error"
    TIMEOUT = "timeout"


class PriceComparison(BaseModel):
    """A result only when booked and current amounts have the same tax basis."""

    comparable: bool
    basis: PriceBasis | None = None
    booked_price: Decimal | None = None
    current_price: Decimal | None = None
    currency: str | None = None
    delta_amount: Decimal | None = None
    delta_percent: Decimal | None = None
    direction: PriceDirection = PriceDirection.UNKNOWN
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class PriceCheckRecord(BaseModel):
    """Append-only result of one explicit availability check."""

    id: UUID = Field(default_factory=uuid4)
    reservation_id: UUID
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: PriceCheckStatus
    parser_status: ParseStatus | None = None
    matched: bool = False
    match_classification: MatchClassification | None = None
    match_score: Decimal | None = None
    comparison: PriceComparison | None = None
    match_result: MatchResult | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class PersistedPriceCheck(PriceCheckRecord):
    """A stored check with the immutable rate-offer snapshots loaded."""

    rate_offers: list[RateOffer] = Field(default_factory=list)
