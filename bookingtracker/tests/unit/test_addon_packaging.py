from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_addon_build_context_is_self_contained() -> None:
    assert (ROOT / "config.yaml").is_file()
    assert (ROOT / "run.sh").is_file()
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "app").is_dir()
    assert (ROOT / "scripts" / "container_browser_smoke.py").is_file()
    run_script = (ROOT / "run.sh").read_text()
    assert run_script.startswith("#!/bin/sh\n")
    assert "set -eu" in run_script
    assert "pipefail" not in run_script
    assert "--reload" not in run_script
    assert "exec /opt/venv/bin/python -m uvicorn" in run_script
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "debian:12-slim" in dockerfile
    assert "apt-get install" in dockerfile and "chromium" in dockerfile
    copies = [
        line.split(maxsplit=2)[1]
        for line in (ROOT / "Dockerfile").read_text().splitlines()
        if line.startswith("COPY ")
    ]
    for source in copies:
        assert ".." not in source
        assert (ROOT / source.rstrip("/")).exists()
