from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.alerts.models import AlertType
from app.alerts.service import AlertService
from app.booking.models import ParseResult, ParseStatus
from app.booking.parser import BookingRateParser
from app.browser.models import AuthenticationState, NavigationResult, NavigationStatus
from app.db.connection import SQLiteDatabase
from app.db.repository import AlertRepository, PriceCheckRepository, ReservationRepository
from app.matching.matcher import ExactReservationMatcher
from app.matching.models import MatchClassification
from app.pricing.check_service import PriceCheckService
from app.pricing.models import CheckDiagnosticPhase, CheckReasonCode, PriceCheckStatus
from app.pricing.service import ComparablePriceService
from test_exact_reservation_matcher import rate, reservation


class FakePage:
    def content(self) -> str:
        return "<html></html>"


class FakeBrowser:
    def __init__(self, status: NavigationStatus) -> None:
        self.status = status
        self.last_url: str | None = None

    def navigate(self, url: str) -> NavigationResult:
        self.last_url = url
        return NavigationResult(
            requested_url=url,
            final_url=url,
            title="test",
            status=self.status,
            authenticated_state=AuthenticationState.AUTHENTICATED,
            manual_action_required=False,
        )

    def current_page(self) -> FakePage:
        return FakePage()


class RetryOnceBrowser(FakeBrowser):
    """Represents one browser navigation whose availability retry stayed internal."""

    def __init__(self) -> None:
        super().__init__(NavigationStatus.SUCCESS)
        self.goto_attempts = 0

    def navigate(self, url: str) -> NavigationResult:
        self.goto_attempts = 2
        return super().navigate(url)


class SnapshotBrowser(FakeBrowser):
    def __init__(self, html: str) -> None:
        super().__init__(NavigationStatus.SUCCESS)
        self.html = html

    def page_content(self) -> str:
        return self.html


class FakeParser:
    def __init__(self, result: ParseResult) -> None:
        self.result = result

    def parse_html(self, html: str, *, source_url: str) -> ParseResult:  # noqa: ARG002
        return self.result


def service(tmp_path, navigation: NavigationStatus, parsed: ParseResult):  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "checks.db")
    reservation_repository = ReservationRepository(database)
    stored = reservation_repository.create(
        reservation(booking_url="https://www.booking.com/hotel/test/example.html")
    )
    history = PriceCheckRepository(database)
    return (
        PriceCheckService(
            FakeBrowser(navigation),  # type: ignore[arg-type]
            FakeParser(parsed),  # type: ignore[arg-type]
            ExactReservationMatcher(),
            ComparablePriceService(),
            history,
        ),
        stored,
        history,
    )


def test_success_is_persisted_with_comparable_price(tmp_path) -> None:  # noqa: ANN001
    checked, stored, history = service(
        tmp_path,
        NavigationStatus.SUCCESS,
        ParseResult(
            status=ParseStatus.SUCCESS,
            offers=[rate(current_price=Decimal("16.88"), taxes_included=True)],
        ),
    )

    result = checked.check(stored)

    assert result.status is PriceCheckStatus.SUCCESS
    assert result.comparison is not None
    assert history.latest_comparable(stored.id).comparison.delta_amount == Decimal("-2.00")  # type: ignore[union-attr]


def test_lowest_accepted_category_is_persisted_for_pricing_and_alerts(tmp_path) -> None:  # noqa: ANN001
    exact = rate(room_name="Economy Triple Room without Balcony", current_price=Decimal("18"))
    better = rate(room_name="Classic Triple Room with Balcony", current_price=Decimal("16"))
    checked, stored, history = service(
        tmp_path,
        NavigationStatus.SUCCESS,
        ParseResult(status=ParseStatus.SUCCESS, offers=[exact, better]),
    )
    stored = stored.model_copy(update={"room_type": "Economy Triple Room without Balcony"})

    result = checked.check(stored)
    persisted = history.latest(stored.id)

    assert result.status is PriceCheckStatus.SUCCESS
    assert result.match_classification is MatchClassification.BETTER
    assert result.comparison.current_price == Decimal("16")  # type: ignore[union-attr]
    assert persisted.match_classification is MatchClassification.BETTER  # type: ignore[union-attr]


