from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html import unescape
from io import BytesIO
from pathlib import Path
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit

import pytest
from app.alerts.models import Alert, AlertSeverity, AlertType
from app.alerts.notifications import HomeAssistantNotificationAdapter
from app.booking.models import RateOffer
from app.browser.models import (
    AuthenticationState,
    BrowserHealth,
    BrowserState,
    RemoteDesktopHealth,
    RemoteDesktopState,
)
from app.config import AppPaths, RemoteDesktopSettings
from app.integrations.home_assistant.remote_desktop import RemoteDesktopError
from app.matching.models import CandidateEvaluation, MatchClassification, MatchResult
from app.pricing.models import (
    CheckDiagnosticPhase,
    CheckReasonCode,
    PriceCheckRecord,
    PriceCheckStatus,
)
from app.reservations.import_document import canonical_booking_hotel_url, pdf_document
from app.reservations.models import Reservation, ReservationCandidate
from app.scheduling.models import CheckTrigger
from app.web.app import create_app, static_asset_revision
from app.web.presentation import (
    format_date,
    format_date_range,
    format_datetime,
    format_money,
    status_label,
)
from app.web.reservation_presentation import (
    check_history_rows,
    group_reservation_cards,
    price_history_view,
    reservation_card_view,
)
from fastapi.testclient import TestClient
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from starlette.websockets import WebSocketDisconnect


class FakeRemoteRuntime:
    def __init__(self, assets) -> None:  # noqa: ANN001
        self.settings = RemoteDesktopSettings(enabled=True, novnc_assets_dir=assets)
        self.enabled = True
        self.active = False
        self.display_started = False
        self.fail_start = False

    @property
    def novnc_assets_dir(self):  # noqa: ANN201
        return self.settings.novnc_assets_dir

    @property
    def session_active(self) -> bool:
        return self.active

    def start_display(self):  # noqa: ANN201
        self.display_started = True
        return self.health()

    def start_session(self):  # noqa: ANN201
        if self.fail_start:
            raise RemoteDesktopError("remote session startup failed")
        self.active = True
        return self.health()

    def stop_session(self):  # noqa: ANN201
        self.active = False
        return self.health()

    def stop_all(self) -> None:
        self.active = False

    def health(self) -> RemoteDesktopHealth:
        return RemoteDesktopHealth(
            state=RemoteDesktopState.SESSION_ACTIVE if self.active else RemoteDesktopState.READY,
            display_running=self.display_started,
            window_manager_running=self.display_started,
            vnc_running=self.active,
            websockify_running=self.active,
            manual_lease_active=self.active,
            error=None,
        )


class FakeBrowserService:
    def __init__(self) -> None:
        self.started = False
        self.start_calls = 0

    def start(self) -> BrowserHealth:
        self.start_calls += 1
        self.started = True
        return self.health()

    def stop(self) -> BrowserHealth:
        self.started = False
        return self.health()

    def health(self) -> BrowserHealth:
        return BrowserHealth(
            state=BrowserState.READY if self.started else BrowserState.STOPPED,
            process_running=self.started,
            context_running=self.started,
            page_available=self.started,
            booking_auth_state=AuthenticationState.UNKNOWN,
            manual_action_required=False,
            last_successful_navigation=None,
            last_error=None,
        )

    def smoke_test(self) -> dict[str, bool]:
        return {"browser": self.started}

    def refresh_state(self) -> BrowserHealth:
        return self.health()


class ManualCheckPipeline:
    def __init__(self, history, status: PriceCheckStatus, error: str | None = None) -> None:  # noqa: ANN001,E501
        self.history = history
        self.status = status
        self.error = error
        self.calls = 0

    def check(self, item, *, run_id: str) -> PriceCheckRecord:  # noqa: ANN001
        self.calls += 1
        return self.history.create(
            PriceCheckRecord(
                reservation_id=item.id,
                run_id=run_id,
                status=self.status,
                error=self.error,
            ),
            [],
        )


class BlockingManualCheckPipeline(ManualCheckPipeline):
    def __init__(self, history) -> None:  # noqa: ANN001
        super().__init__(history, PriceCheckStatus.TIMEOUT)
        self.started = Event()
        self.release = Event()

    def check(self, item, *, run_id: str) -> PriceCheckRecord:  # noqa: ANN001
        self.started.set()
        assert self.release.wait(timeout=2)
        return super().check(item, run_id=run_id)


def checkable_reservation(*, active: bool = True) -> Reservation:
    return Reservation(
        property_name="STORHAUGEN GARD",
        booking_url="https://www.booking.com/hotel/no/example.html",
        check_in=date(2026, 9, 18),
        check_out=date(2026, 9, 19),
        adults=2,
        rooms_count=1,
        room_type="Dvoulůžkový pokoj",
        booked_total_price=Decimal("1320.54"),
        currency="NOK",
        source_text="Sanitized fixture",
        extraction_confidence=1,
        active=active,
    )


def _comparable_check(
    reservation_id,
    current: str,
    *,
    classification: MatchClassification = MatchClassification.EXACT,
    status: PriceCheckStatus = PriceCheckStatus.SUCCESS,
):  # noqa: ANN001
    from app.pricing.models import PriceComparison

    return PriceCheckRecord(
        reservation_id=reservation_id,
        status=status,
        match_classification=classification,
        match_result=MatchResult(
            accepted=True, score=Decimal("1"), classification=classification
        ),
        comparison=PriceComparison(
            comparable=True,
            booked_price=Decimal("32.00"),
            current_price=Decimal(current),
            currency="EUR",
        ),
    )


def test_reservation_card_view_formats_prices_statuses_and_fallback() -> None:
    reservation = checkable_reservation().model_copy(
        update={"booked_total_price": Decimal("32.00"), "currency": "EUR"}
    )
    current = _comparable_check(reservation.id, "29.00")
    card = reservation_card_view(reservation, [current], None, now=datetime(2026, 9, 4, tzinfo=UTC))
    assert card.current_price_label == "29,00 EUR"
    assert card.price_difference_label == "−3,00 EUR · −9,4 %"
    assert card.price_tone == "success"
    assert card.has_image is False and card.image_url is None and card.property_initial == "S"
    assert card.stay_label.endswith("1 noc")

    same = reservation_card_view(reservation, [_comparable_check(reservation.id, "32.00")], None)
    higher = reservation_card_view(reservation, [_comparable_check(reservation.id, "35.00")], None)
    assert same.price_difference_label == "0,00 EUR · 0,0 %" and same.price_tone == "neutral"
    assert higher.price_difference_label == "+3,00 EUR · +9,4 %" and higher.price_tone == "danger"

    unavailable = reservation_card_view(
        reservation,
        [PriceCheckRecord(reservation_id=reservation.id, status=PriceCheckStatus.NO_AVAILABILITY)],
        None,
    )
    assert unavailable.current_price_label is None
    assert unavailable.check_status_label == "Pro termín není dostupné"

    for classification, label in (
        (MatchClassification.EXACT, "Stejný pokoj"),
        (MatchClassification.EQUIVALENT, "Ekvivalentní pokoj"),
        (MatchClassification.BETTER, "Prokazatelně lepší pokoj"),
    ):
        classified = _comparable_check(reservation.id, "29.00", classification=classification)
        classified_card = reservation_card_view(reservation, [classified], None)
        assert classified_card.match_category_label == label
        assert classified_card.current_price_label == "29,00 EUR"


