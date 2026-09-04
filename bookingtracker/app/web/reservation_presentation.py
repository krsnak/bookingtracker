"""Presentation-only view models for the Czech reservation screens."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from app.matching.models import MatchClassification
from app.pricing.models import PersistedPriceCheck, PriceCheckStatus
from app.reservations.models import Reservation
from app.scheduling.models import ScheduleState
from app.web.presentation import format_date, format_money

_PRAGUE = ZoneInfo("Europe/Prague")
_MONTHS_UPPER = (
    "LEDEN",
    "ÚNOR",
    "BŘEZEN",
    "DUBEN",
    "KVĚTEN",
    "ČERVEN",
    "ČERVENEC",
    "SRPEN",
    "ZÁŘÍ",
    "ŘÍJEN",
    "LISTOPAD",
    "PROSINEC",
)
_ACCEPTED_CLASSIFICATIONS = frozenset(
    {MatchClassification.EXACT, MatchClassification.EQUIVALENT, MatchClassification.BETTER}
)


@dataclass(frozen=True)
class ReservationCardView:
    reservation: Reservation
    property_name: str
    property_initial: str
    stay_label: str
    cancellation_label: str
    cancellation_tone: str
    room_label: str | None
    meal_label: str | None
    occupancy_label: str | None
    booked_price_label: str
    current_price_label: str | None
    previous_price_label: str | None
    price_difference_label: str | None
    price_tone: str
    match_category_label: str | None
    check_status_label: str | None
    check_status_tone: str
    last_check_label: str | None
    check_trigger_label: str | None
    next_check_label: str | None
    image_url: str | None = None
    image_alt: str | None = None
    has_image: bool = False


@dataclass(frozen=True)
class PriceHistoryPointView:
    x: int
    y: int
    price_label: str
    date_label: str
    category_label: str | None


@dataclass(frozen=True)
class PriceHistoryView:
    points: tuple[PriceHistoryPointView, ...]
    path: str | None
    booked_y: int | None
    booked_price_label: str
    empty_label: str | None


@dataclass(frozen=True)
class CheckHistoryRowView:
    when_label: str
    trigger_label: str
    result_label: str
    room_label: str | None
    category_label: str | None
    price_label: str | None
    difference_label: str | None
    reason_label: str | None


def _plural_nights(nights: int) -> str:
    if nights == 1:
        return "noc"
    if 2 <= nights <= 4:
        return "noci"
    return "nocí"


def _stay_label(reservation: Reservation) -> str:
    if not reservation.check_in or not reservation.check_out:
        return "Termín neuveden"
    nights = (reservation.check_out - reservation.check_in).days
    dates = format_date_range_short(reservation.check_in, reservation.check_out)
    return f"{dates} · {nights} {_plural_nights(nights)}"


def format_date_range_short(start: date, end: date) -> str:
    months = (
        "ledna",
        "února",
        "března",
        "dubna",
        "května",
        "června",
        "července",
        "srpna",
        "září",
        "října",
        "listopadu",
        "prosince",
    )
    if start.year == end.year and start.month == end.month:
        return f"{start.day}.–{end.day}. {months[start.month - 1]} {end.year}"
    return f"{format_date(start)} – {format_date(end)}"


def _local(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC).astimezone(_PRAGUE)
        if value.tzinfo is None
        else value.astimezone(_PRAGUE)
    )


def _cancellation(reservation: Reservation, now: datetime) -> tuple[str, str]:
    if reservation.free_cancellation is False:
        return "Nevratná rezervace", "neutral"
    if reservation.free_cancellation is not True:
        return "Storno neuvedeno", "neutral"
    if reservation.cancellation_deadline is None:
        return "Bezplatné zrušení", "success"
    deadline = _local(reservation.cancellation_deadline)
    current = _local(now)
    if deadline <= current:
        return "Bezplatné storno skončilo", "danger"
    remaining_days = (deadline.date() - current.date()).days
    if remaining_days <= 2:
        return f"Zdarma ještě {remaining_days} {'den' if remaining_days == 1 else 'dny'}", "warning"
    label = f"Zdarma do {deadline.day}. {format_date(deadline)[3:]} {deadline:%H:%M}"
    return label, "success"


def _meal_label(reservation: Reservation) -> str | None:
    if reservation.breakfast_included is True:
        return "Snídaně v ceně"
    if reservation.meal_plan:
        return (
            f"{reservation.meal_plan} v ceně"
            if "v cen" not in reservation.meal_plan.casefold()
            else reservation.meal_plan
        )
    if reservation.breakfast_included is False:
        return "Bez stravy"
    return "Strava neuvedena"


def _occupancy_label(reservation: Reservation) -> str | None:
    if reservation.adults is None:
        return None
    guests = f"{reservation.adults} {'dospělý' if reservation.adults == 1 else 'dospělí'}"
    if reservation.children:
        guests += f", {reservation.children} {'dítě' if reservation.children == 1 else 'děti'}"
    return guests


def _trigger_label(check: PersistedPriceCheck | None) -> str | None:
    if check is None:
        return None
    if check.run_id.startswith("manual:") or check.run_id.startswith("manual_all:"):
        return "Ručně zkontrolováno"
    if check.run_id.startswith("scheduler:"):
        return "Automaticky zkontrolováno"
    return "Zkontrolováno"


def _status(check: PersistedPriceCheck | None) -> tuple[str | None, str]:
    if check is None:
        return "Zatím nekontrolováno", "neutral"
    if check.status is PriceCheckStatus.NO_MATCH:
        return "Nelze bezpečně porovnat", "neutral"
    if check.status is PriceCheckStatus.NO_AVAILABILITY:
        return "Pro termín není dostupné", "neutral"
    if check.status is PriceCheckStatus.AVAILABILITY_UNKNOWN:
        return "Dostupnost neověřena", "warning"
    if check.status is PriceCheckStatus.INCOMPLETE_RESERVATION:
        return "Doplňte údaje rezervace", "warning"
    if check.status is not PriceCheckStatus.SUCCESS:
        return "Kontrola se nezdařila: Kontrolu se nepodařilo dokončit", "danger"
    if not check.comparison or not check.comparison.comparable:
        return "Nelze bezpečně porovnat", "neutral"
    return None, "neutral"


def is_accepted_comparable(check: PersistedPriceCheck, reservation: Reservation) -> bool:
    """Defence-in-depth UI gate for a current or historical displayed price."""
    comparison = check.comparison
    match = check.match_result
    return bool(
        comparison
        and check.status is PriceCheckStatus.SUCCESS
        and comparison.comparable
        and comparison.current_price is not None
        and comparison.currency == reservation.currency
        and check.match_classification in _ACCEPTED_CLASSIFICATIONS
        and match
        and match.accepted
        and match.classification == check.match_classification
    )


def _last_comparable(
    checks: list[PersistedPriceCheck], reservation: Reservation
) -> PersistedPriceCheck | None:
    return next((check for check in checks if is_accepted_comparable(check, reservation)), None)


def _last_check_label(check: PersistedPriceCheck | None, now: datetime) -> str | None:
    if check is None:
        return None
    checked = check.finished_at or check.checked_at
    local = _local(checked)
    today = _local(now).date()
    if local.date() == today:
        return f"dnes v {local:%H:%M}"
    return f"{format_date(local)} v {local:%H:%M}"


def _price_fields(
    checks: list[PersistedPriceCheck], reservation: Reservation
) -> tuple[str | None, str | None, str | None, str]:
    latest = checks[0] if checks else None
    comparison = latest.comparison if latest else None
    if latest and is_accepted_comparable(latest, reservation) and comparison:
        current = comparison.current_price
        booked = reservation.booked_total_price
        if booked is not None and comparison.currency == reservation.currency:
            delta = current - booked
            percentage = (delta / booked * Decimal("100")) if booked else None
            amount = format_money(abs(delta), reservation.currency)
            pct = (
                f"{abs(percentage).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):f}".replace(
                    ".", ","
                )
                if percentage is not None
                else None
            )
            sign = "−" if delta < 0 else "+" if delta > 0 else ""
            difference = f"{sign}{amount}" + (f" · {sign}{pct} %" if pct is not None else "")
            tone = "success" if delta < 0 else "danger" if delta > 0 else "neutral"
            return format_money(current, reservation.currency), None, difference, tone
    prior = _last_comparable(checks[1:] if latest else checks, reservation)
    if prior and prior.comparison and prior.comparison.current_price is not None:
        known_at = _local(prior.finished_at or prior.checked_at)
        known_price = format_money(prior.comparison.current_price, prior.comparison.currency)
        return None, f"Poslední známá cena {known_price} ({format_date(known_at)})", None, "neutral"
    return None, None, None, "neutral"


def _match_category(check: PersistedPriceCheck | None, reservation: Reservation) -> str | None:
    if not check or not is_accepted_comparable(check, reservation):
        return None
    return {
        MatchClassification.EXACT: "Stejný pokoj",
        MatchClassification.EQUIVALENT: "Ekvivalentní pokoj",
        MatchClassification.BETTER: "Prokazatelně lepší pokoj",
    }.get(check.match_classification)


def reservation_card_view(
    reservation: Reservation,
    checks: list[PersistedPriceCheck],
    schedule: ScheduleState | None,
    *,
    now: datetime | None = None,
) -> ReservationCardView:
    now = now or datetime.now(UTC)
    latest = checks[0] if checks else None
    current, previous, difference, price_tone = _price_fields(checks, reservation)
    status, status_tone = _status(latest)
    next_at = (
        latest.next_check_at
        if latest and latest.next_check_at
        else schedule.next_check_at
        if schedule
        else None
    )
    name = reservation.property_name or "Neuvedené ubytování"
    if next_at:
        local_next = _local(next_at)
        next_label = (
            f"Další kontrola v {local_next:%H:%M}"
            if local_next.date() == _local(now).date()
            else f"Další kontrola {format_date(local_next)} v {local_next:%H:%M}"
        )
    else:
        next_label = None
    cancellation_label, cancellation_tone = _cancellation(reservation, now)
    return ReservationCardView(
        reservation=reservation,
        property_name=name,
        property_initial=name.strip()[:1].upper() or "U",
        stay_label=_stay_label(reservation),
        cancellation_label=cancellation_label,
        cancellation_tone=cancellation_tone,
        room_label=reservation.room_type or "Typ pokoje neuveden",
        meal_label=_meal_label(reservation),
        occupancy_label=_occupancy_label(reservation),
        booked_price_label=format_money(reservation.booked_total_price, reservation.currency),
        current_price_label=current,
        previous_price_label=previous,
        price_difference_label=difference,
        price_tone=price_tone,
        match_category_label=_match_category(latest, reservation),
        check_status_label=status,
        check_status_tone=status_tone,
        last_check_label=_last_check_label(latest, now),
        check_trigger_label=_trigger_label(latest),
        next_check_label=next_label,
        image_url=None,
        image_alt=f"Ilustrační obrázek pro {name}",
        has_image=False,
    )


def group_reservation_cards(
    cards: list[ReservationCardView],
) -> list[tuple[str, list[ReservationCardView]]]:
    grouped: dict[tuple[int, int], list[ReservationCardView]] = {}
    unknown: list[ReservationCardView] = []
    for card in cards:
        if card.reservation.check_in:
            grouped.setdefault(
                (card.reservation.check_in.year, card.reservation.check_in.month), []
            ).append(card)
        else:
            unknown.append(card)
    result = []
    for (year, month), items in sorted(grouped.items()):
        count_label = "rezervace" if len(items) == 1 else "rezervací"
        label = f"{_MONTHS_UPPER[month - 1]} {year} · {len(items)} {count_label}"
        sorted_items = sorted(items, key=lambda card: card.reservation.check_in or date.max)
        result.append((label, sorted_items))
    if unknown:
        result.append((f"TERMÍN NEUVEDEN · {len(unknown)} rezervací", unknown))
    return result


def price_history_view(
    reservation: Reservation, checks: list[PersistedPriceCheck]
) -> PriceHistoryView:
    """Build an SVG-ready graph from accepted, same-currency prices only."""
    accepted = [
        check
        for check in reversed(checks)
        if is_accepted_comparable(check, reservation)
    ]
    if not accepted or reservation.booked_total_price is None:
        return PriceHistoryView(
            points=(), path=None, booked_y=None,
            booked_price_label=format_money(reservation.booked_total_price, reservation.currency),
            empty_label="Zatím není k dispozici žádná bezpečně porovnatelná cena.",
        )
    prices = [check.comparison.current_price for check in accepted if check.comparison]
    minimum, maximum = min(prices + [reservation.booked_total_price]), max(
        prices + [reservation.booked_total_price]
    )
    spread = maximum - minimum or Decimal("1")

    def y_for(price: Decimal) -> int:
        return int(20 + (maximum - price) / spread * 150)

    count = len(accepted)
    points = tuple(
        PriceHistoryPointView(
            x=40 if count == 1 else int(40 + index * 340 / (count - 1)),
            y=y_for(check.comparison.current_price),  # type: ignore[union-attr]
            price_label=format_money(check.comparison.current_price, reservation.currency),  # type: ignore[union-attr]
            date_label=format_date(_local(check.finished_at or check.checked_at)),
            category_label=_match_category(check, reservation),
        )
        for index, check in enumerate(accepted)
    )
    return PriceHistoryView(
        points=points,
        path=" ".join(
            f"{'M' if index == 0 else 'L'} {point.x} {point.y}"
            for index, point in enumerate(points)
        ),
        booked_y=y_for(reservation.booked_total_price),
        booked_price_label=format_money(reservation.booked_total_price, reservation.currency),
        empty_label=None,
    )


def check_history_rows(
    reservation: Reservation, checks: list[PersistedPriceCheck]
) -> list[CheckHistoryRowView]:
    rows = []
    for check in checks:
        current, _previous, difference, _tone = _price_fields([check], reservation)
        status, _status_tone = _status(check)
        room_label = (
            check.match_result.matched_rate.room_name
            if check.match_result and check.match_result.matched_rate
            else None
        )
        rows.append(CheckHistoryRowView(
            when_label=_last_check_label(check, datetime.now(UTC)) or "Neuvedeno",
            trigger_label=_trigger_label(check) or "Kontrola",
            result_label="Porovnatelná cena" if current else status or "Kontrola dokončena",
            room_label=room_label,
            category_label=_match_category(check, reservation), price_label=current,
            difference_label=difference, reason_label=status if not current else None,
        ))
    return rows
