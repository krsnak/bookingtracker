from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.booking.parser import BookingRateParser
from app.matching.matcher import ExactReservationMatcher
from app.pricing.models import PriceDirection
from app.pricing.service import ComparablePriceService
from test_exact_reservation_matcher import rate, reservation

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_papaya_final_total_regression_is_two_euros_lower() -> None:
    parsed = BookingRateParser().parse_html(
        (FIXTURES / "booking_papaya_rates.html").read_text(),
        source_url="https://example.test/papaya",
    )
    match = ExactReservationMatcher().match(reservation(), parsed.offers)

    comparison = ComparablePriceService().compare(reservation(), match)

    assert match.matched_rate == parsed.offers[0]
    assert comparison.comparable is True
    assert comparison.current_price == Decimal("16.88")
    assert comparison.booked_price == Decimal("18.88")
    assert comparison.delta_amount == Decimal("-2.00")
    assert comparison.direction is PriceDirection.LOWER


def test_currency_or_tax_basis_uncertainty_cannot_claim_saving() -> None:
    matcher = ExactReservationMatcher()
    currency_mismatch = matcher.match(reservation(), [rate(currency="USD", taxes_included=True)])
    tax_unknown = matcher.match(reservation(), [rate(taxes_included=None)])

    currency_result = ComparablePriceService().compare(reservation(), currency_mismatch)
    tax_result = ComparablePriceService().compare(reservation(), tax_unknown)

    assert not currency_result.comparable
    assert currency_result.delta_amount is None
    assert not tax_result.comparable
    assert tax_result.delta_amount is None


def test_rejected_rate_cannot_be_compared_even_if_cheaper() -> None:
    rejected = ExactReservationMatcher().match(
        reservation(),
        [rate(current_price=Decimal("1"), non_refundable=True, free_cancellation=False)],
    )

    comparison = ComparablePriceService().compare(reservation(), rejected)

    assert not comparison.comparable
    assert comparison.delta_amount is None
