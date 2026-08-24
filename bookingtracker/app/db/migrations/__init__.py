"""Ordered, immutable SQLite schema migrations."""
# ruff: noqa: E501

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE reservations (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            property_name TEXT,
            booking_url TEXT,
            check_in TEXT,
            check_out TEXT,
            nights INTEGER,
            adults INTEGER,
            children INTEGER,
            children_ages_json TEXT,
            rooms_count INTEGER,
            room_type TEXT,
            rooms_breakdown_json TEXT,
            meal_plan TEXT,
            breakfast_included INTEGER,
            cancellation_text TEXT,
            free_cancellation INTEGER,
            cancellation_deadline TEXT,
            booked_total_price TEXT,
            booked_payable_price TEXT,
            booked_base_price TEXT,
            taxes_and_fees TEXT,
            vat TEXT,
            city_tax TEXT,
            currency TEXT,
            payment_conditions TEXT,
            source_text TEXT NOT NULL,
            extraction_confidence TEXT NOT NULL,
            field_confidence_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL,
            active INTEGER NOT NULL CHECK (active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX reservations_active_check_in_idx ON reservations(active, check_in);

        CREATE TABLE price_checks (
            id TEXT PRIMARY KEY,
            reservation_id TEXT NOT NULL REFERENCES reservations(id) ON DELETE RESTRICT,
            run_id TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            status TEXT NOT NULL,
            parser_status TEXT,
            matched INTEGER NOT NULL CHECK (matched IN (0, 1)),
            match_classification TEXT,
            match_score TEXT,
            comparable INTEGER,
            price_basis TEXT,
            booked_comparable_price TEXT,
            current_comparable_price TEXT,
            currency TEXT,
            delta_amount TEXT,
            delta_percent TEXT,
            direction TEXT,
            comparison_reasons_json TEXT NOT NULL,
            comparison_warnings_json TEXT NOT NULL,
            match_result_json TEXT,
            error TEXT,
            warnings_json TEXT NOT NULL
        );
        CREATE INDEX price_checks_reservation_checked_idx
            ON price_checks(reservation_id, checked_at DESC);

        CREATE TABLE rate_offer_snapshots (
            id TEXT PRIMARY KEY,
            price_check_id TEXT NOT NULL REFERENCES price_checks(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            offer_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(price_check_id, ordinal)
        );
        CREATE INDEX rate_offer_snapshots_check_idx ON rate_offer_snapshots(price_check_id, ordinal);
        """,
    ),
    (
        2,
        """
        CREATE TABLE schedule_states (
            reservation_id TEXT PRIMARY KEY REFERENCES reservations(id) ON DELETE CASCADE,
            next_check_at TEXT NOT NULL,
            last_check_at TEXT,
            last_success_at TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX schedule_states_due_idx ON schedule_states(next_check_at);

        CREATE TABLE alerts (
            id TEXT PRIMARY KEY,
            reservation_id TEXT REFERENCES reservations(id) ON DELETE SET NULL,
            price_check_id TEXT REFERENCES price_checks(id) ON DELETE SET NULL,
            type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            acknowledged_at TEXT,
            delivery_status TEXT NOT NULL,
            delivery_error TEXT
        );
        CREATE INDEX alerts_active_dedupe_idx
            ON alerts(dedupe_key, acknowledged_at, created_at DESC);
        CREATE INDEX alerts_reservation_created_idx
            ON alerts(reservation_id, created_at DESC);
        """,
    ),
    (
        3,
        """
        ALTER TABLE reservations ADD COLUMN price_drop_threshold_percent TEXT;

        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE price_drop_band_states (
            reservation_id TEXT PRIMARY KEY REFERENCES reservations(id) ON DELETE CASCADE,
            threshold_percent TEXT NOT NULL,
            highest_notified_band INTEGER NOT NULL,
            highest_observed_percent TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        4,
        """
        ALTER TABLE reservations ADD COLUMN property_aliases_json TEXT NOT NULL DEFAULT '[]';
        """,
    ),
    (5, """ALTER TABLE price_checks ADD COLUMN started_at TEXT;
ALTER TABLE price_checks ADD COLUMN finished_at TEXT;
ALTER TABLE price_checks ADD COLUMN duration_ms INTEGER;
ALTER TABLE price_checks ADD COLUMN reason_code TEXT;
ALTER TABLE price_checks ADD COLUMN safe_error_detail TEXT;
ALTER TABLE price_checks ADD COLUMN consecutive_failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE price_checks ADD COLUMN next_check_at TEXT;"""),
]
