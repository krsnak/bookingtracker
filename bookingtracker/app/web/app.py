"""FastAPI factory; routes remain relative to a configurable base path."""
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import hashlib
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.alerts.notifications import (
    ConsoleNotificationAdapter,
    HomeAssistantNotificationAdapter,
    NotificationAdapter,
    sanitize_notification_error,
)
from app.alerts.service import AlertService
from app.booking.parser import BookingRateParser
from app.browser.executor import ThreadBoundBookingBrowser
from app.browser.lease import ManualBrowserLease
from app.browser.service import BookingBrowserService
from app.config import AppPaths, BrowserSettings, RemoteDesktopSettings
from app.db.connection import SQLiteDatabase
from app.db.repository import (
    AlertRepository,
    PriceCheckRepository,
    PriceDropBandStateRepository,
    ReservationRepository,
    ScheduleStateRepository,
    SettingsRepository,
)
from app.integrations.home_assistant.remote_desktop import RemoteDesktopError, RemoteDesktopRuntime
from app.matching.matcher import ExactReservationMatcher
from app.pricing.check_service import PriceCheckService
from app.pricing.service import ComparablePriceService
from app.reservations.extractor import ReservationExtractor
from app.reservations.models import Reservation
from app.scheduling.models import CheckTrigger
from app.scheduling.policy import SchedulePolicy
from app.scheduling.service import CheckRunner, ReservationScheduler
from app.web.websocket_bridge import bridge_websocket_frames

ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def static_asset_revision(path: Path) -> str:
    """Return a short deterministic content revision without request metadata."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _normalized_prefix(value: str | None) -> str:
    """Return a safe proxy mount prefix, or an empty string for direct access."""
    if not value:
        return ""
    prefix = value.strip()
    if not prefix.startswith("/") or prefix.startswith("//"):
        return ""
    if any(character in prefix for character in "\r\n?#"):
        return ""
    return "/" + prefix.strip("/") if prefix.strip("/") else ""


def _request_prefix(request: Request, configured_base_path: str) -> str:
    """Prefer Home Assistant's per-session ingress prefix over static configuration."""
    return _normalized_prefix(
        request.headers.get("x-ingress-path")
        or request.headers.get("x-forwarded-prefix")
        or request.scope.get("root_path")
        or configured_base_path
    )


def _has_home_assistant_ingress(request: Request) -> bool:
    raw_prefix = request.headers.get("x-ingress-path")
    normalized = _normalized_prefix(raw_prefix)
    return (
        request.headers.get("x-hass-source") == "core.ingress"
        and bool(normalized)
        and raw_prefix is not None
        and raw_prefix.rstrip("/") == normalized
    )