def test_reservation_presentation_requires_accepted_match_for_every_price_surface() -> None:
    reservation = checkable_reservation().model_copy(
        update={"booked_total_price": Decimal("32.00"), "currency": "EUR"}
    )
    unsafe = _comparable_check(reservation.id, "29.00").model_copy(
        update={"match_classification": None, "match_result": None}
    )
    card = reservation_card_view(reservation, [unsafe], None)
    assert card.current_price_label is None
    assert card.price_difference_label is None
    assert card.match_category_label is None
    assert price_history_view(reservation, [unsafe]).empty_label
    assert check_history_rows(reservation, [unsafe])[0].result_label != "Porovnatelná cena"

    invalid = _comparable_check(reservation.id, "29.00").model_copy(
        update={"match_classification": "unknown"}
    )
    assert reservation_card_view(reservation, [invalid], None).current_price_label is None
    assert price_history_view(reservation, [invalid]).empty_label

    malformed_no_match = _comparable_check(
        reservation.id, "29.00", status=PriceCheckStatus.NO_MATCH
    )
    malformed_card = reservation_card_view(reservation, [malformed_no_match], None)
    malformed_history = check_history_rows(reservation, [malformed_no_match])[0]
    assert malformed_card.current_price_label is None
    assert malformed_history.result_label != "Porovnatelná cena"

    earlier_unsafe = _comparable_check(reservation.id, "29.00").model_copy(
        update={"match_classification": None, "match_result": None}
    )
    latest = PriceCheckRecord(reservation_id=reservation.id, status=PriceCheckStatus.NO_MATCH)
    card_with_unsafe_history = reservation_card_view(reservation, [latest, earlier_unsafe], None)
    assert card_with_unsafe_history.previous_price_label is None


