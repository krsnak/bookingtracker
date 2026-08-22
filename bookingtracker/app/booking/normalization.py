"""Small localized text and money helpers used only by the Booking adapter."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "Kč": "CZK", "kr": "NOK", "MAD": "MAD"}
_CURRENCY_CODES = "EUR|USD|GBP|CZK|NOK|MAD"
_MONEY = re.compile(
    rf"(?:(€|\$|£|Kč|kr)\s*([\d][\d\s.,]*\d|\d)|"
    rf"([\d][\d\s.,]*\d|\d)\s*(Kč|kr|{_CURRENCY_CODES})|"
    rf"({_CURRENCY_CODES})\s*([\d][\d\s.,]*\d|\d))",
    re.IGNORECASE,
)
_CZECH_MONTHS = {
    "ledna": "January",
    "února": "February",
    "března": "March",
    "dubna": "April",
    "května": "May",
    "června": "June",
    "července": "July",
    "srpna": "August",
    "září": "September",
    "října": "October",
    "listopadu": "November",
    "prosince": "December",
}


def normalize_room_name(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value.casefold()))


def parse_price(value: str) -> tuple[Decimal, str] | None:
    match = _MONEY.search(value)
    if not match:
        return None
    symbol, symbol_amount, suffix_amount, suffix_currency, prefix_currency, prefix_amount = (
        match.groups()
    )
    amount_text = symbol_amount or suffix_amount or prefix_amount
    currency = _CURRENCY_SYMBOLS.get(symbol or suffix_currency or "", prefix_currency or "").upper()
    if not amount_text or not currency:
        return None
    normalized = amount_text.replace(" ", "").replace("\xa0", "")
    if normalized.count(",") == 1 and normalized.count(".") == 0:
        before, after = normalized.split(",")
        normalized = before + after if len(after) == 3 else normalized.replace(",", ".")
    elif normalized.count(",") and normalized.count("."):
        if normalized.rfind(".") > normalized.rfind(","):
            normalized = normalized.replace(",", "")
        else:
            normalized = normalized.replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized), currency
    except InvalidOperation:
        return None


def text_contains(value: str, *phrases: str) -> bool:
    lowered = value.casefold()
    return any(phrase.casefold() in lowered for phrase in phrases)


def parse_cancellation_deadline(value: str) -> datetime | None:
    patterns = (
        r"(?:before|until)\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        r"(?:před|do)\s+(\d{1,2})\.?\s*([A-Za-z]+)\s+(\d{4})",
    )
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if not match:
            continue
        day, month, year = match.groups()
        month = _CZECH_MONTHS.get(month.casefold(), month)
        for date_format in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(f"{day} {month} {year}", date_format)
            except ValueError:
                continue
    return None
