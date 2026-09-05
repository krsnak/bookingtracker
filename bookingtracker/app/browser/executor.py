"""Single-thread ownership boundary for Playwright Sync API objects."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.browser.service import BookingBrowserService


class ThreadBoundBookingBrowser:
    """Keeps every synchronous Playwright call on one dedicated thread."""

    def __init__(self, service: BookingBrowserService) -> None:
        self._service = service
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="booking-browser")

    def _call(self, method: str, *args):  # noqa: ANN001, ANN201
        return self._executor.submit(getattr(self._service, method), *args).result()

    def start(self):  # noqa: ANN201
        return self._call("start")

    def stop(self):  # noqa: ANN201
        return self._call("stop")

    def open_manual_session(self):  # noqa: ANN201
        return self._call("open_manual_session")

    def navigate(self, url: str):  # noqa: ANN201
        return self._call("navigate", url)

    def health(self):  # noqa: ANN201
        return self._call("health")

    def status(self):  # noqa: ANN201
        return self._call("status")

    def current_page(self):  # noqa: ANN201
        return None

    def page_content(self) -> str | None:
        def content() -> str | None:
            page = self._service.current_page()
            return page.content() if page else None

        return self._executor.submit(content).result()

    def smoke_test(self):  # noqa: ANN201
        return self._call("smoke_test")

    def refresh_state(self):  # noqa: ANN201
        return self._call("refresh_state")

    def shutdown(self) -> None:
        self.stop()
        self._executor.shutdown(wait=True)
