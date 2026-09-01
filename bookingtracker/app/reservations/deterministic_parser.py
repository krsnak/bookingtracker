"""Conservative, section-aware parsing for pasted Booking confirmation text."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.reservations.import_document import canonical_booking_hotel_url as _canonical_hotel_url
from app.reservations.models import PriceBreakdown, RoomBreakdown

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_CZECH_MONTHS = {
    "ledna": 1,
    "února": 2,
    "března": 3,
    "dubna": 4,
    "května": 5,
    "června": 6,
    "července": 7,
    "srpna": 8,
    "září": 9,
    "října": 10,
    "listopadu": 11,
    "prosince": 12,
}
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
_PROPERTY_EXCLUSIONS = (
    r"arrival|departure|reservation|guests|příjezd|odjezd|"
    r"vaše rezervace|rezervace pro|booking url"
)
_CANCELLATION_HEADINGS = (
    r"cancellation conditions?|cancellation fee|podmínky zrušení rezervace|"
    r"poplatek za zrušení rezervace"
)
_LOCALE_LABELS = {
    "arrival": ("arrival", "check-in", "check in", "příjezd"),
    "departure": ("departure", "check-out", "check out", "odjezd"),
    "price": ("price information", "informace o ceně"),
    "payment": ("payment information", "informace o platbě"),
}
_URL_PATTERN = re.compile(r"(?:https?://|mailto:|tel:|about:)[^\s<>()]+", re.IGNORECASE)
_BOOKING_HOTEL_URL = re.compile(
    r"https?://(?:[a-z]{2}\.)?(?:www\.)?booking\.com/hotel/[^\s?#)]+", re.IGNORECASE
)
_PROPERTY_UI_TEXT = {
    "není vybrána žádná položka",
    "přeskočit na obsah",
    "booking.com",
    "booking confirmation",
    "booking information",
    "your reservation",
}
_PROPERTY_CTA_TEXT = {
    "zjistit více", "find out more", "upravit rezervaci", "manage booking",
    "zaplatit hned", "pay now", "booking.com", "více informací",
}
INVALID_DATE_RANGE_MESSAGE = (
    "Nepodařilo se spolehlivě určit datum příjezdu a odjezdu. Zkontrolujte vložené potvrzení."
)


def canonical_booking_hotel_url(value: str) -> str | None:
    """Return only a canonical hotel page, never a confirmation or payment link."""
    match = _BOOKING_HOTEL_URL.search(value)
    if not match:
        return None
    return _canonical_hotel_url(match.group(0))


def sanitize_source_text(source_text: str) -> str:
    """Keep useful confirmation facts while preventing pasted mail metadata from persistence."""
    sanitized: list[str] = []
    for raw in source_text.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        newline = raw[len(line) :]
        if re.search(r"\b(?:PIN|reservation confirmation number|číslo rezervace)\b", line, re.I):
            continue

        def replace_url(match: re.Match[str]) -> str:
            canonical = canonical_booking_hotel_url(match.group(0))
            return canonical or "[link removed]"

        line = _URL_PATTERN.sub(replace_url, line)
        line = re.sub(r"!\[[^]]*\]\([^)]*\)", "", line)
        sanitized.append(f"{line}{newline}")
    return "".join(sanitized)


def clean_lines(source_text: str) -> list[str]:
    """Normalize safe Markdown presentation into semantic confirmation lines."""
    lines: list[str] = []
    for raw in source_text.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?", line):
            continue
        line = re.sub(r"!\[[^]]*\]\([^)]*\)", "", line)  # Gmail/content images have no facts.
        links = re.findall(r"(?<!!)\[([^]]+)\]\(([^)]+)\)", line)
        for label, url in links:
            line = line.replace(f"[{label}]({url})", label)
            if canonical := canonical_booking_hotel_url(url):
                lines.append(f"Booking property: {label.strip()}")
                lines.append(f"Booking URL: {canonical}")
        for url in _URL_PATTERN.findall(line):
            if canonical := canonical_booking_hotel_url(url):
                lines.append(f"Booking URL: {canonical}")
        line = _URL_PATTERN.sub("", line)
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = re.sub(r"^#{1,6}\s*", "", line)
        if line.startswith("|") and line.endswith("|"):
            cells = [re.sub(r"\s+", " ", cell).strip() for cell in line.strip("|").split("|")]
            cells = [cell for cell in cells if cell]
            if len(cells) == 2:
                lines.append(f"{cells[0]}: {cells[1]}")
            elif len(cells) == 1:
                lines.append(cells[0])
            elif len(cells) > 1:
                # Gmail occasionally adds empty layout columns.  The outer cells
                # remain the semantic label/value pair.
                lines.append(f"{cells[0]}: {cells[-1]}")
            continue
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def parse_date(value: str) -> date | None:
    iso = _ISO_DATE_PATTERN.search(value)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    match = _DATE_PATTERN.search(value)
    if not match:
        czech = re.search(r"\b(\d{1,2})\.\s*([A-Za-záčďéěíňóřšťúůýž]+)\s+(\d{4})", value, re.I)
        if czech:
            day, month, year = czech.groups()
            month_number = _CZECH_MONTHS.get(month.casefold())
            if month_number:
                try:
                    return date(int(year), month_number, int(day))
                except ValueError:
                    return None
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
    anchored = parse_anchored_property_name(lines)
    if anchored:
        return anchored
    candidates = _property_candidates(lines)
    return candidates[0][0] if candidates else None


def parse_anchored_property_name(lines: list[str]) -> str | None:
    """Booking's waiting sentence is an authoritative property identity anchor."""
    anchors = _anchored_property_names(lines)
    normalized = {re.sub(r"\W+", "", name.casefold()): name for name in anchors}
    return next(iter(normalized.values())) if len(normalized) == 1 else None


