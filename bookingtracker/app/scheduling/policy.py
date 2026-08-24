"""Deterministic, injectable-clock scheduling and backoff policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from app.pricing.models import PriceCheckStatus
from app.scheduling.models import ScheduleState


@dataclass(frozen=True)
class SchedulerSettings:
    interval: timedelta = timedelta(hours=8)
    max_infrastructure_backoff: timedelta = timedelta(hours=24)
    manual_action_retry: timedelta = timedelta(days=7)
    failure_alert_threshold: int = 3
    jitter_max: timedelta = timedelta(minutes=10)


class SchedulePolicy:
    def __init__(
        self,
        settings: SchedulerSettings | None = None,
        jitter_seconds: Callable[[int], int] | None = None,
    ) -> None:
        self.settings = settings or SchedulerSettings()
        self._jitter_seconds = jitter_seconds or (lambda maximum: 0)

    def next_state(self, state: ScheduleState, status: PriceCheckStatus, now) -> ScheduleState:  # noqa: ANN001
        failures = state.consecutive_failures
        if status in {PriceCheckStatus.LOGGED_OUT, PriceCheckStatus.CAPTCHA_REQUIRED}:
            failures += 1
            delay = self.settings.manual_action_retry
        elif status in {
            PriceCheckStatus.NAVIGATION_ERROR,
            PriceCheckStatus.TIMEOUT,
            PriceCheckStatus.BROWSER_ERROR,
            PriceCheckStatus.PARSER_ERROR,
        }:
            failures += 1
            delay = min(
                self.settings.interval * (2 ** min(failures, 5)),
                self.settings.max_infrastructure_backoff,
            )
        elif status is PriceCheckStatus.SUCCESS:
            failures = 0
            delay = self.settings.interval
        else:
            failures += 1
            delay = self.settings.interval
        jitter_limit = min(
            int(self.settings.jitter_max.total_seconds()), int(delay.total_seconds() / 10)
        )
        jitter = timedelta(seconds=self._jitter_seconds(max(jitter_limit, 0)))
        return state.model_copy(
            update={
                "last_check_at": now,
                "last_success_at": now
                if status is PriceCheckStatus.SUCCESS
                else state.last_success_at,
                "next_check_at": now + delay + jitter,
                "consecutive_failures": failures,
                "updated_at": now,
            }
        )
