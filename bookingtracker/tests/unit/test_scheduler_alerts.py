from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Event, Thread
from uuid import UUID

from app.alerts.models import Alert, AlertSeverity, AlertType, DeliveryStatus
from app.alerts.notifications import HomeAssistantNotificationAdapter
from app.alerts.service import AlertService, check_failed_is_superseded
from app.db.connection import SQLiteDatabase
from app.db.repository import (
    AlertRepository,
    PriceCheckRepository,
    PriceDropBandStateRepository,
    ReservationRepository,
    ScheduleStateRepository,
    SettingsRepository,
)
from app.matching.models import MatchClassification
from app.pricing.models import (
    CheckReasonCode,
    PriceCheckRecord,
    PriceCheckStatus,
    PriceComparison,
)
from app.scheduling.models import CheckRunBlockReason, CheckTrigger, ScheduleState
from app.scheduling.policy import SchedulePolicy, SchedulerSettings
from app.scheduling.service import CheckRunner, ReservationScheduler
from test_exact_reservation_matcher import reservation


class RecordingNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.delivered: list[AlertType] = []

    def deliver(self, alert) -> None:  # noqa: ANN001
        if self.fail:
            raise RuntimeError("delivery unavailable")
        self.delivered.append(alert.type)


class StaticPipeline:
    def __init__(self, history: PriceCheckRepository, status: PriceCheckStatus) -> None:
        self.history = history
        self.status = status
        self.calls: list[UUID] = []

    def check(self, item, *, run_id: str) -> PriceCheckRecord:  # noqa: ANN001
        self.calls.append(item.id)
        record = PriceCheckRecord(reservation_id=item.id, run_id=run_id, status=self.status)
        return self.history.create(record, [])


class BlockingPipeline(StaticPipeline):
    def __init__(self, history: PriceCheckRepository) -> None:
        super().__init__(history, PriceCheckStatus.NO_MATCH)
        self.started = Event()
        self.release = Event()

    def check(self, item, *, run_id: str) -> PriceCheckRecord:  # noqa: ANN001
        self.started.set()
        assert self.release.wait(timeout=1)
        return super().check(item, run_id=run_id)


def comparable_check(reservation_id: UUID, current: str) -> PriceCheckRecord:
    return PriceCheckRecord(
        reservation_id=reservation_id,
        status=PriceCheckStatus.SUCCESS,
        matched=True,
        match_classification=MatchClassification.EXACT,
        comparison=PriceComparison(
            comparable=True,
            booked_price=Decimal("18.88"),
            current_price=Decimal(current),
            currency="EUR",
            delta_amount=Decimal(current) - Decimal("18.88"),
        ),
    )


def test_papaya_alert_deduplication_and_historical_low(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "alerts.db")
    reservations = ReservationRepository(database)
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    stored = reservations.create(reservation())
    notifier = RecordingNotifier()
    service = AlertService(
        alerts,
        history,
        notifier,
        SettingsRepository(database),
        PriceDropBandStateRepository(database),
    )

    first = history.create(comparable_check(stored.id, "16.88"), [])
    service.process(first, stored)
    duplicate = history.create(comparable_check(stored.id, "16.88"), [])
    service.process(duplicate, stored)
    lower = history.create(comparable_check(stored.id, "15.50"), [])
    service.process(lower, stored)
    higher = history.create(comparable_check(stored.id, "19.50"), [])
    service.process(higher, stored)

    saved = alerts.list_for_reservation(stored.id)
    price_drops = [alert for alert in saved if alert.type is AlertType.PRICE_DROP]
    lows = [alert for alert in saved if alert.type is AlertType.NEW_HISTORICAL_LOW]
    assert [alert.metadata["current_price"] for alert in price_drops] == ["15.50", "16.88"]
    assert len(lows) == 2
    assert notifier.delivered.count(AlertType.PRICE_DROP) == 2


def test_price_drop_alert_names_exact_equivalent_and_better_match_categories(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "category-alerts.db")
    stored = ReservationRepository(database).create(reservation())
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    service = AlertService(alerts, history, RecordingNotifier(), failure_threshold=3)
    messages = []
    for classification, expected in (
        (MatchClassification.EXACT, "stejného pokoje"),
        (MatchClassification.EQUIVALENT, "ekvivalentního pokoje"),
        (MatchClassification.BETTER, "prokazatelně lepšího pokoje"),
    ):
        check = comparable_check(stored.id, "16.00").model_copy(
            update={"match_classification": classification}
        )
        check = history.create(check, [])
        messages.append(service._price_drop_message(check, stored))
        assert expected in messages[-1]


