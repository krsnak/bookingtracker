"""Notification boundary with a local development implementation."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.alerts.models import Alert


class NotificationAdapter(Protocol):
    def deliver(self, alert: Alert) -> None: ...


class ConsoleNotificationAdapter:
    """Development-only adapter that deliberately exposes no reservation source text."""

    def deliver(self, alert: Alert) -> None:
        print(f"[{alert.severity.upper()}] {alert.title}: {alert.message}")


class HomeAssistantNotificationAdapter:
    """Generic HA notify-entity adapter; Telegram secrets remain in HA."""

    def __init__(self, entity_id: str | Callable[[], str | None], *, transport=None) -> None:
        self.entity_id = entity_id
        self.transport = transport or self._request

    def deliver(self, alert: Alert) -> None:
        self.send(alert.title, alert.message)

    def send(self, title: str, message: str) -> None:
        entity_id = self.entity_id() if callable(self.entity_id) else self.entity_id
        if not entity_id:
            raise RuntimeError("Home Assistant notify entity is not configured")
        if not entity_id.startswith("notify."):
            raise ValueError("Home Assistant notify entity must start with notify.")
        self.transport(
            "/services/notify/send_message",
            {
                "target": {"entity_id": entity_id},
                "data": {"title": title, "message": message},
            },
        )
    @staticmethod
    def _request(path: str, payload: dict[str, object]) -> None:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("Home Assistant Supervisor token is unavailable")
        request = Request(
            f"http://supervisor/core/api{path}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed Supervisor URL
                if response.status >= 300:
                    raise RuntimeError(f"Home Assistant notify failed with HTTP {response.status}")
        except (HTTPError, URLError) as error:
            detail = error.code if isinstance(error, HTTPError) else error.reason
            raise RuntimeError(f"Home Assistant notify request failed: {detail}") from error


def sanitize_notification_error(error: Exception) -> str:
    """Return a user-safe diagnostic; never surface transport internals or secrets."""
    _ = error
    return "Test notification failed. Check the configured notify entity and Home Assistant logs."
