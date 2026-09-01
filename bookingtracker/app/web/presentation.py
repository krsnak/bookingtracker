"""Small, explicit Czech presentation helpers for server-rendered pages."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

_STATUS_LABELS = {
    "stopped": "Zastaveno", "starting": "Spouští se", "ready": "Připraven",
    "logged_out": "Booking.com vyžaduje přihlášení", "login_required": "Je nutné přihlášení",
    "captcha_required": "Je nutný ruční zásah", "error": "Chyba",
    "authenticated": "Přihlášeno", "unknown": "Neznámé", "success": "Kontrola dokončena",
    "timeout": "Kontrolu ceny se nepodařilo dokončit včas",
    "navigation_error": "Stránku se nepodařilo otevřít",
    "browser_crash": "Prohlížeč přestal odpovídat", "page_closed": "Stránka byla zavřena",
    "disabled": "Vypnuto", "starting_display": "Spouští se obrazovka",
    "session_active": "Ruční relace je aktivní", "stopping": "Ukončuje se",
    "no_availability": "Nabídka není k dispozici",
    "incomplete_reservation": "Doplňte údaje rezervace",
    "unsupported_structure": "Nepodporovaná struktura nabídky",
    "partial": "Nabídka je neúplná", "no_match": "Odpovídající nabídka nebyla nalezena",
    "ambiguous": "Nabídku nelze bezpečně porovnat", "parser_error": "Nepodařilo se přečíst nabídku",
    "browser_error": "Problém s prohlížečem", "exact": "Přesná shoda",
    "equivalent": "Srovnatelná nabídka",
    "better": "Lepší nabídka", "upgrade_candidate": "Kandidát na lepší nabídku",
    "rejected": "Odmítnuto", "final_total_including_taxes": "Celková cena včetně poplatků",
    "lower": "Nižší", "same": "Stejná", "higher": "Vyšší",
    "price_drop": "Pokles ceny", "new_historical_low": "Nové historické minimum",
    "better_rate_found": "Nalezena lepší nabídka", "check_failed": "Kontrola se nezdařila",
    "info": "Informace", "warning": "Upozornění", "action_required": "Vyžaduje zásah",
    "pending": "Čeká na doručení", "delivered": "Doručeno", "failed": "Doručení se nezdařilo",
    "manual": "Ruční", "manual_all": "Ruční hromadná", "scheduler": "Naplánováno",
    "high": "Vysoká", "medium": "Střední",
    "low": "Nízká", "pasted_booking_confirmation": "Vložené potvrzení Booking.com",
    "booking_confirmation_pdf": "PDF potvrzení Booking.com", "text": "Text", "pdf": "PDF",
    "active": "Aktivní", "inactive": "Neaktivní", "running": "Spuštěno",
    "completed": "Kontrola dokončena",
}
_MONTHS = (
    "ledna", "února", "března", "dubna", "května", "června",
    "července", "srpna", "září", "října", "listopadu", "prosince",
)


def status_label(value: object) -> str:
    raw = value.value if isinstance(value, Enum) else str(value or "")
    return _STATUS_LABELS.get(raw.casefold(), "Neznámý stav")


def format_date(value: date | datetime | None) -> str:
    if value is None:
        return "Neuvedeno"
    return f"{value.day}. {_MONTHS[value.month - 1]} {value.year}"


def format_date_range(start: date | None, end: date | None) -> str:
    if not start or not end:
        return "Neuvedeno"
    if start.year == end.year and start.month == end.month:
        return f"{start.day}.–{end.day}. {_MONTHS[end.month - 1]} {end.year}"
    return f"{format_date(start)} – {format_date(end)}"


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "Neuvedeno"
    return f"{format_date(value)} {value:%H:%M}"


def format_money(value: Decimal | None, currency: str | None = None) -> str:
    if value is None:
        return "Neuvedeno"
    formatted = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"{formatted} {currency}" if currency else formatted


def format_bool(value: bool | None) -> str:
    return "Ano" if value is True else "Ne" if value is False else "Neuvedeno"
