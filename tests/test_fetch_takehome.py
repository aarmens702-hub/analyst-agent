"""Tests for the take-home dataset fetcher's one piece of real logic.

The Canada consolidated contracts file is 641MB; the fetcher must stream it
and keep a prefix rather than downloading the whole thing onto a laptop that
cannot hold it. The network calls themselves are not tested — the truncation
that makes them safe is.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetch_takehome import take_prefix


def test_the_prefix_sample_keeps_the_header_plus_n_rows() -> None:
    stream = io.BytesIO(b"h1,h2\n" + b"".join(f"r{i},x\n".encode() for i in range(100)))

    out = take_prefix(stream, rows=10)

    lines = out.decode().splitlines()
    assert lines[0] == "h1,h2"
    assert len(lines) == 11, "header plus exactly ten data rows"
    assert lines[-1] == "r9,x"


def test_a_short_file_is_kept_whole_without_hanging() -> None:
    stream = io.BytesIO(b"h1,h2\nonly,row\n")

    out = take_prefix(stream, rows=20_000)

    assert out == b"h1,h2\nonly,row\n"
