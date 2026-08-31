"""Tests for the P0 dataset profiler: schema + stats + samples, never raw dumps."""

import re

import pandas as pd

from crivo.profile import profile_df

OMITTED_RE = re.compile(r"… (\d+) columns omitted \(char budget\)")


def _line_for(out: str, column: str) -> str:
    """Return the profile table row for a column name."""
    return next(line for line in out.splitlines() if line.startswith(f"| {column} |"))


def test_header_has_name_shape_and_memory() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    out = profile_df(df, "taxes")
    assert "taxes" in out
    assert "3 rows × 2 cols" in out
    assert re.search(r"\d+\.\d MB", out)


def test_every_column_name_appears() -> None:
    df = pd.DataFrame({"alpha": [1], "beta": [2.5], "gamma": ["g"]})
    out = profile_df(df, "small")
    for col in ("alpha", "beta", "gamma"):
        assert col in out
    for dtype in ("int64", "float64", "str"):
        assert dtype in out


def test_non_null_pct() -> None:
    df = pd.DataFrame({"v": [1.0, None, 3.0, None, 5.0]})
    assert "60.0%" in _line_for(profile_df(df, "d"), "v")


def test_sample_values_truncated_to_80_chars() -> None:
    df = pd.DataFrame({"long": ["x" * 500, "short"]})
    out = profile_df(df, "d")
    assert "x" * 500 not in out
    runs = [len(m) for m in re.findall(r"x+", out)]
    assert max(runs) <= 80  # each sample repr is hard-capped
    assert max(runs) >= 40  # but a truncated sample of the long value is present


def test_numeric_min_median_max() -> None:
    df = pd.DataFrame({"n": [10, 20, 30, 40, 50]})
    line = _line_for(profile_df(df, "d"), "n")
    assert "min=10" in line
    assert "median=30" in line
    assert "max=50" in line


def test_object_top_values_and_nunique() -> None:
    df = pd.DataFrame({"c": ["a", "a", "a", "b", "b", "c"]})
    line = _line_for(profile_df(df, "d"), "c")
    assert "'a'" in line
    assert "×3" in line  # top value_counts with counts
    assert "| 3 |" in line  # nunique cell


def test_empty_df_does_not_raise() -> None:
    assert "0 rows × 0 cols" in profile_df(pd.DataFrame(), "empty")
    assert "0 rows" in profile_df(pd.DataFrame(columns=["a", "b"]), "cols_only")
    assert "0 cols" in profile_df(pd.DataFrame(index=range(2)), "rows_only")


def test_all_nan_column() -> None:
    df = pd.DataFrame({"fnan": [float("nan")] * 4, "onan": [None] * 4})
    out = profile_df(df, "d")
    assert "0.0%" in _line_for(out, "fnan")
    assert "0.0%" in _line_for(out, "onan")


def test_duplicate_column_names() -> None:
    df = pd.DataFrame([[1, "a"], [2, "b"]], columns=["x", "x"])
    out = profile_df(df, "d")
    assert sum(line.startswith("| x |") for line in out.splitlines()) == 2


def test_wide_frame_respects_char_budget() -> None:
    df = pd.DataFrame({f"f_{i:03d}": range(5) for i in range(150)})
    for budget in (6000, 1500):
        out = profile_df(df, "wide", char_budget=budget)
        assert len(out) <= budget  # hard cap, never exceeded
        match = OMITTED_RE.search(out)
        assert match is not None  # omitted marker present
        kept = sum(line.startswith("| f_") for line in out.splitlines())
        assert kept > 0  # truncation happens at whole-row boundaries, not before
        assert kept + int(match.group(1)) == 150


def test_determinism_and_pathological_values() -> None:
    df = pd.DataFrame(
        {
            "mix": [1, "a", 3.5, None],
            "uni": ["café", "🚀🔥", "naïve", "ok"],
            "ts": pd.to_datetime(["2021-03-01", "2020-01-15", "2022-07-09", None]),
        }
    )
    first = profile_df(df, "d")
    assert profile_df(df, "d") == first  # byte-identical, no randomness
    assert "🚀" in first
    line = _line_for(first, "ts")
    assert "min=2020-01-15" in line
    assert "max=2022-07-09" in line


def test_no_raw_dump_on_large_frame() -> None:
    values = [f"common_{i % 3}" for i in range(10_000)]
    values[5_000] = "SENTINEL_ROW_5000_ONLY"
    df = pd.DataFrame({"cat": values, "num": range(10_000)})
    out = profile_df(df, "big")
    assert "SENTINEL_ROW_5000_ONLY" not in out  # only head samples + aggregates leak
    assert "10,000 rows" in out
