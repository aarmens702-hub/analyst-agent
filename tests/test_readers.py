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


def test_short_cp1252_file_falls_back_to_cp1252(tmp_path) -> None:
    """A short file whose only non-utf-8 byte lands in the last 3 bytes must
    still fall back to cp1252 — the 8KB-boundary forgiveness only applies when
    the sample was actually truncated, not to a genuine short-file bad byte."""
    from analyst_agent import checkup

    path = tmp_path / "short.csv"
    # £ (0xA3 in cp1252) three bytes from the end
    path.write_bytes("name,price\nrow,£5\n".encode("cp1252"))

    df = checkup.load(path)

    assert list(df.columns) == ["name", "price"]
    assert df.attrs.get("encoding") == "cp1252"


def test_remote_bad_records_path_errors_clearly(monkeypatch) -> None:
    """A wrong records_path must name itself, not surface a bare KeyError the
    caller can't tie back to the argument they passed."""
    import pytest

    from analyst_agent.readers import remote

    monkeypatch.setattr(
        remote,
        "_fetch",
        lambda url: ('{"data": {"items": [{"a": 1}]}}', "application/json"),
    )

    with pytest.raises(ValueError, match="records_path") as excinfo:
        remote.read_url("http://x/d.json", records_path="data.MISSING")
    assert "MISSING" in str(excinfo.value)


def test_remote_http_error_is_not_blamed_on_the_sandbox(monkeypatch) -> None:
    """A real 404/500 (the server answered) must not be reported as an
    unreachable-sandbox failure — HTTPError is a URLError subclass, so it has to
    be distinguished."""
    import urllib.error
    import urllib.request

    import pytest

    from analyst_agent.readers import remote

    def _raise(*_a, **_k):
        raise urllib.error.HTTPError("http://x/d.json", 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise)

    with pytest.raises(RuntimeError, match="404"):
        remote.read_url("http://x/d.json")


def test_cloud_schemes_fail_clearly_not_as_a_mangled_local_path() -> None:
    """s3://gs://az:// are deferred — the user must get a clear message, not a
    FileNotFoundError from the file reader treating 's3://…' as a local path."""
    import pytest

    from analyst_agent import readers

    for uri in ("s3://bucket/key.csv", "gs://bucket/key.csv", "az://c/key.csv"):
        with pytest.raises(NotImplementedError, match="cloud"):
            readers.read(uri)


def test_compressed_csv_gets_the_same_sniff_as_a_plain_csv(tmp_path) -> None:
    """A semicolon .csv.gz must read like a semicolon .csv — the compressed path
    must not bypass checkup.load's delimiter sniff and collapse 3 columns to 1."""
    import gzip

    from analyst_agent.readers import files

    path = tmp_path / "semi.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("id;amount;note\n1;1000;ok\n2;2000;N/A\n")

    df = files.read_file(path)

    assert list(df.columns) == ["id", "amount", "note"]
    assert (df["note"].astype(str) == "N/A").sum() == 1
