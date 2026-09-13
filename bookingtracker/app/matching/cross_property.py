"""Evidence-first, non-comparable cross-property discovery qualification."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.booking.models import PropertyQuality, RateOffer, SearchCard
from app.booking.room_facts import extract_room_facts
from app.matching.matcher import ExactReservationMatcher
from app.matching.normalization import normalized_tokens
from app.reservations.models import Reservation


class QualifiedDiscoveryAlternative(BaseModel):
    """A verified hotel option that deliberately cannot be priced against a reservation."""

    property_name: str
    detail_url: str
    rate: RateOffer
    quality: PropertyQuality
    comparable: Literal[False] = False
    room_category: Literal["equivalent", "better"] = "equivalent"
    objective_improvements: list[str] = Field(default_factory=list)
    qualification_evidence: list[str] = Field(default_factory=list)


class CrossPropertyQualification(BaseModel):
    qualified: list[QualifiedDiscoveryAlternative] = Field(default_factory=list)
    rejected: dict[str, int] = Field(default_factory=dict)


class CrossPropertyAlternativeVerifier:
    """Qualify detail rates without invoking exact matching or price comparison."""

    def verify(
        self,
        reservation: Reservation,
        card: SearchCard,
        detail_quality: PropertyQuality,
        detail_offers: list[RateOffer],
    ) -> CrossPropertyQualification:
        rejected: dict[str, int] = {}

        def reject(reason: str) -> CrossPropertyQualification:
            rejected[reason] = 1
            return CrossPropertyQualification(rejected=rejected)

        if normalized_tokens(card.property_name) == normalized_tokens(
            reservation.property_name or ""
        ):
            return reject("same_property_not_cross_property")
        if not self._quality_confirmed(detail_quality):
            return reject("hotel_quality_not_confirmed")

        qualified: list[QualifiedDiscoveryAlternative] = []
        for rate in detail_offers:
            reason, improvements = self._rate_qualification(reservation, card, rate)
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            qualified.append(
                QualifiedDiscoveryAlternative(
                    property_name=card.property_name,
                    detail_url=card.detail_url,
                    rate=rate,
                    quality=detail_quality,
                    room_category="better" if improvements else "equivalent",
                    objective_improvements=improvements,
                    qualification_evidence=[
                        "detail property identity confirmed",
                        "requested occupancy confirmed",
                        "room evidence confirms an equivalent or explicitly better room",
                        "meal and breakfast terms confirmed",
                        "cancellation protection confirmed",
                        "payment conditions confirmed",
                        "same currency and tax-inclusive total confirmed",
                        "Booking score and confident review count confirmed",
                    ],
                )
            )
        return CrossPropertyQualification(
            qualified=qualified, rejected=dict(sorted(rejected.items()))
        )

    @staticmethod
    def _quality_confirmed(quality: PropertyQuality) -> bool:
        return (
            quality.booking_score is not None
            and quality.review_count is not None
            and quality.review_count_confident
        )

    @staticmethod
    def _rate_qualification(
        reservation: Reservation, card: SearchCard, rate: RateOffer
    ) -> tuple[str | None, list[str]]:
        if normalized_tokens(rate.property_name or "") != normalized_tokens(card.property_name):
            return "detail_property_identity_not_confirmed", []
        _score, warning, occupancy_rejection = ExactReservationMatcher._occupancy(reservation, rate)
        if occupancy_rejection or warning:
            return "occupancy_not_confirmed", []
        if reservation.rooms_count != 1:
            return "room_count_not_supported", []
        if CrossPropertyAlternativeVerifier._meal_rejection(reservation, rate):
            return "meal_not_confirmed", []
        if CrossPropertyAlternativeVerifier._cancellation_rejection(reservation, rate):
            return "cancellation_not_confirmed", []
        if CrossPropertyAlternativeVerifier._payment_rejection(reservation, rate):
            return "payment_not_confirmed", []
        if reservation.currency != rate.currency:
            return "currency_not_confirmed", []
        if rate.taxes_included is not True or rate.current_price <= Decimal("0"):
            return "tax_inclusive_total_not_confirmed", []
        room_rejections, improvements, _evidence = ExactReservationMatcher._room_facts(
            extract_room_facts(reservation.room_type or ""),
            rate.room_facts,
            normalized_tokens(reservation.room_type or "") == normalized_tokens(rate.room_name),
        )
        if room_rejections:
            return "room_not_confirmed", []
        return None, improvements

    @staticmethod
    def _meal_rejection(reservation: Reservation, rate: RateOffer) -> bool:
        """Require evidence for every known booked meal protection."""
        if reservation.breakfast_included is True and rate.breakfast_included is not True:
            return True
        if reservation.breakfast_included is False and rate.breakfast_included is None:
            return True
        return bool(
            reservation.meal_plan
            and (
                rate.meal_plan is None
                or normalized_tokens(reservation.meal_plan) != normalized_tokens(rate.meal_plan)
            )
        )

    @staticmethod
    def _cancellation_rejection(reservation: Reservation, rate: RateOffer) -> bool:
        """Do not qualify an unknown, earlier, or less protected cancellation term."""
        if reservation.free_cancellation is True:
            if rate.free_cancellation is not True or rate.non_refundable is True:
                return True
        elif reservation.free_cancellation is False:
            if rate.free_cancellation is None or rate.non_refundable is None:
                return True
        if reservation.cancellation_deadline is not None:
            return (
                rate.cancellation_deadline is None
                or rate.cancellation_deadline < reservation.cancellation_deadline
            )
        return False

    @staticmethod
    def _payment_rejection(reservation: Reservation, rate: RateOffer) -> bool:
        """Payment wording is a relevant condition, not a price-comparison hint."""
        return bool(
            reservation.payment_conditions
            and (
                not rate.payment_conditions
                or normalized_tokens(reservation.payment_conditions)
                != normalized_tokens(rate.payment_conditions)
            )
        )
