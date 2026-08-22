"""Single-process scheduler and shared manual/scheduled check runner."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID

from app.alerts.service import AlertService
from app.db.repository import ReservationRepository, ScheduleStateRepository
from app.pricing.check_service import PriceCheckService
from app.pricing.models import PriceCheckRecord
from app.scheduling.models import CheckTrigger, ScheduleState
from app.scheduling.policy import SchedulePolicy


class CheckRunner:
    """The sole shared entry point for manual and scheduled checks."""

    def __init__(
        self,
        reservations: ReservationRepository,
        checks: PriceCheckService,
        schedules: ScheduleStateRepository,
        policy: SchedulePolicy,
        alerts: AlertService,
        clock: Callable[[], datetime] | None = None,
        manual_session_active: Callable[[], bool] | None = None,
    ) -> None:
        self.reservations = reservations
        self.checks = checks
        self.schedules = schedules
        self.policy = policy
        self.alerts = alerts
        self.clock = clock or (lambda: datetime.now(UTC))
        self.manual_session_active = manual_session_active or (lambda: False)
        self._lock = Lock()

    def run_check(self, reservation_id: UUID, trigger: CheckTrigger) -> PriceCheckRecord | None:
        if self.manual_session_active():
            return None
        with self._lock:
            if self.manual_session_active():
                return None
            reservation = self.reservations.get(reservation_id)
            if reservation is None or not reservation.active:
                return None
            now = self.clock()
            if (
                trigger is CheckTrigger.SCHEDULED
                and reservation.check_in
                and reservation.check_in <= now.date()
            ):
                return None
            record = self.checks.check(reservation, run_id=f"{trigger}:{reservation.id}")
            state = self.schedules.get(reservation.id) or ScheduleState(
                reservation_id=reservation.id, next_check_at=now, updated_at=now
            )
            updated = self.policy.next_state(state, record.status, now)
            self.schedules.save(updated)
            self.alerts.process(record, consecutive_failures=updated.consecutive_failures)
            return record

    def begin_manual_session(self, acquire: Callable[[], bool]) -> bool:
        """Atomically acquire the lease after any in-flight check has finished."""
        with self._lock:
            return acquire()


class ReservationScheduler:
    """A pollable scheduler; callers choose the process loop and shutdown lifecycle."""

    def __init__(self, runner: CheckRunner, clock: Callable[[], datetime] | None = None) -> None:
        self.runner = runner
        self.clock = clock or runner.clock
        self._stopped = False

    def run_due(self) -> list[PriceCheckRecord]:
        if self._stopped:
            return []
        now = self.clock()
        completed: list[PriceCheckRecord] = []
        for reservation in self.runner.reservations.list_active():
            if reservation.check_in and reservation.check_in <= now.date():
                continue
            state = self.runner.schedules.get(reservation.id)
            if state is None:
                state = ScheduleState(
                    reservation_id=reservation.id, next_check_at=now, updated_at=now
                )
                self.runner.schedules.save(state)
            if state.next_check_at <= now:
                result = self.runner.run_check(reservation.id, CheckTrigger.SCHEDULED)
                if result:
                    completed.append(result)
        return completed

    def stop(self) -> None:
        self._stopped = True
