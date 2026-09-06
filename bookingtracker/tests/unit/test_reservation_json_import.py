import json

import pytest
from app.reservations.import_json import AI_PROMPT, SAMPLE_JSON, JsonImportError, json_candidate
from app.reservations.models import ReservationSource


def sample_json(**updates: object) -> bytes:
    value = json.loads(SAMPLE_JSON)
    for path, replacement in updates.items():
        target = value
        keys = path.split(".")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = replacement
    return json.dumps(value).encode()


def test_json_maps_nested_facts_directly_to_a_complete_candidate() -> None:
    candidate = json_candidate(
        sample_json(
            **{
                "occupancy.children": 2,
                "occupancy.child_ages": [5, 8],
                "occupancy.rooms": 2,
                "room.name": "Family Room and Double Room",
                "room.breakdown": [
                    {"name": "Family Room", "count": 1},
                    {"name": "Double Room", "count": 1},
                ],
                "meal.breakfast_included": False,
                "meal.meal_plan": None,
                "cancellation.free_cancellation": False,
                "cancellation.deadline": None,
                "payment.conditions": "Prepayment required",
            }
        )
    )

    assert candidate.source is ReservationSource.AI_JSON
    assert candidate.children_ages == [5, 8]
    assert [(room.count, room.room_type) for room in candidate.rooms_breakdown or []] == [
        (1, "Family Room"),
        (1, "Double Room"),
    ]
    assert candidate.breakfast_included is False
    assert candidate.meal_plan is None
    assert candidate.free_cancellation is False
    assert candidate.cancellation_deadline is None
    assert candidate.payment_conditions == "Prepayment required"
    assert candidate.can_activate


def test_json_canonicalizes_booking_url_and_keeps_unknowns_unknown() -> None:
    candidate = json_candidate(
        sample_json(
            **{
                "property.booking_url": (
                    "https://cs.booking.com/hotel/xx/example-hotel.en-gb.html?sid=session&label=x#private"
                ),
                "meal.breakfast_included": None,
                "occupancy.children": None,
                "occupancy.child_ages": None,
                "cancellation.free_cancellation": None,
                "cancellation.deadline": None,
            }
        )
    )

    assert candidate.booking_url == "https://www.booking.com/hotel/xx/example-hotel.html"
    assert candidate.breakfast_included is None
    assert candidate.children is None
    assert candidate.free_cancellation is None


@pytest.mark.parametrize(
    "updates",
    [
        {"meal.breakfast_included": True, "meal.meal_plan": "Breakfast included"},
        {"meal.breakfast_included": False, "meal.meal_plan": "No meals included"},
        {"meal.breakfast_included": None, "meal.meal_plan": None},
        {"cancellation.free_cancellation": True, "cancellation.deadline": None},
        {"cancellation.free_cancellation": False, "cancellation.deadline": None},
        {"cancellation.free_cancellation": None, "cancellation.deadline": None},
        {
            "price.booked_total": "250.00",
            "price.taxes_and_fees": "30.00",
            "price.booked_base": "220.00",
        },
        {
            "price.booked_total": "250.00",
            "price.booked_payable": None,
            "price.booked_base": None,
            "price.taxes_and_fees": None,
            "price.vat": None,
            "price.city_tax": None,
        },
    ],
)
def test_json_accepts_explicit_meal_cancellation_and_price_variants(
    updates: dict[str, object],
) -> None:
    candidate = json_candidate(sample_json(**updates))

    assert candidate.booked_total_price is not None


@pytest.mark.parametrize(
    "contents",
    [
        b"{",
        sample_json(schema_version=2),
        json.dumps(json.loads(SAMPLE_JSON) | {"unexpected": True}).encode(),
        sample_json(**{"stay.check_in": "10-10-2026"}),
        sample_json(**{"stay.check_out": "2026-10-10"}),
        sample_json(**{"cancellation.deadline": "2026-10-08T23:59:00"}),
        sample_json(**{"price.currency": "EURO"}),
        sample_json(**{"price.booked_total": "NaN"}),
        sample_json(**{"occupancy.adults": 0}),
        sample_json(**{"occupancy.rooms": -1}),
        sample_json(**{"occupancy.children": 2, "occupancy.child_ages": [5]}),
        sample_json(**{"property.booking_url": "https://example.test/hotel"}),
        sample_json(**{"property.booking_url": "https://www.booking.com/help"}),
        b'{"schema_version":1,"schema_version":1}',
        b"\xff",
        b"{" + b" " * 70000 + b"}",
    ],
)
def test_json_rejects_invalid_or_unsafe_input(contents: bytes) -> None:
    with pytest.raises(JsonImportError):
        json_candidate(contents)


def test_null_critical_value_reaches_review_as_missing_not_guessed() -> None:
    candidate = json_candidate(sample_json(**{"property.booking_url": None}))

    assert candidate.booking_url is None
    assert "booking_url" in candidate.missing_critical_fields


def test_prompt_is_json_only_and_explicitly_forbids_inference() -> None:
    assert "pouze jeden" in AI_PROMPT
    assert "null" in AI_PROMPT
    assert "Never invent or infer" in AI_PROMPT
    assert "Markdown code fence" in AI_PROMPT
