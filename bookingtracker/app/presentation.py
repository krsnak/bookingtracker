"""Shared Czech presentation for check outcomes and persisted reason codes."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from app.pricing.diagnostics import reason_code_for
from app.pricing.models import CheckReasonCode, PriceCheckRecord, PriceCheckStatus

_REASON_TEXTS = {
    CheckReasonCode.TIMEOUT: "Kontrolu ceny se nepodařilo dokončit v časovém limitu.",
    CheckReasonCode.NAVIGATION_ERROR: (
        "Booking.com se nepodařilo otevřít nebo dokončit načtení stránky."
    ),
    CheckReasonCode.NETWORK_ERROR: (
        "Kontrolu se nepodařilo provést kvůli problému síťového připojení."
    ),
    CheckReasonCode.BROWSER_ERROR: (
        "Kontrolu se nepodařilo provést kvůli problému prohlížeče."
    ),
    CheckReasonCode.LOGIN_REQUIRED: (
        "Pro pokračování je nutné znovu se přihlásit na Booking.com."
    ),
    CheckReasonCode.CAPTCHA_REQUIRED: "Booking.com vyžaduje ruční ověření CAPTCHA.",
    CheckReasonCode.PARSER_ERROR: (
        "Stránka se načetla, ale nepodařilo se z ní bezpečně přečíst odpovídající nabídku."
    ),
    CheckReasonCode.NO_COMPARABLE_OFFER: (
        "Nebyla nalezena nabídka, kterou lze bezpečně porovnat s rezervací."
    ),
    CheckReasonCode.UNEXPECTED_ERROR: "Kontrola skončila neočekávanou technickou chybou.",
}


def check_reason_text(value: CheckReasonCode | str | None) -> str:
    try:
        reason = value if isinstance(value, CheckReasonCode) else CheckReasonCode(value or "")
    except ValueError:
        return "Podrobnosti nejsou k dispozici."
    return _REASON_TEXTS[reason]


def check_reason_for(record: PriceCheckRecord) -> CheckReasonCode | None:
    return record.reason_code or reason_code_for(record.status, record.safe_error_detail)


def check_result_text(record: PriceCheckRecord) -> str:
    if record.status is PriceCheckStatus.SUCCESS:
        return "Cena zkontrolována"
    if record.status is PriceCheckStatus.LOGGED_OUT:
        return "Nutné přihlášení"
    if record.status is PriceCheckStatus.CAPTCHA_REQUIRED:
        return "Nutné ověření CAPTCHA"
    if record.status in {
        PriceCheckStatus.NO_MATCH,
        PriceCheckStatus.AMBIGUOUS,
        PriceCheckStatus.NO_AVAILABILITY,
    }:
        return "Nabídku nelze bezpečně porovnat"
    return "Kontrolu se nepodařilo dokončit"


def manual_check_flash(record: PriceCheckRecord) -> str:
    if record.status is PriceCheckStatus.SUCCESS:
        return "Kontrola ceny byla dokončena."
    if record.status is PriceCheckStatus.LOGGED_OUT:
        return "Pro pokračování je nutné přihlášení na Booking.com."
    if record.status is PriceCheckStatus.CAPTCHA_REQUIRED:
        return "Booking.com vyžaduje ruční ověření CAPTCHA."
    if record.status in {
        PriceCheckStatus.NO_MATCH,
        PriceCheckStatus.AMBIGUOUS,
        PriceCheckStatus.NO_AVAILABILITY,
    }:
        return (
            "Kontrola byla dokončena, ale nebyla nalezena bezpečně "
            "porovnatelná nabídka."
        )
    return "Kontrolu ceny se nepodařilo dokončit. Podrobnosti jsou uvedeny níže."


def format_check_datetime(value: datetime | None) -> str:
    if value is None:
        return "Neuvedeno"
    return f"{value.day}. {value.month}. {value.year} v {value.hour}:{value:%M}"


def format_duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "Neuvedeno"
    if duration_ms < 1000:
        return f"{duration_ms} ms"
    seconds = f"{duration_ms / 1000:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{seconds} s"


def failure_count_word(count: int) -> str:
    return {1: "jednou", 2: "dvakrát", 3: "třikrát"}.get(count, f"{count}krát")


def enum_value(value: object | None) -> str:
    return value.value if isinstance(value, Enum) else str(value or "neuvedeno")
