from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


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
    assert 'version: "0.2.3"' in (ROOT / "config.yaml").read_text()
    assert 'version = "0.2.3"' in (ROOT / "pyproject.toml").read_text()
    assert "ports: {}" in (ROOT / "config.yaml").read_text()
    copies = [
        line.split(maxsplit=2)[1]
        for line in (ROOT / "Dockerfile").read_text().splitlines()
        if line.startswith("COPY ")
    ]
    for source in copies:
        assert ".." not in source
        assert (ROOT / source.rstrip("/")).exists()


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
