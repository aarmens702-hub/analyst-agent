"""Shared pytest config: the `docker` marker auto-skips when no daemon is up."""

import shutil
import subprocess

import pytest


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "docker: needs a running Docker daemon")


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if _docker_available():
        return
    skip = pytest.mark.skip(reason="docker daemon unavailable")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)
