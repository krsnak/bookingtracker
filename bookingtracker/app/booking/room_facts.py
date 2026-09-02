"""Narrow extraction of explicit, objective room facts from public offer text."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from app.booking.models import RoomFacts


def _plain(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()


def extract_room_facts(value: str) -> RoomFacts:
    """Extract facts stated by Booking, never quality tiers or inferred amenities."""
    text = _plain(value)
    dorm_bed = bool(re.search(r"\b(?:bed|luzko)\b.*\b(?:dorm|shared room|sdilen)", text))
    private_room = not dorm_bed and bool(re.search(r"\b(?:room|pokoj|apartment|apartman)\b", text))
    capacity = None
    for number, words in (
        (1, ("single", "jednoluzkov")),
        (2, ("double", "twin", "dvouluzkov")),
        (3, ("triple", "three bed", "triluzkov")),
        (4, ("quad", "four bed", "ctyrluzkov")),
    ):
        if any(word in text for word in words):
            capacity = number
            break
    area_match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:m2|m\u00b2|sqm)\b", text)
    bed_types = []
    for label, patterns in {
        "double": (r"\bdouble bed\b", r"manzelsk(?:a|ou) postel"),
        "single": (r"\bsingle bed\b", r"jednoluzkov"),
        "bunk": (r"\bbunk bed\b", r"palanda"),
    }.items():
        if any(re.search(pattern, text) for pattern in patterns):
            bed_types.append(label)
    view = next(
        (
            label
            for label, patterns in {
                "sea": (r"\bsea view\b", r"vyhled na more"),
                "mountain": (r"\bmountain view\b", r"vyhled na hory"),
                "city": (r"\bcity view\b", r"vyhled na mesto"),
                "garden": (r"\bgarden view\b", r"vyhled do zahrady"),
            }.items()
            if any(re.search(pattern, text) for pattern in patterns)
        ),
        None,
    )
    return RoomFacts(
        accommodation_kind="dorm_bed" if dorm_bed else "private_room" if private_room else None,
        room_capacity=capacity,
        private_bathroom=True
        if re.search(r"\b(?:private bathroom|ensuite|vlastni koupelna)\b", text)
        else False
        if re.search(r"\b(?:shared bathroom|sdilena koupelna)\b", text)
        else None,
        balcony=(
            False
            if re.search(r"\b(?:without balcony|bez balkonu)\b", text)
            else True
            if re.search(r"\b(?:balcony|balkon)\b", text)
            else None
        ),
        terrace=True if re.search(r"\b(?:terrace|terasa)\b", text) else None,
        area_sqm=Decimal(area_match.group(1).replace(",", ".")) if area_match else None,
        view=view,
        air_conditioning=True if re.search(r"\b(?:air conditioning|klimatizace)\b", text) else None,
        kitchen=True if re.search(r"\b(?:kitchenette|kitchen|kuchyn)\b", text) else None,
        accessible=True if re.search(r"\b(?:accessible|bezbarier)\b", text) else None,
        bed_types=bed_types,
    )
