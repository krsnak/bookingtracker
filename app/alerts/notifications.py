"""Notification boundary with a local development implementation."""

from __future__ import annotations

from typing import Protocol

from app.alerts.models import Alert


class NotificationAdapter(Protocol):
    def deliver(self, alert: Alert) -> None: ...


class ConsoleNotificationAdapter:
    """Development-only adapter that deliberately exposes no reservation source text."""

    def deliver(self, alert: Alert) -> None:
        print(f"[{alert.severity.upper()}] {alert.title}: {alert.message}")
