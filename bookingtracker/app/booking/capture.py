"""Sanitize a narrow availability subtree before a developer saves a fixture."""

from __future__ import annotations

import re

_SENSITIVE_ATTRIBUTE = re.compile(
    r"\s(?:data-[\w-]*(?:token|session|account)[\w-]*|(?:href|src))=(['\"]).*?\1",
    re.IGNORECASE,
)
_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_BOOKING_QUERY = re.compile(r"([?&](?:sid|token|label)=[^&\"']+)", re.IGNORECASE)


def sanitize_rate_fixture_html(html: str) -> str:
    """Remove script and likely identifying/session-bearing attribute values.

    This is a defense-in-depth helper, not permission to commit unreviewed DOM.
    A developer must manually inspect every generated fixture before committing it.
    """
    without_scripts = _SCRIPT.sub("", html)
    without_sensitive_attrs = _SENSITIVE_ATTRIBUTE.sub("", without_scripts)
    return _BOOKING_QUERY.sub("", without_sensitive_attrs)
