from __future__ import annotations

from decimal import Decimal

from app.booking.models import ParseResult, ParseStatus
from app.browser.models import AuthenticationState, NavigationResult, NavigationStatus
from app.db.connection import SQLiteDatabase
from app.db.repository import PriceCheckRepository, ReservationRepository
from app.matching.matcher import ExactReservationMatcher
from app.pricing.check_service import PriceCheckService
from app.pricing.models import PriceCheckStatus
from app.pricing.service import ComparablePriceService
from test_exact_reservation_matcher import rate, reservation


class FakePage:
    def content(self) -> str:
        return "<html></html>"


class FakeBrowser:
    def __init__(self, status: NavigationStatus) -> None:
        self.status = status

    def navigate(self, url: str) -> NavigationResult:
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


class FakeParser:
    def __init__(self, result: ParseResult) -> None:
        self.result = result

    def parse_html(self, html: str, *, source_url: str) -> ParseResult:  # noqa: ARG002
        return self.result


def service(tmp_path, navigation: NavigationStatus, parsed: ParseResult):  # noqa: ANN001
    database = SQLiteDatabase(tmp_path / "checks.db")
    reservation_repository = ReservationRepository(database)
    stored = reservation_repository.create(reservation(booking_url="https://example.test"))
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


def test_no_match_ambiguous_and_failures_are_persisted_without_prices(tmp_path) -> None:  # noqa: ANN001
    cases = [
        (
            NavigationStatus.SUCCESS,
            ParseResult(status=ParseStatus.SUCCESS, offers=[]),
            PriceCheckStatus.NO_MATCH,
        ),
        (
            NavigationStatus.SUCCESS,
            ParseResult(status=ParseStatus.SUCCESS, offers=[rate(breakfast_included=None)]),
            PriceCheckStatus.AMBIGUOUS,
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
