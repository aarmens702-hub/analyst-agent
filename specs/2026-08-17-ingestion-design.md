# Ingestion everywhere — the `readers/` layer (Phase 2 core)

Phase 2 of `specs/2026-08-15-master-roadmap.md`. Same methodology as Phase 1:
brainstorm → spec → scoped agents → validate. Extends the keyless wedge — read
from more places, still deterministic, still no key.

## What

Today `aa.read(path)` dispatches on `.suffix` and handles csv/tsv/parquet/
xlsx/json/jsonl. It cannot read a compressed file (`data.csv.gz` → suffix
`.gz` → "unsupported"), a parquet *directory*, a database, or a URL. Phase 2
turns `aa.read` into a **source dispatcher** backed by a small `readers/`
package, one module per source kind, so the surface grows without one giant
function — and so scoped agents can build the pieces in parallel.

## Scope (this increment) — offline-testable, mostly dep-free

- **Files, expanded** (`readers/files.py`): `.gz/.zip/.bz2` compression on the
  text formats, parquet **directories**, `.feather`, `.orc`. Zero new deps
  (pandas + the already-present pyarrow). Sentinel-safe discipline preserved
  (`keep_default_na=False`, values stay strings so the detection engine can see
  `N/A`, `-`, etc.).
- **SQL** (`readers/sql.py`): `read_sql(source, query=None, **kw)` accepting a
  **DBAPI connection** (stdlib `sqlite3`, fully offline-testable) or a
  **SQLAlchemy URL** (`postgresql://…`, optional dep). Existing top-level
  `aa.read_sql(query, connection)` stays for back-compat.
- **Remote** (`readers/remote.py`): `read_url(url, records_path=None, **kw)` over
  stdlib `urllib` (mirrors `ingest.py`'s fetch discipline, no new dep); dispatch
  the payload by content-type/extension (json / csv), `records_path` to pluck a
  nested records array out of a JSON envelope. Sentinel-safe.

## Non-goals (deferred, NOT this increment)

- **Cloud object storage** (`s3://`, `gs://`, `az://`) via fsspec, **Google
  Sheets**, and **warehouses** (Snowflake, BigQuery). All need credentials /
  network, so they cannot be validated offline with the TDD methodology. They
  become documented optional extras later (`analyst-agent[cloud,sql,sheets]`),
  and — per the roadmap NON-GOAL — we do not rebuild connectors an MCP server or
  fsspec already provides; we stamp lineage on whatever DataFrame arrives.
- `ingest.py` (the agent/kernel `load_url` with gates + provenance) is a
  **different surface** and is out of scope; the public keyless `aa.read` never
  touches the kernel.

## Architecture

`aa.read(source, **kw)` delegates to `readers.read(source, **kw)`. The
dispatcher picks a reader by inspecting `source`:

```
readers.read(source, **kw):
  if source is a str with scheme http/https      -> readers.remote.read_url
  elif query kwarg given, OR source is a non-path            (a DBAPI
       connection, OR a str with a SQL URL scheme)  -> readers.sql.read_sql
  else (a path / str / os.PathLike)              -> readers.files.read_file
```

Per-source modules are **lazily imported inside the branch**, so a missing
optional dep (SQLAlchemy) only errors when that source is actually used, and the
package imports cheaply (the keyless surface stays side-effect-free — the Phase 1
rule holds).

## Frozen interfaces (agents code against these)

- `readers.files.read_file(path, **kw) -> pd.DataFrame` *(prefix creates the
  baseline by moving today's local logic here; Agent A extends it)*
  - handles: `.csv/.tsv/.txt` (+ `.gz/.zip/.bz2`), `.parquet/.pq` (file **or**
    directory), `.xlsx/.xls`, `.json`, `.jsonl/.ndjson`, `.feather`, `.orc`.
  - sentinel-safe: text formats read with `keep_default_na=False, dtype=str`
    (reuse `checkup.load` for the csv-family sniff where it applies).
  - unsupported extension raises `ValueError` naming the supported set.
- `readers.sql.read_sql(source, query=None, **kw) -> pd.DataFrame` *(Agent B)*
  - `source` is a DBAPI connection or a SQLAlchemy URL string; `query` is the
    SQL (required for a connection/URL). Uses `pandas.read_sql`. SQLAlchemy is
    imported lazily; a URL with SQLAlchemy absent raises a clear ImportError
    naming the extra.
- `readers.remote.read_url(url, records_path=None, **kw) -> pd.DataFrame`
  *(Agent C)*
  - fetch with urllib (a `User-Agent`, a size cap like `ingest.py`), pick json vs
    csv by content-type then extension, `records_path` (dotted) selects a nested
    list for json, sentinel-safe. Network failure raises a clear error (matches
    `ingest.py`'s `--network none` behaviour).
- `readers.read(source, **kw) -> pd.DataFrame` *(prefix)* — the dispatcher above.
- `api.read(source, **kw)` delegates to `readers.read`; keeps its docstring +
  the supported-formats list, updated for the new sources. *(prefix + integrate)*

## Delegation map

| Agent | Owns (disjoint files) | Depends on |
| --- | --- | --- |
| **prefix (me)** | `readers/__init__.py`, `readers/files.py` (baseline), `api.read` rewire | — |
| **A** files | extends `readers/files.py` + `tests/test_readers_files.py` | prefix baseline |
| **B** sql | `readers/sql.py` + `tests/test_readers_sql.py` | dispatcher contract |
| **C** remote | `readers/remote.py` + `tests/test_readers_remote.py` | dispatcher contract |
| **integrate (me)** | pyproject extras, api.read docstring, dispatch checks | A + B + C |

Agent A extends the file the prefix created (sequential handoff, committed before
fan-out); B and C create brand-new modules; the dispatcher lazy-imports all
three, so no agent edits a file another agent edits.

## Acceptance criteria

- `aa.read("x.csv.gz")` / `.zip` / `.bz2` round-trips a compressed csv,
  sentinels preserved.
- `aa.read(dir_of_parquet_parts)` reads a partitioned parquet directory.
- `aa.read("x.feather")` and `aa.read("x.orc")` work.
- `aa.read(sqlite_conn, query="select …")` returns the rows; a SQLAlchemy URL
  without the extra raises a clear ImportError.
- `aa.read("http://127.0.0.1:PORT/data.json", records_path="…")` reads a served
  JSON records array (localhost fixture); a csv URL reads too.
- Every existing `test_api.py` read test still passes (the refactor is
  behaviour-preserving).
- Full suite + ruff green. No Docker. Import stays side-effect-free.

## Priority

P2 (breadth that compounds the wedge; keyless and deterministic parts first).
