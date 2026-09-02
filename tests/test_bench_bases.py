"""bench/bases.py must emit typed-canonical pristine frames: correctly dtyped,
deterministic per seed, RangeIndex, nothing corrupt.py hasn't touched yet —
everything downstream degrades FROM this state, never starts messier."""

import pandas as pd
import pytest

from bench.bases import transactions, typed_frame

_TYPED_SPEC = {
    "amount": "numeric",
    "count": "int",
    "posted_at": "datetime",
    "category": "category",
    "notes": "text",
    "key": "id",
    "lat": "lat",
    "lon": "lon",
}

_TYPED_DTYPES = {
    "amount": "numeric",
    "count": "numeric",
    "posted_at": "datetime",
    "category": "string",
    "notes": "string",
    "key": "string",
    "lat": "numeric",
    "lon": "numeric",
}

CASES = [
    pytest.param(
        lambda seed, n: transactions(seed, n=n),
        {
            "txn_id": "string",
            "posted_at": "datetime",
            "merchant": "string",
            "category": "string",
            "amount": "numeric",
            "currency": "string",
            "account_no": "string",
            "balance": "numeric",
            "notes": "string",
        },
        id="transactions",
    ),
    pytest.param(
        lambda seed, n: typed_frame(seed, n, _TYPED_SPEC),
        _TYPED_DTYPES,
        id="typed_frame",
    ),
]


def _assert_kind(series, kind):
    if kind == "numeric":
        assert pd.api.types.is_numeric_dtype(series)
    elif kind == "datetime":
        assert pd.api.types.is_datetime64_any_dtype(series)
    elif kind == "string":
        assert pd.api.types.is_string_dtype(series)


@pytest.mark.parametrize("build,dtypes", CASES)
def test_base_is_deterministic_and_typed_canonical(build, dtypes):
    n = 60
    first = build(42, n)
    second = build(42, n)
    assert first.equals(second), "same seed must give byte-identical frames"
    assert not first.equals(build(43, n)), "a different seed must differ"
    assert isinstance(first.index, pd.RangeIndex)
    assert len(first) == n
    assert set(first.columns) == set(dtypes)
    for col, kind in dtypes.items():
        _assert_kind(first[col], kind)
    if "txn_id" in first.columns:
        assert first["txn_id"].is_unique
        assert (first["amount"] > 0).all()
        assert first["merchant"].str.contains(r"[^\x00-\x7f]", regex=True).any(), (
            "merchant vocab must include an accented name for the mojibake disease"
        )
        assert (first["notes"] == "").any(), "notes must include some empty strings"
    if "key" in first.columns:
        assert first["key"].is_unique
        assert (first["amount"] > 0).all()
        assert first["lat"].between(44, 60).all()
        assert first["lon"].between(-140, -60).all()


def test_typed_frame_start_end_pairing_and_end_without_start_raises():
    spec = {"start": "start", "end": "end", "label": "id"}
    frame = typed_frame(7, 40, spec)
    assert (frame["end"] >= frame["start"]).all()
    assert pd.api.types.is_datetime64_any_dtype(frame["start"])
    assert pd.api.types.is_datetime64_any_dtype(frame["end"])
    assert frame["label"].is_unique

    with pytest.raises(ValueError, match="start"):
        typed_frame(7, 10, {"deadline": "end"})
