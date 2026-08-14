"""Kernel-namespace snapshot cells (P5 R8), exec'd against a real namespace.

The failure this exists to prevent: `_restart_and_replay` replays only the
original load cells, so every verified fix applied since a load dies with the
kernel. A best-effort snapshot after each verified fix means a restart
restores the fixes, not just the raw file. Best-effort is the design: one
unpicklable or oversized variable must cost that variable, never the whole
snapshot.
"""

import contextlib
import io
import json

import pandas as pd

from analyst_agent import snapshot


def run_cell(code: str, namespace: dict) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<snap>", "exec"), namespace)  # noqa: S102
    return out.getvalue()


def test_a_snapshot_skips_the_unserialisable_and_keeps_the_rest(tmp_path):
    path = tmp_path / "state.pkl"
    namespace = {
        "df": pd.DataFrame({"a": [1, 2, 3]}),
        "note": "cleaned on tuesday",
        "broken": (i for i in range(3)),  # unpicklable state; costs only itself
        "_private": "never saved",
    }
    stdout = run_cell(snapshot.snapshot_cell(str(path)), dict(namespace))

    report = json.loads(stdout.strip().splitlines()[-1])
    assert sorted(report["saved"]) == ["df", "note"]
    assert "broken" in report["skipped"]
    assert "_private" not in report["saved"]
    assert path.exists()

    restored: dict = {}
    stdout = run_cell(snapshot.restore_cell(str(path)), restored)
    report = json.loads(stdout.strip().splitlines()[-1])
    assert sorted(report["restored"]) == ["df", "note"]
    assert restored["df"].equals(namespace["df"])
    assert restored["note"] == "cleaned on tuesday"
