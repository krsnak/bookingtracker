"""Create deduplicated alerts after a persisted price check."""

from __future__ import annotations

from decimal import Decimal

from app.alerts.models import Alert, AlertSeverity, AlertType, DeliveryStatus
from app.alerts.notifications import NotificationAdapter
from app.db.repository import (
    AlertRepository,
    PriceCheckRepository,
    PriceDropBandStateRepository,
    SettingsRepository,
)
from app.matching.models import MatchClassification
from app.presentation import (
    check_reason_for,
    check_reason_text,
    failure_count_word,
    format_check_datetime,
)
from app.pricing.models import PriceCheckRecord, PriceCheckStatus
from app.reservations.models import Reservation


def is_technically_healthy(check: PriceCheckRecord) -> bool:
    """A completed check that proves browser/parser health without requiring a price."""
    return check.status in {
        PriceCheckStatus.SUCCESS,
        PriceCheckStatus.NO_MATCH,
        PriceCheckStatus.AMBIGUOUS,
        PriceCheckStatus.NO_AVAILABILITY,
    }


def check_failed_is_superseded(alert: Alert, checks: list[PriceCheckRecord]) -> bool:
    """Return whether a later healthy check makes this failure non-current in the UI."""
    if alert.type is not AlertType.CHECK_FAILED or alert.price_check_id is None:
        return False
    for index, check in enumerate(checks):
        if check.id == alert.price_check_id:
            return any(is_technically_healthy(later) for later in checks[:index])
    return False