def test_login_captcha_and_repeated_failure_alerts_are_deduplicated(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "alerts.db")
    stored = ReservationRepository(database).create(reservation())
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    service = AlertService(alerts, history, RecordingNotifier(), failure_threshold=3)
    for status in (
        PriceCheckStatus.LOGGED_OUT,
        PriceCheckStatus.LOGGED_OUT,
        PriceCheckStatus.CAPTCHA_REQUIRED,
        PriceCheckStatus.CAPTCHA_REQUIRED,
    ):
        service.process(
            history.create(PriceCheckRecord(reservation_id=stored.id, status=status), [])
        )
    for failures in (1, 2, 3, 4):
        service.process(
            history.create(
                PriceCheckRecord(reservation_id=stored.id, status=PriceCheckStatus.TIMEOUT), []
            ),
            consecutive_failures=failures,
        )

    assert [alert.type for alert in alerts.list_for_reservation(stored.id)].count(
        AlertType.LOGIN_REQUIRED
    ) == 1
    assert [alert.type for alert in alerts.list_for_reservation(stored.id)].count(
        AlertType.CAPTCHA_REQUIRED
    ) == 1
    assert [alert.type for alert in alerts.list_for_reservation(stored.id)].count(
        AlertType.CHECK_FAILED
    ) == 1


def test_healthy_non_comparable_result_supersedes_failure_without_false_acknowledgement(
    tmp_path,
) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "resolved-alert.db")
    stored = ReservationRepository(database).create(reservation())
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    service = AlertService(alerts, history, RecordingNotifier(), failure_threshold=3)
    failed = history.create(
        PriceCheckRecord(reservation_id=stored.id, status=PriceCheckStatus.TIMEOUT), []
    )
    service.process(failed, stored, consecutive_failures=3)

    no_match = history.create(
        PriceCheckRecord(reservation_id=stored.id, status=PriceCheckStatus.NO_MATCH), []
    )
    service.process(no_match, stored, consecutive_failures=0)

    historical = alerts.list_for_reservation(stored.id)
    assert len(historical) == 1 and historical[0].acknowledged_at is None
    assert check_failed_is_superseded(historical[0], history.list_for_reservation(stored.id))

    next_failure = history.create(
        PriceCheckRecord(reservation_id=stored.id, status=PriceCheckStatus.TIMEOUT), []
    )
    service.process(next_failure, stored, consecutive_failures=3)
    failure_alerts = [
        alert
        for alert in alerts.list_for_reservation(stored.id)
        if alert.type is AlertType.CHECK_FAILED
    ]
    assert len(failure_alerts) == 2
    assert all(alert.acknowledged_at is None for alert in failure_alerts)


def test_czech_check_failed_payload_contains_property_reason_count_and_next_attempt(
    tmp_path,
) -> None:  # noqa: ANN001
    payloads: list[tuple[str, dict[str, object]]] = []
    adapter = HomeAssistantNotificationAdapter(
        "notify.telegram",
        transport=lambda path, payload: payloads.append((path, payload)),
    )
    database = SQLiteDatabase(tmp_path / "czech-alert.db")
    stored = ReservationRepository(database).create(reservation(property_name="STORHAUGEN GARD"))
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    service = AlertService(alerts, history, adapter, failure_threshold=3)
    next_attempt = datetime(2026, 8, 25, 5, 35, tzinfo=UTC)
    for failures in (1, 2, 3, 4):
        check = history.create(
            PriceCheckRecord(
                reservation_id=stored.id,
                status=PriceCheckStatus.PARSER_ERROR,
                reason_code=CheckReasonCode.PARSER_ERROR,
                safe_error_detail="Locator.inner_text: Timeout 1000ms exceeded",
                consecutive_failure_count=failures,
                next_check_at=next_attempt,
            ),
            [],
        )
        service.process(check, stored, consecutive_failures=failures)

    saved = [
        alert
        for alert in alerts.list_for_reservation(stored.id)
        if alert.type is AlertType.CHECK_FAILED
    ]
    assert len(saved) == 1
    alert = saved[0]
    assert alert.title == "Opakovaně se nepodařilo zkontrolovat cenu"
    assert alert.message == (
        "STORHAUGEN GARD: kontrola ceny se nezdařila třikrát po sobě. "
        "Poslední příčina: stránka se načetla, ale nepodařilo se z ní bezpečně "
        "přečíst odpovídající nabídku. Další pokus: 25. 8. 2026 v 5:35."
    )
    assert payloads == [
        (
            "/services/notify/send_message",
            {
                "entity_id": "notify.telegram",
                "title": alert.title,
                "message": alert.message,
            },
        )
    ]
    assert "Locator.inner_text" not in str(payloads)


