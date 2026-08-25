"""Sanitize and audit a narrow availability subtree for offline parser replay."""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

_DROP_CONTENT_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "template",
    "button",
    "select",
    "textarea",
}
_DROP_ELEMENTS = {"input"}
_UNWRAP_TAGS = {"form"}
_ALLOWED_ATTRIBUTES = {"class", "data-testid"}
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\w)(?:\+\d[\d ().-]{7,}\d)(?!\w)")
_BOOKING_NUMBER = re.compile(
    r"(?i)\b(?:booking|reservation|confirmation|rezervace|potvrzení)"
    r"(?:\s+(?:number|id|code|číslo))?\s*[:#-]?\s*[A-Z0-9-]{5,}\b"
)
_PIN = re.compile(r"(?i)\bpin\s*[:#-]?\s*\d{3,}\b")
_TOKEN = re.compile(
    r"(?i)\b(?:token|access_token|refresh_token|session|sid|authorization|cookie)"
    r"\s*[:=]\s*[^\s,;&]+"
)
_LONG_IDENTIFIER = re.compile(r"(?<!\d)\d{9,}(?!\d)")
_URL = re.compile(r"(?i)(?:https?|mailto):[^\s<>\"']+")


def _sanitize_text(value: str) -> str:
    text = value
    for pattern, replacement in (
        (_EMAIL, "[email removed]"),
        (_PHONE, "[phone removed]"),
        (_BOOKING_NUMBER, "[sensitive value removed]"),
        (_PIN, "[sensitive value removed]"),
        (_TOKEN, "[credential removed]"),
        (_URL, "[url removed]"),
        (_LONG_IDENTIFIER, "[identifier removed]"),
    ):
        text = pattern.sub(replacement, text)
    return text


class _FixtureSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self._drop_depth:
            self._drop_depth += 1
            return
        if tag in _DROP_CONTENT_TAGS:
            self._drop_depth = 1
            return
        if tag in _DROP_ELEMENTS or tag in _UNWRAP_TAGS:
            return
        safe_attrs = [
            f' {name}="{escape(_sanitize_text(value or ""), quote=True)}"'
            for name, value in attrs
            if name.casefold() in _ALLOWED_ATTRIBUTES
        ]
        self.parts.append(f"<{tag}{''.join(safe_attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in _DROP_ELEMENTS | _DROP_CONTENT_TAGS:
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._drop_depth:
            self._drop_depth -= 1
            return
        if tag in _DROP_ELEMENTS | _UNWRAP_TAGS | _DROP_CONTENT_TAGS:
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._drop_depth:
            self.parts.append(escape(_sanitize_text(data)))


def sanitize_rate_fixture_html(html: str) -> str:
    """Retain parser DOM evidence while removing active content and sensitive values.

    This is a defense-in-depth helper, not permission to commit unreviewed DOM.
    A developer must manually inspect every generated fixture before committing it.
    """
    sanitizer = _FixtureSanitizer()
    sanitizer.feed(html)
    return "".join(sanitizer.parts)


def audit_rate_fixture_html(html: str) -> list[str]:
    """Return automatic privacy-audit findings; an empty result still needs human review."""
    findings: list[str] = []
    checks = {
        "active or form content": re.compile(
            r"(?i)<(?:script|style|form|input|button|select|textarea)\b"
        ),
        "email address": _EMAIL,
        "phone number": _PHONE,
        "Booking/reservation number": _BOOKING_NUMBER,
        "PIN": _PIN,
        "token/cookie/header": _TOKEN,
        "URL or mailto address": _URL,
        "long numeric identifier": _LONG_IDENTIFIER,
        "non-whitelisted attribute": re.compile(
            r"(?i)\s(?!class\s*=|data-testid\s*=)[a-z_:][-a-z0-9_:.]*\s*="
        ),
    }
    for label, pattern in checks.items():
        if pattern.search(html):
            findings.append(label)
    return findings