class AlertService:
    def __init__(
        self,
        alerts: AlertRepository,
        checks: PriceCheckRepository,
        notifier: NotificationAdapter,
        settings: SettingsRepository | None = None,
        bands: PriceDropBandStateRepository | None = None,
        *,
        failure_threshold: int = 3,
    ) -> None:
        self.alerts = alerts
        self.checks = checks
        self.notifier = notifier
        self.settings = settings
        self.bands = bands
        self.failure_threshold = failure_threshold

    def process(
        self,
        check: PriceCheckRecord,
        reservation: Reservation | None = None,
        *,
        consecutive_failures: int = 0,
    ) -> list[Alert]:
        created: list[Alert] = []
        if (
            self._is_price_drop(check)
            and reservation is not None
            and self._should_notify_band(check, reservation)
        ):
            current = check.comparison.current_price
            created.extend(
                self._create(
                    Alert(
                        reservation_id=check.reservation_id,
                        price_check_id=check.id,
                        type=AlertType.PRICE_DROP,
                        severity=AlertSeverity.INFO,
                        title="💸 Cena rezervace klesla",
                        message=self._price_drop_message(check, reservation),
                        dedupe_key=(
                            f"price-drop-band:{check.reservation_id}:"
                            f"{self._band(check, reservation)}"
                        ),
                        metadata={
                            "current_price": str(current),
                            "delta": str(check.comparison.delta_amount),
                            "percent_saving": str(self._saving_percent(check)),
                            "band": str(self._band(check, reservation)),
                        },
                    ),
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
                            title="Nová nejnižší porovnatelná cena",
                            message=f"Aktuální porovnatelná cena {current} je dosud nejnižší.",
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
                            title="Nalezena bezpečně porovnatelná lepší nabídka",
                            message="Nalezená lepší nabídka splnila pravidla přesného porovnání.",
                            dedupe_key=f"better-rate:{check.reservation_id}:{current}",
                        )
                    )
                )
        if check.status is PriceCheckStatus.LOGGED_OUT:
            created.extend(
                self._manual_action_alert(check, reservation, AlertType.LOGIN_REQUIRED)
            )
        if check.status is PriceCheckStatus.CAPTCHA_REQUIRED:
            created.extend(
                self._manual_action_alert(check, reservation, AlertType.CAPTCHA_REQUIRED)
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
                        title="Opakovaně se nepodařilo zkontrolovat cenu",
                        message=self._check_failed_message(
                            check, reservation, consecutive_failures
                        ),
                        dedupe_key=(
                            f"check-failed:{check.reservation_id}:"
                            f"{check_reason_for(check) or check.status}"
                        ),
                        metadata={
                            "status": check.status.value,
                            "consecutive_failures": str(consecutive_failures),
                        },
                    ),
                    allow_superseded_failure_duplicate=True,
                )
            )
        return created

    @staticmethod
    def _is_price_drop(check: PriceCheckRecord) -> bool:
        return bool(
            check.status is PriceCheckStatus.SUCCESS
            and check.matched
            and check.match_classification
            in {
                MatchClassification.EXACT,
                MatchClassification.EQUIVALENT,
                MatchClassification.BETTER,
            }
            and check.comparison
            and check.comparison.comparable
            and check.comparison.booked_price is not None
            and check.comparison.booked_price > Decimal("0")
            and check.comparison.current_price is not None
            and check.comparison.currency is not None
            and check.comparison.delta_amount is not None
            and check.comparison.delta_amount < Decimal("0")
        )

    def _threshold(self, reservation: Reservation) -> Decimal:
        return reservation.price_drop_threshold_percent or (
            self.settings.get_price_drop_threshold() if self.settings else Decimal("5")
        )

    def _saving_percent(self, check: PriceCheckRecord) -> Decimal:
        assert check.comparison and check.comparison.booked_price and check.comparison.current_price
        return (
            (check.comparison.booked_price - check.comparison.current_price)
            / check.comparison.booked_price
            * Decimal("100")
        )

    def _band(self, check: PriceCheckRecord, reservation: Reservation) -> int:
        return int(self._saving_percent(check) // self._threshold(reservation))

    def _should_notify_band(self, check: PriceCheckRecord, reservation: Reservation) -> bool:
        if not self.bands:
            return self._band(check, reservation) >= 1
        threshold, observed, band = (
            self._threshold(reservation),
            self._saving_percent(check),
            self._band(check, reservation),
        )
        previous = self.bands.get(reservation.id)
        if previous is None:
            self.bands.save(reservation.id, threshold, band, observed)
            return band >= 1
        old_threshold, old_band, old_observed = previous
        if old_threshold != threshold:
            # Rebase silently at the historical high-water mark to avoid alert floods.
            rebased_band = int(max(old_observed, observed) // threshold)
            self.bands.save(reservation.id, threshold, rebased_band, max(old_observed, observed))
            return False
        self.bands.save(reservation.id, threshold, max(old_band, band), max(old_observed, observed))
        return band > old_band

    def _price_drop_message(self, check: PriceCheckRecord, reservation: Reservation) -> str:
        comparison = check.comparison
        assert (
            comparison
            and comparison.booked_price
            and comparison.current_price
            and comparison.currency
            and comparison.delta_amount is not None
        )
        saving = -comparison.delta_amount
        return "\n".join(
            (
                reservation.property_name or "Rezervace Booking.com",
                reservation.room_type or "Neuvedený typ pokoje",
                f"Pobyt: {reservation.check_in} → {reservation.check_out}",
                f"Rezervovaná cena: {comparison.booked_price} {comparison.currency}",
                f"Aktuální cena: {comparison.current_price} {comparison.currency}",
                f"Úspora: {saving} {comparison.currency} ({self._saving_percent(check):.2f} %)",
                "Výsledek: bezpečně porovnatelná nabídka",
            )
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
        self, check: PriceCheckRecord, reservation: Reservation | None, kind: AlertType
    ) -> list[Alert]:
        reason = check_reason_for(check)
        title = (
            "Je nutné přihlášení na Booking.com"
            if kind is AlertType.LOGIN_REQUIRED
            else "Je nutné ruční ověření CAPTCHA"
        )
        property_name = (
            reservation.property_name
            if reservation and reservation.property_name
            else "Rezervace"
        )
        return self._create(
            Alert(
                reservation_id=check.reservation_id,
                price_check_id=check.id,
                type=kind,
                severity=AlertSeverity.ACTION_REQUIRED,
                title=title,
                message=f"{property_name}: {check_reason_text(reason)}",
                dedupe_key=f"{kind}:{check.reservation_id}",
            )
        )

    @staticmethod
    def _check_failed_message(
        check: PriceCheckRecord,
        reservation: Reservation | None,
        consecutive_failures: int,
    ) -> str:
        property_name = (
            reservation.property_name
            if reservation and reservation.property_name
            else "Neuvedené ubytování"
        )
        reason = check_reason_text(check_reason_for(check))
        next_attempt = format_check_datetime(check.next_check_at)
        return (
            f"{property_name}: kontrola ceny se nezdařila "
            f"{failure_count_word(consecutive_failures)} po sobě. "
            f"Poslední příčina: {reason[0].lower() + reason[1:]} "
            f"Další pokus: {next_attempt}."
        )

    def _create(
        self, alert: Alert, *, allow_superseded_failure_duplicate: bool = False
    ) -> list[Alert]:
        duplicate = self.alerts.find_active_duplicate(alert.dedupe_key)
        if duplicate and not (
            allow_superseded_failure_duplicate
            and check_failed_is_superseded(
                duplicate, self.checks.list_for_reservation(alert.reservation_id)
            )
        ):
            return []
        self.alerts.create(alert)
        try:
            self.notifier.deliver(alert)
        except Exception as error:  # delivery must never change persisted check success
            self.alerts.mark_delivery(alert.id, DeliveryStatus.FAILED, self._sanitize_error(error))
            return [alert.model_copy(update={"delivery_status": DeliveryStatus.FAILED})]
        self.alerts.mark_delivery(alert.id, DeliveryStatus.DELIVERED)
        return [alert.model_copy(update={"delivery_status": DeliveryStatus.DELIVERED})]

    def retry(self, alert_id) -> Alert | None:  # noqa: ANN001
        alert = self.alerts.get(alert_id)
        if alert is None or alert.delivery_status is not DeliveryStatus.FAILED:
            return alert
        try:
            self.notifier.deliver(alert)
        except Exception as error:
            sanitized = self._sanitize_error(error)
            self.alerts.mark_delivery(alert.id, DeliveryStatus.FAILED, sanitized)
            return alert.model_copy(update={"delivery_error": sanitized})
        self.alerts.mark_delivery(alert.id, DeliveryStatus.DELIVERED)
        return alert.model_copy(
            update={"delivery_status": DeliveryStatus.DELIVERED, "delivery_error": None}
        )

    @staticmethod
    def _sanitize_error(error: Exception) -> str:
        message = str(error).split("\n", 1)[0]
        return message.replace("SUPERVISOR_TOKEN", "[redacted]")[:300]

    @staticmethod
    def _infrastructure_failures() -> set[PriceCheckStatus]:
        return {
            PriceCheckStatus.NAVIGATION_ERROR,
            PriceCheckStatus.TIMEOUT,
            PriceCheckStatus.BROWSER_ERROR,
            PriceCheckStatus.PARSER_ERROR,
        }
