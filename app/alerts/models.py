"""Typed alert records; delivery state is separate from price-check history."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AlertType(StrEnum):
    PRICE_DROP = "price_drop"
    NEW_HISTORICAL_LOW = "new_historical_low"
    BETTER_RATE_FOUND = "better_rate_found"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA_REQUIRED = "captcha_required"
    CHECK_FAILED = "check_failed"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ACTION_REQUIRED = "action_required"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class Alert(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    reservation_id: UUID | None = None
    price_check_id: UUID | None = None
    type: AlertType
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    severity: AlertSeverity
    title: str
    message: str
    dedupe_key: str
    metadata: dict[str, str] = Field(default_factory=dict)
    acknowledged_at: datetime | None = None
    delivery_status: DeliveryStatus = DeliveryStatus.PENDING
    delivery_error: str | None = None
