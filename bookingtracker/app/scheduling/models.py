"""Scheduling state and explicit check trigger types."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class CheckTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class ScheduleState(BaseModel):
    reservation_id: UUID
    next_check_at: datetime
    last_check_at: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
