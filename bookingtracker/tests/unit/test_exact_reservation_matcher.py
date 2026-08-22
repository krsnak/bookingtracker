from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.booking.parser import BookingRateParser
from app.matching.matcher import ExactReservationMatcher
from app.matching.models import MatchClassification
from app.reservations.models import Reservation

FIXTURES = Path(__file__).parents[1] / "fixtures"
MATCHER = ExactReservationMatcher()


def reservation(**overrides: object) -> Reservation:
    fields: dict[str, object] = {
        "property_name": "Papaya Hostel",
        "check_in": date(2026, 9, 18),
        "check_out": date(2026, 9, 19),
        "nights": 1,
        "adults": 2,
        "children": 0,
        "rooms_count": 1,
        "room_type": "Economy Triple Room",
        "breakfast_included": True,
        "free_cancellation": True,
        "booked_total_price": Decimal("18.88"),
        "currency": "EUR",
        "source_text": "sanitized reservation",
        "extraction_confidence": 1,
    }
    fields.update(overrides)
    return Reservation(**fields)


def rate(**overrides: object):
    from app.booking.models import RateOffer

    fields: dict[str, object] = {
        "property_name": "Papaya Hostel",
        "room_name": "Economy Triple Room",
        "normalized_room_name": "economy triple room",
        "adults": 2,
        "children": 0,
        "breakfast_included": True,
        "current_price": Decimal("20"),
        "currency": "EUR",
        "free_cancellation": True,
        "non_refundable": False,
        "source_row_text": "sanitized rate",
        "source_url": "https://example.test",
        "scrape_timestamp": datetime(2026, 8, 1),
    }
    fields.update(overrides)
    return RateOffer(**fields)


def test_exact_match_accepts_same_conditions() -> None:
    result = MATCHER.match(reservation(), [rate()])

    assert result.accepted
    assert result.classification is MatchClassification.EXACT
    assert result.matched_rate == rate()


def test_equivalent_wording_is_accepted() -> None:
    result = MATCHER.match(
        reservation(room_type="Budget double room with double bed", breakfast_included=None),
        [rate(room_name="Budget Double Room", normalized_room_name="budget double room")],
    )

    assert result.accepted
    assert result.classification is MatchClassification.EQUIVALENT


def test_wrong_room_is_not_accepted_as_exact() -> None:
    result = MATCHER.match(reservation(), [rate(room_name="Triple Room with Balcony")])

    assert not result.accepted
    assert result.candidate_evaluations[0].classification is MatchClassification.UPGRADE_CANDIDATE


def test_breakfast_downgrade_is_rejected() -> None:
    result = MATCHER.match(reservation(), [rate(breakfast_included=False)])

    candidate = result.candidate_evaluations[0]
    assert not result.accepted
    assert candidate.classification is MatchClassification.REJECTED
    assert "breakfast" in candidate.rejection_reasons[0]


def test_genius_breakfast_satisfies_booked_breakfast() -> None:
    result = MATCHER.match(
        reservation(),
        [rate(breakfast_included=True, breakfast_genius_benefit=True, genius=True)],
    )

    assert result.accepted
    assert result.classification is MatchClassification.EXACT


def test_non_refundable_downgrade_is_rejected() -> None:
    result = MATCHER.match(reservation(), [rate(non_refundable=True, free_cancellation=False)])

    candidate = result.candidate_evaluations[0]
    assert candidate.classification is MatchClassification.REJECTED
    assert "free cancellation" in candidate.rejection_reasons[0]


def test_earlier_and_later_cancellation_deadlines_are_distinguished() -> None:
    booked = reservation(cancellation_deadline=datetime(2026, 9, 10))
    earlier = MATCHER.match(booked, [rate(cancellation_deadline=datetime(2026, 9, 9))])
    later = MATCHER.match(booked, [rate(cancellation_deadline=datetime(2026, 9, 11))])

    assert earlier.candidate_evaluations[0].classification is MatchClassification.REJECTED
    assert later.accepted
    assert later.classification is MatchClassification.BETTER


def test_unknown_candidate_breakfast_is_ambiguous() -> None:
    result = MATCHER.match(reservation(), [rate(breakfast_included=None)])

    assert not result.accepted
    assert result.candidate_evaluations[0].classification is MatchClassification.AMBIGUOUS


def test_insufficient_occupancy_is_rejected_and_unknown_is_ambiguous() -> None:
    too_small = MATCHER.match(reservation(adults=2), [rate(adults=1)])
    unknown = MATCHER.match(reservation(adults=2), [rate(adults=None, occupancy_text=None)])

    assert too_small.candidate_evaluations[0].classification is MatchClassification.REJECTED
    assert unknown.candidate_evaluations[0].classification is MatchClassification.AMBIGUOUS
    assert "occupancy" in unknown.candidate_evaluations[0].warnings[0]


def test_all_candidates_are_evaluated_and_price_does_not_select_match() -> None:
    exact = rate(current_price=Decimal("25"))
    cheaper_non_refundable = rate(
        current_price=Decimal("15"), non_refundable=True, free_cancellation=False
    )
    result = MATCHER.match(reservation(), [cheaper_non_refundable, exact])

    assert result.accepted
    assert result.matched_rate == exact
    assert len(result.candidate_evaluations) == 2
    assert result.candidate_evaluations[0].classification is MatchClassification.REJECTED


def test_papaya_regression_refundable_genius_breakfast_beats_non_refundable_rate() -> None:
    parsed = BookingRateParser().parse_html(
        (FIXTURES / "booking_papaya_rates.html").read_text(),
        source_url="https://example.test/papaya",
    )
    result = MATCHER.match(reservation(), parsed.offers)

    assert result.accepted
    assert result.matched_rate == parsed.offers[0]
    assert result.classification is MatchClassification.EXACT
    assert result.candidate_evaluations[1].classification is MatchClassification.REJECTED


def test_known_worse_payment_terms_are_warned_without_price_logic() -> None:
    result = MATCHER.match(
        reservation(payment_conditions="Pay at property"),
        [rate(payment_conditions="Pay in advance")],
    )

    assert result.accepted
    assert result.classification is MatchClassification.EXACT
    assert "payment" in result.warnings[0]