def test_login_and_captcha_alert_immediately_with_czech_notification(tmp_path) -> None:  # noqa: ANN001,E501
    database = SQLiteDatabase(tmp_path / "manual-alert.db")
    stored = ReservationRepository(database).create(reservation(property_name="Safe Hotel"))
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    delivered: list[Alert] = []

    class CaptureNotifier:
        def deliver(self, alert: Alert) -> None:
            delivered.append(alert)

    service = AlertService(alerts, history, CaptureNotifier())
    for status in (PriceCheckStatus.LOGGED_OUT, PriceCheckStatus.CAPTCHA_REQUIRED):
        check = history.create(PriceCheckRecord(reservation_id=stored.id, status=status), [])
        service.process(check, stored, consecutive_failures=1)
    assert [alert.title for alert in delivered] == [
        "Je nutné přihlášení na Booking.com",
        "Je nutné ruční ověření CAPTCHA",
    ]
    assert all(alert.message.startswith("Safe Hotel: ") for alert in delivered)


def test_notification_failure_does_not_invalidate_price_check(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "alerts.db")
    stored = ReservationRepository(database).create(reservation())
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    check = history.create(comparable_check(stored.id, "16.88"), [])

    AlertService(
        alerts,
        history,
        RecordingNotifier(fail=True),
        SettingsRepository(database),
        PriceDropBandStateRepository(database),
    ).process(check, stored)

    assert history.latest(stored.id).status is PriceCheckStatus.SUCCESS  # type: ignore[union-attr]
    assert alerts.list_for_reservation(stored.id)[0].delivery_status is DeliveryStatus.FAILED


def test_percentage_bands_and_threshold_change_are_persisted_without_replay(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "alerts.db")
    stored = ReservationRepository(database).create(reservation())
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    notifier = RecordingNotifier()
    service = AlertService(
        alerts,
        history,
        notifier,
        SettingsRepository(database),
        PriceDropBandStateRepository(database),
    )
    for current in ("18.00", "17.00", "17.00", "16.00", "18.00", "17.00", "15.00"):
        check = history.create(comparable_check(stored.id, current), [])
        service.process(check, stored)
    drops = [
        item for item in alerts.list_for_reservation(stored.id) if item.type is AlertType.PRICE_DROP
    ]
    assert [item.metadata["band"] for item in drops] == ["4", "3", "1"]
    SettingsRepository(database).set_price_drop_threshold(Decimal("2"))
    service.process(history.create(comparable_check(stored.id, "15.00"), []), stored)
    assert (
        len(
            [
                item
                for item in alerts.list_for_reservation(stored.id)
                if item.type is AlertType.PRICE_DROP
            ]
        )
        == 3
    )


def test_home_assistant_notify_rest_payload_uses_top_level_entity_and_message() -> None:
    captured: list[tuple[str, dict[str, object]]] = []
    adapter = HomeAssistantNotificationAdapter(
        "notify.telegram_bot_roman",
        transport=lambda path, payload: captured.append((path, payload)),
    )
    adapter.deliver(
        Alert(
            type=AlertType.PRICE_DROP,
            severity=AlertSeverity.INFO,
            title="Price drop",
            message="Safe message",
            dedupe_key="test",
        )
    )
    assert captured == [
        (
            "/services/notify/send_message",
            {
                "entity_id": "notify.telegram_bot_roman",
                "title": "Price drop",
                "message": "Safe message",
            },
        )
    ]
    assert "target" not in captured[0][1]
    assert "data" not in captured[0][1]


