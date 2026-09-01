"""Scheduling state and explicit check trigger types."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.pricing.models import PriceCheckRecord


class CheckTrigger(StrEnum):
    MANUAL = "manual"
    MANUAL_ALL = "manual_all"
    SCHEDULER = "scheduler"


class CheckRunBlockReason(StrEnum):
    BUSY = "busy"
    NOT_DUE = "not_due"
    MANUAL_SESSION_ACTIVE = "manual_session_active"
    RESERVATION_NOT_FOUND = "reservation_not_found"
    RESERVATION_INACTIVE = "reservation_inactive"


class CheckRunOutcome(BaseModel):
    record: PriceCheckRecord | None = None
    blocked_reason: CheckRunBlockReason | None = None


class ScheduleState(BaseModel):
    reservation_id: UUID
    next_check_at: datetime
    last_check_at: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
