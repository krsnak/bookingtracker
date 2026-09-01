from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from app.booking.navigation import BookingSearchUrlError, build_booking_search_url
from app.reservations.models import Reservation


def reservation(**overrides: object) -> Reservation:
    values: dict[str, object] = {
        "property_name": "Example Hotel",
        "booking_url": "https://www.booking.com/hotel/no/example.html",
        "check_in": date(2026, 8, 29),
        "check_out": date(2026, 8, 30),
        "adults": 2,
        "children": 0,
        "rooms_count": 1,
        "room_type": "Apartment",
        "booked_total_price": "1138.39",
        "currency": "NOK",
        "source_text": "sanitized",
        "extraction_confidence": 1,
    }
    values.update(overrides)
    return Reservation(**values)


def test_canonical_url_gets_deterministic_exact_search_query() -> None:
    url = build_booking_search_url(
        "https://www.booking.com/hotel/no/example.html", reservation()
    )

    assert url == (
        "https://www.booking.com/hotel/no/example.html?"
        "checkin=2026-08-29&checkout=2026-08-30&group_adults=2&"
        "group_children=0&no_rooms=1&selected_currency=NOK"
    )


def test_old_query_tracking_and_fragment_are_removed() -> None:
    url = build_booking_search_url(
        "https://www.booking.com/hotel/no/example.html?sid=secret&label=tracking&"
        "checkin=1999-01-01#private",
        reservation(),
    )

    parsed = urlsplit(url)
    assert parsed.fragment == ""
    assert "sid" not in parse_qs(parsed.query)
    assert "label" not in parse_qs(parsed.query)
    assert parse_qs(parsed.query)["checkin"] == ["2026-08-29"]


def test_zero_children_has_no_age_parameters() -> None:
    item = reservation()
    query = parse_qs(urlsplit(build_booking_search_url(item.booking_url, item)).query)

    assert query["group_children"] == ["0"]
    assert "age" not in query


def test_known_age_for_every_child_is_repeated_in_order() -> None:
    item = reservation(children=2, children_ages=[4, 9])
    query = parse_qs(urlsplit(build_booking_search_url(item.booking_url, item)).query)

    assert query["group_children"] == ["2"]
    assert query["age"] == ["4", "9"]


def test_unknown_or_partial_child_ages_are_not_invented() -> None:
    for ages in (None, [], [7]):
        item = reservation(children=2, children_ages=ages)
        query = parse_qs(urlsplit(build_booking_search_url(item.booking_url, item)).query)
        assert query["group_children"] == ["2"]
        assert "age" not in query


@pytest.mark.parametrize("field", ["check_in", "check_out", "adults", "children", "rooms_count"])
def test_unknown_required_search_fact_is_rejected(field: str) -> None:
    item = reservation(**{field: None})

    with pytest.raises(BookingSearchUrlError, match=field):
        build_booking_search_url(item.booking_url, item)


def test_unicode_hotel_path_is_preserved() -> None:
    item = reservation()
    url = build_booking_search_url(
        "https://www.booking.com/hotel/cz/žlutý-dům.html?aid=123", item
    )

    assert urlsplit(url).path == "/hotel/cz/žlutý-dům.html"
    assert "aid=" not in url


def test_search_contract_remains_deterministic_for_exact_match_inputs() -> None:
    item = reservation(
        booking_url="https://www.booking.com/hotel/xx/example.html",
        room_type="Apartment",
    )

    assert build_booking_search_url(item.booking_url, item) == (
        "https://www.booking.com/hotel/xx/example.html?"
        "checkin=2026-08-29&checkout=2026-08-30&group_adults=2&"
        "group_children=0&no_rooms=1&selected_currency=NOK"
    )
