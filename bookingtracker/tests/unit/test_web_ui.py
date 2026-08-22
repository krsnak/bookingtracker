from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from app.browser.models import RemoteDesktopHealth, RemoteDesktopState
from app.config import AppPaths, RemoteDesktopSettings
from app.reservations.models import Reservation
from app.web.app import create_app
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


class FakeRemoteRuntime:
    def __init__(self, assets) -> None:  # noqa: ANN001
        self.settings = RemoteDesktopSettings(enabled=True, novnc_assets_dir=assets)
        self.enabled = True
        self.active = False
        self.display_started = False

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
        denied_open = client.post(
            "/browser/remote/open", data={"csrf_token": app.state.csrf}
        )
        assert denied_open.status_code == 403
        assert (
            client.post("/browser/remote/open", headers=headers, data={}).status_code == 403
        )

        runtime.active = True
        remote = client.get("/browser/remote", headers=headers)
        assert remote.status_code == 200
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
        assert simulated_url == (
            f"wss://ha.local{prefix}/browser/remote/novnc/websockify"
        )
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
        assert response.status_code == 409
        assert app.state.history.latest(reservation.id) is None
