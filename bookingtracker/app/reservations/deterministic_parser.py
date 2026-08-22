"""Conservative, section-aware parsing for pasted Booking confirmation text."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.reservations.models import PriceBreakdown, RoomBreakdown

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE_PATTERN = re.compile(
    rf"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*)?"
    rf"({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})",
    re.IGNORECASE,
)
_ISO_DATE_PATTERN = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "Kč": "CZK", "zł": "PLN"}
_AMOUNT_PATTERN = re.compile(
    r"(?:(€|\$|£|Kč|zł)\s*([\d][\d\s.,]*\d|\d)|"
    r"([\d][\d\s.,]*\d|\d)\s*(EUR|USD|GBP|CZK|PLN|NOK))",
    re.IGNORECASE,
)


def clean_lines(source_text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in source_text.splitlines() if line.strip()]


def parse_date(value: str) -> date | None:
    iso = _ISO_DATE_PATTERN.search(value)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    match = _DATE_PATTERN.search(value)
    if not match:
        return None
    try:
        return datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
    except ValueError:
        return None


def parse_money(value: str) -> tuple[Decimal, str] | None:
    prefix_match = re.search(r"\b(EUR|USD|GBP|CZK|PLN|NOK)\s+([\d][\d\s.,]*\d|\d)", value, re.I)
    if prefix_match:
        code, amount_text = prefix_match.groups()
        return _decimal_from_text(amount_text), code.upper()
    match = _AMOUNT_PATTERN.search(value)
    if not match:
        return None
    symbol, symbol_amount, code_amount, code = match.groups()
    amount_text = symbol_amount or code_amount
    currency = _CURRENCY_SYMBOLS.get(symbol or "", (code or "").upper())
    if not amount_text or not currency:
        return None
    return _decimal_from_text(amount_text), currency


def _decimal_from_text(amount_text: str) -> Decimal:
    normalized = amount_text.replace(" ", "")
    if normalized.count(",") == 1 and normalized.count(".") == 0:
        normalized = normalized.replace(",", ".")
    elif normalized.count(",") and normalized.count("."):
        if normalized.rfind(".") > normalized.rfind(","):
            normalized = normalized.replace(",", "")
        else:
            normalized = normalized.replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        raise ValueError(f"invalid amount: {amount_text}") from None


def find_after_heading(lines: list[str], heading: str) -> str | None:
    for index, line in enumerate(lines):
        if line.casefold() == heading.casefold() and index + 1 < len(lines):
            return lines[index + 1]
    return None


def parse_property_name(lines: list[str]) -> str | None:
    for line in lines:
        match = re.match(r"(?:property|property name|accommodation|hotel)\s*:\s*(.+)", line, re.I)
        if match:
            return match.group(1).strip()
    blocked = {"booking.com", "booking confirmation", "booking information", "your reservation"}
    for line in lines[:8]:
        if line.casefold() not in blocked and not parse_date(line) and not parse_money(line):
            if (
                len(line) > 2
                and not re.match(r"^\d", line)
                and not re.search(r"^(arrival|departure|reservation|guests)", line, re.I)
            ):
                return line
    return None


def parse_dates(lines: list[str]) -> tuple[date | None, date | None]:
    arrivals: list[date] = []
    departures: list[date] = []
    for index, line in enumerate(lines):
        nearby = " ".join(lines[index : index + 2])
        parsed = parse_date(nearby)
        if parsed and re.search(r"^(arrival|check-in|check in)$", line, re.I):
            arrivals.append(parsed)
        if parsed and re.search(r"^(departure|check-out|check out)$", line, re.I):
            departures.append(parsed)
    if arrivals or departures:
        return (arrivals[0] if arrivals else None, departures[0] if departures else None)
    all_dates = [parsed for line in lines if (parsed := parse_date(line))]
    return (all_dates[0], all_dates[1]) if len(all_dates) >= 2 else (None, None)


def parse_occupancy(lines: Iterable[str]) -> tuple[int | None, int | None, list[int] | None]:
    joined = "\n".join(lines)
    adults_match = re.search(r"\b(\d+)\s+adults?\b", joined, re.I)
    children_match = re.search(r"\b(\d+)\s+child(?:ren)?\b", joined, re.I)
    ages_match = re.search(r"(?:children(?:'s)? ages?|ages?)\s*[:\-]?\s*([\d, /]+)", joined, re.I)
    ages = [int(item) for item in re.findall(r"\d+", ages_match.group(1))] if ages_match else None
    return (
        int(adults_match.group(1)) if adults_match else None,
        int(children_match.group(1)) if children_match else None,
        ages,
    )


def parse_room(lines: list[str]) -> tuple[int | None, str | None, list[RoomBreakdown] | None]:
    for line in lines:
        nights_match = re.search(r"\b\d+\s+nights?\s*,\s*(.+)$", line, re.I)
        if nights_match and re.search(
            r"\b(room|suite|dormitory|apartment|studio)\b", nights_match.group(1), re.I
        ):
            room_type = nights_match.group(1).strip(" ,")
            return 1, room_type, [RoomBreakdown(room_type=room_type, count=1)]
        match = re.search(
            r"\b(\d+)\s+(?:night[s, ]+)?"
            r"([A-Z][^€$£]*?(?:Room|Suite|Dormitory|Apartment|Studio))\b",
            line,
        )
        if match:
            room_type = match.group(2).strip(" ,")
            return 1, room_type, [RoomBreakdown(room_type=room_type, count=1)]
        match = re.match(r"\s*(\d+)\s+(.+?)\s+(?:€|\$|£|Kč|zł|\d)", line)
        if match and re.search(
            r"\b(room|suite|dormitory|apartment|studio)\b", match.group(2), re.I
        ):
            count = int(match.group(1))
            room_type = match.group(2).strip()
            return count, room_type, [RoomBreakdown(room_type=room_type, count=count)]
    return None, None, None


def parse_nights(lines: Iterable[str]) -> int | None:
    match = re.search(r"\b(\d+)\s+nights?\b", "\n".join(lines), re.I)
    return int(match.group(1)) if match else None


def parse_prices(lines: list[str]) -> tuple[PriceBreakdown, list[str], list[str]]:
    prices = PriceBreakdown()
    warnings: list[str] = []
    ambiguous: list[str] = []
    in_payment = False
    totals_by_section: dict[str, list[Decimal]] = {"price": [], "payment": []}
    for line in lines:
        lowered = line.casefold()
        if "payment information" in lowered:
            in_payment = True
            continue
        if "price information" in lowered:
            in_payment = False
            continue
        money = parse_money(line)
        if not money:
            continue
        amount, currency = money
        prices.currency = prices.currency or currency
        if prices.currency != currency:
            warnings.append(f"multiple currencies found ({prices.currency}, {currency})")
            continue
        if re.search(r"\bvat\b", line, re.I):
            prices.vat = amount
        elif re.search(r"city tax", line, re.I):
            prices.city_tax = amount
        elif re.search(r"\b(?:tax|taxes|fees?)\b", line, re.I):
            prices.taxes_and_fees = amount
        elif re.search(r"total future payments?|amount payable|payable", line, re.I):
            prices.payable_price = amount
        elif re.search(r"total price|reservation total", line, re.I):
            section = "payment" if in_payment else "price"
            totals_by_section[section].append(amount)
            if in_payment:
                prices.payable_price = prices.payable_price or amount
            else:
                prices.total_price = prices.total_price or amount
        elif re.match(r"\s*\d+\s+.+?(?:room|suite|dormitory|apartment|studio).+", line, re.I):
            prices.base_price = prices.base_price or amount
    for section, totals in totals_by_section.items():
        if len(set(totals)) > 1:
            ambiguous.append("booked_total_price" if section == "price" else "booked_payable_price")
            warnings.append(f"conflicting total amounts in {section} section")
    if prices.taxes_and_fees is None and prices.vat is not None and prices.city_tax is not None:
        prices.taxes_and_fees = prices.vat + prices.city_tax
    return prices, warnings, ambiguous


def parse_cancellation(lines: list[str]) -> tuple[str | None, bool | None, datetime | None]:
    relevant: list[str] = []
    in_section = False
    for line in lines:
        if re.search(r"cancellation conditions?|cancellation fee", line, re.I):
            in_section = True
        if in_section and re.search(r"^(price|payment) information", line, re.I):
            break
        if in_section or re.search(r"non-refundable|refundable", line, re.I):
            relevant.append(line)
    if not relevant:
        return None, None, None
    text = " ".join(relevant)
    if re.search(r"non-refundable", text, re.I):
        free = False
    elif re.search(r"free (?:of charge )?cancellation|cancel .*free of charge", text, re.I):
        free = True
    else:
        free = None
    deadline_match = re.search(
        rf"(?:until|before)\s+(?:[A-Za-z]+,?\s+)?({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})(?:\s+(\d{{1,2}}):(\d{{2}}))?",
        text,
        re.I,
    )
    deadline = None
    if deadline_match:
        month, day, year, hour, minute = deadline_match.groups()
        try:
            timestamp = f"{month} {day} {year} {hour or '0'}:{minute or '0'}"
            deadline = datetime.strptime(timestamp, "%B %d %Y %H:%M")
        except ValueError:
            pass
    return text, free, deadline


def parse_meal_facts(lines: Iterable[str]) -> tuple[str | None, bool | None]:
    joined = "\n".join(lines)
    if re.search(r"breakfast (?:is )?included|includes breakfast", joined, re.I):
        return "Breakfast included", True
    if re.search(r"breakfast (?:is )?not included|no breakfast", joined, re.I):
        return None, False
    return None, None
