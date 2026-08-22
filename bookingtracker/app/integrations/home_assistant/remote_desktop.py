"""Lifecycle-owned Xvfb/noVNC adapter for Home Assistant Ingress recovery."""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from time import monotonic, sleep
from typing import Protocol

from app.browser.lease import ManualBrowserLease
from app.browser.models import RemoteDesktopHealth, RemoteDesktopState
from app.config import RemoteDesktopSettings


class Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


PopenFactory = Callable[..., Process]
RunFactory = Callable[..., object]
KillGroup = Callable[[int, signal.Signals], None]


class RemoteDesktopError(RuntimeError):
    """Safe deployment failure; underlying command output is never exposed."""


class RemoteDesktopRuntime:
    """Owns only display/VNC child processes, never Chromium or its profile."""

    def __init__(
        self,
        settings: RemoteDesktopSettings,
        lease: ManualBrowserLease,
        *,
        popen: PopenFactory = subprocess.Popen,
        run: RunFactory = subprocess.run,
        kill_group: KillGroup = os.killpg,
    ) -> None:
        self.settings = settings
        self.lease = lease
        self._popen = popen
        self._run = run
        self._kill_group = kill_group
        self._xvfb: Process | None = None
        self._openbox: Process | None = None
        self._x11vnc: Process | None = None
        self._websockify: Process | None = None
        self._state = RemoteDesktopState.DISABLED
        if settings.enabled:
            self._state = RemoteDesktopState.STARTING_DISPLAY
        self._error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def novnc_assets_dir(self) -> Path:
        return self.settings.novnc_assets_dir

    @property
    def session_active(self) -> bool:
        return self._alive(self._x11vnc) and self._alive(self._websockify) and self.lease.active

    def start_display(self) -> RemoteDesktopHealth:
        if not self.enabled:
            return self.health()
        if self._alive(self._xvfb) and self._alive(self._openbox):
            return self.health()
        self._state = RemoteDesktopState.STARTING_DISPLAY
        self._error = None
        try:
            if not (self.novnc_assets_dir / "vnc.html").is_file():
                raise RemoteDesktopError("noVNC assets are unavailable")
            self.settings.xauthority_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings.xauthority_path.unlink(missing_ok=True)
            self._run(
                [
                    "xauth",
                    "-f",
                    str(self.settings.xauthority_path),
                    "add",
                    self.settings.display,
                    ".",
                    secrets.token_hex(16),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._xvfb = self._spawn(
                [
                    "Xvfb",
                    self.settings.display,
                    "-screen",
                    "0",
                    f"{self.settings.width}x{self.settings.height}x24",
                    "-nolisten",
                    "tcp",
                    "-auth",
                    str(self.settings.xauthority_path),
                ],
                environment={},
            )
            self._wait_alive(self._xvfb, "Xvfb")
            self._openbox = self._spawn(
                ["openbox", "--sm-disable"], environment=self._x_environment
            )
            self._wait_alive(self._openbox, "Openbox")
            self._state = RemoteDesktopState.READY
        except (OSError, subprocess.SubprocessError, RemoteDesktopError) as error:
            self._set_error("remote display startup failed")
            self._stop_display()
            raise RemoteDesktopError(self._error) from error
        return self.health()

    def start_session(self) -> RemoteDesktopHealth:
        if not self.enabled:
            raise RemoteDesktopError("remote desktop is disabled")
        if not self._alive(self._xvfb) or not self._alive(self._openbox):
            raise RemoteDesktopError("remote display is not ready")
        if self.session_active:
            return self.health()
        self._error = None
        try:
            self._x11vnc = self._spawn(
                [
                    "x11vnc",
                    "-display",
                    self.settings.display,
                    "-auth",
                    str(self.settings.xauthority_path),
                    "-listen",
                    self.settings.vnc_host,
                    "-rfbport",
                    str(self.settings.vnc_port),
                    "-localhost",
                    "-forever",
                    "-shared",
                    "-nopw",
                ],
                environment=self._x_environment,
            )
            self._wait_alive(self._x11vnc, "x11vnc")
            self._websockify = self._spawn(
                [
                    "websockify",
                    f"{self.settings.vnc_host}:{self.settings.websockify_port}",
                    f"{self.settings.vnc_host}:{self.settings.vnc_port}",
                ],
                environment={},
            )
            self._wait_alive(self._websockify, "websockify")
            self._state = RemoteDesktopState.SESSION_ACTIVE
        except (OSError, RemoteDesktopError) as error:
            self._set_error("remote session startup failed")
            self._stop_process("websockify")
            self._stop_process("x11vnc")
            raise RemoteDesktopError(self._error) from error
        return self.health()

    def stop_session(self) -> RemoteDesktopHealth:
        if not self.enabled:
            return self.health()
        self._state = RemoteDesktopState.STOPPING
        self._stop_process("websockify")
        self._stop_process("x11vnc")
        if self._alive(self._xvfb) and self._alive(self._openbox):
            self._state = RemoteDesktopState.READY
        else:
            self._state = RemoteDesktopState.ERROR
        return self.health()

    def stop_all(self) -> None:
        if not self.enabled:
            return
        self.stop_session()
        self._stop_display()
        self._state = RemoteDesktopState.DISABLED
        self.lease.release()

    def health(self) -> RemoteDesktopHealth:
        return RemoteDesktopHealth(
            state=self._state,
            display_running=self._alive(self._xvfb),
            window_manager_running=self._alive(self._openbox),
            vnc_running=self._alive(self._x11vnc),
            websockify_running=self._alive(self._websockify),
            manual_lease_active=self.lease.active,
            error=self._error,
        )

    @property
    def _x_environment(self) -> dict[str, str]:
        return {
            "DISPLAY": self.settings.display,
            "XAUTHORITY": str(self.settings.xauthority_path),
        }

    def _spawn(self, command: list[str], *, environment: dict[str, str]) -> Process:
        return self._popen(
            command,
            env=os.environ | environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    @staticmethod
    def _alive(process: Process | None) -> bool:
        return process is not None and process.poll() is None

    def _wait_alive(self, process: Process, layer: str) -> None:
        deadline = monotonic() + 0.2
        while monotonic() < deadline:
            if process.poll() is not None:
                raise RemoteDesktopError(f"{layer} exited")
            sleep(0.01)
        if process.poll() is not None:
            raise RemoteDesktopError(f"{layer} exited")

    def _stop_display(self) -> None:
        self._stop_process("openbox")
        self._stop_process("xvfb")
        with suppress(OSError):
            self.settings.xauthority_path.unlink(missing_ok=True)

    def _stop_process(self, name: str) -> None:
        attribute = f"_{name}"
        process = getattr(self, attribute)
        if process is None:
            return
        try:
            if self._alive(process):
                self._kill_group(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._kill_group(process.pid, signal.SIGKILL)
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=2)
        finally:
            setattr(self, attribute, None)

    def _set_error(self, message: str) -> None:
        self._state = RemoteDesktopState.ERROR
        self._error = message
