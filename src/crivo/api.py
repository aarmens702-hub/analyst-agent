"""The public Python API — the surface a developer meets after `pip install`.

    import crivo
    report = crivo.diagnose("data.xlsx")   # or a DataFrame you already have
    print(report)
    df = crivo.read("data.csv")            # one reader, many formats, sentinel-safe
    crivo.write(cleaned, "out.parquet")    # one writer, dispatch on extension

Deliberately keyless and kernel-free: this is the deterministic half of the
project — the detection engine and honest I/O — that works with zero setup.
The LLM-authored cleaning with gates and provenance stays the agent surface
(`python -m crivo`), because that half needs a model and a sandbox.
"""

import json as _json
from pathlib import Path

import pandas as pd

from crivo import checkup as _checkup
from crivo.autoclean import CleanSummary, clean
from crivo.detect import SINGLE_FRAME, detect_all

__all__ = [
    "CleanSummary",
    "Report",
    "clean",
    "diagnose",
    "load_example",
    "read",
    "read_sql",
    "write",
]


def load_example() -> pd.DataFrame:
    """A small messy frame for trying crivo offline — the README's first block.

    Deterministic (same frame every call) and built in code, no download.
    Planted diseases, by taxonomy number: 1 numbers-as-strings ("$1,234.50"
    money formats in `revenue`), 4 sentinel-missing ("-999" and "N/A" in
    `units`), 6 whitespace-damage (padded values in `note`), 7 case variants
    (re-cased `region` values), 9 duplicate-rows (two exact repeats at the
    end). `diagnose` finds them; `clean` fixes the safe subset and shows the
    receipts — the sixty-second demo of verify-or-revert.
    """
    regions = ["north", "south", "east", "west"]
    rows = [
        {
            "customer": f"customer {i % 9}",
            "region": regions[i % 4],
            "revenue": f"{100 + 7.5 * i:.2f}",
            "units": str(3 + (i * 5) % 40),
            "note": "ok" if i % 3 else "review",
        }
        for i in range(58)
    ]
    frame = pd.DataFrame(rows)
    frame.loc[[2, 11, 23, 31, 44], "region"] = [
        "NORTH",
        "SOUTH",
        "East",
        "WEST",
        "North",
    ]
    frame.loc[[4, 13, 26, 38, 49, 55], "revenue"] = [
        "$1,234.50",
        "$987.00",
        "2,450.75",
        "$310.25",
        "1,999.99",
        "$45.10",
    ]
    frame.loc[[6, 19, 40], "units"] = "-999"
    frame.loc[[9, 28, 51], "units"] = "N/A"
    frame.loc[[5, 21, 35, 47], "note"] = [
        "  follow up  ",
        "double  space",
        " padded",
        "trailing  ",
    ]
    return pd.concat([frame, frame.iloc[[3, 17]]], ignore_index=True)


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
        # lazy so `import crivo` stays cheap and side-effect-free — the
        # notebook renderer is only needed when a notebook asks for HTML
        from crivo import notebook as _notebook

        return _notebook.report_html(self._name, self._frame, self._result)

    def plot(self, ax=None):
        """A matplotlib data-quality overview — findings per column, coloured by
        grade. Returns an Axes (never calls show); keyless, no kernel."""
        from crivo import charts

        return charts.overview(self._name, self.findings, self.clear, ax=ax)

    def to_html(self, path) -> Path:
        """Write the report as ONE standalone, self-contained HTML file — the
        notebook card wrapped in a document shell, every style inline, no
        scripts, no external assets — for sending to someone who will never
        install anything. Returns the written path; parents are created."""
        from crivo import notebook as _notebook

        card = _notebook.report_html(self._name, self._frame, self._result)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>crivo report — {self._name}</title></head>\n"
            f'<body style="margin:0;padding:24px;background:#f4f4f2">{card}</body></html>\n'
        )
        return p

    def suggest(self, k: int = 5) -> list[str]:
        """3..k plain-English starter questions for this dataset — keyless,
        deterministic (schema order, findings first), derived from dtypes and
        findings. Column names may appear; cell values never do (house rule:
        no dataset rows on any output surface)."""
        frame = self._frame
        n = len(frame)
        numeric = [c for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])]
        datetimes = [
            c for c in frame.columns if pd.api.types.is_datetime64_any_dtype(frame[c])
        ]
        ids = [c for c in frame.columns if n > 1 and frame[c].nunique(dropna=True) == n]
        categorical = [
            c
            for c in frame.columns
            if c not in ids
            and not pd.api.types.is_numeric_dtype(frame[c])
            and not pd.api.types.is_datetime64_any_dtype(frame[c])
            and 0 < frame[c].nunique(dropna=True) <= max(20, n // 10)
        ]

        out: list[str] = []
        for finding in self.findings[:2]:
            for col in finding["columns"][:1]:
                out.append(
                    f'How many rows of "{col}" are affected by {finding["slug"]}?'
                )
        if numeric and categorical:
            out.append(f'What is the total "{numeric[0]}" by "{categorical[0]}"?')
        if datetimes and numeric:
            out.append(f'How does "{numeric[0]}" change over "{datetimes[0]}"?')
        if ids:
            out.append(f'How many distinct "{ids[0]}" are there?')
        if categorical:
            out.append(f'Which "{categorical[0]}" appears most often?')
        if numeric:
            out.append(f'What are the min, mean, and max of "{numeric[0]}"?')
        if frame.shape[1]:
            # schema-only fallbacks so even a clean two-text-column frame
            # gets its three starters
            out.append("How many rows are there?")
            out.append("Which columns have missing values?")

        seen: set[str] = set()
        deduped = [q for q in out if not (q in seen or seen.add(q))]
        return deduped[:k]


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

    The source is dispatched (see `crivo.readers`):
    - a local path — csv/tsv/txt, parquet (file or directory), xlsx/xls, json,
      jsonl/ndjson, feather, orc, Stata .dta, SAS .sas7bdat, optionally
      .gz/.zip/.bz2 compressed; `format="fwf"` overrides extension dispatch
      for fixed-width files (SPSS .sav needs pyreadstat — not bundled);
    - a sqlite file (.db/.sqlite) — opened, queried (`query=...`), and closed
      for you; other databases — a DBAPI connection or a SQLAlchemy URL;
    - an http(s) URL — json or csv, `records_path=...` to pluck nested json.

    Missing-value tokens ("N/A", "-") are preserved as strings, not silently
    coerced to NaN — the detection engine must see them to report them. CSV
    metadata preambles (bank/ERP exports) are skipped conservatively and the
    skip is stamped on the frame (`.attrs["preamble_rows"]`); big-data note:
    for files beyond memory, load into duckdb and pass the `duckdb://` URL.
    """
    from crivo import readers

    return readers.read(source, **kwargs)


def read_sql(query: str, connection) -> pd.DataFrame:
    """Read a SQL query result into a DataFrame. `connection` is anything
    pandas.read_sql accepts (a SQLAlchemy engine/URL or a DBAPI connection).
    SQLAlchemy is an optional dependency — install it for URL connections."""
    return pd.read_sql(query, connection)


def write(frame: pd.DataFrame, path, **kwargs) -> Path:
    """Write a DataFrame, format inferred from the extension.

    Supported: .csv, .parquet/.pq, .xlsx, .json, .jsonl/.ndjson, .feather,
    .orc. The cleaned data is the deliverable; the original file is never
    touched.
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
    elif suffix == ".feather":
        # feather has no index=False switch; dropping it here keeps parity
        # with every other writer
        frame.reset_index(drop=True).to_feather(p, **kwargs)
    elif suffix == ".orc":
        frame.to_orc(p, index=False, **kwargs)
    else:
        raise ValueError(
            f"unsupported extension {suffix!r} for {p.name}; supported: "
            ".csv .parquet .xlsx .json .jsonl .feather .orc"
        )
    return p


SIGNALS = len(SINGLE_FRAME)  # how many checks diagnose runs on every file
