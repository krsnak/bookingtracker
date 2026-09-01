"""Runtime paths and browser settings, with no platform-specific callers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    logs_dir: Path

    @classmethod
    def from_environment(cls) -> AppPaths:
        project_root = Path(__file__).resolve().parent.parent
        data_dir = Path(os.environ.get("BOOKINGTRACKER_DATA_DIR", project_root / "data"))
        logs_dir = Path(os.environ.get("BOOKINGTRACKER_LOGS_DIR", project_root / "logs"))
        return cls(data_dir=data_dir, logs_dir=logs_dir)

    @property
    def booking_profile_dir(self) -> Path:
        return self.data_dir / "booking_profile"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "bookingtracker.db"


@dataclass(frozen=True)
class BrowserSettings:
    profile_dir: Path
    channel: str | None = "chrome"
    headless: bool = False
    navigation_timeout_ms: int = 60_000
    availability_timeout_ms: int = 10_000
    executable_path: Path | None = None
    launch_args: tuple[str, ...] = ()
    launch_environment: tuple[tuple[str, str], ...] = ()

    @classmethod
    def development(cls, paths: AppPaths | None = None) -> BrowserSettings:
        resolved_paths = paths or AppPaths.from_environment()
        channel = os.environ.get("BOOKINGTRACKER_BROWSER_CHANNEL", "chrome") or None
        headless = os.environ.get("BOOKINGTRACKER_BROWSER_HEADLESS", "false").lower() == "true"
        environment = dict(os.environ)
        if display := os.environ.get("BOOKINGTRACKER_BROWSER_DISPLAY"):
            environment["DISPLAY"] = display
        if xauthority := os.environ.get("BOOKINGTRACKER_XAUTHORITY"):
            environment["XAUTHORITY"] = xauthority
        return cls(
            profile_dir=resolved_paths.booking_profile_dir,
            channel=channel,
            headless=headless,
            executable_path=Path(os.environ["BOOKINGTRACKER_BROWSER_EXECUTABLE"])
            if os.environ.get("BOOKINGTRACKER_BROWSER_EXECUTABLE")
            else None,
            launch_args=tuple(
                item
                for item in os.environ.get("BOOKINGTRACKER_BROWSER_ARGS", "").split(",")
                if item
            ),
            launch_environment=tuple(environment.items()),
        )


@dataclass(frozen=True)
class RemoteDesktopSettings:
    """HA-only deployment settings for the manually opened remote display."""

    enabled: bool = False
    display: str = ":99"
    width: int = 1280
    height: int = 720
    xauthority_path: Path = Path("/run/bookingtracker/Xauthority")
    novnc_assets_dir: Path = Path("/usr/share/novnc")
    vnc_host: str = "127.0.0.1"
    vnc_port: int = 5900
    websockify_port: int = 6080

    @classmethod
    def from_environment(cls) -> RemoteDesktopSettings:
        return cls(
            enabled=os.environ.get("BOOKINGTRACKER_REMOTE_DESKTOP_ENABLED", "false").lower()
            == "true",
            display=os.environ.get("BOOKINGTRACKER_BROWSER_DISPLAY", ":99"),
            xauthority_path=Path(
                os.environ.get("BOOKINGTRACKER_XAUTHORITY", "/run/bookingtracker/Xauthority")
            ),
            novnc_assets_dir=Path(
                os.environ.get("BOOKINGTRACKER_NOVNC_ASSETS_DIR", "/usr/share/novnc")
            ),
        )
