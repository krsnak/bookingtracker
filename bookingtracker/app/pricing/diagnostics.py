"""Stable reason codes and privacy-safe details for persisted check diagnostics."""

from __future__ import annotations

import re

from app.pricing.models import CheckReasonCode, PriceCheckStatus

_REDACTIONS = (
    (re.compile(r"(?is)<(?:!doctype|html|head|body|script|style)\b.*?>.*"), "[html removed]"),
    (re.compile(r"(?is)<[^>]{1,500}>"), "[html removed]"),
    (
        re.compile(r"(?i)\b(?:authorization|cookie|set-cookie|x-api-key)\s*[:=]\s*\S+"),
        "[credential removed]",
    ),
    (
        re.compile(
            r"(?i)\b(?:token|access_token|refresh_token|session|sid|auth)"
            r"\s*[:=]\s*[^\s,;&]+"
        ),
        "[credential removed]",
    ),
    (
        re.compile(
            r"(?i)\b(?:booking|reservation|confirmation)"
            r"(?:\s+(?:number|id|code))?\s*[:#=]?\s*[A-Z0-9-]{5,}\b"
        ),
        "[reservation removed]",
    ),
    (re.compile(r"(?i)\bpin\s*[:#=]?\s*\d{3,}\b"), "[pin removed]"),
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[email removed]"),
    (re.compile(r"(?i)(?:https?|mailto):[^\s<>\"']+"), "[url removed]"),
    (re.compile(r"(?<![\w:])/(?:[^/\s]+/)+[^/\s:]+"), "[path removed]"),
)


def reason_code_for(status: PriceCheckStatus, detail: str | None = None) -> CheckReasonCode | None:
    if status is PriceCheckStatus.SUCCESS:
        return None
    if status is PriceCheckStatus.TIMEOUT:
        return CheckReasonCode.TIMEOUT
    if status is PriceCheckStatus.LOGGED_OUT:
        return CheckReasonCode.LOGIN_REQUIRED
    if status is PriceCheckStatus.CAPTCHA_REQUIRED:
        return CheckReasonCode.CAPTCHA_REQUIRED
    if status is PriceCheckStatus.PARSER_ERROR:
        return CheckReasonCode.PARSER_ERROR
    if status in {
        PriceCheckStatus.NO_MATCH,
        PriceCheckStatus.AMBIGUOUS,
        PriceCheckStatus.NO_AVAILABILITY,
    }:
        return CheckReasonCode.NO_COMPARABLE_OFFER
    if status is PriceCheckStatus.BROWSER_ERROR:
        return CheckReasonCode.BROWSER_ERROR
    if status is PriceCheckStatus.NAVIGATION_ERROR:
        text = (detail or "").casefold()
        if any(word in text for word in ("network", "dns", "connection", "offline", "net::")):
            return CheckReasonCode.NETWORK_ERROR
        return CheckReasonCode.NAVIGATION_ERROR
    return CheckReasonCode.UNEXPECTED_ERROR


def sanitize_error_detail(value: object | None, *, fallback: str | None = None) -> str | None:
    """Return one short line without credentials, PII, markup, URLs, paths, or traceback."""
    if value is None:
        return fallback
    text = str(value).replace("\x00", " ")
    if "Traceback (most recent call last)" in text:
        text = text.split("Traceback (most recent call last)", 1)[0]
    text = text.splitlines()[0] if text.splitlines() else ""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+", " ", text).strip(" ,:;-")
    if not text:
        return fallback
    return text[:237] + "..." if len(text) > 240 else text
