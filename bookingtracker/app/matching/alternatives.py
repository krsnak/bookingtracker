"""Information-only nearby offers; never a comparable-price decision."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from difflib import SequenceMatcher

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


class AlternativeOfferDiagnostics(BaseModel):
    """Aggregated, non-identifying explanation of a Phase A evaluation."""

    offers_found: int = Field(ge=0)
    alternatives_accepted: int = Field(ge=0)
    hard_rejects: dict[str, int] = Field(default_factory=dict)
    soft_unknown_evidence: dict[str, int] = Field(default_factory=dict)


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
        for index, offer in enumerate(offers):
            rejection_reasons = self._hard_rejection_reasons(reservation, offer)
            if rejection_reasons:
                hard_rejects.update(rejection_reasons)
                continue
            alternative = self._evaluate(reservation, offer, index)
            assert alternative is not None
            candidates.append(alternative)
            soft_unknown_evidence.update(alternative.unknown_or_different)
        alternatives = sorted(candidates, key=self._sort_key)[:3]
        return alternatives, AlternativeOfferDiagnostics(
            offers_found=len(offers),
            alternatives_accepted=len(candidates),
            hard_rejects=dict(sorted(hard_rejects.items())),
            soft_unknown_evidence=dict(sorted(soft_unknown_evidence.items())),
        )

    def _evaluate(
        self, reservation: Reservation, rate: RateOffer, diagnostic_index: int
    ) -> AlternativeOffer | None:
        if self._hard_rejection_reasons(reservation, rate):
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
    def _hard_rejection_reasons(reservation: Reservation, rate: RateOffer) -> list[str]:
        """Return only structural or explicitly worse Phase A safety boundaries."""
        rejected: list[str] = []
        if not reservation.property_name or not rate.property_name:
            rejected.append("Ubytování není potvrzeno")
        elif normalized_tokens(reservation.property_name) != normalized_tokens(rate.property_name):
            rejected.append("Jiné ubytování")
        _score, _warning, occupancy_rejection = ExactReservationMatcher._occupancy(
            reservation, rate
        )
        if reservation.rooms_count != 1:
            rejected.append("Kompatibilní počet pokojů není potvrzen")
        elif occupancy_rejection:
            rejected.append("Požadované obsazení není potvrzeno")
        if (
            reservation.currency != rate.currency
            or rate.taxes_included is not True
            or rate.current_price <= 0
        ):
            if reservation.currency != rate.currency:
                rejected.append("Jiná měna")
            if rate.taxes_included is not True or rate.current_price <= 0:
                rejected.append("Bezpečný konečný total včetně daní není potvrzen")
        booked = extract_room_facts(reservation.room_type or "")
        if booked.accommodation_kind == "private_room":
            if rate.room_facts.accommodation_kind == "dorm_bed":
                rejected.append("Lůžko ve sdíleném pokoji nemůže nahradit soukromý pokoj")
            elif rate.room_facts.accommodation_kind != "private_room":
                rejected.append("Soukromý pokoj kandidáta není potvrzen")

        # Missing evidence is informative rather than rejecting. A documented downgrade of a
        # booked protection remains a Phase A safety boundary.
        if reservation.breakfast_included is True and rate.breakfast_included is False:
            rejected.append("Snídaně je explicitně horší")
        if reservation.free_cancellation is True and (
            rate.free_cancellation is False or rate.non_refundable is True
        ):
            rejected.append("Storno podmínky jsou explicitně horší")
        if (
            reservation.cancellation_deadline
            and rate.cancellation_deadline
            and rate.cancellation_deadline < reservation.cancellation_deadline
        ):
            rejected.append("Termín storna je explicitně horší")
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
                rejected.append("Platební podmínky jsou explicitně horší")
        return rejected

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