def test_price_check_service_builds_search_url_without_mutating_canonical_url(tmp_path) -> None:  # noqa: ANN001,E501
    checked, stored, _ = service(
        tmp_path,
        NavigationStatus.SUCCESS,
        ParseResult(
            status=ParseStatus.SUCCESS,
            offers=[rate(current_price=Decimal("16.88"), taxes_included=True)],
        ),
    )

    checked.check(stored)

    assert checked.browser.last_url == (  # type: ignore[attr-defined]
        "https://www.booking.com/hotel/test/example.html?"
        "checkin=2026-09-18&checkout=2026-09-19&group_adults=2&"
        "group_children=0&no_rooms=1&selected_currency=EUR"
    )
    assert stored.booking_url == "https://www.booking.com/hotel/test/example.html"


def test_incomplete_children_stops_before_navigation_with_czech_validation(tmp_path) -> None:  # noqa: ANN001
    checked, stored, history = service(
        tmp_path,
        NavigationStatus.SUCCESS,
        ParseResult(status=ParseStatus.SUCCESS, offers=[]),
    )
    stored = stored.model_copy(update={"children": None})

    result = checked.check(stored)

    assert result.status is PriceCheckStatus.INCOMPLETE_RESERVATION
    assert result.reason_code is CheckReasonCode.INCOMPLETE_RESERVATION
    assert result.diagnostic_phase is CheckDiagnosticPhase.RESERVATION_VALIDATION
    assert "není uveden počet dětí" in (result.safe_error_detail or "")
    assert checked.browser.last_url is None  # type: ignore[attr-defined]
    assert result.comparison is None
    assert history.latest(stored.id).comparison is None  # type: ignore[union-attr]


def test_one_browser_retry_still_creates_one_price_check_record(tmp_path) -> None:  # noqa: ANN001
    checked, stored, history = service(
        tmp_path,
        NavigationStatus.SUCCESS,
        ParseResult(
            status=ParseStatus.SUCCESS,
            offers=[rate(current_price=Decimal("16.88"), taxes_included=True)],
        ),
    )
    browser = RetryOnceBrowser()
    checked.browser = browser

    result = checked.check(stored)

    assert browser.goto_attempts == 2
    assert result.status is PriceCheckStatus.SUCCESS
    assert len(history.list_for_reservation(stored.id)) == 1


def test_no_match_ambiguous_and_failures_are_persisted_without_prices(tmp_path) -> None:  # noqa: ANN001
    cases = [
        (
            NavigationStatus.SUCCESS,
            ParseResult(status=ParseStatus.SUCCESS, offers=[]),
            PriceCheckStatus.PARSER_ERROR,
        ),
        (
            NavigationStatus.SUCCESS,
            ParseResult(status=ParseStatus.SUCCESS, offers=[rate(breakfast_included=None)]),
            PriceCheckStatus.NO_MATCH,
        ),
        (
            NavigationStatus.LOGIN_REQUIRED,
            ParseResult(status=ParseStatus.SUCCESS),
            PriceCheckStatus.LOGGED_OUT,
        ),
        (
            NavigationStatus.CAPTCHA_REQUIRED,
            ParseResult(status=ParseStatus.SUCCESS),
            PriceCheckStatus.CAPTCHA_REQUIRED,
        ),
        (
            NavigationStatus.TIMEOUT,
            ParseResult(status=ParseStatus.SUCCESS),
            PriceCheckStatus.TIMEOUT,
        ),
        (
            NavigationStatus.SUCCESS,
            ParseResult(status=ParseStatus.UNSUPPORTED_STRUCTURE),
            PriceCheckStatus.PARSER_ERROR,
        ),
    ]
    for index, (navigation, parsed, expected) in enumerate(cases):
        checked, stored, history = service(tmp_path / str(index), navigation, parsed)

        result = checked.check(stored)
        persisted = history.latest(stored.id)

        assert result.status is expected
        assert persisted is not None
        assert persisted.status is expected
        assert persisted.comparison is None


def test_failed_check_cannot_carry_a_previous_successful_price(tmp_path) -> None:  # noqa: ANN001
    checked, stored, history = service(
        tmp_path,
        NavigationStatus.SUCCESS,
        ParseResult(
            status=ParseStatus.SUCCESS,
            offers=[rate(current_price=Decimal("16.88"), taxes_included=True)],
        ),
    )
    successful = checked.check(stored)
    checked.browser.status = NavigationStatus.LOGIN_REQUIRED  # type: ignore[attr-defined]

    failed = checked.check(stored)
    history_rows = history.list_for_reservation(stored.id)

    assert successful.comparison is not None
    assert failed.status is PriceCheckStatus.LOGGED_OUT
    assert failed.comparison is None
    assert history_rows[0].comparison is None
    assert history_rows[1].comparison is not None


