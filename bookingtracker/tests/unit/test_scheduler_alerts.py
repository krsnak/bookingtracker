from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Event, Thread
from uuid import UUID

from app.alerts.models import Alert, AlertSeverity, AlertType, DeliveryStatus
from app.alerts.notifications import HomeAssistantNotificationAdapter
from app.alerts.service import AlertService
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
from app.pricing.models import PriceCheckRecord, PriceCheckStatus, PriceComparison
from app.scheduling.models import CheckTrigger
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


def test_shared_runner_serializes_manual_and_scheduled_browser_checks(tmp_path) -> None:  # noqa: ANN001
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
    second = Thread(target=lambda: runner.run_check(stored.id, CheckTrigger.SCHEDULED))

    first.start()
    assert pipeline.started.wait(timeout=1)
    second.start()
    assert pipeline.calls == []
    pipeline.release.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert pipeline.calls == [stored.id, stored.id]


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
