from __future__ import annotations

from datetime import date
from pathlib import Path

from app.booking.capture import audit_rate_fixture_html, sanitize_rate_fixture_html
from app.booking.live_debug import LiveBookingConfig, analyze_html, capture_availability_html

FIXTURES = Path(__file__).parents[1] / "fixtures"


def config(**overrides: object) -> LiveBookingConfig:
    values: dict[str, object] = {
        "property_name": "Example Hotel",
        "hotel_url": "https://www.booking.com/hotel/xx/example.html?sid=secret&label=tracking",
        "check_in": date(2027, 1, 10),
        "check_out": date(2027, 1, 12),
        "adults": 2,
        "children": 0,
        "rooms": 1,
        "room_type": "Economy Triple Room",
        "meal_plan": None,
        "breakfast": True,
        "cancellation_required": True,
        "currency": "eur",
    }
    values.update(overrides)
    return LiveBookingConfig(**values)


def test_config_builds_clean_exact_search_url() -> None:
    parsed = config(children=1, children_ages=[7]).navigation_url()

    assert "sid=" not in parsed
    assert "label=" not in parsed
    assert "checkin=2027-01-10" in parsed
    assert "checkout=2027-01-12" in parsed
    assert "group_adults=2" in parsed
    assert "group_children=1" in parsed
    assert "no_rooms=1" in parsed
    assert "selected_currency=EUR" in parsed
    assert "age=7" in parsed


def test_config_rejects_non_booking_url() -> None:
    try:
        config(hotel_url="https://example.test/hotel/xx/example.html")
    except ValueError as error:
        assert "Booking.com" in str(error)
    else:
        raise AssertionError("non-Booking URL was accepted")


def test_capture_sanitizer_preserves_parser_dom_and_removes_sensitive_content() -> None:
    original = (FIXTURES / "booking_papaya_rates.html").read_text()
    unsafe = (
        '<script>token="secret"</script><style>.x{}</style>'
        '<form action="https://booking.com/account"><input value="Jane Doe"></form>'
        '<a class="offer-1234567890" href="mailto:jane@example.com" '
        'data-session-token="abc">jane@example.com</a>'
        '<p>PIN: 1234 confirmation number: 1234567890 +420 777 123 456</p>'
        + original
    )

    sanitized = sanitize_rate_fixture_html(unsafe)

    assert audit_rate_fixture_html(sanitized) == []
    assert "secret" not in sanitized
    assert "Jane Doe" not in sanitized
    assert "jane@example.com" not in sanitized
    assert "1234567890" not in sanitized
    assert "+420 777 123 456" not in sanitized
    _, match, report = analyze_html(sanitized, config(property_name="Papaya Hostel"))
    assert report["parser_status"] == "success"
    assert report["candidate_count"] == 2
    assert match.accepted


def test_offline_analysis_reports_safe_candidate_contract() -> None:
    html = (FIXTURES / "booking_storhaugen_optional_missing.html").read_text()
    _, _, report = analyze_html(
        html,
        config(
            property_name="STORHAUGEN GARD",
            room_type="Standard Double Room",
            currency="NOK",
            meal_plan="Breakfast",
        ),
    )

    assert report["outcome"] == "no_comparable_offer"
    assert report["reason_code"] == "no_comparable_offer"
    assert report["diagnostic_phase"] == "exact_match"
    assert report["candidate_count"] == 1
    candidate = report["candidates"][0]
    assert candidate["index"] == 1
    assert candidate["room"] == "Standard Double Room"
    assert "meal_plan" in candidate["missing_required_fields"]
    assert "source_row_text" not in candidate


def test_unknown_dom_is_parser_error_in_offer_collection() -> None:
    _, match, report = analyze_html("<main>unknown</main>", config())

    assert not match.accepted
    assert report["outcome"] == "parser_error"
    assert report["reason_code"] == "parser_error"
    assert report["diagnostic_phase"] == "offer_collection"


class _FakeLocator:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def count(self) -> int:
        return len(self.values)

    @property
    def first(self) -> _FakeLocator:
        return self

    def evaluate(self, expression: str) -> str:
        assert "outerHTML" in expression
        return self.values[0]

    def evaluate_all(self, expression: str) -> list[str]:
        assert "outerHTML" in expression
        return self.values


class _LegacyOnlyPage:
    def locator(self, selector: str) -> _FakeLocator:
        if selector == "tr.js-rt-block-row":
            return _FakeLocator(["<tr class='js-rt-block-row'>one</tr>", "<tr>two</tr>"])
        return _FakeLocator([])


def test_capture_wraps_all_legacy_rows_when_shared_table_root_is_absent() -> None:
    captured = capture_availability_html(_LegacyOnlyPage())

    assert captured == (
        "<table><tr class='js-rt-block-row'>one</tr><tr>two</tr></table>"
    )
