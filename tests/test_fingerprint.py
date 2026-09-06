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


def _mixed() -> pd.DataFrame:
    """A dirty text column mid-repair: some cells parsed, some still text."""
    return pd.DataFrame({"x": pd.Series(["1", 2, "x"], dtype=object)})


def test_object_cell_string_and_int_differ() -> None:
    """The confirmed collision. hash_pandas_object falls back to astype(str)
    on a mixed object array, so the string "1" and the int 1 hashed alike and
    a fix that parsed exactly one cell read as a no-op: the M1 re-check skip
    would then drop a real change on the floor."""
    before = _mixed()
    after = _mixed()
    after.loc[0, "x"] = 1

    assert before.loc[0, "x"] != after.loc[0, "x"]  # "1" is not 1
    assert frame_fingerprint(before) != frame_fingerprint(after)
    assert not unchanged(frame_fingerprint(before), after)


def test_object_cell_string_and_bool_differ() -> None:
    """Same fallback, the boolean form: "True" and True both str() to "True"."""
    before = pd.DataFrame({"x": pd.Series(["True", False, "x"], dtype=object)})
    after = before.copy()
    after.loc[0, "x"] = True

    assert frame_fingerprint(before) != frame_fingerprint(after)


def test_object_cells_of_equal_type_still_fingerprint_equal() -> None:
    """The other half of the contract: mixing types in must not make the
    digest wobble on frames that really are identical, or the skip fires at
    random and every fix pays a re-check."""
    first = frame_fingerprint(_mixed())

    assert first == frame_fingerprint(_mixed())
    assert first == frame_fingerprint(_mixed().copy(deep=True))
    assert unchanged(first, _mixed())


def test_object_index_string_and_int_differ() -> None:
    """The index is content too (test_index_relabel_differs), and an object
    index flattens with str() exactly like an object column."""
    labels = pd.DataFrame({"v": [1, 2]}, index=pd.Index(["1", "y"], dtype=object))
    parsed = pd.DataFrame({"v": [1, 2]}, index=pd.Index([1, "y"], dtype=object))

    assert frame_fingerprint(labels) != frame_fingerprint(parsed)


def test_object_multiindex_level_string_and_int_differ() -> None:
    """A MultiIndex is one object array of tuples, so its levels have to be
    taken apart or every row's cell type reads as `tuple`."""
    labels = pd.DataFrame(
        {"v": [1, 2]}, index=pd.MultiIndex.from_tuples([("1", "a"), ("2", "b")])
    )
    parsed = pd.DataFrame(
        {"v": [1, 2]}, index=pd.MultiIndex.from_tuples([(1, "a"), ("2", "b")])
    )

    assert frame_fingerprint(labels) != frame_fingerprint(parsed)


def test_missing_sentinels_stay_interchangeable() -> None:
    """Deliberate non-guarantee, do not "fix" it: pandas' own .equals() calls
    None, NaN and pd.NA equal and every detector drops all three, so a fix
    that only swapped one sentinel for another changed nothing a check can
    see and must keep reading as a no-op."""
    none = pd.DataFrame({"x": pd.Series([None, "a"], dtype=object)})
    nan = pd.DataFrame({"x": pd.Series([np.nan, "a"], dtype=object)})
    na = pd.DataFrame({"x": pd.Series([pd.NA, "a"], dtype=object)})
    assert none.equals(nan)

    assert frame_fingerprint(none) == frame_fingerprint(nan)
    assert frame_fingerprint(none) == frame_fingerprint(na)


def test_100k_object_rows_fingerprint_well_under_a_second() -> None:
    """The type pass walks object cells in Python, so it gets its own perf
    sanity: pandas 3 gives plain text a `str` dtype, and the frame above
    never reaches the object path."""
    n = 100_000
    big = pd.DataFrame(
        {
            "mixed": pd.Series(
                [j if j % 2 else f"v{j % 97}" for j in range(n)], dtype=object
            ),
            "f": np.random.default_rng(0).normal(size=n),
        }
    )
    assert big.dtypes.iloc[0] == object

    start = time.perf_counter()
    frame_fingerprint(big)
    assert time.perf_counter() - start < 1.0
