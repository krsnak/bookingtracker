"""Conservative extraction of one structured offer per Booking rate container."""

from __future__ import annotations

from datetime import datetime

from app.booking.html_tree import Node, parse_html
from app.booking.models import ParseResult, ParseStatus, RateOffer
from app.booking.normalization import (
    normalize_room_name,
    parse_cancellation_deadline,
    parse_price,
    text_contains,
)
from app.booking.selectors import BookingSelectors


class BookingRateParser:
    """Consumes an already navigated page; it never owns browser lifecycle."""

    def parse(self, page: object, source_url: str | None = None) -> ParseResult:
        try:
            html = page.content()  # type: ignore[attr-defined]
            url = source_url or str(page.url)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError) as error:
            return ParseResult(status=ParseStatus.ERROR, error=f"page access failed: {error}")
        return self.parse_html(html, source_url=url)

    def parse_html(self, html: str, source_url: str) -> ParseResult:
        root = parse_html(html)
        if root.first_test_id(BookingSelectors.NO_AVAILABILITY_TEST_ID):
            return ParseResult(status=ParseStatus.NO_AVAILABILITY)
        rooms = root.all_test_id(BookingSelectors.ROOM_TEST_ID)
        if not rooms:
            legacy_rates = root.all_class(BookingSelectors.LEGACY_RATE_CLASS)
            if legacy_rates:
                return self._parse_legacy_rows(
                    legacy_rates, property_name=None, source_url=source_url
                )
            return ParseResult(
                status=ParseStatus.UNSUPPORTED_STRUCTURE,
                warnings=["no Booking room containers found"],
            )
        property_node = root.first_test_id("property-name")
        property_name = property_node.text() if property_node else None
        offers: list[RateOffer] = []
        warnings: list[str] = []
        partial = False
        for room_index, room in enumerate(rooms, start=1):
            room_name_node = room.first_test_id("room-name")
            room_name = room_name_node.text() if room_name_node else None
            rates = room.all_test_id(BookingSelectors.RATE_TEST_ID)
            if not room_name:
                partial = True
                warnings.append(f"room {room_index} has no room-name evidence")
                continue
            if not rates:
                partial = True
                warnings.append(f"room '{room_name}' has no rate containers")
                continue
            for rate_index, rate in enumerate(rates, start=1):
                offer, rate_warning = self._parse_rate(
                    rate,
                    property_name=property_name,
                    room_name=room_name,
                    source_url=source_url,
                )
                if rate_warning:
                    partial = True
                    warnings.append(f"room '{room_name}', rate {rate_index}: {rate_warning}")
                if offer:
                    offers.append(offer)
        status = ParseStatus.PARTIAL if partial else ParseStatus.SUCCESS
        return ParseResult(
            status=status, offers=offers, rooms_detected=len(rooms), warnings=warnings
        )

    def _parse_legacy_rows(
        self,
        rates: list[Node],
        property_name: str | None,
        source_url: str,
    ) -> ParseResult:
        offers: list[RateOffer] = []
        warnings: list[str] = []
        partial = False
        for index, rate in enumerate(rates, start=1):
            room_node = rate.first_class(BookingSelectors.LEGACY_ROOM_NAME_CLASS)
            price_node = rate.first_class(BookingSelectors.LEGACY_PRICE_CLASS)
            room_name = room_node.text() if room_node else None
            parsed_price = parse_price(price_node.text()) if price_node else None
            if not room_name or not parsed_price:
                partial = True
                missing = "room name" if not room_name else "reliable current price"
                warnings.append(f"legacy rate row {index} has no {missing}")
                continue
            current_price, currency = parsed_price
            rate_text = rate.text()
            cancellation_node = rate.first_test_id("cancellation-policy")
            payment_node = rate.first_test_id("prepayment-policy")
            taxes_node = rate.first_class(BookingSelectors.LEGACY_TAXES_CLASS)
            occupancy_node = rate.first_class(BookingSelectors.LEGACY_OCCUPANCY_CLASS)
            cancellation_text = cancellation_node.text() if cancellation_node else None
            taxes_text = taxes_node.text() if taxes_node else None
            free_cancellation, non_refundable = self._cancellation_flags(cancellation_text)
            offers.append(
                RateOffer(
                    property_name=property_name,
                    room_name=room_name,
                    normalized_room_name=normalize_room_name(room_name),
                    occupancy_text=occupancy_node.text() if occupancy_node else None,
                    breakfast_included=self._breakfast_included(rate_text),
                    breakfast_genius_benefit=self._genius_breakfast(rate_text),
                    current_price=current_price,
                    currency=currency,
                    free_cancellation=free_cancellation,
                    cancellation_deadline=parse_cancellation_deadline(cancellation_text or ""),
                    cancellation_text=cancellation_text,
                    non_refundable=non_refundable,
                    payment_conditions=payment_node.text() if payment_node else None,
                    taxes_included=self._taxes_included(taxes_text),
                    taxes_text=taxes_text,
                    source_row_text=rate_text,
                    source_url=source_url,
                    scrape_timestamp=datetime.now(),
                    evidence={
                        "rate_selector": BookingSelectors.LEGACY_RATE,
                        "room_selector": ".hprt-roomtype-link",
                        "current_price_selector": ".bui-price-display__value",
                    },
                )
            )
        status = ParseStatus.PARTIAL if partial else ParseStatus.SUCCESS
        return ParseResult(
            status=status, offers=offers, rooms_detected=len(rates), warnings=warnings
        )

    def _parse_rate(
        self,
        rate: Node,
        *,
        property_name: str | None,
        room_name: str,
        source_url: str,
    ) -> tuple[RateOffer | None, str | None]:
        current_node = rate.first_test_id("current-price")
        if current_node is None:
            return None, "rate container has no current-price selector"
        parsed_current = parse_price(current_node.text())
        if parsed_current is None:
            return None, "current-price text is not a reliable localized price"
        current_price, currency = parsed_current
        original_node = rate.first_test_id("original-price")
        parsed_original = parse_price(original_node.text()) if original_node else None
        parser_warnings: list[str] = []
        original_price = None
        if parsed_original:
            candidate_original, original_currency = parsed_original
            if original_currency == currency:
                original_price = candidate_original
            else:
                parser_warnings.append("original price uses a different currency and was ignored")
        elif original_node:
            parser_warnings.append("original-price text could not be parsed")

        breakfast_node = rate.first_test_id("breakfast")
        genius_node = rate.first_test_id("genius")
        cancellation_node = rate.first_test_id("cancellation")
        payment_node = rate.first_test_id("payment-conditions")
        taxes_node = rate.first_test_id("taxes")
        meal_node = rate.first_test_id("meal-plan")
        occupancy_node = rate.first_test_id("occupancy")
        breakfast_text = breakfast_node.text() if breakfast_node else ""
        genius_text = genius_node.text() if genius_node else ""
        cancellation_text = cancellation_node.text() if cancellation_node else None
        taxes_text = taxes_node.text() if taxes_node else None
        rate_text = rate.text()
        genius = True if genius_node and text_contains(genius_text, "genius") else None
        if genius_node and genius is None:
            parser_warnings.append("genius selector had no explicit Genius evidence")
        genius_percent = self._discount_percent(genius_text)
        breakfast_included = self._breakfast_included(breakfast_text)
        breakfast_genius = self._genius_breakfast(breakfast_text)
        if breakfast_genius:
            breakfast_included = True
        free_cancellation, non_refundable = self._cancellation_flags(cancellation_text)
        taxes_included = self._taxes_included(taxes_text)
        evidence = {
            "room_selector": "data-testid=room-name",
            "rate_selector": "data-testid=rate-option",
            "current_price_selector": "data-testid=current-price",
        }
        for name, node in (
            ("original_price", original_node),
            ("breakfast", breakfast_node),
            ("genius", genius_node),
            ("cancellation", cancellation_node),
            ("payment", payment_node),
            ("taxes", taxes_node),
        ):
            if node:
                evidence[f"{name}_selector"] = f"data-testid={node.attrs.get('data-testid')}"
        return (
            RateOffer(
                property_name=property_name,
                room_name=room_name,
                normalized_room_name=normalize_room_name(room_name),
                occupancy_text=occupancy_node.text() if occupancy_node else None,
                meal_plan=meal_node.text() if meal_node else None,
                breakfast_included=breakfast_included,
                breakfast_genius_benefit=breakfast_genius,
                genius=genius,
                genius_discount_percent=genius_percent,
                current_price=current_price,
                original_price=original_price,
                currency=currency,
                free_cancellation=free_cancellation,
                cancellation_deadline=parse_cancellation_deadline(cancellation_text or ""),
                cancellation_text=cancellation_text,
                non_refundable=non_refundable,
                payment_conditions=payment_node.text() if payment_node else None,
                taxes_included=taxes_included,
                taxes_text=taxes_text,
                source_row_text=rate_text,
                source_url=source_url,
                scrape_timestamp=datetime.now(),
                parser_warnings=parser_warnings,
                evidence=evidence,
            ),
            None,
        )

    @staticmethod
    def _discount_percent(value: str) -> int | None:
        import re

        match = re.search(r"(\d{1,3})\s*%", value)
        return int(match.group(1)) if match else None

    @staticmethod
    def _breakfast_included(value: str) -> bool | None:
        if text_contains(value, "breakfast included", "snídaně v ceně"):
            return True
        if text_contains(value, "breakfast not included", "bez snídaně"):
            return False
        return None

    @staticmethod
    def _genius_breakfast(value: str) -> bool | None:
        if text_contains(value, "free breakfast for genius", "snídaně zdarma pro hosty genius"):
            return True
        if text_contains(value, "breakfast included", "snídaně v ceně"):
            return False
        return None

    @staticmethod
    def _cancellation_flags(value: str | None) -> tuple[bool | None, bool | None]:
        if not value:
            return None, None
        if text_contains(value, "non-refundable", "nevratná"):
            return False, True
        if text_contains(value, "free cancellation", "zrušení zdarma"):
            return True, False
        return None, None

    @staticmethod
    def _taxes_included(value: str | None) -> bool | None:
        if not value:
            return None
        if text_contains(
            value,
            "taxes and fees included",
            "včetně daní a poplatků",
            "zahrnuje daně a poplatky",
        ):
            return True
        if text_contains(value, "taxes and fees excluded", "bez daní a poplatků"):
            return False
        return None
