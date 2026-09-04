"""Stable content fingerprints for DataFrames (A1 build plan T1.2).

The fix loop fingerprints a frame before a fix and compares after: an
unchanged frame is a counted failed attempt with no re-check, and later the
same digest scopes dataset memory keys. The digest covers cell values
(NaN-stable), column names and order, dtypes, row order, and the index: a
fix that only relabels the index still changed state, and a skipped
re-check must never hide a state change (integration call, 2026-09-04).

bench/truth.py pins bench frames as sha256 of to_csv() bytes. That technique
is not replicated here because CSV drops dtype (the string "1" and the int 1
serialize identically) and pays serialization cost at width. This module
instead digests pd.util.hash_pandas_object row hashes, the primitive
verify.py and detect.py already trust, plus a schema line for names, order,
and dtypes. Its default hash_key is a fixed constant, so digests are at least
as stable as the CSV form: identical across processes and runs.
"""

from __future__ import annotations

import hashlib

import pandas as pd


def frame_fingerprint(df: pd.DataFrame) -> str:
    """Hex sha256 of the frame's content; equal digests mean equal content.

    Row hashes are fed to the digest in frame order (row reorder moves it)
    and forced little-endian so the digest never varies by platform.
    """
    schema = repr([(repr(col), str(dtype)) for col, dtype in df.dtypes.items()])
    rows = pd.util.hash_pandas_object(df, index=True).to_numpy()
    digest = hashlib.sha256(schema.encode())
    digest.update(rows.astype("<u8").tobytes())
    return digest.hexdigest()


def unchanged(before_fp: str, after: pd.DataFrame) -> bool:
    """True when `after` still matches the pre-fix fingerprint `before_fp`,
    so the loop counts a failed attempt instead of re-running the check."""
    return frame_fingerprint(after) == before_fp
