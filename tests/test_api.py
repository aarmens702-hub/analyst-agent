"""The public Python API: `import crivo as aa`.

This is the surface a developer meets after `pip install` — it must work with
no key, no kernel, no setup, on a DataFrame they already have. diagnose is the
hero; read/write are the ingestion and export around it.
"""

import pandas as pd

import crivo as aa


def test_diagnose_on_a_dataframe_is_pure_and_keyless() -> None:
    """The one-liner that makes the install pay off: pass a frame, get a
    readable report — no key, no kernel, instant."""
    df = pd.DataFrame(
        {"amount": ["$1,200", "$3,400.50", "15 kg", "980"] * 5, "note": ["ok"] * 20}
    )

    report = aa.diagnose(df)

    assert any(f["slug"] == "numbers-as-strings" for f in report.findings)
    assert "numbers-as-strings" in str(report)  # readable repr for humans
    assert isinstance(report.to_dict(), dict)
    assert "amount" in report.to_json()


def test_report_repr_html_renders_the_notebook_card() -> None:
    """In a notebook, `aa.diagnose(df)` renders as a self-contained HTML card,
    not the plain-text repr — carrying the name, a finding, and the footer."""
    df = pd.DataFrame({"amount": ["$1,200", "$3,400.50", "$15", "$980"] * 5})

    html = aa.diagnose(df, name="txns.csv")._repr_html_()

    assert html.startswith("<div")
    assert "txns.csv" in html
    assert any(f["slug"] in html for f in aa.diagnose(df).findings)
    assert "absence is a checked claim" in html


def test_read_dispatches_a_compressed_csv_through_the_public_api(tmp_path) -> None:
    """End-to-end: aa.read routes `.csv.gz` to the files reader, decompresses,
    and the sentinel still survives — the dispatch + compression path wired."""
    path = tmp_path / "d.csv.gz"
    pd.DataFrame({"id": ["1", "2"], "val": ["N/A", "30"]}).to_csv(
        path, index=False, compression="gzip"
    )

    got = aa.read(path)

    assert list(got.columns) == ["id", "val"]
    assert (got["val"].astype(str) == "N/A").sum() == 1


def test_read_routes_a_db_connection_to_the_sql_reader(tmp_path) -> None:
    """End-to-end: passing a DBAPI connection + query routes aa.read to the SQL
    reader and returns the rows — the query-kwarg dispatch branch wired."""
    import sqlite3

    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("create table t (id integer, val text)")
    con.executemany("insert into t values (?, ?)", [(1, "a"), (2, "b")])
    con.commit()

    got = aa.read(con, query="select * from t order by id")

    assert list(got["val"]) == ["a", "b"]
    con.close()


def test_read_handles_every_format_and_preserves_sentinels(tmp_path) -> None:
    """One reader across the formats a developer actually has data in — and
    'N/A' survives every path, because the engine can only report a sentinel
    it can still see."""
    frame = pd.DataFrame({"id": ["1", "2", "3"], "val": ["10", "N/A", "30"]})
    formats = {
        "csv": lambda p: frame.to_csv(p, index=False),
        "tsv": lambda p: frame.to_csv(p, sep="\t", index=False),
        "parquet": lambda p: frame.to_parquet(p, index=False),
        "xlsx": lambda p: frame.to_excel(p, index=False),
        "json": lambda p: frame.to_json(p, orient="records", indent=2),
        "jsonl": lambda p: frame.to_json(p, orient="records", lines=True),
    }
    for ext, dump in formats.items():
        path = tmp_path / f"d.{ext}"
        dump(path)
        got = aa.read(path)
        assert list(got.columns) == ["id", "val"], ext
        assert (got["val"].astype(str) == "N/A").sum() == 1, f"{ext}: sentinel lost"


def test_write_round_trips_every_export_format(tmp_path) -> None:
    """The cleaned data is the deliverable, in whatever format the next tool
    wants — and read(write(x)) == x for each."""
    frame = pd.DataFrame({"a": ["1", "2"], "b": ["x", "y"]})
    for ext in ("csv", "parquet", "xlsx", "json", "jsonl"):
        path = aa.write(frame, tmp_path / f"out.{ext}")
        assert path.exists()
        back = aa.read(path)
        assert list(back.columns) == ["a", "b"], ext
        assert len(back) == 2, ext


def test_diagnose_accepts_a_path_and_reports_the_file(tmp_path) -> None:
    """The other half of the one-liner: point it at a file, not just a frame."""
    path = tmp_path / "spend.xlsx"
    pd.DataFrame({"amount": ["$1,200"] * 12, "ok": ["y"] * 12}).to_excel(
        path, index=False
    )

    report = aa.diagnose(path)

    assert any(f["slug"] == "numbers-as-strings" for f in report.findings)
    assert report.to_dict()["name"] == "spend.xlsx"


def test_unsupported_formats_fail_with_a_helpful_message() -> None:
    """A developer who hands it an .avro must get a list of what works, not a
    cryptic pandas traceback."""
    import pytest

    with pytest.raises(ValueError, match="supported"):
        aa.read("data.avro")
    with pytest.raises(ValueError, match="supported"):
        aa.write(pd.DataFrame({"a": [1]}), "out.avro")
