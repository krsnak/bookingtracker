from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.booking.models import RateOffer
from app.booking.parser import BookingRateParser
from app.booking.room_facts import extract_room_facts
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
        "taxes_included": True,
        "source_row_text": "sanitized rate",
        "source_url": "https://example.test",
        "scrape_timestamp": datetime(2026, 8, 1),
    }
    fields.update(overrides)
    if "room_facts" not in overrides:
        fields["room_facts"] = extract_room_facts(str(fields["room_name"]))
    return RateOffer(**fields)


def test_exact_match_accepts_same_conditions() -> None:
    result = MATCHER.match(reservation(), [rate()])

    assert result.accepted
    assert result.classification is MatchClassification.EXACT
    assert result.matched_rate == rate()


def test_equivalent_wording_is_accepted() -> None:
    result = MATCHER.match(
        reservation(room_type="Classic Triple Room with Balcony", breakfast_included=None),
        [rate(room_name="Economy Triple Room with Balcony")],
    )

    assert result.accepted
    assert result.classification is MatchClassification.EQUIVALENT


def test_marketing_word_does_not_create_better_room() -> None:
    result = MATCHER.match(
        reservation(room_type="Classic Triple Room", breakfast_included=None),
        [rate(room_name="Deluxe Triple Room")],
    )

    assert result.accepted
    assert result.classification is MatchClassification.EQUIVALENT


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


def test_missing_cancellation_evidence_cannot_match_booked_free_cancellation() -> None:
    result = MATCHER.match(reservation(), [rate(free_cancellation=None)])

    candidate = result.candidate_evaluations[0]
    assert not result.accepted
    assert candidate.classification is MatchClassification.REJECTED
    assert "free cancellation" in candidate.rejection_reasons[0]


def test_earlier_and_later_cancellation_deadlines_are_distinguished() -> None:
    booked = reservation(cancellation_deadline=datetime(2026, 9, 10))
    earlier = MATCHER.match(booked, [rate(cancellation_deadline=datetime(2026, 9, 9))])
    later = MATCHER.match(booked, [rate(cancellation_deadline=datetime(2026, 9, 11))])

    assert earlier.candidate_evaluations[0].classification is MatchClassification.REJECTED
    assert later.accepted
    assert later.classification is MatchClassification.EXACT


def test_missing_candidate_breakfast_is_rejected() -> None:
    result = MATCHER.match(reservation(), [rate(breakfast_included=None)])

    assert not result.accepted
    assert result.candidate_evaluations[0].classification is MatchClassification.REJECTED


def test_insufficient_or_unknown_occupancy_is_rejected() -> None:
    too_small = MATCHER.match(reservation(adults=2), [rate(adults=1)])
    unknown = MATCHER.match(reservation(adults=2), [rate(adults=None, occupancy_text=None)])

    assert too_small.candidate_evaluations[0].classification is MatchClassification.REJECTED
    assert unknown.candidate_evaluations[0].classification is MatchClassification.REJECTED


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


def test_known_worse_payment_terms_are_rejected() -> None:
    result = MATCHER.match(
        reservation(payment_conditions="Pay at property"),
        [rate(payment_conditions="Pay in advance")],
    )

    assert not result.accepted
    assert result.classification is MatchClassification.NO_MATCH


def test_different_name_with_larger_documented_area_is_better() -> None:
    result = MATCHER.match(
        reservation(room_type="Classic Triple Room 18 m² with Balcony", breakfast_included=None),
        [rate(room_name="Economy Triple Room 22 m² with Balcony")],
    )

    assert result.accepted
    assert result.classification is MatchClassification.BETTER
    assert "larger room area" in result.candidate_evaluations[0].objective_differences


def test_private_bathroom_or_extra_balcony_is_better_only_with_full_evidence() -> None:
    bathroom = MATCHER.match(
        reservation(room_type="Classic Triple Room with Shared Bathroom", breakfast_included=None),
        [rate(room_name="Economy Triple Room with Private Bathroom")],
    )
    balcony = MATCHER.match(
        reservation(room_type="Classic Triple Room without Balcony", breakfast_included=None),
        [rate(room_name="Economy Triple Room with Balcony")],
    )

    assert bathroom.classification is MatchClassification.BETTER
    assert balcony.classification is MatchClassification.BETTER


def test_missing_booked_balcony_or_view_is_not_comparable() -> None:
    balcony = MATCHER.match(
        reservation(room_type="Triple Room with Balcony", breakfast_included=None),
        [rate(room_name="Classic Triple Room")],
    )
    view = MATCHER.match(
        reservation(room_type="Triple Room with Sea View", breakfast_included=None),
        [rate(room_name="Classic Triple Room")],
    )

    assert not balcony.accepted and balcony.classification is MatchClassification.NO_MATCH
    assert not view.accepted and view.classification is MatchClassification.NO_MATCH


def test_dorm_bed_and_worse_terms_are_never_compensated_by_room_size() -> None:
    dorm = MATCHER.match(
        reservation(room_type="Triple Room with Balcony", breakfast_included=None),
        [rate(room_name="Bed in 4-Bed Dormitory Room with Balcony")],
    )
    cancellation = MATCHER.match(
        reservation(room_type="Triple Room 18 m²", breakfast_included=None),
        [rate(room_name="Classic Triple Room 22 m²", free_cancellation=False, non_refundable=True)],
    )

    assert not dorm.accepted
    assert not cancellation.accepted


