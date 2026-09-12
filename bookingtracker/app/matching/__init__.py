"""Pure exact-reservation matching domain layer."""

from app.matching.cross_property import (
    CrossPropertyAlternativeVerifier,
    CrossPropertyQualification,
    QualifiedDiscoveryAlternative,
)
from app.matching.matcher import ExactReservationMatcher
from app.matching.models import MatchClassification, MatchResult

__all__ = [
    "CrossPropertyAlternativeVerifier",
    "CrossPropertyQualification",
    "ExactReservationMatcher",
    "MatchClassification",
    "MatchResult",
    "QualifiedDiscoveryAlternative",
]
