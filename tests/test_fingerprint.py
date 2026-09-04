"""Tests for the re-check fingerprint (A1 build plan T1.2).

The fix loop refuses to re-run a check when a "fix" changed nothing, so the
fingerprint must move whenever anything a check can see moves: cell values,
column names and order, dtypes, row order. And it must hold still for
identical content, or the skip would fire at random.
"""

import time

import numpy as np
import pandas as pd

from crivo.fingerprint import frame_fingerprint, unchanged


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"a": [1, 2, 3], "b": [1.5, 2.5, 3.5], "c": ["x", "y", "z"]})


def test_identical_copies_fingerprint_equal() -> None:
    """Same content, different objects: the skip must not see a change."""
    fp = frame_fingerprint(_frame())

    assert fp == frame_fingerprint(_frame().copy(deep=True))
    assert len(fp) == 64
    int(fp, 16)  # a hex digest, storable as a memory key


def test_index_relabel_differs() -> None:
    """An index-only relabel is still a state change: the re-check skip must
    never hide it (integration call, 2026-09-04)."""
    base = _frame()
    relabeled = _frame()
    relabeled.index = [10, 20, 30]

    assert frame_fingerprint(base) != frame_fingerprint(relabeled)


def test_single_cell_change_differs() -> None:
    """One moved value is exactly the change a fix is supposed to make."""
    changed = _frame()
    changed.loc[1, "b"] = 99.0

    assert frame_fingerprint(_frame()) != frame_fingerprint(changed)


def test_dtype_only_change_differs() -> None:
    """int 1 and float 1.0 compare equal; a cast is still a real fix."""
    ints = pd.DataFrame({"a": [1, 2, 3]})
    floats = ints.astype("float64")
    assert (ints["a"] == floats["a"]).all()

    assert frame_fingerprint(ints) != frame_fingerprint(floats)


def test_column_rename_differs() -> None:
    assert frame_fingerprint(_frame()) != frame_fingerprint(
        _frame().rename(columns={"a": "amount"})
    )


def test_column_reorder_differs() -> None:
    assert frame_fingerprint(_frame()) != frame_fingerprint(_frame()[["b", "a", "c"]])


def test_row_reorder_differs() -> None:
    """Index reset on both sides so only the row ORDER separates them."""
    ordered = _frame().reset_index(drop=True)
    reversed_ = _frame().iloc[[2, 1, 0]].reset_index(drop=True)

    assert frame_fingerprint(ordered) != frame_fingerprint(reversed_)


def test_nan_frames_fingerprint_deterministically() -> None:
    """NaN != NaN in pandas; the digest must not inherit that."""
    withnan = pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": ["x", None, "z"]})

    first = frame_fingerprint(withnan)
    assert first == frame_fingerprint(withnan)
    assert first == frame_fingerprint(withnan.copy(deep=True))


def test_unchanged_spots_a_noop_fix() -> None:
    """The loop's actual question: did the fix touch anything at all?"""
    before = frame_fingerprint(_frame())
    noop = _frame()
    real = _frame()
    real.loc[0, "c"] = "fixed"

    assert unchanged(before, noop)
    assert not unchanged(before, real)


def test_100k_rows_fingerprint_well_under_a_second() -> None:
    """Loose perf sanity, not a benchmark: the skip runs after every fix."""
    n = 100_000
    big = pd.DataFrame(
        {
            "i": np.arange(n),
            "f": np.random.default_rng(0).normal(size=n),
            "s": [f"v{j % 97}" for j in range(n)],
        }
    )

    start = time.perf_counter()
    frame_fingerprint(big)
    assert time.perf_counter() - start < 1.0
