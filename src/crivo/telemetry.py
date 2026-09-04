"""JSONL run telemetry on the OTel GenAI attribute names (A0 R4).

No SDK, no network, no framework: one append-only JSONL file whose rows use
the OpenTelemetry GenAI semantic-convention attribute names, so they stay
exportable to any OTel backend later. Off unless CRIVO_TELEMETRY names a
path; a disabled call does nothing; a failing write never takes a run down.
Import-safe on keyless installs.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime


def _path() -> str:
    return os.environ.get("CRIVO_TELEMETRY") or ""


def enabled() -> bool:
    return bool(_path())


def emit(name: str, dur_s: float | None = None, **attrs) -> None:
    path = _path()
    if not path:
        return
    row: dict = {
        "t": datetime.now(tz=UTC).isoformat(timespec="milliseconds"),
        "name": name,
        "attrs": attrs,
    }
    if dur_s is not None:
        row["dur_s"] = round(dur_s, 4)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass  # telemetry must never take a run down


@contextmanager
def span(name: str, **attrs):
    """Time a block and emit one row on exit. Yields a dict; anything the
    block adds to it lands in the row's attrs (late fields like usage)."""
    t0 = time.monotonic()
    extra: dict = {}
    try:
        yield extra
    finally:
        emit(name, dur_s=time.monotonic() - t0, **{**attrs, **extra})
