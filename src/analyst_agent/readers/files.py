"""Local-file readers — dispatch on extension, sentinel-safe throughout.

Moved out of `api.read` so the ingestion surface can grow (compression, parquet
directories, feather/orc) without one giant function. Missing-value tokens
("N/A", "-") are preserved as strings, never coerced to NaN, because the
detection engine can only report a sentinel it can still see.
"""

from pathlib import Path

import pandas as pd

from analyst_agent import checkup as _checkup

# the csv-family goes through checkup.load for the delimiter sniff, the
# utf-8 -> cp1252 fallback, and keep_default_na=False; parquet too (a single file)
_VIA_CHECKUP = {".csv", ".tsv", ".txt", ".parquet", ".pq"}

SUPPORTED = ".csv .tsv .txt .parquet .pq .xlsx .xls .json .jsonl .ndjson"


def read_file(path, **kwargs) -> pd.DataFrame:
    """Read a local file into a DataFrame, format inferred from the extension."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in _VIA_CHECKUP:
        return _checkup.load(p)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(p, keep_default_na=False, dtype=str, **kwargs)
    if suffix == ".json":
        return pd.read_json(p, **kwargs).astype(object)
    if suffix in {".jsonl", ".ndjson"}:
        return pd.read_json(p, lines=True, **kwargs).astype(object)
    raise ValueError(
        f"unsupported extension {suffix!r} for {p.name}; supported: {SUPPORTED}"
    )
