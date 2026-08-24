from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from app.alerts.models import AlertType
from app.alerts.service import AlertService
from app.db.connection import SQLiteDatabase
from app.db.repository import (
    AlertRepository,
    PriceCheckRepository,
    ReservationRepository,
    ScheduleStateRepository,
)
from app.pricing.diagnostics import reason_code_for, sanitize_error_detail
from app.pricing.models import CheckReasonCode, PriceCheckRecord, PriceCheckStatus
from app.scheduling.models import CheckTrigger
from app.scheduling.policy import SchedulePolicy, SchedulerSettings
from app.scheduling.service import CheckRunner
from test_exact_reservation_matcher import reservation
from test_scheduler_alerts import RecordingNotifier, StaticPipeline


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (PriceCheckStatus.SUCCESS, None),
        (PriceCheckStatus.TIMEOUT, CheckReasonCode.TIMEOUT),
        (PriceCheckStatus.PARSER_ERROR, CheckReasonCode.PARSER_ERROR),
        (PriceCheckStatus.NO_MATCH, CheckReasonCode.NO_COMPARABLE_OFFER),
        (PriceCheckStatus.LOGGED_OUT, CheckReasonCode.LOGIN_REQUIRED),
        (PriceCheckStatus.CAPTCHA_REQUIRED, CheckReasonCode.CAPTCHA_REQUIRED),
        (PriceCheckStatus.NAVIGATION_ERROR, CheckReasonCode.NAVIGATION_ERROR),
        (PriceCheckStatus.BROWSER_ERROR, CheckReasonCode.BROWSER_ERROR),
    ],
)
def test_stable_reason_mapping(status, reason) -> None:  # noqa: ANN001
    assert reason_code_for(status) is reason


def test_navigation_network_failure_has_distinct_reason() -> None:
    assert (
        reason_code_for(PriceCheckStatus.NAVIGATION_ERROR, "net::ERR_CONNECTION_RESET")
        is CheckReasonCode.NETWORK_ERROR
    )


@pytest.mark.parametrize(
    ("status", "explicit_reason", "expected"),
    [
        (PriceCheckStatus.TIMEOUT, None, CheckReasonCode.TIMEOUT),
        (PriceCheckStatus.PARSER_ERROR, None, CheckReasonCode.PARSER_ERROR),
        (PriceCheckStatus.NO_MATCH, None, CheckReasonCode.NO_COMPARABLE_OFFER),
        (PriceCheckStatus.LOGGED_OUT, None, CheckReasonCode.LOGIN_REQUIRED),
        (PriceCheckStatus.CAPTCHA_REQUIRED, None, CheckReasonCode.CAPTCHA_REQUIRED),
        (
            PriceCheckStatus.BROWSER_ERROR,
            CheckReasonCode.UNEXPECTED_ERROR,
            CheckReasonCode.UNEXPECTED_ERROR,
        ),
    ],
)
def test_reason_codes_round_trip_through_new_connection(
    tmp_path, status, explicit_reason, expected
) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / f"{expected}.db")
    stored = ReservationRepository(database).create(reservation(active=True))
    PriceCheckRepository(database).create(
        PriceCheckRecord(
            reservation_id=stored.id,
            status=status,
            reason_code=explicit_reason,
            error="safe diagnostic",
        ),
        [],
    )
    loaded = PriceCheckRepository(SQLiteDatabase(database.path)).latest(stored.id)
    assert loaded is not None
    assert loaded.reason_code is expected
    assert loaded.comparison is None


@pytest.mark.parametrize(
    "unsafe",
    [
        "PIN: 1234",
        "Booking number AB12CD34",
        "Cookie: session=secret",
        "Authorization: Bearer-secret",
        "token=supersecret",
        "guest@example.com",
        "<html><body>account data</body></html>",
        "https://booking.com/hotel/x?token=secret&auth_key=nope",
        "mailto:guest@example.com",
        "Traceback (most recent call last):\n  File '/Users/name/app.py'",
        "/data/booking_profile/Default/Cookies",
        "/opt/bookingtracker/app/private.py",
    ],
)
def test_safe_error_detail_redacts_sensitive_input(unsafe: str) -> None:
    safe = sanitize_error_detail(unsafe, fallback="safe failure")
    assert safe
    folded = safe.casefold()
    secrets = (
        "1234", "ab12cd34", "secret", "bearer", "guest@example", "<html", "/users/", "/data/"
    )
    for secret in secrets:
        assert secret not in folded
    assert len(safe) <= 240


