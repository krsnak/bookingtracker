"""Verify one immutable add-on release version before publishing an image."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
CONFIG = (ROOT / "config.yaml").read_text()
PROJECT = (ROOT / "pyproject.toml").read_text()


def version(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ValueError("release version is missing")
    return match.group(1)


def main() -> int:
    config_version = version(CONFIG, r'^version: "([0-9]+\.[0-9]+\.[0-9]+)"$')
    project_version = version(PROJECT, r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$')
    release_version = os.environ.get("RELEASE_VERSION", config_version)
    if {config_version, project_version, release_version} != {config_version}:
        raise ValueError("config.yaml, pyproject.toml, and release tag must match")
    if "image: ghcr.io/krsnak/bookingtracker-addon" not in CONFIG:
        raise ValueError("config.yaml must reference the generic GHCR image")
    print(config_version)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"release verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