def test_scheduler_persists_due_state_skips_inactive_or_expired_and_backs_off(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "scheduler.db")
    reservations = ReservationRepository(database)
    active = reservations.create(
        reservation(active=True, check_in=date(2026, 9, 18), check_out=date(2026, 9, 19))
    )
    reservations.create(reservation(active=False))
    reservations.create(reservation(check_in=date(2026, 8, 1), check_out=date(2026, 8, 2)))
    history = PriceCheckRepository(database)
    pipeline = StaticPipeline(history, PriceCheckStatus.TIMEOUT)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    settings = SchedulerSettings(
        interval=timedelta(hours=8), max_infrastructure_backoff=timedelta(hours=24)
    )
    policy = SchedulePolicy(settings, jitter_seconds=lambda maximum: 0)
    runner = CheckRunner(
        reservations,
        pipeline,  # type: ignore[arg-type]
        ScheduleStateRepository(database),
        policy,
        AlertService(AlertRepository(database), history, RecordingNotifier()),
        clock=lambda: now,
    )

    first_scheduler = ReservationScheduler(runner)
    completed = first_scheduler.run_due()
    restarted_scheduler = ReservationScheduler(runner)
    recovered = restarted_scheduler.run_due()
    state = runner.schedules.get(active.id)

    assert [check.status for check in completed] == [PriceCheckStatus.TIMEOUT]
    assert recovered == []
    assert pipeline.calls == [active.id]
    assert state is not None
    assert state.consecutive_failures == 1
    assert state.next_check_at == now + timedelta(hours=16)
    assert runner.run_check(active.id, CheckTrigger.MANUAL) is not None


def test_waiting_scheduler_run_revalidates_after_manual_completion(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "scheduler.db")
    reservations = ReservationRepository(database)
    stored = reservations.create(reservation(active=True))
    history = PriceCheckRepository(database)
    pipeline = BlockingPipeline(history)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    runner = CheckRunner(
        reservations,
        pipeline,  # type: ignore[arg-type]
        ScheduleStateRepository(database),
        SchedulePolicy(jitter_seconds=lambda maximum: 0),
        AlertService(AlertRepository(database), history, RecordingNotifier()),
        clock=lambda: now,
    )
    first = Thread(target=lambda: runner.run_check(stored.id, CheckTrigger.MANUAL))
    second = Thread(target=lambda: runner.run_check(stored.id, CheckTrigger.SCHEDULER))

    first.start()
    assert pipeline.started.wait(timeout=1)
    second.start()
    assert pipeline.calls == []
    pipeline.release.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert pipeline.calls == [stored.id]
    assert len(history.list_for_reservation(stored.id)) == 1


def test_scheduler_skips_busy_manual_check_without_history_write(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "scheduler-busy.db")
    reservations = ReservationRepository(database)
    stored = reservations.create(reservation(active=True))
    history = PriceCheckRepository(database)
    pipeline = BlockingPipeline(history)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    runner = CheckRunner(
        reservations,
        pipeline,  # type: ignore[arg-type]
        ScheduleStateRepository(database),
        SchedulePolicy(jitter_seconds=lambda maximum: 0),
        AlertService(AlertRepository(database), history, RecordingNotifier()),
        clock=lambda: now,
    )
    manual = Thread(target=lambda: runner.run_check(stored.id, CheckTrigger.MANUAL))
    manual.start()
    assert pipeline.started.wait(timeout=1)

    try:
        assert ReservationScheduler(runner).run_due() == []
        assert history.list_for_reservation(stored.id) == []
        assert pipeline.calls == []
    finally:
        pipeline.release.set()
        manual.join(timeout=1)

    assert not manual.is_alive()
    assert pipeline.calls == [stored.id]
    assert len(history.list_for_reservation(stored.id)) == 1


def test_scheduler_revalidates_due_state_inside_lock_before_writing_history(tmp_path) -> None:  # noqa: ANN001,E501
    database = SQLiteDatabase(tmp_path / "scheduler-revalidate.db")
    reservations = ReservationRepository(database)
    stored = reservations.create(reservation(active=True))
    history = PriceCheckRepository(database)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    due = ScheduleState(reservation_id=stored.id, next_check_at=now, updated_at=now)
    future = due.model_copy(update={"next_check_at": now + timedelta(hours=8)})

    class AdvancingSchedules:
        def __init__(self) -> None:
            self.get_calls = 0

        def get(self, reservation_id):  # noqa: ANN001, ANN201
            assert reservation_id == stored.id
            self.get_calls += 1
            return due if self.get_calls == 1 else future

        def save(self, state):  # noqa: ANN001, ANN201
            raise AssertionError(f"scheduler must not save {state}")

    schedules = AdvancingSchedules()
    pipeline = StaticPipeline(history, PriceCheckStatus.SUCCESS)
    runner = CheckRunner(
        reservations,
        pipeline,  # type: ignore[arg-type]
        schedules,  # type: ignore[arg-type]
        SchedulePolicy(jitter_seconds=lambda maximum: 0),
        AlertService(AlertRepository(database), history, RecordingNotifier()),
        clock=lambda: now,
    )

    assert ReservationScheduler(runner).run_due() == []
    assert schedules.get_calls == 2
    assert pipeline.calls == []
    assert history.list_for_reservation(stored.id) == []


