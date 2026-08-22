"""FastAPI factory; routes remain relative to a configurable base path."""
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.alerts.notifications import ConsoleNotificationAdapter
from app.alerts.service import AlertService
from app.booking.parser import BookingRateParser
from app.browser.executor import ThreadBoundBookingBrowser
from app.browser.service import BookingBrowserService
from app.config import AppPaths, BrowserSettings
from app.db.connection import SQLiteDatabase
from app.db.repository import (
    AlertRepository,
    PriceCheckRepository,
    ReservationRepository,
    ScheduleStateRepository,
)
from app.matching.matcher import ExactReservationMatcher
from app.pricing.check_service import PriceCheckService
from app.pricing.service import ComparablePriceService
from app.reservations.extractor import ReservationExtractor
from app.reservations.models import Reservation
from app.scheduling.models import CheckTrigger
from app.scheduling.policy import SchedulePolicy
from app.scheduling.service import CheckRunner, ReservationScheduler

ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def create_app(
    *,
    base_path: str = "",
    paths: AppPaths | None = None,
    runner: CheckRunner | None = None,
    start_browser_on_startup: bool = True,
) -> FastAPI:
    base_path = "/" + base_path.strip("/") if base_path.strip("/") else ""
    resolved_paths = paths or AppPaths.from_environment()
    database = SQLiteDatabase(resolved_paths.database_path)
    reservations = ReservationRepository(database)
    history = PriceCheckRepository(database)
    alerts = AlertRepository(database)
    browser = ThreadBoundBookingBrowser(
        BookingBrowserService(BrowserSettings.development(resolved_paths))
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
        AlertService(alerts, history, ConsoleNotificationAdapter()),
    )
    scheduler = ReservationScheduler(actual_runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.migrate()
        if start_browser_on_startup:
            browser.start()
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
        browser.shutdown()

    app = FastAPI(root_path=base_path, lifespan=lifespan)
    app.state.base_path = base_path
    app.state.reservations = reservations
    app.state.history = history
    app.state.alerts = alerts
    app.state.browser = browser
    app.state.runner = actual_runner
    app.state.scheduler = scheduler
    app.state.extractor = ReservationExtractor()
    app.state.csrf = secrets.token_urlsafe(24)
    app.state.pending: dict[str, object] = {}
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    def url(name: str, **params: str) -> str:
        return f"{base_path}{app.url_path_for(name, **params)}"

    def render(request: Request, name: str, **context: object):
        return templates.TemplateResponse(
            request, name, {"url": url, "csrf": app.state.csrf, **context}
        )

    def csrf(token: str) -> None:
        if not secrets.compare_digest(token, app.state.csrf):
            raise HTTPException(403, "Invalid form token")

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
        csrf_token: str = Form(),
    ):
        csrf(csrf_token)
        candidate = app.state.pending.pop(token, None)
        if candidate is None:
            raise HTTPException(400, "Review session expired; extract the confirmation again.")
        candidate = candidate.model_copy(
            update={"property_name": property_name or None, "booking_url": booking_url or None}
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
            url("reservation_detail", reservation_id=str(saved.id)), status_code=303
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
            }
            candidate = Reservation.model_validate(data)
        except (ValidationError, ValueError) as error:
            return render(request, "edit.html", reservation=item, error=str(error))
        reservations.update(candidate)
        return RedirectResponse(
            url("reservation_detail", reservation_id=reservation_id), status_code=303
        )

    @app.post("/reservations/{reservation_id}/check", name="check_now")
    def check_now(reservation_id: str, csrf_token: str = Form()):
        csrf(csrf_token)
        actual_runner.run_check(UUID(reservation_id), CheckTrigger.MANUAL)
        return RedirectResponse(
            url("reservation_detail", reservation_id=reservation_id), status_code=303
        )

    @app.post("/reservations/{reservation_id}/toggle", name="toggle_reservation")
    def toggle_reservation(reservation_id: str, csrf_token: str = Form()):
        csrf(csrf_token)
        item = reservations.get(UUID(reservation_id))
        if item is None:
            raise HTTPException(404, "Reservation not found")
        reservations.update(item.model_copy(update={"active": not item.active}))
        return RedirectResponse(
            url("reservation_detail", reservation_id=reservation_id), status_code=303
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
    def ack_alert(alert_id: str, csrf_token: str = Form()):
        csrf(csrf_token)
        alerts.acknowledge(
            UUID(alert_id), __import__("datetime").datetime.now(__import__("datetime").UTC)
        )
        return RedirectResponse(url("alerts_view"), status_code=303)

    @app.get("/browser", name="browser_status")
    def browser_status(request: Request):
        return render(request, "browser.html", health=browser.health())

    @app.post("/browser/start", name="browser_start")
    def browser_start(csrf_token: str = Form()):
        csrf(csrf_token)
        browser.start()
        return RedirectResponse(url("browser_status"), status_code=303)

    @app.post("/browser/stop", name="browser_stop")
    def browser_stop(csrf_token: str = Form()):
        csrf(csrf_token)
        browser.stop()
        return RedirectResponse(url("browser_status"), status_code=303)

    return app