def has_conflicting_anchored_properties(lines: list[str]) -> bool:
    anchors = _anchored_property_names(lines)
    return len({re.sub(r"\W+", "", name.casefold()) for name in anchors}) > 1


def _anchored_property_names(lines: list[str]) -> list[str]:
    anchors: list[str] = []
    for index in range(len(lines)):
        evidence = " ".join(lines[index : index + 3])
        match = re.search(
            r"\b(?:Ubytování|Accommodation)\s+(.+?)\s+"
            r"(?:vás bude očekávat|will be waiting for you)\b",
            evidence,
            re.I,
        )
        if match:
            name = re.sub(r"[*_]+", "", match.group(1)).strip(" .,:;-—")
            if _is_property_name(name):
                anchors.append(name)
    return anchors


def _property_candidates(lines: list[str]) -> list[tuple[str, int]]:
    """Rank explicit Booking property evidence; generic UI text can never win."""
    candidates: dict[str, int] = {}

    def add(value: str, score: int) -> None:
        name = value.strip(" .:-")
        if _is_property_name(name):
            candidates[name] = max(candidates.get(name, 0), score)

    for line in lines:
        match = re.match(r"Booking property:\s*(.+)", line, re.I)
        if match:
            add(match.group(1), 100)
    # Booking's hotel heading is immediately before the information section.
    for index, line in enumerate(lines):
        if line.casefold() in {"booking information", "informace o rezervaci"}:
            for candidate in reversed(lines[max(0, index - 3) : index]):
                if (
                    _is_property_name(candidate)
                    and not parse_date(candidate)
                    and not parse_money(candidate)
                    and not candidate.casefold().startswith("booking property:")
                ):
                    add(candidate, 100)
    for index, _line in enumerate(lines):
        evidence = " ".join(lines[index : index + 3])
        match = re.search(r"\baccommodation\s+(.+?)\s+will be waiting for you\b", evidence, re.I)
        if match:
            add(match.group(1), 95)
        match = re.search(r"\bubytování\s+(.+?)\s+vás bude očekávat\b", evidence, re.I)
        if match:
            add(match.group(1), 95)
        title = re.search(
            r"^PDF title:\s*(?:Gmail\s*-\s*)?.*?([A-Z][^-–—]+?)\s*"
            r"[–—-]\s*(?:Děkujeme|Thank)",
            evidence,
            re.I,
        )
        if title:
            add(title.group(1).replace("🛄", ""), 80)
    for line in lines:
        match = re.match(
            r"(?:property|property name|accommodation|hotel|ubytování)\s*:\s*(.+)", line, re.I
        )
        if match:
            add(match.group(1), 70)
    for line in lines:
        if (
            line.casefold() not in _PROPERTY_UI_TEXT
            and not parse_date(line)
            and not parse_money(line)
        ):
            if (
                len(line) > 2
                and not re.match(r"^\d", line)
                and not re.search(rf"^({_PROPERTY_EXCLUSIONS})", line, re.I)
                and _is_property_name(line)
            ):
                add(line, 10)
    return sorted(candidates.items(), key=lambda item: (-item[1], item[0].casefold()))


