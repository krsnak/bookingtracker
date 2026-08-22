from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.config import AppPaths
from app.reservations.models import Reservation
from app.web.app import create_app
from fastapi.testclient import TestClient


def test_dashboard_add_extract_and_prefixed_routes(tmp_path) -> None:  # noqa: ANN001
    app = create_app(
        base_path="/bookingtracker-test",
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"),
        start_browser_on_startup=False,
    )
    with TestClient(app, root_path="/bookingtracker-test") as client:
        dashboard = client.get("/bookingtracker-test/")
        assert dashboard.status_code == 200
        assert 'href="/bookingtracker-test/reservations/new"' in dashboard.text
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
        assert "Review reservation" in extracted.text
        assert "/bookingtracker-test/reservations/save" in extracted.text
        assert client.get("/bookingtracker-test/static/app.css").status_code == 200


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
        assert "Required fields are missing" in invalid.text


def test_home_assistant_ingress_prefix_applies_to_links_forms_redirects_and_static(tmp_path) -> None:  # noqa: ANN001,E501
    """HA strips the path before proxying and supplies it in X-Ingress-Path."""
    app = create_app(
        paths=AppPaths(tmp_path / "data", tmp_path / "logs"), start_browser_on_startup=False
    )
    prefix = "/api/hassio_ingress/real-session-token"
    headers = {"X-Ingress-Path": prefix, "X-Forwarded-Host": "ha.local"}
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
        for target in ("/reservations/new", "/alerts", "/browser", "/static/app.css"):
            assert f'"{prefix}{target}"' in dashboard.text
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
