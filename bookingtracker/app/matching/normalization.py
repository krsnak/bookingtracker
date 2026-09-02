"""Conservative room identity helpers; marketing words are never quality evidence."""

from __future__ import annotations

import re
import unicodedata

_FILLER_TOKENS = {"with", "and", "the", "a", "an", "bed", "beds", "s", "se"}


def normalized_tokens(value: str) -> set[str]:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    tokens = set(re.findall(r"[a-z0-9]+", ascii_value.casefold()))
    normalized = {
        token[:-1] if token.endswith("s") and len(token) > 3 else token for token in tokens
    }
    return normalized - _FILLER_TOKENS


def same_room_identity(booked_name: str, candidate_name: str) -> bool:
    """Allow only punctuation/plural normalization for an exact room identity."""
    return normalized_tokens(booked_name) == normalized_tokens(candidate_name)
