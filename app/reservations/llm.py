"""Optional semantic extraction contract; no provider is wired in Phase 1."""

from __future__ import annotations

from typing import Protocol

from app.reservations.models import ReservationCandidate


class SemanticExtractionProvider(Protocol):
    def extract(self, source_text: str) -> ReservationCandidate:
        """Return a typed proposal that the normal validator must still review."""