def parse_property_aliases(lines: list[str], primary: str | None) -> list[str]:
    """Keep explicit accommodation names as review evidence, never as a matcher shortcut."""
    aliases: list[str] = []
    for name, score in _property_candidates(lines):
        if name != primary and score >= 80:
            aliases.append(name)
    return list(dict.fromkeys(aliases))


def _is_property_name(value: str) -> bool:
    return bool(
        value
        and value.casefold() not in _PROPERTY_UI_TEXT
        and value.casefold() not in _PROPERTY_CTA_TEXT
        and len(value) <= 100
        and value.count(".") <= 1
        and not re.search(
            r"(?:gmail|přeskočit|vybrána žádná|booking url|^pdf title:|zrušit|storno|"
            r"cena|nikdy vás|platb|we will never|payment|rezervaci můžete)",
            value,
            re.I,
        )
    )


def parse_dates_with_evidence(
    lines: list[str],
) -> tuple[date | None, date | None, list[str], list[str]]:
    """Prefer labelled stay dates and never return a model-invalid date range."""
    arrivals: list[date] = []
    departures: list[date] = []
    labelled_indexes: set[int] = set()
    for index, line in enumerate(lines):
        nearby = " ".join(lines[index : index + 2])
        parsed = parse_date(nearby)
        arrival_label = r"^(?:" + "|".join(_LOCALE_LABELS["arrival"]) + r")\s*:"
        if parsed and re.search(arrival_label, line, re.I):
            arrivals.append(parsed)
            labelled_indexes.add(index)
        departure_label = r"^(?:" + "|".join(_LOCALE_LABELS["departure"]) + r")\s*:"
        if parsed and re.search(departure_label, line, re.I):
            departures.append(parsed)
            labelled_indexes.add(index)
    if arrivals or departures:
        check_in = arrivals[0] if arrivals else None
        check_out = departures[0] if departures else None
        if check_in and check_out and check_out > check_in:
            fallback_dates = [
                parsed
                for index, parsed in _fallback_stay_dates(lines)
                if index not in labelled_indexes
            ]
            warnings = (
                ["Explicitní data pobytu mají přednost před konfliktním pomocným datem."]
                if any(value not in {check_in, check_out} for value in fallback_dates)
                else []
            )
            return check_in, check_out, warnings, []
        if check_in is None or check_out is None:
            return check_in, check_out, ["Chybí jedno z explicitně označených dat pobytu."], []
        return (
            None,
            None,
            ["Nekonzistentní explicitní data pobytu byla odmítnuta."],
            [INVALID_DATE_RANGE_MESSAGE],
        )
    all_dates = [parsed for _, parsed in _fallback_stay_dates(lines)]
    if len(all_dates) < 2:
        return (all_dates[0] if all_dates else None), None, [], []
    check_in, check_out = all_dates[0], all_dates[1]
    if check_out > check_in:
        return check_in, check_out, [], []
    return (
        None,
        None,
        ["Nekonzistentní pomocná data pobytu byla odmítnuta."],
        [INVALID_DATE_RANGE_MESSAGE],
    )


def _is_cancellation_date_line(line: str) -> bool:
    return bool(
        re.search(
            r"cancel|cancellation|storno|zrušení|poplatek|zdarma do|free of charge", line, re.I
        )
    )


def _fallback_stay_dates(lines: Iterable[str]) -> list[tuple[int, date]]:
    """Exclude cancellation sections before considering unlabelled date evidence."""
    dates: list[tuple[int, date]] = []
    in_cancellation = False
    for index, line in enumerate(lines):
        if re.search(_CANCELLATION_HEADINGS, line, re.I):
            in_cancellation = True
            continue
        if in_cancellation and re.search(
            r"^(?:booking property|booking url|informace o rezervaci|reservation information|"
            r"arrival|departure|check-in|check-out|příjezd|odjezd)\b",
            line,
            re.I,
        ):
            in_cancellation = False
        if in_cancellation or _is_cancellation_date_line(line):
            continue
        if parsed := parse_date(line):
            dates.append((index, parsed))
    return dates


