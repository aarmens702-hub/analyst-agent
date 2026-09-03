"""P2-polish wave: error clarity, passthrough parity, and the preamble
sniffer — the gaps a first-session user actually hits (think-fork audit,
2026-09-03). Every error here must name the fix, not just the failure."""

import pytest

import crivo


def test_file_like_objects_get_a_clear_error_not_the_sql_branch():
    """A BytesIO (Flask upload, S3 body) used to fall through the dispatcher
    into the SQL reader and die with "read_sql needs a `query`" — a lie about
    what went wrong. It must be told what read() accepts instead."""
    import io

    with pytest.raises(TypeError, match="path, URL, or DB connection"):
        crivo.read(io.BytesIO(b"a,b\n1,2\n"))


def test_sqlite_file_paths_open_query_and_close_themselves(tmp_path):
    """cv.read("app.db", query=...) is the most common local-database ask;
    forcing the user to open the connection was pure ceremony. Without a
    query, the error must teach — by listing the tables it found."""
    import sqlite3

    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.execute("create table txns (id integer, amount real)")
    conn.execute("insert into txns values (1, 12.5), (2, 99.0)")
    conn.commit()
    conn.close()

    frame = crivo.read(db, query="select * from txns order by id")
    assert frame.shape == (2, 2)
    assert float(frame["amount"].iloc[1]) == 99.0

    with pytest.raises(ValueError, match="txns"):
        crivo.read(str(db))


def test_excel_cover_sheets_raise_with_names_and_xls_names_its_dependency(tmp_path):
    """A workbook whose first sheet is an 'Instructions' cover used to be
    silently diagnosed as the data. The guard lists the sheet names at the
    moment of failure; an explicit sheet_name= bypasses it. And .xls is
    advertised but needs xlrd we don't ship — the ImportError must say so."""
    import pandas as pd

    book = tmp_path / "report.xlsx"
    with pd.ExcelWriter(book) as writer:
        pd.DataFrame({"note": ["see next sheet"]}).to_excel(
            writer, sheet_name="Instructions", index=False
        )
        pd.DataFrame({"id": [1, 2], "amount": [3.5, 4.5]}).to_excel(
            writer, sheet_name="Data", index=False
        )

    with pytest.raises(ValueError, match="Instructions.*Data|Data.*Instructions"):
        crivo.read(book)
    frame = crivo.read(book, sheet_name="Data")
    assert list(frame.columns) == ["id", "amount"]

    fake_xls = tmp_path / "old.xls"
    fake_xls.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)  # OLE2 magic
    with pytest.raises((ImportError, ValueError), match="xlrd"):
        crivo.read(fake_xls)


def test_write_round_trips_feather_and_orc(tmp_path):
    """We could read feather/orc but not write them — asymmetry for no
    reason. Round-trip through crivo.write -> crivo.read, index dropped like
    every other format."""
    import pandas as pd

    frame = pd.DataFrame({"id": ["a", "b", "c"], "v": [1.5, 2.5, 3.5]})
    for name in ("out.feather", "out.orc"):
        path = crivo.write(frame, tmp_path / name)
        back = crivo.read(path)
        assert list(back.columns) == ["id", "v"], name
        assert len(back) == 3, name


def test_format_override_and_stat_package_dispatch(tmp_path):
    """format= overrides extension dispatch — fixed-width files have no
    reliable extension, so fwf is its first citizen; unknown values must
    list what's known. .dta auto-dispatches (pandas round-trips it); a junk
    .sas7bdat proves the sas route by failing with the file named."""
    import pandas as pd

    fwf = tmp_path / "positions.txt"
    fwf.write_text("id  amount\na    12.5\nb    99.0\n")
    frame = crivo.read(fwf, format="fwf")
    assert list(frame.columns) == ["id", "amount"]
    assert len(frame) == 2

    with pytest.raises(ValueError, match="fwf"):
        crivo.read(fwf, format="parquetish")

    dta = tmp_path / "survey.dta"
    pd.DataFrame({"score": [1.0, 2.0]}).to_stata(dta, write_index=False)
    back = crivo.read(dta)
    assert list(back.columns) == ["score"]

    junk_sas = tmp_path / "export.sas7bdat"
    junk_sas.write_bytes(b"\x00" * 64)
    with pytest.raises(ValueError, match="export.sas7bdat"):
        crivo.read(junk_sas)


_BANK_EXPORT = (
    "Account: 1234-5678\n"
    "Export date: 2026-09-01\n"
    "\n"
    "txn_id,posted_at,amount\n"
    "T001,2026-08-01,12.50\n"
    "T002,2026-08-02,9.99\n"
    "T003,2026-08-03,45.00\n"
    "T004,2026-08-04,3.75\n"
)


def test_bank_export_preambles_are_skipped_and_stamped(tmp_path):
    """THE first-session gap (think-fork audit): bank/ERP exports open with
    metadata lines before the real header, and the metadata used to become
    the header (or a tokenize error). The sniffer engages only on failure,
    finds where the delimiter count stabilizes, and stamps what it skipped —
    through BOTH entry points, which share one load path."""
    f = tmp_path / "export.csv"
    f.write_text(_BANK_EXPORT)

    frame = crivo.read(f)
    assert list(frame.columns) == ["txn_id", "posted_at", "amount"]
    assert len(frame) == 4
    assert frame.attrs["preamble_rows"] == 3

    report = crivo.diagnose(f)
    assert report.to_dict()["columns"] == 3


def test_comma_padded_preambles_and_explicit_pins(tmp_path):
    """Excel-exported CSVs pad their metadata lines with the full delimiter
    count ("Report,,,"), so the parse SUCCEEDS into an Unnamed: flood. The
    sniffer prefers the first stable line whose fields are all filled — the
    header — over mere count stability. And an explicit skiprows=/header=
    pin means the sniffer never runs: the user's layout wins, flood and all."""
    padded = (
        "Report,,,\n"
        "Generated,2026-09-01,,\n"
        ",,,\n"
        "txn_id,posted_at,amount,merchant\n"
        "T001,2026-08-01,12.50,Acme\n"
        "T002,2026-08-02,9.99,Globex\n"
        "T003,2026-08-03,45.00,Initech\n"
    )
    f = tmp_path / "padded.csv"
    f.write_text(padded)

    frame = crivo.read(f)
    assert list(frame.columns) == ["txn_id", "posted_at", "amount", "merchant"]
    assert frame.attrs["preamble_rows"] == 3

    pinned = crivo.read(f, skiprows=0)
    assert any(str(c).startswith("Unnamed:") for c in pinned.columns)


def test_all_junk_files_refuse_clearly_instead_of_returning_garbage(tmp_path):
    """A file that is metadata all the way down — Unnamed-flood header, no
    consistent table anywhere below — must refuse with the first lines shown,
    never hand back a garbage frame. A genuine single-column file stays
    readable: one column is legal, flood is not."""
    junk = tmp_path / "junk.csv"
    junk.write_text("Report,,,,\nNotes,to,self\ntotals below,\n9,9\nend\n")
    with pytest.raises(ValueError, match="no consistent table"):
        crivo.read(junk)

    single = tmp_path / "single.csv"
    single.write_text("note\nalpha\nbeta\ngamma\n")
    frame = crivo.read(single)
    assert list(frame.columns) == ["note"]
    assert len(frame) == 3
