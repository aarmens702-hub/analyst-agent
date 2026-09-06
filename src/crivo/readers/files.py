"""Local-file readers — dispatch on extension, sentinel-safe throughout.

Moved out of `api.read` so the ingestion surface can grow (compression, parquet
directories, feather/orc) without one giant function. Missing-value tokens
("N/A", "-") are preserved as strings, never coerced to NaN, because the
detection engine can only report a sentinel it can still see.
"""

from pathlib import Path

import pandas as pd

from crivo import checkup as _checkup

# the csv-family goes through checkup.load for the delimiter sniff, the
# utf-8 -> cp1252 fallback, and keep_default_na=False; parquet too (a single file)
_VIA_CHECKUP = {".csv", ".tsv", ".txt", ".parquet", ".pq"}

# a trailing one of these means "the real format is the suffix before it"; pandas
# decompresses transparently (compression="infer") from the same path
_COMPRESSION = {".gz", ".zip", ".bz2", ".xz"}

SUPPORTED = (
    ".csv .tsv .txt .parquet .pq .xlsx .xls .json .jsonl .ndjson .feather .orc "
    ".dta .sas7bdat (text formats also with a .gz/.zip/.bz2/.xz suffix; parquet "
    "as a directory of parts; format='fwf' for fixed-width)"
)


_BOMB_RATIO = 200
_BOMB_FLOOR = 10 * 1024 * 1024
_BOMB_PROBE_CHUNK = 1024 * 1024


def _declared_size(p: Path, suffix: str) -> int | None:
    """What the container says it expands to, or None when it says nothing.

    gzip's ISIZE trailer and zip's central directory are metadata, so this
    costs four bytes and a directory read. bz2 and xz declare nothing. It is
    only ever the container's own word: `cat a.gz b.gz` is an ordinary file
    that declares its last member alone, four bytes of padding change the
    answer, and a trailer can simply be edited. So a declaration is trusted to
    CONDEMN a file and never to clear one."""
    if suffix == ".gz":
        import struct

        with open(p, "rb") as fh:
            fh.seek(-4, 2)
            return struct.unpack("<I", fh.read(4))[0]
    if suffix == ".zip":
        import zipfile

        with zipfile.ZipFile(p) as zf:
            return sum(info.file_size for info in zf.infolist())
    return None


def _probe_size(p: Path, suffix: str, ceiling: int) -> int:
    """Measure the expansion by doing it: decompress into a counter, drop the
    bytes, and stop at the first chunk past `ceiling`.

    Bounded work and one chunk of memory at a time (the decompressor's own
    dictionary is on top of that), nothing kept, and the count is a floor
    rather than the true size once it stops early. An archive that stays under
    the ceiling — every legitimate one — is decompressed in full here and
    again by the reader, so the guard costs a second pass over the real data,
    scaling with the expanded size and not with the ceiling. That is the price
    of measuring instead of believing a header.

    A format with no probe here is refused rather than read, so adding a
    suffix to _COMPRESSION cannot quietly reopen an unguarded path."""
    if suffix == ".zip":
        import zipfile

        expanded = 0
        with zipfile.ZipFile(p) as zf:
            for info in zf.infolist():
                with zf.open(info) as fh:
                    while expanded <= ceiling:
                        chunk = fh.read(_BOMB_PROBE_CHUNK)
                        if not chunk:
                            break
                        expanded += len(chunk)
                if expanded > ceiling:
                    break
        return expanded
    if suffix == ".gz":
        import gzip

        opener = gzip.open
    elif suffix == ".bz2":
        import bz2

        opener = bz2.open
    elif suffix == ".xz":
        import lzma

        opener = lzma.open
    else:
        raise ValueError(
            f"{p.name}: refusing to decompress {suffix} because there is no "
            "decompression-bomb guard for that format"
        )
    expanded = 0
    with opener(p, "rb") as fh:
        while expanded <= ceiling:
            chunk = fh.read(_BOMB_PROBE_CHUNK)
            if not chunk:
                break
            expanded += len(chunk)
    return expanded


def _bomb_check(p: Path, suffix: str) -> None:
    """Refuse decompression bombs before the expansion can hurt, for every
    compressed format the reader accepts.

    A bomb clears both the 10MB floor and the 200:1 ratio, so big legitimate
    archives pass and a 20KB file promising 20MB of zeros does not. A declared
    size (gz, zip) is used only when it already condemns the file, which costs
    metadata alone; otherwise the expansion is MEASURED by _probe_size,
    because concatenation, trailing padding and an edited trailer each make a
    declaration a lie, and the first two are ordinary files rather than
    forgeries.

    The ratio was calibrated on gzip. bz2 and xz compress repeated categorical
    data an order of magnitude harder, so a legitimate archive in those
    formats reaches the ceiling sooner than a gzip of the same bytes would.
    Retuning that is a threshold decision with data behind it, not a fix."""
    compressed = p.stat().st_size
    if not compressed:
        return
    # the two conditions in one number: above this, expanded is past the floor
    # AND past the ratio, so the probe can stop as soon as it is exceeded
    ceiling = max(_BOMB_FLOOR, _BOMB_RATIO * compressed)
    expanded = _declared_size(p, suffix)
    if expanded is None or expanded <= ceiling:
        expanded = _probe_size(p, suffix, ceiling)
    if expanded > ceiling:
        raise ValueError(
            f"{p.name}: refusing to decompress — at least {expanded:,} bytes "
            f"from {compressed:,} on disk ({expanded // compressed}:1) looks "
            "like a decompression bomb"
        )