def _runner(tmp_path, statuses):  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "diagnostics.db")
    reservations = ReservationRepository(database)
    stored = reservations.create(
        reservation(active=True, check_in=date(2026, 9, 18), check_out=date(2026, 9, 19))
    )
    history = PriceCheckRepository(database)

    class SequencePipeline(StaticPipeline):
        def check(self, item, *, run_id: str):  # noqa: ANN001,ANN201
            self.status = statuses.pop(0)
            return super().check(item, run_id=run_id)

    pipeline = SequencePipeline(history, statuses[0])
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    runner = CheckRunner(
        reservations,
        pipeline,  # type: ignore[arg-type]
        ScheduleStateRepository(database),
        SchedulePolicy(
            SchedulerSettings(interval=timedelta(hours=8)), jitter_seconds=lambda maximum: 0
        ),
        AlertService(AlertRepository(database), history, RecordingNotifier()),
        clock=lambda: now,
    )
    return database, stored, history, runner


def test_scheduler_persists_diagnostics_backoff_and_reset_across_connections(tmp_path) -> None:  # noqa: ANN001,E501
    database, stored, history, runner = _runner(
        tmp_path,
        [PriceCheckStatus.TIMEOUT, PriceCheckStatus.PARSER_ERROR, PriceCheckStatus.SUCCESS],
    )
    first = runner.run_check(stored.id, CheckTrigger.MANUAL)
    second = runner.run_check(stored.id, CheckTrigger.MANUAL)
    third = runner.run_check(stored.id, CheckTrigger.MANUAL)
    assert first and second and third
    assert (first.consecutive_failure_count, second.consecutive_failure_count) == (1, 2)
    assert third.consecutive_failure_count == 0
    restarted_history = PriceCheckRepository(SQLiteDatabase(database.path))
    rows = restarted_history.list_for_reservation(stored.id)
    assert [row.consecutive_failure_count for row in rows] == [0, 2, 1]
    assert rows[1].reason_code is CheckReasonCode.PARSER_ERROR
    assert rows[2].reason_code is CheckReasonCode.TIMEOUT
    assert rows[0].started_at is not None and rows[0].finished_at is not None
    assert rows[0].comparison is None
    assert rows[1].comparison is None
    restarted_state = ScheduleStateRepository(SQLiteDatabase(database.path)).get(stored.id)
    assert restarted_state is not None
    assert restarted_state.consecutive_failures == 0
    assert rows[0].next_check_at == restarted_state.next_check_at
    assert history.latest(stored.id).duration_ms == 0  # type: ignore[union-attr]


def test_no_comparable_offer_increments_then_success_resets_failure_count(tmp_path) -> None:  # noqa: ANN001,E501
    _, stored, _, runner = _runner(
        tmp_path, [PriceCheckStatus.NO_MATCH, PriceCheckStatus.SUCCESS]
    )
    failed = runner.run_check(stored.id, CheckTrigger.MANUAL)
    succeeded = runner.run_check(stored.id, CheckTrigger.MANUAL)
    assert failed is not None and succeeded is not None
    assert failed.reason_code is CheckReasonCode.NO_COMPARABLE_OFFER
    assert failed.consecutive_failure_count == 1
    assert failed.comparison is None
    assert succeeded.consecutive_failure_count == 0


def test_unexpected_exception_is_persisted_without_price_or_price_alert(tmp_path) -> None:  # noqa: ANN001,E501
    database = SQLiteDatabase(tmp_path / "unexpected.db")
    reservations = ReservationRepository(database)
    stored = reservations.create(reservation(active=True))
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)

    class ExplodingPipeline:
        def __init__(self) -> None:
            self.history = history

        def check(self, item, *, run_id: str):  # noqa: ANN001,ANN201,ARG002
            raise RuntimeError("token=secret guest@example.com /Users/guest/private.py")

    runner = CheckRunner(
        reservations,
        ExplodingPipeline(),  # type: ignore[arg-type]
        ScheduleStateRepository(database),
        SchedulePolicy(jitter_seconds=lambda maximum: 0),
        AlertService(alerts, history, RecordingNotifier()),
        clock=lambda: datetime(2026, 8, 24, 12, tzinfo=UTC),
    )
    result = runner.run_check(stored.id, CheckTrigger.MANUAL)
    assert result is not None
    assert result.reason_code is CheckReasonCode.UNEXPECTED_ERROR
    assert result.comparison is None
    assert "secret" not in (result.safe_error_detail or "")
    persisted = PriceCheckRepository(SQLiteDatabase(database.path)).latest(stored.id)
    assert persisted is not None
    assert persisted.reason_code is CheckReasonCode.UNEXPECTED_ERROR
    assert persisted.safe_error_detail == result.safe_error_detail
    saved_alerts = alerts.list_for_reservation(stored.id)
    assert not any(alert.type is AlertType.PRICE_DROP for alert in saved_alerts)
