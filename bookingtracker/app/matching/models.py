"""Typed, explainable results of exact-reservation matching."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from app.booking.models import RateOffer


class MatchClassification(StrEnum):
    EXACT = "exact"
    EQUIVALENT = "equivalent"
    BETTER = "better"
    UPGRADE_CANDIDATE = "upgrade_candidate"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"


class CandidateEvaluation(BaseModel):
    diagnostic_index: int = Field(default=0, ge=0)
    rate: RateOffer
    accepted: bool
    score: Decimal = Field(ge=0, le=1)
    classification: MatchClassification
    reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    component_scores: dict[str, Decimal] = Field(default_factory=dict)
    objective_differences: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class MatchResult(BaseModel):
    accepted: bool
    score: Decimal = Field(ge=0, le=1)
    matched_rate: RateOffer | None = None
    classification: MatchClassification
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rejected_candidates: list[CandidateEvaluation] = Field(default_factory=list)
    candidate_evaluations: list[CandidateEvaluation] = Field(default_factory=list)

    @property
    def matched_evaluation(self) -> CandidateEvaluation | None:
        if self.matched_rate is None:
            return None
        return next(
            (
                candidate
                for candidate in self.candidate_evaluations
                if candidate.accepted and candidate.rate == self.matched_rate
            ),
            None,
        )
