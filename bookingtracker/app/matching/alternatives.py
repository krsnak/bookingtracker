"""Information-only nearby offers; never a comparable-price decision."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from difflib import SequenceMatcher
from enum import StrEnum

from pydantic import BaseModel, Field

from app.booking.models import RateOffer, RoomFacts
from app.booking.room_facts import extract_room_facts
from app.matching.matcher import ExactReservationMatcher
from app.matching.normalization import normalized_tokens
from app.reservations.models import Reservation


class AlternativeOffer(BaseModel):
    """A safely described option which is deliberately not comparable."""

    rate: RateOffer
    similarity_score: Decimal = Field(ge=0, le=1)
    preserved: list[str] = Field(default_factory=list)
    better: list[str] = Field(default_factory=list)
    unknown_or_different: list[str] = Field(default_factory=list)
    worse: list[str] = Field(default_factory=list)
    diagnostic_index: int = Field(default=0, ge=0, exclude=True)


class AlternativeHardRejectCode(StrEnum):
    PROPERTY_NOT_PROVEN = "property_not_proven"
    PROPERTY_MISMATCH = "property_mismatch"
    OCCUPANCY_MISMATCH = "occupancy_mismatch"
    ROOM_COUNT_MISMATCH = "room_count_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    TAX_INCLUSIVE_TOTAL_MISSING = "tax_inclusive_total_missing"
    DORM_MISMATCH = "dorm_mismatch"
    PRIVATE_ROOM_NOT_PROVEN = "private_room_not_proven"
    BREAKFAST_WORSE = "breakfast_worse"
    CANCELLATION_WORSE = "cancellation_worse"
    PAYMENT_WORSE = "payment_worse"


class AlternativeSoftUnknownCode(StrEnum):
    BREAKFAST = "breakfast_unknown"
    MEAL = "meal_unknown"
    CANCELLATION = "cancellation_unknown"
    PAYMENT = "payment_unknown"
    PRIVATE_BATHROOM = "private_bathroom_unknown"
    BALCONY = "balcony_unknown"
    TERRACE = "terrace_unknown"
    AIR_CONDITIONING = "air_conditioning_unknown"
    KITCHEN = "kitchen_unknown"
    ACCESSIBLE = "accessible_unknown"
    VIEW = "view_unknown"
    AREA = "area_unknown"
    BED_TYPE = "bed_type_unknown"


class AlternativeOfferDiagnostics(BaseModel):
    """Aggregated, non-identifying explanation of a Phase A evaluation."""

    offers_found: int = Field(ge=0)
    offers_evaluated: int = Field(ge=0)
    alternatives_accepted: int = Field(ge=0)
    hard_rejects: dict[str, int] = Field(default_factory=dict)
    soft_unknown_evidence: dict[str, int] = Field(default_factory=dict)
    single_hard_rejects: dict[str, int] = Field(default_factory=dict)


class AlternativeOfferEvaluator:
    """Find a few same-search alternatives without relaxing exact matching."""

    def evaluate(self, reservation: Reservation, offers: list[RateOffer]) -> list[AlternativeOffer]:
        alternatives, _diagnostics = self.evaluate_with_diagnostics(reservation, offers)
        return alternatives

    def diagnostics(
        self, reservation: Reservation, offers: list[RateOffer]
    ) -> AlternativeOfferDiagnostics:
        """Describe snapshot outcomes without exposing candidate-specific source data."""
        _alternatives, diagnostics = self.evaluate_with_diagnostics(reservation, offers)
        return diagnostics

    def evaluate_with_diagnostics(
        self, reservation: Reservation, offers: list[RateOffer]
    ) -> tuple[list[AlternativeOffer], AlternativeOfferDiagnostics]:
        candidates: list[AlternativeOffer] = []
        hard_rejects: Counter[str] = Counter()
        soft_unknown_evidence: Counter[str] = Counter()
        single_hard_rejects: Counter[str] = Counter()
        for index, offer in enumerate(offers):
            rejection_codes = self._hard_rejection_codes(reservation, offer)
            hard_rejects.update(rejection_codes)
            soft_unknown_evidence.update(self._soft_unknown_codes(reservation, offer))
            if len(rejection_codes) == 1:
                single_hard_rejects.update(rejection_codes)
            if rejection_codes:
                continue
            alternative = self._evaluate(reservation, offer, index)
            assert alternative is not None
            candidates.append(alternative)
        alternatives = sorted(candidates, key=self._sort_key)[:3]
        return alternatives, AlternativeOfferDiagnostics(
            offers_found=len(offers),
            offers_evaluated=len(offers),
            alternatives_accepted=len(candidates),
            hard_rejects=dict(sorted(hard_rejects.items())),
            soft_unknown_evidence=dict(sorted(soft_unknown_evidence.items())),
            single_hard_rejects=dict(sorted(single_hard_rejects.items())),
        )

    def _evaluate(
        self, reservation: Reservation, rate: RateOffer, diagnostic_index: int
    ) -> AlternativeOffer | None:
        if self._hard_rejection_codes(reservation, rate):
            return None

        preserved: list[str] = ["Stejné ubytování", "Požadované obsazení"]
        better: list[str] = []
        unknown: list[str] = []
        worse: list[str] = []
        booked = extract_room_facts(reservation.room_type or "")
        candidate = rate.room_facts

        self._rate_terms(reservation, rate, preserved, better, unknown)
        self._room_evidence(booked, candidate, preserved, better, unknown, worse)

        # Names are intentionally a weak tie-breaker only: Deluxe/Classic is not evidence.
        name_similarity = Decimal(
            str(SequenceMatcher(None, reservation.room_type or "", rate.room_name).ratio())
        )
        evidence_total = len(preserved) + len(better) + len(unknown) + len(worse)
        score = (
            Decimal(len(preserved) * 10 + len(better) * 2 - len(unknown) * 3 - len(worse) * 5)
            / Decimal(max(1, evidence_total * 10))
        ) + name_similarity / Decimal("100")
        return AlternativeOffer(
            rate=rate,
            similarity_score=max(Decimal("0"), min(Decimal("1"), score)),
            preserved=sorted(set(preserved)),
            better=sorted(set(better)),
            unknown_or_different=sorted(set(unknown)),
            worse=sorted(set(worse)),
            diagnostic_index=diagnostic_index,
        )

    @staticmethod
    def _hard_rejection_codes(reservation: Reservation, rate: RateOffer) -> list[str]:
        """Return only structural or explicitly worse Phase A safety boundaries."""
        rejected: list[str] = []
        if not reservation.property_name or not rate.property_name:
            rejected.append(AlternativeHardRejectCode.PROPERTY_NOT_PROVEN.value)
        elif normalized_tokens(reservation.property_name) != normalized_tokens(rate.property_name):
            rejected.append(AlternativeHardRejectCode.PROPERTY_MISMATCH.value)
        _score, _warning, occupancy_rejection = ExactReservationMatcher._occupancy(
            reservation, rate
        )
        if reservation.rooms_count != 1:
            rejected.append(AlternativeHardRejectCode.ROOM_COUNT_MISMATCH.value)
        elif occupancy_rejection:
            rejected.append(AlternativeHardRejectCode.OCCUPANCY_MISMATCH.value)
        if (
            reservation.currency != rate.currency
            or rate.taxes_included is not True
            or rate.current_price <= 0
        ):
            if reservation.currency != rate.currency:
                rejected.append(AlternativeHardRejectCode.CURRENCY_MISMATCH.value)
            if rate.taxes_included is not True or rate.current_price <= 0:
                rejected.append(AlternativeHardRejectCode.TAX_INCLUSIVE_TOTAL_MISSING.value)
        booked = extract_room_facts(reservation.room_type or "")
        if booked.accommodation_kind == "private_room":
            if rate.room_facts.accommodation_kind == "dorm_bed":
                rejected.append(AlternativeHardRejectCode.DORM_MISMATCH.value)
            elif rate.room_facts.accommodation_kind != "private_room":
                rejected.append(AlternativeHardRejectCode.PRIVATE_ROOM_NOT_PROVEN.value)

        # Missing evidence is informative rather than rejecting. A documented downgrade of a
        # booked protection remains a Phase A safety boundary.
        if reservation.breakfast_included is True and rate.breakfast_included is False:
            rejected.append(AlternativeHardRejectCode.BREAKFAST_WORSE.value)
        if reservation.free_cancellation is True and (
            rate.free_cancellation is False or rate.non_refundable is True
        ):
            rejected.append(AlternativeHardRejectCode.CANCELLATION_WORSE.value)
        if (
            reservation.cancellation_deadline
            and rate.cancellation_deadline
            and rate.cancellation_deadline < reservation.cancellation_deadline
        ):
            rejected.append(AlternativeHardRejectCode.CANCELLATION_WORSE.value)
        if reservation.payment_conditions:
            booked_payment = reservation.payment_conditions.casefold()
            candidate_payment = (rate.payment_conditions or "").casefold()
            booked_pay_property = (
                "pay at property" in booked_payment or "zaplatíte v ubytování" in booked_payment
            )
            candidate_prepay = (
                "prepayment required" in candidate_payment or "pay in advance" in candidate_payment
            )
            if booked_pay_property and candidate_prepay:
                rejected.append(AlternativeHardRejectCode.PAYMENT_WORSE.value)
        return rejected

    @staticmethod
    def _soft_unknown_codes(reservation: Reservation, rate: RateOffer) -> set[str]:
        """Return missing evidence only; these codes never alter Phase A eligibility."""
        unknown: set[str] = set()
        if reservation.breakfast_included is True and rate.breakfast_included is None:
            unknown.add(AlternativeSoftUnknownCode.BREAKFAST.value)
        if reservation.meal_plan and (
            not rate.meal_plan
            or normalized_tokens(reservation.meal_plan) != normalized_tokens(rate.meal_plan)
        ):
            unknown.add(AlternativeSoftUnknownCode.MEAL.value)
        if reservation.free_cancellation is True and rate.free_cancellation is None:
            unknown.add(AlternativeSoftUnknownCode.CANCELLATION.value)
        if reservation.cancellation_deadline and rate.cancellation_deadline is None:
            unknown.add(AlternativeSoftUnknownCode.CANCELLATION.value)
        if reservation.payment_conditions and not rate.payment_conditions:
            unknown.add(AlternativeSoftUnknownCode.PAYMENT.value)

        booked = extract_room_facts(reservation.room_type or "")
        candidate = rate.room_facts
        for field, code in (
            ("private_bathroom", AlternativeSoftUnknownCode.PRIVATE_BATHROOM),
            ("balcony", AlternativeSoftUnknownCode.BALCONY),
            ("terrace", AlternativeSoftUnknownCode.TERRACE),
            ("air_conditioning", AlternativeSoftUnknownCode.AIR_CONDITIONING),
            ("kitchen", AlternativeSoftUnknownCode.KITCHEN),
            ("accessible", AlternativeSoftUnknownCode.ACCESSIBLE),
        ):
            if getattr(booked, field) is not None and getattr(candidate, field) is None:
                unknown.add(code.value)
        if booked.view and candidate.view is None:
            unknown.add(AlternativeSoftUnknownCode.VIEW.value)
        if booked.area_sqm is not None and candidate.area_sqm is None:
            unknown.add(AlternativeSoftUnknownCode.AREA.value)
        if booked.bed_types and not candidate.bed_types:
            unknown.add(AlternativeSoftUnknownCode.BED_TYPE.value)
        return unknown

    @staticmethod
    def _rate_terms(
        reservation: Reservation,
        rate: RateOffer,
        preserved: list[str],
        better: list[str],
        unknown: list[str],
    ) -> None:
        if reservation.breakfast_included is True:
            (preserved if rate.breakfast_included is True else unknown).append(
                "Snídaně zachována" if rate.breakfast_included is True else "Snídaně není potvrzena"
            )
        if reservation.meal_plan:
            if rate.meal_plan and normalized_tokens(reservation.meal_plan) == normalized_tokens(
                rate.meal_plan
            ):
                preserved.append("Strava zachována")
            else:
                unknown.append("Strava není potvrzena")
        if reservation.free_cancellation is True:
            (preserved if rate.free_cancellation is True else unknown).append(
                "Bezplatné storno zachováno"
                if rate.free_cancellation is True
                else "Storno není potvrzeno"
            )
        if reservation.cancellation_deadline:
            if rate.cancellation_deadline is None:
                unknown.append("Termín storna není potvrzen")
            elif rate.cancellation_deadline == reservation.cancellation_deadline:
                preserved.append("Stejný termín storna")
            elif rate.cancellation_deadline > reservation.cancellation_deadline:
                better.append("Pozdější bezplatné storno")
        if reservation.payment_conditions:
            if rate.payment_conditions:
                preserved.append("Platební podmínky bez zjevného zhoršení")
            else:
                unknown.append("Platební podmínky nejsou doloženy")

    @staticmethod
    def _room_evidence(
        booked: RoomFacts,
        candidate: RoomFacts,
        preserved: list[str],
        better: list[str],
        unknown: list[str],
        worse: list[str],
    ) -> None:
        if booked.accommodation_kind == candidate.accommodation_kind and booked.accommodation_kind:
            preserved.append(
                "Soukromý pokoj zachován"
                if booked.accommodation_kind == "private_room"
                else "Typ ubytování zachován"
            )
        elif booked.accommodation_kind:
            unknown.append("Typ pokoje není potvrzen")
        for field, label in (
            ("private_bathroom", "Vlastní koupelna"),
            ("balcony", "Balkon"),
            ("terrace", "Terasa"),
            ("air_conditioning", "Klimatizace"),
            ("kitchen", "Kuchyň"),
            ("accessible", "Bezbariérovost"),
        ):
            before, after = getattr(booked, field), getattr(candidate, field)
            if before is None:
                continue
            if after is None:
                unknown.append(f"{label} není potvrzen")
            elif after == before:
                preserved.append(f"{label} zachován")
            elif before is False and after is True:
                better.append(label)
            else:
                worse.append(f"{label} je horší")
        if booked.view:
            if candidate.view == booked.view:
                preserved.append("Výhled zachován")
            elif candidate.view is None:
                unknown.append("Výhled není potvrzen")
            else:
                worse.append("Výhled se liší")
        if booked.area_sqm is not None:
            if candidate.area_sqm is None:
                unknown.append("Plocha pokoje není potvrzena")
            elif candidate.area_sqm >= booked.area_sqm:
                (better if candidate.area_sqm > booked.area_sqm else preserved).append(
                    "Větší plocha pokoje"
                    if candidate.area_sqm > booked.area_sqm
                    else "Plocha pokoje zachována"
                )
            else:
                worse.append("Plocha pokoje je menší")
        if booked.bed_types:
            if set(booked.bed_types) <= set(candidate.bed_types):
                preserved.append("Typ postele zachován")
            elif candidate.bed_types:
                worse.append("Typ postele se liší")
            else:
                unknown.append("Typ postele není potvrzen")

    @staticmethod
    def _sort_key(item: AlternativeOffer) -> tuple[object, ...]:
        return (
            -len(item.preserved),
            len(item.worse),
            len(item.unknown_or_different),
            -len(item.better),
            -item.similarity_score,
            item.rate.current_price,
            item.diagnostic_index,
        )
