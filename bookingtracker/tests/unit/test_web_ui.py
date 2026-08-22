from __future__ import annotations

from app.config import AppPaths
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
