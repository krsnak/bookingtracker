"""Conservative extraction of one structured offer per Booking rate container."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from urllib.parse import urljoin

from app.booking.html_tree import Node, parse_html
from app.booking.models import ParseResult, ParseStatus, PropertyQuality, RateOffer, SearchCard
from app.booking.normalization import (
    normalize_room_name,
    parse_cancellation_deadline,
    parse_price,
    text_contains,
)
from app.booking.room_facts import extract_room_facts
from app.booking.selectors import BookingSelectors
from app.reservations.import_document import canonical_booking_hotel_url


class BookingSearchCardParser:
    """Parse search cards into non-comparable detail-page discovery leads."""

    def parse_html(self, html: str, source_url: str) -> list[SearchCard]:
        root = parse_html(html)
        cards: list[SearchCard] = []
        for card in root.all_test_id(BookingSelectors.SEARCH_CARD_TEST_ID):
            parsed = self._parse_card(card, source_url)
            if parsed is not None:
                cards.append(parsed)
        return cards

    def _parse_card(self, card: Node, source_url: str) -> SearchCard | None:
        link = card.first_test_id(BookingSelectors.SEARCH_CARD_LINK_TEST_ID)
        name = card.first_test_id(BookingSelectors.SEARCH_CARD_NAME_TEST_ID)
        price = card.first_test_id(BookingSelectors.SEARCH_CARD_HEADLINE_PRICE_TEST_ID)
        if link is None or name is None or price is None:
            return None
        detail_url = canonical_booking_hotel_url(urljoin(source_url, link.attrs.get("href", "")))
        parsed_price = parse_price(price.text())
        if not detail_url or not name.text() or not parsed_price:
            return None
        headline_price, headline_currency = parsed_price
        return SearchCard(
            property_name=name.text(),
            detail_url=detail_url,
            headline_price=headline_price,
            headline_currency=headline_currency,
            quality=BookingPropertyQualityParser().parse_node(card),
            source_text=card.text(),
            evidence={
                "card_selector": f"data-testid={BookingSelectors.SEARCH_CARD_TEST_ID}",
                "detail_link_selector": f"data-testid={BookingSelectors.SEARCH_CARD_LINK_TEST_ID}",
                "headline_price_selector": (
                    f"data-testid={BookingSelectors.SEARCH_CARD_HEADLINE_PRICE_TEST_ID}"
                ),
            },
        )


class BookingPropertyQualityParser:
    """Extract only explicit hotel-quality labels; absent values remain unknown."""

    def parse_html(self, html: str) -> PropertyQuality:
        return self.parse_node(parse_html(html))

    def parse_node(self, node: Node) -> PropertyQuality:
        score = node.first_test_id(BookingSelectors.BOOKING_SCORE_TEST_ID)
        reviews = node.first_test_id(BookingSelectors.REVIEW_COUNT_TEST_ID)
        category = node.first_test_id(BookingSelectors.STAR_CATEGORY_TEST_ID)
        location = node.first_test_id(BookingSelectors.LOCATION_DISTANCE_TEST_ID)
        score_value = self._score(score.text()) if score else None
        review_count = self._review_count(reviews.text()) if reviews else None
        evidence: dict[str, str] = {}
        for key, value in (
            ("booking_score", score),
            ("review_count", reviews),
            ("star_category", category),
            ("location_or_distance", location),
        ):
            if value:
                evidence[key] = f"data-testid={value.attrs.get('data-testid')}"
        return PropertyQuality(
            booking_score=score_value,
            review_count=review_count,
            review_count_confident=review_count is not None,
            star_category=category.text() if category and category.text() else None,
            location_or_distance=location.text() if location and location.text() else None,
            evidence=evidence,
        )

    @staticmethod
    def _score(value: str) -> Decimal | None:
        match = re.fullmatch(r"\s*(\d{1,2}(?:[.,]\d+)?)\s*", value)
        if match is None:
            return None
        score = Decimal(match.group(1).replace(",", "."))
        return score if score <= Decimal("10") else None

    @staticmethod
    def _review_count(value: str) -> int | None:
        # An abbreviated or fractional count (for example ``1.2K``) is not a
        # reliable count. Preserve the distinction rather than inventing 12.
        match = re.search(
            r"(?<![\d\s,.])(\d+|\d{1,3}(?:[\s,.]\d{3})+)\s*(?:reviews?|hodnocen[íi])\b",
            value,
            re.I,
        )
        if match is None:
            return None
        digits = re.sub(r"\D", "", match.group(1))
        return int(digits) if digits else None


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
                    legacy_rates,
                    property_name=self._property_name(root),
                    source_url=source_url,
                )
            return ParseResult(
                status=ParseStatus.UNSUPPORTED_STRUCTURE,
                warnings=["no Booking room containers found"],
            )
        property_name = self._property_name(root)
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
                    room_facts_text=room.text(),
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
            occupancy_text = (
                occupancy_node.text()
                if occupancy_node
                else self._explicit_occupancy(rate_text)
            )
            breakfast_included = self._breakfast_included(rate_text)
            offers.append(
                RateOffer(
                    property_name=property_name,
                    room_name=room_name,
                    normalized_room_name=normalize_room_name(room_name),
                    occupancy_text=occupancy_text,
                    room_facts=self._legacy_room_facts(room_name, rate_text),
                    meal_plan="Breakfast included" if breakfast_included is True else None,
                    breakfast_included=breakfast_included,
                    breakfast_genius_benefit=self._genius_breakfast(rate_text),
                    current_price=current_price,
                    currency=currency,
                    free_cancellation=free_cancellation,
                    cancellation_deadline=parse_cancellation_deadline(cancellation_text or ""),
                    cancellation_text=cancellation_text,
                    non_refundable=non_refundable,
                    payment_conditions=payment_node.text() if payment_node else None,
                    taxes_included=self._taxes_included(taxes_text or rate_text),
                    taxes_text=taxes_text or self._explicit_taxes_text(rate_text),
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
        room_facts_text: str,
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
                room_facts=extract_room_facts(room_facts_text),
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
    def _legacy_room_facts(room_name: str, rate_text: str):
        facts = extract_room_facts(rate_text)
        named = extract_room_facts(room_name)
        updates = {}
        if named.accommodation_kind is not None:
            updates["accommodation_kind"] = named.accommodation_kind
        if named.room_capacity is not None:
            updates["room_capacity"] = named.room_capacity
        return facts.model_copy(update=updates)

    @staticmethod
    def _explicit_occupancy(value: str) -> str | None:
        adult_matches = {
            int(match)
            for match in re.findall(r"\b(\d+)\s+(?:adults?|dospěl[íe])\b", value, re.I)
        }
        child_matches = {
            int(match)
            for match in re.findall(r"\b(\d+)\s+(?:children?|dět[íi])\b", value, re.I)
        }
        if len(adult_matches) != 1 or len(child_matches) > 1:
            return None
        adults = next(iter(adult_matches))
        if child_matches:
            return f"{adults} adults {next(iter(child_matches))} children"
        return f"{adults} adults"

    @staticmethod
    def _property_name(root: Node) -> str | None:
        modern = root.first_test_id("property-name")
        if modern and modern.text():
            return modern.text()
        for node in root.descendants():
            if node.attrs.get("id") in {"hp_hotel_name", "hotel_name"} and node.text():
                return node.text()
            classes = set(node.attrs.get("class", "").split())
            if classes.intersection({"pp-header__title", "hp__hotel-name"}) and node.text():
                return node.text()
        return None

    @staticmethod
    def _explicit_taxes_text(value: str) -> str | None:
        if text_contains(
            value,
            "includes taxes and fees",
            "taxes and fees included",
            "včetně daní a poplatků",
            "zahrnuje daně a poplatky",
        ):
            return "Includes taxes and fees"
        if text_contains(value, "taxes and fees excluded", "bez daní a poplatků"):
            return "Taxes and fees excluded"
        return None

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
            "includes taxes and fees",
            "taxes and fees included",
            "včetně daní a poplatků",
            "zahrnuje daně a poplatky",
        ):
            return True
        if text_contains(value, "taxes and fees excluded", "bez daní a poplatků"):
            return False
        return None
