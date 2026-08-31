"""`read_file` — the extended local reader: compression on the text formats,
parquet directories, feather, and orc, all keeping the sentinel-safe discipline
(missing tokens like "N/A" survive as strings for the detection engine)."""

import pandas as pd

from crivo.readers.files import read_file


def test_csv_gz_round_trips_and_preserves_sentinel(tmp_path) -> None:
    """A gzip-compressed csv reads, and "N/A" survives as a string (not NaN)."""
    path = tmp_path / "d.csv.gz"
    pd.DataFrame({"id": ["1", "2", "3"], "val": ["10", "N/A", "30"]}).to_csv(
        path, index=False, compression="gzip"
    )

    df = read_file(path)

    assert list(df.columns) == ["id", "val"]
    assert (df["val"].astype(str) == "N/A").sum() == 1


def test_csv_bz2_round_trips_and_preserves_sentinel(tmp_path) -> None:
    """A bz2-compressed csv reads, and the "N/A" sentinel survives as a string."""
    path = tmp_path / "d.csv.bz2"
    pd.DataFrame({"id": ["1", "2", "3"], "val": ["10", "N/A", "30"]}).to_csv(
        path, index=False, compression="bz2"
    )

    df = read_file(path)

    assert list(df.columns) == ["id", "val"]
    assert (df["val"].astype(str) == "N/A").sum() == 1


def test_orc_round_trips(tmp_path) -> None:
    """A .orc file reads back its rows and columns unchanged."""
    path = tmp_path / "d.orc"
    pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]}).to_orc(path)

    df = read_file(path)

    assert list(df.columns) == ["id", "val"]
    assert list(df["val"]) == ["a", "b", "c"]


def test_feather_round_trips(tmp_path) -> None:
    """A .feather file reads back its rows and columns unchanged."""
    path = tmp_path / "d.feather"
    pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]}).to_feather(path)

    df = read_file(path)

    assert list(df.columns) == ["id", "val"]
    assert list(df["val"]) == ["a", "b", "c"]


def test_parquet_directory_reads_all_parts(tmp_path) -> None:
    """A directory of parquet part files reads as one concatenated frame."""
    d = tmp_path / "parts"
    d.mkdir()
    df = pd.DataFrame({"id": [1, 2, 3, 4], "val": ["a", "b", "c", "d"]})
    df.iloc[:2].to_parquet(d / "part-0.parquet")
    df.iloc[2:].to_parquet(d / "part-1.parquet")

    out = read_file(d)

    assert len(out) == 4
    assert set(out["id"]) == {1, 2, 3, 4}


def test_json_gz_round_trips(tmp_path) -> None:
    """A gzip-compressed json reads back its rows (compression inferred)."""
    path = tmp_path / "d.json.gz"
    pd.DataFrame({"id": ["1", "2", "3"], "val": ["10", "N/A", "30"]}).to_json(
        path, compression="gzip"
    )

    df = read_file(path)

    assert list(df.columns) == ["id", "val"]
    assert (df["val"].astype(str) == "N/A").sum() == 1


def test_jsonl_gz_round_trips(tmp_path) -> None:
    """A gzip-compressed jsonl (line-delimited) reads back its rows."""
    path = tmp_path / "d.jsonl.gz"
    pd.DataFrame({"id": ["1", "2", "3"], "val": ["10", "N/A", "30"]}).to_json(
        path, orient="records", lines=True, compression="gzip"
    )

    df = read_file(path)

    assert list(df.columns) == ["id", "val"]
    assert (df["val"].astype(str) == "N/A").sum() == 1


def test_csv_zip_round_trips_and_preserves_sentinel(tmp_path) -> None:
    """A zip-compressed csv reads, and the "N/A" sentinel survives as a string."""
    path = tmp_path / "d.csv.zip"
    pd.DataFrame({"id": ["1", "2", "3"], "val": ["10", "N/A", "30"]}).to_csv(
        path, index=False, compression="zip"
    )

    df = read_file(path)

    assert list(df.columns) == ["id", "val"]
    assert (df["val"].astype(str) == "N/A").sum() == 1
