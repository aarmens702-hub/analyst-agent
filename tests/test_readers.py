"""The `readers` package: `aa.read` dispatches a source — a path, a database, a
URL — to the right reader, keeping the sentinel-safe discipline throughout."""

import pandas as pd


def test_readers_read_a_local_csv_keeps_sentinels(tmp_path) -> None:
    """The baseline: a local path still reads, and 'N/A' survives as a string
    the detection engine can see (not silently coerced to NaN)."""
    from analyst_agent import readers

    path = tmp_path / "d.csv"
    pd.DataFrame({"id": ["1", "2", "3"], "val": ["10", "N/A", "30"]}).to_csv(
        path, index=False
    )

    df = readers.read(path)

    assert list(df.columns) == ["id", "val"]
    assert (df["val"].astype(str) == "N/A").sum() == 1
