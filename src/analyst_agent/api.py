"""The public Python API — the surface a developer meets after `pip install`.

    import analyst_agent as aa
    report = aa.diagnose("data.xlsx")   # or a DataFrame you already have
    print(report)
    df = aa.read("data.csv")            # one reader, many formats, sentinel-safe
    aa.write(cleaned, "out.parquet")    # one writer, dispatch on extension

Deliberately keyless and kernel-free: this is the deterministic half of the
project — the detection engine and honest I/O — that works with zero setup.
The LLM-authored cleaning with gates and provenance stays the agent surface
(`python -m analyst_agent`), because that half needs a model and a sandbox.
"""

import json as _json
from pathlib import Path

import pandas as pd

from analyst_agent import checkup as _checkup
from analyst_agent.autoclean import CleanSummary, clean
from analyst_agent.detect import SINGLE_FRAME, detect_all

__all__ = [
    "CleanSummary",
    "Report",
    "clean",
    "diagnose",
    "read",
    "read_sql",
    "write",
]


class Report:
    """A diagnosis result that reads well for a human and serialises for a
    machine. `print(report)` is the free report; `.findings` / `.to_json()`
    are for code."""

    def __init__(self, frame: pd.DataFrame, result: dict, name: str = "dataframe"):
        self._frame = frame
        self._result = result
        self._name = name

    @property
    def findings(self) -> list[dict]:
        return self._result["findings"]

    @property
    def clear(self) -> list[int]:
        return self._result["clear"]

    @property
    def broken(self) -> dict:
        return self._result.get("broken", {})

    def to_dict(self) -> dict:
        return {
            "name": self._name,
            "rows": len(self._frame),
            "columns": len(self._frame.columns),
            **self._result,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return _json.dumps(self.to_dict(), indent=indent)

    def __repr__(self) -> str:
        return _checkup.render(self._name, self._frame, self._result)

    def _repr_html_(self) -> str:
        # lazy so `import analyst_agent` stays cheap and side-effect-free — the
        # notebook renderer is only needed when a notebook asks for HTML
        from analyst_agent import notebook as _notebook

        return _notebook.report_html(self._name, self._frame, self._result)


def diagnose(data, name: str | None = None) -> Report:
    """Diagnose a DataFrame or a file path against the 22-check engine.

    Pure and keyless: no model, no kernel. A path is read with `read` (format
    sniffed, sentinels preserved); a DataFrame is used as-is.
    """
    if isinstance(data, pd.DataFrame):
        frame = data
        label = name or "dataframe"
    else:
        frame = read(data)
        label = name or Path(str(data)).name
    return Report(frame, detect_all(frame, label), label)


def read(source, **kwargs) -> pd.DataFrame:
    """Read data into a DataFrame from a path, a database, or a URL.

    The source is dispatched (see `analyst_agent.readers`):
    - a local path — csv/tsv/txt, parquet (file or directory), xlsx/xls, json,
      jsonl/ndjson, feather, orc, optionally .gz/.zip/.bz2 compressed;
    - a database — a DBAPI connection or a SQLAlchemy URL, with `query=...`;
    - an http(s) URL — json or csv, `records_path=...` to pluck nested json.

    Missing-value tokens ("N/A", "-") are preserved as strings, not silently
    coerced to NaN — the detection engine must see them to report them.
    """
    from analyst_agent import readers

    return readers.read(source, **kwargs)


def read_sql(query: str, connection) -> pd.DataFrame:
    """Read a SQL query result into a DataFrame. `connection` is anything
    pandas.read_sql accepts (a SQLAlchemy engine/URL or a DBAPI connection).
    SQLAlchemy is an optional dependency — install it for URL connections."""
    return pd.read_sql(query, connection)


def write(frame: pd.DataFrame, path, **kwargs) -> Path:
    """Write a DataFrame, format inferred from the extension.

    Supported: .csv, .parquet/.pq, .xlsx, .json, .jsonl/.ndjson. The cleaned
    data is the deliverable; the original file is never touched.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    p.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        frame.to_csv(p, index=False, **kwargs)
    elif suffix in {".parquet", ".pq"}:
        frame.to_parquet(p, index=False, **kwargs)
    elif suffix == ".xlsx":
        frame.to_excel(p, index=False, **kwargs)
    elif suffix == ".json":
        frame.to_json(p, orient="records", indent=2, **kwargs)
    elif suffix in {".jsonl", ".ndjson"}:
        frame.to_json(p, orient="records", lines=True, **kwargs)
    else:
        raise ValueError(
            f"unsupported extension {suffix!r} for {p.name}; supported: "
            ".csv .parquet .xlsx .json .jsonl"
        )
    return p


SIGNALS = len(SINGLE_FRAME)  # how many checks diagnose runs on every file
