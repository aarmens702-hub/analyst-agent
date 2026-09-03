"""Arc W3/H7 (prime-agent backlog §2): the snapshot cells, exercised exactly
as the kernel runs them — exec'd plain Python over a globals() namespace —
so caps, manifest, and round-trip are proven without a kernel process.

Deliberate divergence from the backlog's 16MiB/var: our kernel's main state
IS the dataset, so the per-variable cap stays 64MiB and the new safety is the
AGGREGATE cap — ten big frames must not write an unbounded state file."""

import json


def _run(code: str, namespace: dict) -> None:
    # exec IS the point: this is exactly how the kernel runs these cells
    exec(compile(code, "<kernel-cell>", "exec"), namespace)  # noqa: S102


def test_snapshot_enforces_aggregate_cap_and_writes_a_manifest(tmp_path):
    from crivo import snapshot

    dest = tmp_path / "state.pkl"
    namespace = {
        "__name__": "kernel",
        "a": b"x" * 2_000_000,
        "b": b"y" * 2_000_000,
        "c": b"z" * 2_000_000,  # would push the aggregate past the cap
        "keep": 7,
    }
    code = snapshot.snapshot_cell(
        str(dest), cap_bytes=8_000_000, aggregate_cap_bytes=5_000_000
    )
    _run(code, namespace)

    manifest = json.loads((tmp_path / "state.pkl.manifest.json").read_text())
    assert manifest["serializer"] in {"pickle", "dill"}
    assert "a" in manifest["saved"] and "b" in manifest["saved"]
    assert "keep" in manifest["saved"]
    assert any("aggregate" in reason for reason in manifest["skipped"].values())
    assert manifest["sizes"]["a"] > 1_000_000
    assert dest.exists()
    assert not dest.with_suffix(".tmp").exists(), "atomic write leaves no tmp"


def test_snapshot_round_trips_into_a_fresh_kernel_namespace(tmp_path):
    """The point of the whole file: a kernel dies after verified fixes, a new
    one restores them. DataFrames included — the state that actually matters."""
    import pandas as pd

    from crivo import snapshot

    dest = tmp_path / "state.pkl"
    old = {
        "__name__": "kernel",
        "df": pd.DataFrame({"a": [1.5, 2.5], "b": ["x", "y"]}),
        "threshold": 0.75,
        "note": "post-fix",
    }
    _run(snapshot.snapshot_cell(str(dest)), old)

    fresh = {"__name__": "kernel"}
    _run(snapshot.restore_cell(str(dest)), fresh)
    assert fresh["threshold"] == 0.75
    assert fresh["note"] == "post-fix"
    pd.testing.assert_frame_equal(fresh["df"], old["df"])
