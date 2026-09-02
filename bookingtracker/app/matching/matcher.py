"""Evidence-first matching of the booked room, equivalent room, or proven improvement."""

from __future__ import annotations

import re
from decimal import Decimal

from app.booking.models import RateOffer, RoomFacts
from app.booking.room_facts import extract_room_facts
from app.matching.models import CandidateEvaluation, MatchClassification, MatchResult
from app.matching.normalization import normalized_tokens, same_room_identity
from app.reservations.models import Reservation


class ExactReservationMatcher:
    """Pure matcher that refuses absent evidence rather than guessing room quality."""

    def match(self, reservation: Reservation, offers: list[RateOffer]) -> MatchResult:
        evaluations = [
            self.evaluate(reservation, offer).model_copy(update={"diagnostic_index": index})
            for index, offer in enumerate(offers, start=1)
        ]
        accepted = [item for item in evaluations if item.accepted]
        rejected = [item for item in evaluations if not item.accepted]
        if not accepted:
            classification = (
                MatchClassification.AMBIGUOUS
                if any(item.classification is MatchClassification.AMBIGUOUS for item in evaluations)
                else MatchClassification.NO_MATCH
            )
            return MatchResult(
                accepted=False,
                score=Decimal("0"),
                classification=classification,
                reasons=["no offer satisfied all known reservation constraints"],
                rejected_candidates=rejected,
                candidate_evaluations=evaluations,
            )
        chosen, ambiguous = self._choose_accepted(accepted)
        if ambiguous:
            return MatchResult(
                accepted=False,
                score=Decimal("0"),
                classification=MatchClassification.AMBIGUOUS,
                reasons=["multiple otherwise-safe offers have non-orderable terms"],
                rejected_candidates=rejected,
                candidate_evaluations=evaluations,
            )
        assert chosen is not None
        return MatchResult(
            accepted=True,
            score=chosen.score,
            matched_rate=chosen.rate,
            classification=chosen.classification,
            reasons=chosen.reasons,
            warnings=chosen.warnings,
            rejected_candidates=rejected,
            candidate_evaluations=evaluations,
        )

    def evaluate(self, reservation: Reservation, rate: RateOffer) -> CandidateEvaluation:
        reasons: list[str] = []
        rejected: list[str] = []
        warnings: list[str] = []
        differences: list[str] = []
        evidence: list[str] = []
        components: dict[str, Decimal] = {}

        if reservation.property_name:
            if not rate.property_name:
                rejected.append("candidate property identity is missing")
            elif normalized_tokens(reservation.property_name) != normalized_tokens(
                rate.property_name
            ):
                rejected.append("property differs from reservation")
            else:
                evidence.append("property identity confirmed")

        occupancy, occupancy_warning, occupancy_rejection = self._occupancy(reservation, rate)
        components["occupancy"] = occupancy
        if occupancy_rejection:
            rejected.append(occupancy_rejection)
        elif occupancy_warning:
            warnings.append(occupancy_warning)
        else:
            reasons.append("occupancy confirms the requested guests")
            evidence.append("requested occupancy confirmed")

        for component, outcome in (
            ("meal", self._meal_and_breakfast(reservation, rate)),
            ("cancellation", self._cancellation(reservation, rate)),
            ("payment", self._payment(reservation, rate)),
        ):
            score, warning, rejection, improvement = outcome
            components[component] = score
            if rejection:
                rejected.append(rejection)
            elif warning:
                warnings.append(warning)
            elif score == 1:
                evidence.append(f"{component} terms confirmed")
            if improvement:
                differences.append(improvement)

        if reservation.currency != rate.currency:
            rejected.append("candidate currency differs from booked currency")
        if rate.taxes_included is not True:
            rejected.append("candidate final total does not explicitly include taxes and fees")

        booked_facts = extract_room_facts(reservation.room_type or "")
        room_exact = same_room_identity(reservation.room_type or "", rate.room_name)
        room_rejections, room_differences, room_evidence = self._room_facts(
            booked_facts, rate.room_facts, room_exact
        )
        rejected.extend(room_rejections)
        differences.extend(room_differences)
        evidence.extend(room_evidence)
        components["room"] = Decimal("1") if room_exact else Decimal("0.9")

        score = self._score(components)
        if rejected:
            return self._evaluation(
                rate,
                False,
                score,
                MatchClassification.REJECTED,
                reasons,
                rejected,
                warnings,
                components,
                differences,
                evidence,
            )
        if warnings:
            return self._evaluation(
                rate,
                False,
                score,
                MatchClassification.AMBIGUOUS,
                reasons,
                [],
                warnings,
                components,
                differences,
                evidence,
            )
        if room_exact:
            classification = MatchClassification.EXACT
        elif differences:
            classification = MatchClassification.BETTER
        else:
            classification = MatchClassification.EQUIVALENT
        return self._evaluation(
            rate,
            True,
            score,
            classification,
            reasons,
            [],
            warnings,
            components,
            differences,
            evidence,
        )

    @staticmethod
    def _evaluation(
        rate: RateOffer,
        accepted: bool,
        score: Decimal,
        classification: MatchClassification,
        reasons: list[str],
        rejected: list[str],
        warnings: list[str],
        components: dict[str, Decimal],
        differences: list[str],
        evidence: list[str],
    ) -> CandidateEvaluation:
        return CandidateEvaluation(
            rate=rate,
            accepted=accepted,
            score=score,
            classification=classification,
            reasons=reasons,
            rejection_reasons=rejected,
            warnings=warnings,
            component_scores=components,
            objective_differences=differences,
            evidence=evidence,
        )

    @staticmethod
    def _occupancy(
        reservation: Reservation, rate: RateOffer
    ) -> tuple[Decimal, str | None, str | None]:
        adults, children = rate.adults, rate.children
        if adults is None and rate.occupancy_text:
            adults_match = re.search(r"(\d+)\s+(?:adults?|dospěl[íe])", rate.occupancy_text, re.I)
            children_match = re.search(r"(\d+)\s+(?:children?|dět[íi])", rate.occupancy_text, re.I)
            adults = int(adults_match.group(1)) if adults_match else None
            children = int(children_match.group(1)) if children_match else None
        if adults != reservation.adults:
            return Decimal("0"), None, "candidate occupancy does not confirm booked adults"
        if reservation.children:
            if children != reservation.children:
                return Decimal("0"), None, "candidate occupancy does not confirm booked children"
        elif children not in {None, 0}:
            return Decimal("0"), None, "candidate occupancy includes different children"
        if reservation.rooms_count != 1:
            return Decimal("0"), None, "candidate room-count evidence is unavailable"
        return Decimal("1"), None, None

    @staticmethod
    def _meal_and_breakfast(
        reservation: Reservation, rate: RateOffer
    ) -> tuple[Decimal, str | None, str | None, str | None]:
        if reservation.breakfast_included is True:
            if rate.breakfast_included is not True:
                return Decimal("0"), None, "booked breakfast is not confirmed for candidate", None
        elif reservation.breakfast_included is False and rate.breakfast_included is True:
            return Decimal("1"), None, None, "breakfast included"
        if reservation.meal_plan:
            if rate.meal_plan is None:
                return Decimal("0"), None, "candidate meal plan is missing", None
            if normalized_tokens(reservation.meal_plan) != normalized_tokens(rate.meal_plan):
                return Decimal("0"), None, "candidate meal plan differs from booked meal plan", None
        return Decimal("1"), None, None, None

    @staticmethod
    def _cancellation(
        reservation: Reservation, rate: RateOffer
    ) -> tuple[Decimal, str | None, str | None, str | None]:
        if reservation.free_cancellation is True:
            if rate.free_cancellation is not True or rate.non_refundable is True:
                return Decimal("0"), None, "booked free cancellation is not confirmed", None
        if reservation.cancellation_deadline:
            if rate.cancellation_deadline is None:
                return Decimal("0"), None, "candidate cancellation deadline is missing", None
            if rate.cancellation_deadline < reservation.cancellation_deadline:
                return Decimal("0"), None, "candidate cancellation deadline is earlier", None
            if rate.cancellation_deadline > reservation.cancellation_deadline:
                return Decimal("1"), None, None, "later free cancellation deadline"
        return Decimal("1"), None, None, None

    @staticmethod
    def _payment(
        reservation: Reservation, rate: RateOffer
    ) -> tuple[Decimal, str | None, str | None, str | None]:
        if not reservation.payment_conditions:
            return Decimal("1"), None, None, None
        if not rate.payment_conditions:
            return Decimal("0"), None, "candidate payment conditions are missing", None
        booked = reservation.payment_conditions.casefold()
        candidate = rate.payment_conditions.casefold()
        booked_pay_property = "pay at property" in booked or "zaplatíte v ubytování" in booked
        candidate_prepay = "prepayment required" in candidate or "pay in advance" in candidate
        if booked_pay_property and candidate_prepay:
            return Decimal("0"), None, "candidate payment terms are materially worse", None
        return Decimal("1"), None, None, None

    @staticmethod
    def _room_facts(
        booked: RoomFacts, candidate: RoomFacts, exact: bool
    ) -> tuple[list[str], list[str], list[str]]:
        rejected: list[str] = []
        improvements: list[str] = []
        evidence: list[str] = []
        if not exact:
            if booked.accommodation_kind is None or candidate.accommodation_kind is None:
                rejected.append("room type evidence is insufficient for a differently named room")
            elif (
                booked.accommodation_kind == "private_room"
                and candidate.accommodation_kind != "private_room"
            ):
                rejected.append("private booked room cannot be replaced by a dorm bed")
            elif (
                booked.accommodation_kind == "dorm_bed"
                and candidate.accommodation_kind == "private_room"
            ):
                improvements.append("private room instead of a dorm bed")
            if booked.room_capacity is None or candidate.room_capacity != booked.room_capacity:
                rejected.append("candidate room capacity does not exactly confirm booked room type")
        for field, label in (
            ("balcony", "balcony"),
            ("terrace", "terrace"),
            ("private_bathroom", "private bathroom"),
            ("air_conditioning", "air conditioning"),
            ("kitchen", "kitchen"),
            ("accessible", "accessibility"),
        ):
            booked_value = getattr(booked, field)
            candidate_value = getattr(candidate, field)
            if booked_value is True and candidate_value is not True:
                rejected.append(f"booked {label} is not confirmed")
            elif not exact and booked_value is False and candidate_value is None:
                rejected.append(f"booked {label} is not confirmed")
            elif booked_value is False and candidate_value is True:
                improvements.append(label)
            elif booked_value is True and candidate_value is True:
                evidence.append(f"booked {label} preserved")
            elif booked_value is False and candidate_value is False:
                evidence.append(f"booked {label} preserved")
        if booked.view:
            if candidate.view != booked.view:
                rejected.append("booked view is not confirmed")
            else:
                evidence.append("booked view preserved")
        if booked.bed_types and not set(booked.bed_types) <= set(candidate.bed_types):
            rejected.append("booked bed type is not confirmed")
        elif booked.bed_types:
            evidence.append("booked bed type preserved")
        if booked.area_sqm is not None:
            if candidate.area_sqm is None:
                rejected.append("booked room area is not confirmed")
            elif candidate.area_sqm < booked.area_sqm:
                rejected.append("candidate room area is smaller")
            elif candidate.area_sqm > booked.area_sqm:
                improvements.append("larger room area")
        return rejected, sorted(set(improvements)), sorted(set(evidence))

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
    def _category_rank(candidate: CandidateEvaluation) -> int:
        return {
            MatchClassification.EXACT: 0,
            MatchClassification.EQUIVALENT: 1,
            MatchClassification.BETTER: 2,
        }.get(candidate.classification, 3)

    @staticmethod
    def _choose_accepted(
        candidates: list[CandidateEvaluation],
    ) -> tuple[CandidateEvaluation | None, bool]:
        """Choose the cheapest accepted full-stay total; category only breaks a price tie."""
        signatures = {
            (
                item.rate.breakfast_included,
                item.rate.meal_plan,
                item.rate.free_cancellation,
                item.rate.cancellation_deadline,
                item.rate.non_refundable,
                item.rate.payment_conditions,
            )
            for item in candidates
        }
        if len(signatures) != 1:
            return None, True
        return (
            min(
                candidates,
                key=lambda item: (
                    item.rate.current_price,
                    ExactReservationMatcher._category_rank(item),
                    item.diagnostic_index,
                ),
            ),
            False,
        )