def test_manual_busy_then_later_manual_request_has_exactly_two_legitimate_runs(tmp_path) -> None:  # noqa: ANN001,E501
    database = SQLiteDatabase(tmp_path / "manual-busy.db")
    reservations = ReservationRepository(database)
    stored = reservations.create(reservation(active=True))
    history = PriceCheckRepository(database)
    pipeline = BlockingPipeline(history)
    runner = CheckRunner(
        reservations,
        pipeline,  # type: ignore[arg-type]
        ScheduleStateRepository(database),
        SchedulePolicy(jitter_seconds=lambda maximum: 0),
        AlertService(AlertRepository(database), history, RecordingNotifier()),
    )
    first = Thread(target=lambda: runner.run_check(stored.id, CheckTrigger.MANUAL))
    first.start()
    assert pipeline.started.wait(timeout=1)

    duplicate = runner.try_run_check(stored.id, CheckTrigger.MANUAL)
    assert duplicate.blocked_reason is CheckRunBlockReason.BUSY
    assert history.list_for_reservation(stored.id) == []

    pipeline.release.set()
    first.join(timeout=1)
    assert not first.is_alive()
    assert len(history.list_for_reservation(stored.id)) == 1

    later = runner.try_run_check(stored.id, CheckTrigger.MANUAL)
    assert later.record is not None
    assert len(history.list_for_reservation(stored.id)) == 2


def test_manual_remote_lease_blocks_scheduler_without_a_price_check(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "scheduler.db")
    reservations = ReservationRepository(database)
    stored = reservations.create(reservation(active=True))
    history = PriceCheckRepository(database)
    pipeline = StaticPipeline(history, PriceCheckStatus.NO_MATCH)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    runner = CheckRunner(
        reservations,
        pipeline,  # type: ignore[arg-type]
        ScheduleStateRepository(database),
        SchedulePolicy(jitter_seconds=lambda maximum: 0),
        AlertService(AlertRepository(database), history, RecordingNotifier()),
        clock=lambda: now,
        manual_session_active=lambda: True,
    )

    assert ReservationScheduler(runner).run_due() == []
    assert runner.run_check(stored.id, CheckTrigger.MANUAL) is None
    assert pipeline.calls == []
    assert history.latest(stored.id) is None


def test_manual_failure_alert_is_deduplicated_against_following_scheduler_run(
    tmp_path,
) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "manual-dedupe.db")
    reservations = ReservationRepository(database)
    stored = reservations.create(reservation(active=True))
    history = PriceCheckRepository(database)
    pipeline = StaticPipeline(history, PriceCheckStatus.TIMEOUT)
    alerts = AlertRepository(database)
    runner = CheckRunner(
        reservations,
        pipeline,  # type: ignore[arg-type]
        ScheduleStateRepository(database),
        SchedulePolicy(jitter_seconds=lambda maximum: 0),
        AlertService(alerts, history, RecordingNotifier(), failure_threshold=3),
        clock=lambda: datetime(2026, 8, 24, 12, tzinfo=UTC),
    )

    for _ in range(3):
        runner.run_check(stored.id, CheckTrigger.MANUAL)
    runner.run_check(stored.id, CheckTrigger.SCHEDULER)
    pipeline.status = PriceCheckStatus.SUCCESS
    succeeded = runner.run_check(stored.id, CheckTrigger.MANUAL)

    saved = alerts.list_for_reservation(stored.id)
    assert sum(alert.type is AlertType.CHECK_FAILED for alert in saved) == 1
    assert not any(alert.type is AlertType.PRICE_DROP for alert in saved)
    assert succeeded is not None and succeeded.consecutive_failure_count == 0
    state = runner.schedules.get(stored.id)
    assert state is not None and state.consecutive_failures == 0
