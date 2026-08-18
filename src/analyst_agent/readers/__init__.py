"""Source dispatch for `aa.read`: a URL, a database, or a local path — each to
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
    scheme = _scheme(source)
    if scheme in {"http", "https"}:
        from analyst_agent.readers import remote

        return remote.read_url(source, **kwargs)
    if scheme in _CLOUD_SCHEMES:
        raise NotImplementedError(
            f"cloud storage ({scheme}://) is deferred, not built yet — fetch it "
            "with fsspec or your cloud client and pass the DataFrame straight to "
            "aa.diagnose/aa.clean (they stamp lineage on any frame)."
        )
    if (
        query is not None
        or scheme.split("+")[0] in _SQL_SCHEMES
        or not _is_pathlike(source)
    ):
        from analyst_agent.readers import sql

        return sql.read_sql(source, query=query, **kwargs)
    from analyst_agent.readers import files

    return files.read_file(source, **kwargs)
