from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
REPOSITORY_ROOT = ROOT.parent


def test_addon_build_context_is_self_contained() -> None:
    assert (ROOT / "config.yaml").is_file()
    assert (ROOT / "run.sh").is_file()
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "app").is_dir()
    assert (ROOT / "app" / "browser" / "session.py").is_file()
    assert (ROOT / "scripts" / "container_browser_smoke.py").is_file()
    run_script = (ROOT / "run.sh").read_text()
    assert run_script.startswith("#!/bin/sh\n")
    assert "set -eu" in run_script
    assert "pipefail" not in run_script
    assert "--reload" not in run_script
    assert "exec /opt/venv/bin/python -m uvicorn" in run_script
    assert "BOOKINGTRACKER_BROWSER_HEADLESS=false" in run_script
    assert "BOOKINGTRACKER_REMOTE_DESKTOP_ENABLED=true" in run_script
    assert "BOOKINGTRACKER_BROWSER_DISPLAY=:99" in run_script
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "debian:12-slim" in dockerfile
    assert "apt-get install" in dockerfile and "chromium" in dockerfile
    for package in ("xvfb", "openbox", "x11vnc", "novnc", "websockify", "xauth"):
        assert package in dockerfile
    assert "test -f /usr/share/novnc/vnc.html" in dockerfile
    assert "test -d /usr/share/novnc/app" in dockerfile
    assert "test -d /usr/share/novnc/core" in dockerfile
    assert 'version: "0.4.2"' in (ROOT / "config.yaml").read_text()
    assert "image: ghcr.io/krsnak/bookingtracker-addon" in (ROOT / "config.yaml").read_text()
    assert "arch: [aarch64]" in (ROOT / "config.yaml").read_text()
    assert "homeassistant_api: true" in (ROOT / "config.yaml").read_text()
    assert 'version = "0.4.2"' in (ROOT / "pyproject.toml").read_text()
    assert "ports: {}" in (ROOT / "config.yaml").read_text()
    copies = [
        line.split(maxsplit=2)[1]
        for line in (ROOT / "Dockerfile").read_text().splitlines()
        if line.startswith("COPY ")
    ]
    for source in copies:
        assert ".." not in source
        assert (ROOT / source.rstrip("/")).exists()


def test_prebuilt_release_configuration_is_consistent_and_secret_free() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "publish-addon.yml").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text()
    assert "linux/arm64" in workflow
    assert "ghcr.io/krsnak/bookingtracker-addon:$VERSION" in workflow
    assert "contents: read" in workflow
    assert "packages: write" in workflow
    assert "secrets.GITHUB_TOKEN" in workflow
    assert "pytest" in workflow and "ruff check ." in workflow
    assert "verify_release.py" in workflow
    assert "SUPERVISOR_TOKEN" not in workflow
    assert "TELEGRAM" not in workflow
    assert "booking_profile" not in workflow and ".db" not in workflow
    assert "io.hass.version=$BUILD_VERSION" in dockerfile
    assert "io.hass.arch=$BUILD_ARCH" in dockerfile
    for ignored in ("data/", "logs/", ".env", "*.db", "*Cookies*", "*Login Data*"):
        assert ignored in dockerignore
    result = subprocess.run(
        [sys.executable, "scripts/verify_release.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    mismatch = subprocess.run(
        [sys.executable, "scripts/verify_release.py"],
        cwd=ROOT,
        env=os.environ | {"RELEASE_VERSION": "0.0.0"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert mismatch.returncode == 1


def test_production_entrypoint_imports_from_self_contained_tree() -> None:
    environment = os.environ | {"PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", "import app.web.main; import app.browser.session"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