def test_storhaugen_partial_snapshot_selects_later_exact_offer(tmp_path) -> None:  # noqa: ANN001
    html = (
        Path(__file__).parents[1] / "fixtures" / "booking_storhaugen_optional_missing.html"
    ).read_text()
    database = SQLiteDatabase(tmp_path / "storhaugen.db")
    reservations = ReservationRepository(database)
    stored = reservations.create(
        reservation(
            property_name="STORHAUGEN GARD",
            room_type="Standard Double Room",
            booked_total_price=Decimal("1500"),
            currency="NOK",
            booking_url="https://www.booking.com/hotel/xx/example.html",
        )
    )
    history = PriceCheckRepository(database)
    checked = PriceCheckService(
        SnapshotBrowser(html),  # type: ignore[arg-type]
        BookingRateParser(),
        ExactReservationMatcher(),
        ComparablePriceService(),
        history,
    )

    result = checked.check(stored)

    assert result.status is PriceCheckStatus.SUCCESS
    assert result.diagnostic_phase is CheckDiagnosticPhase.EXACT_MATCH
    assert result.comparison is not None
    assert result.comparison.delta_amount == Decimal("0")
    assert len(history.latest(stored.id).rate_offers) == 1  # type: ignore[union-attr]


def test_snapshot_no_exact_offer_and_missing_required_structure_are_distinct(
    tmp_path,
) -> None:  # noqa: ANN001
    fixture = (
        Path(__file__).parents[1] / "fixtures" / "booking_storhaugen_optional_missing.html"
    ).read_text()

    class NullNotifier:
        def deliver(self, alert) -> None:  # noqa: ANN001
            del alert

    results = []
    for index, (html, room_type, expected, reason, phase) in enumerate(
        (
            (
                fixture,
                "Unrelated Family Suite",
                PriceCheckStatus.NO_MATCH,
                CheckReasonCode.NO_COMPARABLE_OFFER,
                CheckDiagnosticPhase.EXACT_MATCH,
            ),
            (
                "<main>Booking page without availability structure</main>",
                "Standard Double Room",
                PriceCheckStatus.PARSER_ERROR,
                CheckReasonCode.PARSER_ERROR,
                CheckDiagnosticPhase.OFFER_COLLECTION,
            ),
        )
    ):
        database = SQLiteDatabase(tmp_path / f"case-{index}.db")
        reservations = ReservationRepository(database)
        stored = reservations.create(
            reservation(
                property_name="STORHAUGEN GARD",
                room_type=room_type,
                booked_total_price=Decimal("1500"),
                currency="NOK",
                booking_url="https://www.booking.com/hotel/xx/example.html",
            )
        )
        history = PriceCheckRepository(database)
        result = PriceCheckService(
            SnapshotBrowser(html),  # type: ignore[arg-type]
            BookingRateParser(),
            ExactReservationMatcher(),
            ComparablePriceService(),
            history,
        ).check(stored)
        AlertService(AlertRepository(database), history, NullNotifier()).process(
            result, stored, consecutive_failures=3
        )
        assert result.status is expected
        assert result.reason_code is reason
        assert result.diagnostic_phase is phase
        assert result.comparison is None
        assert not any(
            alert.type is AlertType.PRICE_DROP
            for alert in AlertRepository(database).list_for_reservation(stored.id)
        )
        results.append(result)

    assert results[0].safe_error_detail == "Nebyla nalezena bezpečně porovnatelná nabídka."
    assert results[1].safe_error_detail == ("Povinná struktura cenové nabídky nebyla rozpoznána.")


def test_availability_unknown_is_safe_and_has_no_price_or_alert(tmp_path) -> None:  # noqa: ANN001
    checked, stored, history = service(
        tmp_path, NavigationStatus.AVAILABILITY_UNKNOWN, ParseResult(status=ParseStatus.SUCCESS)
    )
    result = checked.check(stored)
    alerts = AlertRepository(SQLiteDatabase(tmp_path / "checks.db"))
    created = AlertService(alerts, history, lambda alert: None).process(
        result, stored, consecutive_failures=2
    )

    assert result.status is PriceCheckStatus.AVAILABILITY_UNKNOWN
    assert result.reason_code is CheckReasonCode.AVAILABILITY_UNKNOWN
    assert result.diagnostic_phase is CheckDiagnosticPhase.AVAILABILITY_DETECTION
    assert result.comparison is None
    assert "Dostupnost se nepodařilo ověřit" in (result.safe_error_detail or "")
    assert not created
    assert not alerts.list_for_reservation(stored.id)
