from __future__ import annotations

import sqlite3
from datetime import timedelta
from decimal import Decimal

import pytest
from app.db.connection import SQLiteDatabase
from app.db.repository import PriceCheckRepository, ReservationRepository
from app.pricing.models import PriceCheckRecord, PriceCheckStatus
from test_exact_reservation_matcher import rate, reservation


def test_migrations_enable_foreign_keys_and_reservation_round_trips(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "history.db")
    reservations = ReservationRepository(database)
    original = reservation(
        booked_payable_price=Decimal("17.77"),
        taxes_and_fees=Decimal("1.11"),
        vat=Decimal("0.55"),
        city_tax=Decimal("0.56"),
        meal_plan=None,
        cancellation_text=None,
    )

    stored = reservations.create(original)
    loaded = reservations.get(stored.id)

    assert loaded is not None
    assert loaded.booked_total_price == Decimal("18.88")
    assert loaded.booked_payable_price == Decimal("17.77")
    assert loaded.taxes_and_fees == Decimal("1.11")
    assert loaded.vat == Decimal("0.55")
    assert loaded.city_tax == Decimal("0.56")
    assert loaded.meal_plan is None
    assert loaded.source_text == "sanitized reservation"
    assert loaded.created_at.utcoffset() == timedelta(0)
    with database.transaction() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO price_checks (id, reservation_id, run_id, checked_at, status, "
                "matched, comparison_reasons_json, comparison_warnings_json, warnings_json) "
                "VALUES ('bad', 'missing', 'run', '2026-01-01T00:00:00+00:00', "
                "'success', 0, '[]', '[]', '[]')"
            )


def test_active_lifecycle_and_immutable_check_snapshots(tmp_path) -> None:  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "history.db")
    reservation_repository = ReservationRepository(database)
    history = PriceCheckRepository(database)
    stored = reservation_repository.create(reservation(active=True))
    first = PriceCheckRecord(reservation_id=stored.id, status=PriceCheckStatus.NO_MATCH)
    second = PriceCheckRecord(reservation_id=stored.id, status=PriceCheckStatus.PARSER_ERROR)

    history.create(first, [rate(current_price=Decimal("20.00"))])
    history.create(second, [])
    deactivated = reservation_repository.deactivate(stored.id)
    checks = history.list_for_reservation(stored.id)

    assert deactivated.active is False
    assert reservation_repository.list_active() == []
    assert [check.status for check in checks] == [
        PriceCheckStatus.PARSER_ERROR,
        PriceCheckStatus.NO_MATCH,
    ]
    assert checks[1].rate_offers[0].current_price == Decimal("20.00")
    assert history.latest_comparable(stored.id) is None
