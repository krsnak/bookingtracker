"""Centralized, intentionally narrow Booking DOM selector vocabulary."""

from __future__ import annotations


class BookingSelectors:
    AVAILABILITY = '[data-testid="availability-table"]'
    NO_AVAILABILITY = '[data-testid="no-availability"]'
    PROPERTY_NAME = '[data-testid="property-name"]'
    ROOM = '[data-testid="room-row"]'
    ROOM_NAME = '[data-testid="room-name"]'
    RATE = '[data-testid="rate-option"]'
    CURRENT_PRICE = '[data-testid="current-price"]'
    ORIGINAL_PRICE = '[data-testid="original-price"]'
    OCCUPANCY = '[data-testid="occupancy"]'
    MEAL_PLAN = '[data-testid="meal-plan"]'
    BREAKFAST = '[data-testid="breakfast"]'
    GENIUS = '[data-testid="genius"]'
    CANCELLATION = '[data-testid="cancellation"]'
    PAYMENT = '[data-testid="payment-conditions"]'
    TAXES = '[data-testid="taxes"]'
    LEGACY_RATE = "tr.js-rt-block-row"
    LEGACY_ROOM_NAME_CLASS = "hprt-roomtype-link"
    LEGACY_PRICE_CLASS = "bui-price-display__value"
    LEGACY_OCCUPANCY_CLASS = "hprt-roomtype-occupancy-text"
    LEGACY_TAXES_CLASS = "prd-taxes-and-fees-under-price"

    ROOM_TEST_ID = "room-row"
    RATE_TEST_ID = "rate-option"
    NO_AVAILABILITY_TEST_ID = "no-availability"
    LEGACY_RATE_CLASS = "js-rt-block-row"
