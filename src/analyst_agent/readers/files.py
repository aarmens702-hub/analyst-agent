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

# a trailing one of these means "the real format is the suffix before it"; pandas
# decompresses transparently (compression="infer") from the same path
_COMPRESSION = {".gz", ".zip", ".bz2", ".xz"}

SUPPORTED = (
    ".csv .tsv .txt .parquet .pq .xlsx .xls .json .jsonl .ndjson .feather .orc "
    "(text formats also with a .gz/.zip/.bz2/.xz suffix; parquet as a directory "
    "of parts)"
)


def read_file(path, **kwargs) -> pd.DataFrame:
    """Read a local file into a DataFrame, format inferred from the extension."""
    p = Path(path)
    # a partitioned parquet dataset is a directory of parts; read it whole
    # before any suffix logic (a directory name may still carry a dot)
    if p.is_dir():
        return pd.read_parquet(p, **kwargs)
    suffix = p.suffix.lower()
    if suffix in _COMPRESSION and len(p.suffixes) >= 2:
        inner = p.suffixes[-2].lower()
        if inner in {".csv", ".tsv", ".txt"}:
            # route through checkup.load so a compressed CSV gets the SAME
            # delimiter sniff, utf-8->cp1252 fallback, and keep_default_na as a
            # plain one (it decompresses the sample and infers the codec)
            return _checkup.load(p, **kwargs)
        if inner == ".json":
            return pd.read_json(p, **kwargs).astype(object)
        if inner in {".jsonl", ".ndjson"}:
            return pd.read_json(p, lines=True, **kwargs).astype(object)
    if suffix in _VIA_CHECKUP:
        return _checkup.load(p, **kwargs)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(p, keep_default_na=False, dtype=str, **kwargs)
    if suffix == ".json":
        return pd.read_json(p, **kwargs).astype(object)
    if suffix in {".jsonl", ".ndjson"}:
        return pd.read_json(p, lines=True, **kwargs).astype(object)
    if suffix == ".feather":
        return pd.read_feather(p, **kwargs)
    if suffix == ".orc":
        return pd.read_orc(p, **kwargs)
    raise ValueError(
        f"unsupported extension {suffix!r} for {p.name}; supported: {SUPPORTED}"
    )
