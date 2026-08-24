"""Single-process scheduler and shared manual/scheduled check runner."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID

from app.alerts.service import AlertService
from app.db.repository import ReservationRepository, ScheduleStateRepository
from app.pricing.check_service import PriceCheckService
from app.pricing.diagnostics import reason_code_for, sanitize_error_detail
from app.pricing.models import CheckReasonCode, PriceCheckRecord, PriceCheckStatus
from app.scheduling.models import CheckTrigger, ScheduleState
from app.scheduling.policy import SchedulePolicy

LOGGER = logging.getLogger("bookingtracker.checks")
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False
_STDOUT_HANDLER = logging.StreamHandler(sys.stdout)
_STDOUT_HANDLER.setFormatter(logging.Formatter("%(message)s"))
LOGGER.addHandler(_STDOUT_HANDLER)


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
            try:
                record = self.checks.check(reservation, run_id=f"{trigger}:{reservation.id}")
            except Exception as error:
                started_at = now
                finished_at = self.clock()
                safe_detail = sanitize_error_detail(error, fallback="unexpected check failure")
                record = self.checks.history.create(
                    PriceCheckRecord(
                        reservation_id=reservation.id,
                        run_id=f"{trigger}:{reservation.id}",
                        checked_at=started_at,
                        started_at=started_at,
                        finished_at=finished_at,
                        duration_ms=max(
                            0, int((finished_at - started_at).total_seconds() * 1000)
                        ),
                        status=PriceCheckStatus.BROWSER_ERROR,
                        reason_code=CheckReasonCode.UNEXPECTED_ERROR,
                        safe_error_detail=safe_detail,
                        error=safe_detail,
                    ),
                    [],
                )
            state = self.schedules.get(reservation.id) or ScheduleState(
                reservation_id=reservation.id, next_check_at=now, updated_at=now
            )
            updated = self.policy.next_state(state, record.status, now)
            finished_at = record.finished_at or self.clock()
            reason = record.reason_code or reason_code_for(record.status, record.error)
            safe_detail = sanitize_error_detail(record.safe_error_detail or record.error)
            record = record.model_copy(
                update={
                    "started_at": record.started_at or record.checked_at,
                    "finished_at": finished_at,
                    "duration_ms": record.duration_ms
                    if record.duration_ms is not None
                    else max(
                        0,
                        int(
                            (
                                finished_at - (record.started_at or record.checked_at)
                            ).total_seconds()
                            * 1000
                        ),
                    ),
                    "reason_code": reason,
                    "safe_error_detail": safe_detail,
                    "error": safe_detail,
                    "consecutive_failure_count": updated.consecutive_failures,
                    "next_check_at": updated.next_check_at,
                }
            )
            self.checks.history.complete_with_schedule(record, updated)
            if trigger is CheckTrigger.SCHEDULED:
                self._log_completed_check(record, reservation.property_name)
            self.alerts.process(
                record, reservation, consecutive_failures=updated.consecutive_failures
            )
            return record

    @staticmethod
    def _log_completed_check(record: PriceCheckRecord, property_name: str | None) -> None:
        safe_property = sanitize_error_detail(property_name, fallback="Neuvedené ubytování")
        safe_detail = sanitize_error_detail(record.safe_error_detail)
        payload = {
            "event": "booking_check_completed",
            "property_name": safe_property,
            "reservation_id": str(record.reservation_id),
            "status": record.status.value,
            "reason_code": record.reason_code.value if record.reason_code else None,
            "duration_ms": record.duration_ms,
            "consecutive_failure_count": record.consecutive_failure_count,
            "next_check_at": record.next_check_at.isoformat() if record.next_check_at else None,
            "safe_error_detail": safe_detail,
        }
        message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if record.reason_code is CheckReasonCode.UNEXPECTED_ERROR:
            LOGGER.error(message)
        elif record.status is PriceCheckStatus.SUCCESS:
            LOGGER.info(message)
        else:
            LOGGER.warning(message)

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
