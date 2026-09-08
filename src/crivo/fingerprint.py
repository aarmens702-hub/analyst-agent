"""Stable content fingerprints for DataFrames (A1 build plan T1.2).

The fix loop fingerprints a frame before a fix and compares after: an
unchanged frame is a counted failed attempt with no re-check, and later the
same digest scopes dataset memory keys. The digest covers cell values
(NaN-stable), column names and order, dtypes, row order, and the index: a
fix that only relabels the index still changed state, and a skipped
re-check must never hide a state change (integration call, 2026-09-04).

bench/truth.py pins bench frames as sha256 of to_csv() bytes. That technique
is not replicated here because CSV drops dtype: the string "1" and the int 1
serialize identically, and telling them apart is the whole job. This module
instead digests pd.util.hash_pandas_object row hashes, the primitive
verify.py and detect.py already trust, plus a schema line for names, order,
and dtypes. Its default hash_key is a fixed constant, so digests are at least
as stable as the CSV form: identical across processes and runs.

Cost is not part of that argument any more, and an earlier version of this
paragraph said it was. The saving is real only where the frame is not object:
a wide numeric frame digests two orders of magnitude faster than its CSV, but
the per-cell type pass below walks object cells in Python and spends the
saving back, so an all-object frame lands about where the CSV digest does
(8 object columns x 100k rows: 0.072s against 0.072s, this machine). Dtype
fidelity is what carries the choice; speed is a bonus that arrives only on
some shapes.

Row hashes alone do not beat CSV inside an object array: hash_pandas_object
falls back to astype(str) there, so "1" hashed like 1 and "True" like True.
A fix that parsed one cell of a half-repaired text column then looked like a
no-op, and the M1 re-check skip (loop.py) drops re-checks on a no-op, so a
real change went unchecked. Object arrays therefore get a second pass that
digests the exact type of every cell in row order.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

import numpy as np
import pandas as pd

# One shared name for every missing cell, so None, NaN and pd.NA keep hashing
# alike. Deliberate: pandas' own .equals() calls them equal and the detectors
# drop all three, so a swap between them is not a state change a check sees.
_MISSING = b"?missing\x00"


def frame_fingerprint(df: pd.DataFrame) -> str:
    """Hex sha256 of the frame's content. Equal digests mean equal content
    except in the two cases named at the bottom; a DIFFERENT digest means the
    frame's state moved, which is a wider net than inequality of content.

    The two are not the same net and this docstring used to claim they were.
    The loop's question is "did this fix change anything a later check could
    see", so the digest is deliberately finer than pandas' own .equals(),
    which is documented to ignore an axis whose values compare equal at
    another type. Two frames .equals() calls equal can fingerprint apart: an
    index of 0, 1 against one of 0.0, 1.0, and 0.0 against -0.0. Both are
    state changes here, on purpose. What must never happen is the reverse, a
    changed frame keeping its digest, because that is the case the re-check
    skip drops on the floor.

    Row hashes are fed to the digest in frame order (row reorder moves it)
    and forced little-endian so the digest never varies by platform. Every
    object array, in the columns and in the index, is then digested a second
    time by the exact type of each cell, because the row hashes see only
    str(cell) there.

    Two things it does not separate. Distinct values of the same type whose
    str() is equal, which for the builtins in a data frame means a custom
    class with a constant __str__. And object values held inside a category
    dtype, whose row hashes still flatten with str(); nothing in crivo builds
    a categorical, so that path is left alone rather than guessed at.
    """
    schema = repr([(repr(col), str(dtype)) for col, dtype in df.dtypes.items()])
    rows = pd.util.hash_pandas_object(df, index=True).to_numpy()
    digest = hashlib.sha256(schema.encode())
    digest.update(rows.astype("<u8").tobytes())
    for label, values in _object_arrays(df):
        digest.update(_type_digest(label, values))
    return digest.hexdigest()


def _object_arrays(df: pd.DataFrame) -> Iterator[tuple[str, pd.Series | pd.Index]]:
    """Every object array whose cells the row hashes flatten with str(),
    labelled by position so two columns of the same types stay distinct.

    A MultiIndex is itself one object array of tuples, so its levels are
    taken apart; hashing it whole would read every cell's type as `tuple`.
    Levels are materialized only when the level's own dtype is object.
    """
    index = df.index
    if isinstance(index, pd.MultiIndex):
        for level, uniques in enumerate(index.levels):
            if uniques.dtype == object:
                yield f"index[{level}]", index.get_level_values(level)
    elif index.dtype == object:
        yield "index", index
    for position, dtype in enumerate(df.dtypes):
        if dtype == object:
            yield f"column[{position}]", df.iloc[:, position]


def _type_digest(label: str, values: pd.Series | pd.Index) -> bytes:
    """The exact type of every cell of one object array, in row order, as a
    fixed 32 bytes so the caller can append it without framing it.

    The label is hashed in first, so a column's type run cannot be read as
    another column's. Type names are memoized because an object array holds
    a handful of distinct types over however many rows.
    """
    cells = values.to_numpy()
    # Series.isna gives a Series and Index.isna an ndarray; zip wants one shape.
    missing = np.asarray(values.isna())
    encoded: dict[type, bytes] = {}
    names = []
    for cell, absent in zip(cells, missing, strict=True):
        if absent:
            names.append(_MISSING)
            continue
        kind = type(cell)
        name = encoded.get(kind)
        if name is None:
            name = f"{kind.__module__}.{kind.__qualname__}\x00".encode()
            encoded[kind] = name
        names.append(name)
    digest = hashlib.sha256(label.encode())
    digest.update(b"".join(names))
    return digest.digest()


def unchanged(before_fp: str, after: pd.DataFrame) -> bool:
    """True when `after` still matches the pre-fix fingerprint `before_fp`,
    so the loop counts a failed attempt instead of re-running the check."""
    return frame_fingerprint(after) == before_fp
