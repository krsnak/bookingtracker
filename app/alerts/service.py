"""Create deduplicated alerts after a persisted price check."""

from __future__ import annotations

from decimal import Decimal

from app.alerts.models import Alert, AlertSeverity, AlertType, DeliveryStatus
from app.alerts.notifications import NotificationAdapter
from app.db.repository import AlertRepository, PriceCheckRepository
from app.matching.models import MatchClassification
from app.pricing.models import PriceCheckRecord, PriceCheckStatus


class AlertService:
    def __init__(
        self,
        alerts: AlertRepository,
        checks: PriceCheckRepository,
        notifier: NotificationAdapter,
        *,
        failure_threshold: int = 3,
    ) -> None:
        self.alerts = alerts
        self.checks = checks
        self.notifier = notifier
        self.failure_threshold = failure_threshold

    def process(self, check: PriceCheckRecord, *, consecutive_failures: int = 0) -> list[Alert]:
        created: list[Alert] = []
        if self._is_price_drop(check):
            current = check.comparison.current_price
            created.extend(
                self._create(
                    Alert(
                        reservation_id=check.reservation_id,
                        price_check_id=check.id,
                        type=AlertType.PRICE_DROP,
                        severity=AlertSeverity.INFO,
                        title="Comparable price dropped",
                        message=(
                            f"Comparable current price is {current} {check.comparison.currency}."
                        ),
                        dedupe_key=(
                            f"price-drop:{check.reservation_id}:{current}:"
                            f"{check.match_classification or 'unknown'}"
                        ),
                        metadata={
                            "current_price": str(current),
                            "delta": str(check.comparison.delta_amount),
                        },
                    )
                )
            )
            if self._is_new_historical_low(check):
                created.extend(
                    self._create(
                        Alert(
                            reservation_id=check.reservation_id,
                            price_check_id=check.id,
                            type=AlertType.NEW_HISTORICAL_LOW,
                            severity=AlertSeverity.INFO,
                            title="New historical comparable low",
                            message=f"Current comparable price {current} is the lowest observed.",
                            dedupe_key=f"historical-low:{check.reservation_id}:{current}",
                            metadata={"current_price": str(current)},
                        )
                    )
                )
            if check.match_classification is MatchClassification.BETTER:
                created.extend(
                    self._create(
                        Alert(
                            reservation_id=check.reservation_id,
                            price_check_id=check.id,
                            type=AlertType.BETTER_RATE_FOUND,
                            severity=AlertSeverity.INFO,
                            title="Comparable better rate found",
                            message="A matcher-approved better rate is now comparable.",
                            dedupe_key=f"better-rate:{check.reservation_id}:{current}",
                        )
                    )
                )
        if check.status is PriceCheckStatus.LOGGED_OUT:
            created.extend(
                self._manual_action_alert(check, AlertType.LOGIN_REQUIRED, "Booking login required")
            )
        if check.status is PriceCheckStatus.CAPTCHA_REQUIRED:
            created.extend(
                self._manual_action_alert(
                    check, AlertType.CAPTCHA_REQUIRED, "Booking CAPTCHA required"
                )
            )
        if (
            consecutive_failures >= self.failure_threshold
            and check.status in self._infrastructure_failures()
        ):
            created.extend(
                self._create(
                    Alert(
                        reservation_id=check.reservation_id,
                        price_check_id=check.id,
                        type=AlertType.CHECK_FAILED,
                        severity=AlertSeverity.WARNING,
                        title="Repeated Booking check failure",
                        message=(
                            f"{consecutive_failures} consecutive infrastructure failures recorded."
                        ),
                        dedupe_key=f"check-failed:{check.reservation_id}:{check.status}",
                        metadata={
                            "status": check.status.value,
                            "consecutive_failures": str(consecutive_failures),
                        },
                    )
                )
            )
        return created

    @staticmethod
    def _is_price_drop(check: PriceCheckRecord) -> bool:
        return bool(
            check.status is PriceCheckStatus.SUCCESS
            and check.comparison
            and check.comparison.comparable
            and check.comparison.delta_amount is not None
            and check.comparison.delta_amount < Decimal("0")
        )

    def _is_new_historical_low(self, check: PriceCheckRecord) -> bool:
        current = check.comparison.current_price if check.comparison else None
        if current is None:
            return False
        previous = [
            item.comparison.current_price
            for item in self.checks.list_for_reservation(check.reservation_id)
            if item.id != check.id
            and item.status is PriceCheckStatus.SUCCESS
            and item.comparison
            and item.comparison.comparable
            and item.comparison.current_price is not None
        ]
        return not previous or current < min(previous)

    def _manual_action_alert(
        self, check: PriceCheckRecord, kind: AlertType, title: str
    ) -> list[Alert]:
        return self._create(
            Alert(
                reservation_id=check.reservation_id,
                price_check_id=check.id,
                type=kind,
                severity=AlertSeverity.ACTION_REQUIRED,
                title=title,
                message="Manual action is required before automated checks resume normally.",
                dedupe_key=f"{kind}:{check.reservation_id}",
            )
        )

    def _create(self, alert: Alert) -> list[Alert]:
        if self.alerts.find_active_duplicate(alert.dedupe_key):
            return []
        self.alerts.create(alert)
        try:
            self.notifier.deliver(alert)
        except Exception as error:  # delivery must never change persisted check success
            self.alerts.mark_delivery(alert.id, DeliveryStatus.FAILED, str(error).split("\n", 1)[0])
            return [alert.model_copy(update={"delivery_status": DeliveryStatus.FAILED})]
        self.alerts.mark_delivery(alert.id, DeliveryStatus.DELIVERED)
        return [alert.model_copy(update={"delivery_status": DeliveryStatus.DELIVERED})]

    @staticmethod
    def _infrastructure_failures() -> set[PriceCheckStatus]:
        return {
            PriceCheckStatus.NAVIGATION_ERROR,
            PriceCheckStatus.TIMEOUT,
            PriceCheckStatus.BROWSER_ERROR,
            PriceCheckStatus.PARSER_ERROR,
        }
