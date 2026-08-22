"""Conservative price comparison, deliberately separate from matching."""

from __future__ import annotations

from decimal import Decimal

from app.matching.models import MatchResult
from app.pricing.models import PriceBasis, PriceComparison, PriceDirection
from app.reservations.models import Reservation


class ComparablePriceService:
    """Compare final totals only after the exact matcher accepted a rate."""

    def compare(self, reservation: Reservation, match: MatchResult) -> PriceComparison:
        if not match.accepted or match.matched_rate is None:
            return PriceComparison(
                comparable=False,
                warnings=["an accepted exact-reservation match is required before pricing"],
            )
        rate = match.matched_rate
        if reservation.booked_total_price is None:
            return PriceComparison(
                comparable=False,
                warnings=["booked final total is unknown"],
            )
        if not reservation.currency or rate.currency != reservation.currency:
            return PriceComparison(
                comparable=False,
                warnings=["currency mismatch or unknown currency; conversion is not supported"],
            )
        if rate.taxes_included is not True:
            return PriceComparison(
                comparable=False,
                warnings=["current offer does not explicitly include mandatory taxes and fees"],
            )
        delta = rate.current_price - reservation.booked_total_price
        direction = (
            PriceDirection.LOWER
            if delta < 0
            else PriceDirection.HIGHER
            if delta > 0
            else PriceDirection.SAME
        )
        delta_percent = (delta / reservation.booked_total_price * Decimal("100")).quantize(
            Decimal("0.01")
        )
        return PriceComparison(
            comparable=True,
            basis=PriceBasis.FINAL_TOTAL_INCLUDING_TAXES,
            booked_price=reservation.booked_total_price,
            current_price=rate.current_price,
            currency=reservation.currency,
            delta_amount=delta,
            delta_percent=delta_percent,
            direction=direction,
            reasons=["matched offer and booked reservation use final totals including taxes"],
        )
