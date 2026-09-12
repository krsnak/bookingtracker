from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.booking.discovery import CrossPropertyDiscoveryService, DiscoveryStatus
from app.booking.parser import (
    BookingPropertyQualityParser,
    BookingRateParser,
    BookingSearchCardParser,
)
from app.browser.models import AuthenticationState, NavigationResult, NavigationStatus
from app.matching.cross_property import CrossPropertyAlternativeVerifier
from app.matching.matcher import ExactReservationMatcher
from test_exact_reservation_matcher import reservation

FIXTURES = Path(__file__).parents[1] / "fixtures"
SEARCH_HTML = (FIXTURES / "booking_cross_property_search.html").read_text()
DETAIL_HTML = (FIXTURES / "booking_cross_property_detail.html").read_text()


def test_search_card_headline_is_a_lead_not_a_rate_offer() -> None:
    cards = BookingSearchCardParser().parse_html(
        SEARCH_HTML, "https://www.booking.com/searchresults.html"
    )

    assert len(cards) == 1
    assert cards[0].headline_price == Decimal("9.99")
    assert cards[0].comparable is False
    assert cards[0].detail_url == "https://www.booking.com/hotel/cz/verified-alternative.html"
    assert cards[0].quality.review_count == 1234


def test_only_detail_verified_cross_property_rate_can_qualify() -> None:
    card = BookingSearchCardParser().parse_html(
        SEARCH_HTML, "https://www.booking.com/searchresults.html"
    )[0]
    detail = BookingRateParser().parse_html(DETAIL_HTML, card.detail_url)
    quality = BookingPropertyQualityParser().parse_html(DETAIL_HTML)

    result = CrossPropertyAlternativeVerifier().verify(reservation(), card, quality, detail.offers)

    assert len(result.qualified) == 1
    alternative = result.qualified[0]
    assert alternative.comparable is False
    assert alternative.rate.current_price == Decimal("120")
    assert alternative.rate.current_price != card.headline_price
    assert alternative.quality.booking_score == Decimal("8.7")
    assert alternative.quality.star_category == "4 stars"
    assert alternative.quality.location_or_distance == "0.4 km from centre"
    assert not ExactReservationMatcher().match(reservation(), detail.offers).accepted


def test_missing_detail_terms_or_quality_reject_cross_property_candidate() -> None:
    card = BookingSearchCardParser().parse_html(
        SEARCH_HTML, "https://www.booking.com/searchresults.html"
    )[0]
    detail = BookingRateParser().parse_html(
        DETAIL_HTML.replace("Breakfast included", "Breakfast not included"), card.detail_url
    )
    no_quality = BookingPropertyQualityParser().parse_html("<main></main>")

    quality_result = CrossPropertyAlternativeVerifier().verify(
        reservation(), card, no_quality, detail.offers
    )
    terms_result = CrossPropertyAlternativeVerifier().verify(
        reservation(), card, BookingPropertyQualityParser().parse_html(DETAIL_HTML), detail.offers
    )

    assert quality_result.qualified == []
    assert quality_result.rejected == {"hotel_quality_not_confirmed": 1}
    assert terms_result.qualified == []
    assert terms_result.rejected == {"meal_not_confirmed": 1}


def test_cross_property_rejects_worse_deadline_payment_or_unproven_room() -> None:
    card = BookingSearchCardParser().parse_html(
        SEARCH_HTML, "https://www.booking.com/searchresults.html"
    )[0]
    detail = BookingRateParser().parse_html(DETAIL_HTML, card.detail_url)
    verified_quality = BookingPropertyQualityParser().parse_html(DETAIL_HTML)
    verifier = CrossPropertyAlternativeVerifier()

    deadline = verifier.verify(
        reservation(cancellation_deadline=datetime(2026, 9, 11)),
        card,
        verified_quality,
        detail.offers,
    )
    payment = verifier.verify(
        reservation(payment_conditions="Pay at property after arrival"),
        card,
        verified_quality,
        detail.offers,
    )
    room = verifier.verify(
        reservation(room_type="Triple Room with Balcony"), card, verified_quality, detail.offers
    )
    taxes = verifier.verify(
        reservation(),
        card,
        verified_quality,
        [detail.offers[0].model_copy(update={"taxes_included": False})],
    )

    assert deadline.rejected == {"cancellation_not_confirmed": 1}
    assert payment.rejected == {"payment_not_confirmed": 1}
    assert room.rejected == {"room_not_confirmed": 1}
    assert taxes.rejected == {"tax_inclusive_total_not_confirmed": 1}


def test_quality_count_requires_an_unabbreviated_explicit_count() -> None:
    quality = BookingPropertyQualityParser().parse_html(
        '<span data-testid="review-score">11</span>'
        '<span data-testid="review-count">12.34 reviews</span>'
    )

    assert quality.booking_score is None
    assert quality.review_count is None
    assert quality.review_count_confident is False


class _Browser:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._content = ""

    def navigate(self, url: str) -> NavigationResult:
        self.calls.append(url)
        self._content = SEARCH_HTML if "searchresults.html" in url else DETAIL_HTML
        return NavigationResult(
            requested_url=url,
            final_url=url,
            title="fixture",
            status=NavigationStatus.SUCCESS,
            authenticated_state=AuthenticationState.AUTHENTICATED,
            manual_action_required=False,
        )

    def page_content(self) -> str:
        return self._content

    def current_page(self) -> None:
        return None


def test_service_reuses_navigation_but_never_creates_a_price_check_record() -> None:
    browser = _Browser()

    result = CrossPropertyDiscoveryService(browser).discover(reservation(), "Nice")

    assert result.status is DiscoveryStatus.SUCCESS
    assert len(browser.calls) == 2
    assert len(result.alternatives) == 1
    assert result.alternatives[0].comparable is False