def parse_dates(lines: list[str]) -> tuple[date | None, date | None]:
    """Backward-compatible date extraction without exposing parser evidence."""
    check_in, check_out, _, _ = parse_dates_with_evidence(lines)
    return check_in, check_out


def parse_occupancy(lines: Iterable[str]) -> tuple[int | None, int | None, list[int] | None]:
    """Parse one explicit guest block without inferring children from document silence.

    An adult count is reliable only when every adult mention agrees.  Children
    may appear on the same line or immediately adjacent confirmation lines; if
    that recognized block has adults but no child statement, it explicitly means
    zero children.  Conflicting counts deliberately remain unknown.
    """
    values = list(lines)
    adult_pattern = re.compile(r"\b(\d+)\s+(?:adults?|dospěl[ií])\b", re.I)
    child_pattern = re.compile(r"\b(\d+)\s+(?:child(?:ren)?|dět[ií]|dítě)\b", re.I)
    age_pattern = re.compile(
        r"(?:children(?:'s)?\s+ages?|child\s+ages?|věk(?:\s+(?:dětí|dítěte))?)"
        r"\s*[:\-]?\s*([\d, /]+)",
        re.I,
    )
    adult_mentions = [(index, int(match.group(1))) for index, line in enumerate(values)
                      if (match := adult_pattern.search(line))]
    if not adult_mentions or len({count for _, count in adult_mentions}) != 1:
        return None, None, None

    adult_indexes = {index for index, _ in adult_mentions}
    nearby_indexes = {
        index + offset
        for index in adult_indexes
        for offset in (-1, 0, 1)
        if 0 <= index + offset < len(values)
    }
    child_mentions = [
        (index, int(match.group(1)))
        for index, line in enumerate(values)
        if (match := child_pattern.search(line))
    ]
    # An explicit child count outside the recognized block could refer to a
    # different reservation/mail fragment; do not combine it with this booking.
    if any(index not in nearby_indexes for index, _ in child_mentions):
        return adult_mentions[0][1], None, None
    if child_mentions and len({count for _, count in child_mentions}) != 1:
        return adult_mentions[0][1], None, None

    ages: list[int] | None = None
    age_mentions = [
        (index, [int(item) for item in re.findall(r"\d+", match.group(1))])
        for index, line in enumerate(values)
        if (match := age_pattern.search(line))
    ]
    if any(index not in nearby_indexes for index, _ in age_mentions):
        return adult_mentions[0][1], None, None
    if age_mentions:
        flattened = [age for _, found in age_mentions for age in found]
        ages = flattened or None

    children = child_mentions[0][1] if child_mentions else 0
    if ages is not None and children != len(ages):
        return adult_mentions[0][1], None, None
    return adult_mentions[0][1], children, ages


def parse_room(lines: list[str]) -> tuple[int | None, str | None, list[RoomBreakdown] | None]:
    for line in lines:
        nights_match = re.search(r"\b\d+\s+(?:nights?|noc(?:i)?)\s*,\s*(.+)$", line, re.I)
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
        czech = re.search(r"(?:vaše rezervace\s*:\s*)?\d+\s+noc(?:i)?\s*,\s*(.+)$", line, re.I)
        if czech:
            room_type = czech.group(1).strip(" ,")
            return 1, room_type, [RoomBreakdown(room_type=room_type, count=1)]
    return None, None, None


def parse_nights(lines: Iterable[str]) -> int | None:
    match = re.search(r"\b(\d+)\s+(?:nights?|noc(?:i)?)\b", "\n".join(lines), re.I)
    return int(match.group(1)) if match else None


