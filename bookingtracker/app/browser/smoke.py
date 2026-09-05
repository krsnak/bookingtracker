"""Reusable container browser lifecycle acceptance check."""

from __future__ import annotations

from app.browser.service import BookingBrowserService


def run_browser_smoke(service: BookingBrowserService) -> int:
    health = service.start()
    if not health.context_running:
        print(f"browser start failed: {health.last_error}")
        service.stop()
        return 1
    result = service.smoke_test()
    stopped = service.stop()
    if not result["success"] or stopped.context_running:
        print("browser lifecycle smoke failed")
        return 1
    print("browser lifecycle smoke passed")
    return 0
