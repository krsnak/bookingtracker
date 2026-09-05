from __future__ import annotations

from dataclasses import replace

from app.browser.models import AuthenticationState, BrowserHealth, BrowserState
from app.browser.smoke import run_browser_smoke

READY = BrowserHealth(
    state=BrowserState.READY,
    process_running=True,
    context_running=True,
    page_available=True,
    booking_auth_state=AuthenticationState.UNKNOWN,
    manual_action_required=False,
    last_successful_navigation=None,
    last_error=None,
)


class FakeService:
    def __init__(self, *, starts: bool = True, smoke_succeeds: bool = True) -> None:
        self.starts = starts
        self.smoke_succeeds = smoke_succeeds
        self.stopped = False

    def start(self) -> BrowserHealth:
        if self.starts:
            return READY
        return replace(
            READY,
            state=BrowserState.ERROR,
            process_running=False,
            context_running=False,
            page_available=False,
            last_error="safe fixture failure",
        )

    def smoke_test(self) -> dict[str, bool]:
        return {"success": self.smoke_succeeds}

    def stop(self) -> BrowserHealth:
        self.stopped = True
        return replace(
            READY,
            state=BrowserState.STOPPED,
            process_running=False,
            context_running=False,
            page_available=False,
        )


def test_container_smoke_uses_isolated_browser_smoke_and_stops_context() -> None:
    service = FakeService()

    assert run_browser_smoke(service) == 0  # type: ignore[arg-type]
    assert service.stopped


def test_container_smoke_fails_for_start_or_smoke_failure() -> None:
    failed_start = FakeService(starts=False)
    failed_smoke = FakeService(smoke_succeeds=False)

    assert run_browser_smoke(failed_start) == 1  # type: ignore[arg-type]
    assert failed_start.stopped
    assert run_browser_smoke(failed_smoke) == 1  # type: ignore[arg-type]
    assert failed_smoke.stopped
