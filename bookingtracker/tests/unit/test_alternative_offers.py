from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.matching.alternatives import AlternativeOfferEvaluator
from app.matching.matcher import ExactReservationMatcher
from test_exact_reservation_matcher import rate, reservation

EVALUATOR = AlternativeOfferEvaluator()


def test_exact_equivalent_and_better_offers_remain_matcher_results() -> None:
    exact = rate()
    equivalent = rate(room_name="Classic Triple Room")
    better = rate(room_name="Classic Triple Room 24 m²")

    assert ExactReservationMatcher().match(reservation(), [exact]).accepted
    assert (
        ExactReservationMatcher().match(reservation(breakfast_included=None), [equivalent]).accepted
    )
    assert (
        ExactReservationMatcher()
        .match(reservation(room_type="Triple Room 18 m²", breakfast_included=None), [better])
        .accepted
    )


def test_unique_room_absent_exposes_close_information_only_alternative() -> None:
    booked = reservation(
        room_type="Unique Triple Room with Balcony and Sea View", breakfast_included=True
    )
    offer = rate(room_name="Classic Triple Room with Balcony", breakfast_included=True)

    alternatives = EVALUATOR.evaluate(booked, [offer])

    assert len(alternatives) == 1
    assert alternatives[0].rate == offer
    assert "Snídaně zachována" in alternatives[0].preserved
    assert alternatives[0].rate.current_price == Decimal("20")
    assert not ExactReservationMatcher().match(booked, [offer]).accepted


def test_unsafe_minimum_candidates_are_never_alternatives() -> None:
    booked = reservation(room_type="Triple Room", breakfast_included=None)
    unsafe = [
        rate(room_name="Bed in 4-Bed Dormitory Room"),
        rate(adults=1),
        rate(currency="USD"),
        rate(taxes_included=False),
        rate(current_price=Decimal("0")),
    ]

    assert EVALUATOR.evaluate(booked, unsafe) == []


def test_unknown_balcony_can_be_alternative_but_is_explicitly_not_comparable() -> None:
    booked = reservation(room_type="Triple Room with Balcony and Sea View", breakfast_included=True)
    offer = rate(room_name="Classic Triple Room", breakfast_included=True)

    alternative = EVALUATOR.evaluate(booked, [offer])[0]

    assert "Balkon není potvrzen" in alternative.unknown_or_different
    assert not ExactReservationMatcher().match(booked, [offer]).accepted


def test_known_worse_rate_protections_are_excluded() -> None:
    booked = reservation(
        breakfast_included=True,
        free_cancellation=True,
        cancellation_deadline=datetime(2026, 9, 10),
        payment_conditions="Pay at property",
    )
    offers = [
        rate(breakfast_included=False),
        rate(free_cancellation=False, non_refundable=True),
        rate(cancellation_deadline=datetime(2026, 9, 9)),
        rate(payment_conditions="Pay in advance"),
    ]

    assert EVALUATOR.evaluate(booked, offers) == []


def test_objectively_worse_room_fact_is_labeled_not_hidden() -> None:
    booked = reservation(room_type="Triple Room with Balcony", breakfast_included=None)
    offer = rate(room_name="Classic Triple Room without Balcony")

    alternative = EVALUATOR.evaluate(booked, [offer])[0]

    assert "Balkon je horší" in alternative.worse


def test_marketing_name_and_price_do_not_outrank_documented_similarity() -> None:
    booked = reservation(room_type="Triple Room with Balcony", breakfast_included=True)
    close_expensive = rate(
        room_name="Basic Triple Room with Balcony",
        breakfast_included=True,
        current_price=Decimal("40"),
    )
    deluxe_unknown = rate(
        room_name="Deluxe Triple Room", breakfast_included=True, current_price=Decimal("10")
    )

    alternatives = EVALUATOR.evaluate(booked, [deluxe_unknown, close_expensive])

    assert alternatives[0].rate == close_expensive
    assert alternatives[1].rate == deluxe_unknown


def test_order_is_deterministic_and_price_only_breaks_similarity_ties() -> None:
    booked = reservation(room_type="Triple Room", breakfast_included=None)
    first = rate(room_name="Classic Triple Room", current_price=Decimal("30"))
    second = rate(room_name="Classic Triple Room", current_price=Decimal("20"))

    alternatives = EVALUATOR.evaluate(booked, [first, second])

    assert [item.rate.current_price for item in alternatives] == [Decimal("20"), Decimal("30")]


def test_multi_room_reservation_has_no_unsafe_alternative_shortcut() -> None:
    assert EVALUATOR.evaluate(reservation(rooms_count=2), [rate()]) == []
