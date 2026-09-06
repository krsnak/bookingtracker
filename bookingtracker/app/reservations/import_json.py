"""Strict, versioned, privacy-preserving JSON imports created by user-selected AI."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, ValidationError, field_validator

from app.reservations.import_document import canonical_booking_hotel_url
from app.reservations.models import (
    FieldConfidence,
    ReservationCandidate,
    ReservationSource,
    RoomBreakdown,
)
from app.reservations.validator import validate_activation

MAX_JSON_BYTES = 64 * 1024
SCHEMA_VERSION = 1
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class JsonImportError(ValueError):
    """A safe failure which never returns the uploaded JSON contents."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Property(_StrictModel):
    name: str | None
    booking_url: str | None


class _Stay(_StrictModel):
    check_in: date | None
    check_out: date | None

    @field_validator("check_in", "check_out", mode="before")
    @classmethod
    def iso_date_or_null(cls, value: object) -> object:
        if value is not None and (not isinstance(value, str) or not _DATE.fullmatch(value)):
            raise ValueError("date must use YYYY-MM-DD")
        return value


class _Occupancy(_StrictModel):
    adults: StrictInt | None
    children: StrictInt | None
    child_ages: list[StrictInt] | None
    rooms: StrictInt | None

    @field_validator("adults", "rooms")
    @classmethod
    def positive_or_null(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("must be positive")
        return value

    @field_validator("children")
    @classmethod
    def non_negative_or_null(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("must be non-negative")
        return value

    @field_validator("child_ages")
    @classmethod
    def valid_ages_or_null(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(age < 0 or age > 17 for age in value):
            raise ValueError("child age must be between 0 and 17")
        return value


class _RoomItem(_StrictModel):
    name: str
    count: StrictInt

    @field_validator("count")
    @classmethod
    def positive_count(cls, value: int) -> int:
        if value < 1:
            raise ValueError("count must be positive")
        return value


class _Room(_StrictModel):
    name: str | None
    breakdown: list[_RoomItem] | None


class _Meal(_StrictModel):
    breakfast_included: StrictBool | None
    meal_plan: str | None


class _Cancellation(_StrictModel):
    text: str | None
    free_cancellation: StrictBool | None
    deadline: datetime | None

    @field_validator("deadline", mode="after")
    @classmethod
    def aware_deadline_or_null(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("deadline must include a timezone")
        return value


class _Payment(_StrictModel):
    conditions: str | None


class _Price(_StrictModel):
    currency: str | None
    booked_total: str | None
    booked_payable: str | None
    booked_base: str | None
    taxes_and_fees: str | None
    vat: str | None
    city_tax: str | None

    @field_validator("currency")
    @classmethod
    def iso_currency_or_null(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z]{3}", value):
            raise ValueError("currency must use a three-letter ISO code")
        return value.upper() if value else value

    @field_validator(
        "booked_total", "booked_payable", "booked_base", "taxes_and_fees", "vat", "city_tax"
    )
    @classmethod
    def decimal_string_or_null(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError) as error:
            raise ValueError("money must be a decimal string") from error
        if not parsed.is_finite():
            raise ValueError("money must be finite")
        return value


class ReservationImportJson(_StrictModel):
    schema_version: StrictInt
    property: _Property
    stay: _Stay
    occupancy: _Occupancy
    room: _Room
    meal: _Meal
    cancellation: _Cancellation
    payment: _Payment
    price: _Price

    @field_validator("schema_version")
    @classmethod
    def supported_schema(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")
        return value


SAMPLE_JSON = json.dumps(
    {
        "schema_version": 1,
        "property": {
            "name": "Example Hotel",
            "booking_url": "https://www.booking.com/hotel/xx/example-hotel.html",
        },
        "stay": {"check_in": "2026-10-10", "check_out": "2026-10-12"},
        "occupancy": {"adults": 2, "children": 0, "child_ages": None, "rooms": 1},
        "room": {"name": "Double Room", "breakdown": [{"name": "Double Room", "count": 1}]},
        "meal": {"breakfast_included": True, "meal_plan": "Breakfast included"},
        "cancellation": {
            "text": "Free cancellation until 2026-10-08 23:59",
            "free_cancellation": True,
            "deadline": "2026-10-08T23:59:00+02:00",
        },
        "payment": {"conditions": "Pay at the property"},
        "price": {
            "currency": "EUR", "booked_total": "250.00", "booked_payable": "250.00",
            "booked_base": "220.00", "taxes_and_fees": "30.00", "vat": "30.00", "city_tax": None,
        },
    },
    ensure_ascii=False,
    indent=2,
) + "\n"

AI_PROMPT = """Nahraj do AI Booking confirmation PDF a vlož tento prompt. Vrať pouze jeden
syntakticky validní JSON objekt bez Markdown code fence, komentářů nebo dalšího textu.

Použij přesně schema_version 1 a pouze pole ze vzorového souboru
BookingTracker-import-example.json. Zachovej strukturu i všechny klíče.

Pokud údaj není přímo a jednoznačně doložen v dokumentu, použij null. Nic neodhaduj,
nedoplňuj z kontextu ani nevymýšlej. Never invent or infer a value merely because it is
common for Booking.com reservations.

Pravidla:
- true znamená výslovně doložené ano, false výslovně doložené ne a null neznámé.
- Absence snídaně, dětí, storno deadline nebo platební podmínky nikdy neznamená false nebo 0.
- booking_url musí být přímá HTTPS Booking hotel URL ve tvaru
  https://www.booking.com/hotel/{country}/{property}.html bez query a fragmentu. Pokud ji
  dokument neobsahuje, použij null; nikdy ji nedohledávej ani nevymýšlej.
- Všechny peníze jsou desetinné řetězce, například "412.00". Přepiš jen explicitně uvedené
  částky; chybějící složky jsou null a nic nesčítej.
- Data jsou YYYY-MM-DD. cancellation.deadline je timezone-aware ISO datum a čas, jinak null.
- children je null, pokud počet dětí není jednoznačný. Pokud je children větší než 0,
  child_ages obsahuje jeden věk pro každé dítě, jinak je null.
- room.breakdown je pole objektů {"name": "...", "count": 1}; pro neznámé použij null.
- Zachovej původní jazyk name, meal_plan, cancellation.text a payment.conditions.
- Nevkládej číslo rezervace, PIN, e-mail, telefon, jména hostů, adresu ani platební údaje.
"""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JsonImportError("JSON obsahuje duplicitní klíč.")
        result[key] = value
    return result


def parse_children_ages(value: str | None) -> list[int] | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        ages = [int(item.strip()) for item in raw.split("|")]
    except ValueError as error:
        raise JsonImportError("Věky dětí musí být celá čísla oddělená znakem |.") from error
    if any(age < 0 or age > 17 for age in ages):
        raise JsonImportError("Věky dětí obsahují neplatnou hodnotu.")
    return ages


def parse_rooms_breakdown(value: str | None) -> list[RoomBreakdown] | None:
    raw = (value or "").strip()
    if not raw:
        return None
    rooms: list[RoomBreakdown] = []
    for item in raw.split("|"):
        count_text, separator, room_name = item.strip().partition(":")
        if not separator or not room_name.strip():
            raise JsonImportError("Rozpis pokojů musí používat formát počet:typ oddělený |.")
        try:
            rooms.append(RoomBreakdown(count=int(count_text), room_type=room_name.strip()))
        except (ValidationError, ValueError) as error:
            raise JsonImportError("Rozpis pokojů obsahuje neplatný pokoj.") from error
    return rooms


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def json_candidate(contents: bytes) -> ReservationCandidate:
    """Validate one untrusted JSON object and map it directly to the domain candidate."""
    if len(contents) > MAX_JSON_BYTES:
        raise JsonImportError("JSON soubor je příliš velký.")
    try:
        parsed = json.loads(contents.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except UnicodeDecodeError as error:
        raise JsonImportError("JSON musí být v kódování UTF-8.") from error
    except (json.JSONDecodeError, JsonImportError) as error:
        if isinstance(error, JsonImportError):
            raise
        raise JsonImportError("JSON nelze přečíst.") from error
    if not isinstance(parsed, dict):
        raise JsonImportError("JSON musí obsahovat právě jeden objekt rezervace.")
    try:
        document = ReservationImportJson.model_validate(parsed)
    except ValidationError as error:
        if any(item["loc"] == ("schema_version",) for item in error.errors()):
            raise JsonImportError("JSON používá nepodporovanou verzi schématu.") from error
        raise JsonImportError("JSON obsahuje neplatnou nebo nepovolenou strukturu.") from error
    raw_url = document.property.booking_url
    booking_url = canonical_booking_hotel_url(raw_url) if raw_url else None
    if raw_url and booking_url is None:
        raise JsonImportError("booking_url musí být přímý HTTPS odkaz na hotel na Booking.com.")
    try:
        candidate = ReservationCandidate(
            source=ReservationSource.AI_JSON,
            property_name=document.property.name,
            booking_url=booking_url,
            check_in=document.stay.check_in,
            check_out=document.stay.check_out,
            adults=document.occupancy.adults,
            children=document.occupancy.children,
            children_ages=document.occupancy.child_ages,
            rooms_count=document.occupancy.rooms,
            room_type=document.room.name,
            rooms_breakdown=(
                [
                    RoomBreakdown(room_type=item.name, count=item.count)
                    for item in document.room.breakdown
                ]
                if document.room.breakdown is not None
                else None
            ),
            meal_plan=document.meal.meal_plan,
            breakfast_included=document.meal.breakfast_included,
            cancellation_text=document.cancellation.text,
            free_cancellation=document.cancellation.free_cancellation,
            cancellation_deadline=document.cancellation.deadline,
            booked_total_price=_decimal(document.price.booked_total),
            booked_payable_price=_decimal(document.price.booked_payable),
            booked_base_price=_decimal(document.price.booked_base),
            taxes_and_fees=_decimal(document.price.taxes_and_fees),
            vat=_decimal(document.price.vat),
            city_tax=_decimal(document.price.city_tax),
            currency=document.price.currency,
            payment_conditions=document.payment.conditions,
            source_text="AI JSON import (schema version 1)",
            extraction_confidence=1,
            field_confidence={"schema_version": FieldConfidence.HIGH},
        )
    except (InvalidOperation, ValidationError) as error:
        raise JsonImportError("JSON obsahuje neplatné hodnoty rezervace.") from error
    if candidate.children and (
        candidate.children_ages is None or len(candidate.children_ages) != candidate.children
    ):
        raise JsonImportError("Počet věků dětí neodpovídá children.")
    if candidate.rooms_breakdown and (
        sum(room.count for room in candidate.rooms_breakdown) != candidate.rooms_count
    ):
        raise JsonImportError("Součet room.breakdown neodpovídá occupancy.rooms.")
    if candidate.rooms_count and candidate.rooms_count > 1 and not candidate.rooms_breakdown:
        raise JsonImportError("Pro více pokojů je nutné uvést room.breakdown.")
    if candidate.check_in and candidate.check_out:
        candidate = candidate.model_copy(
            update={"nights": (candidate.check_out - candidate.check_in).days}
        )
    validation = validate_activation(candidate)
    return candidate.model_copy(
        update={
            "missing_critical_fields": validation.missing_fields,
            "validation_errors": validation.errors,
        }
    )