def create_app(
    *,
    base_path: str = "",
    paths: AppPaths | None = None,
    runner: CheckRunner | None = None,
    start_browser_on_startup: bool = True,
    remote_runtime: RemoteDesktopRuntime | None = None,
    notification_adapter: NotificationAdapter | None = None,
) -> FastAPI:
    base_path = "/" + base_path.strip("/") if base_path.strip("/") else ""
    resolved_paths = paths or AppPaths.from_environment()
    database = SQLiteDatabase(resolved_paths.database_path)
    reservations = ReservationRepository(database)
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    settings = SettingsRepository(database)
    lease = ManualBrowserLease()
    remote = remote_runtime or RemoteDesktopRuntime(RemoteDesktopSettings.from_environment(), lease)
    browser = ThreadBoundBookingBrowser(
        BookingBrowserService(BrowserSettings.development(resolved_paths))
    )
    notifier = notification_adapter or (
        HomeAssistantNotificationAdapter(settings.get_notify_entity)
        if __import__("os").environ.get("SUPERVISOR_TOKEN")
        else ConsoleNotificationAdapter()
    )
    actual_runner = runner or CheckRunner(
        reservations,
        PriceCheckService(
            browser,
            BookingRateParser(),
            ExactReservationMatcher(),
            ComparablePriceService(),
            history,
        ),
        ScheduleStateRepository(database),
        SchedulePolicy(),
        AlertService(
            alerts,
            history,
            notifier,
            settings,
            PriceDropBandStateRepository(database),
        ),
        manual_session_active=lambda: lease.active,
    )
    scheduler = ReservationScheduler(actual_runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.migrate()
        try:
            remote.start_display()
            if start_browser_on_startup:
                browser_health = browser.start()
                if remote.enabled and not browser_health.context_running:
                    raise RuntimeError("persistent browser context failed to start")
        except Exception:
            remote.stop_all()
            raise
        app.state.scheduler_running = True
        app.state.stop_event = asyncio.Event()

        async def poll() -> None:
            while not app.state.stop_event.is_set():
                if browser.health().context_running:
                    scheduler.run_due()
                try:
                    await asyncio.wait_for(app.state.stop_event.wait(), timeout=60)
                except TimeoutError:
                    pass

        task = asyncio.create_task(poll())
        yield
        app.state.scheduler_running = False
        app.state.stop_event.set()
        scheduler.stop()
        await task
        remote.stop_session()
        browser.shutdown()
        remote.stop_all()

    app = FastAPI(root_path=base_path, lifespan=lifespan)
    app.state.base_path = base_path
    app.state.reservations = reservations
    app.state.history = history
    app.state.alerts = alerts
    app.state.settings = settings
    app.state.notification_adapter = notifier
    app.state.settings_flash: str | None = None
    app.state.browser = browser
    app.state.runner = actual_runner
    app.state.scheduler = scheduler
    app.state.manual_lease = lease
    app.state.remote = remote
    app.state.extractor = ReservationExtractor()
    app.state.csrf = secrets.token_urlsafe(24)
    app.state.pending: dict[str, object] = {}
    app.state.static_css_revision = static_asset_revision(ROOT / "static" / "app.css")
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.middleware("http")
    async def protect_remote_desktop(request: Request, call_next):  # noqa: ANN202
        if remote.enabled and request.url.path.startswith("/browser/remote"):
            if not _has_home_assistant_ingress(request):
                return PlainTextResponse("Home Assistant Ingress required", status_code=403)
            if request.url.path.startswith("/browser/remote/novnc") and not remote.session_active:
                return PlainTextResponse("No manual remote session is active", status_code=409)
        return await call_next(request)

    def url_for_request(request: Request, name: str, **params: str) -> str:
        return f"{_request_prefix(request, base_path)}{app.url_path_for(name, **params)}"

    def render(request: Request, name: str, **context: object):
        def static_url(path: str) -> str:
            asset_url = url_for_request(request, "static", path=path)
            if path == "app.css":
                return f"{asset_url}?v={app.state.static_css_revision}"
            return asset_url

        return templates.TemplateResponse(
            request,
            name,
            {
                "url": lambda route_name, **params: url_for_request(request, route_name, **params),
                "static_url": static_url,
                "csrf": app.state.csrf,
                **context,
            },
        )

    def csrf(token: str) -> None:
        if not secrets.compare_digest(token, app.state.csrf):
            raise HTTPException(403, "Invalid form token")

    def require_remote_ingress(request: Request) -> None:
        if not remote.enabled:
            raise HTTPException(404, "Remote desktop is unavailable in this runtime")
        if not _has_home_assistant_ingress(request):
            raise HTTPException(403, "Home Assistant Ingress required")

    @app.get("/", name="dashboard")
    def dashboard(request: Request):
        items = []
        for item in reservations.list_active():
            latest = history.latest(item.id)
            state = actual_runner.schedules.get(item.id)
            items.append({"reservation": item, "latest": latest, "schedule": state})
        return render(
            request,
            "dashboard.html",
            items=items,
            browser=browser.health(),
            scheduler_running=app.state.scheduler_running,
        )

    @app.get("/health", name="health")
    def health():
        return {"status": "ok", "browser": browser.health().state}

    @app.get("/reservations/new", name="new_reservation")
    def new_reservation(request: Request):
        return render(request, "new.html")

    @app.get("/settings", name="settings_view")
    def settings_view(request: Request):
        flash, app.state.settings_flash = app.state.settings_flash, None
        return render(request, "settings.html", settings=settings, flash=flash)

    @app.post("/settings", name="update_settings")
    def update_settings(
        request: Request,
        csrf_token: str = Form(),
        price_drop_threshold_percent: str = Form(""),
        home_assistant_notify_entity: str = Form(""),
    ):
        csrf(csrf_token)
        try:
            threshold = Decimal(price_drop_threshold_percent)
            if not Decimal("0") < threshold <= Decimal("100"):
                raise ValueError("Threshold must be greater than 0 and at most 100")
            entity = home_assistant_notify_entity.strip()
            if entity and not entity.startswith("notify."):
                raise ValueError("Home Assistant notify entity must start with notify.")
        except (ValueError, ArithmeticError) as error:
            return render(request, "settings.html", settings=settings, error=str(error))
        settings.set_price_drop_threshold(threshold)
        settings.set_notify_entity(entity or None)
        return RedirectResponse(url_for_request(request, "settings_view"), status_code=303)

    @app.post("/settings/notifications/test", name="test_notification")
    async def test_notification(request: Request, csrf_token: str = Form("")):
        csrf(csrf_token)
        entity = settings.get_notify_entity()
        adapter = app.state.notification_adapter
        if not entity or not entity.startswith("notify."):
            app.state.settings_flash = "A valid Home Assistant notify entity must be saved first."
        elif not isinstance(adapter, HomeAssistantNotificationAdapter):
            app.state.settings_flash = (
                "Home Assistant notification delivery is unavailable in this runtime."
            )
        else:
            try:
                await asyncio.to_thread(
                    adapter.send,
                    "✅ BookingTracker test",
                    "Home Assistant notification adapter is working.\n"
                    "This is a test message; no price drop was detected.",
                )
            except Exception as error:
                app.state.settings_flash = sanitize_notification_error(error)
            else:
                app.state.settings_flash = "Test notification sent successfully."
        return RedirectResponse(url_for_request(request, "settings_view"), status_code=303)

    @app.post("/reservations/extract", name="extract_reservation")
    def extract_reservation(request: Request, source_text: str = Form(), csrf_token: str = Form()):
        csrf(csrf_token)
        candidate = app.state.extractor.extract(source_text)
        token = secrets.token_urlsafe(12)
        app.state.pending[token] = candidate
        return render(request, "review.html", candidate=candidate, token=token)

    @app.post("/reservations/save", name="save_reservation")
    def save_reservation(
        request: Request,
        token: str = Form(),
        property_name: str = Form(""),
        booking_url: str = Form(""),
        price_drop_threshold_percent: str = Form(""),
        csrf_token: str = Form(),
    ):
        csrf(csrf_token)
        candidate = app.state.pending.pop(token, None)
        if candidate is None:
            raise HTTPException(400, "Review session expired; extract the confirmation again.")
        candidate = candidate.model_copy(
            update={
                "property_name": property_name or None,
                "booking_url": booking_url or None,
                "price_drop_threshold_percent": Decimal(price_drop_threshold_percent)
                if price_drop_threshold_percent
                else None,
            }
        )
        if not candidate.can_activate:
            return render(
                request,
                "review.html",
                candidate=candidate,
                token=token,
                error="Required fields are missing.",
            )
        saved = reservations.create(Reservation(**candidate.model_dump(), active=True))
        return RedirectResponse(
            url_for_request(request, "reservation_detail", reservation_id=str(saved.id)),
            status_code=303,
        )

    @app.get("/reservations/{reservation_id}", name="reservation_detail")
    def reservation_detail(request: Request, reservation_id: str):
        item = reservations.get(UUID(reservation_id))
        if item is None:
            raise HTTPException(404, "Reservation not found")
        return render(
            request,
            "detail.html",
            reservation=item,
            checks=history.list_for_reservation(item.id),
            alerts=alerts.list_for_reservation(item.id),
            schedule=actual_runner.schedules.get(item.id),
        )

    @app.get("/reservations/{reservation_id}/edit", name="edit_reservation")
    def edit_reservation(request: Request, reservation_id: str):
        item = reservations.get(UUID(reservation_id))
        if item is None:
            raise HTTPException(404, "Reservation not found")
        return render(request, "edit.html", reservation=item)

    @app.post("/reservations/{reservation_id}/edit", name="update_reservation")
    def update_reservation(
        request: Request,
        reservation_id: str,
        csrf_token: str = Form(),
        property_name: str = Form(""),
        booking_url: str = Form(""),
        check_in: str = Form(""),
        check_out: str = Form(""),
        adults: str = Form(""),
        children: str = Form(""),
        rooms_count: str = Form(""),
        room_type: str = Form(""),
        meal_plan: str = Form(""),
        breakfast_included: str = Form(""),
        cancellation_text: str = Form(""),
        free_cancellation: str = Form(""),
        cancellation_deadline: str = Form(""),
        booked_total_price: str = Form(""),
        booked_payable_price: str = Form(""),
        booked_base_price: str = Form(""),
        taxes_and_fees: str = Form(""),
        currency: str = Form(""),
        payment_conditions: str = Form(""),
        price_drop_threshold_percent: str = Form(""),
    ):
        csrf(csrf_token)
        item = reservations.get(UUID(reservation_id))
        if item is None:
            raise HTTPException(404, "Reservation not found")
        try:
            data = item.model_dump() | {
                "property_name": property_name or None,
                "booking_url": booking_url or None,
                "check_in": date.fromisoformat(check_in) if check_in else None,
                "check_out": date.fromisoformat(check_out) if check_out else None,
                "adults": int(adults) if adults else None,
                "children": int(children) if children else None,
                "rooms_count": int(rooms_count) if rooms_count else None,
                "room_type": room_type or None,
                "meal_plan": meal_plan or None,
                "breakfast_included": {"true": True, "false": False}.get(breakfast_included),
                "cancellation_text": cancellation_text or None,
                "free_cancellation": {"true": True, "false": False}.get(free_cancellation),
                "cancellation_deadline": cancellation_deadline or None,
                "booked_total_price": Decimal(booked_total_price) if booked_total_price else None,
                "booked_payable_price": Decimal(booked_payable_price)
                if booked_payable_price
                else None,
                "booked_base_price": Decimal(booked_base_price) if booked_base_price else None,
                "taxes_and_fees": Decimal(taxes_and_fees) if taxes_and_fees else None,
                "currency": currency or None,
                "payment_conditions": payment_conditions or None,
                "price_drop_threshold_percent": Decimal(price_drop_threshold_percent)
                if price_drop_threshold_percent
                else None,
            }
            candidate = Reservation.model_validate(data)
        except (ValidationError, ValueError) as error:
            return render(request, "edit.html", reservation=item, error=str(error))
        reservations.update(candidate)
        return RedirectResponse(
            url_for_request(request, "reservation_detail", reservation_id=reservation_id),
            status_code=303,
        )

    @app.post("/reservations/{reservation_id}/check", name="check_now")
    def check_now(request: Request, reservation_id: str, csrf_token: str = Form()):
        csrf(csrf_token)
        if lease.active:
            raise HTTPException(409, "Manual remote session is active; end it before checking.")
        actual_runner.run_check(UUID(reservation_id), CheckTrigger.MANUAL)
        return RedirectResponse(
            url_for_request(request, "reservation_detail", reservation_id=reservation_id),
            status_code=303,
        )

    @app.post("/reservations/{reservation_id}/toggle", name="toggle_reservation")
    def toggle_reservation(request: Request, reservation_id: str, csrf_token: str = Form()):
        csrf(csrf_token)
        item = reservations.get(UUID(reservation_id))
        if item is None:
            raise HTTPException(404, "Reservation not found")
        reservations.update(item.model_copy(update={"active": not item.active}))
        return RedirectResponse(
            url_for_request(request, "reservation_detail", reservation_id=reservation_id),
            status_code=303,
        )

    @app.get("/alerts", name="alerts_view")
    def alerts_view(request: Request):
        all_alerts = [
            alert
            for item in reservations.list_active()
            for alert in alerts.list_for_reservation(item.id)
        ]
        return render(request, "alerts.html", alerts=all_alerts)

    @app.post("/alerts/{alert_id}/acknowledge", name="ack_alert")
    def ack_alert(request: Request, alert_id: str, csrf_token: str = Form()):
        csrf(csrf_token)
        alerts.acknowledge(
            UUID(alert_id), __import__("datetime").datetime.now(__import__("datetime").UTC)
        )
        return RedirectResponse(url_for_request(request, "alerts_view"), status_code=303)

    @app.post("/alerts/{alert_id}/retry", name="retry_alert")
    def retry_alert(request: Request, alert_id: str, csrf_token: str = Form()):
        csrf(csrf_token)
        actual_runner.alerts.retry(UUID(alert_id))
        return RedirectResponse(url_for_request(request, "alerts_view"), status_code=303)

    @app.get("/browser", name="browser_status")
    def browser_status(request: Request):
        return render(request, "browser.html", health=browser.health(), remote=remote.health())

    @app.post("/browser/smoke", name="browser_smoke")
    def browser_smoke(request: Request, csrf_token: str = Form()):
        csrf(csrf_token)
        if lease.active:
            raise HTTPException(
                409, "Manual remote session is active; end it before browser actions."
            )
        return render(
            request,
            "browser.html",
            health=browser.health(),
            remote=remote.health(),
            smoke=browser.smoke_test(),
        )

    @app.post("/browser/start", name="browser_start")
    def browser_start(request: Request, csrf_token: str = Form()):
        csrf(csrf_token)
        if lease.active:
            raise HTTPException(
                409, "Manual remote session is active; end it before browser actions."
            )
        browser.start()
        return RedirectResponse(url_for_request(request, "browser_status"), status_code=303)

    @app.post("/browser/stop", name="browser_stop")
    def browser_stop(request: Request, csrf_token: str = Form()):
        csrf(csrf_token)
        if lease.active:
            raise HTTPException(
                409, "Manual remote session is active; end it before browser actions."
            )
        browser.stop()
        return RedirectResponse(url_for_request(request, "browser_status"), status_code=303)

    @app.post("/browser/remote/open", name="remote_open")
    def remote_open(request: Request, csrf_token: str = Form("")):
        require_remote_ingress(request)
        csrf(csrf_token)
        if not actual_runner.begin_manual_session(lease.acquire):
            raise HTTPException(409, "A manual remote session is already active")
        try:
            if not browser.health().context_running:
                raise RemoteDesktopError("browser context is not active")
            remote.start_session()
        except RemoteDesktopError as error:
            lease.release()
            raise HTTPException(503, str(error)) from error
        return RedirectResponse(url_for_request(request, "remote_desktop"), status_code=303)

    @app.post("/browser/remote/end", name="remote_end")
    def remote_end(request: Request, csrf_token: str = Form("")):
        require_remote_ingress(request)
        csrf(csrf_token)
        try:
            remote.stop_session()
            browser.refresh_state()
        finally:
            lease.release()
        return RedirectResponse(url_for_request(request, "browser_status"), status_code=303)

    @app.get("/browser/remote", name="remote_desktop")
    def remote_desktop(request: Request):
        require_remote_ingress(request)
        if not remote.session_active:
            raise HTTPException(409, "No manual remote session is active")
        websocket_path = url_for_request(request, "remote_websockify").lstrip("/")
        novnc_fragment = urlencode(
            {
                "autoconnect": "1",
                "reconnect": "0",
                "resize": "scale",
                "shared": "1",
                "path": websocket_path,
            }
        )
        return render(
            request,
            "remote_desktop.html",
            health=remote.health(),
            novnc_fragment=novnc_fragment,
        )

    @app.websocket("/browser/remote/novnc/websockify", name="remote_websockify")
    async def remote_websockify(websocket: WebSocket) -> None:
        if not remote.enabled or not _has_home_assistant_ingress(websocket):
            await websocket.close(code=1008)
            return
        if not remote.session_active:
            await websocket.close(code=1013)
            return
        await websocket.accept()
        try:
            from websockets.asyncio.client import connect

            async with connect(
                f"ws://127.0.0.1:{remote.settings.websockify_port}", max_size=16 * 1024 * 1024
            ) as upstream:
                await bridge_websocket_frames(websocket, upstream)
        except WebSocketDisconnect:
            pass
        except Exception:
            with suppress(RuntimeError):
                await websocket.close(code=1011)

    # Register this mount after the WebSocket child route so Starlette does not
    # let StaticFiles consume the upgrade request.
    if remote.enabled:
        app.mount(
            "/browser/remote/novnc",
            StaticFiles(directory=str(remote.novnc_assets_dir)),
            name="remote_novnc",
        )

    return app
