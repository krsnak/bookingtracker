from __future__ import annotations

import signal
import subprocess
from pathlib import Path

import pytest
from app.browser.lease import ManualBrowserLease
from app.browser.models import RemoteDesktopState
from app.config import RemoteDesktopSettings
from app.integrations.home_assistant.remote_desktop import (
    RemoteDesktopError,
    RemoteDesktopRuntime,
)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


class FakeProcesses:
    def __init__(self, fail_on: int | None = None) -> None:
        self.fail_on = fail_on
        self.commands: list[list[str]] = []
        self.processes: dict[int, FakeProcess] = {}
        self.signals: list[tuple[int, signal.Signals]] = []

    def popen(self, command: list[str], **_: object) -> FakeProcess:
        self.commands.append(command)
        if self.fail_on == len(self.commands):
            raise OSError("unavailable")
        process = FakeProcess(len(self.commands))
        self.processes[process.pid] = process
        return process

    def run(self, command: list[str], **_: object) -> object:
        self.commands.append(command)
        if self.fail_on == len(self.commands):
            raise subprocess.CalledProcessError(1, command)
        return object()

    def kill_group(self, pid: int, sig: signal.Signals) -> None:
        self.signals.append((pid, sig))
        self.processes[pid].returncode = 0


def build_runtime(
    tmp_path: Path, fail_on: int | None = None
) -> tuple[RemoteDesktopRuntime, FakeProcesses, ManualBrowserLease]:
    assets = tmp_path / "novnc"
    (assets / "app").mkdir(parents=True)
    (assets / "core").mkdir()
    (assets / "vnc.html").write_text("sanitized noVNC")
    processes = FakeProcesses(fail_on)
    lease = ManualBrowserLease()
    runtime = RemoteDesktopRuntime(
        RemoteDesktopSettings(
            enabled=True,
            xauthority_path=tmp_path / "run" / "Xauthority",
            novnc_assets_dir=assets,
        ),
        lease,
        popen=processes.popen,
        run=processes.run,
        kill_group=processes.kill_group,
    )
    return runtime, processes, lease


def test_remote_runtime_orders_display_session_and_shutdown(tmp_path: Path) -> None:
    runtime, processes, lease = build_runtime(tmp_path)
    runtime.start_display()
    assert [command[0] for command in processes.commands[:3]] == ["xauth", "Xvfb", "openbox"]
    assert runtime.health().state is RemoteDesktopState.READY

    assert lease.acquire()
    runtime.start_session()
    assert [command[0] for command in processes.commands[3:]] == ["x11vnc", "websockify"]
    assert "127.0.0.1" in processes.commands[3]
    assert "127.0.0.1:6080" in processes.commands[4]
    assert "127.0.0.1:5900" in processes.commands[4]

    runtime.stop_all()
    assert [pid for pid, _ in processes.signals] == [5, 4, 3, 2]
    assert all(process.poll() is not None for process in processes.processes.values())
    assert not lease.active


@pytest.mark.parametrize("fail_on", [1, 2, 3, 4, 5])
def test_remote_runtime_rolls_back_each_start_layer(tmp_path: Path, fail_on: int) -> None:
    runtime, processes, lease = build_runtime(tmp_path, fail_on)
    if fail_on <= 3:
        with pytest.raises(RemoteDesktopError, match="remote display startup failed"):
            runtime.start_display()
    else:
        runtime.start_display()
        assert lease.acquire()
        with pytest.raises(RemoteDesktopError, match="remote session startup failed"):
            runtime.start_session()
        lease.release()
    runtime.stop_all()
    assert all(process.poll() is not None for process in processes.processes.values())


def test_remote_runtime_sanitizes_missing_assets(tmp_path: Path) -> None:
    lease = ManualBrowserLease()
    runtime = RemoteDesktopRuntime(
        RemoteDesktopSettings(
            enabled=True,
            xauthority_path=tmp_path / "Xauthority",
            novnc_assets_dir=tmp_path / "missing",
        ),
        lease,
    )
    with pytest.raises(RemoteDesktopError, match="remote display startup failed"):
        runtime.start_display()
    assert runtime.health().error == "remote display startup failed"
