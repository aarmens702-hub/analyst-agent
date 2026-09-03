"""Source dispatch for `crivo.read`: a URL, a database, or a local path — each to
its own reader.

    readers.read("data.csv.gz")                 -> readers.files
    readers.read(sqlite_conn, query="select …") -> readers.sql
    readers.read("https://api/…json")           -> readers.remote

The per-source modules are imported lazily inside each branch, so a missing
optional dependency (SQLAlchemy) only bites when that source is actually used,
and importing this package stays cheap and side-effect-free — the keyless
surface must not do heavy work at import time (it runs in every notebook cell).
"""

import os
from urllib.parse import urlparse

# scheme (before any '+driver') that means "this is a database URL"
_SQL_SCHEMES = {
    "postgresql",
    "postgres",
    "mysql",
    "mariadb",
    "sqlite",
    "mssql",
    "oracle",
    "duckdb",
    "cockroachdb",
    "snowflake",
    "bigquery",
}

# object stores we deliberately do NOT read yet (deferred, see the spec's
# non-goals) — flagged with a clear message instead of falling through to the
# local-file reader, which would treat "s3://…" as a mangled local path
_CLOUD_SCHEMES = {"s3", "gs", "gcs", "az", "abfs", "abfss", "adl", "adlfs"}


def _scheme(source) -> str:
    if isinstance(source, str) and "://" in source:
        return urlparse(source).scheme.lower()
    return ""


def _is_pathlike(source) -> bool:
    return isinstance(source, (str, os.PathLike))


def read(source, *, query=None, **kwargs):
    """Dispatch `source` to the right reader. See the module docstring."""
    if hasattr(source, "read") and not _is_pathlike(source):
        # a BytesIO/file object would fall through to the SQL branch and die
        # with "needs a query" — a lie about what went wrong
        raise TypeError(
            "crivo.read takes a path, URL, or DB connection — not an open "
            "file object. Write the buffer to a temp file first, or hand it "
            "to pandas directly and pass the DataFrame to crivo."
        )
    scheme = _scheme(source)
    if scheme in {"http", "https"}:
        from crivo.readers import remote

        return remote.read_url(source, **kwargs)
    if scheme in _CLOUD_SCHEMES:
        raise NotImplementedError(
            f"cloud storage ({scheme}://) is deferred, not built yet — fetch it "
            "with fsspec or your cloud client and pass the DataFrame straight to "
            "crivo.diagnose/crivo.clean (they stamp lineage on any frame)."
        )
    if (
        not scheme  # "sqlite:///file.db" is a URL for SQLAlchemy, not a bare file
        and _is_pathlike(source)
        and str(source).lower().endswith((".db", ".sqlite", ".sqlite3"))
    ):
        # a bare sqlite file: open/query/close it ourselves — demanding a
        # pre-opened connection for the most common local-database case was
        # pure ceremony
        from crivo.readers import sql

        return sql.read_sqlite_file(source, query=query, **kwargs)
    if (
        query is not None
        or scheme.split("+")[0] in _SQL_SCHEMES
        or not _is_pathlike(source)
    ):
        from crivo.readers import sql

        return sql.read_sql(source, query=query, **kwargs)
    from crivo.readers import files

    return files.read_file(source, **kwargs)