def parse_prices(lines: list[str]) -> tuple[PriceBreakdown, list[str], list[str]]:
    prices = PriceBreakdown()
    warnings: list[str] = []
    ambiguous: list[str] = []
    in_payment = False
    totals_by_section: dict[str, list[Decimal]] = {"price": [], "payment": []}
    for line in lines:
        lowered = line.casefold()
        if any(label in lowered for label in _LOCALE_LABELS["payment"]):
            in_payment = True
            continue
        if any(label in lowered for label in _LOCALE_LABELS["price"]):
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
        if re.search(r"\b(?:vat|dph)\b", line, re.I):
            prices.vat = amount
        elif re.search(r"city tax|městská daň", line, re.I):
            prices.city_tax = amount
        elif re.search(r"\b(?:tax|taxes|fees?)\b", line, re.I):
            prices.taxes_and_fees = amount
        elif re.search(
            r"total future payments?|amount payable|payable|plánované platby celkem", line, re.I
        ):
            prices.payable_price = amount
        elif re.search(r"total price|reservation total|celková cena|final price", line, re.I):
            section = "payment" if in_payment else "price"
            totals_by_section[section].append(amount)
            if in_payment:
                prices.payable_price = prices.payable_price or amount
            else:
                prices.total_price = prices.total_price or amount
        elif re.search(r"(?:room|suite|dormitory|apartment|studio|pokoj)", line, re.I):
            prices.base_price = prices.base_price or amount
    for section, totals in totals_by_section.items():
        if len(set(totals)) > 1:
            ambiguous.append("booked_total_price" if section == "price" else "booked_payable_price")
            warnings.append(f"conflicting total amounts in {section} section")
    if prices.taxes_and_fees is None and prices.vat is not None and prices.city_tax is not None:
        prices.taxes_and_fees = prices.vat + prices.city_tax
        if prices.total_price is not None and prices.base_price is not None:
            if abs(prices.base_price + prices.taxes_and_fees - prices.total_price) <= Decimal(
                "0.01"
            ):
                warnings.append("explicit total differs from itemized tax lines by rounding")
    if (
        prices.taxes_and_fees is None
        and prices.city_tax is not None
        and prices.base_price is not None
        and prices.total_price is not None
        and prices.base_price + prices.city_tax == prices.total_price
    ):
        prices.taxes_and_fees = prices.city_tax
        warnings.append("daně a poplatky byly odvozeny z explicitní městské daně")
    return prices, warnings, ambiguous


def parse_cancellation(lines: list[str]) -> tuple[str | None, bool | None, datetime | None]:
    relevant: list[str] = []
    in_section = False
    for line in lines:
        if re.search(_CANCELLATION_HEADINGS, line, re.I):
            in_section = True
        if in_section and re.search(
            r"^(price|payment) information|^informace o (ceně|platbě)", line, re.I
        ):
            break
        if in_section or re.search(r"non-refundable|refundable", line, re.I):
            relevant.append(line)
    if not relevant:
        return None, None, None
    text = " ".join(relevant)
    if re.search(r"non-refundable", text, re.I):
        free = False
    elif re.search(
        r"free (?:of charge )?cancellation|cancel .*free of charge|zdarma do|"
        r"bezplatné zrušení(?: rezervace)?\s+do", text, re.I
    ):
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
    if deadline is None:
        czech = re.search(
            r"(?:zdarma do|bezplatné zrušení(?: rezervace)?\s+do|"
            r"rezervaci můžete zrušit zdarma do)\s+(\d{1,2})\.\s*"
            r"([A-Za-záčďéěíňóřšťúůýž]+)\s+(\d{4})\s+(\d{1,2}):(\d{2})",
            text,
            re.I,
        )
        if czech:
            day, month, year, hour, minute = czech.groups()
            month_number = _CZECH_MONTHS.get(month.casefold())
            if month_number:
                deadline = datetime(int(year), month_number, int(day), int(hour), int(minute))
    return text, free, deadline


def parse_meal_facts(lines: Iterable[str]) -> tuple[str | None, bool | None]:
    joined = "\n".join(lines)
    if re.search(
        r"breakfast (?:is )?included|includes breakfast|konečná cena zahrnuje snídani|"
        r"snídaně zahrnutá v ceně",
        joined,
        re.I,
    ):
        return "Breakfast included", True
    if re.search(
        r"meals? (?:are |is )?not included in the room rate|"
        r"breakfast (?:is )?not included|no breakfast",
        joined,
        re.I,
    ):
        return "No meals included", False
    return None, None


def parse_booking_url(lines: Iterable[str]) -> str | None:
    for line in lines:
        if line.startswith("Booking URL:") and (canonical := canonical_booking_hotel_url(line)):
            return canonical
    return None


def parse_payment_conditions(lines: Iterable[str]) -> str | None:
    for line in lines:
        if re.search(
            r"booking(?:\.com)? automaticky strhne částku z (?:(?:vaší|vaši|vaš[eí]) )?karty",
            line,
            re.I,
        ):
            return "Automatická budoucí platba kartou přes Booking.com"
        if re.search(r"booking (?:will |automatically )?charges? (?:your )?card", line, re.I):
            return "Booking automatically charges card"
    return None
