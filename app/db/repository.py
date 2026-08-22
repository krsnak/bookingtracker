"""Repositories that keep reservation and check history semantics explicit."""
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.alerts.models import Alert, DeliveryStatus
from app.booking.models import RateOffer
from app.db.connection import SQLiteDatabase
from app.pricing.models import PersistedPriceCheck, PriceCheckRecord, PriceComparison
from app.reservations.models import Reservation
from app.scheduling.models import ScheduleState


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


class ReservationRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, reservation: Reservation) -> Reservation:
        self.database.migrate()
        now = datetime.now(UTC)
        stored = reservation.model_copy(update={"created_at": now, "updated_at": now})
        with self.database.transaction() as connection:
            values = self._values(stored)
            connection.execute(
                self._insert_sql(), tuple(values[column] for column in self._columns())
            )
        return stored

    def get(self, reservation_id: UUID) -> Reservation | None:
        self.database.migrate()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM reservations WHERE id = ?", (str(reservation_id),)
            ).fetchone()
        return self._from_row(row) if row else None

    def update(self, reservation: Reservation) -> Reservation:
        self.database.migrate()
        stored = reservation.model_copy(update={"updated_at": datetime.now(UTC)})
        values = self._values(stored)
        assignments = ", ".join(f"{column} = ?" for column in values if column != "id")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE reservations SET {assignments} WHERE id = ?",
                tuple(values[column] for column in values if column != "id") + (values["id"],),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"reservation not found: {reservation.id}")
        return stored

    def list_active(self) -> list[Reservation]:
        self.database.migrate()
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM reservations WHERE active = 1 ORDER BY check_in, id"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def deactivate(self, reservation_id: UUID) -> Reservation:
        reservation = self.get(reservation_id)
        if reservation is None:
            raise KeyError(f"reservation not found: {reservation_id}")
        return self.update(reservation.model_copy(update={"active": False}))

    @staticmethod
    def _insert_sql() -> str:
        columns = ", ".join(ReservationRepository._columns())
        placeholders = ", ".join("?" for _ in ReservationRepository._columns())
        return f"INSERT INTO reservations ({columns}) VALUES ({placeholders})"

    @staticmethod
    def _columns() -> tuple[str, ...]:
        return (
            "id",
            "source",
            "property_name",
            "booking_url",
            "check_in",
            "check_out",
            "nights",
            "adults",
            "children",
            "children_ages_json",
            "rooms_count",
            "room_type",
            "rooms_breakdown_json",
            "meal_plan",
            "breakfast_included",
            "cancellation_text",
            "free_cancellation",
            "cancellation_deadline",
            "booked_total_price",
            "booked_payable_price",
            "booked_base_price",
            "taxes_and_fees",
            "vat",
            "city_tax",
            "currency",
            "payment_conditions",
            "source_text",
            "extraction_confidence",
            "field_confidence_json",
            "warnings_json",
            "active",
            "created_at",
            "updated_at",
        )

    @staticmethod
    def _values(reservation: Reservation) -> dict[str, object]:
        return {
            "id": str(reservation.id),
            "source": reservation.source.value,
            "property_name": reservation.property_name,
            "booking_url": reservation.booking_url,
            "check_in": reservation.check_in.isoformat() if reservation.check_in else None,
            "check_out": reservation.check_out.isoformat() if reservation.check_out else None,
            "nights": reservation.nights,
            "adults": reservation.adults,
            "children": reservation.children,
            "children_ages_json": _json(reservation.children_ages)
            if reservation.children_ages is not None
            else None,
            "rooms_count": reservation.rooms_count,
            "room_type": reservation.room_type,
            "rooms_breakdown_json": _json(
                [room.model_dump() for room in reservation.rooms_breakdown]
            )
            if reservation.rooms_breakdown is not None
            else None,
            "meal_plan": reservation.meal_plan,
            "breakfast_included": reservation.breakfast_included,
            "cancellation_text": reservation.cancellation_text,
            "free_cancellation": reservation.free_cancellation,
            "cancellation_deadline": _utc_iso(reservation.cancellation_deadline)
            if reservation.cancellation_deadline
            else None,
            "booked_total_price": _decimal(reservation.booked_total_price),
            "booked_payable_price": _decimal(reservation.booked_payable_price),
            "booked_base_price": _decimal(reservation.booked_base_price),
            "taxes_and_fees": _decimal(reservation.taxes_and_fees),
            "vat": _decimal(reservation.vat),
            "city_tax": _decimal(reservation.city_tax),
            "currency": reservation.currency,
            "payment_conditions": reservation.payment_conditions,
            "source_text": reservation.source_text,
            "extraction_confidence": str(reservation.extraction_confidence),
            "field_confidence_json": _json(
                {key: value.value for key, value in reservation.field_confidence.items()}
            ),
            "warnings_json": _json(reservation.warnings),
            "active": int(reservation.active),
            "created_at": _utc_iso(reservation.created_at),
            "updated_at": _utc_iso(reservation.updated_at),
        }

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Reservation:
        data = dict(row)
        for name in (
            "booked_total_price",
            "booked_payable_price",
            "booked_base_price",
            "taxes_and_fees",
            "vat",
            "city_tax",
        ):
            if data[name] is not None:
                data[name] = Decimal(data[name])
        data.update(
            id=UUID(data["id"]),
            children_ages=json.loads(data.pop("children_ages_json"))
            if data.get("children_ages_json")
            else None,
            rooms_breakdown=json.loads(data.pop("rooms_breakdown_json"))
            if data.get("rooms_breakdown_json")
            else None,
            field_confidence=json.loads(data.pop("field_confidence_json")),
            warnings=json.loads(data.pop("warnings_json")),
            active=bool(data["active"]),
            extraction_confidence=float(data["extraction_confidence"]),
        )
        return Reservation.model_validate(data)


class PriceCheckRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, check: PriceCheckRecord, rate_offers: list[RateOffer]) -> PriceCheckRecord:
        self.database.migrate()
        comparison = check.comparison
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO price_checks (
                    id, reservation_id, run_id, checked_at, status, parser_status, matched,
                    match_classification, match_score, comparable, price_basis,
                    booked_comparable_price, current_comparable_price, currency, delta_amount,
                    delta_percent, direction, comparison_reasons_json, comparison_warnings_json,
                    match_result_json, error, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(check.id),
                    str(check.reservation_id),
                    check.run_id,
                    _utc_iso(check.checked_at),
                    check.status.value,
                    check.parser_status.value if check.parser_status else None,
                    int(check.matched),
                    check.match_classification.value if check.match_classification else None,
                    _decimal(check.match_score),
                    int(comparison.comparable) if comparison else None,
                    comparison.basis.value if comparison and comparison.basis else None,
                    _decimal(comparison.booked_price) if comparison else None,
                    _decimal(comparison.current_price) if comparison else None,
                    comparison.currency if comparison else None,
                    _decimal(comparison.delta_amount) if comparison else None,
                    _decimal(comparison.delta_percent) if comparison else None,
                    comparison.direction.value if comparison else None,
                    _json(comparison.reasons) if comparison else "[]",
                    _json(comparison.warnings) if comparison else "[]",
                    _json(check.match_result.model_dump(mode="json"))
                    if check.match_result
                    else None,
                    check.error,
                    _json(check.warnings),
                ),
            )
            for ordinal, offer in enumerate(rate_offers):
                connection.execute(
                    "INSERT INTO rate_offer_snapshots (id, price_check_id, ordinal, offer_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        str(uuid4()),
                        str(check.id),
                        ordinal,
                        _json(offer.model_dump(mode="json")),
                        _utc_iso(check.checked_at),
                    ),
                )
        return check

    def list_for_reservation(self, reservation_id: UUID) -> list[PersistedPriceCheck]:
        self.database.migrate()
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM price_checks WHERE reservation_id = ? ORDER BY checked_at DESC, id DESC",
                (str(reservation_id),),
            ).fetchall()
            return [self._from_row(connection, row) for row in rows]

    def latest(self, reservation_id: UUID) -> PersistedPriceCheck | None:
        checks = self.list_for_reservation(reservation_id)
        return checks[0] if checks else None

    def latest_comparable(self, reservation_id: UUID) -> PersistedPriceCheck | None:
        self.database.migrate()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM price_checks WHERE reservation_id = ? AND status = 'success' AND comparable = 1 ORDER BY checked_at DESC, id DESC LIMIT 1",
                (str(reservation_id),),
            ).fetchone()
            return self._from_row(connection, row) if row else None

    @staticmethod
    def _from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> PersistedPriceCheck:
        data = dict(row)
        for name in (
            "match_score",
            "booked_comparable_price",
            "current_comparable_price",
            "delta_amount",
            "delta_percent",
        ):
            if data[name] is not None:
                data[name] = Decimal(data[name])
        comparison = None
        comparable = data.pop("comparable")
        if comparable is not None:
            comparison = PriceComparison(
                comparable=bool(comparable),
                basis=data.pop("price_basis"),
                booked_price=data.pop("booked_comparable_price"),
                current_price=data.pop("current_comparable_price"),
                currency=data.pop("currency"),
                delta_amount=data.pop("delta_amount"),
                delta_percent=data.pop("delta_percent"),
                direction=data.pop("direction"),
                reasons=json.loads(data.pop("comparison_reasons_json")),
                warnings=json.loads(data.pop("comparison_warnings_json")),
            )
        else:
            for name in (
                "price_basis",
                "booked_comparable_price",
                "current_comparable_price",
                "currency",
                "delta_amount",
                "delta_percent",
                "direction",
                "comparison_reasons_json",
                "comparison_warnings_json",
            ):
                data.pop(name)
        snapshots = connection.execute(
            "SELECT offer_json FROM rate_offer_snapshots WHERE price_check_id = ? ORDER BY ordinal",
            (data["id"],),
        ).fetchall()
        match_json = data.pop("match_result_json")
        data.update(
            id=UUID(data["id"]),
            reservation_id=UUID(data["reservation_id"]),
            checked_at=datetime.fromisoformat(data["checked_at"]),
            matched=bool(data["matched"]),
            comparison=comparison,
            match_result=json.loads(match_json) if match_json else None,
            warnings=json.loads(data.pop("warnings_json")),
            rate_offers=[
                RateOffer.model_validate_json(snapshot["offer_json"]) for snapshot in snapshots
            ],
        )
        return PersistedPriceCheck.model_validate(data)


class ScheduleStateRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def get(self, reservation_id: UUID) -> ScheduleState | None:
        self.database.migrate()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM schedule_states WHERE reservation_id = ?", (str(reservation_id),)
            ).fetchone()
        return self._from_row(row) if row else None

    def save(self, state: ScheduleState) -> ScheduleState:
        self.database.migrate()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO schedule_states (
                    reservation_id, next_check_at, last_check_at, last_success_at,
                    consecutive_failures, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(reservation_id) DO UPDATE SET
                    next_check_at=excluded.next_check_at,
                    last_check_at=excluded.last_check_at,
                    last_success_at=excluded.last_success_at,
                    consecutive_failures=excluded.consecutive_failures,
                    updated_at=excluded.updated_at""",
                (
                    str(state.reservation_id),
                    _utc_iso(state.next_check_at),
                    _utc_iso(state.last_check_at) if state.last_check_at else None,
                    _utc_iso(state.last_success_at) if state.last_success_at else None,
                    state.consecutive_failures,
                    _utc_iso(state.updated_at),
                ),
            )
        return state

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ScheduleState:
        return ScheduleState(
            reservation_id=UUID(row["reservation_id"]),
            next_check_at=datetime.fromisoformat(row["next_check_at"]),
            last_check_at=datetime.fromisoformat(row["last_check_at"])
            if row["last_check_at"]
            else None,
            last_success_at=datetime.fromisoformat(row["last_success_at"])
            if row["last_success_at"]
            else None,
            consecutive_failures=row["consecutive_failures"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class AlertRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, alert: Alert) -> Alert:
        self.database.migrate()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO alerts (
                    id, reservation_id, price_check_id, type, created_at, severity, title,
                    message, dedupe_key, metadata_json, acknowledged_at, delivery_status,
                    delivery_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(alert.id),
                    str(alert.reservation_id) if alert.reservation_id else None,
                    str(alert.price_check_id) if alert.price_check_id else None,
                    alert.type.value,
                    _utc_iso(alert.created_at),
                    alert.severity.value,
                    alert.title,
                    alert.message,
                    alert.dedupe_key,
                    _json(alert.metadata),
                    _utc_iso(alert.acknowledged_at) if alert.acknowledged_at else None,
                    alert.delivery_status.value,
                    alert.delivery_error,
                ),
            )
        return alert

    def find_active_duplicate(self, dedupe_key: str) -> Alert | None:
        self.database.migrate()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM alerts WHERE dedupe_key = ? AND acknowledged_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (dedupe_key,),
            ).fetchone()
        return self._from_row(row) if row else None

    def list_for_reservation(self, reservation_id: UUID) -> list[Alert]:
        self.database.migrate()
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM alerts WHERE reservation_id = ? ORDER BY created_at DESC, id DESC",
                (str(reservation_id),),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def acknowledge(self, alert_id: UUID, acknowledged_at: datetime) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE alerts SET acknowledged_at = ? WHERE id = ?",
                (_utc_iso(acknowledged_at), str(alert_id)),
            )

    def mark_delivery(
        self, alert_id: UUID, status: DeliveryStatus, error: str | None = None
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE alerts SET delivery_status = ?, delivery_error = ? WHERE id = ?",
                (status.value, error, str(alert_id)),
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Alert:
        data = dict(row)
        data.update(
            id=UUID(data["id"]),
            reservation_id=UUID(data["reservation_id"]) if data["reservation_id"] else None,
            price_check_id=UUID(data["price_check_id"]) if data["price_check_id"] else None,
            created_at=datetime.fromisoformat(data["created_at"]),
            acknowledged_at=datetime.fromisoformat(data["acknowledged_at"])
            if data["acknowledged_at"]
            else None,
            metadata=json.loads(data.pop("metadata_json")),
        )
        return Alert.model_validate(data)
