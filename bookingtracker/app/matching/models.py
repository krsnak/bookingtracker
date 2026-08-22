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
    rate: RateOffer
    accepted: bool
    score: Decimal = Field(ge=0, le=1)
    classification: MatchClassification
    reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    component_scores: dict[str, Decimal] = Field(default_factory=dict)


class MatchResult(BaseModel):
    accepted: bool
    score: Decimal = Field(ge=0, le=1)
    matched_rate: RateOffer | None = None
    classification: MatchClassification
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rejected_candidates: list[CandidateEvaluation] = Field(default_factory=list)
    candidate_evaluations: list[CandidateEvaluation] = Field(default_factory=list)
