"""Typed-canonical pristine frames (spec R2): the clean starting point every
injector in corrupt.py degrades. RangeIndex, no defects, deterministic per
seed — corrupt.py is the only place a disease gets planted.

Pandas 3.x infers its own StringDtype for text built from python str, which
*is* the typed-canonical form here (corrupt.py's diseases are what degrade a
column down to plain object dtype — that degradation is part of what several
diseases plant, not something these bases should pre-empt).
"""

import numpy as np
import pandas as pd

MERCHANTS = [
    "Acme Corp",
    "Globex",
    "Initech",
    "Umbrella Corp",
    "Stark Industries",
    "Café Málaga",  # accented — mojibake (disease 8) needs non-ascii to chew on
    "Müller GmbH",
]
CATEGORIES = ["groceries", "travel", "fuel", "dining", "utilities"]
CURRENCIES = ["CAD", "USD", "EUR", "GBP"]
NOTES = ["", "", "", "reversed", "see ticket", "pending review"]

_CATEGORY_VOCAB = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
_TEXT_VOCAB = ["café résumé", "naïve élan", "über cool", "plain phrase", "hello world"]


def _dates(rng: np.random.Generator, n: int) -> pd.Series:
    start = pd.Timestamp("2023-01-01")
    offsets = rng.integers(0, 700, n)
    return pd.Series(start + pd.to_timedelta(offsets, unit="D"))


def transactions(seed: int, n: int = 500) -> pd.DataFrame:
    """Clean transaction ledger — the domain scripts/make_transactions.py
    plants defects into; here every column is pristine and correctly typed."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "txn_id": [f"TX{i:06d}" for i in range(n)],
            "posted_at": _dates(rng, n),
            "merchant": rng.choice(MERCHANTS, n),
            "category": rng.choice(CATEGORIES, n),
            "amount": rng.uniform(1.0, 5000.0, n).round(2),
            "currency": rng.choice(CURRENCIES, n),
            "account_no": rng.integers(1000, 9999, n).astype(str),
            "balance": rng.uniform(0.0, 90000.0, n).round(2),
            "notes": rng.choice(NOTES, n),
        }
    )


def _typed_column(kind: str, name: str, n: int, rng: np.random.Generator):
    if kind == "numeric":
        return rng.uniform(1.0, 1000.0, n).round(2)
    if kind == "int":
        return rng.integers(0, 1000, n).astype("int64")
    if kind in ("datetime", "start"):
        return _dates(rng, n)
    if kind == "category":
        return rng.choice(_CATEGORY_VOCAB, n)
    if kind == "text":
        return rng.choice(_TEXT_VOCAB, n)
    if kind == "flag":
        # exactly two clean spellings — the pristine form boolean-chaos
        # (disease 23) degrades into mixed Y/N/1/0/TRUE representations
        return rng.choice(["yes", "no"], n)
    if kind == "id":
        return [f"{name[:3].upper()}{i:06d}" for i in range(n)]
    if kind == "lat":
        return rng.uniform(44.0, 60.0, n).round(6)
    if kind == "lon":
        return rng.uniform(-140.0, -60.0, n).round(6)
    raise ValueError(f"typed_frame: unknown kind {kind!r} for column {name!r}")


def typed_frame(seed: int, n: int, spec: dict[str, str]) -> pd.DataFrame:
    """Build a frame from a column-name -> kind spec. Kinds: numeric, int,
    datetime, category, text, flag (two-valued yes/no), id, lat, lon,
    start/end (paired, end >= start).

    "end" is built in a second pass so its position in `spec` doesn't matter —
    it always resolves against whichever column declared kind "start".
    """
    if "end" in spec.values() and "start" not in spec.values():
        raise ValueError("typed_frame: 'end' column needs a paired 'start' column")
    rng = np.random.default_rng(seed)
    start_col = next((c for c, k in spec.items() if k == "start"), None)
    columns: dict[str, object] = {}
    for name, kind in spec.items():
        if kind == "end":
            continue
        columns[name] = _typed_column(kind, name, n, rng)
    for name, kind in spec.items():
        if kind != "end":
            continue
        span = rng.integers(1, 60, n)
        columns[name] = pd.Series(columns[start_col]) + pd.to_timedelta(span, unit="D")
    return pd.DataFrame(columns)
