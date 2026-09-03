"""Hostile-file hardening for the readers (arc W3, H4/H5): every failure mode
a CLEAR error naming the path and a one-line why — never a hang, never a bare
pandas/arrow traceback the user can't act on. Readable-but-weird succeeds
sentinel-safely; unreadable refuses loudly. All fixtures are tmp files; the
remote guards run against loopback servers only."""

import pandas as pd
import pytest

from crivo.readers.files import read_file


def test_empty_and_header_only_files(tmp_path):
    """A 0-byte file is unreadable and must say so clearly; a header-only CSV
    is a legitimate 0-row frame and must succeed."""
    header_only = tmp_path / "header_only.csv"
    header_only.write_text("id,amount,note\n")
    df = read_file(header_only)
    assert list(df.columns) == ["id", "amount", "note"]
    assert len(df) == 0

    for name in ("empty.csv", "empty.parquet", "empty.xlsx"):
        target = tmp_path / name
        target.write_bytes(b"")
        with pytest.raises((ValueError, OSError), match=name):
            read_file(target)


def test_truncated_binary_files_raise_clear_errors(tmp_path):
    """Bytes cut mid-file: binary formats (parquet, xlsx) must refuse with the
    file named, never a bare arrow/openpyxl traceback. A CSV cut mid-row is
    still text — pandas reads what is there, and that's acceptable."""
    whole = pd.DataFrame({"id": ["a", "b", "c"], "v": ["1", "2", "3"]})
    for suffix in (".parquet", ".xlsx"):
        intact = tmp_path / f"intact{suffix}"
        if suffix == ".parquet":
            whole.to_parquet(intact, index=False)
        else:
            whole.to_excel(intact, index=False)
        cut = tmp_path / f"truncated{suffix}"
        cut.write_bytes(intact.read_bytes()[: intact.stat().st_size // 2])
        with pytest.raises((ValueError, OSError), match=f"truncated{suffix}"):
            read_file(cut)


def test_bom_marked_csvs_read_transparently(tmp_path):
    """Excel exports lead with byte-order marks. utf-8-sig and utf-16 CSVs
    must read cleanly with the BOM consumed — a column literally named
    '\\ufeffid' is the failure this pins against."""
    text = "id,note\n1,ok\n2,N/A\n"
    sig = tmp_path / "sig.csv"
    sig.write_bytes(text.encode("utf-8-sig"))
    wide = tmp_path / "wide.csv"
    wide.write_bytes(text.encode("utf-16"))

    for target in (sig, wide):
        df = read_file(target)
        assert list(df.columns) == ["id", "note"], target.name
        assert df["note"].tolist() == ["ok", "N/A"], target.name


def test_cp1252_short_file_still_reads_with_stamped_encoding(tmp_path):
    """Regression net: this bug class shipped twice (short files with a real
    cp1252 byte died before any detector ran). A tiny file with a literal £
    must read via the fallback and stamp the switched encoding on the frame."""
    target = tmp_path / "spend.csv"
    target.write_bytes("dept,amount\ntreasury,£45\n".encode("cp1252"))
    df = read_file(target)
    assert df["amount"].tolist() == ["£45"]
    assert df.attrs.get("encoding") == "cp1252"


def test_malformed_json_raises_clear_errors(tmp_path):
    """JSON cut mid-record (a killed export, a partial download) must refuse
    with the file named — never a bare 'Unexpected character' stack."""
    cut_json = tmp_path / "cut.json"
    cut_json.write_text('[{"id": "1", "note": "ok"}, {"id": "2", "no')
    cut_jsonl = tmp_path / "cut.jsonl"
    cut_jsonl.write_text('{"id": "1", "note": "ok"}\n{"id": "2", "no\n')

    for target in (cut_json, cut_jsonl):
        with pytest.raises((ValueError, OSError), match=target.name):
            read_file(target)


def test_parquet_directory_with_a_corrupt_part_raises_clearly(tmp_path):
    """One rotten part in a partitioned dataset must not read as a silently
    smaller frame — it refuses, naming the directory."""
    dataset = tmp_path / "warehouse_dump"
    dataset.mkdir()
    pd.DataFrame({"id": ["a"], "v": ["1"]}).to_parquet(
        dataset / "part-0.parquet", index=False
    )
    good = dataset / "part-1.parquet"
    pd.DataFrame({"id": ["b"], "v": ["2"]}).to_parquet(good, index=False)
    good.write_bytes(good.read_bytes()[: good.stat().st_size // 2])

    with pytest.raises((ValueError, OSError), match="warehouse_dump"):
        read_file(dataset)


def test_zip_oddities_raise_clear_errors(tmp_path):
    """A .csv.zip holding two members (which one?) or no tabular member at
    all must refuse with the archive named — not a bare zipfile stack."""
    import zipfile

    two = tmp_path / "two.csv.zip"
    with zipfile.ZipFile(two, "w") as zf:
        zf.writestr("a.csv", "id\n1\n")
        zf.writestr("b.csv", "id\n2\n")
    junk = tmp_path / "junk.csv.zip"
    with zipfile.ZipFile(junk, "w") as zf:
        zf.writestr("readme.txt", "no data here")

    for target in (two, junk):
        with pytest.raises((ValueError, OSError), match=target.name):
            read_file(target)


def test_remote_read_times_out_with_the_limit_named(monkeypatch):
    """H5: a server that accepts and then hangs must fail within the
    configured budget, naming CRIVO_HTTP_TIMEOUT_S — never hang the
    notebook. The sleepy server answers after 2s; the budget is 0.3s."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from crivo.readers.remote import read_url

    class Sleepy(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(2)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.end_headers()
            self.wfile.write(b"id\n1\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sleepy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("CRIVO_HTTP_TIMEOUT_S", "0.3")
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="CRIVO_HTTP_TIMEOUT_S"):
            read_url(f"http://127.0.0.1:{server.server_address[1]}/slow.csv")
        assert time.monotonic() - started < 1.5, "must fail on the budget"
    finally:
        server.shutdown()
        server.server_close()


def test_decompression_bomb_is_refused_but_normal_gz_reads(tmp_path):
    """H5: a tiny archive expanding past 10MB at a bomb-grade ratio (>200:1)
    is refused with the ratio named; an ordinary compressed CSV still reads."""
    import gzip

    bomb = tmp_path / "bomb.csv.gz"
    with gzip.open(bomb, "wb") as fh:
        fh.write(b"0" * (20 * 1024 * 1024))  # 20MB of zeros -> ~20KB on disk
    with pytest.raises(ValueError, match="bomb.csv.gz"):
        read_file(bomb)

    normal = tmp_path / "normal.csv.gz"
    with gzip.open(normal, "wb") as fh:
        fh.write(b"id,note\n1,ok\n2,N/A\n")
    df = read_file(normal)
    assert df["note"].tolist() == ["ok", "N/A"]