def guard_compressed(p: Path) -> None:
    """Run the bomb guard when this path is a compressed format, else nothing.

    The entry point other modules use, so every door that opens a file holds
    the same guard: checkup.load is reached by `crivo diagnose` and by
    ingest.load_url with no other check between a stranger's bytes and pandas.
    """
    suffix = p.suffix.lower()
    if suffix in _COMPRESSION:
        _bomb_check(p, suffix)


def read_file(path, **kwargs) -> pd.DataFrame:
    """Read a local file into a DataFrame, format inferred from the extension.

    Every failure is a CLEAR error naming the file and a one-line why (the
    original exception rides along as __cause__) — never a bare pandas/arrow
    traceback the user can't act on. A missing file keeps its native
    FileNotFoundError, which already says everything."""
    p = Path(path)
    try:
        return _read_file_raw(p, **kwargs)
    except FileNotFoundError:
        raise
    except Exception as exc:
        message = str(exc)
        if p.name in message:  # already clear (our own messages qualify)
            raise
        raise ValueError(
            f"could not read {p.name}: {type(exc).__name__}: {message}"
        ) from exc


# format= overrides extension dispatch: for shapes with no reliable
# extension. fwf is the first citizen; each entry is sentinel-safe where the
# underlying reader allows (binary stat formats encode missingness natively)
_FORMATS = {
    "fwf": lambda p, **kw: pd.read_fwf(p, dtype=str, keep_default_na=False, **kw),
    "stata": lambda p, **kw: pd.read_stata(p, **kw),
    "sas": lambda p, **kw: pd.read_sas(p, **kw),
}


def _read_file_raw(p: Path, format: str | None = None, **kwargs) -> pd.DataFrame:
    if format is not None:
        try:
            reader = _FORMATS[format]
        except KeyError:
            known = " ".join(sorted(_FORMATS))
            raise ValueError(
                f"unknown format={format!r} for {p.name}; known: {known}"
            ) from None
        return reader(p, **kwargs)
    # a partitioned parquet dataset is a directory of parts; read it whole
    # before any suffix logic (a directory name may still carry a dot)
    if p.is_dir():
        return pd.read_parquet(p, **kwargs)
    suffix = p.suffix.lower()
    if suffix == ".dta":
        return pd.read_stata(p, **kwargs)
    if suffix == ".sas7bdat":
        return pd.read_sas(p, **kwargs)
    if suffix == ".zip":
        import zipfile

        with zipfile.ZipFile(p) as zf:
            members = zf.namelist()
        if len(members) == 1 and len(p.suffixes) >= 2:
            promised = p.suffixes[-2].lower()
            inner_ext = Path(members[0]).suffix.lower()
            if inner_ext != promised:
                # "data.csv.zip" is a promise about the member; pandas would
                # happily read a README as a header row — silent garbage.
                # Refuse with the archive, the member, and the mismatch named.
                raise ValueError(
                    f"{p.name}: the archive is named as {promised} but its "
                    f"only member is {members[0]!r}"
                )
        # multiple members fall through to pandas' own clear refusal
    # the bomb guard belongs on the branches that actually decompress: run it
    # above this and a file with a compression suffix but no readable inner
    # one ("nosuffix.bz2") is accused of being a bomb instead of being told it
    # is an extension crivo cannot read
    if suffix in _COMPRESSION and len(p.suffixes) >= 2:
        inner = p.suffixes[-2].lower()
        if inner in {".csv", ".tsv", ".txt"}:
            # route through checkup.load so a compressed CSV gets the SAME
            # delimiter sniff, utf-8->cp1252 fallback, and keep_default_na as a
            # plain one (it decompresses the sample and infers the codec).
            # checkup.load runs the bomb guard for everything it opens, so this
            # branch is guarded there rather than a second time here
            return _checkup.load(p, **kwargs)
        if inner == ".json":
            _bomb_check(p, suffix)
            return pd.read_json(p, **kwargs).astype(object)
        if inner in {".jsonl", ".ndjson"}:
            _bomb_check(p, suffix)
            return pd.read_json(p, lines=True, **kwargs).astype(object)
    if suffix in _VIA_CHECKUP:
        return _checkup.load(p, **kwargs)
    if suffix in {".xlsx", ".xls"}:
        try:
            # .xls always means the xlrd engine in pandas; naming it here makes
            # the missing-dependency failure deterministic instead of shape-dependent
            book = pd.ExcelFile(p, engine="xlrd" if suffix == ".xls" else None)
        except ImportError as exc:
            if "xlrd" in str(exc):
                # we advertise .xls but don't ship its engine — say so
                raise ImportError(
                    f"{p.name}: .xls needs the xlrd package "
                    "(pip install xlrd) — or resave as .xlsx"
                ) from exc
            raise
        with book:
            frame = pd.read_excel(book, keep_default_na=False, dtype=str, **kwargs)
            if "sheet_name" not in kwargs and len(book.sheet_names) > 1:
                cells = frame.astype(str).to_numpy()
                filled = int((cells != "").sum()) if cells.size else 0
                if frame.shape[1] < 2 or filled < 3:
                    # an 'Instructions' cover tab silently diagnosed as the
                    # data is worse than an error that teaches sheet_name=
                    names = ", ".join(repr(n) for n in book.sheet_names)
                    raise ValueError(
                        f"{p.name}: the first sheet looks like a cover page — "
                        f"pick the real one with sheet_name=...; sheets: {names}"
                    )
        return frame
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
