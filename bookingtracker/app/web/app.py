"""FastAPI factory; routes remain relative to a configurable base path."""
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import hashlib
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
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
from app.alerts.service import AlertService, check_failed_is_superseded
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
from app.presentation import (
    check_reason_for,
    check_reason_text,
    check_result_text,
    enum_value,
    format_check_datetime,
    format_duration,
    manual_check_flash,
    visible_safe_error_detail,
)
from app.pricing.check_service import PriceCheckService
from app.pricing.service import ComparablePriceService
from app.reservations.extractor import ReservationExtractor
from app.reservations.import_document import ImportDocumentError, pdf_document
from app.reservations.models import Reservation
from app.scheduling.models import CheckRunBlockReason, CheckTrigger
from app.scheduling.policy import SchedulePolicy
from app.scheduling.service import CheckRunner, ReservationScheduler
from app.web.presentation import (
    format_bool,
    format_date,
    format_date_range,
    format_datetime,
    format_money,
    status_label,
)
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
    app.state.reservation_flash: dict[str, str] = {}
    app.state.browser = browser
    app.state.runner = actual_runner
    app.state.scheduler = scheduler
    app.state.manual_lease = lease
    app.state.remote = remote
    app.state.extractor = ReservationExtractor()
    app.state.csrf = secrets.token_urlsafe(24)
    app.state.pending: dict[str, object] = {}
    app.state.static_css_revisions = {
        name: static_asset_revision(ROOT / "static" / name)
        for name in ("app.css", "ui.css", "review.css")
    }
    app.state.static_css_revision = app.state.static_css_revisions["app.css"]
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

    def render(request: Request, name: str, *, status_code: int = 200, **context: object):
        def static_url(path: str) -> str:
            asset_url = url_for_request(request, "static", path=path)
            if revision := app.state.static_css_revisions.get(path):
                return f"{asset_url}?v={revision}"
            return asset_url

        return templates.TemplateResponse(
            request,
            name,
            {
                "url": lambda route_name, **params: url_for_request(request, route_name, **params),
                "static_url": static_url,
                "csrf": app.state.csrf,
                "current_route": getattr(request.scope.get("route"), "name", ""),
                "status_label": status_label,
                "format_date": format_date,
                "format_date_range": format_date_range,
                "format_datetime": format_datetime,
                "format_money": format_money,
                "format_bool": format_bool,
                "check_reason_for": check_reason_for,
                "check_reason_text": check_reason_text,
                "check_result_text": check_result_text,
                "format_check_datetime": format_check_datetime,
                "format_duration": format_duration,
                "enum_value": enum_value,
                "visible_safe_error_detail": visible_safe_error_detail,
                **context,
            },
            status_code=status_code,
        )

    def csrf(token: str) -> None:
        if not secrets.compare_digest(token, app.state.csrf):
            raise HTTPException(403, "Invalid form token")

    @app.exception_handler(HTTPException)
    async def handled_http_error(request: Request, error: HTTPException):
        messages = {
            400: "Požadavek nelze bezpečně zpracovat.",
            403: "Tato akce není povolena.",
            404: "Požadovaná stránka nebo rezervace nebyla nalezena.",
            409: "Akci nyní nelze provést.",
            503: "Služba je dočasně nedostupná.",
        }
        return render(
            request,
            "error.html",
            status_code=error.status_code,
            message=messages.get(error.status_code, "Došlo k neočekávanému problému."),
        )

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
                raise ValueError("Hranice musí být vyšší než 0 a nejvýše 100")
            entity = home_assistant_notify_entity.strip()
            if entity and not entity.startswith("notify."):
                raise ValueError("Entita upozornění Home Assistant musí začínat notify.")
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
            app.state.settings_flash = "Nejprve uložte platnou entitu upozornění Home Assistant."
        elif not isinstance(adapter, HomeAssistantNotificationAdapter):
            app.state.settings_flash = (
                "Doručování upozornění Home Assistant není v tomto prostředí k dispozici."
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
                app.state.settings_flash = "Testovací upozornění bylo odesláno."
        return RedirectResponse(url_for_request(request, "settings_view"), status_code=303)

    @app.post("/reservations/extract", name="extract_reservation")
    def extract_reservation(request: Request, source_text: str = Form(), csrf_token: str = Form()):
        csrf(csrf_token)
        try:
            candidate = app.state.extractor.extract(source_text)
        except ValidationError:
            return render(
                request,
                "new.html",
                status_code=422,
                error=(
                    "Nepodařilo se spolehlivě vytěžit potvrzení. "
                    "Zkontrolujte vložené údaje a zkuste to znovu."
                ),
            )
        token = secrets.token_urlsafe(12)
        app.state.pending[token] = candidate
        return render(request, "review.html", candidate=candidate, token=token)

    @app.post("/reservations/extract/pdf", name="extract_reservation_pdf")
    async def extract_reservation_pdf(
        request: Request,
        pdf: Annotated[UploadFile, File()],
        csrf_token: Annotated[str, Form()],
    ):
        csrf(csrf_token)
        try:
            # Read at most one byte beyond the limit: uploads never reach disk or /data.
            from app.reservations.import_document import MAX_PDF_BYTES

            contents = await pdf.read(MAX_PDF_BYTES + 1)
            candidate = app.state.extractor.extract_document(pdf_document(contents))
        except (ImportDocumentError, ValidationError):
            return render(
                request,
                "new.html",
                status_code=422,
                error="PDF potvrzení nelze bezpečně zpracovat.",
            )
        finally:
            await pdf.close()
        token = secrets.token_urlsafe(12)
        app.state.pending[token] = candidate
        return render(request, "review.html", candidate=candidate, token=token)

    @app.post("/reservations/save", name="save_reservation")
    async def save_reservation(
        request: Request,
        token: str = Form(),
        csrf_token: str = Form(),
    ):
        csrf(csrf_token)
        candidate = app.state.pending.get(token)
        if candidate is None:
            raise HTTPException(400, "Review session expired; extract the confirmation again.")
        form = await request.form()
        def value(name: str) -> str | None:
            raw = str(form.get(name, "")).strip()
            return raw or None
        def integer(name: str) -> int | None:
            raw = value(name)
            return int(raw) if raw else None
        def money(name: str) -> Decimal | None:
            raw = value(name)
            return Decimal(raw) if raw else None
        try:
            data = candidate.model_dump() | {
                "property_name": value("property_name"), "booking_url": value("booking_url"),
                "check_in": date.fromisoformat(value("check_in")) if value("check_in") else None,
                "check_out": date.fromisoformat(value("check_out")) if value("check_out") else None,
                "nights": (
                    (date.fromisoformat(value("check_out")) - date.fromisoformat(value("check_in"))).days
                    if value("check_in") and value("check_out")
                    else candidate.nights
                ),
                "adults": integer("adults"), "children": integer("children"),
                "rooms_count": integer("rooms_count"), "room_type": value("room_type"),
                "meal_plan": value("meal_plan"),
                "breakfast_included": {"yes": True, "no": False}.get(value("breakfast_included") or ""),
                "free_cancellation": {"yes": True, "no": False}.get(value("free_cancellation") or ""),
                "cancellation_text": (
                    value("cancellation_text")
                    if "cancellation_text" in form
                    else candidate.cancellation_text
                ),
                "cancellation_deadline": datetime.fromisoformat(value("cancellation_deadline")) if value("cancellation_deadline") else None,
                "payment_conditions": value("payment_conditions"), "currency": value("currency"),
                "booked_total_price": money("booked_total_price"), "booked_payable_price": money("booked_payable_price"),
                "booked_base_price": money("booked_base_price"), "taxes_and_fees": money("taxes_and_fees"),
                "vat": money("vat"), "city_tax": money("city_tax"),
                "price_drop_threshold_percent": money("price_drop_threshold_percent"),
            }
            candidate = candidate.__class__.model_validate(data)
        except (ValidationError, ValueError, ArithmeticError):
            return render(request, "review.html", candidate=candidate, token=token, error="Neplatné údaje ve formuláři.")
        from app.reservations.validator import validate_activation
        validation = validate_activation(candidate)
        candidate = candidate.model_copy(update={"missing_critical_fields": validation.missing_fields, "validation_errors": validation.errors})
        if not candidate.can_activate:
            return render(
                request,
                "review.html",
                candidate=candidate,
                token=token,
                error="Chybí povinné údaje rezervace.",
            )
        app.state.pending.pop(token, None)
        saved = reservations.create(Reservation(**candidate.model_dump(), active=True))
        app.state.reservation_flash[str(saved.id)] = "Rezervace byla úspěšně uložena."
        return RedirectResponse(
            url_for_request(request, "reservation_detail", reservation_id=str(saved.id)),
            status_code=303,
        )

    @app.get("/reservations/{reservation_id}", name="reservation_detail")
    def reservation_detail(request: Request, reservation_id: str):
        item = reservations.get(UUID(reservation_id))
        if item is None:
            raise HTTPException(404, "Reservation not found")
        flash = app.state.reservation_flash.pop(reservation_id, None)
        checks_for_detail = history.list_for_reservation(item.id)
        return render(
            request,
            "detail.html",
            reservation=item,
            checks=checks_for_detail,
            alerts=[
                alert
                for alert in alerts.list_for_reservation(item.id)
                if not check_failed_is_superseded(alert, checks_for_detail)
            ],
            schedule=actual_runner.schedules.get(item.id),
            flash=flash,
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
        try:
            item_id = UUID(reservation_id)
        except ValueError as error:
            raise HTTPException(404, "Rezervace nebyla nalezena.") from error
        outcome = (
            None
            if lease.active
            else actual_runner.try_run_check(item_id, CheckTrigger.MANUAL)
        )
        if outcome is None:
            flash = (
                "Automatickou kontrolu nelze spustit během otevřené vzdálené relace."
            )
        elif outcome.record is not None:
            flash = manual_check_flash(outcome.record)
        elif outcome.blocked_reason is CheckRunBlockReason.BUSY:
            flash = "Kontrola této rezervace právě probíhá."
        elif outcome.blocked_reason is CheckRunBlockReason.MANUAL_SESSION_ACTIVE:
            flash = (
                "Automatickou kontrolu nelze spustit během otevřené vzdálené relace."
            )
        elif outcome.blocked_reason is CheckRunBlockReason.RESERVATION_INACTIVE:
            flash = "Neaktivní rezervaci nelze zkontrolovat."
        else:
            raise HTTPException(404, "Rezervace nebyla nalezena.")
        app.state.reservation_flash[reservation_id] = flash
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
