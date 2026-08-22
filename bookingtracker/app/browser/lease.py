"""A small explicit lease that protects a user-controlled browser session."""

from __future__ import annotations

from threading import Lock


class ManualBrowserLease:
    """Blocks automated navigation while the user controls the single browser."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active = False

    def acquire(self) -> bool:
        with self._lock:
            if self._active:
                return False
            self._active = True
            return True

    def release(self) -> None:
        with self._lock:
            self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active
