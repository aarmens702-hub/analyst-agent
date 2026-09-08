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


def test_bomb_guard_covers_bz2_and_xz_not_just_gz(tmp_path):
    """The guard is per-format or it is nothing: bz2 and xz declare no
    expanded size, so a bomb in either used to expand unbounded while the
    guard's docstring claimed otherwise. Both must now be refused with the
    archive named, and an ordinary compressed CSV in either format must still
    read sentinel-safely. The payload is valid CSV on purpose: before the fix
    these read all the way through, they did not fail on a bad parse.

    Neither format declares a size, so the number in the refusal comes from a
    probe that stopped at the ceiling: it is a lower bound and has to be
    printed as one, ratio included. It used to print "(6553:1)", which reads
    as a measurement of a file nothing measured."""
    import bz2
    import lzma

    payload = b"id,note\n" + b"1,ok\n" * (4 * 1024 * 1024)  # 20MB of real rows

    for opener, ext in ((bz2.open, ".bz2"), (lzma.open, ".xz")):
        bomb = tmp_path / f"bomb.csv{ext}"
        with opener(bomb, "wb") as fh:
            fh.write(payload)
        with pytest.raises(ValueError, match=f"bomb.csv{ext}") as caught:
            read_file(bomb)
        assert "at least" in str(caught.value).split("on disk (")[1], (
            f"the ratio is a bound, not a measurement: {caught.value}"
        )

        normal = tmp_path / f"normal.csv{ext}"
        with opener(normal, "wb") as fh:
            fh.write(b"id,note\n1,ok\n2,N/A\n")
        df = read_file(normal)
        assert df["note"].tolist() == ["ok", "N/A"], ext


def test_an_ordinary_boilerplate_heavy_csv_xz_is_not_a_bomb(tmp_path):
    """The 200:1 ratio was calibrated on gzip, and xz clears it on an honest
    export: 12MB of rows whose columns are mostly constant, the shape an ERP
    or CRM dump has, compresses about 355:1, so the free keyless diagnose
    refused a real file. bz2 and xz need their own ratio. The bomb payload in
    the test above stays refused, which is what keeps this from being a
    loosening: it reaches 6,553:1 in xz and 17,403:1 in bz2."""
    import lzma

    header = ",".join(["id", "region", "status"] + [f"c{i}" for i in range(20)])
    boilerplate = (
        "ACME_CORP_2026,USD,N/A,standard,,0,false,US,v3.1.4,unspecified,"
        "0.00,none,active,2026-01-01,system,,,,,"
    )  # the 20 columns that never vary, which is why xz clears 200:1
    export = tmp_path / "export.csv.xz"
    with lzma.open(export, "wb") as fh:
        fh.write((header + "\n").encode())
        fh.write(
            "".join(
                f"{i},North America,completed,{boilerplate}\n" for i in range(90_000)
            ).encode()
        )
    assert export.stat().st_size * 200 < 10 * 1024 * 1024, (
        "the fixture only tests the fix if the gzip ratio would have refused it"
    )

    df = read_file(export)

    assert len(df) == 90_000
    assert df["region"].tolist()[:1] == ["North America"]
    assert df["c2"].tolist()[:1] == ["N/A"], "sentinel-safe on the way through"


def test_a_gz_bomb_its_own_trailer_hides_is_still_refused(tmp_path):
    """gzip's ISIZE trailer is the container's word, and two ordinary files
    make it a lie with no forgery at all: concatenated members (what `cat
    a.gz b.gz` produces) declare only the last one, and four bytes of padding
    make it declare nothing. Both used to sail past the guard, so the guard
    now measures whenever the declaration does not already condemn."""
    import gzip

    half = tmp_path / "half.gz"
    with gzip.open(half, "wb") as fh:
        fh.write(b"0" * (8 * 1024 * 1024))
    concatenated = tmp_path / "concatenated.csv.gz"
    concatenated.write_bytes(half.read_bytes() * 2)  # 16MB, trailer says 8MB
    with pytest.raises(ValueError, match="concatenated.csv.gz"):
        read_file(concatenated)

    big = tmp_path / "big.gz"
    with gzip.open(big, "wb") as fh:
        fh.write(b"0" * (20 * 1024 * 1024))
    padded = tmp_path / "padded.csv.gz"
    padded.write_bytes(big.read_bytes() + b"\0\0\0\0")  # trailer now reads 0
    with pytest.raises(ValueError, match="padded.csv.gz"):
        read_file(padded)