def test_currency_and_capacity_difference_cannot_be_accepted_or_marked_better() -> None:
    currency = MATCHER.match(
        reservation(room_type="Triple Room", breakfast_included=None),
        [rate(room_name="Classic Triple Room", currency="USD")],
    )
    capacity = MATCHER.match(
        reservation(room_type="Double Room", breakfast_included=None),
        [rate(room_name="Classic Triple Room")],
    )

    assert not currency.accepted
    assert not capacity.accepted


def test_same_category_identical_terms_selects_lowest_total_but_conflict_is_ambiguous() -> None:
    selected = MATCHER.match(
        reservation(), [rate(current_price=Decimal("22")), rate(current_price=Decimal("20"))]
    )
    ambiguous = MATCHER.match(
        reservation(),
        [
            rate(current_price=Decimal("22")),
            rate(current_price=Decimal("20"), cancellation_deadline=datetime(2026, 9, 11)),
        ],
    )

    assert selected.accepted and selected.matched_rate.current_price == Decimal("20")
    assert not ambiguous.accepted and ambiguous.classification is MatchClassification.AMBIGUOUS


def _room_choice_reservation() -> Reservation:
    return reservation(room_type="Economy Triple Room without Balcony")


def _equivalent_choice_rate(price: str) -> RateOffer:
    return rate(room_name="Classic Triple Room without Balcony", current_price=Decimal(price))


def _better_choice_rate(price: str) -> RateOffer:
    return rate(room_name="Classic Triple Room with Balcony", current_price=Decimal(price))


def test_lowest_safe_total_can_select_better_over_more_expensive_exact() -> None:
    result = MATCHER.match(
        _room_choice_reservation(),
        [
            rate(room_name="Economy Triple Room without Balcony", current_price=Decimal("100")),
            _better_choice_rate("80"),
        ],
    )

    assert result.accepted
    assert result.classification is MatchClassification.BETTER
    assert result.matched_rate.current_price == Decimal("80")


def test_lowest_safe_total_keeps_cheaper_exact_over_better_room() -> None:
    result = MATCHER.match(
        _room_choice_reservation(),
        [
            rate(room_name="Economy Triple Room without Balcony", current_price=Decimal("80")),
            _better_choice_rate("100"),
        ],
    )

    assert result.accepted
    assert result.classification is MatchClassification.EXACT
    assert result.matched_rate.current_price == Decimal("80")


def test_lowest_safe_total_can_select_equivalent_over_more_expensive_better_room() -> None:
    result = MATCHER.match(
        _room_choice_reservation(), [_equivalent_choice_rate("70"), _better_choice_rate("80")]
    )

    assert result.accepted
    assert result.classification is MatchClassification.EQUIVALENT
    assert result.matched_rate.current_price == Decimal("70")


def test_equal_totals_use_category_then_diagnostic_index() -> None:
    all_categories = MATCHER.match(
        _room_choice_reservation(),
        [
            _better_choice_rate("80"),
            _equivalent_choice_rate("80"),
            rate(room_name="Economy Triple Room without Balcony", current_price=Decimal("80")),
        ],
    )
    no_exact = MATCHER.match(
        _room_choice_reservation(), [_better_choice_rate("80"), _equivalent_choice_rate("80")]
    )

    assert all_categories.classification is MatchClassification.EXACT
    assert all_categories.matched_evaluation.diagnostic_index == 3  # type: ignore[union-attr]
    assert no_exact.classification is MatchClassification.EQUIVALENT
    assert no_exact.matched_evaluation.diagnostic_index == 2  # type: ignore[union-attr]


def test_cheaper_incomplete_or_worse_room_never_reaches_price_selection() -> None:
    result = MATCHER.match(
        _room_choice_reservation(),
        [
            rate(room_name="Economy Triple Room without Balcony", current_price=Decimal("100")),
            rate(room_name="Classic Triple Room", current_price=Decimal("10")),
            rate(
                room_name="Classic Triple Room with Balcony",
                current_price=Decimal("20"),
                breakfast_included=None,
            ),
            rate(
                room_name="Classic Triple Room with Balcony",
                current_price=Decimal("30"),
                free_cancellation=False,
                non_refundable=True,
            ),
        ],
    )

    assert result.classification is MatchClassification.EXACT
    assert result.matched_rate.current_price == Decimal("100")
    assert [item.accepted for item in result.candidate_evaluations] == [True, False, False, False]


def test_currency_or_non_orderable_terms_produce_no_price_selection() -> None:
    currency = MATCHER.match(
        _room_choice_reservation(),
        [rate(room_name="Economy Triple Room without Balcony", currency="USD")],
    )
    terms = MATCHER.match(
        _room_choice_reservation(),
        [
            rate(room_name="Economy Triple Room without Balcony", current_price=Decimal("100")),
            _better_choice_rate("80").model_copy(update={"payment_conditions": "Pay later"}),
        ],
    )

    assert not currency.accepted and currency.classification is MatchClassification.NO_MATCH
    assert not terms.accepted and terms.classification is MatchClassification.AMBIGUOUS
