"""The SQL reader for `crivo.read`: a DBAPI connection or a SQLAlchemy URL string,
each into a DataFrame, both via `pandas.read_sql`.

SQLAlchemy is an *optional* dependency (the `sql` extra). It is imported lazily,
inside `read_sql`, so importing this module stays cheap and side-effect-free —
the keyless surface runs in every notebook cell — and a missing SQLAlchemy only
bites when a URL string is actually handed in, never when a live DBAPI
connection (which needs no SQLAlchemy at all) is.
"""

import pandas as pd


def read_sqlite_file(path, query=None, **kwargs) -> pd.DataFrame:
    """Open a local sqlite file, run `query`, close — always close. Without a
    query the error teaches: it lists the tables the file actually holds."""
    import sqlite3
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no such database file: {p}")
    conn = sqlite3.connect(p)
    try:
        if query is None:
            rows = conn.execute(
                "select name from sqlite_master where type='table' order by name"
            ).fetchall()
            tables = ", ".join(name for (name,) in rows) or "(no tables)"
            raise ValueError(
                f"{p.name}: pass query='select ...' — tables here: {tables}"
            )
        return pd.read_sql(query, conn, **kwargs)
    finally:
        conn.close()


def read_sql(source, query=None, **kwargs) -> pd.DataFrame:
    """Read `query` against `source` into a DataFrame.

    `source` is either a DBAPI connection (e.g. a `sqlite3.Connection`) — passed
    straight to `pandas.read_sql` — or a SQLAlchemy URL string
    (`postgresql://…`, `sqlite:///file.db`), in which case SQLAlchemy is required
    to turn the URL into an engine.
    """
    if query is None:
        raise ValueError(
            "read_sql needs a `query`: read_sql(source, query='select ...')"
        )
    if isinstance(source, str):
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            raise ImportError(
                "reading from a database URL needs SQLAlchemy, an optional "
                'dependency; install it with: pip install "crivo[sql]"'
            ) from exc
        return pd.read_sql(query, create_engine(source), **kwargs)
    return pd.read_sql(query, source, **kwargs)
