"""Deterministic exact-reservation matcher; price is deliberately not consulted."""

from __future__ import annotations

import re
from decimal import Decimal

from app.booking.models import RateOffer
from app.matching.models import CandidateEvaluation, MatchClassification, MatchResult
from app.matching.normalization import compare_rooms, normalized_tokens
from app.reservations.models import Reservation


class ExactReservationMatcher:
    """Evaluate every offer independently and select only accepted non-upgrades."""

    def match(self, reservation: Reservation, offers: list[RateOffer]) -> MatchResult:
        evaluations = [self.evaluate(reservation, offer) for offer in offers]
        accepted = [evaluation for evaluation in evaluations if evaluation.accepted]
        rejected = [evaluation for evaluation in evaluations if not evaluation.accepted]
        if not accepted:
            return MatchResult(
                accepted=False,
                score=Decimal("0"),
                classification=MatchClassification.NO_MATCH,
                reasons=["no offer satisfied all known reservation constraints"],
                rejected_candidates=rejected,
                candidate_evaluations=evaluations,
            )
        best = sorted(accepted, key=self._sort_key, reverse=True)[0]
        return MatchResult(
            accepted=True,
            score=best.score,
            matched_rate=best.rate,
            classification=best.classification,
            reasons=best.reasons,
            warnings=best.warnings,
            rejected_candidates=rejected,
            candidate_evaluations=evaluations,
        )

    def evaluate(self, reservation: Reservation, rate: RateOffer) -> CandidateEvaluation:
        reasons: list[str] = []
        rejection_reasons: list[str] = []
        warnings: list[str] = []
        components: dict[str, Decimal] = {}
        better = False
        ambiguous = False
        upgrade = False

        if reservation.property_name and rate.property_name:
            if normalized_tokens(reservation.property_name) != normalized_tokens(
                rate.property_name
            ):
                rejection_reasons.append("property differs from reservation")
        room = compare_rooms(reservation.room_type or "", rate.room_name)
        components["room"] = Decimal(str(room.score))
        if room.rejection:
            rejection_reasons.append(room.rejection)
        elif room.upgrade:
            upgrade = True
            reasons.append(room.reason)
        else:
            reasons.append(room.reason)

        occupancy_score, occupancy_warning, occupancy_rejection = self._occupancy(reservation, rate)
        components["occupancy"] = occupancy_score
        if occupancy_warning:
            warnings.append(occupancy_warning)
            ambiguous = True
        if occupancy_rejection:
            rejection_reasons.append(occupancy_rejection)
        else:
            reasons.append(
                "occupancy satisfies reservation" if occupancy_score == 1 else "occupancy unknown"
            )

        meal_score, meal_warning, meal_rejection = self._meal_and_breakfast(reservation, rate)
        components["meal"] = meal_score
        if meal_warning:
            warnings.append(meal_warning)
            ambiguous = True
        if meal_rejection:
            rejection_reasons.append(meal_rejection)
        elif meal_score == 1:
            reasons.append("meal and breakfast conditions preserved")

        cancellation_score, cancellation_warning, cancellation_rejection, cancellation_better = (
            self._cancellation(reservation, rate)
        )
        components["cancellation"] = cancellation_score
        if cancellation_warning:
            warnings.append(cancellation_warning)
            ambiguous = True
        if cancellation_rejection:
            rejection_reasons.append(cancellation_rejection)
        elif cancellation_score == 1:
            reasons.append("cancellation conditions preserved")
        better = better or cancellation_better

        payment_score, payment_warning, payment_better = self._payment(reservation, rate)
        components["payment"] = payment_score
        if payment_warning:
            warnings.append(payment_warning)
        better = better or payment_better

        score = self._score(components)
        if rejection_reasons:
            return CandidateEvaluation(
                rate=rate,
                accepted=False,
                score=score,
                classification=MatchClassification.REJECTED,
                reasons=reasons,
                rejection_reasons=rejection_reasons,
                warnings=warnings,
                component_scores=components,
            )
        if upgrade:
            return CandidateEvaluation(
                rate=rate,
                accepted=False,
                score=score,
                classification=MatchClassification.UPGRADE_CANDIDATE,
                reasons=reasons,
                warnings=warnings,
                component_scores=components,
            )
        if ambiguous:
            return CandidateEvaluation(
                rate=rate,
                accepted=False,
                score=score,
                classification=MatchClassification.AMBIGUOUS,
                reasons=reasons,
                warnings=warnings,
                component_scores=components,
            )
        classification = (
            MatchClassification.BETTER if better else self._base_classification(room.exact)
        )
        return CandidateEvaluation(
            rate=rate,
            accepted=True,
            score=score,
            classification=classification,
            reasons=reasons,
            warnings=warnings,
            component_scores=components,
        )

    @staticmethod
    def _occupancy(
        reservation: Reservation, rate: RateOffer
    ) -> tuple[Decimal, str | None, str | None]:
        adults = rate.adults
        children = rate.children
        if adults is None and rate.occupancy_text:
            adults_match = re.search(r"(\d+)\s+(?:adults?|dospěl[íe])", rate.occupancy_text, re.I)
            children_match = re.search(r"(\d+)\s+(?:children?|dět[íi])", rate.occupancy_text, re.I)
            adults = int(adults_match.group(1)) if adults_match else None
            children = int(children_match.group(1)) if children_match else None
        if adults is not None and adults < (reservation.adults or 0):
            return Decimal("0"), None, "candidate occupancy has insufficient adults"
        if children is not None and children < (reservation.children or 0):
            return Decimal("0"), None, "candidate occupancy has insufficient children"
        if adults is None or (reservation.children and children is None):
            return Decimal("0.7"), "candidate occupancy is unknown", None
        if reservation.rooms_count and reservation.rooms_count > 1:
            return Decimal("0.7"), "candidate room-count evidence is unavailable", None
        return Decimal("1"), None, None

    @staticmethod
    def _meal_and_breakfast(
        reservation: Reservation, rate: RateOffer
    ) -> tuple[Decimal, str | None, str | None]:
        if reservation.breakfast_included is True:
            if rate.breakfast_included is False:
                return Decimal("0"), None, "booked breakfast is missing from candidate"
            if rate.breakfast_included is None:
                return Decimal("0.6"), "candidate breakfast is unknown", None
        if reservation.meal_plan:
            if rate.meal_plan is None:
                return Decimal("0.7"), "candidate meal plan is unknown", None
            if normalized_tokens(reservation.meal_plan) != normalized_tokens(rate.meal_plan):
                return Decimal("0"), None, "candidate meal plan differs from booked meal plan"
        return Decimal("1"), None, None

    @staticmethod
    def _cancellation(
        reservation: Reservation, rate: RateOffer
    ) -> tuple[Decimal, str | None, str | None, bool]:
        if reservation.free_cancellation is True:
            if rate.non_refundable is True or rate.free_cancellation is False:
                return (
                    Decimal("0"),
                    None,
                    "booked free cancellation replaced by worse candidate",
                    False,
                )
            if rate.free_cancellation is None:
                return Decimal("0.6"), "candidate cancellation policy is unknown", None, False
        if reservation.cancellation_deadline and rate.cancellation_deadline:
            if rate.cancellation_deadline < reservation.cancellation_deadline:
                return Decimal("0"), None, "candidate cancellation deadline is earlier", False
            if rate.cancellation_deadline > reservation.cancellation_deadline:
                return Decimal("1"), None, None, True
        return Decimal("1"), None, None, False

    @staticmethod
    def _payment(reservation: Reservation, rate: RateOffer) -> tuple[Decimal, str | None, bool]:
        if not reservation.payment_conditions or not rate.payment_conditions:
            return Decimal("1"), None, False
        booked = reservation.payment_conditions.casefold()
        candidate = rate.payment_conditions.casefold()
        booked_pay_property = "pay at property" in booked or "zaplatíte v ubytování" in booked
        candidate_prepay = "prepayment required" in candidate or "pay in advance" in candidate
        if booked_pay_property and candidate_prepay:
            return Decimal("0.75"), "candidate payment terms may be materially worse", False
        return Decimal("1"), None, False

    @staticmethod
    def _score(components: dict[str, Decimal]) -> Decimal:
        weights = {
            "room": Decimal("0.45"),
            "occupancy": Decimal("0.20"),
            "meal": Decimal("0.15"),
            "cancellation": Decimal("0.15"),
            "payment": Decimal("0.05"),
        }
        return sum(components[name] * weights[name] for name in weights).quantize(Decimal("0.01"))

    @staticmethod
    def _base_classification(room_exact: bool) -> MatchClassification:
        return MatchClassification.EXACT if room_exact else MatchClassification.EQUIVALENT

    @staticmethod
    def _sort_key(candidate: CandidateEvaluation) -> tuple[int, Decimal]:
        rank = {
            MatchClassification.EXACT: 3,
            MatchClassification.EQUIVALENT: 2,
            MatchClassification.BETTER: 1,
        }
        return rank.get(candidate.classification, 0), candidate.score
