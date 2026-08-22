"""Conservative room-name normalization and feature comparison."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_FILLER_TOKENS = {"with", "and", "the", "a", "an", "bed", "beds"}
_FEATURES = {
    "balcony": ({"balcony"},),
    "shared_bathroom": ({"shared", "bathroom"},),
    "private_bathroom": ({"private", "bathroom"}, {"ensuite"}),
    "sea_view": ({"sea", "view"},),
    "mountain_view": ({"mountain", "view"},),
    "family": ({"family"},),
    "suite": ({"suite"},),
    "economy": ({"economy"}, {"budget"}),
    "standard": ({"standard"},),
    "superior": ({"superior"},),
    "deluxe": ({"deluxe"},),
    "twin": ({"twin"},),
    "double": ({"double"},),
    "triple": ({"triple"},),
    "apartment": ({"apartment"},),
}
_TIER = {"economy": 0, "standard": 1, "superior": 2, "deluxe": 3, "suite": 4}
_SIGNIFICANT_FEATURES = {
    "balcony",
    "shared_bathroom",
    "private_bathroom",
    "sea_view",
    "mountain_view",
}


def normalized_tokens(value: str) -> set[str]:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    tokens = set(re.findall(r"[a-z0-9]+", ascii_value.casefold()))
    normalized = {
        token[:-1] if token.endswith("s") and len(token) > 3 else token for token in tokens
    }
    return normalized - _FILLER_TOKENS


def _basic_room_text(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value.casefold()))


def room_features(value: str) -> set[str]:
    tokens = normalized_tokens(value)
    return {
        feature
        for feature, alternatives in _FEATURES.items()
        if any(feature_tokens <= tokens for feature_tokens in alternatives)
    }


@dataclass(frozen=True)
class RoomComparison:
    score: float
    exact: bool
    upgrade: bool
    rejection: str | None
    reason: str


def compare_rooms(booked_name: str, candidate_name: str) -> RoomComparison:
    booked_tokens = normalized_tokens(booked_name)
    candidate_tokens = normalized_tokens(candidate_name)
    booked_features = room_features(booked_name)
    candidate_features = room_features(candidate_name)
    if booked_tokens == candidate_tokens:
        if _basic_room_text(booked_name) == _basic_room_text(candidate_name):
            return RoomComparison(1, True, False, None, "room normalized exactly")
        return RoomComparison(0.95, False, False, None, "room wording normalized equivalent")

    shared = booked_tokens & candidate_tokens
    essential = {"room", "double", "triple", "twin", "apartment", "suite", "family"}
    if (booked_tokens & essential) != (candidate_tokens & essential):
        return RoomComparison(0, False, False, "room capacity/type tokens differ", "room differs")
    missing_significant = (booked_features & _SIGNIFICANT_FEATURES) - candidate_features
    if missing_significant:
        feature = sorted(missing_significant)[0].replace("_", " ")
        return RoomComparison(
            0, False, False, f"booked {feature} is absent", "room feature downgraded"
        )
    booked_tier = max((_TIER[feature] for feature in booked_features & _TIER.keys()), default=None)
    candidate_tier = max(
        (_TIER[feature] for feature in candidate_features & _TIER.keys()), default=None
    )
    extra_significant = (candidate_features & _SIGNIFICANT_FEATURES) - booked_features
    if (
        booked_tier is not None and candidate_tier is not None and candidate_tier > booked_tier
    ) or extra_significant:
        return RoomComparison(0.8, False, True, None, "candidate room appears to be an upgrade")
    overlap = len(shared) / max(len(booked_tokens), len(candidate_tokens))
    if overlap >= 0.65:
        return RoomComparison(0.9, False, False, None, "room wording normalized equivalent")
    return RoomComparison(0, False, False, "room names are not safely equivalent", "room differs")
