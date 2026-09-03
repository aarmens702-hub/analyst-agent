"""Shared pytest config: the `docker` marker auto-skips when no daemon is up,
and provider credentials are quarantined so tests never see the shell's keys."""

import shutil
import subprocess

import pytest

from crivo.llm import KEY_ENV_VARS
from crivo.readers.remote import READER_ENV_VARS


@pytest.fixture(autouse=True)
def _quarantine_provider_env(monkeypatch):
    """Deletes every crivo-tunable env var before each test.

    A developer's real key (or a local CRIVO_* tuning) in the shell must never
    make a test pass that would fail clean in CI. Composed from each module's
    own registry — single source of truth per module, one quarantine. Tests
    that need a var set it explicitly with monkeypatch."""
    for var in KEY_ENV_VARS + READER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


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