def test_reservation_card_view_keeps_last_known_price_explicit_and_groups_months() -> None:
    first = checkable_reservation().model_copy(
        update={
            "check_in": date(2026, 9, 6),
            "check_out": date(2026, 9, 9),
            "booked_total_price": Decimal("32.00"),
            "currency": "EUR",
        }
    )
    prior = _comparable_check(first.id, "29.00").model_copy(
        update={"checked_at": datetime(2026, 9, 1, tzinfo=UTC)}
    )
    latest = PriceCheckRecord(
        reservation_id=first.id,
        status=PriceCheckStatus.AVAILABILITY_UNKNOWN,
        checked_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    card = reservation_card_view(first, [latest, prior], None)
    assert card.current_price_label is None
    assert card.previous_price_label and card.previous_price_label.startswith("Poslední známá cena")
    assert card.check_status_label == "Dostupnost neověřena"
    second = first.model_copy(
        update={"check_in": date(2026, 9, 15), "check_out": date(2026, 9, 16)}
    )
    groups = group_reservation_cards([reservation_card_view(second, [], None), card])
    assert groups[0][0] == "ZÁŘÍ 2026 · 2 rezervací"
    assert [item.reservation.check_in for item in groups[0][1]] == [
        date(2026, 9, 6),
        date(2026, 9, 15),
    ]


def test_price_history_uses_only_safe_same_currency_prices() -> None:
    reservation = checkable_reservation().model_copy(
        update={"booked_total_price": Decimal("32.00"), "currency": "EUR"}
    )
    safe = _comparable_check(reservation.id, "29.00").model_copy(
        update={"checked_at": datetime(2026, 9, 3, tzinfo=UTC)}
    )
    unavailable = PriceCheckRecord(
        reservation_id=reservation.id, status=PriceCheckStatus.NO_AVAILABILITY
    )
    chart = price_history_view(reservation, [unavailable, safe])
    assert len(chart.points) == 1
    assert chart.points[0].price_label == "29,00 EUR"
    assert chart.booked_y is not None and chart.path
    assert price_history_view(reservation, [unavailable]).empty_label


def test_dashboard_add_extract_and_prefixed_routes(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        base_path="/bookingtracker-test",
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
    )
    with TestClient(app, root_path="/bookingtracker-test") as client:
        dashboard = client.get("/bookingtracker-test/")
        assert dashboard.status_code == 200
        assert dashboard.text.count("<head>") == dashboard.text.count("</head>") == 1
        assert dashboard.text.count("<body>") == dashboard.text.count("</body>") == 1
        assert all(
            f'href="/bookingtracker-test/{path}"' in dashboard.text
            for path in ("reservations/new", "alerts", "settings", "browser")
        )
        assert 'aria-label="Hlavní navigace"' in dashboard.text
        assert "BookingTracker" in dashboard.text
        form = client.get("/bookingtracker-test/reservations/new")
        token = app.state.csrf
        extracted = client.post(
            "/bookingtracker-test/reservations/extract",
            data={
                "csrf_token": token,
                "source_text": (
                    "Property: Papaya Hostel\n2026-09-18\n2026-09-19\n2 adults\n"
                    "Economy Triple Room\nTotal price EUR 18.88"
                ),
            },
        )
        assert form.status_code == 200
        assert extracted.status_code == 200
        assert "Kontrola rezervace" in extracted.text
        assert "/bookingtracker-test/reservations/save" in extracted.text
        css_link = re.search(r'<link[^>]+href="([^"]+)"', dashboard.text)[1]
        assert css_link == f"/bookingtracker-test/static/app.css?v={app.state.static_css_revision}"
        app_css = Path(__file__).parents[2] / "app" / "web" / "static" / "app.css"
        assert css_link.endswith(f"?v={static_asset_revision(app_css)}")
        assert client.get("/bookingtracker-test/static/app.css").status_code == 200
        assert "review.css" not in dashboard.text


def test_czech_navigation_back_links_and_presentation_helpers(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    prefix = "/api/hassio_ingress/czech-ui"
    headers = {"X-Ingress-Path": prefix}
    with TestClient(app) as client:
        for path, active in (
            ("/", "Rezervace"),
            ("/reservations/new", "Přidat rezervaci"),
            ("/alerts", "Upozornění"),
            ("/settings", "Nastavení"),
            ("/browser", "Prohlížeč"),
        ):
            response = client.get(path, headers=headers)
            assert response.status_code == 200
            assert response.text.count(prefix) >= 5
            assert response.text.count(prefix + prefix) == 0
            assert f'aria-current="page">{active}</a>' in response.text
        assert f'href="{prefix}/">BookingTracker</a>' in client.get("/", headers=headers).text
        assert (
            f'href="{prefix}/">← Zpět na rezervace</a>'
            in client.get("/settings", headers=headers).text
        )
    assert status_label("ready") == "Připraven"
    assert status_label("parser_error") == "Nepodařilo se přečíst nabídku"
    assert status_label("future_state") == "Neznámý stav"
    assert format_date(date(2026, 8, 26)) == "26. srpna 2026"
    assert format_date_range(date(2026, 8, 26), date(2026, 8, 27)) == "26.–27. srpna 2026"
    assert format_datetime(datetime(2026, 8, 24, 23, 59)) == "24. srpna 2026 23:59"
    assert format_money(Decimal("1320.54"), "NOK") == "1 320,54 NOK"


def test_dashboard_and_detail_render_czech_check_diagnostics_under_ingress(tmp_path) -> None:  # noqa: ANN001,E501
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    stored = app.state.reservations.create(
        Reservation(
            property_name="STORHAUGEN GARD",
            booking_url="https://www.booking.com/hotel/no/example.html",
            check_in=date(2026, 9, 18),
            check_out=date(2026, 9, 19),
            adults=2,
            rooms_count=1,
            room_type="Dvoulůžkový pokoj",
            booked_total_price=Decimal("1320.54"),
            currency="NOK",
            source_text="Sanitized fixture",
            extraction_confidence=1,
            active=True,
        )
    )
    started = datetime(2026, 8, 24, 23, 58, tzinfo=UTC)
    app.state.history.create(
        PriceCheckRecord(
            reservation_id=stored.id,
            checked_at=started,
            started_at=started,
            finished_at=started + timedelta(seconds=2),
            duration_ms=2000,
            status=PriceCheckStatus.PARSER_ERROR,
            reason_code=CheckReasonCode.PARSER_ERROR,
            safe_error_detail="bezpečný detail parseru",
            consecutive_failure_count=3,
            next_check_at=datetime(2026, 8, 25, 5, 35, tzinfo=UTC),
        ),
        [],
    )
    prefix = "/api/hassio_ingress/diagnostics"
    headers = {"X-Ingress-Path": prefix}
    with TestClient(app) as client:
        dashboard = client.get("/", headers=headers)
        detail = client.get(f"/reservations/{stored.id}", headers=headers)
    assert "Kontrolu se nepodařilo dokončit" in dashboard.text
    assert "Další kontrola 25. srpna 2026 v 07:35" in dashboard.text
    assert "parser_error" not in dashboard.text and "timeout" not in dashboard.text
    assert "Poslední kontrola" in detail.text
    assert "Doba trvání: 2 s" in detail.text
    assert "Stránka se načetla, ale nepodařilo se z ní bezpečně přečíst" in detail.text
    assert "Počet po sobě jdoucích neúspěchů: 3" in detail.text
    assert "Zkontrolovat nyní" in detail.text
    assert f'href="{prefix}/">← Zpět na rezervace</a>' in detail.text
    assert f'action="{prefix}/reservations/{stored.id}/check"' in detail.text


def test_detail_renders_availability_unknown_without_failure_claims(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    stored = app.state.reservations.create(checkable_reservation())
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    app.state.history.create(
        PriceCheckRecord(
            reservation_id=stored.id,
            checked_at=now,
            started_at=now,
            finished_at=now,
            status=PriceCheckStatus.AVAILABILITY_UNKNOWN,
            reason_code=CheckReasonCode.AVAILABILITY_UNKNOWN,
            diagnostic_phase=CheckDiagnosticPhase.AVAILABILITY_DETECTION,
            safe_error_detail=(
                "Dostupnost se nepodařilo ověřit. Booking.com pro zadaný termín "
                "nezobrazil nabídky ani potvrzení, že je ubytování vyprodané."
            ),
            consecutive_failure_count=2,
            next_check_at=now + timedelta(hours=2),
        ),
        [],
    )

    with TestClient(app) as client:
        detail = client.get(f"/reservations/{stored.id}")

    assert "Dostupnost se nepodařilo ověřit" in detail.text
    assert (
        "Booking.com pro zadaný termín nezobrazil nabídky ani potvrzení, že je ubytování "
        "vyprodané."
    ) in detail.text
    assert "Stav: availability_unknown" in detail.text
    assert "Důvod: availability_unknown" in detail.text
    assert "Fáze: availability_detection" in detail.text
    assert "Další pokus: 4. 9. 2026 v 14:00" in detail.text
    assert "Počet po sobě jdoucích neúspěchů: 2" in detail.text
    assert "parser_error" not in detail.text
    assert "Nabídku nelze bezpečně porovnat" not in detail.text
    assert "Ubytování je vyprodané" not in detail.text
    assert "reviews-block-availability" not in detail.text
    assert "Playwright" not in detail.text


def test_detail_displays_safe_equivalent_or_better_room_evidence(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    stored = app.state.reservations.create(checkable_reservation())
    offer = RateOffer(
        property_name="STORHAUGEN GARD",
        room_name="Jiný dvoulůžkový pokoj s balkonem",
        normalized_room_name="jiny dvouluzkovy pokoj s balkonem",
        adults=2,
        children=0,
        breakfast_included=True,
        current_price=Decimal("1200"),
        currency="NOK",
        free_cancellation=True,
        taxes_included=True,
        source_row_text="sanitized",
        source_url="https://example.test",
        scrape_timestamp=datetime(2026, 9, 1, tzinfo=UTC),
    )
    evaluation = CandidateEvaluation(
        rate=offer,
        accepted=True,
        score=Decimal("1"),
        classification=MatchClassification.BETTER,
        evidence=["requested occupancy confirmed", "breakfast terms confirmed"],
        objective_differences=["balcony"],
    )
    match = MatchResult(
        accepted=True,
        score=Decimal("1"),
        matched_rate=offer,
        classification=MatchClassification.BETTER,
        candidate_evaluations=[evaluation],
    )
    app.state.history.create(
        PriceCheckRecord(
            reservation_id=stored.id,
            status=PriceCheckStatus.SUCCESS,
            matched=True,
            match_classification=MatchClassification.BETTER,
            match_result=match,
        ),
        [offer],
    )

    with TestClient(app) as client:
        detail = client.get(f"/reservations/{stored.id}")

    assert "Shoda: Lepší nabídka" in detail.text
    assert "Nalezený pokoj: Jiný dvoulůžkový pokoj s balkonem" in detail.text
    assert "Objektivní zlepšení: balcony" in detail.text


def test_detail_renders_legacy_match_result_without_room_facts(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    stored = app.state.reservations.create(checkable_reservation())
    offer = RateOffer(
        property_name="STORHAUGEN GARD",
        room_name="Dvoulůžkový pokoj",
        normalized_room_name="dvouluzkovy pokoj",
        adults=2,
        children=0,
        breakfast_included=True,
        current_price=Decimal("1200"),
        currency="NOK",
        free_cancellation=True,
        taxes_included=True,
        source_row_text="sanitized",
        source_url="https://example.test",
        scrape_timestamp=datetime(2026, 9, 1, tzinfo=UTC),
    )
    match = MatchResult(
        accepted=True,
        score=Decimal("1"),
        matched_rate=offer,
        classification=MatchClassification.EXACT,
        candidate_evaluations=[
            CandidateEvaluation(
                rate=offer,
                accepted=True,
                score=Decimal("1"),
                classification=MatchClassification.EXACT,
            )
        ],
    )
    check = app.state.history.create(
        PriceCheckRecord(
            reservation_id=stored.id,
            status=PriceCheckStatus.SUCCESS,
            matched=True,
            match_classification=MatchClassification.EXACT,
            match_result=match,
        ),
        [offer],
    )
    legacy_match = match.model_dump(mode="json")
    legacy_match["matched_rate"].pop("room_facts")
    legacy_match["candidate_evaluations"][0].pop("diagnostic_index")
    legacy_match["candidate_evaluations"][0].pop("objective_differences")
    legacy_match["candidate_evaluations"][0].pop("evidence")
    legacy_match["candidate_evaluations"][0]["rate"].pop("room_facts")
    with app.state.history.database.transaction() as connection:
        connection.execute(
            "UPDATE price_checks SET match_result_json = ? WHERE id = ?",
            (json.dumps(legacy_match), str(check.id)),
        )

    with TestClient(app) as client:
        detail = client.get(f"/reservations/{stored.id}")

    assert detail.status_code == 200
    assert "Shoda: Přesná shoda" in detail.text
    assert "Nalezený pokoj: Dvoulůžkový pokoj" in detail.text


def test_detail_falls_back_for_pre_diagnostics_history_row(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    stored = app.state.reservations.create(
        Reservation(
            property_name="Legacy Hotel",
            booking_url="https://www.booking.com/hotel/cz/example.html",
            check_in=date(2026, 9, 18),
            check_out=date(2026, 9, 19),
            adults=2,
            rooms_count=1,
            room_type="Pokoj",
            booked_total_price=Decimal("100"),
            currency="EUR",
            source_text="Sanitized fixture",
            extraction_confidence=1,
            active=True,
        )
    )
    app.state.history.create(
        PriceCheckRecord(reservation_id=stored.id, status=PriceCheckStatus.TIMEOUT), []
    )
    with TestClient(app) as client:
        detail = client.get(f"/reservations/{stored.id}")
    assert detail.status_code == 200
    assert "Podrobnosti nejsou k dispozici" in detail.text
    assert "Kontrola vypršela" not in detail.text
    assert "timeout" not in detail.text


def test_raw_english_library_detail_is_only_in_closed_technical_diagnostics(
    tmp_path,
) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    stored = app.state.reservations.create(checkable_reservation())
    started = datetime(2026, 8, 24, 20, tzinfo=UTC)
    app.state.history.create(
        PriceCheckRecord(
            reservation_id=stored.id,
            checked_at=started,
            started_at=started,
            finished_at=started + timedelta(seconds=1),
            duration_ms=1000,
            status=PriceCheckStatus.TIMEOUT,
            reason_code=CheckReasonCode.TIMEOUT,
            safe_error_detail="Locator.inner_text: Timeout 1000ms exceeded",
            consecutive_failure_count=4,
        ),
        [],
    )
    with TestClient(app) as client:
        detail = client.get(f"/reservations/{stored.id}")

    ordinary, technical = detail.text.split("<details", 1)
    assert "Locator.inner_text" not in ordinary
    assert "Kontrolu ceny se nepodařilo dokončit v časovém limitu." in ordinary
    assert "Locator.inner_text" not in technical


def test_detail_hides_superseded_failure_but_alert_history_keeps_manual_ack_state(tmp_path) -> None:  # noqa: ANN001,E501
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    stored = app.state.reservations.create(checkable_reservation())
    failed_at = datetime(2026, 8, 24, 20, tzinfo=UTC)
    failed = app.state.history.create(
        PriceCheckRecord(
            reservation_id=stored.id,
            checked_at=failed_at,
            status=PriceCheckStatus.TIMEOUT,
        ),
        [],
    )
    app.state.alerts.create(
        Alert(
            reservation_id=stored.id,
            price_check_id=failed.id,
            type=AlertType.CHECK_FAILED,
            severity=AlertSeverity.WARNING,
            title="Opakovaně se nepodařilo zkontrolovat cenu",
            message="Historické upozornění",
            dedupe_key=f"check-failed:{stored.id}:timeout",
        )
    )
    app.state.history.create(
        PriceCheckRecord(
            reservation_id=stored.id,
            checked_at=failed_at + timedelta(minutes=1),
            status=PriceCheckStatus.NO_MATCH,
        ),
        [],
    )
    with TestClient(app) as client:
        detail = client.get(f"/reservations/{stored.id}")
        alerts = client.get("/alerts")
        historical_alert = app.state.alerts.list_for_reservation(stored.id)[0]
        acknowledged = client.post(
            f"/alerts/{historical_alert.id}/acknowledge",
            data={"csrf_token": app.state.csrf},
        )
    assert "Historické upozornění" not in detail.text
    assert "Historické upozornění" in alerts.text
    assert "Nepotvrzeno" in alerts.text and "Potvrdit" in alerts.text
    assert acknowledged.status_code == 200
    assert app.state.alerts.get(historical_alert.id).acknowledged_at is not None  # type: ignore[union-attr]


def test_review_uses_czech_read_only_sections_and_preserves_recognized_values(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    source = (
        "Property: Safe Hotel\nPříjezd: 5. září 2026\nOdjezd: 6. září 2026\n"
        "2 dospělí\n1 noc, Economy Triple Room\nCelková cena EUR 18.88"
    )
    with TestClient(app) as client:
        review = client.post(
            "/reservations/extract", data={"csrf_token": app.state.csrf, "source_text": source}
        )
        assert all(label in review.text for label in ("Pobyt", "Podmínky", "Cena"))
        assert "Rozpoznáno" not in review.text and "Upravit údaje" in review.text
        assert 'name="nights"' not in review.text
        token = next(iter(app.state.pending))
        saved = client.post(
            "/reservations/save",
            data={
                "csrf_token": app.state.csrf,
                "token": token,
                "property_name": "Safe Hotel",
                "booking_url": "https://www.booking.com/hotel/test/safe.html",
                "check_in": "2026-09-05",
                "check_out": "2026-09-06",
                "adults": "2",
                "rooms_count": "1",
                "room_type": "Economy Triple Room",
                "booked_total_price": "18.88",
                "currency": "EUR",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
        assert app.state.reservations.list_active()[0].nights == 1


def _grand_hotel_pdf() -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    font_path = next(
        path
        for path in (
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        )
        if path.is_file()
    )
    pdfmetrics.registerFont(TTFont("FixtureUnicode", font_path))
    pdf.setFont("FixtureUnicode", 10)
    pdf.setTitle("Gmail - Grand Hotel Hønefoss - reservation confirmed")
    lines = [
        "Zjistit více",
        "Nikdy vás nepožádáme o platbu mimo Booking.com.",
        "Ubytování Grand Hotel",
        "Hønefoss vás bude očekávat",
        "Grand Hotel Hønefoss",
        "Informace o rezervaci",
        "Arrival: 26. srpna 2026",
        "Departure: 27. srpna 2026",
        "Vaše rezervace: 1 noc, Levný dvoulůžkový pokoj s manželskou postelí",
        "Reservations for: 2 adults",
        "Breakfast included",
        "Podmínky zrušení rezervace",
        "zdarma do 24. srpna 2026 23:59",
        "Informace o ceně",
        "Celková cena NOK 1320.54",
        "Informace o platbě",
        "Plánované platby celkem NOK 1320.54",
        "Celkem zaplaceno NOK 0",
        "Booking.com automaticky strhne částku z Vaší karty",
    ]
    y = 800
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 20
    url = "https://www.booking.com/hotel/no/grand-hotel-honefoss.html?tracking=discard"
    pdf.linkURL(url, (60, y - 5, 320, y + 15), relative=0)
    pdf.drawString(60, y, "Booking.com hotel")
    pdf.showPage()
    pdf.setFont("FixtureUnicode", 10)
    pdf.drawString(60, 800, "Accommodation Grand Hotel Hønefoss will be waiting for you")
    pdf.save()
    return output.getvalue()


def _riad_dar_sirine_layout_pdf() -> bytes:
    """Minimal sanitized PDF layout covering the production import regression."""
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.setTitle("Sanitized Booking import fixture")
    lines = [
        "Your booking is confirmed at Riad Dar Sirine & Palmyra 12.",
        "American Express, Visa, Euro/Mastercard, Diners Club, JCB, Maestro",
        "Payment methods",
        "Payment issued: 2 September 2026",
        "Arrival: 11 September 2026",
        "Departure: 12 September 2026",
        "Your reservation: 1 night, Deluxe Double Room",
        "Reservations for: 2 adults",
        "Breakfast included",
        "Total price EUR 32.88",
    ]
    y = 800
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 20
    pdf.save()
    return output.getvalue()


def _guest_house_cancellation_layout_pdf() -> bytes:
    """Sanitized layout with a Guest House name and cancellation-policy heading."""
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.setTitle("Sanitized Booking import fixture")
    lines = [
        "Your booking is confirmed at Sample Guest House at ",
        "Market Square 12.",
        "Arrival: 14 September 2026",
        "Departure: 15 September 2026",
        "Your reservation: 1 night, Standard Double Room",
        "Reservations for: 2 adults",
        "Breakfast included",
        "Cancellation policy",
        "Free cancellation",
        "Total price EUR 47.08",
    ]
    y = 800
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 20
    pdf.save()
    return output.getvalue()


def _hotel_hyperlink_pdf(
    *,
    duplicate: bool = False,
    rotated: bool = False,
    text: bool = True,
    second_hotel: bool = False,
    gmail_graphics_transform: bool = False,
) -> bytes:
    """Sanitized PDF with a real hotel annotation and unrelated Booking links."""
    output = BytesIO()
    pdf = canvas.Canvas(output)
    if rotated:
        pdf.setPageRotation(90)
    pdf.setTitle("Sanitized Booking import fixture")
    hotel_url = (
        "https://www.booking.com/hotel/ma/comfortable-and-downtown.html"
        "?label=fixture"
    )
    if text:
        if gmail_graphics_transform:
            # Gmail's PDF export draws page content through a scaled/flipped
            # graphics state while /Rect stays in page coordinates.
            pdf.saveState()
            pdf.translate(27.7, 1656.3)
            pdf.scale(0.7, -0.7)
            pdf.drawString(445.5, 701, "Comfortable and Downtown, 1min to beach")
            pdf.restoreState()
        else:
            pdf.drawString(60, 790, "Comfortable and Downtown,")
            pdf.drawString(200, 790, "1min to beach")
    hotel_rect = (362.2, 1124.6, 830.2, 1145.6) if gmail_graphics_transform else (58, 786, 320, 804)
    pdf.linkURL(hotel_url, hotel_rect, relative=0)
    if duplicate:
        pdf.linkURL(hotel_url, hotel_rect, relative=0)
    if second_hotel:
        pdf.drawString(60, 770, "Other Hotel")
        pdf.linkURL(
            "https://www.booking.com/hotel/ma/other-hotel.html",
            (58, 766, 160, 784),
            relative=0,
        )
    lines = [
        "Arrival: 15 September 2026",
        "Departure: 16 September 2026",
        "Your reservation: 1 night, One-Bedroom Apartment",
        "Reservations for: 2 adults",
        "Breakfast included",
        "Cancellation policy",
        "Free cancellation until 13 September 2026 at 23:59",
        "Total price EUR 61.00",
    ]
    y = 750
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 20
    for index, url in enumerate(
        [
            "https://www.booking.com/confirmation?label=fixture",
            "https://www.booking.com/help",
            "https://www.booking.com/payment_transactions?label=fixture",
            "https://www.booking.com/",
            "https://example.test/ad",
        ]
    ):
        link_y = 500 - index * 20
        pdf.drawString(60, link_y, f"Other link {index + 1}")
        pdf.linkURL(url, (58, link_y - 4, 150, link_y + 12), relative=0)
    pdf.save()
    return output.getvalue()


def test_pdf_hotel_hyperlink_identity_is_canonical_and_geometry_scoped(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    pdf = _hotel_hyperlink_pdf()
    document = pdf_document(pdf)
    assert document.uris == ["https://www.booking.com/hotel/ma/comfortable-and-downtown.html"]
    assert len(document.hotel_links) == 1
    assert document.hotel_links[0].visible_text == "Comfortable and Downtown, 1min to beach"

    with TestClient(app) as client:
        response = client.post(
            "/reservations/extract/pdf",
            data={"csrf_token": app.state.csrf},
            files={"pdf": ("confirmation.pdf", pdf, "application/pdf")},
        )
        candidate = next(iter(app.state.pending.values()))

    assert response.status_code == 200
    assert candidate.property_name == "Comfortable and Downtown, 1min to beach"
    assert candidate.booking_url == "https://www.booking.com/hotel/ma/comfortable-and-downtown.html"
    assert candidate.property_name_evidence == "pdf_hotel_link_text"
    assert candidate.booking_url_evidence == "pdf_hotel_link"
    assert (candidate.check_in, candidate.check_out, candidate.nights) == (
        date(2026, 9, 15),
        date(2026, 9, 16),
        1,
    )
    assert (candidate.rooms_count, candidate.adults, candidate.children) == (1, 2, 0)
    assert candidate.room_type == "One-Bedroom Apartment"
    assert candidate.breakfast_included is True
    assert candidate.cancellation_deadline == datetime(2026, 9, 13, 23, 59)
    assert (candidate.booked_total_price, candidate.currency) == (Decimal("61.00"), "EUR")
    assert candidate.missing_critical_fields == []
    assert "label=fixture" not in response.text
    assert "Other link 1" not in response.text and "Other link 5" not in response.text


def test_pdf_hotel_annotation_deduplication_rotation_and_missing_text() -> None:
    duplicate = pdf_document(_hotel_hyperlink_pdf(duplicate=True))
    rotated = pdf_document(_hotel_hyperlink_pdf(rotated=True))
    without_text = pdf_document(_hotel_hyperlink_pdf(text=False))

    assert len(duplicate.hotel_links) == 1
    assert rotated.hotel_links[0].visible_text == "Comfortable and Downtown, 1min to beach"
    assert without_text.uris == ["https://www.booking.com/hotel/ma/comfortable-and-downtown.html"]
    assert without_text.hotel_links[0].visible_text is None


def test_pdf_hotel_link_text_composes_the_gmail_graphics_transform() -> None:
    document = pdf_document(_hotel_hyperlink_pdf(gmail_graphics_transform=True))

    assert document.hotel_links[0].visible_text == "Comfortable and Downtown, 1min to beach"


def test_multiple_pdf_hotel_links_require_manual_identity_review(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    with TestClient(app) as client:
        response = client.post(
            "/reservations/extract/pdf",
            data={"csrf_token": app.state.csrf},
            files={
                "pdf": (
                    "confirmation.pdf",
                    _hotel_hyperlink_pdf(second_hotel=True),
                    "application/pdf",
                )
            },
        )
        candidate = next(iter(app.state.pending.values()))

    assert response.status_code == 200
    assert candidate.property_name is None and candidate.booking_url is None
    assert "property_name" in candidate.missing_critical_fields


def test_only_exact_hotel_paths_are_canonicalized() -> None:
    assert canonical_booking_hotel_url(
        "https://cs.booking.com/hotel/ma/comfortable-and-downtown.en-gb.html?label=fixture#top"
    ) == "https://www.booking.com/hotel/ma/comfortable-and-downtown.html"
    assert canonical_booking_hotel_url("https://www.booking.com/confirmation?label=fixture") is None
    assert canonical_booking_hotel_url("https://www.booking.com/help") is None
    assert canonical_booking_hotel_url("https://www.booking.com/") is None


def test_pdf_upload_pipeline_renders_grand_hotel_and_responsive_review(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    with TestClient(app) as client:
        prefix = "/api/hassio_ingress/review-session"
        response = client.post(
            "/reservations/extract/pdf",
            headers={"X-Ingress-Path": prefix},
            data={"csrf_token": app.state.csrf},
            files={"pdf": ("confirmation.pdf", _grand_hotel_pdf(), "application/pdf")},
        )
        candidate = next(iter(app.state.pending.values()))
        assert response.status_code == 200
        assert candidate.property_name == "Grand Hotel Hønefoss"
        assert candidate.booking_url == "https://www.booking.com/hotel/no/grand-hotel-honefoss.html"
        assert candidate.check_in == date(2026, 8, 26) and candidate.check_out == date(2026, 8, 27)
        assert candidate.nights == 1 and candidate.rooms_count == 1
        assert candidate.adults == 2 and candidate.children == 0
        assert candidate.breakfast_included is True
        assert candidate.free_cancellation is True
        assert str(candidate.cancellation_deadline) == "2026-08-24 23:59:00"
        assert candidate.payment_conditions == "Automatická budoucí platba kartou přes Booking.com"
        assert candidate.booked_total_price == Decimal("1320.54")
        assert candidate.booked_payable_price == Decimal("1320.54")
        assert "Grand Hotel Hønefoss" in response.text and "Rozpoznáno" not in response.text
        assert "Nikdy vás nepožádáme" not in response.text
        assert "Informace o rezervaci" not in response.text
        assert "<details open" not in response.text
        assert response.text.count('class="reservation-review__total"') == 1
        assert 'name="children"' in response.text
        assert "Booking URL:" not in response.text
        links = re.findall(r'<link[^>]+href="([^"]+)"', response.text)
        static_root = Path(__file__).parents[2] / "app" / "web" / "static"
        assert links[0] == (
            f"{prefix}/static/app.css?v={static_asset_revision(static_root / 'app.css')}"
        )
        assert links[1] == (
            f"{prefix}/static/ui.css?v={static_asset_revision(static_root / 'ui.css')}"
        )
        assert links[2] == (
            f"{prefix}/static/review.css?v={static_asset_revision(static_root / 'review.css')}"
        )
        assert all(link.count(prefix) == 1 for link in links)
        assert len(links) == 3
    css = (Path(__file__).parents[2] / "app" / "web" / "static" / "review.css").read_text()
    assert "max-width: 800px" in css and "repeat(2, minmax(0, 1fr))" in css
    assert "grid-template-columns: 1fr" in css
    assert "overflow-x: hidden" in css
    assert "url(" not in css and "ingress" not in css.casefold()
    assert all(
        not line.lstrip().startswith(("body", "main", "section", "input"))
        for line in css.splitlines()
        if "{" in line
    )
    assert "style=" not in response.text


def test_pdf_upload_uses_confirmation_anchor_not_payment_cards_or_issue_date(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    with TestClient(app) as client:
        response = client.post(
            "/reservations/extract/pdf",
            data={"csrf_token": app.state.csrf},
            files={"pdf": ("confirmation.pdf", _riad_dar_sirine_layout_pdf(), "application/pdf")},
        )
        candidate = next(iter(app.state.pending.values()))

    assert response.status_code == 200
    assert candidate.property_name == "Riad Dar Sirine & Palmyra"
    assert (candidate.check_in, candidate.check_out, candidate.nights) == (
        date(2026, 9, 11),
        date(2026, 9, 12),
        1,
    )
    assert (candidate.rooms_count, candidate.adults, candidate.children) == (1, 2, 0)
    assert candidate.room_type == "Deluxe Double Room"
    assert candidate.breakfast_included is True
    assert (candidate.booked_total_price, candidate.currency) == (Decimal("32.88"), "EUR")
    assert candidate.missing_critical_fields == []
    assert "American Express, Visa, Euro/Mastercard, Diners Club, JCB, Maestro" not in response.text
    assert ">2. září 2026<" not in response.text
    assert "Chybí povinné údaje rezervace" not in response.text


def test_pdf_upload_renders_guest_house_cancellation_policy(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    with TestClient(app) as client:
        response = client.post(
            "/reservations/extract/pdf",
            data={"csrf_token": app.state.csrf},
            files={
                "pdf": (
                    "confirmation.pdf",
                    _guest_house_cancellation_layout_pdf(),
                    "application/pdf",
                )
            },
        )
        candidate = next(iter(app.state.pending.values()))

    assert response.status_code == 200
    assert candidate.property_name == "Sample Guest House at Market Square"
    assert (candidate.check_in, candidate.check_out) == (date(2026, 9, 14), date(2026, 9, 15))
    assert candidate.free_cancellation is True
    assert candidate.cancellation_deadline is None
    assert candidate.missing_critical_fields == []
    assert "Bezplatné zrušení" in response.text


@pytest.mark.parametrize(
    ("cancellation_lines", "expected", "forbidden"),
    [
        (["Free cancellation"], "Bezplatné zrušení", "Zdarma do"),
        (
            ["Free cancellation until 13 September 2026 at 23:59"],
            "Zdarma do 13. září 2026 23:59",
            "Rezervace nelze bezplatně zrušit",
        ),
        (["Non-refundable"], "Rezervace nelze bezplatně zrušit", "Bezplatné zrušení"),
        (["Contact the property for conditions"], None, "Bezplatné zrušení"),
    ],
)
def test_review_cancellation_display_is_explicit_and_never_guesses_deadline(
    tmp_path, cancellation_lines, expected, forbidden  # noqa: ANN001
) -> None:
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    source_text = "\n".join(
        [
            "Your booking is confirmed at Sample Guest House",
            "Arrival: 14 September 2026",
            "Departure: 15 September 2026",
            "Your reservation: 1 night, Standard Double Room",
            "Reservations for: 2 adults",
            "Breakfast included",
            "Cancellation policy",
            *cancellation_lines,
            "Price information",
            "Total price EUR 47.08",
        ]
    )
    with TestClient(app) as client:
        response = client.post(
            "/reservations/extract",
            data={"csrf_token": app.state.csrf, "source_text": source_text},
        )
        candidate = next(iter(app.state.pending.values()))

    assert response.status_code == 200
    assert candidate.missing_critical_fields == []
    if expected is None:
        assert candidate.free_cancellation is None
        assert "Zdarma do" not in response.text
    else:
        assert expected in response.text
    assert forbidden not in response.text


def test_browser_alerts_and_validation_error_render(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/browser").status_code == 200
        assert client.get("/alerts").status_code == 200
        response = client.post(
            "/reservations/extract",
            data={"csrf_token": app.state.csrf, "source_text": "Booking confirmation"},
        )
        token = next(key for key in app.state.pending)
        invalid = client.post(
            "/reservations/save",
            data={
                "csrf_token": app.state.csrf,
                "token": token,
                "property_name": "",
                "booking_url": "",
            },
        )
        assert response.status_code == 200
        assert "Chybí povinné údaje rezervace" in invalid.text


def test_invalid_import_dates_render_safe_review_and_cannot_be_saved(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    raw_text = (
        "Property: Safe Hotel\n2026-09-06\n2026-09-05\n2 adults\n"
        "Economy Triple Room\nTotal price EUR 18.88\nPIN: synthetic-pin"
    )
    with TestClient(app) as client:
        response = client.post(
            "/reservations/extract",
            data={"csrf_token": app.state.csrf, "source_text": raw_text},
        )
        assert response.status_code == 200
        assert "Kontrola rezervace" in response.text
        assert "Nepodařilo se spolehlivě určit datum příjezdu a odjezdu." in response.text
        assert "synthetic-pin" not in response.text
        token = next(key for key in app.state.pending)
        saved = client.post(
            "/reservations/save",
            data={
                "csrf_token": app.state.csrf,
                "token": token,
                "property_name": "Safe Hotel",
                "booking_url": "https://www.booking.com/hotel/test/safe.html",
            },
        )
        assert saved.status_code == 200
        assert "Chybí povinné údaje rezervace" in saved.text
        assert app.state.reservations.list_active() == []


def test_web_catches_expected_pydantic_extraction_validation_without_echoing_input(
    tmp_path,
) -> None:  # noqa: ANN001
    class InvalidExtractor:
        def extract(self, source_text: str) -> ReservationCandidate:
            return ReservationCandidate(
                check_in=date(2026, 9, 5),
                check_out=date(2026, 9, 5),
                source_text=source_text,
                extraction_confidence=0,
            )

    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    app.state.extractor = InvalidExtractor()
    with TestClient(app) as client:
        response = client.post(
            "/reservations/extract",
            data={"csrf_token": app.state.csrf, "source_text": "PIN: synthetic-pin"},
        )

    assert response.status_code == 422
    assert "Nepodařilo se spolehlivě vytěžit potvrzení." in response.text
    assert "synthetic-pin" not in response.text


def test_notification_test_uses_saved_entity_with_ingress_prg_and_no_side_effects(tmp_path) -> None:  # noqa: ANN001,E501
    sent: list[tuple[str, dict[str, object]]] = []
    adapter = HomeAssistantNotificationAdapter(
        lambda: "notify.roman", transport=lambda path, payload: sent.append((path, payload))
    )
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
        notification_adapter=adapter,
    )
    app.state.settings.set_notify_entity("notify.roman")
    prefix = "/api/hassio_ingress/test-notification"
    headers = {"X-Ingress-Path": prefix}
    with TestClient(app) as client:
        settings = client.get("/settings", headers=headers)
        assert f'action="{prefix}/settings/notifications/test"' in settings.text
        assert "Odeslat testovací upozornění" in settings.text
        assert client.post("/settings/notifications/test", headers=headers).status_code == 403
        response = client.post(
            "/settings/notifications/test",
            headers=headers,
            data={"csrf_token": app.state.csrf, "home_assistant_notify_entity": "notify.attacker"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"{prefix}/settings"
        assert sent == [
            (
                "/services/notify/send_message",
                {
                    "entity_id": "notify.roman",
                    "title": "✅ BookingTracker test",
                    "message": "Home Assistant notification adapter is working.\n"
                    "This is a test message; no price drop was detected.",
                },
            )
        ]
        result = client.get("/settings", headers=headers)
        assert "Testovací upozornění bylo odesláno." in result.text
        assert (
            "Testovací upozornění bylo odesláno."
            not in client.get("/settings", headers=headers).text
        )
        assert app.state.reservations.list_active() == []
        with app.state.history.database.transaction() as connection:
            assert connection.execute("SELECT count(*) FROM price_checks").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM alerts").fetchone()[0] == 0
            assert (
                connection.execute("SELECT count(*) FROM price_drop_band_states").fetchone()[0] == 0
            )


def test_notification_test_handles_missing_invalid_and_sanitized_errors(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    with TestClient(app) as client:
        missing = client.post(
            "/settings/notifications/test",
            data={"csrf_token": app.state.csrf},
            follow_redirects=True,
        )
        assert "Nejprve uložte platnou entitu upozornění Home Assistant." in missing.text
        app.state.settings.set_notify_entity("bad.entity")
        invalid = client.post(
            "/settings/notifications/test",
            data={"csrf_token": app.state.csrf},
            follow_redirects=True,
        )
        assert "Nejprve uložte platnou entitu upozornění Home Assistant." in invalid.text

    failing = HomeAssistantNotificationAdapter(
        lambda: "notify.roman",
        transport=lambda path, payload: (_ for _ in ()).throw(
            RuntimeError("http://user:secret@example.invalid?token=secret")
        ),
    )
    app = create_app(
        paths=AppPaths(tmp_path / "other-data", tmp_path / "logs"),
        start_browser_on_startup=False,
        notification_adapter=failing,
    )
    app.state.settings.set_notify_entity("notify.roman")
    with TestClient(app) as client:
        failed = client.post(
            "/settings/notifications/test",
            data={"csrf_token": app.state.csrf},
            follow_redirects=True,
        )
        assert "Test notification failed." in failed.text
        assert "user:secret@example.invalid" not in failed.text


def test_home_assistant_ingress_prefix_applies_to_links_forms_redirects_and_static(
    tmp_path,
) -> None:  # noqa: ANN001,E501
    """HA strips the path before proxying and supplies it in X-Ingress-Path."""
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    prefix = "/api/hassio_ingress/real-session-token"
    headers = {"X-Ingress-Path": prefix, "X-Forwarded-Host": "ha.local"}
    expected_revision = static_asset_revision(
        Path(__file__).parents[2] / "app" / "web" / "static" / "app.css"
    )
    reservation = app.state.reservations.create(
        Reservation(
            property_name="Papaya Hostel",
            booking_url="https://www.booking.com/hotel/ma/moroccan-friends-guesthouse.html",
            check_in=date(2026, 9, 18),
            check_out=date(2026, 9, 19),
            adults=2,
            rooms_count=1,
            room_type="Economy Triple Room",
            booked_total_price=Decimal("18.88"),
            currency="EUR",
            source_text="Sanitized fixture text",
            extraction_confidence=1,
            active=True,
        )
    )
    with TestClient(app) as client:
        dashboard = client.get("/", headers=headers)
        assert dashboard.status_code == 200
        for target in ("/reservations/new", "/alerts", "/browser"):
            assert f'"{prefix}{target}"' in dashboard.text
        css_link = re.search(r'<link[^>]+href="([^"]+)"', dashboard.text)[1]
        parsed_css = urlsplit(css_link)
        assert parsed_css.path == f"{prefix}/static/app.css"
        assert parsed_css.path.count(prefix) == 1
        assert parse_qs(parsed_css.query) == {"v": [expected_revision]}
        assert app.state.static_css_revision == expected_revision
        assert "real-session-token" not in app.state.static_css_revision
        assert 'href="/browser"' not in dashboard.text
        assert "http://localhost/browser" not in dashboard.text
        assert client.get("/static/app.css", headers=headers).status_code == 200

        browser = client.get("/browser", headers=headers)
        assert f'action="{prefix}/browser/smoke"' in browser.text
        assert f'action="{prefix}/browser/start"' in browser.text
        assert f'action="{prefix}/browser/stop"' in browser.text
        smoke = client.post(
            "/browser/smoke",
            headers=headers,
            data={"csrf_token": app.state.csrf},
        )
        assert smoke.status_code == 200

        detail = client.get(f"/reservations/{reservation.id}", headers=headers)
        assert f'href="{prefix}/reservations/{reservation.id}/edit"' in detail.text
        assert f'action="{prefix}/reservations/{reservation.id}/check"' in detail.text
        assert f'action="{prefix}/reservations/{reservation.id}/toggle"' in detail.text
        redirect = client.post(
            f"/reservations/{reservation.id}/toggle",
            headers=headers,
            data={"csrf_token": app.state.csrf},
            follow_redirects=False,
        )
        assert redirect.headers["location"] == f"{prefix}/reservations/{reservation.id}"


def test_direct_root_mode_does_not_add_an_ingress_prefix(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert 'href="/browser"' in response.text
        assert 'href="/reservations/new"' in response.text
        assert f'href="/static/app.css?v={app.state.static_css_revision}"' in response.text


def test_static_asset_revision_changes_with_content(tmp_path) -> None:  # noqa: ANN001
    stylesheet = tmp_path / "app.css"
    stylesheet.write_text("body{color:#000}")
    first = static_asset_revision(stylesheet)
    stylesheet.write_text("body{color:#111}")
    second = static_asset_revision(stylesheet)

    assert len(first) == 12
    assert first != second


def test_remote_desktop_requires_ha_ingress_and_uses_dynamic_prefix(tmp_path) -> None:  # noqa: ANN001
    assets = tmp_path / "novnc"
    assets.mkdir()
    (assets / "vnc.html").write_text("safe noVNC fixture")
    runtime = FakeRemoteRuntime(assets)
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
        remote_runtime=runtime,  # type: ignore[arg-type]
    )
    prefix = "/api/hassio_ingress/session-token"
    headers = {"X-Hass-Source": "core.ingress", "X-Ingress-Path": prefix}
    with TestClient(app) as client:
        assert runtime.display_started
        assert client.get("/browser/remote").status_code == 403
        assert client.get("/browser/remote/novnc/vnc.html").status_code == 403
        with pytest.raises(WebSocketDisconnect) as denied_socket:
            with client.websocket_connect("/browser/remote/novnc/websockify"):
                pass
        assert denied_socket.value.code == 1008
        denied_open = client.post("/browser/remote/open", data={"csrf_token": app.state.csrf})
        assert denied_open.status_code == 403
        assert client.post("/browser/remote/open", headers=headers, data={}).status_code == 403

        runtime.active = True
        remote = client.get("/browser/remote", headers=headers)
        assert remote.status_code == 200
        assert 'class="remote-desktop-frame"' in remote.text
        assert "<iframe" in remote.text and "style=" not in remote.text
        iframe_source = unescape(re.search(r'<iframe[^>]+src="([^"]+)"', remote.text)[1])
        parsed_iframe = urlsplit(iframe_source)
        assert parsed_iframe.path == f"{prefix}/browser/remote/novnc/vnc.html"
        assert parsed_iframe.path.count(prefix) == 1
        assert parsed_iframe.query == ""
        assert iframe_source.split("#", maxsplit=1)[0] == (
            f"{prefix}/browser/remote/novnc/vnc.html"
        )
        assert ".." not in iframe_source
        websocket_path = parse_qs(parsed_iframe.fragment)["path"][0]
        assert websocket_path == f"{prefix.lstrip('/')}/browser/remote/novnc/websockify"
        assert websocket_path.count(prefix.lstrip("/")) == 1
        assert not websocket_path.startswith("/")
        simulated_url = "wss://ha.local/" + websocket_path
        assert simulated_url == (f"wss://ha.local{prefix}/browser/remote/novnc/websockify")
        routes = app.router.routes
        websocket_index = next(
            index
            for index, route in enumerate(routes)
            if getattr(route, "path", None) == "/browser/remote/novnc/websockify"
        )
        static_index = next(
            index
            for index, route in enumerate(routes)
            if getattr(route, "path", None) == "/browser/remote/novnc"
        )
        assert websocket_index < static_index
        assert client.get("/browser/remote/novnc/vnc.html", headers=headers).status_code == 200
    stylesheet = (Path(__file__).parents[2] / "app" / "web" / "static" / "app.css").read_text()
    assert ".remote-desktop-frame{" in stylesheet
    assert "width:100%" in stylesheet
    assert "max-width:1280px" in stylesheet
    assert "aspect-ratio:16/9" in stylesheet


def test_browser_start_opens_an_ingress_only_manual_remote_session(tmp_path) -> None:  # noqa: ANN001
    assets = tmp_path / "novnc"
    assets.mkdir()
    (assets / "vnc.html").write_text("safe noVNC fixture")
    runtime = FakeRemoteRuntime(assets)
    browser = FakeBrowserService()
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
        remote_runtime=runtime,  # type: ignore[arg-type]
        browser_service=browser,  # type: ignore[arg-type]
    )
    prefix = "/api/hassio_ingress/session-token"
    headers = {"X-Hass-Source": "core.ingress", "X-Ingress-Path": prefix}
    with TestClient(app) as client:
        denied = client.post("/browser/start", data={"csrf_token": app.state.csrf})
        assert denied.status_code == 403
        assert not browser.started
        assert not app.state.manual_lease.active

        opened = client.post(
            "/browser/start",
            headers=headers,
            data={"csrf_token": app.state.csrf},
            follow_redirects=False,
        )
        assert opened.status_code == 303
        assert opened.headers["location"] == f"{prefix}/browser/remote"
        assert browser.start_calls == 1
        assert runtime.session_active
        assert app.state.manual_lease.active

        remote = client.get("/browser/remote", headers=headers)
        assert remote.status_code == 200
        iframe_source = unescape(re.search(r'<iframe[^>]+src="([^"]+)"', remote.text)[1])
        assert parse_qs(urlsplit(iframe_source).fragment)["path"] == [
            f"{prefix.lstrip('/')}/browser/remote/novnc/websockify"
        ]

        ended = client.post(
            "/browser/remote/end",
            headers=headers,
            data={"csrf_token": app.state.csrf},
            follow_redirects=False,
        )
        assert ended.status_code == 303
        assert not runtime.session_active
        assert not app.state.manual_lease.active


def test_failed_manual_remote_start_releases_lease(tmp_path) -> None:  # noqa: ANN001
    assets = tmp_path / "novnc"
    assets.mkdir()
    (assets / "vnc.html").write_text("safe noVNC fixture")
    runtime = FakeRemoteRuntime(assets)
    runtime.fail_start = True
    browser = FakeBrowserService()
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
        remote_runtime=runtime,  # type: ignore[arg-type]
        browser_service=browser,  # type: ignore[arg-type]
    )
    headers = {
        "X-Hass-Source": "core.ingress",
        "X-Ingress-Path": "/api/hassio_ingress/session-token",
    }
    with TestClient(app) as client:
        response = client.post(
            "/browser/start", headers=headers, data={"csrf_token": app.state.csrf}
        )
        assert browser.started
        assert not runtime.session_active
        assert not app.state.manual_lease.active

    assert response.status_code == 503


@pytest.mark.parametrize(
    ("status", "flash"),
    [
        (PriceCheckStatus.SUCCESS, "Kontrola ceny byla dokončena."),
        (
            PriceCheckStatus.NO_MATCH,
            "Kontrola byla dokončena, ale nebyla nalezena bezpečně porovnatelná nabídka.",
        ),
        (
            PriceCheckStatus.TIMEOUT,
            "Kontrolu ceny se nepodařilo dokončit. Podrobnosti jsou uvedeny níže.",
        ),
        (
            PriceCheckStatus.PARSER_ERROR,
            "Kontrolu ceny se nepodařilo dokončit. Podrobnosti jsou uvedeny níže.",
        ),
        (
            PriceCheckStatus.LOGGED_OUT,
            "Pro pokračování je nutné přihlášení na Booking.com.",
        ),
        (
            PriceCheckStatus.CAPTCHA_REQUIRED,
            "Booking.com vyžaduje ruční ověření CAPTCHA.",
        ),
    ],
)
def test_check_now_persists_result_logs_once_and_shows_czech_flash(
    tmp_path, caplog, status, flash
) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
    )
    stored = app.state.reservations.create(checkable_reservation())
    pipeline = ManualCheckPipeline(app.state.history, status)
    app.state.runner.checks = pipeline
    logger = logging.getLogger("bookingtracker.checks")
    logger.addHandler(caplog.handler)
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/reservations/{stored.id}/check",
                data={"csrf_token": app.state.csrf},
            )
    finally:
        logger.removeHandler(caplog.handler)

    assert response.status_code == 200
    assert flash in response.text
    persisted = app.state.history.latest(stored.id)
    assert persisted is not None and persisted.status is status
    assert persisted.started_at is not None and persisted.finished_at is not None
    assert persisted.duration_ms is not None and persisted.next_check_at is not None
    assert persisted.consecutive_failure_count == (
        0 if status in {PriceCheckStatus.SUCCESS, PriceCheckStatus.NO_MATCH} else 1
    )
    records = [record for record in caplog.records if record.name == "bookingtracker.checks"]
    assert len(records) == 1
    payload = json.loads(records[0].message)
    assert payload["event"] == "booking_check_completed"
    assert payload["trigger"] == "manual"
    assert payload["started_at"] == persisted.started_at.isoformat()
    assert "property_name" not in payload
    assert "reservation_id" not in payload
    assert payload["status"] == status.value
    assert payload["reason_code"] == (
        persisted.reason_code.value if persisted.reason_code else None
    )


def test_check_now_sanitizes_stdout_and_does_not_create_price_drop_on_failure(
    tmp_path, caplog
) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
    )
    stored = app.state.reservations.create(checkable_reservation())
    app.state.runner.checks = ManualCheckPipeline(
        app.state.history,
        PriceCheckStatus.PARSER_ERROR,
        "PIN: 1234 token=secret guest@example.com <html>private</html>",
    )
    logger = logging.getLogger("bookingtracker.checks")
    logger.addHandler(caplog.handler)
    try:
        with TestClient(app) as client:
            client.post(
                f"/reservations/{stored.id}/check",
                data={"csrf_token": app.state.csrf},
            )
    finally:
        logger.removeHandler(caplog.handler)
    logged = "\n".join(
        record.message for record in caplog.records if record.name == "bookingtracker.checks"
    ).casefold()
    assert "booking_check_completed" in logged
    assert all(
        value not in logged for value in ("1234", "secret", "guest@example", "<html>", "private")
    )
    assert not any(
        alert.type is AlertType.PRICE_DROP
        for alert in app.state.alerts.list_for_reservation(stored.id)
    )


def test_check_now_rejects_invalid_csrf_missing_and_inactive_reservations(tmp_path) -> None:  # noqa: ANN001,E501
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
    )
    inactive = app.state.reservations.create(checkable_reservation(active=False))
    with TestClient(app) as client:
        denied = client.post(f"/reservations/{inactive.id}/check", data={"csrf_token": "invalid"})
        get_attempt = client.get(f"/reservations/{inactive.id}/check")
        missing = client.post(
            "/reservations/00000000-0000-0000-0000-000000000001/check",
            data={"csrf_token": app.state.csrf},
        )
        inactive_response = client.post(
            f"/reservations/{inactive.id}/check", data={"csrf_token": app.state.csrf}
        )
    assert denied.status_code == 403
    assert get_attempt.status_code == 405
    assert missing.status_code == 404
    assert inactive_response.status_code == 200
    assert "Neaktivní rezervaci nelze zkontrolovat." in inactive_response.text
    assert app.state.history.latest(inactive.id) is None


def test_check_now_returns_immediately_when_shared_runner_is_busy(tmp_path) -> None:  # noqa: ANN001,E501
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
    )
    stored = app.state.reservations.create(checkable_reservation())
    pipeline = BlockingManualCheckPipeline(app.state.history)
    app.state.runner.checks = pipeline
    running = Thread(target=lambda: app.state.runner.run_check(stored.id, CheckTrigger.SCHEDULER))
    running.start()
    assert pipeline.started.wait(timeout=1)
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/reservations/{stored.id}/check",
                data={"csrf_token": app.state.csrf},
            )
        assert response.status_code == 200
        assert "Kontrola této rezervace právě probíhá." in response.text
        assert pipeline.calls == 0
    finally:
        pipeline.release.set()
        running.join(timeout=2)
    assert not running.is_alive()
    assert pipeline.calls == 1


def test_check_now_uses_ingress_aware_redirect(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
    )
    stored = app.state.reservations.create(checkable_reservation())
    app.state.runner.checks = ManualCheckPipeline(app.state.history, PriceCheckStatus.SUCCESS)
    prefix = "/api/hassio_ingress/manual-check"
    with TestClient(app) as client:
        response = client.post(
            f"/reservations/{stored.id}/check",
            headers={"X-Ingress-Path": prefix},
            data={"csrf_token": app.state.csrf},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == f"{prefix}/reservations/{stored.id}"


def test_manual_lease_rejects_check_now_without_persisting_history(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    reservation = app.state.reservations.create(
        Reservation(
            property_name="Papaya Hostel",
            check_in=date(2026, 9, 18),
            check_out=date(2026, 9, 19),
            adults=2,
            rooms_count=1,
            room_type="Economy Triple Room",
            booked_total_price=Decimal("18.88"),
            currency="EUR",
            source_text="Sanitized fixture text",
            extraction_confidence=1,
            active=True,
        )
    )
    assert app.state.manual_lease.acquire()
    with TestClient(app) as client:
        response = client.post(
            f"/reservations/{reservation.id}/check", data={"csrf_token": app.state.csrf}
        )
        assert response.status_code == 200
        assert (
            "Automatickou kontrolu nelze spustit během otevřené vzdálené relace." in response.text
        )
        assert app.state.history.latest(reservation.id) is None
