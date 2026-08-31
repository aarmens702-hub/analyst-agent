"""The SQL reader: a DBAPI connection or a SQLAlchemy URL string -> DataFrame.

All offline. The happy path uses stdlib `sqlite3`, so no optional dependency is
needed to exercise it; the URL-without-the-extra path is guarded so it asserts
the ImportError only when SQLAlchemy is genuinely absent.
"""

import sqlite3


def _sqlite_conn_with_rows(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("create table t (id integer, name text)")
    conn.executemany("insert into t values (?, ?)", [(1, "a"), (2, "b")])
    conn.commit()
    return conn


def test_read_sql_dbapi_connection_returns_rows(tmp_path) -> None:
    """A DBAPI connection + a query reads straight through pandas."""
    from crivo.readers.sql import read_sql

    conn = _sqlite_conn_with_rows(tmp_path / "t.db")
    try:
        df = read_sql(conn, query="select * from t")
    finally:
        conn.close()

    assert list(df.columns) == ["id", "name"]
    assert df["id"].tolist() == [1, 2]
    assert df["name"].tolist() == ["a", "b"]


def test_read_sql_without_query_raises_valueerror(tmp_path) -> None:
    """A source with no `query` is a usage error, not a silent empty read."""
    import pytest

    from crivo.readers.sql import read_sql

    conn = _sqlite_conn_with_rows(tmp_path / "t.db")
    try:
        with pytest.raises(ValueError, match="query"):
            read_sql(conn)
    finally:
        conn.close()


def test_read_sql_url_without_the_extra_raises_importerror() -> None:
    """A SQLAlchemy URL string with the `sql` extra uninstalled fails loudly,
    naming the extra to install — not with pandas' generic complaint."""
    import importlib.util

    import pytest

    from crivo.readers.sql import read_sql

    if importlib.util.find_spec("sqlalchemy") is not None:
        pytest.skip("sqlalchemy installed")

    with pytest.raises(ImportError, match=r"crivo\[sql\]"):
        read_sql("postgresql://x/y", query="select 1")
