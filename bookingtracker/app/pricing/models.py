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
    INCOMPLETE_RESERVATION = "incomplete_reservation"
    AVAILABILITY_UNKNOWN = "availability_unknown"


class CheckReasonCode(StrEnum):
    TIMEOUT = "timeout"
    NAVIGATION_ERROR = "navigation_error"
    NETWORK_ERROR = "network_error"
    BROWSER_ERROR = "browser_error"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA_REQUIRED = "captcha_required"
    PARSER_ERROR = "parser_error"
    NO_COMPARABLE_OFFER = "no_comparable_offer"
    UNEXPECTED_ERROR = "unexpected_error"
    INCOMPLETE_RESERVATION = "incomplete_reservation"
    AVAILABILITY_UNKNOWN = "availability_unknown"


class CheckDiagnosticPhase(StrEnum):
    RESERVATION_VALIDATION = "reservation_validation"
    PAGE_NAVIGATION = "page_navigation"
    PAGE_STATE_DETECTION = "page_state_detection"
    OFFER_COLLECTION = "offer_collection"
    ROOM_NAME = "room_name"
    MEAL_PLAN = "meal_plan"
    CANCELLATION = "cancellation"
    PRICE = "price"
    CURRENCY = "currency"
    EXACT_MATCH = "exact_match"
    AVAILABILITY_DETECTION = "availability_detection"


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
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    status: PriceCheckStatus
    parser_status: ParseStatus | None = None
    matched: bool = False
    match_classification: MatchClassification | None = None
    match_score: Decimal | None = None
    comparison: PriceComparison | None = None
    match_result: MatchResult | None = None
    error: str | None = None
    reason_code: CheckReasonCode | None = None
    safe_error_detail: str | None = None
    diagnostic_phase: CheckDiagnosticPhase | None = None
    consecutive_failure_count: int = Field(default=0, ge=0)
    next_check_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class PersistedPriceCheck(PriceCheckRecord):
    """A stored check with the immutable rate-offer snapshots loaded."""

    rate_offers: list[RateOffer] = Field(default_factory=list)