def test_a_compression_suffix_with_no_inner_one_is_named_unreadable(tmp_path):
    """'nosuffix.bz2' is an extension crivo cannot read, not a bomb. Running
    the guard before the routing accused it of being one, which is both a
    false alarm and the wrong instruction to the user."""
    import bz2

    unreadable = tmp_path / "nosuffix.bz2"
    with bz2.open(unreadable, "wb") as fh:
        fh.write(b"0" * (20 * 1024 * 1024))  # a real archive, ~50 bytes on disk

    with pytest.raises(ValueError, match="unsupported extension"):
        read_file(unreadable)


def test_the_keyless_diagnose_path_refuses_a_bomb_too(tmp_path):
    """read_file was guarded and checkup.load was not, so `crivo diagnose` —
    the free, keyless report a stranger points at a file — expanded the whole
    bomb. ingest.load_url comes through the same door."""
    import bz2

    from crivo import checkup

    bomb = tmp_path / "diagnosed.csv.bz2"
    with bz2.open(bomb, "wb") as fh:
        fh.write(b"id,note\n" + b"1,ok\n" * (4 * 1024 * 1024))

    with pytest.raises(ValueError, match="decompression bomb"):
        checkup.load(bomb)
    with pytest.raises(ValueError, match="decompression bomb"):
        checkup.report(bomb)


def test_an_unguarded_compression_format_is_refused_not_read(tmp_path):
    """The guard is a closed contract: a compressed format with no size check
    is refused, so a suffix added to _COMPRESSION tomorrow cannot quietly
    reopen the unbounded path. The set is pinned here for the same reason."""
    from crivo.readers.files import _COMPRESSION, _bomb_check

    assert set(_COMPRESSION) == {".gz", ".zip", ".bz2", ".xz"}, (
        "a new compressed suffix needs a bomb guard and a case above"
    )

    unguarded = tmp_path / "payload.csv.zst"
    unguarded.write_bytes(b"not really zstd, the guard refuses before reading")
    with pytest.raises(ValueError, match="no decompression-bomb guard"):
        _bomb_check(unguarded, ".zst")


def test_a_bz2_far_past_any_honest_ratio_is_still_refused(tmp_path):
    """bz2 was lifted to 2000:1 on evidence gathered from xz. The two are not
    comparable: bz2 compresses in independent 900KB blocks, so its ratio is
    flat with file size, while xz's grows with the window. Measured on
    boilerplate-heavy CSV at 11MB, 45MB and 225MB, honest bz2 sits at 101:1
    and does not move; the packet's own fixture measured 122:1. Nothing
    honest was found anywhere near 2000, so the lift opened a band from a few
    hundred to 2000 that is neither honest nor previously allowed - and the
    ratio has no absolute cap behind it, so the same construction at 48MB
    compressed is 63GB expanded and still accepted.

    The file below expands 1300:1, which no measurement of an honest export
    has ever approached."""
    import bz2
    import os

    from crivo.readers.files import _bomb_check

    # tuned to land INSIDE the band the lift opened rather than far above it,
    # so this test fails on the ratio rather than on being an obvious bomb
    payload = b"0" * (64 * 1024 * 1024) + os.urandom(50_000)
    bomb = tmp_path / "bomb.csv.bz2"
    bomb.write_bytes(bz2.compress(payload))
    ratio = len(payload) // bomb.stat().st_size
    assert 1000 < ratio < 2000, f"the fixture has to sit in the opened band: {ratio}"

    with pytest.raises(ValueError, match="decompression bomb"):
        _bomb_check(bomb, ".bz2")
